"""Stéréo rectifiée : matching épipolaire sparse et triangulation 3D (sans Qt)."""

from __future__ import annotations

from typing import Optional

import cv2 as cv
import numpy as np


def _prep_match_gray(bgr: np.ndarray) -> np.ndarray:
    """Niveaux de gris + CLAHE léger pour le template matching sous l'eau."""
    if bgr.ndim == 2:
        gray = bgr
    else:
        gray = cv.cvtColor(bgr, cv.COLOR_BGR2GRAY)
    clahe = cv.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    return clahe.apply(gray)


def ensure_stereo_rect_cache(
    mtx1, dist1, mtx2, dist2, R, T, image_size, cache: dict | None = None,
) -> dict:
    w, h = image_size
    if cache is not None and cache.get("size") == (w, h) and "map1x" in cache:
        return cache
    if cache is None:
        cache = {}
    R1, R2, P1, P2, Q, _, _ = cv.stereoRectify(
        mtx1, dist1, mtx2, dist2, (w, h), R, T, alpha=0)
    map1x, map1y = cv.initUndistortRectifyMap(
        mtx1, dist1, R1, P1, (w, h), cv.CV_32FC1)
    map2x, map2y = cv.initUndistortRectifyMap(
        mtx2, dist2, R2, P2, (w, h), cv.CV_32FC1)
    cache.clear()
    cache.update({
        "size": (w, h), "R1": R1, "R2": R2, "P1": P1, "P2": P2, "Q": Q,
        "map1x": map1x, "map1y": map1y, "map2x": map2x, "map2y": map2y,
    })
    return cache


def shift_image_vertical(img: np.ndarray, dy: float) -> np.ndarray:
    if abs(dy) < 0.01:
        return img
    M = np.float32([[1.0, 0.0, 0.0], [0.0, 1.0, float(dy)]])
    return cv.warpAffine(
        img, M, (img.shape[1], img.shape[0]),
        flags=cv.INTER_LINEAR, borderMode=cv.BORDER_CONSTANT)


def triangulate_rect_pixels(left_pt, right_pt, P1, P2) -> np.ndarray:
    p1 = np.array([[float(left_pt[0])], [float(left_pt[1])]], dtype=np.float64)
    p2 = np.array([[float(right_pt[0])], [float(right_pt[1])]], dtype=np.float64)
    hom = cv.triangulatePoints(P1, P2, p1, p2)
    w_h = float(hom[3, 0])
    if abs(w_h) < 1e-12:
        return np.zeros(3, dtype=np.float64)
    return (hom[:3, 0] / w_h).astype(np.float64)


def is_plausible_triangulation_mm(p3d, z_min=80.0, z_max=12000.0) -> bool:
    p = np.asarray(p3d, dtype=np.float64).ravel()[:3]
    if p.size < 3 or not np.all(np.isfinite(p)):
        return False
    z = float(p[2])
    if z < z_min or z > z_max:
        return False
    xy = float(np.linalg.norm(p[:2]))
    return xy <= z * 3.0


def template_match_score(
    rect_l: np.ndarray,
    rect_r: np.ndarray,
    x_left: float,
    y_left: float,
    x_right: float,
    y_right: float,
    *,
    patch_r: int = 10,
) -> float:
    h, w = rect_l.shape[:2]
    r = patch_r
    yi = int(np.clip(round(y_left), r, h - r - 1))
    xi_l = int(np.clip(round(x_left), r, w - r - 1))
    xi_r = int(np.clip(round(x_right), r, w - r - 1))
    if abs(yi - int(np.clip(round(y_right), r, h - r - 1))) > 2:
        return -1.0
    patch = rect_l[yi - r:yi + r, xi_l - r:xi_l + r]
    patch_r_img = rect_r[yi - r:yi + r, xi_r - r:xi_r + r]
    if patch.size == 0 or patch_r_img.shape != patch.shape:
        return -1.0
    return float(cv.matchTemplate(patch_r_img, patch, cv.TM_CCOEFF_NORMED)[0, 0])


def stereo_match_disparity_rect(
    rect_l: np.ndarray,
    rect_r: np.ndarray,
    x: float,
    y: float,
    *,
    max_shift: int = 360,
    patch_r: int = 10,
    min_score: float = 0.42,
    use_gray: bool = True,
) -> Optional[tuple[tuple[float, float], float]]:
    """Point gauche rectifié → correspondance droite + score."""
    img_l = _prep_match_gray(rect_l) if use_gray else rect_l
    img_r = _prep_match_gray(rect_r) if use_gray else rect_r
    h, w = img_l.shape[:2]
    r = patch_r
    yi = int(np.clip(round(y), r, h - r - 1))
    xi = int(np.clip(round(x), r, w - r - 1))
    patch = img_l[yi - r:yi + r, xi - r:xi + r]
    if patch.size == 0:
        return None
    best_dx, best_val = 0, -1.0
    for dx in range(-max_shift, 1):
        xr = xi + dx
        if xr < r or xr + r >= w:
            continue
        patch_r_img = img_r[yi - r:yi + r, xr - r:xr + r]
        if patch_r_img.shape != patch.shape:
            continue
        val = float(cv.matchTemplate(patch_r_img, patch, cv.TM_CCOEFF_NORMED)[0, 0])
        if val > best_val:
            best_val = val
            best_dx = dx
    if best_val < min_score:
        return None
    return (float(xi + best_dx), float(yi)), best_val


def bbox_pixels(bbox: dict) -> tuple[float, float, float, float]:
    if "x_min" in bbox:
        return (
            float(bbox["x_min"]), float(bbox["y_min"]),
            float(bbox["x_max"]), float(bbox["y_max"]),
        )
    return (
        float(bbox["x1"]), float(bbox["y1"]),
        float(bbox["x2"]), float(bbox["y2"]),
    )


def bbox_center(bbox: dict) -> tuple[float, float]:
    x1, y1, x2, y2 = bbox_pixels(bbox)
    return (x1 + x2) * 0.5, (y1 + y2) * 0.5


def match_right_box_epipolar(
    bbox_l: dict,
    right_boxes: list[dict],
    rect_l: np.ndarray,
    rect_r: np.ndarray,
    *,
    y_tol_px: float = 14.0,
    min_disp_px: float = 4.0,
    max_disp_px: float = 280.0,
    min_score: float = 0.38,
) -> Optional[tuple[tuple[float, float], tuple[float, float], float]]:
    """Associe une bbox gauche à une détection droite sur la même ligne épipolaire."""
    cx_l, cy_l = bbox_center(bbox_l)
    x1_l, y1_l, x2_l, y2_l = bbox_pixels(bbox_l)
    w_l = max(x2_l - x1_l, 1.0)
    h_l = max(y2_l - y1_l, 1.0)

    best: Optional[tuple[tuple[float, float], tuple[float, float], float]] = None
    for box in right_boxes:
        cx_r, cy_r = bbox_center(box)
        if abs(cy_r - cy_l) > y_tol_px:
            continue
        disp = cx_l - cx_r
        if disp < min_disp_px or disp > max_disp_px:
            continue
        x1_r, y1_r, x2_r, y2_r = bbox_pixels(box)
        w_r = max(x2_r - x1_r, 1.0)
        h_r = max(y2_r - y1_r, 1.0)
        size_sim = min(w_l, w_r) / max(w_l, w_r) * min(h_l, h_r) / max(h_l, h_r)
        tmpl = template_match_score(rect_l, rect_r, cx_l, cy_l, cx_r, cy_r)
        if tmpl < 0:
            continue
        score = 0.55 * tmpl + 0.45 * size_sim
        if score < min_score:
            continue
        if best is None or score > best[2]:
            best = ((cx_l, cy_l), (cx_r, cy_r), score)
    return best


def match_stereo_point(
    bbox_l: dict,
    rect_l: np.ndarray,
    rect_r: np.ndarray,
    right_boxes: list[dict] | None = None,
    *,
    y_tol_px: float = 14.0,
    min_score: float = 0.42,
) -> Optional[tuple[np.ndarray, float, str]]:
    """Retourne (pts 4D, score, method) ou None."""
    cx, cy = bbox_center(bbox_l)
    candidates: list[tuple[np.ndarray, float, str]] = []

    tmpl = stereo_match_disparity_rect(
        rect_l, rect_r, cx, cy, min_score=min_score)
    if tmpl is not None:
        (xr, yr), sc = tmpl
        candidates.append((np.array([cx, cy, xr, yr], dtype=np.float64), sc, "template"))

    if right_boxes:
        yolo = match_right_box_epipolar(
            bbox_l, right_boxes, rect_l, rect_r,
            y_tol_px=y_tol_px, min_score=min_score * 0.9)
        if yolo is not None:
            (xl, yl), (xr, yr), sc = yolo
            candidates.append(
                (np.array([xl, yl, xr, yr], dtype=np.float64), sc, "yolo_epipolar"))

    if not candidates:
        return None
    pts, score, method = max(candidates, key=lambda c: c[1])
    return pts, score, method


def project_bbox_to_right_rect(
    bbox_l: dict,
    rect_l: np.ndarray,
    rect_r: np.ndarray,
    *,
    min_score: float = 0.24,
    patch_r: int = 14,
) -> Optional[dict]:
    """Projette une bbox gauche (rectifiée) sur la vue droite via disparité médiane."""
    x1, y1, x2, y2 = bbox_pixels(bbox_l)
    cx, cy = bbox_center(bbox_l)
    w = max(x2 - x1, 1.0)
    h = max(y2 - y1, 1.0)
    if w >= h:
        sample_pts = (
            (cx, cy),
            (x1 + w * 0.25, cy),
            (x1 + w * 0.75, cy),
        )
    else:
        sample_pts = (
            (cx, cy),
            (cx, y1 + h * 0.25),
            (cx, y1 + h * 0.75),
        )

    disps: list[float] = []
    scores: list[float] = []
    for px, py in sample_pts:
        match = stereo_match_disparity_rect(
            rect_l, rect_r, px, py,
            min_score=min_score, patch_r=patch_r, max_shift=360)
        if match is None:
            continue
        (xr, _yr), score = match
        disps.append(px - xr)
        scores.append(score)
    if not disps:
        return None
    disp = float(np.median(disps))
    out = dict(bbox_l)
    out.update({
        "x1": x1 - disp,
        "y1": y1,
        "x2": x2 - disp,
        "y2": y2,
        "stereo_projected": True,
        "stereo_score": float(np.mean(scores)),
    })
    return out


def project_boxes_to_right_rect(
    boxes_l: list[dict],
    rect_l: np.ndarray,
    rect_r: np.ndarray,
    *,
    min_score: float = 0.24,
) -> list[dict]:
    """Liste de bbox gauches → bbox droite (même ordre ; échec = non dessiné)."""
    out: list[dict] = []
    for box in boxes_l:
        pr = project_bbox_to_right_rect(
            box, rect_l, rect_r, min_score=min_score)
        if pr is None:
            pr = dict(box)
            pr["stereo_projected"] = False
        out.append(pr)
    return out


def triangulate_bbox_rect_mm(
    rect_l: np.ndarray,
    rect_r: np.ndarray,
    bbox: dict,
    P1,
    P2,
    right_boxes: list[dict] | None = None,
    *,
    min_score: float = 0.42,
) -> Optional[tuple[float, float, float, float, str]]:
    """BBox en coords rectifiées → (x,y,z mm, score, method)."""
    matched = match_stereo_point(
        bbox, rect_l, rect_r, right_boxes, min_score=min_score)
    if matched is None:
        return None
    pts, score, method = matched
    p3d = triangulate_rect_pixels((pts[0], pts[1]), (pts[2], pts[3]), P1, P2)
    if not is_plausible_triangulation_mm(p3d):
        return None
    return float(p3d[0]), float(p3d[1]), float(p3d[2]), score, method
