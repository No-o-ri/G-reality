/**
 * GPU data for entity instances and the XZ acceleration grid.
 *
 * Instances are data, not code: one row of INSTANCE_TEXELS RGBA32F texels per entity. A world mutation
 * rewrites a row (or rebuilds these arrays); the shader program is untouched.
 *
 *   texel 0: position.xyz, uniform scale
 *   texel 1: rotation quaternion (x, y, z, w)
 *   texel 2: world-space bounding sphere center.xyz, radius
 *   texel 3: archetype parameters (up to 4, in declaration order)
 *   texel 4: archetype index, burning flag (0/1), burn visual (0..1), unused
 */
import type { WorldEntity } from "@contracts";
import type { CompiledArchetype } from "../../sdf/compiler";
import type { ArchetypeMeasure } from "../../sdf/measure";

export const INSTANCE_TEXELS = 8;
export const GRID_CELL_M = 8;
export const GRID_CAPACITY = 16; // instance slots per cell (4 texels)
export const MAX_GRID_DIM = 96;

export type Vec3 = [number, number, number];

export interface ArchetypeRuntime {
  compiled: CompiledArchetype;
  /** measured with default parameters: used for entities that do not override any parameter */
  base: ArchetypeMeasure;
  /** measured with every parameter at its maximum: used for entities that override parameters */
  widest: ArchetypeMeasure;
  paramDefaults: number[];
  paramMin: number[];
  paramMax: number[];
}

export interface InstanceData {
  data: Float32Array;
  count: number;
  /** entity id for each instance row */
  ids: string[];
  /** world bounding spheres (x, y, z, r) per instance, for shader culling and CPU picking */
  spheres: Float32Array;
  /** XZ footprint circles (x, z, r) per instance, for grid registration */
  footprints: Float32Array;
  skipped: number;
}

function rotate(q: readonly number[], v: Vec3): Vec3 {
  const [x, y, z, w] = q;
  // v + 2 * cross(q.xyz, cross(q.xyz, v) + w * v)
  const cx = y * v[2] - z * v[1] + w * v[0];
  const cy = z * v[0] - x * v[2] + w * v[1];
  const cz = x * v[1] - y * v[0] + w * v[2];
  return [v[0] + 2 * (y * cz - z * cy), v[1] + 2 * (z * cx - x * cz), v[2] + 2 * (x * cy - y * cx)];
}

export function buildInstances(entities: WorldEntity[], archetypes: Map<string, ArchetypeRuntime>): InstanceData {
  const rows = Math.max(1, entities.length);
  const data = new Float32Array(rows * INSTANCE_TEXELS * 4);
  const spheres = new Float32Array(rows * 4);
  const footprints = new Float32Array(rows * 3);
  const ids: string[] = [];
  let count = 0;
  let skipped = 0;
  for (const e of entities) {
    const state = e.state ?? {};
    const condition = state.condition ?? "intact";
    const rt = archetypes.get(e.geometry_id);
    if (condition === "removed" || !rt) {
      if (!rt) skipped += 1;
      continue;
    }
    const o = count * INSTANCE_TEXELS * 4;
    const pos = e.transform.position as Vec3;
    const q = (e.transform.rotation ?? [0, 0, 0, 1]) as number[];
    const scale = e.transform.scale ?? 1;
    data.set([pos[0], pos[1], pos[2], scale], o);
    data.set(q, o + 4);

    const params = (e.geometry_params ?? {}) as Record<string, number>;
    const overridden = rt.compiled.paramNames.some((name, i) => params[name] !== undefined && params[name] > rt.paramDefaults[i]);
    const m = overridden ? rt.widest : rt.base;
    const c = rotate(q, [0, m.sphereCenterY * scale, 0]);
    const sphere = [pos[0] + c[0], pos[1] + c[1], pos[2] + c[2], m.sphereRadius * scale];
    data.set(sphere, o + 8);
    spheres.set(sphere, count * 4);
    // Footprint cylinder is exact for yaw-only rotations; tilted entities fall back to the sphere.
    const upright = Math.abs(q[0]) < 1e-4 && Math.abs(q[2]) < 1e-4;
    footprints.set(upright ? [pos[0], pos[2], m.footprint * scale] : [sphere[0], sphere[2], sphere[3]], count * 3);

    rt.compiled.paramNames.forEach((name, i) => {
      const v = params[name] ?? rt.paramDefaults[i];
      data[o + 12 + i] = Math.min(rt.paramMax[i], Math.max(rt.paramMin[i], v));
    });

    const burning = condition === "burning";
    const burnVisual = condition === "charred" ? 1 : burning ? Math.max(0.2, params.burn ?? 0) : (params.burn ?? 0);
    data.set([rt.compiled.index, burning ? 1 : 0, burnVisual, 0], o + 16);
    ids.push(e.id);
    count += 1;
  }
  return { data, count, ids, spheres, footprints, skipped };
}

export interface GridData {
  data: Float32Array;
  originX: number;
  originZ: number;
  dimX: number;
  dimZ: number;
  cell: number;
  /** instances that did not fit into a full cell (should be 0; reported in diagnostics) */
  overflow: number;
  maxPerCell: number;
}

/** Register every instance in each cell its XZ footprint circle overlaps. Empty slots hold -1. */
export function buildGrid(inst: InstanceData, cell = GRID_CELL_M): GridData {
  let minX = Infinity, minZ = Infinity, maxX = -Infinity, maxZ = -Infinity;
  for (let i = 0; i < inst.count; i++) {
    const x = inst.footprints[i * 3], z = inst.footprints[i * 3 + 1], r = inst.footprints[i * 3 + 2];
    minX = Math.min(minX, x - r); maxX = Math.max(maxX, x + r);
    minZ = Math.min(minZ, z - r); maxZ = Math.max(maxZ, z + r);
  }
  if (inst.count === 0) {
    return { data: new Float32Array(4 * 4).fill(-1), originX: 0, originZ: 0, dimX: 1, dimZ: 1, cell, overflow: 0, maxPerCell: 0 };
  }
  // Coarsen the cell size if the populated area would exceed the maximum grid dimension.
  let size = cell;
  while (Math.ceil((maxX - minX) / size) + 2 > MAX_GRID_DIM || Math.ceil((maxZ - minZ) / size) + 2 > MAX_GRID_DIM) size *= 2;
  const originX = Math.floor(minX / size) * size - size;
  const originZ = Math.floor(minZ / size) * size - size;
  const dimX = Math.ceil((maxX - originX) / size) + 1;
  const dimZ = Math.ceil((maxZ - originZ) / size) + 1;
  const slots = GRID_CAPACITY / 4;
  const data = new Float32Array(dimX * slots * dimZ * 4).fill(-1);
  const counts = new Uint8Array(dimX * dimZ);
  let overflow = 0;
  let maxPerCell = 0;
  for (let i = 0; i < inst.count; i++) {
    const x = inst.footprints[i * 3], z = inst.footprints[i * 3 + 1], r = inst.footprints[i * 3 + 2];
    const x0 = Math.max(0, Math.floor((x - r - originX) / size)), x1 = Math.min(dimX - 1, Math.floor((x + r - originX) / size));
    const z0 = Math.max(0, Math.floor((z - r - originZ) / size)), z1 = Math.min(dimZ - 1, Math.floor((z + r - originZ) / size));
    for (let cz = z0; cz <= z1; cz++) {
      for (let cx = x0; cx <= x1; cx++) {
        // skip cells the circle does not actually reach
        const nx = Math.max(originX + cx * size, Math.min(x, originX + (cx + 1) * size));
        const nz = Math.max(originZ + cz * size, Math.min(z, originZ + (cz + 1) * size));
        if ((nx - x) ** 2 + (nz - z) ** 2 > r * r) continue;
        const n = counts[cz * dimX + cx];
        if (n >= GRID_CAPACITY) { overflow += 1; continue; }
        // texel (cx * slots + floor(n / 4), cz), component n % 4
        data[(cz * dimX * slots + cx * slots + (n >> 2)) * 4 + (n & 3)] = i;
        counts[cz * dimX + cx] = n + 1;
        maxPerCell = Math.max(maxPerCell, n + 1);
      }
    }
  }
  return { data, originX, originZ, dimX, dimZ, cell: size, overflow, maxPerCell };
}
