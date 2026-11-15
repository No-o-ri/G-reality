/**
 * Camera rig for Gaussian reconstruction of one region (docs/architecture §5.2 step 2).
 *
 * 24 training cameras on two rings and a top cap plus 4 held-out cameras at azimuths and an elevation that no
 * training camera uses, so the held-out PSNR measures novel-view quality rather than memorization. All cameras look
 * at the center of the clip box from a distance at which its bounding sphere fills the field of view.
 */
export type Vec3 = [number, number, number];

export interface RigView {
  index: number;
  eye: Vec3;
  target: Vec3;
  holdout: boolean;
  elevationDeg: number;
  azimuthDeg: number;
}

export interface CaptureRig {
  fovDeg: number;
  distance: number;
  near: number;
  far: number;
  views: RigView[];
}

// [count, elevation°, azimuth offset°, distance factor, holdout]
const RINGS: [number, number, number, number, boolean][] = [
  [12, 10, 0, 1.12, false],
  [8, 32, 22.5, 1.04, false],
  [4, 62, 45, 1.0, false],
  [4, 21, 15, 1.08, true],
];

export function buildRig(clipMin: Vec3, clipMax: Vec3, fovDeg = 50): CaptureRig {
  const center: Vec3 = [0, 1, 2].map((i) => (clipMin[i] + clipMax[i]) / 2) as Vec3;
  const radius = Math.hypot(clipMax[0] - clipMin[0], clipMax[1] - clipMin[1], clipMax[2] - clipMin[2]) / 2;
  const distance = radius / Math.sin(((fovDeg / 2) * Math.PI) / 180);
  const views: RigView[] = [];
  for (const [count, elev, offset, factor, holdout] of RINGS) {
    for (let i = 0; i < count; i++) {
      const az = offset + (360 * i) / count;
      const e = (elev * Math.PI) / 180;
      const a = (az * Math.PI) / 180;
      const d = distance * factor;
      views.push({
        index: views.length,
        eye: [center[0] + d * Math.cos(e) * Math.cos(a), center[1] + d * Math.sin(e), center[2] + d * Math.cos(e) * Math.sin(a)],
        target: center,
        holdout,
        elevationDeg: elev,
        azimuthDeg: az,
      });
    }
  }
  const maxFactor = Math.max(...RINGS.map((r) => r[3]));
  return {
    fovDeg,
    distance,
    near: Math.max(0.1, distance - radius - 10),
    far: distance * maxFactor + radius + 50,
    views,
  };
}
