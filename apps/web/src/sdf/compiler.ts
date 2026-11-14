/**
 * Geometry AST → GLSL compiler.
 *
 * Input is contract-validated data (GeometryDefinition). Output is generated from a fixed template per
 * op; no string from the AST is ever copied into the shader except numbers (formatted here) and
 * identifiers that this file invents. Unknown ops throw.
 */
import type { GeometryDefinition, GeometryNode, MaterialDefinition } from "@contracts";

export const MAX_ARCHETYPE_PARAMS = 4;

const STEP_SCALE: Record<string, number> = { ellipsoid: 0.8, twist: 0.6, bend: 0.6, warp: 0.7, displace: 0.7 };

export interface CompiledArchetype {
  id: string;
  /** dense index used in the instance buffer and the GLSL dispatch switch */
  index: number;
  fnName: string;
  glsl: string;
  /** parameter name → component of the `prm` vec4, in declaration order */
  paramNames: string[];
  /** index of this archetype's first material in the scene material table */
  materialBase: number;
  stepScale: number;
  nodeCount: number;
}

export interface CompiledScene {
  archetypes: Map<string, CompiledArchetype>;
  materials: MaterialDefinition[];
  /** archetype functions + evalArchetype(int, vec3, vec4) + materialColor/materialExtra(int) */
  glsl: string;
}

type Scalar = number | { ref: string };

/** GLSL float literal. Rejects anything that is not a finite number. */
export function glslFloat(value: unknown): string {
  if (typeof value !== "number" || !Number.isFinite(value)) throw new Error(`not a finite number: ${String(value)}`);
  if (Number.isInteger(value)) return value.toFixed(1);
  const s = String(value);
  return /[.e]/.test(s) ? s : `${s}.0`;
}

interface Emitted {
  d: string;
  /** material as a GLSL float expression */
  m: string;
  /** set when the material is a compile-time constant */
  constMat: number | null;
}

class ArchetypeBuilder {
  lines: string[] = [];
  counter = 0;
  nodeCount = 0;
  stepScale = 1;

  constructor(
    private readonly params: string[],
    private readonly materialIndex: Map<string, number>,
  ) {}

  private id(prefix: string): string {
    this.counter += 1;
    return `${prefix}${this.counter}`;
  }

  private scalar(value: unknown): string {
    if (value !== null && typeof value === "object" && "ref" in (value as object)) {
      const index = this.params.indexOf((value as { ref: string }).ref);
      if (index < 0) throw new Error(`unknown parameter reference '${String((value as { ref: string }).ref)}'`);
      return `prm.${"xyzw"[index]}`;
    }
    return glslFloat(value);
  }

  private vec3(value: unknown): string {
    if (!Array.isArray(value) || value.length !== 3) throw new Error("expected a vec3");
    return `vec3(${(value as Scalar[]).map((v) => this.scalar(v)).join(", ")})`;
  }

  private int(value: unknown, lo: number, hi: number): number {
    if (typeof value !== "number" || !Number.isInteger(value) || value < lo || value > hi) {
      throw new Error(`expected an integer in [${lo}, ${hi}]`);
    }
    return value;
  }

  private leaf(expr: string, mat: number): Emitted {
    const d = this.id("d");
    this.lines.push(`float ${d} = ${expr};`);
    return { d, m: glslFloat(mat), constMat: mat };
  }

  /** combine two children; `pick` is the GLSL condition under which the material of `a` is used */
  private combine(a: Emitted, b: Emitted, expr: string, pick: string | "first"): Emitted {
    const d = this.id("d");
    this.lines.push(`float ${d} = ${expr};`);
    if (pick === "first") return { d, m: a.m, constMat: a.constMat };
    if (a.constMat !== null && a.constMat === b.constMat) return { d, m: a.m, constMat: a.constMat };
    const m = this.id("m");
    this.lines.push(`float ${m} = (${pick}) ? ${a.m} : ${b.m};`);
    return { d, m, constMat: null };
  }

  private domain(node: GeometryNode, p: string, mat: number, expr: string): Emitted {
    const q = this.id("p");
    this.lines.push(`vec3 ${q} = ${expr};`);
    return this.emit(child(node, 0), q, mat);
  }

  emit(node: GeometryNode, p: string, inherited: number): Emitted {
    this.nodeCount += 1;
    const op = node.op;
    const prm = (node.params ?? {}) as Record<string, unknown>;
    let mat = inherited;
    if (node.material != null) {
      const index = this.materialIndex.get(node.material);
      if (index === undefined) throw new Error(`unknown material '${node.material}'`);
      mat = index;
    }
    this.stepScale = Math.min(this.stepScale, STEP_SCALE[op] ?? 1);
    const kids = (node.children ?? []) as GeometryNode[];

    switch (op) {
      case "sphere":
        return this.leaf(`sdSphere(${p}, ${this.scalar(prm.radius)})`, mat);
      case "ellipsoid":
        return this.leaf(`sdEllipsoid(${p}, ${this.vec3(prm.radii)})`, mat);
      case "box":
        return this.leaf(`sdBox(${p}, ${this.vec3(prm.half)})`, mat);
      case "round_box":
        return this.leaf(`sdRoundBox(${p}, ${this.vec3(prm.half)}, ${this.scalar(prm.radius)})`, mat);
      case "capsule":
        return this.leaf(`sdCapsule(${p}, ${this.vec3(prm.a)}, ${this.vec3(prm.b)}, ${this.scalar(prm.radius)})`, mat);
      case "cylinder":
        return this.leaf(`sdCylinder(${p}, ${this.scalar(prm.half_height)}, ${this.scalar(prm.radius)})`, mat);
      case "cone":
        return this.leaf(
          `sdCone(${p}, ${this.scalar(prm.half_height)}, ${this.scalar(prm.radius_bottom)}, ${this.scalar(prm.radius_top)})`,
          mat,
        );
      case "torus":
        return this.leaf(`sdTorus(${p}, ${this.scalar(prm.major)}, ${this.scalar(prm.minor)})`, mat);
      case "plane": {
        const n = prm.normal;
        if (Array.isArray(n) && n.every((c) => typeof c === "number") && Math.hypot(...(n as number[])) < 1e-6) {
          throw new Error("plane normal must be non-zero");
        }
        return this.leaf(`sdPlane(${p}, ${this.vec3(prm.normal)}, ${this.scalar(prm.offset)})`, mat);
      }

      case "union":
      case "intersect":
      case "smooth_union": {
        if (kids.length < 2) throw new Error(`${op} needs at least 2 children`);
        let acc = this.emit(kids[0], p, mat);
        for (let i = 1; i < kids.length; i++) {
          const next = this.emit(kids[i], p, mat);
          if (op === "union") acc = this.combine(acc, next, `min(${acc.d}, ${next.d})`, `${acc.d} < ${next.d}`);
          else if (op === "intersect") acc = this.combine(acc, next, `max(${acc.d}, ${next.d})`, `${acc.d} > ${next.d}`);
          else {
            acc = this.combine(acc, next, `opSmoothUnion(${acc.d}, ${next.d}, ${this.scalar(prm.k)})`, `${acc.d} < ${next.d}`);
          }
        }
        return acc;
      }
      case "subtract":
      case "smooth_subtract":
      case "smooth_intersect": {
        if (kids.length !== 2) throw new Error(`${op} needs exactly 2 children`);
        const a = this.emit(kids[0], p, mat);
        const b = this.emit(kids[1], p, mat);
        if (op === "subtract") return this.combine(a, b, `max(${a.d}, -${b.d})`, "first");
        if (op === "smooth_subtract") {
          return this.combine(a, b, `opSmoothSubtract(${a.d}, ${b.d}, ${this.scalar(prm.k)})`, "first");
        }
        return this.combine(a, b, `opSmoothIntersect(${a.d}, ${b.d}, ${this.scalar(prm.k)})`, `${a.d} > ${b.d}`);
      }

      case "translate":
        return this.domain(node, p, mat, `${p} - ${this.vec3(prm.offset)}`);
      case "rotate": {
        const q = prm.quat;
        if (!Array.isArray(q) || q.length !== 4) throw new Error("rotate needs a quaternion");
        const len = Math.hypot(...(q as number[]));
        if (!(len > 1e-6)) throw new Error("zero quaternion");
        const n = (q as number[]).map((c) => glslFloat(c / len)).join(", ");
        return this.domain(node, p, mat, `quatRotateInv(vec4(${n}), ${p})`);
      }
      case "scale": {
        const s = this.scalar(prm.factor);
        const inner = this.domain(node, p, mat, `${p} / ${s}`);
        const d = this.id("d");
        this.lines.push(`float ${d} = ${inner.d} * ${s};`);
        return { ...inner, d };
      }
      case "repeat":
        return this.domain(node, p, mat, `opRepeat(${p}, ${this.vec3(prm.period)}, ${this.vec3(prm.count)})`);
      case "mirror": {
        const axis = "xyz"[this.int(prm.axis, 0, 2)];
        const q = this.id("p");
        this.lines.push(`vec3 ${q} = ${p};`, `${q}.${axis} = abs(${q}.${axis});`);
        return this.emit(child(node, 0), q, mat);
      }
      case "twist":
        return this.domain(node, p, mat, `opTwist(${p}, ${this.scalar(prm.rate)})`);
      case "bend":
        return this.domain(node, p, mat, `opBend(${p}, ${this.scalar(prm.rate)})`);
      case "warp":
        return this.domain(
          node, p, mat,
          `warp3(${p}, ${this.scalar(prm.amplitude)}, ${this.scalar(prm.frequency)}, ${glslFloat(this.int(prm.seed, 0, 65535))})`,
        );

      case "displace": {
        const inner = this.emit(child(node, 0), p, mat);
        const d = this.id("d");
        const oct = this.int(prm.octaves, 1, 5);
        this.lines.push(
          `float ${d} = ${inner.d} + ${this.scalar(prm.amplitude)} * fbm3(${p} * ${this.scalar(prm.frequency)}, ${oct}, ${glslFloat(this.int(prm.seed, 0, 65535))});`,
        );
        return { ...inner, d };
      }
      case "round": {
        const inner = this.emit(child(node, 0), p, mat);
        const d = this.id("d");
        this.lines.push(`float ${d} = ${inner.d} - ${this.scalar(prm.radius)};`);
        return { ...inner, d };
      }
      case "shell": {
        const inner = this.emit(child(node, 0), p, mat);
        const d = this.id("d");
        this.lines.push(`float ${d} = abs(${inner.d}) - ${this.scalar(prm.thickness)};`);
        return { ...inner, d };
      }
      default:
        throw new Error(`unknown op '${String(op)}'`);
    }
  }
}

function child(node: GeometryNode, index: number): GeometryNode {
  const kids = (node.children ?? []) as GeometryNode[];
  if (!kids[index]) throw new Error(`op '${node.op}' is missing child ${index}`);
  return kids[index];
}

export function compileArchetype(def: GeometryDefinition, index: number, materialBase: number): CompiledArchetype {
  const paramNames = Object.keys(def.parameters ?? {});
  if (paramNames.length > MAX_ARCHETYPE_PARAMS) {
    throw new Error(`archetype '${def.id}' declares ${paramNames.length} parameters; at most ${MAX_ARCHETYPE_PARAMS} are supported`);
  }
  const materialIndex = new Map<string, number>();
  def.materials.forEach((m, i) => materialIndex.set(m.id, materialBase + i));
  const builder = new ArchetypeBuilder(paramNames, materialIndex);
  const result = builder.emit(def.root as GeometryNode, "p", materialBase);
  const fnName = `sdArch_${index}`;
  const glsl = [
    `// archetype ${index} (${builder.nodeCount} nodes)`,
    `vec2 ${fnName}(vec3 p, vec4 prm) {`,
    ...builder.lines.map((l) => `  ${l}`),
    `  return vec2(${result.d} * ${glslFloat(builder.stepScale)}, ${result.m});`,
    `}`,
  ].join("\n");
  return { id: def.id, index, fnName, glsl, paramNames, materialBase, stepScale: builder.stepScale, nodeCount: builder.nodeCount };
}

/** Compile every archetype of a world plus extra scene materials (terrain etc., appended after them). */
export function compileScene(defs: GeometryDefinition[], extraMaterials: MaterialDefinition[] = []): CompiledScene {
  const archetypes = new Map<string, CompiledArchetype>();
  const materials: MaterialDefinition[] = [];
  const functions: string[] = [];
  defs.forEach((def, index) => {
    const compiled = compileArchetype(def, index, materials.length);
    materials.push(...def.materials);
    archetypes.set(def.id, compiled);
    functions.push(compiled.glsl);
  });
  materials.push(...extraMaterials);

  const cases = [...archetypes.values()].map((a) => `    case ${a.index}: return ${a.fnName}(p, prm);`).join("\n");
  const color = materials
    .map((m, i) => `    case ${i}: return vec4(${(m.base_color as number[]).map(glslFloat).join(", ")}, ${glslFloat(m.roughness ?? 0.8)});`)
    .join("\n");
  const extra = materials
    .map((m, i) => {
      const e = (m.emissive ?? [0, 0, 0]) as number[];
      const strength = m.emissive_strength ?? 0;
      const lum = Math.max(e[0], e[1], e[2]) * strength;
      return `    case ${i}: return vec4(${glslFloat(m.variation ?? 0)}, ${glslFloat(m.variation_frequency ?? 1)}, ${glslFloat(lum)}, 0.0);`;
    })
    .join("\n");

  const glsl = `${functions.join("\n\n")}

vec2 evalArchetype(int id, vec3 p, vec4 prm) {
  switch (id) {
${cases || "    case 0: return vec2(1e9, 0.0);"}
  }
  return vec2(1e9, 0.0);
}
// rgb = linear base color, a = roughness
vec4 materialColor(int m) {
  switch (m) {
${color || "    case 0: return vec4(0.5, 0.5, 0.5, 0.8);"}
  }
  return vec4(1.0, 0.0, 1.0, 0.8);
}
// x = variation amplitude, y = variation frequency, z = emissive luminance
vec4 materialExtra(int m) {
  switch (m) {
${extra || "    case 0: return vec4(0.0);"}
  }
  return vec4(0.0);
}`;
  return { archetypes, materials, glsl };
}
