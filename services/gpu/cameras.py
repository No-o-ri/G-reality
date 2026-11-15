"""Camera conventions and projection for captured views."""

from __future__ import annotations

import numpy as np

GL_TO_CV = np.diag([1.0, -1.0, -1.0, 1.0])


def mat4_from_column_major(values16: object) -> np.ndarray:
    return np.asarray(values16, dtype=np.float64).reshape(4, 4, order="F")


def mat4_to_column_major(m: np.ndarray) -> list[float]:
    return np.asarray(m, dtype=np.float64).reshape(16, order="F").tolist()


def world_to_cam_cv(camera_to_world_gl: np.ndarray) -> np.ndarray:
    return GL_TO_CV @ np.linalg.inv(camera_to_world_gl)


def camera_to_world_gl_from_cv(world_to_cam_cv: np.ndarray) -> np.ndarray:
    return np.linalg.inv(GL_TO_CV @ world_to_cam_cv)


def intrinsics(fx: float, fy: float, cx: float, cy: float) -> np.ndarray:
    return np.array([[fx, 0, cx], [0, fy, cy], [0, 0, 1]], dtype=np.float64)


def fov_to_focal(fov_rad: float, size_px: int) -> float:
    return float(size_px / 2 / np.tan(fov_rad / 2))


def look_at_gl(eye: object, target: object, up: object = (0, 1, 0)) -> np.ndarray:
    eye = np.asarray(eye, dtype=np.float64)
    backward = eye - np.asarray(target, dtype=np.float64)
    norm = np.linalg.norm(backward)
    if not np.isfinite(norm) or norm < 1e-12:
        raise ValueError("eye and target must define a view direction")
    backward /= norm
    right = np.cross(np.asarray(up, dtype=np.float64), backward)
    norm = np.linalg.norm(right)
    if not np.isfinite(norm) or norm < 1e-12:
        raise ValueError("up must not be parallel to the view direction")
    right /= norm
    result = np.eye(4)
    result[:3, :3] = np.column_stack((right, np.cross(backward, right), backward))
    result[:3, 3] = eye
    return result


def project_points(world_to_cam: np.ndarray, K: np.ndarray, points: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    camera = np.asarray(points) @ world_to_cam[:3, :3].T + world_to_cam[:3, 3]
    projected = camera @ K.T
    with np.errstate(divide="ignore", invalid="ignore"):
        uv = projected[:, :2] / projected[:, 2:3]
    return uv, camera[:, 2]
