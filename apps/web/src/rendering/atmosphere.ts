/**
 * CPU twin of the shader's single-scattering atmosphere (shader.ts `atmosphere`). Used once per world / time change to
 * derive consistent lighting inputs: the sun's colour after passing through the air (warm and dim near the horizon),
 * the zenith sky colour (ambient light) and the horizon colour (fog), so sky, light and fog agree.
 */
export type RGB = [number, number, number];

const EARTH_R = 6360e3;
const ATMO_R = 6420e3;
const BETA_R: RGB = [5.8e-6, 13.5e-6, 33.1e-6];
const BETA_M = 21e-6;

type V3 = [number, number, number];
const dot = (a: V3, b: V3) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const len = (a: V3) => Math.sqrt(dot(a, a));

function raySphere(ro: V3, rd: V3, r: number): [number, number] {
  const b = dot(ro, rd);
  const c = dot(ro, ro) - r * r;
  const h = b * b - c;
  if (h < 0) return [1e9, -1e9];
  const s = Math.sqrt(h);
  return [-b - s, -b + s];
}

/** Radiance seen along `rd` for a sun in direction `sun` with radiance `sunRadiance` (matches the shader). */
export function atmosphere(rd: V3, sun: V3, sunRadiance: number): RGB {
  const ro: V3 = [0, EARTH_R + 50, 0];
  let tMax = raySphere(ro, rd, ATMO_R)[1];
  const ground = raySphere(ro, rd, EARTH_R);
  if (ground[0] > 0) tMax = Math.min(tMax, ground[0]);
  const seg = tMax / 8;
  const mu = dot(rd, sun);
  const phaseR = (3 / (16 * Math.PI)) * (1 + mu * mu);
  const g = 0.76;
  const phaseM = ((3 / (8 * Math.PI)) * ((1 - g * g) * (1 + mu * mu))) / ((2 + g * g) * Math.pow(1 + g * g - 2 * g * mu, 1.5));
  const sumR: RGB = [0, 0, 0];
  const sumM: RGB = [0, 0, 0];
  let odR = 0;
  let odM = 0;
  for (let i = 0; i < 8; i++) {
    const t = seg * (i + 0.5);
    const x: V3 = [ro[0] + rd[0] * t, ro[1] + rd[1] * t, ro[2] + rd[2] * t];
    const h = len(x) - EARTH_R;
    const hr = Math.exp(-h / 8000) * seg;
    const hm = Math.exp(-h / 1200) * seg;
    odR += hr;
    odM += hm;
    const segL = raySphere(x, sun, ATMO_R)[1] / 4;
    let lR = 0;
    let lM = 0;
    for (let j = 0; j < 4; j++) {
      const s = segL * (j + 0.5);
      const y: V3 = [x[0] + sun[0] * s, x[1] + sun[1] * s, x[2] + sun[2] * s];
      const hy = len(y) - EARTH_R;
      lR += Math.exp(-hy / 8000) * segL;
      lM += Math.exp(-hy / 1200) * segL;
    }
    for (let k = 0; k < 3; k++) {
      const att = Math.exp(-(BETA_R[k] * (odR + lR) + BETA_M * 1.1 * (odM + lM)));
      sumR[k] += att * hr;
      sumM[k] += att * hm;
    }
  }
  return [0, 1, 2].map((k) => sunRadiance * (sumR[k] * BETA_R[k] * phaseR + sumM[k] * BETA_M * phaseM)) as RGB;
}

/** Colour of direct sunlight at the ground (transmittance along the sun direction), relative to the sun at zenith. */
export function sunTransmittance(sun: V3): RGB {
  const ro: V3 = [0, EARTH_R + 50, 0];
  const tMax = raySphere(ro, sun, ATMO_R)[1];
  const seg = tMax / 16;
  let odR = 0;
  let odM = 0;
  for (let i = 0; i < 16; i++) {
    const t = seg * (i + 0.5);
    const h = len([ro[0] + sun[0] * t, ro[1] + sun[1] * t, ro[2] + sun[2] * t]) - EARTH_R;
    odR += Math.exp(-h / 8000) * seg;
    odM += Math.exp(-h / 1200) * seg;
  }
  const zenith = [0.869, 0.761, 0.578]; // transmittance straight up, normalised so midday sun is ~white
  return [0, 1, 2].map((k) => Math.exp(-(BETA_R[k] * odR + BETA_M * 1.1 * odM)) / zenith[k]) as RGB;
}

export interface SkyLighting {
  sunRadiance: number; // input to the shader's sky model
  sun: RGB; // direct light colour × intensity used for shading
  zenith: RGB; // ambient sky colour
  horizon: RGB; // fog / aerial perspective colour
  daylight: number;
}

/** All lighting inputs for a sun direction and daylight factor (0 night .. 1 day). */
export function skyLighting(sun: V3, daylight: number): SkyLighting {
  const sunRadiance = 22 * Math.max(0.02, daylight);
  const t = sunTransmittance(sun);
  const moon: RGB = [0.05, 0.065, 0.1];
  const sunCol = t.map((v, k) => v * 1.05 * daylight + moon[k] * (1 - daylight)) as RGB;
  const zenith = atmosphere([0, 1, 0], sun, sunRadiance);
  // horizon looking away from the sun (perpendicular in azimuth): the typical fog colour
  const az = Math.atan2(sun[2], sun[0]) + Math.PI / 2;
  const horizon = atmosphere([Math.cos(az) * 0.995, 0.1, Math.sin(az) * 0.995], sun, sunRadiance);
  const night: RGB = [0.012, 0.018, 0.035];
  return {
    sunRadiance,
    sun: sunCol,
    zenith: zenith.map((v, k) => v + night[k] * (1 - daylight)) as RGB,
    horizon: horizon.map((v, k) => v + night[k] * (1 - daylight)) as RGB,
    daylight,
  };
}
