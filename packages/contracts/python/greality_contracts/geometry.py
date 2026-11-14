"""Validation of the SDF scene language (geometry AST) against schemas/geometry_ops.json.

The op table JSON is the single source of truth shared with the TypeScript compiler.
Nothing in here executes model output: a GeometryNode is data, checked against a closed op set
with bounded numeric parameters, bounded depth and bounded node count.
"""

from __future__ import annotations

import json
import math
from functools import lru_cache
from pathlib import Path
from typing import Any

OPS_PATH = Path(__file__).resolve().parents[2] / "schemas" / "geometry_ops.json"


class GeometryValidationError(ValueError):
    def __init__(self, path: str, message: str):
        super().__init__(f"{path}: {message}")
        self.path = path
        self.message = message


@lru_cache(maxsize=1)
def load_ops() -> dict[str, Any]:
    return json.loads(OPS_PATH.read_text(encoding="utf-8"))


def _check_scalar(value: Any, spec: dict[str, Any], parameters: set[str], path: str) -> None:
    if isinstance(value, dict):
        if set(value) != {"ref"} or not isinstance(value["ref"], str):
            raise GeometryValidationError(path, 'parameter reference must be {"ref": "<name>"}')
        if value["ref"] not in parameters:
            raise GeometryValidationError(path, f"unknown parameter '{value['ref']}'")
        return
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise GeometryValidationError(path, "expected a finite number or a parameter reference")
    if "min" in spec and value < spec["min"]:
        raise GeometryValidationError(path, f"{value} below minimum {spec['min']}")
    if "max" in spec and value > spec["max"]:
        raise GeometryValidationError(path, f"{value} above maximum {spec['max']}")


def _check_param(value: Any, spec: dict[str, Any], parameters: set[str], path: str) -> None:
    kind = spec["type"]
    if kind == "scalar":
        _check_scalar(value, spec, parameters, path)
    elif kind == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            raise GeometryValidationError(path, "expected an integer")
        _check_scalar(value, spec, parameters, path)
    elif kind in ("vec3", "quat"):
        size = 3 if kind == "vec3" else 4
        if not isinstance(value, (list, tuple)) or len(value) != size:
            raise GeometryValidationError(path, f"expected an array of {size} numbers")
        for i, item in enumerate(value):
            # quaternion components are plain numbers in [-1, 1]; refs are not allowed there
            item_spec = spec if kind == "vec3" else {"min": -1.0, "max": 1.0}
            _check_scalar(item, item_spec, parameters if kind == "vec3" else set(), f"{path}[{i}]")
        if kind == "quat" and sum(c * c for c in value) < 1e-6:
            raise GeometryValidationError(path, "quaternion must be non-zero")
    else:  # pragma: no cover - guarded by the op table itself
        raise GeometryValidationError(path, f"unknown param type '{kind}' in op table")


def validate_node(
    node: dict[str, Any],
    parameters: set[str],
    materials: set[str],
    path: str = "root",
    depth: int = 1,
) -> int:
    """Validate one node (plain dict form) recursively. Returns the number of nodes visited."""
    table = load_ops()
    limits = table["limits"]
    if depth > limits["max_depth"]:
        raise GeometryValidationError(path, f"tree deeper than {limits['max_depth']}")
    op = node.get("op")
    spec = table["ops"].get(op) if isinstance(op, str) else None
    if spec is None:
        raise GeometryValidationError(path, f"unknown op '{op}'")

    params = node.get("params") or {}
    unknown = set(params) - set(spec["params"])
    if unknown:
        raise GeometryValidationError(path, f"unexpected params {sorted(unknown)} for op '{op}'")
    for name, param_spec in spec["params"].items():
        if name not in params:
            raise GeometryValidationError(path, f"missing param '{name}' for op '{op}'")
        _check_param(params[name], param_spec, parameters, f"{path}.{name}")

    material = node.get("material")
    if material is not None and material not in materials:
        raise GeometryValidationError(path, f"unknown material '{material}'")

    children = node.get("children") or []
    lo, hi = spec["children"]
    if not lo <= len(children) <= hi:
        raise GeometryValidationError(path, f"op '{op}' takes {lo}..{hi} children, got {len(children)}")

    count = 1
    for i, child in enumerate(children):
        count += validate_node(child, parameters, materials, f"{path}.children[{i}]", depth + 1)
        if count > limits["max_nodes"]:
            raise GeometryValidationError(path, f"more than {limits['max_nodes']} nodes")
    return count
