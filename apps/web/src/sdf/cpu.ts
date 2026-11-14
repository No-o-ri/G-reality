import type { GeometryDefinition, GeometryNode } from '@contracts';
import { fbm3, warp3 } from './noise';

export type Vec3 = [number, number, number];
export interface EvalResult { d: number; material: number }
export type SdfFn = (p: Vec3, params: Record<string, number>) => EvalResult;
type Scalar = (params: Record<string, number>) => number;
const clamp = (x: number, lo = 0, hi = 1) => Math.min(hi, Math.max(lo, x));
const mix = (a: number, b: number, h: number) => a * (1 - h) + b * h;
const box = (x: number, y: number, z: number, a: number, b: number, c: number) => {
  const qx = Math.abs(x) - a, qy = Math.abs(y) - b, qz = Math.abs(z) - c;
  return Math.hypot(Math.max(qx, 0), Math.max(qy, 0), Math.max(qz, 0)) + Math.min(Math.max(qx, qy, qz), 0);
};

/** Compile the AST once. Each closure owns its result and domain scratch point;
 * only the public call allocates a result, so callers can retain it safely. */
export function compileCpu(def: GeometryDefinition): SdfFn {
  const scalar = (v: unknown): Scalar => {
    if (typeof v === 'number') return () => v;
    if (v && typeof v === 'object' && 'ref' in v) {
      const ref = String(v.ref), fallback = def.parameters?.[ref]?.default;
      if (fallback === undefined) throw new Error(`Unknown parameter: ${ref}`);
      return params => params[ref] ?? fallback;
    }
    throw new Error('Expected scalar or parameter reference');
  };
  const build = (node: GeometryNode, inherited: number): SdfFn => {
    const material = node.material == null ? inherited : def.materials.findIndex(m => m.id === node.material);
    if (material < 0) throw new Error(`Unknown material: ${node.material}`);
    const children = (node.children ?? []).map(n => build(n, material));
    const p = node.params ?? {};
    const s = (name: string) => scalar(p[name]);
    const v = (name: string) => (p[name] as unknown[]).map(scalar);
    const out: EvalResult = { d: 0, material };
    let distance: (point: Vec3, params: Record<string, number>) => number;
    switch (node.op) {
      case 'sphere': { const r = s('radius'); distance = (p, a) => Math.hypot(...p) - r(a); break; }
      case 'ellipsoid': {
        const r = v('radii');
        distance = (p, a) => {
          const x = r[0](a), y = r[1](a), z = r[2](a);
          const k0 = Math.hypot(p[0] / x, p[1] / y, p[2] / z);
          const k1 = Math.hypot(p[0] / (x * x), p[1] / (y * y), p[2] / (z * z));
          return k0 * (k0 - 1) / Math.max(k1, 1e-6);
        }; break;
      }
      case 'box': case 'round_box': {
        const b = v('half'), r = node.op === 'round_box' ? s('radius') : () => 0;
        distance = (p, a) => box(...p, b[0](a), b[1](a), b[2](a)) - r(a); break;
      }
      case 'capsule': {
        const av = v('a'), bv = v('b'), r = s('radius');
        distance = (p, a) => {
          const ax = av[0](a), ay = av[1](a), az = av[2](a);
          const x = p[0] - ax, y = p[1] - ay, z = p[2] - az;
          const bx = bv[0](a) - ax, by = bv[1](a) - ay, bz = bv[2](a) - az;
          const h = clamp((x * bx + y * by + z * bz) / Math.max(bx * bx + by * by + bz * bz, 1e-8));
          return Math.hypot(x - bx * h, y - by * h, z - bz * h) - r(a);
        }; break;
      }
      case 'cylinder': {
        const h = s('half_height'), r = s('radius');
        distance = (p, a) => {
          const x = Math.hypot(p[0], p[2]) - r(a), y = Math.abs(p[1]) - h(a);
          return Math.min(Math.max(x, y), 0) + Math.hypot(Math.max(x, 0), Math.max(y, 0));
        }; break;
      }
      case 'cone': {
        const height = s('half_height'), bottom = s('radius_bottom'), top = s('radius_top');
        distance = (p, a) => {
          const h = height(a), r1 = bottom(a), r2 = top(a), qx = Math.hypot(p[0], p[2]), qy = p[1];
          const kx = r2 - r1, ky = 2 * h;
          const cax = qx - Math.min(qx, qy < 0 ? r1 : r2), cay = Math.abs(qy) - h;
          const t = clamp(((r2 - qx) * kx + (h - qy) * ky) / (kx * kx + ky * ky));
          const cbx = qx - r2 + kx * t, cby = qy - h + ky * t;
          return (cbx < 0 && cay < 0 ? -1 : 1) * Math.sqrt(Math.min(cax * cax + cay * cay, cbx * cbx + cby * cby));
        }; break;
      }
      case 'torus': {
        const major = s('major'), minor = s('minor');
        distance = (p, a) => Math.hypot(Math.hypot(p[0], p[2]) - major(a), p[1]) - minor(a); break;
      }
      case 'plane': {
        const n = v('normal'), offset = s('offset');
        distance = (p, a) => {
          const x = n[0](a), y = n[1](a), z = n[2](a);
          return (p[0] * x + p[1] * y + p[2] * z) / Math.hypot(x, y, z) - offset(a);
        }; break;
      }
      case 'union': case 'intersect': case 'subtract': case 'smooth_union': case 'smooth_intersect': case 'smooth_subtract': {
        const smooth = node.op.startsWith('smooth_'), k = smooth ? s('k') : () => 0;
        const subtract = node.op.endsWith('subtract'), intersect = node.op.endsWith('intersect');
        return (p, params) => {
          const first = children[0](p, params); let d = first.d, m = first.material;
          // Material selection uses raw child distances, independently of the smoothed fold.
          let selected = d;
          for (let i = 1; i < children.length; i++) {
            const child = children[i](p, params), b = child.d;
            if (!subtract && (intersect ? b > selected : b < selected)) { selected = b; m = child.material; }
            if (!smooth) d = subtract ? Math.max(d, -b) : intersect ? Math.max(d, b) : Math.min(d, b);
            else {
              const width = k(params);
              if (subtract) { const h = clamp(0.5 - 0.5 * (d + b) / width); d = mix(d, -b, h) + width * h * (1 - h); }
              else { const h = clamp(0.5 + (intersect ? -0.5 : 0.5) * (b - d) / width); d = mix(b, d, h) + (intersect ? 1 : -1) * width * h * (1 - h); }
            }
          }
          out.d = d; out.material = m; return out;
        };
      }
      case 'round': case 'shell': case 'displace': {
        const amount = s(node.op === 'round' ? 'radius' : node.op === 'shell' ? 'thickness' : 'amplitude');
        const frequency = node.op === 'displace' ? s('frequency') : () => 0;
        const octaves = node.op === 'displace' ? s('octaves') : () => 0;
        const seed = node.op === 'displace' ? s('seed') : () => 0;
        const q: Vec3 = [0, 0, 0];
        return (p, a) => {
          const child = children[0](p, a); out.material = child.material;
          if (node.op === 'round') out.d = child.d - amount(a);
          else if (node.op === 'shell') out.d = Math.abs(child.d) - amount(a);
          else { const f = frequency(a); q[0] = p[0] * f; q[1] = p[1] * f; q[2] = p[2] * f; out.d = child.d + amount(a) * fbm3(q, octaves(a), seed(a)); }
          return out;
        };
      }
      case 'translate': case 'rotate': case 'scale': case 'repeat': case 'mirror': case 'twist': case 'bend': case 'warp': {
        const q: Vec3 = [0, 0, 0];
        const offset = node.op === 'translate' ? v('offset') : [];
        const period = node.op === 'repeat' ? v('period') : [], count = node.op === 'repeat' ? v('count') : [];
        const factor = node.op === 'scale' ? s('factor') : () => 1;
        const rate = node.op === 'twist' || node.op === 'bend' ? s('rate') : () => 0;
        const amplitude = node.op === 'warp' ? s('amplitude') : () => 0;
        const frequency = node.op === 'warp' ? s('frequency') : () => 0;
        const seed = node.op === 'warp' ? s('seed') : () => 0;
        const axis = Number(p.axis);
        const quat = node.op === 'rotate' ? (p.quat as number[]) : [0, 0, 0, 1];
        const len = Math.hypot(...quat), qx = -quat[0] / len, qy = -quat[1] / len, qz = -quat[2] / len, qw = quat[3] / len;
        return (p, a) => {
          q[0] = p[0]; q[1] = p[1]; q[2] = p[2];
          switch (node.op) {
            case 'translate': for (let i = 0; i < 3; i++) q[i] -= offset[i](a); break;
            case 'scale': for (let i = 0; i < 3; i++) q[i] /= factor(a); break;
            case 'mirror': q[axis] = Math.abs(q[axis]); break;
            case 'repeat': for (let i = 0; i < 3; i++) { const per = period[i](a), c = count[i](a); q[i] -= per * clamp(Math.round(q[i] / per), -c, c); } break;
            case 'rotate': {
              const tx = qy * p[2] - qz * p[1] + qw * p[0], ty = qz * p[0] - qx * p[2] + qw * p[1], tz = qx * p[1] - qy * p[0] + qw * p[2];
              q[0] += 2 * (qy * tz - qz * ty); q[1] += 2 * (qz * tx - qx * tz); q[2] += 2 * (qx * ty - qy * tx); break;
            }
            case 'twist': case 'bend': {
              const angle = rate(a) * p[node.op === 'twist' ? 1 : 0], c = Math.cos(angle), s = Math.sin(angle);
              if (node.op === 'twist') { q[0] = c * p[0] - s * p[2]; q[2] = s * p[0] + c * p[2]; }
              else { q[0] = c * p[0] - s * p[1]; q[1] = s * p[0] + c * p[1]; } break;
            }
            case 'warp': { const w = warp3(p, amplitude(a), frequency(a), seed(a)); q[0] = w[0]; q[1] = w[1]; q[2] = w[2]; break; }
          }
          const child = children[0](q, a); out.d = child.d * factor(a); out.material = child.material; return out;
        };
      }
      default: throw new Error(`Unsupported geometry op: ${node.op}`);
    }
    return (p, params) => { out.d = distance(p, params); return out; };
  };
  const root = build(def.root, 0);
  return (p, params) => { const r = root(p, params); return { d: r.d, material: r.material }; };
}

export function stepScale(def: GeometryDefinition): number {
  const visit = (n: GeometryNode): number => Math.min(
    n.op === 'ellipsoid' ? 0.8 : n.op === 'twist' || n.op === 'bend' ? 0.6 : n.op === 'warp' || n.op === 'displace' ? 0.7 : 1,
    ...(n.children ?? []).map(visit));
  return visit(def.root);
}

/** Sampling bound within the declared bounding cube, padded by a full cell. */
export function estimateBounds(def: GeometryDefinition, params?: Record<string, number>): { min: Vec3; max: Vec3 } {
  const values = params ?? Object.fromEntries(Object.entries(def.parameters ?? {}).map(([k, v]) => [k, v.max]));
  const R = def.bound_radius, cell = 2 * R / 28, diagonal = Math.sqrt(3) * cell;
  const min: Vec3 = [R, R, R], max: Vec3 = [-R, -R, -R], p: Vec3 = [0, 0, 0], evaluate = compileCpu(def);
  let found = false;
  for (let x = 0; x < 28; x++) for (let y = 0; y < 28; y++) for (let z = 0; z < 28; z++) {
    p[0] = -R + (x + 0.5) * cell; p[1] = -R + (y + 0.5) * cell; p[2] = -R + (z + 0.5) * cell;
    if (evaluate(p, values).d <= diagonal) {
      found = true;
      for (let i = 0; i < 3; i++) { min[i] = Math.min(min[i], p[i] - 1.5 * cell); max[i] = Math.max(max[i], p[i] + 1.5 * cell); }
    }
  }
  if (!found) return { min: [-R, -R, -R], max: [R, R, R] };
  for (let i = 0; i < 3; i++) { min[i] = Math.max(-R, min[i]); max[i] = Math.min(R, max[i]); }
  return { min, max };
}
