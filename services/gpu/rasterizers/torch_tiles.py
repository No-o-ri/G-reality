"""Tile-based differentiable 3D Gaussian rasterizer in pure PyTorch (tier 1 of docs/architecture §5.3).

Forward and backward run as ordinary CUDA kernels through autograd; nothing is approximated by a 2D splat:

  1. EWA projection of every 3D Gaussian (Σ = R S Sᵀ Rᵀ) to a 2D covariance with the perspective Jacobian,
     plus the standard 0.3 px² dilation, exactly as in Kerbl et al. 2023.
  2. Binning into 16 × 16 px tiles by the 3σ footprint; within each tile Gaussians are ordered front to back.
  3. Per pixel, front-to-back alpha compositing with cumulative transmittance (computed in log space).

Tiles are rendered in chunks under activation checkpointing, so peak memory is bounded by one chunk rather than
the whole image. A tile keeps at most `max_per_tile` Gaussians (the nearest ones); the number dropped is reported.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass, field

# torch 2.14 sends some eager CUDA ops (bmm backward) to Triton JIT kernels that need a C compiler; the WSL distribution
# has none, so keep the stock ATen kernels. Only effective before torch is imported (scripts/wsl-gpu.sh sets it too).
os.environ.setdefault("TORCH_DISABLE_NATIVE_JIT", "1")

import torch  # noqa: E402
from torch.utils.checkpoint import checkpoint  # noqa: E402

TILE = 16
PIXELS_PER_TILE = TILE * TILE
ALPHA_MIN = 1.0 / 255.0
ALPHA_MAX = 0.99
DILATION_PX2 = 0.3


@dataclass(frozen=True)
class Camera:
    """Pinhole camera in OpenCV convention (x right, y down, z forward); pixel (i, j) center at (j+0.5, i+0.5)."""

    world_to_cam: torch.Tensor  # (4, 4)
    fx: float
    fy: float
    cx: float
    cy: float
    width: int
    height: int
    near: float = 0.05


@dataclass
class RasterOutput:
    rgb: torch.Tensor    # (H, W, 3), composited over the background
    alpha: torch.Tensor  # (H, W), accumulated opacity
    depth: torch.Tensor  # (H, W), Σ wᵢ zᵢ (divide by alpha for the expected surface depth)
    stats: dict = field(default_factory=dict)


def quat_to_rotmat(q: torch.Tensor) -> torch.Tensor:
    """(N, 4) quaternions (w, x, y, z), any norm → (N, 3, 3) rotation matrices."""
    q = q / q.norm(dim=-1, keepdim=True).clamp_min(1e-12)
    w, x, y, z = q.unbind(-1)
    return torch.stack([
        1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y),
        2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x),
        2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y),
    ], dim=-1).reshape(-1, 3, 3)


@dataclass
class Projected:
    mean2d: torch.Tensor  # (N, 2) pixels
    conic: torch.Tensor   # (N, 3) inverse 2D covariance (a, b, c) of [[a, b], [b, c]]
    depth: torch.Tensor   # (N,) camera z
    radius: torch.Tensor  # (N,) 3σ radius in pixels (no grad)
    valid: torch.Tensor   # (N,) bool


def project(means: torch.Tensor, scales: torch.Tensor, quats: torch.Tensor, cam: Camera) -> Projected:
    R = cam.world_to_cam[:3, :3].to(means)
    t = cam.world_to_cam[:3, 3].to(means)
    pc = means @ R.T + t
    x, y, z = pc.unbind(-1)
    valid = z > cam.near
    zs = z.clamp_min(cam.near)
    # Clamp the Jacobian's evaluation point to slightly beyond the frustum (reference implementation), which keeps
    # Gaussians far off-screen from producing huge, unstable footprints.
    limx = 1.3 * (0.5 * cam.width / cam.fx)
    limy = 1.3 * (0.5 * cam.height / cam.fy)
    xz = (x / zs).clamp(-limx, limx)
    yz = (y / zs).clamp(-limy, limy)
    zero = torch.zeros_like(zs)
    J = torch.stack([
        cam.fx / zs, zero, -cam.fx * xz / zs,
        zero, cam.fy / zs, -cam.fy * yz / zs,
    ], dim=-1).reshape(-1, 2, 3)
    M = quat_to_rotmat(quats) * scales[:, None, :]          # R S
    T = J @ R                                               # (N, 2, 3)
    TM = T @ M
    cov2 = TM @ TM.transpose(1, 2)
    a = cov2[:, 0, 0] + DILATION_PX2
    b = cov2[:, 0, 1]
    c = cov2[:, 1, 1] + DILATION_PX2
    det = a * c - b * b
    valid = valid & (det > 1e-12)
    det_safe = torch.where(valid, det, torch.ones_like(det))
    conic = torch.stack([c / det_safe, -b / det_safe, a / det_safe], dim=-1)
    mean2d = torch.stack([cam.fx * x / zs + cam.cx, cam.fy * y / zs + cam.cy], dim=-1)
    with torch.no_grad():
        mid = 0.5 * (a + c)
        lam = mid + torch.sqrt((mid * mid - det).clamp_min(0.1))
        radius = torch.ceil(3.0 * torch.sqrt(lam))
        radius = torch.where(valid, radius, torch.zeros_like(radius))
    return Projected(mean2d, conic, z, radius, valid)


@dataclass
class TileBins:
    index: torch.Tensor     # (T, K) Gaussian ids front to back, -1 padded
    counts: torch.Tensor    # (T,) Gaussians overlapping each tile before truncation
    tiles_x: int
    tiles_y: int
    pairs: int
    dropped: int
    # tile footprint rectangle of every Gaussian, [x0, x1) × [y0, y1) in tile units (for the reference renderer)
    rect: torch.Tensor


@torch.no_grad()
def bin_tiles(p: Projected, width: int, height: int, max_per_tile: int) -> TileBins:
    tiles_x = math.ceil(width / TILE)
    tiles_y = math.ceil(height / TILE)
    n_tiles = tiles_x * tiles_y
    dev = p.mean2d.device
    m, r = p.mean2d.detach(), p.radius
    x0 = torch.floor((m[:, 0] - r) / TILE).clamp(0, tiles_x).long()
    x1 = (torch.floor((m[:, 0] + r) / TILE) + 1).clamp(0, tiles_x).long()
    y0 = torch.floor((m[:, 1] - r) / TILE).clamp(0, tiles_y).long()
    y1 = (torch.floor((m[:, 1] + r) / TILE) + 1).clamp(0, tiles_y).long()
    w = (x1 - x0).clamp_min(0)
    n = w * (y1 - y0).clamp_min(0)
    n = torch.where(p.valid & (r > 0), n, torch.zeros_like(n))
    rect = torch.stack([x0, x1, y0, y1], dim=-1)

    ids = torch.nonzero(n > 0).squeeze(1)
    ids = ids[torch.argsort(p.depth.detach()[ids])]          # front to back
    nn = n[ids]
    total = int(nn.sum())
    if total == 0:
        return TileBins(torch.full((n_tiles, 0), -1, dtype=torch.long, device=dev),
                        torch.zeros(n_tiles, dtype=torch.long, device=dev), tiles_x, tiles_y, 0, 0, rect)
    g = torch.repeat_interleave(ids, nn)
    start = torch.cumsum(nn, 0) - nn
    local = torch.arange(total, device=dev) - torch.repeat_interleave(start, nn)
    wg = w[g]
    tile = (y0[g] + local // wg) * tiles_x + (x0[g] + local % wg)
    tile_sorted, perm = torch.sort(tile, stable=True)        # stable: keeps the depth order inside each tile
    g_sorted = g[perm]
    counts = torch.bincount(tile_sorted, minlength=n_tiles)
    offsets = torch.cumsum(counts, 0) - counts
    pos = torch.arange(total, device=dev) - offsets[tile_sorted]
    k = min(int(counts.max()), max_per_tile)
    keep = pos < k
    index = torch.full((n_tiles, k), -1, dtype=torch.long, device=dev)
    index[tile_sorted[keep], pos[keep]] = g_sorted[keep]
    dropped = int((counts - k).clamp_min(0).sum())
    return TileBins(index, counts, tiles_x, tiles_y, total, dropped, rect)


def _tile_pixels(tile_ids: torch.Tensor, tiles_x: int, dtype: torch.dtype) -> torch.Tensor:
    """(C,) tile ids → (C, 256, 2) pixel-center coordinates."""
    dev = tile_ids.device
    ly, lx = torch.meshgrid(torch.arange(TILE, device=dev), torch.arange(TILE, device=dev), indexing="ij")
    local = torch.stack([lx.reshape(-1), ly.reshape(-1)], dim=-1).to(dtype) + 0.5
    origin = torch.stack([(tile_ids % tiles_x) * TILE, (tile_ids // tiles_x) * TILE], dim=-1).to(dtype)
    return origin[:, None, :] + local[None, :, :]


def composite(px: torch.Tensor, idx: torch.Tensor, mean2d: torch.Tensor, conic: torch.Tensor,
              opacity: torch.Tensor, color: torch.Tensor, depth: torch.Tensor):
    """Front-to-back compositing of ordered Gaussian lists.

    px (C, P, 2) pixel centers; idx (C, K) Gaussian ids in front-to-back order, -1 = empty slot.
    Returns rgb (C, P, 3), accumulated alpha (C, P), Σ w z (C, P).
    """
    present = idx >= 0
    safe = idx.clamp_min(0)
    mu = mean2d[safe]                                        # (C, K, 2)
    co = conic[safe]                                         # (C, K, 3)
    d = px[:, :, None, :] - mu[:, None, :, :]                # (C, P, K, 2)
    dx, dy = d[..., 0], d[..., 1]
    power = -0.5 * (co[:, None, :, 0] * dx * dx + co[:, None, :, 2] * dy * dy) - co[:, None, :, 1] * dx * dy
    alpha = (opacity[safe][:, None, :] * torch.exp(power.clamp_max(0.0))).clamp_max(ALPHA_MAX)
    keep = present[:, None, :] & (power <= 0) & (alpha >= ALPHA_MIN)
    alpha = torch.where(keep, alpha, torch.zeros_like(alpha))
    log_t = torch.log1p(-alpha)
    cum = torch.cumsum(log_t, dim=-1)
    weight = alpha * torch.exp(cum - log_t)                  # alpha_i · Π_{j<i} (1 − alpha_j)
    rgb = torch.einsum("cpk,ckd->cpd", weight, color[safe])
    acc = 1.0 - torch.exp(cum[..., -1])
    dep = torch.einsum("cpk,ck->cp", weight, depth[safe])
    return rgb, acc, dep


class TorchTileRasterizer:
    """`Rasterizer` implementation (docs/architecture §12): render(gaussians, camera) → rgb, alpha."""

    name = "torch"

    def __init__(self, max_per_tile: int = 2048, chunk_elements: int = 6_000_000, checkpointing: bool = True):
        self.max_per_tile = max_per_tile
        self.chunk_elements = chunk_elements
        self.checkpointing = checkpointing

    def render(self, means: torch.Tensor, scales: torch.Tensor, quats: torch.Tensor, opacities: torch.Tensor,
               colors: torch.Tensor, cam: Camera, background: torch.Tensor | None = None) -> RasterOutput:
        """means (N,3), scales (N,3) positive std devs, quats (N,4) wxyz, opacities (N,) in (0,1), colors (N,3)."""
        H, W = cam.height, cam.width
        dev, dt = means.device, means.dtype
        bg = background if background is not None else torch.zeros(3, device=dev, dtype=dt)
        p = project(means, scales, quats, cam)
        bins = bin_tiles(p, W, H, self.max_per_tile)
        n_tiles = bins.tiles_x * bins.tiles_y
        P = PIXELS_PER_TILE
        stats = {"visible": int(p.valid.sum()), "pairs": bins.pairs, "dropped": bins.dropped,
                 "max_per_tile": int(bins.counts.max()) if bins.counts.numel() else 0, "chunks": 0}

        rgb_t = torch.zeros(n_tiles, P, 3, device=dev, dtype=dt)
        acc_t = torch.zeros(n_tiles, P, device=dev, dtype=dt)
        dep_t = torch.zeros(n_tiles, P, device=dev, dtype=dt)
        if bins.index.shape[1] > 0:
            # Group tiles of similar length so short tiles are not padded to the longest one.
            counts = bins.counts.clamp_max(bins.index.shape[1])
            order = torch.argsort(counts, descending=True)
            counts_sorted = counts[order].tolist()
            outs, pieces = [], []
            i = 0
            grad = torch.is_grad_enabled() and self.checkpointing
            while i < n_tiles:
                k = counts_sorted[i]
                if k == 0:
                    break
                c = max(1, min(n_tiles - i, self.chunk_elements // (P * k)))
                tiles = order[i:i + c]
                idx = bins.index[tiles, :k]
                px = _tile_pixels(tiles, bins.tiles_x, dt)
                args = (px, idx, p.mean2d, p.conic, opacities, colors, p.depth)
                out = checkpoint(composite, *args, use_reentrant=False) if grad else composite(*args)
                outs.append(out)
                pieces.append(tiles)
                i += c
            stats["chunks"] = len(outs)
            if outs:
                tiles = torch.cat(pieces)
                rgb_t = rgb_t.index_put((tiles,), torch.cat([o[0] for o in outs]))
                acc_t = acc_t.index_put((tiles,), torch.cat([o[1] for o in outs]))
                dep_t = dep_t.index_put((tiles,), torch.cat([o[2] for o in outs]))

        def untile(x: torch.Tensor) -> torch.Tensor:
            tail = x.shape[2:]
            x = x.reshape(bins.tiles_y, bins.tiles_x, TILE, TILE, *tail)
            x = x.permute(0, 2, 1, 3, *range(4, x.dim())).reshape(bins.tiles_y * TILE, bins.tiles_x * TILE, *tail)
            return x[:H, :W]

        acc = untile(acc_t)
        rgb = untile(rgb_t) + (1.0 - acc)[..., None] * bg
        return RasterOutput(rgb=rgb, alpha=acc, depth=untile(dep_t), stats=stats)
