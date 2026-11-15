"""Dense reference renderer used only by tests: every Gaussian against every pixel, no tiles, no chunks.

It shares the projection and the per-pixel compositing formula with the tile rasterizer but none of the binning,
sorting, truncation or checkpointing, so agreement between the two checks exactly the parts that can go wrong.
A Gaussian is applied to a pixel only if that pixel's tile lies in the Gaussian's 3σ tile rectangle, which is the
same footprint rule the tile renderer uses.
"""

from __future__ import annotations

import torch

from .torch_tiles import TILE, Camera, RasterOutput, bin_tiles, composite, project


def render_dense(means, scales, quats, opacities, colors, cam: Camera, background=None) -> RasterOutput:
    H, W = cam.height, cam.width
    dev, dt = means.device, means.dtype
    p = project(means, scales, quats, cam)
    rect = bin_tiles(p, W, H, max_per_tile=1).rect
    order = torch.argsort(p.depth.detach())
    order = order[p.valid[order] & (p.radius[order] > 0)]
    ys, xs = torch.meshgrid(torch.arange(H, device=dev), torch.arange(W, device=dev), indexing="ij")
    px = torch.stack([xs.reshape(-1), ys.reshape(-1)], dim=-1).to(dt) + 0.5     # (HW, 2)
    tx, ty = (xs.reshape(-1) // TILE), (ys.reshape(-1) // TILE)
    r = rect[order]                                                             # (K, 4)
    inside = (tx[:, None] >= r[None, :, 0]) & (tx[:, None] < r[None, :, 1]) & \
             (ty[:, None] >= r[None, :, 2]) & (ty[:, None] < r[None, :, 3])     # (HW, K)
    idx = torch.where(inside, order[None, :], torch.full_like(inside, -1, dtype=torch.long))
    # one "tile" per pixel: px (HW, 1, 2), idx (HW, K)
    rgb, acc, dep = composite(px[:, None, :], idx, p.mean2d, p.conic, opacities, colors, p.depth)
    bg = background if background is not None else torch.zeros(3, device=dev, dtype=dt)
    rgb = rgb[:, 0].reshape(H, W, 3)
    acc = acc[:, 0].reshape(H, W)
    return RasterOutput(rgb=rgb + (1 - acc)[..., None] * bg, alpha=acc, depth=dep[:, 0].reshape(H, W))
