"""Reference implementation of the analytic heightfield `terrain-v1` and deterministic hashing.

All arithmetic is float32 so the GLSL and TypeScript evaluators (Chunk 2) can reproduce it within the
golden-vector tolerance. Formula (also the specification):

    hash12(p):  p3 = fract(p.xyx * 0.1031); p3 += dot(p3, p3.yzx + 33.33); return fract((p3.x + p3.y) * p3.z)
    vnoise(p):  bilinear interpolation of hash12 on the integer lattice (offset by the seed), quintic fade
    field(p):   sum over octaves i of a_i * s_i, a_i = 0.5^i, sample point p * 2^i + 17 * i
                smooth s = 2n - 1, ridged s = 1 - 2 * |2n - 1|, blended by `ridge`, normalised by sum(a_i)
    warp:       p += warp * (2 * vnoise(p * frequency * 0.5 + offset) - 1) per axis, before sampling
    height:     base_height + amplitude * field(p * frequency) - basin_depth * (1 - smoothstep(0, 1, d / basin_radius))
"""

from __future__ import annotations

import hashlib
import math
import struct

import numpy as np

from greality_contracts import TerrainDefinition

F = np.float32
_K = F(0.1031)
_S = F(33.33)


def _fract(x: np.float32) -> np.float32:
    return F(x - np.floor(x))


def hash12(x: float, y: float) -> np.float32:
    x, y = F(x), F(y)
    ax, ay, az = _fract(x * _K), _fract(y * _K), _fract(x * _K)
    d = F(ax * (ay + _S) + ay * (az + _S) + az * (ax + _S))
    ax, ay, az = F(ax + d), F(ay + d), F(az + d)
    return _fract(F(ax + ay) * az)


def seed_offset(seed: int) -> tuple[float, float]:
    return float(seed & 1023), float((seed >> 10) & 1023)


def vnoise(x: float, y: float, seed: int) -> np.float32:
    x, y = F(x), F(y)
    ix, iy = np.floor(x), np.floor(y)
    fx, fy = F(x - ix), F(y - iy)
    ux = F(fx * fx * fx * (fx * (fx * F(6) - F(15)) + F(10)))
    uy = F(fy * fy * fy * (fy * (fy * F(6) - F(15)) + F(10)))
    sx, sy = seed_offset(seed)
    lx, ly = F(ix + F(sx)), F(iy + F(sy))
    a, b = hash12(lx, ly), hash12(lx + F(1), ly)
    c, d = hash12(lx, ly + F(1)), hash12(lx + F(1), ly + F(1))
    top = F(a + (b - a) * ux)
    bottom = F(c + (d - c) * ux)
    return F(top + (bottom - top) * uy)


def _smoothstep(e0: float, e1: float, x: float) -> float:
    t = min(1.0, max(0.0, (x - e0) / (e1 - e0)))
    return t * t * (3.0 - 2.0 * t)


def terrain_height(t: TerrainDefinition, seed: int, x: float, z: float) -> float:
    px, pz = F(x), F(z)
    if t.warp > 0.0:
        wf = F(t.frequency * 0.5)
        wx = F(2) * vnoise(px * wf + F(31.7), pz * wf + F(11.3), seed) - F(1)
        wz = F(2) * vnoise(px * wf + F(7.9), pz * wf + F(53.1), seed) - F(1)
        px, pz = F(px + F(t.warp) * wx), F(pz + F(t.warp) * wz)
    fx, fz = F(px * F(t.frequency)), F(pz * F(t.frequency))
    smooth = ridged = norm = F(0)
    amp, scale = F(1), F(1)
    for i in range(t.octaves):
        n = vnoise(fx * scale + F(17 * i), fz * scale + F(17 * i), seed)
        s = F(F(2) * n - F(1))
        smooth = F(smooth + amp * s)
        ridged = F(ridged + amp * (F(1) - F(2) * abs(s)))
        norm = F(norm + amp)
        amp, scale = F(amp * F(0.5)), F(scale * F(2))
    v = F((smooth * F(1 - t.ridge) + ridged * F(t.ridge)) / norm)
    h = float(F(t.base_height) + F(t.amplitude) * v)
    if t.basin_center is not None and t.basin_radius > 0.0:
        d = math.hypot(x - t.basin_center[0], z - t.basin_center[1]) / t.basin_radius
        h -= t.basin_depth * (1.0 - _smoothstep(0.0, 1.0, d))
    return h


def terrain_slope(t: TerrainDefinition, seed: int, x: float, z: float) -> float:
    """Gradient magnitude (rise over run) from 1 m central differences."""
    dx = terrain_height(t, seed, x + 1.0, z) - terrain_height(t, seed, x - 1.0, z)
    dz = terrain_height(t, seed, x, z + 1.0) - terrain_height(t, seed, x, z - 1.0)
    return math.hypot(dx, dz) * 0.5


def stable_unit(*key: object) -> float:
    """Deterministic value in [0, 1) from an arbitrary key; identical across processes and platforms."""
    digest = hashlib.blake2b("|".join(map(str, key)).encode("utf-8"), digest_size=8).digest()
    return struct.unpack("<Q", digest)[0] / 2.0**64
