"""Read and write the INRIA binary Gaussian splat PLY layout."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

SH_C0 = 0.28209479177387814
_BASE = "x y z nx ny nz f_dc_0 f_dc_1 f_dc_2".split()
_TAIL = "opacity scale_0 scale_1 scale_2 rot_0 rot_1 rot_2 rot_3".split()


class PlyError(ValueError):
    """Invalid Gaussian PLY data."""


@dataclass
class GaussianArrays:
    means: np.ndarray
    log_scales: np.ndarray
    quats: np.ndarray
    opacity_logits: np.ndarray
    sh_dc: np.ndarray
    sh_rest: np.ndarray | None = None

    @property
    def count(self) -> int:
        return len(self.means)

    @property
    def sh_degree(self) -> int:
        k = 0 if self.sh_rest is None else self.sh_rest.shape[1]
        if k not in (0, 3, 8, 15):
            raise PlyError("SH coefficient count must correspond to degree 0..3")
        return (0, 3, 8, 15).index(k)


def rgb_to_sh_dc(rgb: np.ndarray) -> np.ndarray:
    return (np.asarray(rgb) - 0.5) / SH_C0


def sh_dc_to_rgb(sh: np.ndarray) -> np.ndarray:
    return 0.5 + SH_C0 * np.asarray(sh)


def validate_gaussians(g: GaussianArrays, bounds_min: object = None, bounds_max: object = None) -> list[str]:
    problems = []
    n = g.count
    if n == 0:
        problems.append("count must be greater than zero")
    shapes = {"means": (n, 3), "log_scales": (n, 3), "quats": (n, 4), "opacity_logits": (n,), "sh_dc": (n, 3)}
    if g.sh_rest is not None:
        shapes["sh_rest"] = (n, g.sh_rest.shape[1] if g.sh_rest.ndim == 3 else -1, 3)
    for name, shape in shapes.items():
        value = getattr(g, name)
        if value.shape != shape:
            problems.append(f"{name} must have shape {shape}")
        if value.dtype != np.float32:
            problems.append(f"{name} must have dtype float32")
        if not np.isfinite(value).all():
            problems.append(f"{name} must be finite")
    try:
        g.sh_degree
    except (PlyError, IndexError):
        problems.append("sh_rest must have 0, 3, 8 or 15 coefficients per channel")
    if np.any((g.log_scales < -12) | (g.log_scales > 6)):
        problems.append("log_scales must be within [-12, 6]")
    if g.quats.shape == (n, 4) and np.any(np.linalg.norm(g.quats, axis=1) < 1e-8):
        problems.append("quats contain near-zero norms")
    if g.means.shape == (n, 3):
        outside = np.zeros(n, dtype=bool)
        if bounds_min is not None:
            outside |= np.any(g.means < bounds_min, axis=1)
        if bounds_max is not None:
            outside |= np.any(g.means > bounds_max, axis=1)
        if outside.any():
            problems.append(f"{outside.sum()} means outside bounds")
    return problems


def encode_ply(g: GaussianArrays) -> bytes:
    problems = validate_gaussians(g)
    if g.count == 0:
        problems.remove("count must be greater than zero")
    if problems:
        raise PlyError("; ".join(problems))
    k = 0 if g.sh_rest is None else g.sh_rest.shape[1]
    names = _BASE + [f"f_rest_{i}" for i in range(3 * k)] + _TAIL
    header = "\n".join(["ply", "format binary_little_endian 1.0", f"element vertex {g.count}"] + [f"property float {name}" for name in names] + ["end_header", ""])
    rest = np.empty((g.count, 0), dtype=np.float32) if k == 0 else g.sh_rest.transpose(0, 2, 1).reshape(g.count, 3 * k)
    body = np.column_stack((g.means, np.zeros((g.count, 3)), g.sh_dc, rest, g.opacity_logits, g.log_scales, g.quats))
    return header.encode("ascii") + body.astype("<f4").tobytes()


def decode_ply(data: bytes) -> GaussianArrays:
    offset = 0
    lines = []
    while True:
        end = data.find(b"\n", offset)
        if end < 0:
            raise PlyError("missing end_header")
        try:
            line = data[offset:end].rstrip(b"\r").decode("ascii")
        except UnicodeDecodeError as exc:
            raise PlyError("header must be ASCII") from exc
        offset = end + 1
        lines.append(line)
        if line == "end_header":
            break
    if lines[:2] != ["ply", "format binary_little_endian 1.0"]:
        raise PlyError("expected ply format binary_little_endian 1.0")
    n = None
    names = []
    for line in lines[2:-1]:
        parts = line.split()
        if not parts or parts[0] in ("comment", "obj_info"):
            continue
        if parts[:2] == ["element", "vertex"] and len(parts) == 3 and n is None:
            try:
                n = int(parts[2])
            except ValueError as exc:
                raise PlyError("invalid vertex count") from exc
            if n < 0:
                raise PlyError("invalid vertex count")
        elif parts[0] == "property" and len(parts) == 3 and n is not None:
            if parts[1] not in ("float", "float32"):
                raise PlyError(f"property {parts[2]} must be float")
            if parts[2] in names:
                raise PlyError(f"duplicate property {parts[2]}")
            names.append(parts[2])
        else:
            raise PlyError(f"unsupported header line: {line}")
    if n is None:
        raise PlyError("missing vertex element")
    for name in _BASE[:3] + _BASE[6:] + _TAIL:
        if name not in names:
            raise PlyError(f"missing required property {name}")
    rest = [name for name in names if name.startswith("f_rest_")]
    if len(rest) not in (0, 9, 24, 45) or set(rest) != {f"f_rest_{i}" for i in range(len(rest))}:
        raise PlyError("invalid f_rest properties: expected 0, 9, 24 or 45")
    expected = n * len(names) * 4
    if len(data) - offset != expected:
        raise PlyError("truncated body or trailing garbage")
    body = np.frombuffer(data, dtype="<f4", offset=offset).reshape(n, len(names))
    def columns(fields: list[str]) -> np.ndarray:
        return body[:, [names.index(name) for name in fields]].astype(np.float32, copy=True)
    sh_rest = columns([f"f_rest_{i}" for i in range(len(rest))]).reshape(n, 3, len(rest) // 3).transpose(0, 2, 1).copy() if rest else None
    return GaussianArrays(columns(_BASE[:3]), columns(_TAIL[1:4]), columns(_TAIL[4:]), columns(["opacity"])[:, 0], columns(_BASE[6:]), sh_rest)
