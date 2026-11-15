"""Procedural archetype generation (generator v4): every world gets its own geometry.

Instead of a fixed library of complete models, each world generates, from its seed and the requested family, several
structurally different SDF archetypes ("variants"): trees with their own trunk kinks, branch counts, angles, tier
counts and crown shapes; rocks with their own lobe arrangement and erosion; buildings with their own footprint,
storeys, roof form, openings and materials. Instances then add continuous variation (canopy fullness, tilt, scale,
yaw). Only low-level building blocks are shared (SDF primitives, CSG, noise), which is ordinary procedural generation.

Every output is validated by the GeometryDefinition contract (closed op set, node and depth limits).
"""

from __future__ import annotations

import colorsys
import math
import random

from greality_contracts import GeometryDefinition

_BURN = {"default": 0.0, "min": 0.0, "max": 1.0}
_CANOPY = {"default": 1.0, "min": 0.02, "max": 1.3}  # foliage scale; fire shrinks it (simulation.CHARRED_CANOPY)
# per-entity crown shape: independent scales of the two halves of the crown (lower/upper tiers, or two lobe sets)
_CROWN = {"default": 1.0, "min": 0.6, "max": 1.4}
PER_ENTITY_RANGES = {"canopy": (0.72, 1.18), "crown_a": (0.7, 1.3), "crown_b": (0.7, 1.3)}


def _scaled(node: dict, *refs: str) -> dict:
    for r in refs:
        node = {"op": "scale", "params": {"factor": {"ref": r}}, "children": [node]}
    return node

VARIANTS = {"tree": 5, "rock": 3, "house": 4}


def _mat(id_: str, rgb, rough: float = 0.85, variation: float = 0.18, freq: float = 2.0) -> dict:
    return {"id": id_, "base_color": [max(0.0, min(1.0, c)) for c in rgb], "roughness": rough,
            "variation": variation, "variation_frequency": freq}


def _t(offset, node: dict) -> dict:
    return {"op": "translate", "params": {"offset": [round(v, 4) for v in offset]}, "children": [node]}


def _quat_axis(axis, angle: float) -> list[float]:
    s = math.sin(angle / 2)
    n = math.sqrt(sum(a * a for a in axis)) or 1.0
    return [axis[0] / n * s, axis[1] / n * s, axis[2] / n * s, math.cos(angle / 2)]


def _jitter_color(rng: random.Random, rgb, hue: float = 0.03, sat: float = 0.12, val: float = 0.15):
    h, s, v = colorsys.rgb_to_hsv(*rgb)
    h = (h + rng.uniform(-hue, hue)) % 1.0
    s = max(0.0, min(1.0, s * (1 + rng.uniform(-sat, sat))))
    v = max(0.0, min(1.0, v * (1 + rng.uniform(-val, val))))
    return colorsys.hsv_to_rgb(h, s, v)


def _capsule(a, b, r: float, material: str) -> dict:
    return {"op": "capsule", "material": material,
            "params": {"a": [round(x, 4) for x in a], "b": [round(x, 4) for x in b], "radius": round(r, 4)}}


def _union(children: list[dict], k: float | None = None) -> dict:
    if len(children) == 1:
        return children[0]
    if k is None:
        return {"op": "union", "params": {}, "children": children[:8]} if len(children) <= 8 else \
            {"op": "union", "params": {}, "children": [_union(children[:8]), _union(children[8:])]}
    return {"op": "smooth_union", "params": {"k": k}, "children": children[:8]} if len(children) <= 8 else \
        {"op": "smooth_union", "params": {"k": k}, "children": [_union(children[:8], k), _union(children[8:], k)]}


def _trunk(rng: random.Random, height: float, r0: float, r1: float, segments: int, kink: float) -> tuple[list[dict], list]:
    """A jointed, slightly crooked trunk: capsule segments through randomly displaced joints. Returns nodes, joints."""
    pts = [(0.0, -0.8, 0.0)]
    x = z = 0.0
    for i in range(1, segments + 1):
        y = height * i / segments
        x += rng.uniform(-kink, kink) * (i / segments)
        z += rng.uniform(-kink, kink) * (i / segments)
        pts.append((x, y, z))
    nodes = []
    for i in range(segments):
        r = r0 + (r1 - r0) * (i / max(1, segments - 1))
        nodes.append(_capsule(pts[i], pts[i + 1], r, "bark"))
    return nodes, pts


def _tree_conifer(seed: int, family_seed: int, k: int, foliage, bark) -> GeometryDefinition:
    rng = random.Random(f"conifer|{seed}|{family_seed}|{k}")
    height = rng.uniform(8.0, 14.0)
    trunk, pts = _trunk(rng, height * 0.92, rng.uniform(0.26, 0.36), rng.uniform(0.08, 0.13), 3, rng.uniform(0.05, 0.35))
    tiers = rng.randint(4, 8)
    base = height * rng.uniform(0.16, 0.34)
    crown = rng.uniform(1.6, 3.0)
    profile = rng.uniform(0.7, 1.3)
    droop = rng.uniform(0.2, 0.45)
    spacing = (height * 0.95 - base) / tiers
    cones = []
    for i in range(tiers):
        f = i / tiers
        y = base + spacing * (i + 0.5)
        r = 0.35 + crown * (1 - f) ** profile * rng.uniform(0.85, 1.15)
        hh = spacing * rng.uniform(0.7, 0.95)
        jx, jz = rng.uniform(-0.12, 0.12) * r, rng.uniform(-0.12, 0.12) * r
        tx = pts[-1][0] * (y / height)
        tz = pts[-1][2] * (y / height)
        cone = {"op": "cone", "material": "needles",
                "params": {"half_height": round(hh, 3), "radius_bottom": round(r, 3), "radius_top": round(r * droop, 3)}}
        cones.append(_t((tx + jx, y, tz + jz), _scaled(cone, "crown_a" if f < 0.5 else "crown_b", "canopy")))
    tip = {"op": "cone", "material": "needles", "params": {"half_height": round(spacing * 0.9, 3), "radius_bottom": 0.45, "radius_top": 0.0}}
    cones.append(_t((pts[-1][0], height * 0.95 + spacing * 0.5, pts[-1][2]),
                    {"op": "scale", "params": {"factor": {"ref": "canopy"}}, "children": [tip]}))
    foliage_node = {"op": "displace", "params": {"amplitude": round(rng.uniform(0.12, 0.22), 3), "frequency": round(rng.uniform(1.4, 2.2), 3),
                                                 "octaves": 2, "seed": rng.randint(0, 65535)}, "children": [_union(cones)]}
    root = _union([_union(trunk, 0.15), foliage_node])
    return GeometryDefinition.model_validate({
        "id": f"tree.conifer.g{family_seed}{k}", "version": 1, "bound_radius": round(height * 0.62 + crown * 0.5, 2),
        "parameters": {"canopy": _CANOPY, "burn": _BURN, "crown_a": _CROWN, "crown_b": _CROWN},
        "materials": [_mat("bark", _jitter_color(rng, bark)), _mat("needles", _jitter_color(rng, foliage), variation=0.32, freq=1.6)],
        "root": root,
    })


def _tree_broadleaf(seed: int, family_seed: int, k: int, foliage, bark) -> GeometryDefinition:
    rng = random.Random(f"broadleaf|{seed}|{family_seed}|{k}")
    fork = rng.uniform(2.2, 4.0)
    trunk, pts = _trunk(rng, fork, rng.uniform(0.24, 0.36), rng.uniform(0.18, 0.26), 2, rng.uniform(0.1, 0.45))
    top = pts[-1]
    n_branch = rng.randint(3, 6)
    spin = rng.uniform(0, math.tau)
    limbs = list(trunk)
    clusters = []
    reach = 0.0
    for i in range(n_branch):
        az = spin + math.tau * i / n_branch + rng.uniform(-0.4, 0.4)
        el = rng.uniform(0.45, 1.05)
        length = rng.uniform(2.0, 4.2)
        end = (top[0] + math.cos(az) * math.cos(el) * length, top[1] + math.sin(el) * length,
               top[2] + math.sin(az) * math.cos(el) * length)
        limbs.append(_capsule(top, end, rng.uniform(0.09, 0.15), "bark"))
        if rng.random() < 0.6:  # a secondary limb from the middle of the branch
            mid = tuple(top[j] + (end[j] - top[j]) * 0.55 for j in range(3))
            a2 = az + rng.choice((-1, 1)) * rng.uniform(0.6, 1.1)
            sub = (mid[0] + math.cos(a2) * 1.6, mid[1] + rng.uniform(0.8, 1.6), mid[2] + math.sin(a2) * 1.6)
            limbs.append(_capsule(mid, sub, 0.07, "bark"))
            if len(clusters) < 9:
                clusters.append((sub, rng.uniform(0.9, 1.5)))
        clusters.append((end, rng.uniform(1.3, 2.3)))
        reach = max(reach, math.hypot(end[0], end[2]) + 2.3, end[1])
    blobs = []
    for (c, r) in clusters[:10]:
        e = {"op": "ellipsoid", "material": "leaves",
             "params": {"radii": [round(r * rng.uniform(0.95, 1.2), 3), round(r * rng.uniform(0.7, 0.95), 3), round(r * rng.uniform(0.95, 1.2), 3)]}}
        blobs.append(_t(c, _scaled(e, "crown_a" if len(blobs) % 2 == 0 else "crown_b", "canopy")))
    crown = {"op": "displace", "params": {"amplitude": round(rng.uniform(0.25, 0.45), 3), "frequency": round(rng.uniform(1.0, 1.6), 3),
                                          "octaves": 3, "seed": rng.randint(0, 65535)}, "children": [_union(blobs, 0.6)]}
    root = _union([_union(limbs[:8], 0.2) if len(limbs) <= 8 else _union([_union(limbs[:8], 0.2), _union(limbs[8:], 0.2)]), crown])
    return GeometryDefinition.model_validate({
        "id": f"tree.broadleaf.g{family_seed}{k}", "version": 1, "bound_radius": round(max(reach, 6.0) + 1.0, 2),
        "parameters": {"canopy": _CANOPY, "burn": _BURN, "crown_a": _CROWN, "crown_b": _CROWN},
        "materials": [_mat("bark", _jitter_color(rng, bark)), _mat("leaves", _jitter_color(rng, foliage), variation=0.35, freq=1.3)],
        "root": root,
    })


def _rock(seed: int, family_seed: int, k: int, color) -> GeometryDefinition:
    rng = random.Random(f"rock|{seed}|{family_seed}|{k}")
    style = rng.choice(("boulder", "boulder", "slab", "stack"))
    lobes = []
    extent = 0.0
    n = rng.randint(2, 4)
    for i in range(n):
        if style == "stack":
            radii = [rng.uniform(0.6, 1.1), rng.uniform(0.5, 0.8), rng.uniform(0.6, 1.1)]
            c = (rng.uniform(-0.3, 0.3), 0.4 + i * 0.9, rng.uniform(-0.3, 0.3))
        elif style == "slab":
            radii = [rng.uniform(1.2, 2.2), rng.uniform(0.35, 0.6), rng.uniform(0.9, 1.6)]
            c = (rng.uniform(-0.8, 0.8), 0.25, rng.uniform(-0.8, 0.8))
        else:
            radii = [rng.uniform(0.7, 1.5), rng.uniform(0.6, 1.1), rng.uniform(0.7, 1.4)]
            c = (rng.uniform(-0.9, 0.9), 0.45 + rng.uniform(-0.1, 0.3), rng.uniform(-0.9, 0.9))
        q = _quat_axis((rng.uniform(-1, 1), 1.0, rng.uniform(-1, 1)), rng.uniform(0, math.tau))
        lobes.append(_t(c, {"op": "rotate", "params": {"quat": [round(v, 5) for v in q]},
                            "children": [{"op": "ellipsoid", "material": "stone", "params": {"radii": [round(r, 3) for r in radii]}}]}))
        extent = max(extent, math.hypot(*c) + max(radii))
    body = {"op": "displace", "params": {"amplitude": round(rng.uniform(0.12, 0.28), 3), "frequency": round(rng.uniform(1.4, 2.6), 3),
                                         "octaves": 3, "seed": rng.randint(0, 65535)}, "children": [_union(lobes, rng.uniform(0.2, 0.5))]}
    return GeometryDefinition.model_validate({
        "id": f"rock.g{family_seed}{k}", "version": 1, "bound_radius": round(extent + 0.6, 2), "parameters": {},
        "materials": [_mat("stone", _jitter_color(rng, color, hue=0.02, sat=0.2, val=0.18), rough=0.95, variation=0.28, freq=1.8)],
        "root": body,
    })


_WALLS = [(0.62, 0.48, 0.32), (0.72, 0.66, 0.55), (0.55, 0.53, 0.50), (0.78, 0.70, 0.58), (0.45, 0.33, 0.22)]
_ROOFS = [(0.42, 0.16, 0.11), (0.25, 0.22, 0.20), (0.50, 0.30, 0.15), (0.30, 0.18, 0.12)]


def _house(seed: int, family_seed: int, k: int) -> GeometryDefinition:
    rng = random.Random(f"house|{seed}|{family_seed}|{k}")
    w, d = rng.uniform(2.0, 3.6), rng.uniform(1.8, 3.0)  # half extents
    storeys = rng.choice((1, 1, 2))
    wall_h = rng.uniform(1.25, 1.6) * storeys  # half height of the above-ground walls
    roof = rng.choice(("gable", "gable", "hip", "flat"))
    floor_y = wall_h  # wall center above the base
    walls = _t((0, floor_y - 1.0, 0), {"op": "box", "params": {"half": [round(w, 3), round(wall_h + 1.0, 3), round(d, 3)]}})
    door = _t((rng.uniform(-w * 0.4, w * 0.4), 0.95, d), {"op": "box", "params": {"half": [0.5, 0.95, 0.35]}})
    # a row of windows on the front and back, generated with limited repetition
    n_win = max(1, int(w / 1.3))
    win = {"op": "repeat", "params": {"period": [round(2 * w / (n_win + 1), 3), 1.0, round(2 * d, 3)], "count": [n_win // 2, 0, 1]},
           "children": [{"op": "box", "params": {"half": [0.38, 0.42, 0.25]}}]}
    windows = _t((0, wall_h * (1.25 if storeys == 2 else 1.1), 0), win)
    body = {"op": "subtract", "material": "wall", "params": {}, "children": [
        walls, {"op": "union", "params": {}, "children": [door, windows]}]}
    top = 2 * wall_h
    pitch = rng.uniform(0.55, 0.85)
    if roof == "flat":
        roof_node = _t((0, top + 0.18, 0), {"op": "round_box", "material": "roof", "params": {"half": [round(w + 0.3, 3), 0.18, round(d + 0.3, 3)], "radius": 0.05}})
    else:
        span = d + 0.45
        ridge = span * pitch
        slab = {"op": "rotate", "params": {"quat": _quat_axis((1, 0, 0), math.atan2(ridge, span))},
                "children": [{"op": "box", "params": {"half": [round(w + 0.4, 3), 0.12, round(math.hypot(span, ridge) / 2 + 0.1, 3)]}}]}
        # gable: two mirrored slabs; mirror across z
        gable = {"op": "mirror", "params": {"axis": 2}, "children": [_t((0, top + ridge / 2, -span / 2), slab)]}
        fill = {"op": "intersect", "params": {}, "children": [
            _t((0, top + ridge / 2, 0), {"op": "box", "params": {"half": [round(w, 3), round(ridge / 2, 3), round(span, 3)]}}),
            {"op": "mirror", "params": {"axis": 2}, "children": [_t((0, top + ridge, 0), {"op": "rotate", "params": {"quat": _quat_axis((1, 0, 0), math.atan2(ridge, span))},
                                                                                           "children": [{"op": "box", "params": {"half": [round(w + 1, 3), round(ridge, 3), round(span * 1.2, 3)]}}]})]},
        ]}
        roof_node = {"op": "union", "material": "roof", "params": {}, "children": [gable, {**fill, "material": "wall"}]}
        if roof == "hip":
            roof_node = {"op": "intersect", "material": "roof", "params": {}, "children": [
                roof_node, {"op": "mirror", "params": {"axis": 0}, "children": [_t((w * 0.55, top + ridge * 0.5, 0), {"op": "rotate", "params": {"quat": _quat_axis((0, 0, 1), -0.75)},
                                                                                                                  "children": [{"op": "box", "params": {"half": [round(w, 3), round(ridge * 2, 3), round(span + 1, 3)]}}]})]}]}
    parts = [body, roof_node]
    if rng.random() < 0.6:  # chimney
        cx = rng.uniform(-w * 0.6, w * 0.6)
        parts.append(_t((cx, top + 0.9, rng.uniform(-d * 0.4, d * 0.4)), {"op": "box", "material": "stone", "params": {"half": [0.28, 1.1, 0.28]}}))
    if rng.random() < 0.5:  # porch with posts
        posts = {"op": "repeat", "params": {"period": [round(w * 0.9, 3), 1.0, 1.0], "count": [1, 0, 0]},
                 "children": [_capsule((0, 0, 0), (0, 2.3, 0), 0.08, "wood")]}
        parts.append(_t((0, 0, d + 1.1), posts))
        parts.append(_t((0, 2.35, d + 0.75), {"op": "box", "material": "roof", "params": {"half": [round(w * 0.95, 3), 0.08, 0.8]}}))
    wall_col = _jitter_color(rng, rng.choice(_WALLS), val=0.1)
    roof_col = _jitter_color(rng, rng.choice(_ROOFS), val=0.12)
    return GeometryDefinition.model_validate({
        "id": f"building.house.g{family_seed}{k}", "version": 1,
        "bound_radius": round(math.hypot(w + 1.0, d + 1.9, top + 2.5) + 0.5, 2), "parameters": {"burn": _BURN},
        "materials": [_mat("wall", wall_col, variation=0.08), _mat("roof", roof_col), _mat("stone", (0.42, 0.40, 0.38), rough=0.95),
                      _mat("wood", (0.33, 0.22, 0.13))],
        "root": _union(parts),
    })


_TREE_COLORS = {
    "tree.conifer": ((0.06, 0.20, 0.09), (0.22, 0.15, 0.10)),
    "tree.broadleaf": ((0.15, 0.34, 0.09), (0.30, 0.22, 0.14)),
}


def generate_family(family: str, seed: int, family_seed: int, foliage=None) -> list[GeometryDefinition]:
    """Generated variants for a requested archetype family (the family ids are what the language draft selects)."""
    if family in _TREE_COLORS:
        leaf, bark = _TREE_COLORS[family]
        leaf = foliage or leaf
        make = _tree_conifer if family == "tree.conifer" else _tree_broadleaf
        return [make(seed, family_seed, k, leaf, bark) for k in range(VARIANTS["tree"])]
    if family == "rock.boulder":
        return [_rock(seed, family_seed, k, (0.42, 0.41, 0.39)) for k in range(VARIANTS["rock"])]
    if family == "building.house":
        return [_house(seed, family_seed, k) for k in range(VARIANTS["house"])]
    return []
