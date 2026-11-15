"""Tolerant normalisation of language-model geometry before strict validation.

Models write nearly-valid SDF graphs: synonyms for ops, numbers slightly out of range, numbers as strings, a combine node
with one child, a domain node with several. This pass repairs those *structurally* using only the op table
(packages/contracts/schemas/geometry_ops.json); it never adds geometry of its own. It also expands named reusable
sub-expressions: a graph may be {"defs": {"name": node, ...}, "root": node} and use {"use": "name"} anywhere, which is
inlined with a size bound. The result is then validated by GeometryDefinition exactly as before.
"""

from __future__ import annotations

import copy
import math
from typing import Any

from greality_contracts.geometry import load_ops

SYNONYMS = {
    "cube": "box", "rounded_box": "round_box", "roundbox": "round_box", "rect": "box", "cuboid": "box",
    "tube": "capsule", "line": "capsule", "segment": "capsule", "ring": "torus", "donut": "torus",
    "difference": "subtract", "minus": "subtract", "smooth_difference": "smooth_subtract", "intersection": "intersect",
    "blend": "smooth_union", "smoothunion": "smooth_union", "group": "union", "combine": "union",
    "translation": "translate", "move": "translate", "position": "translate", "rotation": "rotate",
    "uniform_scale": "scale", "nonuniform_scale": "stretch", "scale3": "stretch", "polar_repeat": "radial_repeat",
    "radial": "radial_repeat", "array": "repeat", "noise": "displace", "bump": "displace", "hollow": "shell",
}
MAX_EXPANDED_NODES = 128


class NormalizeError(ValueError):
    pass


def _num(v: Any) -> Any:
    if isinstance(v, str):
        try:
            return float(v)
        except ValueError:
            return v
    return v


def _clamp_scalar(v: Any, spec: dict) -> Any:
    v = _num(v)
    if isinstance(v, dict) and "ref" in v:
        return {"ref": str(v["ref"])}
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        return v  # leave for the validator to report
    lo, hi = spec.get("min", -math.inf), spec.get("max", math.inf)
    return min(hi, max(lo, float(v)))


def _fix_params(op: str, params: dict, spec: dict) -> dict:
    out = {}
    for name, ps in spec.get("params", {}).items():
        if name not in params:
            continue
        v = params[name]
        kind = ps.get("type")
        if kind == "scalar":
            out[name] = _clamp_scalar(v, ps)
        elif kind == "vec3":
            if isinstance(v, (int, float, str)) and not isinstance(v, bool):
                v = [v, v, v]
            if isinstance(v, dict) and all(k in v for k in "xyz"):
                v = [v["x"], v["y"], v["z"]]
            out[name] = [_clamp_scalar(c, ps) for c in v][:3] if isinstance(v, list) else v
        elif kind == "int":
            n = _num(v)
            out[name] = int(round(min(ps.get("max", n), max(ps.get("min", n), n)))) if isinstance(n, (int, float)) else n
        elif kind == "quat":
            q = [_num(c) for c in v] if isinstance(v, list) else v
            if isinstance(q, list) and len(q) == 4 and all(isinstance(c, (int, float)) for c in q):
                norm = math.sqrt(sum(c * c for c in q)) or 1.0
                q = [c / norm for c in q]
            out[name] = q
        else:
            out[name] = v
    return out


def normalize_graph(doc: Any) -> dict:
    ops = load_ops()["ops"]
    defs: dict = {}
    root = doc
    if isinstance(doc, dict) and "op" not in doc and "root" in doc:
        defs = doc.get("defs") or doc.get("definitions") or {}
        root = doc["root"]
    budget = [0]

    def walk(node: Any, depth: int) -> dict:
        if depth > 24:
            raise NormalizeError("geometry nests too deeply (recursive 'use'?)")
        if isinstance(node, list):
            node = {"op": "union", "children": node}
        if not isinstance(node, dict):
            raise NormalizeError(f"expected a node object, got {type(node).__name__}")
        if "use" in node and "op" not in node:
            name = str(node["use"])
            if name not in defs:
                raise NormalizeError(f"'use' refers to unknown definition '{name}'")
            inner = copy.deepcopy(defs[name])
            if node.get("material") is not None and isinstance(inner, dict):
                inner.setdefault("material", node["material"])
            return walk(inner, depth + 1)
        budget[0] += 1
        if budget[0] > MAX_EXPANDED_NODES:
            raise NormalizeError(f"more than {MAX_EXPANDED_NODES} nodes after expanding definitions")
        op = str(node.get("op", "")).strip().lower().replace("-", "_").replace(" ", "_")
        op = SYNONYMS.get(op, op)
        if op not in ops:
            raise NormalizeError(f"unknown op '{node.get('op')}'")
        spec = ops[op]
        params = node.get("params") or {k: v for k, v in node.items() if k in spec.get("params", {})}
        if not isinstance(params, dict):
            params = {}
        children = [walk(c, depth + 1) for c in (node.get("children") or [])]
        lo, hi = spec["children"]
        out: dict = {"op": op, "params": _fix_params(op, params, spec)}
        if node.get("material") is not None:
            out["material"] = node["material"]
        if spec["class"] == "combine":
            if len(children) == 1:
                child = children[0]
                if out.get("material") and "material" not in child:
                    child["material"] = out["material"]
                return child
            if len(children) > hi:
                if hi >= 8:
                    children = [{"op": op, "params": out["params"], "children": children[i:i + 8]}
                                for i in range(0, len(children), 8)]
                else:  # 2-child ops: subtract the union of the rest from the first
                    children = [children[0], {"op": "union", "params": {}, "children": children[1:9]}]
        elif hi == 1 and len(children) > 1:
            children = [{"op": "union", "params": {}, "children": children[:8]}]
        elif hi == 0:
            children = []
        out["children"] = children
        if len(children) < lo:
            raise NormalizeError(f"op '{op}' needs at least {lo} child node(s)")
        return out

    return walk(root, 0)
