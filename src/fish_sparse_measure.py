"""Mesure sparse longueur poisson — extrémités bbox + template épipolaire."""

from __future__ import annotations

from typing import Any, Optional

import numpy as np

from stereo_utils import (
    bbox_pixels,
    is_plausible_triangulation_mm,
    stereo_match_disparity_rect,
    triangulate_rect_pixels,
)


def bbox_length_endpoints(
    bbox: dict,
) -> tuple[tuple[float, float], tuple[float, float]]:
    """Deux points aux extrémités de la bbox (axe le plus long = longueur)."""
    x1, y1, x2, y2 = bbox_pixels(bbox)
    w = max(x2 - x1, 1.0)
    h = max(y2 - y1, 1.0)
    if w >= h:
        cy = (y1 + y2) * 0.5
        return (float(x1), float(cy)), (float(x2), float(cy))
    cx = (x1 + x2) * 0.5
    return (float(cx), float(y1)), (float(cx), float(y2))


def triangulate_sparse_point_mm(
    rect_l: np.ndarray,
    rect_r: np.ndarray,
    x: float,
    y: float,
    P1,
    P2,
    *,
    min_score: float = 0.38,
    patch_r: int = 8,
) -> Optional[tuple[float, float, float, float]]:
    """Point gauche rectifié → (x,y,z mm, score template) ou None."""
    match = stereo_match_disparity_rect(
        rect_l, rect_r, x, y, min_score=min_score, patch_r=patch_r)
    if match is None:
        return None
    (xr, yr), score = match
    p3d = triangulate_rect_pixels((x, y), (xr, yr), P1, P2)
    if not is_plausible_triangulation_mm(p3d):
        return None
    return float(p3d[0]), float(p3d[1]), float(p3d[2]), score


def measure_bbox_length_mm(
    rect_l: np.ndarray,
    rect_r: np.ndarray,
    bbox: dict,
    P1,
    P2,
    *,
    min_score: float = 0.38,
) -> Optional[dict[str, Any]]:
    """Longueur 3D (mm) entre les deux extrémités de la bbox."""
    p_a, p_b = bbox_length_endpoints(bbox)
    end_a = triangulate_sparse_point_mm(
        rect_l, rect_r, p_a[0], p_a[1], P1, P2, min_score=min_score)
    end_b = triangulate_sparse_point_mm(
        rect_l, rect_r, p_b[0], p_b[1], P1, P2, min_score=min_score)
    if end_a is None or end_b is None:
        return None
    va = np.array(end_a[:3], dtype=np.float64)
    vb = np.array(end_b[:3], dtype=np.float64)
    length_mm = float(np.linalg.norm(vb - va))
    if not np.isfinite(length_mm) or length_mm < 1.0:
        return None
    return {
        "length_mm": length_mm,
        "end_a_mm": end_a[:3],
        "end_b_mm": end_b[:3],
        "pixel_a": p_a,
        "pixel_b": p_b,
        "score_a": end_a[3],
        "score_b": end_b[3],
    }
