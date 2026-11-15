import { describe, expect, it } from "vitest";
import { buildRig, type Vec3 } from "../src/capture/rig";

const lo: Vec3 = [-2, -10, -2];
const hi: Vec3 = [66, 30, 66];
const center = [32, 10, 32];

describe("capture rig", () => {
  const rig = buildRig(lo, hi);
  it("has 24 training and 4 held-out views with unique indices", () => {
    expect(rig.views).toHaveLength(28);
    expect(rig.views.filter((v) => v.holdout)).toHaveLength(4);
    expect(new Set(rig.views.map((v) => v.index)).size).toBe(28);
  });
  it("puts held-out cameras at poses no training camera uses", () => {
    const train = rig.views.filter((v) => !v.holdout);
    for (const h of rig.views.filter((v) => v.holdout)) {
      for (const t of train) expect(t.elevationDeg === h.elevationDeg && t.azimuthDeg === h.azimuthDeg).toBe(false);
      expect(train.some((t) => t.elevationDeg === h.elevationDeg)).toBe(false);
    }
  });
  it("looks at the clip-box center from outside its bounding sphere, within near/far", () => {
    const radius = Math.hypot(hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2]) / 2;
    for (const v of rig.views) {
      expect(v.target).toEqual(center);
      const d = Math.hypot(v.eye[0] - center[0], v.eye[1] - center[1], v.eye[2] - center[2]);
      expect(d).toBeGreaterThan(radius);
      expect(d - radius).toBeGreaterThanOrEqual(rig.near - 1e-9);
      expect(d + radius).toBeLessThanOrEqual(rig.far);
      expect(v.eye[1]).toBeGreaterThan(center[1]);
      // the bounding sphere fits the field of view
      expect(Math.asin(radius / d)).toBeLessThanOrEqual(((rig.fovDeg / 2) * Math.PI) / 180 + 1e-9);
    }
  });
});
