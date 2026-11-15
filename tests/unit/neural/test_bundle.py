"""Capture bundle validation and depth geometry tests."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from gpu.bundle import Bundle, BundleError, View, _validate_bundle, load_bundle, unproject_depth
from gpu.cameras import intrinsics, look_at_gl, mat4_to_column_major, project_points, world_to_cam_cv


def view() -> View:
    pose = look_at_gl((3, 4, 5), (0, 1, -2))
    return View(0, 4, 3, intrinsics(8, 9, 2, 1.5), pose, world_to_cam_cv(pose), np.arange(36, dtype=np.float32).reshape(3, 4, 3) / 36, np.ones((3, 4), dtype=np.float32), np.full((3, 4), 10, dtype=np.float32), False)


def bundle(views: list[View]) -> Bundle:
    return Bundle({}, views, np.zeros(3), np.zeros(3), np.ones(3))


def test_unproject_plane(tmp_path: Path) -> None:
    v = view()
    path = tmp_path / "view_00.depth.f32"
    v.depth.astype("<f4").tofile(path)
    v.depth = np.frombuffer(path.read_bytes(), dtype="<f4").reshape(3, 4)
    points, rgb = unproject_depth(v)
    normal = -v.camera_to_world_gl[:3, 2]
    np.testing.assert_allclose((points - v.camera_to_world_gl[:3, 3]) @ normal, 10, atol=1e-4)
    uv, z = project_points(v.world_to_cam_cv, v.K, points)
    rows, cols = np.mgrid[:3, :4]
    np.testing.assert_allclose(uv, np.column_stack((cols.ravel() + 0.5, rows.ravel() + 0.5)), atol=1e-12)
    np.testing.assert_allclose(z, 10)
    np.testing.assert_array_equal(rgb, v.rgb.reshape(-1, 3))


def test_masks_stride_and_empty() -> None:
    v = view()
    v.alpha[0, 0] = 0.49
    v.alpha[0, 2] = 0.5
    v.depth[2, 0] = 0
    points, rgb = unproject_depth(v, stride=2)
    assert points.shape == (2, 3)
    np.testing.assert_array_equal(rgb, v.rgb[[0, 2], [2, 2]])
    assert unproject_depth(v, min_alpha=2)[0].shape == (0, 3)
    with pytest.raises(ValueError, match="stride"):
        unproject_depth(v, stride=0)


def test_synthetic_validation() -> None:
    v = view()
    _validate_bundle(bundle([v, replace(v, holdout=True)]))
    for views in ([], [v], [replace(v, holdout=True)]):
        with pytest.raises(BundleError, match="train and one holdout"):
            _validate_bundle(bundle(views))
    for bad, match in ((replace(v, rgb=v.rgb[:1]), "image size"), (replace(v, alpha=v.alpha[:1]), "image size"), (replace(v, depth=v.depth[:1]), "depth size"), (replace(v, depth=np.full((3, 4), -2.0)), "nonnegative"), (replace(v, depth=np.full((3, 4), np.nan)), "finite"), (replace(v, depth=np.full((3, 4), np.inf)), "finite")):
        with pytest.raises(BundleError, match=match):
            _validate_bundle(bundle([bad, replace(v, holdout=True)]))


def write_metadata(directory: Path) -> None:
    v = view()
    item = dict(index=0, width=4, height=3, fx=8, fy=9, cx=2, cy=1.5, camera_to_world=mat4_to_column_major(v.camera_to_world_gl), near=0.1, far=2000, holdout=False)
    meta = dict(job_id="job", world_id="world", coord=dict(rx=0, rz=0), region_revision=3, provider="browser_sdf", views=[item, dict(item, index=1, holdout=True)], capture=dict(region_origin=[0, 0, 0], clip_min=[-10] * 3, clip_max=[10] * 3))
    (directory / "bundle.json").write_text(json.dumps(meta), encoding="utf-8")


def test_missing_files_and_depth_size(tmp_path: Path) -> None:
    write_metadata(tmp_path)
    with pytest.raises(BundleError, match="missing view file"):
        load_bundle(tmp_path)
    (tmp_path / "view_00.png").write_bytes(b"")
    with pytest.raises(BundleError, match="missing view file"):
        load_bundle(tmp_path)
    (tmp_path / "view_00.depth.f32").write_bytes(b"short")
    with pytest.raises(BundleError, match="depth bytes"):
        load_bundle(tmp_path)


def test_load_end_to_end(tmp_path: Path) -> None:
    image = pytest.importorskip("PIL.Image")
    write_metadata(tmp_path)
    rgba = np.arange(48, dtype=np.uint8).reshape(3, 4, 4)
    for i in range(2):
        image.fromarray(rgba).save(tmp_path / f"view_{i:02d}.png")
        view().depth.astype("<f4").tofile(tmp_path / f"view_{i:02d}.depth.f32")
    loaded = load_bundle(tmp_path)
    assert len(loaded.train_views()) == len(loaded.holdout_views()) == 1
    np.testing.assert_allclose(loaded.views[0].rgb, rgba[..., :3] / 255, atol=1e-7)
    np.testing.assert_allclose(loaded.views[0].alpha, rgba[..., 3] / 255, atol=1e-7)
    np.testing.assert_array_equal(loaded.views[0].depth, 10)
    image.fromarray(rgba[:1]).save(tmp_path / "view_00.png")
    with pytest.raises(BundleError, match="image size"):
        load_bundle(tmp_path)


def test_unobserved_marker_is_valid_and_excluded() -> None:
    v = view()
    depth = v.depth.copy()
    depth[0, :] = -1.0
    marked = replace(v, depth=depth)
    _validate_bundle(bundle([marked, replace(v, holdout=True)]))
    assert not marked.observed[0].any() and marked.observed[1:].all()
    points, _ = unproject_depth(marked, min_alpha=0.0)
    assert points.shape[0] == int((depth > 0).sum())
