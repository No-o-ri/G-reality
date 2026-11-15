/**
 * CC0 photo textures for the near field (identity fixed in the world spec: MaterialDefinition.texture).
 *
 * Each texture set (albedo, OpenGL normal, AO/roughness/metal) occupies one layer of three WebGL2 texture arrays. Maps
 * are requested from the companion (which serves them from its Neon cache, fetching from Poly Haven only on first use)
 * strictly in the background: rendering never waits. Until a set has arrived its materials keep their procedural
 * surface (blend weight 0), so a slow or missing network only means fewer photo details, never a stall. Decoded layers
 * are kept in memory for the session, so reopening a world (e.g. after an in-world creation) does not download again.
 */
import * as THREE from "three";
import type { MaterialDefinition } from "@contracts";
import { MAX_TEXTURED_MATERIALS } from "./shader";

export type TextureSource = (assetId: string, map: "albedo" | "normal" | "arm" | "height") => Promise<Blob>;

const SIZE = 512;
const MAX_LAYERS = 12;
const MAPS = ["albedo", "normal", "arm"] as const;

interface Decoded {
  maps: Record<(typeof MAPS)[number], Uint8Array>;
  /** mean albedo colour, linear */
  mean: [number, number, number];
}

const decoded = new Map<string, Decoded>();
const inflight = new Map<string, Promise<Decoded>>();

async function decode(blob: Blob, flipY: boolean): Promise<Uint8Array> {
  const bitmap = await createImageBitmap(blob, { resizeWidth: SIZE, resizeHeight: SIZE, resizeQuality: "high" });
  const canvas = new OffscreenCanvas(SIZE, SIZE);
  const ctx = canvas.getContext("2d", { willReadFrequently: true })!;
  ctx.drawImage(bitmap, 0, 0);
  bitmap.close();
  const src = ctx.getImageData(0, 0, SIZE, SIZE).data;
  if (!flipY) return new Uint8Array(src.buffer.slice(0));
  // canvas rows run top-down, texture rows bottom-up: flip so the normal map's +Y (green) points up the texture
  const out = new Uint8Array(src.length);
  const row = SIZE * 4;
  for (let y = 0; y < SIZE; y++) out.set(src.subarray(y * row, (y + 1) * row), (SIZE - 1 - y) * row);
  return out;
}

function meanLinear(rgba: Uint8Array): [number, number, number] {
  let r = 0, g = 0, b = 0, n = 0;
  for (let i = 0; i < rgba.length; i += 4 * 61) { // sparse sample is enough for a mean
    r += (rgba[i] / 255) ** 2.2; g += (rgba[i + 1] / 255) ** 2.2; b += (rgba[i + 2] / 255) ** 2.2; n++;
  }
  return [r / n, g / n, b / n];
}

function load(assetId: string, source: TextureSource): Promise<Decoded> {
  const done = decoded.get(assetId);
  if (done) return Promise.resolve(done);
  let p = inflight.get(assetId);
  if (!p) {
    p = (async () => {
      const blobs = await Promise.all(MAPS.map((m) => source(assetId, m)));
      const [albedo, normal, arm] = await Promise.all(blobs.map((b) => decode(b, true)));
      const mean = meanLinear(albedo);
      // the height (displacement) map goes into the albedo's alpha channel (no extra texture); without one the
      // surface counts as flat (alpha 255) and only the colour, normal and ARM maps apply
      try {
        const height = await decode(await source(assetId, "height"), true);
        for (let i = 3; i < albedo.length; i += 4) albedo[i] = height[i - 3];
      } catch {
        for (let i = 3; i < albedo.length; i += 4) albedo[i] = 255;
      }
      const d: Decoded = { maps: { albedo, normal, arm }, mean };
      decoded.set(assetId, d);
      return d;
    })().finally(() => inflight.delete(assetId));
    inflight.set(assetId, p);
  }
  return p;
}

function arrayTexture(): THREE.DataArrayTexture {
  const tex = new THREE.DataArrayTexture(new Uint8Array(SIZE * SIZE * 4 * MAX_LAYERS), SIZE, SIZE, MAX_LAYERS);
  tex.format = THREE.RGBAFormat;
  tex.type = THREE.UnsignedByteType;
  tex.wrapS = tex.wrapT = THREE.RepeatWrapping;
  tex.magFilter = THREE.LinearFilter;
  tex.minFilter = THREE.LinearMipmapLinearFilter;
  tex.generateMipmaps = true;
  tex.anisotropy = 4;
  tex.needsUpdate = true;
  return tex;
}

export class TextureLibrary {
  readonly albedo = arrayTexture();
  readonly normal = arrayTexture();
  readonly arm = arrayTexture();
  /** per material: layer, 1 / tile size, blend weight, normal strength (uniform uMatTex) */
  readonly matTex = Array.from({ length: MAX_TEXTURED_MATERIALS }, () => new THREE.Vector4(-1, 0.5, 0, 1));
  /** per material: mean albedo (rgb), tint amount (a) (uniform uMatTint) */
  readonly matTint = Array.from({ length: MAX_TEXTURED_MATERIALS }, () => new THREE.Vector4(0.5, 0.5, 0.5, 0.35));
  private generation = 0;
  /** texture sets bound in the current world / loaded so far (diagnostics) */
  bound = 0;
  loaded = 0;
  failed = 0;

  /**
   * Bind the scene's materials (scene material table order) and start loading their texture sets in the background.
   * Without a source (public sample, no companion) nothing is requested and every surface stays procedural.
   */
  bind(materials: MaterialDefinition[], source: TextureSource | null): void {
    const gen = ++this.generation;
    for (const v of this.matTex) v.set(-1, 0.5, 0, 1);
    const layers = new Map<string, number>();
    const users = new Map<string, number[]>();
    materials.slice(0, MAX_TEXTURED_MATERIALS).forEach((m, i) => {
      const t = m.texture;
      if (!t?.asset_id) return;
      let layer = layers.get(t.asset_id);
      if (layer === undefined) {
        if (layers.size >= MAX_LAYERS) return;
        layer = layers.size;
        layers.set(t.asset_id, layer);
      }
      this.matTex[i].set(layer, 1 / (t.scale_m ?? 2), 0, t.normal_strength ?? 1);
      this.matTint[i].w = t.tint ?? 0.35;
      users.set(t.asset_id, [...(users.get(t.asset_id) ?? []), i]);
    });
    this.bound = layers.size;
    this.loaded = this.failed = 0;
    if (!source) return;
    for (const [assetId, layer] of layers) {
      load(assetId, source).then((d) => {
        if (gen !== this.generation) return; // another world was opened meanwhile
        const offset = layer * SIZE * SIZE * 4;
        for (const name of MAPS) {
          const tex = this[name];
          (tex.image.data as Uint8Array).set(d.maps[name], offset);
          tex.addLayerUpdate(layer);
          tex.needsUpdate = true;
        }
        for (const i of users.get(assetId) ?? []) {
          this.matTint[i].set(d.mean[0], d.mean[1], d.mean[2], this.matTint[i].w);
          this.matTex[i].z = 1;
        }
        this.loaded++;
      }).catch(() => {
        if (gen === this.generation) this.failed++; // stays procedural
      });
    }
  }
}
