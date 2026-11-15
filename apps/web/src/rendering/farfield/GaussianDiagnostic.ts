/**
 * Gaussian-asset diagnostic renderer (Chunk 3). Shows optimized 3DGS assets with Spark in the shared scene, next to
 * or instead of the SDF output, so the two can be compared from the same camera. It is *not* the hybrid far field:
 * there is no region ownership, no SDF discard and no cross-fade yet (Chunk 4).
 *
 * An asset is activated only after (1) its sha256 matches the manifest and (2) its region_revision equals the
 * revision of the region currently loaded. A region whose revision moves past the asset's is deactivated and listed
 * as stale; nothing stands in for it.
 */
import * as THREE from "three";
import { SparkRenderer, SplatMesh } from "@sparkjsdev/spark";
import type { NeuralAssetManifest, RegionCoord } from "@contracts";
import type { FarFieldRenderer } from "../hybrid/types";

export type GaussianMode = "sdf" | "gaussian" | "split";
export const GAUSSIAN_MODES: GaussianMode[] = ["sdf", "gaussian", "split"];

export interface GaussianAssetStatus {
  assetId: string;
  coord: RegionCoord;
  assetRevision: number;
  regionRevision: number | null;
  gaussians: number;
  bytes: number;
  psnrHoldout: number | null;
  state: "loading" | "active" | "stale" | "error";
  error?: string;
}

const key = (c: RegionCoord) => `${c.rx},${c.rz}`;

async function sha256Hex(bytes: ArrayBuffer): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

export class GaussianDiagnostic implements FarFieldRenderer {
  readonly spark: SparkRenderer;
  private meshes = new Map<string, { mesh: SplatMesh; manifest: NeuralAssetManifest }>();
  private status = new Map<string, GaussianAssetStatus>();

  constructor(renderer: THREE.WebGLRenderer, private readonly scene: THREE.Scene) {
    // Match the training rasterizer (services/gpu/rasterizers/torch_tiles.py): 0.3 px² covariance dilation without
    // opacity compensation, 3σ cutoff, front-to-back order by camera z.
    this.spark = new SparkRenderer({ renderer, preBlurAmount: 0.3, blurAmount: 0, maxStdDev: 3, sortRadial: false });
    this.scene.add(this.spark);
  }

  /** Verify and add one asset. `regionRevision` is the revision of the region as loaded right now. */
  async load(manifest: NeuralAssetManifest, bytes: ArrayBuffer, regionRevision: number): Promise<void> {
    const k = key(manifest.coord);
    const st: GaussianAssetStatus = {
      assetId: manifest.asset_id, coord: manifest.coord, assetRevision: manifest.region_revision, regionRevision,
      gaussians: manifest.gaussian_count, bytes: manifest.byte_size, psnrHoldout: manifest.metrics.psnr_holdout ?? null,
      state: "loading",
    };
    this.status.set(k, st);
    if (manifest.region_revision !== regionRevision) {
      st.state = "stale";
      return;
    }
    const digest = await sha256Hex(bytes);
    if (digest !== manifest.sha256) {
      st.state = "error";
      st.error = "sha256 mismatch";
      return;
    }
    await this.activate(manifest, bytes);
    st.state = "active";
  }

  async activate(manifest: NeuralAssetManifest, bytes: ArrayBuffer): Promise<void> {
    this.deactivate(manifest.coord);
    const mesh = new SplatMesh({ fileBytes: bytes, fileType: "ply" as never, fileName: manifest.uri });
    await mesh.initialized;
    // stored centers are relative to the region origin, in the world frame (+Y up, meters)
    const o = manifest.origin as number[];
    mesh.position.set(o[0], o[1], o[2]);
    this.scene.add(mesh);
    this.meshes.set(key(manifest.coord), { mesh, manifest });
  }

  deactivate(coord: RegionCoord): void {
    const entry = this.meshes.get(key(coord));
    if (!entry) return;
    this.scene.remove(entry.mesh);
    entry.mesh.dispose();
    this.meshes.delete(key(coord));
  }

  /** Re-check every asset against the latest region revisions (e.g. after a region snapshot was refreshed). */
  syncRevisions(revisionOf: (c: RegionCoord) => number | null): void {
    for (const [k, st] of this.status) {
      const rev = revisionOf(st.coord);
      st.regionRevision = rev;
      if (rev !== null && rev !== st.assetRevision && st.state === "active") {
        this.deactivate(st.coord);
        st.state = "stale";
      }
      this.status.set(k, st);
    }
  }

  /**
   * Hide or show all Gaussians by toggling the SparkRenderer itself (meshes stay visible): Spark prepares its draw
   * list in its own onBeforeRender, so in a split frame it must only run in the pass that actually draws splats.
   */
  setVisible(visible: boolean): void {
    this.spark.visible = visible;
  }

  has(assetId: string): boolean {
    return [...this.status.values()].some((s) => s.assetId === assetId && s.state !== "error");
  }

  assets(): GaussianAssetStatus[] {
    return [...this.status.values()];
  }

  manifests(): NeuralAssetManifest[] {
    return [...this.meshes.values()].map((m) => m.manifest);
  }

  stats(): { regions: number; splats: number; gpuMs: number | null } {
    let splats = 0;
    for (const { mesh } of this.meshes.values()) splats += mesh.numSplats ?? 0;
    return { regions: this.meshes.size, splats, gpuMs: null };
  }

  clear(): void {
    for (const k of [...this.meshes.keys()]) {
      const [rx, rz] = k.split(",").map(Number);
      this.deactivate({ rx, rz });
    }
    this.status.clear();
  }
}
