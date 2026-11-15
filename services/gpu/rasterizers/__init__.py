"""Differentiable Gaussian rasterizers behind one interface (docs/architecture §12).

`make_rasterizer("torch")` is the guaranteed tier; `make_rasterizer("gsplat")` raises `RasterizerUnavailable` with the
measured reason when gsplat cannot run in this environment (see `gsplat_backend.probe`).
"""

from __future__ import annotations


class RasterizerUnavailable(RuntimeError):
    pass


def make_rasterizer(name: str = "torch", **kwargs):
    if name == "torch":
        from .torch_tiles import TorchTileRasterizer
        return TorchTileRasterizer(**kwargs)
    if name == "gsplat":
        from .gsplat_backend import probe
        ok, reason = probe()
        raise RasterizerUnavailable(f"gsplat backend not available: {reason}" if not ok else
                                    "gsplat imports, but no adapter is integrated or parity-tested yet")
    raise ValueError(f"unknown rasterizer {name!r}")
