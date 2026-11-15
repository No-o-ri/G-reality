"""Load browser capture bundles and unproject their linear depth."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from gpu.cameras import intrinsics, mat4_from_column_major, world_to_cam_cv


UNOBSERVED = -1.0  # depth marker: the ray entered the clip box inside a solid; the pixel carries no information


class BundleError(ValueError):
    """Invalid capture bundle."""


@dataclass
class View:
    index: int
    width: int
    height: int
    K: np.ndarray
    camera_to_world_gl: np.ndarray
    world_to_cam_cv: np.ndarray
    rgb: np.ndarray
    alpha: np.ndarray
    depth: np.ndarray
    holdout: bool

    @property
    def observed(self) -> np.ndarray:
        """(H, W) bool: pixels that constrain the reconstruction (everything except UNOBSERVED)."""
        return self.depth != UNOBSERVED


@dataclass
class Bundle:
    meta: dict
    views: list[View]
    region_origin: np.ndarray
    clip_min: np.ndarray
    clip_max: np.ndarray

    def train_views(self) -> list[View]:
        return [view for view in self.views if not view.holdout]

    def holdout_views(self) -> list[View]:
        return [view for view in self.views if view.holdout]


def _load_png_rgba(path: Path) -> np.ndarray:
    from PIL import Image

    with Image.open(path) as image:
        if image.mode != "RGBA":
            raise BundleError(f"{path.name}: expected 8-bit RGBA PNG")
        return np.asarray(image, dtype=np.uint8).copy()


def _validate_bundle(bundle: Bundle) -> None:
    if not bundle.train_views() or not bundle.holdout_views():
        raise BundleError("bundle needs at least one train and one holdout view")
    for view in bundle.views:
        shape = (view.height, view.width)
        if view.width <= 0 or view.height <= 0:
            raise BundleError("view dimensions must be positive")
        if view.rgb.shape != (*shape, 3) or view.alpha.shape != shape:
            raise BundleError(f"view {view.index}: image size does not match metadata")
        if view.depth.shape != shape:
            raise BundleError(f"view {view.index}: depth size does not match metadata")
        if not np.isfinite(view.depth).all() or np.any((view.depth < 0) & (view.depth != UNOBSERVED)):
            raise BundleError(f"view {view.index}: depth must be finite and nonnegative (or {UNOBSERVED} = unobserved)")


def load_bundle(directory: str | Path) -> Bundle:
    directory = Path(directory)
    try:
        meta = json.loads((directory / "bundle.json").read_text(encoding="utf-8"))
        views = []
        for item in meta["views"]:
            index, width, height = item["index"], item["width"], item["height"]
            png_path = directory / f"view_{index:02d}.png"
            depth_path = directory / f"view_{index:02d}.depth.f32"
            for path in (png_path, depth_path):
                if not path.is_file():
                    raise BundleError(f"missing view file: {path.name}")
            raw = depth_path.read_bytes()
            if len(raw) != width * height * 4:
                raise BundleError(f"{depth_path.name}: expected exactly W*H*4 depth bytes")
            rgba = _load_png_rgba(png_path).astype(np.float32) / 255
            pose = mat4_from_column_major(item["camera_to_world"])
            views.append(View(index, width, height, intrinsics(item["fx"], item["fy"], item["cx"], item["cy"]), pose, world_to_cam_cv(pose), rgba[..., :3], rgba[..., 3], np.frombuffer(raw, dtype="<f4").reshape(height, width).copy(), item.get("holdout", False)))
        capture = meta["capture"]
        bundle = Bundle(meta, views, *(np.asarray(capture[name], dtype=np.float64) for name in ("region_origin", "clip_min", "clip_max")))
        _validate_bundle(bundle)
        return bundle
    except BundleError:
        raise
    except (OSError, ValueError, KeyError, TypeError, np.linalg.LinAlgError) as exc:
        raise BundleError(f"invalid capture bundle: {exc}") from exc


def unproject_depth(view: View, stride: int = 1, min_alpha: float = 0.5) -> tuple[np.ndarray, np.ndarray]:
    if not isinstance(stride, int) or stride < 1:
        raise ValueError("stride must be a positive integer")
    rows, cols = np.mgrid[0:view.height:stride, 0:view.width:stride]
    depth = view.depth[::stride, ::stride]
    mask = (view.alpha[::stride, ::stride] >= min_alpha) & (depth > 0)
    pixels = np.column_stack((cols[mask] + 0.5, rows[mask] + 0.5, np.ones(mask.sum())))
    camera = (pixels @ np.linalg.inv(view.K).T) * depth[mask, None]
    pose = np.linalg.inv(view.world_to_cam_cv)
    points = camera @ pose[:3, :3].T + pose[:3, 3]
    return points, view.rgb[::stride, ::stride][mask].copy()
