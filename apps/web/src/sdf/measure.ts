/**
 * Conservative size measurements of an archetype, taken from its own SDF on the CPU.
 * Used for bounding spheres (shader culling) and XZ footprints (acceleration grid registration).
 */
import type { GeometryDefinition } from "@contracts";
import { compileCpu, estimateBounds, type Vec3 } from "./cpu";

export interface ArchetypeMeasure {
  /** radius of the vertical cylinder around the local Y axis that contains the surface (scale 1) */
  footprint: number;
  yMin: number;
  yMax: number;
  /** bounding sphere of that cylinder, local space */
  sphereCenterY: number;
  sphereRadius: number;
}

const NX = 36;
const NY = 28;

export function measureArchetype(def: GeometryDefinition, params?: Record<string, number>): ArchetypeMeasure {
  const values = params ?? Object.fromEntries(Object.entries(def.parameters ?? {}).map(([k, v]) => [k, v.default]));
  const box = estimateBounds(def, values);
  const fn = compileCpu(def);
  const size: Vec3 = [box.max[0] - box.min[0], box.max[1] - box.min[1], box.max[2] - box.min[2]];
  const cell: Vec3 = [size[0] / NX, size[1] / NY, size[2] / NX];
  const diagonal = Math.hypot(cell[0], cell[1], cell[2]);
  let footprint = 0;
  let yMin = Infinity;
  let yMax = -Infinity;
  const p: Vec3 = [0, 0, 0];
  for (let ix = 0; ix < NX; ix++) {
    for (let iy = 0; iy < NY; iy++) {
      for (let iz = 0; iz < NX; iz++) {
        p[0] = box.min[0] + (ix + 0.5) * cell[0];
        p[1] = box.min[1] + (iy + 0.5) * cell[1];
        p[2] = box.min[2] + (iz + 0.5) * cell[2];
        // a cell can contain surface only if the distance at its center is below the cell diagonal
        if (fn(p, values).d > diagonal) continue;
        footprint = Math.max(footprint, Math.hypot(Math.abs(p[0]) + cell[0], Math.abs(p[2]) + cell[2]));
        yMin = Math.min(yMin, p[1] - cell[1]);
        yMax = Math.max(yMax, p[1] + cell[1]);
      }
    }
  }
  if (!(footprint > 0)) {
    // nothing found (degenerate shape): fall back to the declared bound
    footprint = def.bound_radius;
    yMin = -def.bound_radius;
    yMax = def.bound_radius;
  }
  const halfY = (yMax - yMin) / 2;
  return { footprint, yMin, yMax, sphereCenterY: (yMin + yMax) / 2, sphereRadius: Math.hypot(footprint, halfY) };
}
