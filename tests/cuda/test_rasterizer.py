"""CUDA tests of the differentiable tile rasterizer. Run in WSL2: scripts/wsl-gpu.sh."""

from __future__ import annotations

import math

import pytest

try:  # on Windows, Smart App Control blocks the torch DLLs (OSError, not ImportError)
    import torch
except Exception as exc:  # noqa: BLE001
    pytest.skip(f"torch unavailable: {exc}", allow_module_level=True)
if not torch.cuda.is_available():
    pytest.skip("CUDA is not available", allow_module_level=True)

from gpu.rasterizers import RasterizerUnavailable, make_rasterizer  # noqa: E402
from gpu.rasterizers.reference import render_dense  # noqa: E402
from gpu.rasterizers.torch_tiles import Camera, TorchTileRasterizer, project  # noqa: E402

pytestmark = pytest.mark.cuda
DEV = torch.device("cuda")


def look_at_cv(eye, target, dtype=torch.float32) -> torch.Tensor:
    eye, target = torch.tensor(eye, dtype=torch.float64), torch.tensor(target, dtype=torch.float64)
    f = target - eye
    f = f / f.norm()
    r = torch.linalg.cross(f, torch.tensor([0.0, 1.0, 0.0], dtype=torch.float64))
    r = r / r.norm()
    d = torch.linalg.cross(f, r)               # camera y (down)
    R = torch.stack([r, d, f])                 # rows: camera x, y, z in world coordinates
    M = torch.eye(4, dtype=torch.float64)
    M[:3, :3] = R
    M[:3, 3] = -R @ eye
    return M.to(DEV, dtype)


def scene(n: int, seed: int = 0, dtype=torch.float32, spread: float = 1.5):
    g = torch.Generator().manual_seed(seed)
    means = (torch.rand(n, 3, generator=g, dtype=torch.float64) - 0.5) * 2 * spread
    scales = torch.exp(torch.rand(n, 3, generator=g, dtype=torch.float64) * 1.5 - 3.2)
    quats = torch.randn(n, 4, generator=g, dtype=torch.float64)
    opac = torch.rand(n, generator=g, dtype=torch.float64) * 0.8 + 0.1
    cols = torch.rand(n, 3, generator=g, dtype=torch.float64)
    return [t.to(DEV, dtype) for t in (means, scales, quats, opac, cols)]


def camera(w=96, h=80, dtype=torch.float32, eye=(0.3, 0.8, -5.0)) -> Camera:
    fov = math.radians(50)
    f = 0.5 * w / math.tan(fov / 2)
    return Camera(look_at_cv(eye, (0, 0, 0), dtype), f, f, w / 2, h / 2, w, h)


def test_tile_renderer_matches_dense_reference():
    means, scales, quats, opac, cols = scene(400)
    cam = camera()
    bg = torch.tensor([0.2, 0.3, 0.4], device=DEV)
    tiled = TorchTileRasterizer(chunk_elements=50_000).render(means, scales, quats, opac, cols, cam, bg)
    dense = render_dense(means, scales, quats, opac, cols, cam, bg)
    assert tiled.stats["dropped"] == 0 and tiled.stats["chunks"] > 1
    assert tiled.rgb.shape == (80, 96, 3)
    assert (tiled.rgb - dense.rgb).abs().max() < 2e-5
    assert (tiled.alpha - dense.alpha).abs().max() < 2e-5
    assert (tiled.depth - dense.depth).abs().max() < 1e-4
    assert tiled.alpha.max() > 0.5  # the scene is actually visible


def test_gradients_match_dense_reference_and_live_on_cuda():
    params = [p.requires_grad_() for p in scene(250, seed=1)]
    cam = camera()
    grads = []
    for render in (lambda *a: TorchTileRasterizer(chunk_elements=40_000).render(*a, cam),
                   lambda *a: render_dense(*a, cam)):
        for p in params:
            p.grad = None
        out = render(*params)
        target = torch.linspace(0, 1, out.rgb.numel(), device=DEV).reshape(out.rgb.shape)
        ((out.rgb - target) ** 2).mean().add(out.alpha.mean()).backward()
        grads.append([p.grad.clone() for p in params])
    for gt, gd, name in zip(*grads, ("means", "scales", "quats", "opacities", "colors")):
        assert gt.device.type == "cuda"
        assert torch.isfinite(gt).all(), name
        assert gt.abs().sum() > 0, name
        scale = gd.abs().max().clamp_min(1e-8)
        assert ((gt - gd).abs().max() / scale) < 1e-3, name


def test_gradcheck_against_finite_differences_float64():
    means, scales, quats, opac, cols = scene(6, seed=3, dtype=torch.float64, spread=0.6)
    scales = scales * 3.0
    cam = camera(32, 32, torch.float64, eye=(0.2, 0.3, -3.0))
    raster = TorchTileRasterizer(chunk_elements=4_000)
    weights = torch.rand(32, 32, 3, generator=torch.Generator().manual_seed(9), dtype=torch.float64).to(DEV)

    def f(m, s, q, o, c):
        out = raster.render(m, s, q, o, c, cam)
        return (out.rgb * weights).sum() + out.alpha.sum() * 0.1

    inputs = tuple(t.clone().requires_grad_() for t in (means, scales, quats, opac, cols))
    assert torch.autograd.gradcheck(f, inputs, eps=1e-6, atol=1e-5, rtol=1e-3, nondet_tol=1e-9)


def test_projection_of_single_isotropic_gaussian():
    cam = camera(128, 128, eye=(0, 0, -10))
    means = torch.tensor([[0.0, 0.0, 0.0]], device=DEV)
    scales = torch.full((1, 3), 0.1, device=DEV)
    quats = torch.tensor([[1.0, 0, 0, 0]], device=DEV)
    p = project(means, scales, quats, cam)
    assert torch.allclose(p.mean2d, torch.tensor([[64.0, 64.0]], device=DEV), atol=1e-4)
    assert abs(float(p.depth) - 10.0) < 1e-5
    sigma_px = cam.fx * 0.1 / 10.0
    expected = 1.0 / (sigma_px ** 2 + 0.3)
    assert abs(float(p.conic[0, 0]) - expected) / expected < 1e-4
    assert abs(float(p.conic[0, 1])) < 1e-6


def test_truncation_is_reported():
    means, scales, quats, opac, cols = scene(400)
    out = TorchTileRasterizer(max_per_tile=8).render(means, scales, quats, opac, cols, camera())
    assert out.stats["dropped"] > 0


def test_optimization_reduces_photometric_error():
    torch.manual_seed(0)
    target_params = scene(150, seed=5)
    cams = [camera(64, 64, eye=e) for e in ((0, 0.5, -5), (4, 1, -3), (-4, 1.5, -2), (0, 4, -3))]
    raster = TorchTileRasterizer()
    with torch.no_grad():
        targets = [raster.render(*target_params, c).rgb for c in cams]
    m, s, q, o, c = scene(150, seed=6)
    raw = [m.clone().requires_grad_(), torch.log(s).requires_grad_(), q.clone().requires_grad_(),
           torch.logit(o).requires_grad_(), c.clone().requires_grad_()]
    opt = torch.optim.Adam(raw, lr=0.02)

    @torch.no_grad()
    def loss_all():
        return sum((raster.render(raw[0], raw[1].exp(), raw[2], raw[3].sigmoid(), raw[4], cam).rgb - t).abs().mean()
                   for cam, t in zip(cams, targets))

    start = float(loss_all())
    for i in range(150):
        opt.zero_grad()
        cam = cams[i % len(cams)]
        out = raster.render(raw[0], raw[1].exp(), raw[2], raw[3].sigmoid(), raw[4], cam)
        (out.rgb - targets[i % len(cams)]).abs().mean().backward()
        opt.step()
    end = float(loss_all())
    assert end < 0.5 * start, (start, end)


def test_gsplat_unavailable_is_explicit():
    try:
        import gsplat  # noqa: F401
    except Exception:
        with pytest.raises(RasterizerUnavailable):
            make_rasterizer("gsplat")
    else:
        pytest.skip("gsplat is importable here; adapter parity test not written yet")
