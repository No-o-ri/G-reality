"""Conservative axis-aligned bounds of a geometry AST, computed symbolically (no SDF sampling).

Used server-side to check what a language model built before it is accepted: whether an object sits on its base or is
buried, how large it really is (its bound radius is derived from this instead of the model's own `size_m` estimate),
and whether it is degenerate. Parameter references take the parameter's maximum, so the box contains every entity.
The result may be larger than the true shape (domain warps, smooth blends), never smaller.
"""

from __future__ import annotations

import math
from typing import Any, Optional

Box = tuple[list[float], list[float]]  # (min xyz, max xyz)

# circumradius / inradius of the regular polyhedra (kind 0..4)
_POLY_CIRCUM = (3.0, math.sqrt(3.0), math.sqrt(3.0), 1.2584085723, 1.2584085723)


def _scalar(v: Any, params: dict[str, dict]) -> float:
    if isinstance(v, dict) and "ref" in v:
        spec = params.get(v["ref"]) or {}
        return float(spec.get("max", spec.get("default", 1.0)))
    return float(v)


def _vec(v: Any, params: dict[str, dict]) -> list[float]:
    return [_scalar(c, params) for c in v]


def _sym(x: float, y: float, z: float) -> Box:
    return [-x, -y, -z], [x, y, z]


def _hull(boxes: list[Box]) -> Optional[Box]:
    boxes = [b for b in boxes if b is not None]
    if not boxes:
        return None
    return ([min(b[0][i] for b in boxes) for i in range(3)], [max(b[1][i] for b in boxes) for i in range(3)])


def _corners(b: Box):
    for x in (b[0][0], b[1][0]):
        for y in (b[0][1], b[1][1]):
            for z in (b[0][2], b[1][2]):
                yield (x, y, z)


def _rotate(q: list[float], v) -> tuple[float, float, float]:
    x, y, z, w = q
    n = math.sqrt(x * x + y * y + z * z + w * w) or 1.0
    x, y, z, w = x / n, y / n, z / n, w / n
    vx, vy, vz = v
    # v + 2 * cross(q.xyz, cross(q.xyz, v) + w * v)
    cx, cy, cz = y * vz - z * vy + w * vx, z * vx - x * vz + w * vy, x * vy - y * vx + w * vz
    return vx + 2 * (y * cz - z * cy), vy + 2 * (z * cx - x * cz), vz + 2 * (x * cy - y * cx)


def _radial(b: Box, y_extra: float = 0.0) -> Box:
    r = max(math.hypot(c[0], c[2]) for c in _corners(b))
    return [-r, b[0][1], -r], [r, b[1][1] + y_extra, r]


def bounds(node: dict, params: Optional[dict[str, dict]] = None) -> Optional[Box]:
    """AABB in the node's local space, or None if unbounded (a bare half-space)."""
    params = params or {}
    op = node.get("op")
    p = node.get("params") or {}
    kids = [bounds(c, params) for c in node.get("children") or []]
    s = lambda name: _scalar(p[name], params)  # noqa: E731
    v = lambda name: _vec(p[name], params)  # noqa: E731
    if op == "sphere":
        return _sym(s("radius"), s("radius"), s("radius"))
    if op in ("ellipsoid", "superquadric"):
        return _sym(*v("radii"))
    if op == "box":
        return _sym(*v("half"))
    if op == "round_box":
        h, r = v("half"), s("radius")
        return _sym(h[0] + r, h[1] + r, h[2] + r)
    if op == "capsule":
        a, b, r = v("a"), v("b"), s("radius")
        return [min(a[i], b[i]) - r for i in range(3)], [max(a[i], b[i]) + r for i in range(3)]
    if op == "curve_tube":
        pts, r = [v("a"), v("b"), v("c")], max(s("radius_start"), s("radius_end"))
        return [min(q[i] for q in pts) - r for i in range(3)], [max(q[i] for q in pts) + r for i in range(3)]
    if op == "cylinder":
        return _sym(s("radius"), s("half_height"), s("radius"))
    if op == "cone":
        r = max(s("radius_bottom"), s("radius_top"))
        return _sym(r, s("half_height"), r)
    if op == "torus":
        R, r = s("major"), s("minor")
        return _sym(R + r, r, R + r)
    if op == "polyhedron":
        r = s("radius") * _POLY_CIRCUM[int(p.get("kind", 1))]
        return _sym(r, r, r)
    if op == "plane":
        return None
    if op in ("union", "smooth_union"):
        return _hull(kids)
    if op in ("intersect", "smooth_intersect"):
        boxes = [k for k in kids if k is not None]
        if not boxes:
            return None
        lo = [max(b[0][i] for b in boxes) for i in range(3)]
        hi = [min(b[1][i] for b in boxes) for i in range(3)]
        return lo, [max(lo[i], hi[i]) for i in range(3)]
    if op in ("subtract", "smooth_subtract"):
        return kids[0] if kids else None
    child = kids[0] if kids else None
    if child is None:
        return None
    lo, hi = list(child[0]), list(child[1])
    if op == "translate":
        o = v("offset")
        return [lo[i] + o[i] for i in range(3)], [hi[i] + o[i] for i in range(3)]
    if op == "rotate":
        pts = [_rotate(list(p["quat"]), c) for c in _corners(child)]
        return [min(q[i] for q in pts) for i in range(3)], [max(q[i] for q in pts) for i in range(3)]
    if op == "scale":
        f = s("factor")
        return [c * f for c in lo], [c * f for c in hi]
    if op == "stretch":
        f = v("factor")
        return [lo[i] * f[i] for i in range(3)], [hi[i] * f[i] for i in range(3)]
    if op == "mirror":
        a = int(p.get("axis", 0))
        m = max(abs(lo[a]), abs(hi[a]))
        lo[a], hi[a] = -m, m
        return lo, hi
    if op == "repeat":
        per, cnt = v("period"), v("count")
        return ([lo[i] - per[i] * round(cnt[i]) for i in range(3)], [hi[i] + per[i] * round(cnt[i]) for i in range(3)])
    if op in ("radial_repeat", "twist"):
        return _radial(child)
    if op == "spiral_repeat":
        n = max(round(s("count")), 1)
        copies = max(round(s("copies")), 1)
        return _radial(child, (copies - 1) * s("pitch") / n)
    if op == "taper":
        k = max(max(0.05, 1 + s("rate") * lo[1]), max(0.05, 1 + s("rate") * hi[1]))
        return [lo[0] * k, lo[1], lo[2] * k], [hi[0] * k, hi[1], hi[2] * k]
    if op == "bend":
        r = max(math.hypot(c[0], c[1]) for c in _corners(child))
        return [-r, -r, lo[2]], [r, r, hi[2]]
    if op in ("warp", "displace"):
        e = s("amplitude") * (1.8 if op == "warp" else 1.0)
        return [c - e for c in lo], [c + e for c in hi]
    if op == "round":
        e = s("radius")
        return [c - e for c in lo], [c + e for c in hi]
    if op == "shell":
        e = s("thickness")
        return [c - e for c in lo], [c + e for c in hi]
    return child


def radius_from_origin(box: Box) -> float:
    """Radius of the sphere around the local origin (the entity's base point) that contains the box."""
    return max(math.sqrt(sum(c * c for c in corner)) for corner in _corners(box))
