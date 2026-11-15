"""Binary Gaussian PLY tests."""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest

from gpu.ply import GaussianArrays, PlyError, decode_ply, encode_ply, rgb_to_sh_dc, sh_dc_to_rgb, validate_gaussians


def gaussians(k: int = 0, n: int = 4) -> GaussianArrays:
    rng = np.random.default_rng(7)
    def values(shape: tuple) -> np.ndarray:
        return rng.normal(size=shape).astype(np.float32)
    return GaussianArrays(values((n, 3)), values((n, 3)), values((n, 4)), values((n,)), values((n, 3)), values((n, k, 3)) if k else None)


@pytest.mark.parametrize("k", [0, 3, 8, 15])
def test_round_trip_and_header(k: int) -> None:
    g = gaussians(k)
    data = encode_ply(g)
    names = "x y z nx ny nz f_dc_0 f_dc_1 f_dc_2".split() + [f"f_rest_{i}" for i in range(3 * k)] + "opacity scale_0 scale_1 scale_2 rot_0 rot_1 rot_2 rot_3".split()
    header = "\n".join(["ply", "format binary_little_endian 1.0", "element vertex 4"] + [f"property float {name}" for name in names] + ["end_header", ""]).encode()
    assert data.startswith(header)
    assert len(data) == len(header) + 4 * 4 * len(names)
    decoded = decode_ply(data)
    assert decoded.sh_degree == (0, 3, 8, 15).index(k)
    for name in ("means", "log_scales", "quats", "opacity_logits", "sh_dc", "sh_rest"):
        np.testing.assert_array_equal(getattr(decoded, name), getattr(g, name))
    body = np.frombuffer(data[len(header):], dtype="<f4").reshape(4, -1)
    np.testing.assert_array_equal(body[:, 3:6], 0)
    if k:
        np.testing.assert_array_equal(body[:, 9:9 + 3 * k], g.sh_rest.transpose(0, 2, 1).reshape(4, 3 * k))


def test_reordered_crlf_header() -> None:
    names = "rot_3 rot_2 rot_1 rot_0 scale_2 scale_1 scale_0 opacity f_dc_2 f_dc_1 f_dc_0 z y x extra".split()
    header = "\r\n".join(["ply", "format binary_little_endian 1.0", "comment test", "obj_info test", "element vertex 1"] + [f"property float32 {name}" for name in names] + ["end_header", ""]).encode()
    g = decode_ply(header + np.arange(len(names), dtype="<f4").tobytes())
    np.testing.assert_array_equal(g.means, [[13, 12, 11]])
    np.testing.assert_array_equal(g.quats, [[3, 2, 1, 0]])


@pytest.mark.parametrize("change,match", [
    (lambda b: b.replace(b"binary_little_endian", b"ascii"), "format"),
    (lambda b: b.replace(b"binary_little_endian", b"binary_big_endian"), "format"),
    (lambda b: b.replace(b"property float x\n", b""), "missing required"),
    (lambda b: b.replace(b"property float x", b"property int x"), "must be float"),
    (lambda b: b[:-1], "truncated"),
    (lambda b: b + b"x", "trailing"),
    (lambda b: b.replace(b"end_header\n", b"property float f_rest_0\nend_header\n"), "f_rest"),
    (lambda b: b.replace(b"end_header\n", b"property float f_rest_9\n" + b"".join(f"property float f_rest_{i}\n".encode() for i in range(8)) + b"end_header\n"), "f_rest"),
    (lambda b: b"ply\n", "end_header"),
    (lambda b: b.replace(b"element vertex 4", b"element vertex -1"), "count"),
])
def test_decode_errors(change: object, match: str) -> None:
    with pytest.raises(PlyError, match=match):
        decode_ply(change(encode_ply(gaussians())))


def test_empty_and_color() -> None:
    assert decode_ply(encode_ply(gaussians(n=0))).count == 0
    rgb = np.random.default_rng(2).random((10, 3)).astype(np.float32)
    np.testing.assert_allclose(sh_dc_to_rgb(rgb_to_sh_dc(rgb)), rgb, atol=1e-7)


def test_validation() -> None:
    g = gaussians()
    assert validate_gaussians(g) == []
    assert "4 means outside bounds" in validate_gaussians(g, [100] * 3, [101] * 3)
    assert validate_gaussians(gaussians(n=0))
    for bad in (replace(g, means=g.means.astype(np.float64)), replace(g, means=g.means[:, :2]), replace(g, sh_dc=np.full((4, 3), np.nan, dtype=np.float32)), replace(g, log_scales=np.full((4, 3), 7, dtype=np.float32)), replace(g, quats=np.zeros((4, 4), dtype=np.float32)), replace(g, sh_rest=np.zeros((4, 2, 3), dtype=np.float32))):
        assert validate_gaussians(bad)
        with pytest.raises(PlyError):
            encode_ply(bad)
