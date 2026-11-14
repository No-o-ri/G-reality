import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import type { GeometryDefinition, WorldEntity } from "@contracts";
import { compileArchetype, compileScene, glslFloat } from "../src/sdf/compiler";
import { measureArchetype } from "../src/sdf/measure";
import { compileCpu } from "../src/sdf/cpu";
import { buildGrid, buildInstances, GRID_CAPACITY, INSTANCE_TEXELS, type ArchetypeRuntime } from "../src/rendering/nearfield/buffers";
import { buildMarchFragment } from "../src/rendering/nearfield/shader";

const here = dirname(fileURLToPath(import.meta.url));
const library = JSON.parse(readFileSync(join(here, "../../../tests/golden/archetypes.json"), "utf8")) as
  | GeometryDefinition[]
  | Record<string, GeometryDefinition>;
const defs: GeometryDefinition[] = Array.isArray(library) ? library : Object.values(library);

const mat = (id: string) => ({ id, base_color: [0.5, 0.5, 0.5] as [number, number, number], roughness: 0.8 });
const def = (root: unknown, parameters: Record<string, { default: number; min: number; max: number }> = {}): GeometryDefinition =>
  ({ id: "t", version: 1, language_version: 1, root, parameters, materials: [mat("a"), mat("b")], bound_radius: 4 }) as unknown as GeometryDefinition;

function balanced(src: string): boolean {
  let depth = 0;
  for (const ch of src) {
    if (ch === "{" || ch === "(") depth++;
    if (ch === "}" || ch === ")") depth--;
    if (depth < 0) return false;
  }
  return depth === 0;
}

describe("glslFloat", () => {
  it("always emits a float literal", () => {
    expect(glslFloat(1)).toBe("1.0");
    expect(glslFloat(-3)).toBe("-3.0");
    expect(glslFloat(0.25)).toBe("0.25");
    expect(glslFloat(1e-7)).toBe("1e-7");
    expect(glslFloat(1e21)).toMatch(/e\+?21/);
  });
  it("rejects anything that is not a finite number", () => {
    for (const bad of [NaN, Infinity, "1.0", "1.0); discard; //", null, undefined, {}, []]) {
      expect(() => glslFloat(bad)).toThrow();
    }
  });
});

describe("compiler", () => {
  it("compiles every library archetype into a well-formed function", () => {
    expect(defs.length).toBeGreaterThanOrEqual(5);
    defs.forEach((d, i) => {
      const c = compileArchetype(d, i, 0);
      expect(c.glsl).toContain(`vec2 sdArch_${i}(vec3 p, vec4 prm)`);
      expect(c.glsl).toMatch(/return vec2\(d\d+ \* [0-9.]+, .+\);/);
      expect(balanced(c.glsl)).toBe(true);
      expect(c.nodeCount).toBeGreaterThan(0);
    });
  });

  it("assigns global material indices and builds the dispatch and material tables", () => {
    const scene = compileScene(defs, [mat("ground"), mat("cliff")]);
    const total = defs.reduce((n, d) => n + d.materials.length, 0) + 2;
    expect(scene.materials.length).toBe(total);
    let base = 0;
    for (const d of defs) {
      expect(scene.archetypes.get(d.id)!.materialBase).toBe(base);
      base += d.materials.length;
    }
    for (let i = 0; i < defs.length; i++) expect(scene.glsl).toContain(`case ${i}: return sdArch_${i}(p, prm);`);
    for (let i = 0; i < total; i++) expect(scene.glsl).toMatch(new RegExp(`case ${i}: return vec4\\(`));
    const fragment = buildMarchFragment(scene.glsl, { ground: total - 2, cliff: total - 1, peak: -1, water: -1 });
    expect(balanced(fragment)).toBe(true);
    expect(fragment).toContain("gl_FragDepth");
  });

  it("maps parameter references to prm components in declaration order", () => {
    const d = def({ op: "sphere", params: { radius: { ref: "r" } } }, { h: { default: 1, min: 0, max: 2 }, r: { default: 1, min: 0, max: 2 } });
    const c = compileArchetype(d, 0, 0);
    expect(c.paramNames).toEqual(["h", "r"]);
    expect(c.glsl).toContain("sdSphere(p, prm.y)");
  });

  it("tracks materials through CSG", () => {
    const d = def({
      op: "union", params: {}, children: [
        { op: "sphere", params: { radius: 1 }, material: "a" },
        { op: "translate", params: { offset: [3, 0, 0] }, material: "b", children: [{ op: "box", params: { half: [1, 1, 1] } }] },
      ],
    });
    const c = compileArchetype(d, 0, 10);
    expect(c.glsl).toMatch(/float m\d+ = \(d\d+ < d\d+\) \? 10\.0 : 11\.0;/);
  });

  it("propagates the step scale of non-isometric ops", () => {
    const d = def({ op: "twist", params: { rate: 0.5 }, children: [{ op: "box", params: { half: [1, 2, 1] } }] });
    expect(compileArchetype(d, 0, 0).stepScale).toBe(0.6);
    expect(compileArchetype(def({ op: "sphere", params: { radius: 1 } }), 0, 0).stepScale).toBe(1);
  });

  it("refuses malformed input instead of emitting it", () => {
    const cases: unknown[] = [
      { op: "exec", params: {} },
      { op: "sphere", params: { radius: "1.0); discard; //" } },
      { op: "sphere", params: { radius: { ref: "nope" } } },
      { op: "sphere", params: { radius: NaN } },
      { op: "box", params: { half: [1, 2] } },
      { op: "union", params: {}, children: [{ op: "sphere", params: { radius: 1 } }] },
      { op: "sphere", params: { radius: 1 }, material: "missing" },
      { op: "rotate", params: { quat: [0, 0, 0, 0] }, children: [{ op: "sphere", params: { radius: 1 } }] },
      { op: "displace", params: { amplitude: 1, frequency: 1, octaves: 99, seed: 1 }, children: [{ op: "sphere", params: { radius: 1 } }] },
      { op: "mirror", params: { axis: 7 }, children: [{ op: "sphere", params: { radius: 1 } }] },
      { op: "plane", params: { normal: [0, 0, 0], offset: 0 } },
    ];
    for (const root of cases) expect(() => compileArchetype(def(root), 0, 0)).toThrow();
  });

  it("never copies strings from the definition into shader code", () => {
    const hostile = def({ op: "sphere", params: { radius: 1 }, material: "a" });
    (hostile as { id: string }).id = "x; } void main() { discard; } //\n#define HACK";
    const c = compileArchetype(hostile, 0, 0);
    expect(c.glsl).not.toContain("discard");
    expect(c.glsl).not.toContain("#define");
    expect(c.glsl.split("\n")[0].startsWith("//")).toBe(true);
    expect(c.glsl.split("\n").filter((l) => l.includes("HACK")).length).toBeLessThanOrEqual(1);
  });

  it("limits archetype parameters to the vec4 it can address", () => {
    const params = Object.fromEntries(["a", "b", "c", "d", "e"].map((k) => [k, { default: 0, min: 0, max: 1 }]));
    expect(() => compileArchetype(def({ op: "sphere", params: { radius: 1 } }, params), 0, 0)).toThrow(/at most 4/);
  });
});

describe("measurements, instances and grid", () => {
  const runtimes = new Map<string, ArchetypeRuntime>();
  const scene = compileScene(defs);
  for (const d of defs) {
    const compiled = scene.archetypes.get(d.id)!;
    const specs = d.parameters ?? {};
    runtimes.set(d.id, {
      compiled,
      base: measureArchetype(d),
      widest: measureArchetype(d, Object.fromEntries(Object.entries(specs).map(([k, v]) => [k, v.max]))),
      paramDefaults: compiled.paramNames.map((n) => specs[n].default),
      paramMin: compiled.paramNames.map((n) => specs[n].min),
      paramMax: compiled.paramNames.map((n) => specs[n].max),
    });
  }

  it("measured cylinders contain the surface of every archetype", () => {
    for (const d of defs) {
      const m = measureArchetype(d);
      const fn = compileCpu(d);
      const params = Object.fromEntries(Object.entries(d.parameters ?? {}).map(([k, v]) => [k, v.default]));
      let inside = 0;
      for (let i = 0; i < 4000; i++) {
        // deterministic quasi-random points in the declared bound
        const R = d.bound_radius;
        const p: [number, number, number] = [
          (((i * 0.754877) % 1) * 2 - 1) * R,
          (((i * 0.569840) % 1) * 2 - 1) * R,
          (((i * 0.362437) % 1) * 2 - 1) * R,
        ];
        if (fn(p, params).d < 0) {
          inside++;
          expect(Math.hypot(p[0], p[2])).toBeLessThanOrEqual(m.footprint);
          expect(p[1]).toBeGreaterThanOrEqual(m.yMin);
          expect(p[1]).toBeLessThanOrEqual(m.yMax);
        }
      }
      expect(m.footprint).toBeLessThan(d.bound_radius);
      expect(inside).toBeGreaterThan(0);
    }
  });

  const entity = (id: string, geometry: string, x: number, z: number, extra: Partial<WorldEntity> = {}): WorldEntity =>
    ({
      id, world_id: "w", kind: "tree", geometry_id: geometry, geometry_params: {},
      transform: { position: [x, 0, z], rotation: [0, 0, 0, 1], scale: 1 }, region: { rx: 0, rz: 0 },
      bound_radius: 5, capabilities: [], state: { condition: "intact" }, revision: 0, created_revision: 0, ...extra,
    }) as unknown as WorldEntity;

  it("packs instances, skips removed and unknown entities, and encodes burn state", () => {
    const tree = defs.find((d) => d.id.startsWith("tree"))!.id;
    const inst = buildInstances(
      [
        entity("a", tree, 10, 10),
        entity("b", tree, 20, 10, { state: { condition: "burning" } as never, geometry_params: { burn: 0.2 } as never }),
        entity("c", tree, 30, 10, { state: { condition: "removed" } as never }),
        entity("d", "no.such.archetype", 40, 10),
        entity("e", tree, 50, 10, { state: { condition: "charred" } as never }),
      ],
      runtimes,
    );
    expect(inst.count).toBe(3);
    expect(inst.ids).toEqual(["a", "b", "e"]);
    expect(inst.skipped).toBe(1);
    const row = (i: number, texel: number) => Array.from(inst.data.slice((i * INSTANCE_TEXELS + texel) * 4, (i * INSTANCE_TEXELS + texel) * 4 + 4));
    expect(row(0, 0)).toEqual([10, 0, 10, 1]);
    expect(row(0, 4)[1]).toBe(0);
    expect(row(1, 4)[1]).toBe(1); // burning flag
    expect(row(1, 4)[2]).toBeCloseTo(0.2);
    expect(row(2, 4)[2]).toBe(1); // charred
    expect(row(0, 4)[0]).toBe(runtimes.get(tree)!.compiled.index);
  });

  it("registers each instance in every grid cell its footprint touches", () => {
    const tree = defs.find((d) => d.id.startsWith("tree"))!.id;
    const entities: WorldEntity[] = [];
    for (let i = 0; i < 300; i++) entities.push(entity(`e${i}`, tree, ((i * 0.618034) % 1) * 300 - 150, ((i * 0.414214) % 1) * 300 - 150));
    const inst = buildInstances(entities, runtimes);
    const grid = buildGrid(inst);
    expect(grid.overflow).toBe(0);
    const slots = GRID_CAPACITY / 4;
    const cellHas = (cx: number, cz: number, index: number) => {
      const o = (cz * grid.dimX * slots + cx * slots) * 4;
      return Array.from(grid.data.slice(o, o + GRID_CAPACITY)).includes(index);
    };
    for (let i = 0; i < inst.count; i++) {
      const [x, z, r] = [inst.footprints[i * 3], inst.footprints[i * 3 + 1], inst.footprints[i * 3 + 2]];
      // sample points on and inside the footprint circle: their cells must list the instance
      for (let a = 0; a < 16; a++) {
        for (const f of [0, 0.5, 0.999]) {
          const px = x + Math.cos((a / 16) * 2 * Math.PI) * r * f;
          const pz = z + Math.sin((a / 16) * 2 * Math.PI) * r * f;
          const cx = Math.floor((px - grid.originX) / grid.cell);
          const cz = Math.floor((pz - grid.originZ) / grid.cell);
          expect(cx >= 0 && cz >= 0 && cx < grid.dimX && cz < grid.dimZ).toBe(true);
          expect(cellHas(cx, cz, i)).toBe(true);
        }
      }
    }
  });

  it("reports overflow instead of silently dropping instances", () => {
    const tree = defs.find((d) => d.id.startsWith("tree"))!.id;
    const crowd = Array.from({ length: GRID_CAPACITY + 5 }, (_, i) => entity(`c${i}`, tree, 1 + i * 0.01, 1));
    const grid = buildGrid(buildInstances(crowd, runtimes));
    expect(grid.overflow).toBeGreaterThan(0);
    expect(grid.maxPerCell).toBe(GRID_CAPACITY);
  });
});
