"""`3dgs-optim-v1`: fit 3D Gaussians to a captured view bundle (docs/architecture §5.2).

Steps: initialize from the unprojected depth of the training views (voxel-downsampled colored point cloud) →
Adam over every Gaussian parameter through the differentiable rasterizer → periodic opacity / bounds pruning →
held-out evaluation → serialization to 3DGS PLY and a reload check (the decoded file must render identically).

All Gaussian centers are optimized in region-local coordinates (world minus the region origin), which is also how
they are stored in the asset, so float32 precision does not depend on where the region is in the world.
"""

from __future__ import annotations

import hashlib
import json
import math
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np
import torch

from gpu.bundle import Bundle, View, unproject_depth
from gpu.ply import SH_C0, GaussianArrays, decode_ply, encode_ply, rgb_to_sh_dc, validate_gaussians
from gpu.rasterizers.torch_tiles import Camera, TorchTileRasterizer

from .losses import psnr, ssim

GIB = 1024 ** 3
SH_C1 = 0.4886025119029199


@dataclass
class TrainConfig:
    iterations: int = 1500
    max_gaussians: int = 25_000
    init_opacity: float = 0.3
    sh_degree: int = 1                # 1 = view-dependent color (water reflections); 0 = diffuse only
    lr_means: float = 1.6e-4          # × scene extent, decayed exponentially to 1 % (reference schedule)
    lr_log_scales: float = 5e-3
    lr_quats: float = 1e-3
    lr_opacity: float = 5e-2
    lr_sh: float = 2.5e-3             # higher SH bands use lr_sh / 20 (reference)
    ssim_weight: float = 0.2          # (1 − λ) L1 + λ (1 − SSIM), as in the 3DGS reference
    alpha_weight: float = 0.5         # L1 on coverage (the capture has a transparent background)
    depth_weight: float = 0.1         # relative L1 on expected depth where the capture saw a surface
    prune_every: int = 200
    prune_opacity: float = 0.02
    eval_every: int = 250
    log_every: int = 10
    bounds_margin_m: float = 2.0      # centers must stay inside the clip box grown by this margin
    min_holdout_psnr: float = 20.0
    vram_cap_gib: float = 3.5
    seed: int = 0


@dataclass
class TrainResult:
    ok: bool
    problems: list[str]
    metrics: dict = field(default_factory=dict)


def _camera(view: View, origin: np.ndarray, device) -> Camera:
    shift = np.eye(4)
    shift[:3, 3] = origin                      # p_world = p_local + origin
    w2c = torch.tensor(view.world_to_cam_cv @ shift, dtype=torch.float32, device=device)
    K = view.K
    return Camera(w2c, float(K[0, 0]), float(K[1, 1]), float(K[0, 2]), float(K[1, 2]), view.width, view.height)


def _center(view: View, origin: np.ndarray, device) -> torch.Tensor:
    """Camera position in region-local coordinates."""
    return torch.tensor(view.camera_to_world_gl[:3, 3] - origin, dtype=torch.float32, device=device)


def _voxel_downsample(points: np.ndarray, colors: np.ndarray, target: int) -> tuple[np.ndarray, np.ndarray, float]:
    """Average points per voxel; the voxel size is searched so the result has at most `target` points."""
    lo, hi = 0.02, 8.0
    best = None
    for _ in range(18):
        size = math.sqrt(lo * hi)
        keys = np.floor(points / size).astype(np.int64)
        _, inverse, counts = np.unique(keys, axis=0, return_inverse=True, return_counts=True)
        n = counts.size
        if n > target:
            lo = size
        else:
            hi = size
            best = (size, inverse.reshape(-1), counts)
        if best and n > 0.92 * target and n <= target:
            break
    if best is None:
        size = hi
        keys = np.floor(points / size).astype(np.int64)
        _, inverse, counts = np.unique(keys, axis=0, return_inverse=True, return_counts=True)
        best = (size, inverse.reshape(-1), counts)
    size, inverse, counts = best
    pts = np.zeros((counts.size, 3))
    cols = np.zeros((counts.size, 3))
    np.add.at(pts, inverse, points)
    np.add.at(cols, inverse, colors)
    return (pts / counts[:, None]).astype(np.float32), (cols / counts[:, None]).astype(np.float32), size


def _knn_mean_distance(points: torch.Tensor, k: int = 3, chunk: int = 2048) -> torch.Tensor:
    out = torch.empty(points.shape[0], device=points.device)
    for i in range(0, points.shape[0], chunk):
        d = torch.cdist(points[i:i + chunk], points)
        out[i:i + chunk] = d.topk(k + 1, largest=False).values[:, 1:].mean(dim=1)
    return out


class GaussianModel:
    """Raw (unconstrained) parameters; activations: exp for scales, sigmoid for opacity, normalize for rotations."""

    def __init__(self, means, log_scales, quats, opacity_logits, sh_dc, sh_rest=None):
        self.params = {
            "means": means.requires_grad_(), "log_scales": log_scales.requires_grad_(),
            "quats": quats.requires_grad_(), "opacity_logits": opacity_logits.requires_grad_(),
            "sh_dc": sh_dc.requires_grad_(),
        }
        if sh_rest is not None:
            if sh_rest.shape[1] != 3:
                raise ValueError("only SH degree 0 and 1 are supported")
            self.params["sh_rest"] = sh_rest.requires_grad_()  # (N, 3 coefficients, RGB)

    @property
    def count(self) -> int:
        return self.params["means"].shape[0]

    def activated(self, camera_center: torch.Tensor):
        """Rasterizer inputs for one camera (color depends on the view direction when SH degree ≥ 1)."""
        p = self.params
        color = SH_C0 * p["sh_dc"]
        if "sh_rest" in p:
            # real SH basis and sign convention of the 3DGS reference (eval_sh), direction from camera to Gaussian
            d = p["means"] - camera_center
            d = d / d.norm(dim=-1, keepdim=True).clamp_min(1e-9)
            x, y, z = d[:, 0:1], d[:, 1:2], d[:, 2:3]
            r = p["sh_rest"]
            color = color - SH_C1 * y * r[:, 0] + SH_C1 * z * r[:, 1] - SH_C1 * x * r[:, 2]
        colors = (color + 0.5).clamp_min(0.0)
        return p["means"], p["log_scales"].exp(), p["quats"], torch.sigmoid(p["opacity_logits"]), colors

    def keep(self, mask: torch.Tensor, optimizer: torch.optim.Adam) -> None:
        """Remove Gaussians where mask is False, carrying the Adam moments of the survivors."""
        for group in optimizer.param_groups:
            old = group["params"][0]
            name = group["name"]
            new = old.detach()[mask].clone().requires_grad_()
            state = optimizer.state.pop(old, None)
            if state:
                for key in ("exp_avg", "exp_avg_sq"):
                    state[key] = state[key][mask].clone()
                optimizer.state[new] = state
            group["params"][0] = new
            self.params[name] = new

    def to_arrays(self) -> GaussianArrays:
        p = {k: v.detach().float().cpu().numpy() for k, v in self.params.items()}
        q = p["quats"] / np.linalg.norm(p["quats"], axis=1, keepdims=True).clip(1e-12)
        return GaussianArrays(means=p["means"], log_scales=p["log_scales"], quats=q.astype(np.float32),
                              opacity_logits=p["opacity_logits"], sh_dc=p["sh_dc"], sh_rest=p.get("sh_rest"))


def initialize(bundle: Bundle, cfg: TrainConfig, device) -> tuple[GaussianModel, dict]:
    pts, cols = [], []
    for v in bundle.train_views():
        p, c = unproject_depth(v, stride=1, min_alpha=0.5)
        pts.append(p)
        cols.append(c)
    points = np.concatenate(pts) - bundle.region_origin
    colors = np.concatenate(cols)
    lo = bundle.clip_min - bundle.region_origin
    hi = bundle.clip_max - bundle.region_origin
    inside = np.all((points >= lo) & (points <= hi), axis=1)
    points, colors = points[inside], colors[inside]
    if points.shape[0] == 0:
        raise ValueError("the training views contain no surface pixels inside the clip box")
    means, rgb, voxel = _voxel_downsample(points, colors, cfg.max_gaussians)
    m = torch.tensor(means, device=device)
    dist = _knn_mean_distance(m).clamp(0.02, 4.0 * voxel)
    n = m.shape[0]
    model = GaussianModel(
        means=m,
        log_scales=torch.log(dist)[:, None].repeat(1, 3),
        quats=torch.tensor([[1.0, 0.0, 0.0, 0.0]], device=device).repeat(n, 1),
        opacity_logits=torch.full((n,), math.log(cfg.init_opacity / (1 - cfg.init_opacity)), device=device),
        sh_dc=torch.tensor(rgb_to_sh_dc(rgb), device=device, dtype=torch.float32),
        sh_rest=torch.zeros(n, 3, 3, device=device) if cfg.sh_degree >= 1 else None,
    )
    return model, {"surface_points": int(points.shape[0]), "voxel_m": voxel, "init_gaussians": n}


def _targets(view: View, device):
    rgb = torch.tensor(view.rgb, device=device)
    alpha = torch.tensor(view.alpha, device=device)
    depth = torch.tensor(view.depth, device=device)
    observed = torch.tensor(view.observed, device=device)
    return rgb, alpha, depth, observed


def _composite(rgb, alpha, bg):
    return rgb * alpha[..., None] + (1 - alpha[..., None]) * bg


@torch.no_grad()
def evaluate(model: GaussianModel, views: list[View], origin, raster, device) -> dict:
    """PSNR against the capture composited on black over observed pixels: whole image, and covered pixels only
    (capture or reconstruction has alpha > 0.05). Unobserved pixels are excluded everywhere (SSIM: set equal)."""
    per_view = []
    black = torch.zeros(3, device=device)
    for v in views:
        out = raster.render(*model.activated(_center(v, origin, device)), _camera(v, origin, device), black)
        rgb, alpha, _, observed = _targets(v, device)
        pred = out.rgb.clamp(0, 1)
        gt = torch.where(observed[..., None], _composite(rgb, alpha, black), pred)
        covered = observed & ((alpha > 0.05) | (out.alpha > 0.05))
        per_view.append({
            "index": v.index,
            "psnr": psnr(pred, gt, observed),
            "psnr_covered": psnr(pred, gt, covered),
            "ssim": float(ssim(pred, gt)),
            "alpha_l1": float((out.alpha - alpha).abs()[observed].mean()),
            "unobserved_fraction": float(1 - observed.float().mean()),
        })
    mean = {k: float(np.mean([p[k] for p in per_view])) for k in ("psnr", "psnr_covered", "ssim", "alpha_l1")}
    return {**mean, "views": per_view}


def _save_comparison(path: Path, gt: torch.Tensor, pred: torch.Tensor) -> None:
    from PIL import Image

    diff = ((pred - gt).abs() * 4).clamp(0, 1)  # unobserved pixels show their (meaningless) diff vs magenta
    row = torch.cat([gt, pred.clamp(0, 1), diff], dim=1)
    Image.fromarray((row.cpu().numpy() * 255 + 0.5).astype(np.uint8)).save(path)


def train(bundle: Bundle, out_dir: Path, cfg: TrainConfig, device: str = "cuda",
          progress=None) -> TrainResult:
    out_dir.mkdir(parents=True, exist_ok=True)
    dev = torch.device(device)
    if dev.type == "cuda" and dev.index is None:
        dev = torch.device("cuda", torch.cuda.current_device())
    torch.manual_seed(cfg.seed)
    rng = np.random.default_rng(cfg.seed)
    total_vram = torch.cuda.get_device_properties(dev).total_memory
    fraction = min(1.0, cfg.vram_cap_gib * GIB / total_vram)
    torch.cuda.set_per_process_memory_fraction(fraction, dev)
    torch.cuda.empty_cache()  # cached blocks from earlier work in this process would bypass the cap
    torch.cuda.reset_peak_memory_stats(dev)
    free0, _ = torch.cuda.mem_get_info(dev)

    origin = bundle.region_origin
    raster = TorchTileRasterizer()
    t0 = time.perf_counter()
    try:
        model, init_info = initialize(bundle, cfg, dev)
    except torch.OutOfMemoryError as exc:
        return TrainResult(False, [f"GPU memory cap of {cfg.vram_cap_gib} GiB exceeded during initialization: {exc}"])
    torch.cuda.synchronize()
    init_s = time.perf_counter() - t0

    extent = float(np.linalg.norm(bundle.clip_max - bundle.clip_min) / 2)
    lr_means0 = cfg.lr_means * extent
    groups = [
        {"params": [model.params["means"]], "lr": lr_means0, "name": "means"},
        {"params": [model.params["log_scales"]], "lr": cfg.lr_log_scales, "name": "log_scales"},
        {"params": [model.params["quats"]], "lr": cfg.lr_quats, "name": "quats"},
        {"params": [model.params["opacity_logits"]], "lr": cfg.lr_opacity, "name": "opacity_logits"},
        {"params": [model.params["sh_dc"]], "lr": cfg.lr_sh, "name": "sh_dc"},
    ]
    if "sh_rest" in model.params:
        groups.append({"params": [model.params["sh_rest"]], "lr": cfg.lr_sh / 20, "name": "sh_rest"})
    opt = torch.optim.Adam(groups, eps=1e-15)
    train_views = bundle.train_views()
    holdout = bundle.holdout_views()
    cams = [_camera(v, origin, dev) for v in train_views]
    centers = [_center(v, origin, dev) for v in train_views]
    targets = [_targets(v, dev) for v in train_views]
    lo = torch.tensor(bundle.clip_min - origin - cfg.bounds_margin_m, device=dev, dtype=torch.float32)
    hi = torch.tensor(bundle.clip_max - origin + cfg.bounds_margin_m, device=dev, dtype=torch.float32)

    loss_curve, eval_curve, pruned = [], [], []
    device_used_peak = 0
    order = []
    t_train = time.perf_counter()
    try:
        for it in range(1, cfg.iterations + 1):
            frac = (it - 1) / max(1, cfg.iterations - 1)
            opt.param_groups[0]["lr"] = lr_means0 * (0.01 ** frac)
            if not order:
                order = list(rng.permutation(len(train_views)))
            vi = order.pop()
            rgb_gt, alpha_gt, depth_gt, observed = targets[vi]
            bg = torch.rand(3, device=dev)
            out = raster.render(*model.activated(centers[vi]), cams[vi], bg)
            # unobserved pixels get the prediction itself as target: zero gradient from them in every term
            gt = torch.where(observed[..., None], _composite(rgb_gt, alpha_gt, bg), out.rgb.detach())
            a_gt = torch.where(observed, alpha_gt, out.alpha.detach())
            l1 = (out.rgb - gt).abs().mean()
            l_ssim = 1.0 - ssim(out.rgb, gt)
            l_alpha = (out.alpha - a_gt).abs().mean()
            surf = observed & (alpha_gt > 0.99) & (depth_gt > 0)
            if bool(surf.any()):
                d_pred = out.depth[surf] / out.alpha[surf].clamp_min(1e-3)
                l_depth = ((d_pred - depth_gt[surf]).abs() / depth_gt[surf]).mean()
            else:
                l_depth = torch.zeros((), device=dev)
            loss = (1 - cfg.ssim_weight) * l1 + cfg.ssim_weight * l_ssim + cfg.alpha_weight * l_alpha \
                + cfg.depth_weight * l_depth
            opt.zero_grad(set_to_none=True)
            loss.backward()
            opt.step()

            if it % cfg.log_every == 0 or it == 1:
                free, _ = torch.cuda.mem_get_info(dev)
                device_used_peak = max(device_used_peak, total_vram - free)
                loss_curve.append({"it": it, "loss": loss.item(), "l1": l1.item(), "dssim": l_ssim.item(),
                                   "alpha": l_alpha.item(), "depth": l_depth.item(), "count": model.count,
                                   "t": time.perf_counter() - t_train})
                if progress:
                    progress(loss_curve[-1])
            if it % cfg.prune_every == 0 and it < cfg.iterations:
                with torch.no_grad():
                    m = model.params["means"]
                    keep = (torch.sigmoid(model.params["opacity_logits"]) >= cfg.prune_opacity) & \
                        torch.all((m >= lo) & (m <= hi), dim=1)
                removed = int((~keep).sum())
                if removed:
                    model.keep(keep, opt)
                pruned.append({"it": it, "removed": removed, "count": model.count})
            if it % cfg.eval_every == 0 or it == cfg.iterations:
                ev = evaluate(model, holdout, origin, raster, dev)
                eval_curve.append({"it": it, "psnr": ev["psnr"], "psnr_covered": ev["psnr_covered"],
                                   "ssim": ev["ssim"], "t": time.perf_counter() - t_train})
    except torch.OutOfMemoryError as exc:
        return TrainResult(False, [f"GPU memory cap of {cfg.vram_cap_gib} GiB exceeded: {exc}"],
                           {"peak_allocated_mb": torch.cuda.max_memory_allocated(dev) / 2 ** 20})
    torch.cuda.synchronize()
    train_s = time.perf_counter() - t_train

    # final pruning of anything outside the bounds (the asset must satisfy the validator)
    with torch.no_grad():
        m = model.params["means"]
        keep = torch.all((m >= lo) & (m <= hi), dim=1) & torch.isfinite(m).all(dim=1)
    if not bool(keep.all()):
        model.keep(keep, opt)

    t_eval = time.perf_counter()
    final_holdout = evaluate(model, holdout, origin, raster, dev)
    final_train = evaluate(model, train_views[:: max(1, len(train_views) // 6)], origin, raster, dev)
    eval_s = time.perf_counter() - t_eval

    arrays = model.to_arrays()
    problems = validate_gaussians(arrays, lo.cpu().numpy(), hi.cpu().numpy())
    data = encode_ply(arrays)
    sha = hashlib.sha256(data).hexdigest()
    ply_path = out_dir / "gaussians.ply"
    ply_path.write_bytes(data)

    # reload check: the bytes on disk must decode to the same Gaussians and render the same image
    reloaded = decode_ply(ply_path.read_bytes())
    with torch.no_grad():
        black = torch.zeros(3, device=dev)
        cam, center = _camera(holdout[0], origin, dev), _center(holdout[0], origin, dev)
        ref = raster.render(*model.activated(center), cam, black).rgb
        rm = GaussianModel(*(None if a is None else torch.tensor(a, device=dev) for a in (
            reloaded.means, reloaded.log_scales, reloaded.quats, reloaded.opacity_logits, reloaded.sh_dc,
            reloaded.sh_rest)))
        re = raster.render(*[t.detach() for t in rm.activated(center)], cam, black).rgb
        reload_diff = float((ref - re).abs().max())
        magenta = torch.tensor([0.6, 0.0, 0.6], device=dev)
        for v in holdout:
            out = raster.render(*model.activated(_center(v, origin, dev)), _camera(v, origin, dev), black)
            rgb, alpha, _, observed = _targets(v, dev)
            gt = torch.where(observed[..., None], _composite(rgb, alpha, black), magenta)  # magenta = unobserved
            _save_comparison(out_dir / f"holdout_{v.index:02d}.png", gt, out.rgb)
    if reload_diff > 1e-5:
        problems.append(f"reloaded asset renders differently (max abs diff {reload_diff:.2e})")
    if not final_holdout["psnr"] >= cfg.min_holdout_psnr:
        problems.append(f"holdout PSNR {final_holdout['psnr']:.2f} dB is below {cfg.min_holdout_psnr} dB")

    bmin = arrays.means.min(axis=0) + origin
    bmax = arrays.means.max(axis=0) + origin
    metrics = {
        "method": "3dgs-optim-v1",
        "rasterizer": raster.name,
        "device": torch.cuda.get_device_name(dev),
        "torch": torch.__version__,
        "config": asdict(cfg),
        "iterations": cfg.iterations,
        "gaussian_count": arrays.count,
        **init_info,
        "pruned": pruned,
        "train_views": len(train_views),
        "holdout_views": len(holdout),
        "resolution": [train_views[0].width, train_views[0].height],
        "init_seconds": init_s,
        "train_seconds": train_s,
        "eval_seconds": eval_s,
        "ms_per_iteration": 1000 * train_s / cfg.iterations,
        "psnr_holdout": final_holdout["psnr"],
        "psnr_holdout_covered": final_holdout["psnr_covered"],
        "ssim_holdout": final_holdout["ssim"],
        "alpha_l1_holdout": final_holdout["alpha_l1"],
        "holdout": final_holdout["views"],
        "psnr_train": final_train["psnr"],
        "loss_curve": loss_curve,
        "eval_curve": eval_curve,
        "vram_cap_mb": cfg.vram_cap_gib * 1024,
        "peak_allocated_mb": torch.cuda.max_memory_allocated(dev) / 2 ** 20,
        "peak_reserved_mb": torch.cuda.max_memory_reserved(dev) / 2 ** 20,
        "device_used_peak_mb": device_used_peak / 2 ** 20,
        "device_free_at_start_mb": free0 / 2 ** 20,
        "asset_bytes": len(data),
        "sha256": sha,
        "sh_degree": arrays.sh_degree,
        "origin": [float(x) for x in origin],
        "bounds": {"min": [float(x) for x in bmin], "max": [float(x) for x in bmax]},
        "reload_max_abs_diff": reload_diff,
        "problems": problems,
    }
    (out_dir / "metrics.json").write_text(json.dumps(metrics, indent=1), encoding="utf-8")
    return TrainResult(not problems, problems, metrics)
