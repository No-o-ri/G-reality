"""Camera convention tests."""

from __future__ import annotations


import numpy as np
import pytest

from gpu.cameras import (
    camera_to_world_gl_from_cv, fov_to_focal, intrinsics, look_at_gl,
    mat4_from_column_major, mat4_to_column_major, project_points, world_to_cam_cv,
)


def test_random_pose_round_trip() -> None:
    rng = np.random.default_rng(4)
    for _ in range(30):
        pose = look_at_gl(rng.normal(size=3), rng.normal(size=3))
        np.testing.assert_allclose(camera_to_world_gl_from_cv(world_to_cam_cv(pose)), pose, atol=1e-12, rtol=0)


def test_projection_conventions() -> None:
    pose = look_at_gl((3, 4, 5), (3, 4, -5))
    points = np.array([[3, 4, -5], [3, 5, -5], [4, 4, -5]])
    uv, z = project_points(world_to_cam_cv(pose), intrinsics(100, 100, 128, 64), points)
    np.testing.assert_allclose(uv[0], [128, 64])
    np.testing.assert_allclose(z, 10)
    assert uv[1, 1] < 64
    assert uv[2, 0] > 128


def test_column_major_and_fov() -> None:
    pose = look_at_gl((2, 3, 4), (0, 0, 0))
    packed = mat4_to_column_major(pose)
    assert packed[12:15] == [2, 3, 4]
    np.testing.assert_array_equal(mat4_from_column_major(packed), pose)
    assert fov_to_focal(np.pi / 2, 256) == pytest.approx(128)


@pytest.mark.parametrize("eye,target,up", [((0, 0, 0), (0, 1, 0), (0, 1, 0)), ((0, 0, 0), (0, 0, 0), (0, 1, 0))])
def test_degenerate_look_at(eye: tuple, target: tuple, up: tuple) -> None:
    with pytest.raises(ValueError):
        look_at_gl(eye, target, up)
