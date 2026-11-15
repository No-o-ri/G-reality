"""End-to-end CUDA test of `3dgs-optim-v1` on a synthetic bundle. Run in WSL2 through scripts/wsl-gpu.sh."""

from __future__ import annotations

import json
import math

import numpy as np
import pytest

try:  # on Windows, Smart App Control blocks the torch DLLs (OSError, not ImportError)
    import torch
except Exception as exc:  # noqa: BLE001
    pytest.skip(f"torch unavailable: {exc}", allow_module_level=True)
if not torch.cuda.is_available():
    pytest.skip("CUDA is not available", allow_module_level=True)

from gpu.bundle import Bundle, View  # noqa: E402
from gpu.cameras import intrinsics, look_at_gl, world_to_cam_cv  # noqa: E402
from gpu.optimizer.train import TrainConfig, train  # noqa: E402
from gpu.ply import decode_ply  # noqa: E402
from gpu.rasterizers.torch_tiles import Camera, TorchTileRasterizer  # noqa: E402

pytestmark = pytest.mark.cuda
@pytest.fixture(autouse=True)
def _restore_memory_fraction():
    yield
    torch.cuda.set_per_process_memory_fraction(1.0)
    torch.cuda.empty_cache()


ORIGIN = np.array([128.0, 0.0, -64.0])   # a region away from the world origin: local/world handling must be right


def synthetic_bundle(size=64) -> Bundle:
    """A colored 'terrain' sheet with a few blobs, rendered by the rasterizer from a ring of cameras."""
    rng = np.random.default_rng(1)
    gx, gz = np.meshgrid(np.linspace(4, 60, 28), np.linspace(4, 60, 28))
    ground = np.stack([gx.ravel(), 2 * np.sin(gx.ravel() / 9) + 2, gz.ravel()], axis=1)
    blobs = np.column_stack([rng.uniform(10, 54, 40), rng.uniform(4, 10, 40), rng.uniform(10, 54, 40)])
    means = np.concatenate([ground, blobs]).astype(np.float32)
    n = means.shape[0]
    scales = np.concatenate([np.tile([[1.6, 0.25, 1.6]], (ground.shape[0], 1)), np.full((40, 3), 1.2)]).astype(np.float32)
    colors = np.concatenate([np.tile([[0.35, 0.6, 0.25]], (ground.shape[0], 1)), rng.uniform(0.2, 0.9, (40, 3))])
    dev = torch.device("cuda")
    t = lambda a: torch.tensor(a, device=dev, dtype=torch.float32)  # noqa: E731
    raster = TorchTileRasterizer()
    center = np.array([32.0, 4.0, 32.0]) + ORIGIN
    views = []
    fov = math.radians(50)
    f = size / 2 / math.tan(fov / 2)
    K = intrinsics(f, f, size / 2, size / 2)
    for i in range(14):
        holdout = i >= 12
        az = 2 * math.pi * (i + (0.5 if holdout else 0)) / 12
        el = math.radians(30 if not holdout else 22)
        eye = center + 95 * np.array([math.cos(el) * math.cos(az), math.sin(el), math.cos(el) * math.sin(az)])
        c2w = look_at_gl(eye, center)
        w2c = world_to_cam_cv(c2w)
        shift = np.eye(4)
        shift[:3, 3] = ORIGIN
        cam = Camera(t(w2c @ shift), f, f, size / 2, size / 2, size, size)
        with torch.no_grad():
            out = raster.render(t(means), t(scales), t(np.tile([1, 0, 0, 0], (n, 1))), t(np.full(n, 0.95)), t(colors), cam)
        alpha = out.alpha.cpu().numpy()
        rgb = (out.rgb / out.alpha.clamp_min(1e-6)[..., None]).clamp(0, 1).cpu().numpy() * (alpha[..., None] > 0.01)
        depth = np.where(alpha > 0.5, (out.depth / out.alpha.clamp_min(1e-6)).cpu().numpy(), 0.0).astype(np.float32)
        depth[-4:, :] = -1.0  # a band of unobserved pixels must be ignored, not treated as empty
        views.append(View(i, size, size, K, c2w, w2c, rgb.astype(np.float32), alpha.astype(np.float32), depth, holdout))
    meta = {"job_id": "t", "world_id": "t", "coord": {"rx": 2, "rz": -1}, "region_revision": 0, "provider": "browser_sdf"}
    return Bundle(meta, views, ORIGIN.copy(), ORIGIN + np.array([-2.0, -2.0, -2.0]), ORIGIN + np.array([66.0, 16.0, 66.0]))


def test_optimizer_end_to_end(tmp_path):
    bundle = synthetic_bundle()
    cfg = TrainConfig(iterations=400, max_gaussians=3000, eval_every=200, prune_every=150, min_holdout_psnr=22.0,
                      vram_cap_gib=1.0)
    result = train(bundle, tmp_path, cfg)
    m = result.metrics
    assert result.ok, result.problems
    assert m["psnr_holdout"] >= 22.0 and m["psnr_holdout_covered"] > 18.0
    first, last = m["loss_curve"][0]["loss"], np.mean([r["loss"] for r in m["loss_curve"][-5:]])
    assert last < 0.5 * first
    assert m["peak_reserved_mb"] <= 1024
    assert m["reload_max_abs_diff"] <= 1e-5
    g = decode_ply((tmp_path / "gaussians.ply").read_bytes())
    assert g.count == m["gaussian_count"] and g.sh_degree == 1
    # stored centers are region-local; bounds in the metrics are world-space
    assert np.all(g.means >= -4.1) and np.all(g.means <= 68.1)
    assert np.allclose(np.array(m["bounds"]["min"]), g.means.min(0) + ORIGIN, atol=1e-3)
    assert json.loads((tmp_path / "metrics.json").read_text())["sha256"] == m["sha256"]


def test_memory_cap_is_enforced(tmp_path):
    bundle = synthetic_bundle()
    cfg = TrainConfig(iterations=5, max_gaussians=3000, vram_cap_gib=0.002)
    result = train(bundle, tmp_path, cfg)
    assert not result.ok and "memory cap" in result.problems[0]
