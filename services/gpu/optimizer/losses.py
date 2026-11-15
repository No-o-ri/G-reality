"""Image losses and metrics for Gaussian fitting."""

from __future__ import annotations

import math

import torch
import torch.nn.functional as F


def _gaussian_window(size: int, sigma: float, device, dtype) -> torch.Tensor:
    x = torch.arange(size, device=device, dtype=dtype) - (size - 1) / 2
    g = torch.exp(-(x * x) / (2 * sigma * sigma))
    g = g / g.sum()
    return (g[:, None] * g[None, :]).expand(3, 1, size, size).contiguous()


def ssim(a: torch.Tensor, b: torch.Tensor, size: int = 11, sigma: float = 1.5) -> torch.Tensor:
    """Mean SSIM of two (H, W, 3) images in [0, 1] (Wang et al. 2004, Gaussian window, as in the 3DGS reference)."""
    x = a.permute(2, 0, 1)[None]
    y = b.permute(2, 0, 1)[None]
    w = _gaussian_window(size, sigma, a.device, a.dtype)
    pad = size // 2
    mu_x = F.conv2d(x, w, padding=pad, groups=3)
    mu_y = F.conv2d(y, w, padding=pad, groups=3)
    sxx = F.conv2d(x * x, w, padding=pad, groups=3) - mu_x ** 2
    syy = F.conv2d(y * y, w, padding=pad, groups=3) - mu_y ** 2
    sxy = F.conv2d(x * y, w, padding=pad, groups=3) - mu_x * mu_y
    c1, c2 = 0.01 ** 2, 0.03 ** 2
    m = ((2 * mu_x * mu_y + c1) * (2 * sxy + c2)) / ((mu_x ** 2 + mu_y ** 2 + c1) * (sxx + syy + c2))
    return m.mean()


def psnr(a: torch.Tensor, b: torch.Tensor, mask: torch.Tensor | None = None) -> float:
    err = (a - b) ** 2
    if mask is not None:
        if not bool(mask.any()):
            return float("nan")
        err = err[mask]
    mse = float(err.mean())
    return float("inf") if mse == 0 else 10.0 * math.log10(1.0 / mse)
