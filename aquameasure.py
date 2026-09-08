import sys
import os
import re
import glob
import gc
import json
import shutil
import threading
import tempfile
import time
import traceback
import zipfile
from datetime import datetime, timezone
from typing import Optional

import cv2 as cv
import numpy as np
from scipy import linalg

try:
    from PyQt6.QtWidgets import (
        QApplication, QMainWindow, QTabWidget, QWidget,
        QVBoxLayout, QHBoxLayout, QPushButton, QTextEdit,
        QLabel, QSpinBox, QDoubleSpinBox, QFileDialog,
        QProgressBar, QSlider, QScrollArea, QGridLayout,
        QDialog, QSplitter, QComboBox, QGroupBox, QLineEdit, QInputDialog,
        QCheckBox, QFrame, QSizePolicy,
        QListWidget,
        QListWidgetItem,
        QDateTimeEdit,
    )
    from PyQt6.QtGui import QFont, QPixmap, QImage, QPainter, QPen, QBrush, QColor
    from PyQt6.QtCore import (
        QThread, QObject, pyqtSignal, Qt, QTimer, QSize, QDateTime, QRect, QPoint)
except ImportError:
    # La nouvelle interface est en PySide6. La distribution Windows n'embarque
    # donc qu'une seule copie de Qt ; les classes historiques restent
    # importables pour réutiliser le moteur de calibration.
    from PySide6.QtWidgets import (
        QApplication, QMainWindow, QTabWidget, QWidget,
        QVBoxLayout, QHBoxLayout, QPushButton, QTextEdit,
        QLabel, QSpinBox, QDoubleSpinBox, QFileDialog,
        QProgressBar, QSlider, QScrollArea, QGridLayout,
        QDialog, QSplitter, QComboBox, QGroupBox, QLineEdit, QInputDialog,
        QCheckBox, QFrame, QSizePolicy,
        QListWidget,
        QListWidgetItem,
        QDateTimeEdit,
    )
    from PySide6.QtGui import QFont, QPixmap, QImage, QPainter, QPen, QBrush, QColor
    from PySide6.QtCore import (
        QThread, QObject, Signal as pyqtSignal, Qt, QTimer, QSize, QDateTime,
        QRect, QPoint,
    )

try:
    import fish_detect as _fish_detect
    _FISH_DETECT_AVAILABLE = _fish_detect.is_available()
except ImportError:
    _fish_detect = None
    _FISH_DETECT_AVAILABLE = False

try:
    import fish_annotate as _fish_annotate
    _FISH_ANNOTATE_AVAILABLE = _fish_annotate.is_available()
except ImportError:
    _fish_annotate = None
    _FISH_ANNOTATE_AVAILABLE = False

# Phase 7 (nettoyage) : `fish_registry.py` (panneau « registre ») et
# `fish_database.py` (onglet « Base de données ») ont été supprimés du dépôt.
# C'étaient deux interfaces PyQt6 de l'ancienne application, remplacées par les
# pages Mesure et Données de l'application PySide6 - plus aucun code vivant ne
# les importait. Ce fichier reste, lui, importé à l'exécution par
# `aquameasure-pyside` pour ses fonctions de vision (calibration, flash,
# rectification, triangulation) ; seule son interface PyQt6 est morte.

try:
    import fish_track as _fish_track
    _FISH_TRACK_AVAILABLE = _fish_track.is_available()
except ImportError:
    _fish_track = None
    _FISH_TRACK_AVAILABLE = False

from stereo_utils import (
    project_boxes_to_right_rect as _project_boxes_to_right_rect,
    triangulate_bbox_rect_mm as _triangulate_bbox_rect_mm,
)

try:
    import fish_sparse_measure as _fish_sparse_measure
    _FISH_SPARSE_MEASURE_AVAILABLE = True
except ImportError:
    _fish_sparse_measure = None
    _FISH_SPARSE_MEASURE_AVAILABLE = False


# ── Heartbeat pour opérations OpenCV bloquantes ───────────────────────────────

# OpenCV calibrateCamera : DLT pose estimation exige ≥ 6 correspondances par vue.
MIN_CALIB_CORNERS = 6
# Appariement stéréo : recherche frame droite rf0 ± slack (meilleur nb d'IDs communs).
MAX_SYNC_SLACK_FRAMES = 2
# Seuils qualité stéréo (RMSE reprojection globale, pixels).
STEREO_RMSE_EXCELLENT_PX = 2.0
STEREO_RMSE_GOOD_PX = 5.0
STEREO_RMSE_WARN_PX = 10.0
MAX_STEREO_RMSE_PX = 12.0   # au-delà → rejet (mesure peu fiable)
MAX_MONO_RMSE_PX = 5.0      # au-delà → intrinsèque invalide (ne pas sauver mtx/dist)
MONO_PNP_OUTLIER_PX = 2.0   # élagage vues mono avant calibrateCamera (solvePnP)
STEREO_OUTLIER_SIGMA = 1.5    # élagage batch stéréo (per-view après stereoCalibrate)
STEREO_MIN_COMMON_CORNERS = 12  # pool stéréo : IDs ChArUco communs minimum (scan min = 6)
MIN_STEREO_PAIRS = 40         # minimum de paires pour une stéréo exploitable
MAX_RECT_DY_WARN_PX = 5.0     # |ΔY| rectifié au-delà → mesure peu fiable
MAX_RECT_DY_REJECT_PX = 15.0  # au-delà → R/T invalides (rejeter la calibration)

# Plage profondeur SGBM (mm) - défaut aquarium / mire proche ; réglable dans Mesure.
DEPTH_Z_NEAR_DEFAULT_MM = 500.0
DEPTH_Z_FAR_DEFAULT_MM = 4000.0
# Ancienne plage banc poisson lointain (préréglage optionnel).
DEPTH_Z_FISH_NEAR_MM = 1800.0
DEPTH_Z_FISH_FAR_MM = 6500.0

# Préréglages UI : (vues intrinsèque G/D, paires stéréo cible).
# Plus de vues = calibrateCamera / stereoCalibrate plus longs en HD.
CALIB_QUALITY_PRESETS: dict[str, tuple[int, int]] = {
    "Rapide (80 / 120)":           (80, 120),
    "Standard (120 / 160)":        (120, 160),
    "Haute précision (160 / 200)": (160, 200),
    "Maximum (200 / 250)":         (200, 250),
}

# calibrateCamera mono : durée indicative (pleine résolution, vues = paramètre UI).
_CALIB_SEC_PER_INTRINSIC_VIEW = 2.0   # s/vue HD (ordre de grandeur CPU récent)
_CALIB_SEC_PER_STEREO_PAIR = 0.15    # s/paire stéréo (FIX_INTRINSIC)


def _calib_duration_hints(n_intrinsic: int, n_stereo: int) -> dict[str, str]:
    """Fourchette réaliste pour les logs UI (calibrateCamera ×2 + stereoCalibrate)."""
    v = max(1, int(n_intrinsic))
    p = max(1, int(n_stereo))
    intr_sec_lo = max(15, int(v * _CALIB_SEC_PER_INTRINSIC_VIEW))
    intr_sec_hi = max(intr_sec_lo + 10, int(v * _CALIB_SEC_PER_INTRINSIC_VIEW * 1.4) + 5)
    stereo_sec_lo = max(10, int(p * _CALIB_SEC_PER_STEREO_PAIR))
    stereo_sec_hi = max(stereo_sec_lo + 15, int(p * _CALIB_SEC_PER_STEREO_PAIR * 1.5) + 10)
    total_lo = 2 * intr_sec_lo + stereo_sec_lo
    total_hi = 2 * intr_sec_hi + stereo_sec_hi + 20
    return {
        'intrinsic_per_cam': f"{intr_sec_lo}–{intr_sec_hi} s",
        'stereo': f"{stereo_sec_lo}–{stereo_sec_hi} s",
        'total': f"{total_lo // 60}–{(total_hi + 59) // 60} min",
    }

from app_paths import app_root

_APP_ROOT = app_root()


def cam_param(filename: str) -> str:
    """Chemin absolu vers un fichier dans camera_parameters/."""
    return os.path.join(_APP_ROOT, 'camera_parameters', filename)


def ensure_cam_params_dir() -> None:
    os.makedirs(os.path.join(_APP_ROOT, 'camera_parameters'), exist_ok=True)


CALIB_PROFILE_CLASSIC = 'classic'
CALIB_PROFILE_FAST_V2 = 'fast_v2'
CALIB_PROFILE_LABELS: dict[str, str] = {
    CALIB_PROFILE_CLASSIC: 'Classique',
    CALIB_PROFILE_FAST_V2: 'Rapide v2',
}


def load_active_calib_profile() -> str:
    path = cam_param('active_profile.txt')
    if os.path.isfile(path):
        try:
            with open(path, encoding='utf-8') as f:
                name = f.read().strip()
            if name in CALIB_PROFILE_LABELS:
                return name
        except OSError:
            pass
    return CALIB_PROFILE_CLASSIC


def save_active_calib_profile(profile: str) -> None:
    ensure_cam_params_dir()
    if profile not in CALIB_PROFILE_LABELS:
        profile = CALIB_PROFILE_CLASSIC
    with open(cam_param('active_profile.txt'), 'w', encoding='utf-8') as f:
        f.write(profile)


def calib_profile_dir(profile: str | None = None) -> str:
    if profile is None:
        profile = load_active_calib_profile()
    base = os.path.join(_APP_ROOT, 'camera_parameters')
    if profile == CALIB_PROFILE_CLASSIC:
        return base
    d = os.path.join(base, 'profiles', profile)
    os.makedirs(d, exist_ok=True)
    return d


def calib_param(filename: str, profile: str | None = None) -> str:
    return os.path.join(calib_profile_dir(profile), filename)


def calib_profile_complete(profile: str | None = None) -> bool:
    return calibration_package_complete(calib_profile_dir(profile))


def calib_profile_label(profile: str | None = None) -> str:
    if profile is None:
        profile = load_active_calib_profile()
    return CALIB_PROFILE_LABELS.get(profile, profile)


def save_videos_txt(left_video: str, right_video: str) -> None:
    """Ligne 1 = caméra gauche (bouton Sync), ligne 2 = caméra droite."""
    ensure_cam_params_dir()
    with open(cam_param('videos.txt'), 'w', encoding='utf-8') as f:
        f.write(left_video.strip() + '\n')
        f.write(right_video.strip() + '\n')


def load_videos_txt() -> tuple[str, str] | None:
    """Lit videos.txt - retourne (gauche, droite) selon l'assignation Sync."""
    path = cam_param('videos.txt')
    if not os.path.isfile(path):
        return None
    with open(path, encoding='utf-8') as f:
        lines = [l.strip() for l in f.readlines() if l.strip()]
    if len(lines) < 2:
        return None
    return lines[0], lines[1]


def _short_video_name(path: str, max_len: int = 48) -> str:
    name = os.path.basename(path)
    return name if len(name) <= max_len else ('…' + name[-(max_len - 1):])


def clear_trim_frames() -> None:
    """Supprime trim_frames.npy (bornes In/Out - liées à une vidéo précise)."""
    path = cam_param('trim_frames.npy')
    if os.path.isfile(path):
        os.remove(path)


def save_flash_roi(left_roi, right_roi) -> None:
    """Sauvegarde les ROI de détection du flash (coordonnées normalisées 0..1).

    Format : [lx, ly, lw, lh, rx, ry, rw, rh]. Une ROI absente est codée -1.
    """
    def pack(roi):
        if roi is None:
            return [-1.0, -1.0, -1.0, -1.0]
        return [float(roi[0]), float(roi[1]), float(roi[2]), float(roi[3])]
    ensure_cam_params_dir()
    np.save(cam_param('flash_roi.npy'),
            np.array(pack(left_roi) + pack(right_roi), dtype=float))


def load_flash_roi():
    """Retourne (left_roi, right_roi) en coords normalisées, ou (None, None)."""
    path = cam_param('flash_roi.npy')
    if not os.path.isfile(path):
        return None, None
    try:
        a = np.load(path).astype(float)
        if a.size < 8:
            return None, None

        def unpack(vals):
            x, y, w, h = vals
            if w <= 0 or h <= 0:
                return None
            return (float(x), float(y), float(w), float(h))
        return unpack(a[0:4]), unpack(a[4:8])
    except Exception:
        return None, None


def clear_flash_roi() -> None:
    path = cam_param('flash_roi.npy')
    if os.path.isfile(path):
        os.remove(path)


CALIB_PACKAGE_REQUIRED = (
    'mtx1.npy', 'dist1.npy', 'mtx2.npy', 'dist2.npy', 'R.npy', 'T.npy',
)
CALIB_PACKAGE_OPTIONAL = (
    'F.npy', 'stereo_rmse.npy', 'left_rmse.npy', 'right_rmse.npy', 'videos.txt', 'sync_frames.npy', 'trim_frames.npy',
)


def calibration_package_complete(dir_path: str) -> bool:
    return all(os.path.isfile(os.path.join(dir_path, name))
               for name in CALIB_PACKAGE_REQUIRED)


def _find_calib_payload_dir(root: str) -> str | None:
    if calibration_package_complete(root):
        return root
    try:
        for name in os.listdir(root):
            sub = os.path.join(root, name)
            if os.path.isdir(sub) and calibration_package_complete(sub):
                return sub
    except OSError:
        pass
    return None


def export_calibration_package(dest_zip: str) -> str | None:
    """Archive .zip de camera_parameters/ - retourne un message d'erreur ou None."""
    src = os.path.join(_APP_ROOT, 'camera_parameters')
    if not calibration_package_complete(src):
        return "Aucune calibration complète dans camera_parameters/ (mtx/R manquants)."
    try:
        manifest = {
            'format': 'aquameasure-calib-v1',
            'exported_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        }
        rmse_path = os.path.join(src, 'stereo_rmse.npy')
        if os.path.isfile(rmse_path):
            manifest['stereo_rmse'] = float(np.load(rmse_path))
        with zipfile.ZipFile(dest_zip, 'w', zipfile.ZIP_DEFLATED) as zf:
            for name in CALIB_PACKAGE_REQUIRED + CALIB_PACKAGE_OPTIONAL:
                path = os.path.join(src, name)
                if os.path.isfile(path):
                    zf.write(path, name)
            zf.writestr('manifest.json', json.dumps(manifest, indent=2))
        return None
    except OSError as exc:
        return f"Export impossible : {exc}"


def save_charuco_config(squares_x: int, squares_y: int,
                        square_length_mm: float, marker_length_mm: float,
                        aruco_dict_id: int,
                        profile: str | None = CALIB_PROFILE_CLASSIC) -> None:
    ensure_cam_params_dir()
    if profile is None:
        profile = CALIB_PROFILE_CLASSIC
    np.save(calib_param('charuco_config.npy', profile),
            np.array([squares_x, squares_y, square_length_mm,
                      marker_length_mm, float(aruco_dict_id)]))


def load_charuco_config() -> tuple[int, int, float, float, int]:
    path = cam_param('charuco_config.npy')
    if os.path.isfile(path):
        try:
            a = np.load(path)
            return (int(a[0]), int(a[1]), float(a[2]), float(a[3]), int(a[4]))
        except Exception:
            pass
    return 5, 7, 49.5, 37.0, cv.aruco.DICT_5X5_50


def _stereo_rectification_dy_stats(mtx1, dist1, mtx2, dist2, R, T, image_size,
                                   imgpoints_left, imgpoints_right):
    """|ΔY| après rectification sur paires stéréo (px) - doit être ≪ 5 px."""
    w, h = image_size
    R1, R2, P1, P2, _, _, _ = cv.stereoRectify(
        mtx1, dist1, mtx2, dist2, (w, h), R, T, alpha=0)
    dys = []
    for l, r in zip(imgpoints_left, imgpoints_right):
        c1 = np.asarray(l, dtype=np.float32).reshape(-1, 1, 2)
        c2 = np.asarray(r, dtype=np.float32).reshape(-1, 1, 2)
        if len(c1) < 4:
            continue
        p1 = cv.undistortPoints(c1, mtx1, dist1, R=R1, P=P1)
        p2 = cv.undistortPoints(c2, mtx2, dist2, R=R2, P=P2)
        dys.extend(np.abs(p1[:, 0, 1] - p2[:, 0, 1]).ravel().tolist())
    if not dys:
        return None
    arr = np.asarray(dys, dtype=np.float64)
    return float(np.median(arr)), float(np.percentile(arr, 95)), float(np.max(arr))


def _ensure_stereo_rect_cache(mtx1, dist1, mtx2, dist2, R, T,
                              image_size, cache: dict | None = None) -> dict:
    """Cache stereoRectify + maps remap pour affichage et triangulation."""
    w, h = image_size
    if cache is not None and cache.get('size') == (w, h) and 'map1x' in cache:
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
        'size': (w, h), 'R1': R1, 'R2': R2, 'P1': P1, 'P2': P2, 'Q': Q,
        'map1x': map1x, 'map1y': map1y, 'map2x': map2x, 'map2y': map2y,
    })
    return cache


def _triangulate_rect_pixels(left_pt, right_pt, P1, P2) -> np.ndarray:
    """Triangulation à partir de clics sur images déjà rectifiées."""
    p1 = np.array([[float(left_pt[0])], [float(left_pt[1])]], dtype=np.float64)
    p2 = np.array([[float(right_pt[0])], [float(right_pt[1])]], dtype=np.float64)
    hom = cv.triangulatePoints(P1, P2, p1, p2)
    w_h = float(hom[3, 0])
    if abs(w_h) < 1e-12:
        return np.zeros(3, dtype=np.float64)
    return (hom[:3, 0] / w_h).astype(np.float64)


def _shift_image_vertical(img: np.ndarray, dy: float) -> np.ndarray:
    """Décale une image verticalement de dy px (dy>0 = vers le bas)."""
    if abs(dy) < 0.01:
        return img
    M = np.float32([[1.0, 0.0, 0.0], [0.0, 1.0, float(dy)]])
    return cv.warpAffine(
        img, M, (img.shape[1], img.shape[0]),
        flags=cv.INTER_LINEAR, borderMode=cv.BORDER_CONSTANT)


def _estimate_vertical_offset(rect_left, rect_right):
    """ΔY médian (y_droite − y_gauche) du fond immobile entre deux vues rectifiées.

    ORB + ratio test + RANSAC fondamentale. Retourne (dy_median, n_inliers) ou None.
    Sert au recalage vertical quand la géométrie du rig a légèrement changé
    entre la calibration et le clip mesuré (translation verticale ≈ petite rotation)."""
    g1 = cv.cvtColor(rect_left, cv.COLOR_BGR2GRAY)
    g2 = cv.cvtColor(rect_right, cv.COLOR_BGR2GRAY)
    orb = cv.ORB_create(4000)
    k1, d1 = orb.detectAndCompute(g1, None)
    k2, d2 = orb.detectAndCompute(g2, None)
    if d1 is None or d2 is None or len(k1) < 20 or len(k2) < 20:
        return None
    bf = cv.BFMatcher(cv.NORM_HAMMING)
    raw = [m for m in bf.knnMatch(d1, d2, k=2) if len(m) == 2]
    good = [a for a, b in raw if a.distance < 0.75 * b.distance]
    if len(good) < 15:
        return None
    p1 = np.float32([k1[m.queryIdx].pt for m in good])
    p2 = np.float32([k2[m.trainIdx].pt for m in good])
    F, mask = cv.findFundamentalMat(p1, p2, cv.FM_RANSAC, 2.0)
    if mask is None:
        return None
    inl = mask.ravel().astype(bool)
    if int(inl.sum()) < 12:
        return None
    dys = p2[inl, 1] - p1[inl, 1]
    return float(np.median(dys)), int(inl.sum())


def _stereo_match_disparity_rect(
    rect_l: np.ndarray,
    rect_r: np.ndarray,
    x: float,
    y: float,
    *,
    max_shift: int = 280,
) -> Optional[tuple[float, float]]:
    """Cherche la correspondance droite d'un point gauche (images rectifiées)."""
    h, w = rect_l.shape[:2]
    r = 10
    yi = int(np.clip(round(y), r, h - r - 1))
    xi = int(np.clip(round(x), r, w - r - 1))
    patch = rect_l[yi - r:yi + r, xi - r:xi + r]
    if patch.size == 0:
        return None
    best_dx, best_val = 0, -1.0
    for dx in range(-max_shift, 12):
        xr = xi + dx
        if xr < r or xr + r >= w:
            continue
        patch_r = rect_r[yi - r:yi + r, xr - r:xr + r]
        if patch_r.shape != patch.shape:
            continue
        val = float(cv.matchTemplate(patch_r, patch, cv.TM_CCOEFF_NORMED)[0, 0])
        if val > best_val:
            best_val = val
            best_dx = dx
    if best_val < 0.42:
        return None
    return float(xi + best_dx), float(yi)


def triangulate_bbox_center_mm(
    frame_left: np.ndarray,
    frame_right: np.ndarray,
    bbox: dict,
    mtx1, dist1, mtx2, dist2, R, T,
    *,
    right_dy: float = 0.0,
    cache: dict | None = None,
    right_boxes: list | None = None,
) -> Optional[tuple[float, float, float]]:
    """Position 3D (mm) du centre d'une bbox gauche (coords rectifiées)."""
    if frame_left is None or frame_right is None:
        return None
    img_sz = (frame_left.shape[1], frame_left.shape[0])
    cache = _ensure_stereo_rect_cache(mtx1, dist1, mtx2, dist2, R, T, img_sz, cache or {})
    rect_l = cv.remap(frame_left, cache["map1x"], cache["map1y"], cv.INTER_LINEAR)
    rect_r = cv.remap(frame_right, cache["map2x"], cache["map2y"], cv.INTER_LINEAR)
    if abs(right_dy) > 0.01:
        rect_r = _shift_image_vertical(rect_r, right_dy)
    out = _triangulate_bbox_rect_mm(
        rect_l, rect_r, bbox, cache["P1"], cache["P2"], right_boxes)
    if out is None:
        return None
    return out[0], out[1], out[2]


def _triangulate_rectified_mm(left_pt, right_pt,
                              mtx1, dist1, mtx2, dist2, R, T,
                              image_size, cache: dict | None = None):
    """Triangulation stéréo rectifiée (mm) - cohérente avec stereoCalibrate."""
    cache = _ensure_stereo_rect_cache(
        mtx1, dist1, mtx2, dist2, R, T, image_size, cache)
    p1 = cv.undistortPoints(
        np.array([[left_pt]], dtype=np.float32),
        mtx1, dist1, R=cache['R1'], P=cache['P1'])
    p2 = cv.undistortPoints(
        np.array([[right_pt]], dtype=np.float32),
        mtx2, dist2, R=cache['R2'], P=cache['P2'])
    hom = cv.triangulatePoints(
        cache['P1'], cache['P2'],
        p1.reshape(2, 1).astype(np.float64),
        p2.reshape(2, 1).astype(np.float64))
    w_h = float(hom[3, 0])
    if abs(w_h) < 1e-12:
        return np.zeros(3, dtype=np.float64), cache
    return (hom[:3, 0] / w_h).astype(np.float64), cache


def import_calibration_package(src_zip: str) -> str | None:
    """Importe une archive .zip vers camera_parameters/ - retourne erreur ou None."""
    if not os.path.isfile(src_zip):
        return "Fichier introuvable."
    try:
        with tempfile.TemporaryDirectory() as tmp:
            with zipfile.ZipFile(src_zip, 'r') as zf:
                zf.extractall(tmp)
            payload = _find_calib_payload_dir(tmp)
            if payload is None:
                return "Archive invalide (fichiers mtx/R manquants)."
            ensure_cam_params_dir()
            dest = os.path.join(_APP_ROOT, 'camera_parameters')
            backup = os.path.join(dest, '_calib_import_backup')
            if calibration_package_complete(dest):
                if os.path.isdir(backup):
                    shutil.rmtree(backup)
                shutil.copytree(dest, backup, ignore=shutil.ignore_patterns(
                    '_calib_import_backup', '_calib_run_backup', 'verify'))
            for name in os.listdir(payload):
                if not (name.endswith('.npy') or name.endswith('.txt')
                        or name == 'manifest.json'):
                    continue
                shutil.copy2(os.path.join(payload, name), os.path.join(dest, name))
        return None
    except (OSError, zipfile.BadZipFile) as exc:
        return f"Import impossible : {exc}"


def _n_calib_points(obj_pts) -> int:
    if obj_pts is None:
        return 0
    return int(np.asarray(obj_pts).reshape(-1, 3).shape[0])


def _filter_mono_calib_sets(objpoints, imgpoints, detected_info, min_corners, log_fn):
    """Retire les vues avec trop peu de coins (échec DLT dans calibrateCamera)."""
    obj_f, img_f, info_f = [], [], []
    skipped = 0
    infos = detected_info if detected_info is not None else [None] * len(objpoints)
    for op, ip, info in zip(objpoints, imgpoints, infos):
        if _n_calib_points(op) >= min_corners:
            obj_f.append(op)
            img_f.append(ip)
            if detected_info is not None:
                info_f.append(info)
        else:
            skipped += 1
    if skipped:
        log_fn(
            f"  ⚠ {skipped} vue(s) ignorée(s) : < {min_corners} coins ChArUco "
            f"(OpenCV exige ≥ {min_corners} par vue).")
    return obj_f, img_f, (info_f if detected_info is not None else None)


def _mono_view_pnp_rmse(obj_pts, img_pts, image_size) -> float:
    """RMSE solvePnP par vue - détecte les poses/coins aberrants avant calibrateCamera."""
    w, h = image_size
    op = np.asarray(obj_pts, dtype=np.float64).reshape(-1, 3)
    ip = np.asarray(img_pts, dtype=np.float64).reshape(-1, 2)
    if len(op) < MIN_CALIB_CORNERS:
        return float('inf')
    mtx, dist = _default_intrinsics(w, h)
    ok, rvec, tvec = cv.solvePnP(
        op, ip, mtx, dist, flags=cv.SOLVEPNP_ITERATIVE)
    if not ok:
        return float('inf')
    proj, _ = cv.projectPoints(op, rvec, tvec, mtx, dist)
    diff = ip - proj.reshape(-1, 2)
    return float(np.sqrt(np.mean(np.sum(diff * diff, axis=1))))


def _filter_mono_pnp_outliers(objpoints, imgpoints, detected_info, image_size,
                              log_fn, max_rmse_px: float = MONO_PNP_OUTLIER_PX):
    """Retire les vues dont solvePnP diverge - sinon calibrateCamera explose (RMSE 1e16)."""
    obj_f, img_f, info_f = [], [], []
    skipped = 0
    infos = detected_info if detected_info is not None else [None] * len(objpoints)
    for op, ip, info in zip(objpoints, imgpoints, infos):
        rmse = _mono_view_pnp_rmse(op, ip, image_size)
        if not np.isfinite(rmse) or rmse > max_rmse_px:
            skipped += 1
            continue
        obj_f.append(op)
        img_f.append(ip)
        if detected_info is not None:
            info_f.append(info)
    if skipped:
        log_fn(
            f"  Élagage PnP mono : {skipped} vue(s) retirée(s) "
            f"(RMSE > {max_rmse_px} px - souvent 6 coins dégénérés / mire tronquée).")
    return obj_f, img_f, (info_f if detected_info is not None else None)


def _filter_cache_entries(cache, min_corners, log_fn, label=""):
    """Filtre un cache de détection scan (entrées avec obj_pts)."""
    kept = [e for e in cache if _n_calib_points(e.get('obj_pts')) >= min_corners]
    dropped = len(cache) - len(kept)
    if dropped:
        suffix = f" ({label})" if label else ""
        log_fn(
            f"  ⚠ {dropped} détection(s) ignorée(s){suffix} : "
            f"< {min_corners} coins (seuil calibrateCamera).")
    return kept


def _log_exception(label, log_fn):
    tb = traceback.format_exc()
    log_fn(f"  [ERREUR {label}]\n{tb}")
    return tb


def _validate_calib_points(objpoints, imgpoints, log_fn):
    """Vérifie les correspondances 3D/2D avant calibrateCamera."""
    n_corners = 0
    for i, (op, ip) in enumerate(zip(objpoints, imgpoints)):
        op = np.asarray(op, dtype=np.float32)
        ip = np.asarray(ip, dtype=np.float32)
        if op.ndim != 3 or op.shape[2] != 3:
            raise ValueError(
                f"Vue {i}: objpoints shape invalide {op.shape} (attendu N×1×3)")
        if ip.ndim != 3 or ip.shape[2] != 2:
            raise ValueError(
                f"Vue {i}: imgpoints shape invalide {ip.shape} (attendu N×1×2)")
        if len(op) != len(ip):
            raise ValueError(
                f"Vue {i}: {len(op)} points 3D vs {len(ip)} points 2D")
        if not np.all(np.isfinite(op)) or not np.all(np.isfinite(ip)):
            raise ValueError(f"Vue {i}: NaN ou Inf dans les points")
        if len(op) < MIN_CALIB_CORNERS:
            raise ValueError(
                f"Vue {i}: seulement {len(op)} coins "
                f"(minimum {MIN_CALIB_CORNERS} pour calibrateCamera)")
        n_corners += len(op)
    log_fn(
        f"  Contrôle données : {len(objpoints)} vues, "
        f"{n_corners} correspondances 3D/2D au total "
        f"(≥ {MIN_CALIB_CORNERS} coins/vue) - OK")


def _prepare_calib_buffers(objpoints, imgpoints):
    """OpenCV exige Point3f/Point2f → float32 contigu N×1×3 et N×1×2."""
    op_out, ip_out = [], []
    for o, i in zip(objpoints, imgpoints):
        o = np.ascontiguousarray(np.asarray(o, dtype=np.float32).reshape(-1, 1, 3))
        i = np.ascontiguousarray(np.asarray(i, dtype=np.float32).reshape(-1, 1, 2))
        op_out.append(o)
        ip_out.append(i)
    return op_out, ip_out


def _default_intrinsics(w: int, h: int):
    """Matrice intrinsèque initiale (focale ≈ largeur image)."""
    f = float(max(w, h))
    mtx = np.array([[f, 0, w / 2.0],
                    [0, f, h / 2.0],
                    [0, 0, 1]], dtype=np.float64)
    dist = np.zeros(5, dtype=np.float64)
    return mtx, dist


def _validate_intrinsics(mtx, dist, image_size, label: str) -> str | None:
    """Retourne un message d'erreur si mtx/dist sont aberrants."""
    w, h = image_size
    m = np.asarray(mtx, dtype=np.float64)
    d = np.asarray(dist, dtype=np.float64).ravel()
    if m.shape != (3, 3) or not np.all(np.isfinite(m)) or not np.all(np.isfinite(d)):
        return f"{label} : mtx/dist non finis ou forme invalide."
    fx, fy = float(m[0, 0]), float(m[1, 1])
    lo, hi = 0.25 * min(w, h), 4.0 * max(w, h)
    if not (lo < fx < hi and lo < fy < hi):
        return (f"{label} : focale hors plage ({fx:.1f}, {fy:.1f} px) "
                f"- attendu ~{lo:.0f}–{hi:.0f}.")
    if np.max(np.abs(d)) > 50.0:
        return f"{label} : coefficients de distortion aberrants (max |k| > 50)."
    return None


def _mono_calib_ok(rmse: float, mtx, dist, image_size, label: str,
                   log_fn) -> bool:
    """Vérifie RMSE + intrinsèques avant checkpoint mono."""
    if not np.isfinite(rmse) or rmse > MAX_MONO_RMSE_PX:
        log_fn(
            f"  ✗ {label} invalide : RMSE {rmse:.4f} px "
            f"(seuil {MAX_MONO_RMSE_PX} px).")
        return False
    err = _validate_intrinsics(mtx, dist, image_size, label)
    if err:
        log_fn(f"  ✗ {err}")
        return False
    return True


def _frame_num_from_path(path: str) -> int:
    m = re.search(r'frame_(\d+)', os.path.basename(path))
    return int(m.group(1)) if m else -1


def _count_common_charuco_ids(left_e, right_e) -> int:
    ids1 = left_e['ids'].flatten()
    ids2 = right_e['ids'].flatten()
    return int(len(np.intersect1d(ids1, ids2)))


def _pair_stereo_caches(left_cache, right_cache, left_sync: int, right_sync: int,
                        slack: int = MAX_SYNC_SLACK_FRAMES):
    """Apparie gauche/droite par instant sync (rf0 ± slack, max IDs ChArUco communs)."""
    right_by_frame = {_frame_num_from_path(e['path']): e for e in right_cache}
    paired = []
    for le in left_cache:
        lf = _frame_num_from_path(le['path'])
        if lf < 0:
            continue
        rf0 = lf - left_sync + right_sync
        best_re = None
        best_common = 0
        best_abs_delta = slack + 1
        for delta in range(-slack, slack + 1):
            re = right_by_frame.get(rf0 + delta)
            if re is None:
                continue
            common = _count_common_charuco_ids(le, re)
            if common < MIN_CALIB_CORNERS:
                continue
            abs_delta = abs(delta)
            if (best_re is None or common > best_common
                    or (common == best_common and abs_delta < best_abs_delta)):
                best_re = re
                best_common = common
                best_abs_delta = abs_delta
        if best_re is not None:
            paired.append((le, best_re))
    return paired


def _pair_epipolar_error(pts_left, pts_right):
    """Erreur épipolaire moyenne d'une paire (F estimé sur la paire, FM_8POINT)."""
    pl = np.asarray(pts_left, dtype=np.float64).reshape(-1, 2)
    pr = np.asarray(pts_right, dtype=np.float64).reshape(-1, 2)
    if len(pl) < 8 or len(pr) < 8 or len(pl) != len(pr):
        return None
    F, _ = cv.findFundamentalMat(pl, pr, cv.FM_8POINT)
    if F is None or F.shape != (3, 3):
        return None
    pl_h = np.hstack([pl, np.ones((len(pl), 1))])
    pr_h = np.hstack([pr, np.ones((len(pr), 1))])
    # Contrainte épipolaire : pr · F · pl ≈ 0
    errs = np.abs(np.einsum('ij,jk,ik->i', pr_h, F, pl_h))
    return float(np.mean(errs))


def _prune_stereo_outliers(objpoints, imgpoints_left, imgpoints_right, log_fn,
                           sigma=2.5, min_keep=10):
    """Désactivé avant stereoCalibrate - l'épipolaire par paire (F local) est trompeur
    et l'élagage batch per-view dans _stereo_calibrate_robust suffit."""
    _ = sigma, min_keep
    log_fn(
        "  Élagage pré-stéréo : ignoré (passe robuste per-view après stereoCalibrate)")
    return objpoints, imgpoints_left, imgpoints_right, 0


def _stereo_points_from_pair(left_e, right_e, board):
    """Coins communs (mêmes IDs ChArUco) pour une paire stéréo."""
    ids1 = left_e['ids'].flatten()
    ids2 = right_e['ids'].flatten()
    c1 = left_e['corners']
    c2 = right_e['corners']
    common = np.intersect1d(ids1, ids2)
    if len(common) < MIN_CALIB_CORNERS:
        return None
    idx1 = np.where(np.isin(ids1, common))[0]
    idx2 = np.where(np.isin(ids2, common))[0]
    sort1 = np.argsort(ids1[idx1])
    sort2 = np.argsort(ids2[idx2])
    matched_c1 = c1[idx1[sort1]]
    matched_c2 = c2[idx2[sort2]]
    matched_ids = common.reshape(-1, 1).astype(np.int32)
    obj_pts, _ = board.matchImagePoints(matched_c1, matched_ids)
    if obj_pts is None or _n_calib_points(obj_pts) < MIN_CALIB_CORNERS:
        return None
    return obj_pts, matched_c1, matched_c2


def _subsample_calib_views(objpoints, imgpoints, detected_info, n_max: int, log_fn):
    """Plafonne les vues passées à calibrateCamera (gain vitesse, RMSE ChArUco stable)."""
    if len(objpoints) <= n_max:
        return objpoints, imgpoints, detected_info
    step = len(objpoints) / n_max
    sel = [int(i * step) for i in range(n_max)]
    log_fn(
        f"  calibrateCamera : sous-échantillon {len(objpoints)} → {n_max} vues "
        f"(limite « vues max » du paramétrage)")
    obj_s = [objpoints[i] for i in sel]
    img_s = [imgpoints[i] for i in sel]
    if detected_info is not None:
        info_s = [detected_info[i] for i in sel]
    else:
        info_s = None
    return obj_s, img_s, info_s


def _downscale_imgpoints_for_calib(objpoints, imgpoints, w, h, scale: float):
    """Demi-résolution pour calibrateCamera - LM ~4× plus rapide, mtx rescalée après."""
    if scale >= 0.999:
        return objpoints, imgpoints, (w, h), 1.0
    sw = max(64, int(round(w * scale)))
    sh = max(64, int(round(h * scale)))
    ip_out = []
    for ip in imgpoints:
        arr = np.ascontiguousarray(
            np.asarray(ip, dtype=np.float32).reshape(-1, 1, 2) * scale)
        ip_out.append(arr)
    return objpoints, ip_out, (sw, sh), scale


def _upscale_intrinsics(mtx, scale: float):
    """Ramène fx,fy,cx,cy à la résolution native après calib en demi-résolution."""
    if scale >= 0.999:
        return mtx
    m = np.asarray(mtx, dtype=np.float64).copy()
    inv = 1.0 / scale
    m[0, 0] *= inv
    m[1, 1] *= inv
    m[0, 2] *= inv
    m[1, 2] *= inv
    return m


def _run_calibrate_camera(op, ip, image_size, log_fn):
    """Appel calibrateCamera avec types et flags corrects."""
    w, h = image_size
    mtx, dist = _default_intrinsics(w, h)
    flags = (cv.CALIB_USE_INTRINSIC_GUESS
             | cv.CALIB_FIX_K3
             | cv.CALIB_FIX_K4
             | cv.CALIB_ZERO_TANGENT_DIST
             | cv.CALIB_FIX_ASPECT_RATIO)
    criteria = (cv.TERM_CRITERIA_EPS + cv.TERM_CRITERIA_COUNT, 30, 1e-5)
    n_threads = max(1, min(8, (os.cpu_count() or 4)))
    try:
        cv.setNumThreads(n_threads)
    except Exception:
        pass
    os.environ.setdefault('OMP_NUM_THREADS', str(n_threads))
    return cv.calibrateCamera(
        op, ip, (w, h), mtx, dist, flags=flags, criteria=criteria)


def _run_opencv_blocking(label, fn, log_fn, progress_fn=None, prog_val=None,
                         interval_s=5.0, live_fn=None, busy_fn=None):
    """OpenCV bloquant dans un thread ; logs + live_fn toutes les interval_s."""
    result = [None]
    exc = [None]

    def _target():
        try:
            result[0] = fn()
        except Exception as e:
            exc[0] = e

    if busy_fn:
        busy_fn(True)
    log_fn(f"  → Démarrage {label} (OpenCV ne fournit pas de % d'avancement)…")
    if live_fn:
        live_fn(f"{label} - démarrage…")

    work = threading.Thread(target=_target, daemon=True)
    work.start()
    t0 = time.monotonic()
    while work.is_alive():
        elapsed = int(time.monotonic() - t0)
        msg = f"  … {label} en cours ({elapsed} s) - calcul en cours, pas un crash"
        log_fn(msg)
        if live_fn:
            if elapsed >= 60:
                live_fn(
                    f"⏳ {label} - {elapsed // 60} min {elapsed % 60}s "
                    f"(OpenCV calcule en silence, soyez patient)")
            else:
                live_fn(f"⏳ {label} - {elapsed}s (OpenCV calcule en silence)")
        if progress_fn is not None and prog_val is not None:
            progress_fn(prog_val)
        work.join(timeout=interval_s)

    if busy_fn:
        busy_fn(False)
    if exc[0] is not None:
        raise exc[0]
    return result[0]


# ── Utilitaire zoom ───────────────────────────────────────────────────────────

def get_zoomed_patch(image: np.ndarray, x: int, y: int,
                     patch_size: int = 75, display_size: int = 300) -> np.ndarray:
    h, w = image.shape[:2]
    x1 = max(0, x - patch_size // 2)
    y1 = max(0, y - patch_size // 2)
    x2 = min(w, x + patch_size // 2)
    y2 = min(h, y + patch_size // 2)
    patch  = image[y1:y2, x1:x2]
    zoomed = cv.resize(patch, (display_size, display_size),
                       interpolation=cv.INTER_LINEAR)
    cx, cy = display_size // 2, display_size // 2
    cv.line(zoomed, (cx - 15, cy), (cx + 15, cy), (0, 255, 0), 1)
    cv.line(zoomed, (cx, cy - 15), (cx, cy + 15), (0, 255, 0), 1)
    return zoomed


# ── Fonctions de calibration ───────────────────────────────────────────────────

def DLT(P1, P2, point1, point2):
    A = [point1[1] * P1[2, :] - P1[1, :],
         P1[0, :] - point1[0] * P1[2, :],
         point2[1] * P2[2, :] - P2[1, :],
         P2[0, :] - point2[0] * P2[2, :]]
    A = np.array(A).reshape((4, 4))
    B = A.transpose() @ A
    U, s, Vh = linalg.svd(B, full_matrices=False)
    return Vh[3, 0:3] / Vh[3, 3]


def _undistort_pixel(pt, mtx, dist):
    """Clic sur image déformée → pixel corrigé (pinhole, même grille)."""
    arr = np.array([[[float(pt[0]), float(pt[1])]]], dtype=np.float64)
    out = cv.undistortPoints(arr, mtx, dist, P=mtx)
    return [float(out[0, 0, 0]), float(out[0, 0, 1])]


def _triangulate_undistorted_pair(u1, u2, mtx1, mtx2, R, T):
    """Triangulation à partir de pixels déjà dé-distordus."""
    P1 = mtx1 @ np.concatenate([np.eye(3), np.zeros((3, 1))], axis=-1)
    P2 = mtx2 @ np.concatenate([R, T], axis=-1)
    hom = cv.triangulatePoints(
        P1, P2,
        np.array(u1, dtype=np.float64).reshape(2, 1),
        np.array(u2, dtype=np.float64).reshape(2, 1))
    w = float(hom[3, 0])
    if abs(w) < 1e-12:
        return DLT(P1, P2, u1, u2)
    return (hom[:3, 0] / w).astype(np.float64)


def _epipolar_error_px(left_pt, right_pt, F, mtx1, dist1, mtx2, dist2) -> float:
    """Distance du clic droit à la ligne épilolaire (px, images dé-distordues)."""
    u1 = np.array([*_undistort_pixel(left_pt, mtx1, dist1), 1.0], dtype=np.float64)
    u2 = np.array([*_undistort_pixel(right_pt, mtx2, dist2), 1.0], dtype=np.float64)
    l = np.asarray(F, dtype=np.float64) @ u1
    a, b, c = float(l[0]), float(l[1]), float(l[2])
    denom = np.sqrt(a * a + b * b)
    if denom < 1e-12:
        return 0.0
    return float(abs(u2 @ l) / denom)


def _snap_right_to_epipolar(left_pt, right_pt, F, mtx1, dist1, mtx2, dist2):
    """Projette le clic droit sur la ligne épilolaire du clic gauche (undistorted)."""
    u1 = _undistort_pixel(left_pt, mtx1, dist1)
    u2 = _undistort_pixel(right_pt, mtx2, dist2)
    p1 = np.array([u1[0], u1[1], 1.0], dtype=np.float64)
    l = np.asarray(F, dtype=np.float64) @ p1
    a, b, c = float(l[0]), float(l[1]), float(l[2])
    denom = a * a + b * b
    if denom < 1e-12:
        return u2, 0.0
    err = abs(a * u2[0] + b * u2[1] + c) / np.sqrt(denom)
    d = (a * u2[0] + b * u2[1] + c) / denom
    return [u2[0] - a * d, u2[1] - b * d], err


def triangulate_stereo_mm(left_pt, right_pt, mtx1, dist1, mtx2, dist2, R, T,
                          F=None, snap_epipolar: bool = True,
                          image_size=None, rect_cache: dict | None = None):
    """Triangulation 3D (mm) - rectifiée si image_size fourni."""
    if image_size is not None:
        cache = rect_cache if rect_cache is not None else {}
        p3d, _ = _triangulate_rectified_mm(
            left_pt, right_pt, mtx1, dist1, mtx2, dist2, R, T,
            image_size, cache)
        if rect_cache is not None:
            rect_cache.clear()
            rect_cache.update(cache)
        return p3d, 0.0
    u1 = _undistort_pixel(left_pt, mtx1, dist1)
    u2 = _undistort_pixel(right_pt, mtx2, dist2)
    epi_err = 0.0
    if F is not None and snap_epipolar:
        u2, epi_err = _snap_right_to_epipolar(
            left_pt, right_pt, F, mtx1, dist1, mtx2, dist2)
    p3d = _triangulate_undistorted_pair(u1, u2, mtx1, mtx2, R, T)
    return p3d, epi_err


def _is_plausible_triangulation_mm(p3d, z_min=80.0, z_max=12000.0) -> bool:
    """Rejette les triangulations dégénérées (sync foireuse, épipolaire cassée)."""
    p = np.asarray(p3d, dtype=np.float64).ravel()[:3]
    if p.size < 3 or not np.all(np.isfinite(p)):
        return False
    z = float(p[2])
    if z < z_min or z > z_max:
        return False
    xy = float(np.linalg.norm(p[:2]))
    return xy <= z * 3.0


def _max_fish_length_mm() -> float:
    return 5000.0


def _stereo_baseline_mm(T) -> float:
    n = float(np.linalg.norm(np.asarray(T, dtype=np.float64).ravel()))
    return n if n > 1.0 else float(abs(T[0, 0]))


# Baseline physique typique du banc (mm) - contrôle visuel après stereoCalibrate.
EXPECTED_BASELINE_MM = 800.0
BASELINE_OK_MIN_MM = 600.0
BASELINE_OK_MAX_MM = 1000.0


def _stereo_extrinsics_summary(R, T, rmse_stereo=None, rmse_left=None, rmse_right=None):
    """Texte lisible R/T + baseline pour logs et UI."""
    Rm = np.asarray(R, dtype=np.float64).reshape(3, 3)
    Tv = np.asarray(T, dtype=np.float64).reshape(-1)
    baseline = _stereo_baseline_mm(Tv)
    if BASELINE_OK_MIN_MM <= baseline <= BASELINE_OK_MAX_MM:
        b_tag = "OK"
    elif 300 <= baseline <= 1500:
        b_tag = "?"
    else:
        b_tag = "ANORMAL"
    lines = [
        f"Baseline ‖T‖ : {baseline:.1f} mm  (attendu ≈ {EXPECTED_BASELINE_MM:.0f} mm) [{b_tag}]",
        f"T (mm) : Tx={Tv[0]:+.2f}  Ty={Tv[1]:+.2f}  Tz={Tv[2]:+.2f}",
        "R :",
        f"  [{Rm[0,0]:8.5f} {Rm[0,1]:8.5f} {Rm[0,2]:8.5f}]",
        f"  [{Rm[1,0]:8.5f} {Rm[1,1]:8.5f} {Rm[1,2]:8.5f}]",
        f"  [{Rm[2,0]:8.5f} {Rm[2,1]:8.5f} {Rm[2,2]:8.5f}]",
    ]
    if rmse_left is not None:
        lines.append(f"RMSE intrinsèque G : {rmse_left:.3f} px")
    if rmse_right is not None:
        lines.append(f"RMSE intrinsèque D : {rmse_right:.3f} px")
    if rmse_stereo is not None:
        lines.append(
            f"RMSE stéréo : {rmse_stereo:.3f} px ({_stereo_rmse_quality_label(rmse_stereo)})")
    return "\n".join(lines), baseline, b_tag


def _expected_disparity_px(mtx1, T, depth_mm: float) -> float:
    """Z(mm) → disparité (px) : d ≈ f × B / Z."""
    f = float((mtx1[0, 0] + mtx1[1, 1]) * 0.5)
    B = _stereo_baseline_mm(T)
    return f * B / max(depth_mm, 1.0)


def _focal_from_projection(P) -> float:
    return float((P[0, 0] + P[1, 1]) * 0.5)


def _expected_disparity_px_rect(P1, T, depth_mm: float) -> float:
    """Disparité attendue sur paires rectifiées (focale de P1)."""
    f = _focal_from_projection(P1)
    B = _stereo_baseline_mm(T)
    return f * B / max(depth_mm, 1.0)


def _disparity_search_range(mtx1, T, image_width: int,
                            z_near_mm: float, z_far_mm: float,
                            P1=None):
    """minDisparity + numDisparities pour couvrir [z_near, z_far] sans tout scanner depuis 0.

    Avec minDisparity=0 et une grande numDisparities, SGBM invalide toute la bande
    gauche (~numDisparities px) - d'où une depth map « à moitié vide » en HD.
    """
    w = max(int(image_width), 64)
    z_lo = max(200.0, min(z_near_mm, z_far_mm))
    z_hi = max(z_lo + 1.0, max(z_near_mm, z_far_mm))
    if P1 is not None:
        d_close = _expected_disparity_px_rect(P1, T, z_lo)
        d_far = _expected_disparity_px_rect(P1, T, z_hi)
    else:
        d_close = _expected_disparity_px(mtx1, T, z_lo)
        d_far = _expected_disparity_px(mtx1, T, z_hi)
    d_min = int(np.floor(min(d_far, d_close) / 16.0) * 16)
    d_max = int(np.ceil(max(d_far, d_close) / 16.0) * 16)
    span  = max(16, d_max - d_min)
    span  = int(np.ceil(span / 16.0) * 16)
    max_num = max(128, (w - 32) // 16 * 16)
    if d_min + span > max_num:
        span = max(16, max_num - d_min)
    if span < 16:
        d_min = 0
        span = min(max_num, max(128, int(np.ceil(d_close / 16.0) * 16)))
    return d_min, span


def _prep_stereo_gray_for_match(bgr: np.ndarray) -> np.ndarray:
    """Contraste léger pour matcher sous l'eau (sans déformer la géométrie)."""
    gray = cv.cvtColor(bgr, cv.COLOR_BGR2GRAY)
    clahe = cv.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    return clahe.apply(gray)


def _make_stereo_sgbm(min_disp: int, num_disp: int, block_size: int = 7,
                      *, precise: bool = False):
    """SGBM - block_size = fenêtre de matching N×N (impair)."""
    bs = block_size if block_size % 2 == 1 else block_size + 1
    bs = max(3, min(bs, 31))
    if precise:
        mode = cv.STEREO_SGBM_MODE_HH
        uniq, speck_w, speck_r, d12 = 15, 36, 2, 1
    elif bs > 11:
        mode = cv.STEREO_SGBM_MODE_SGBM_3WAY
        uniq, speck_w, speck_r, d12 = 12, 100, 8, 3
    else:
        mode = cv.STEREO_SGBM_MODE_HH
        uniq, speck_w, speck_r, d12 = 10, 120, 6, 2
    return cv.StereoSGBM_create(
        minDisparity=int(min_disp),
        numDisparities=int(num_disp),
        blockSize=bs,
        P1=8 * bs * bs,
        P2=32 * bs * bs,
        disp12MaxDiff=d12,
        uniquenessRatio=uniq,
        speckleWindowSize=speck_w,
        speckleRange=speck_r,
        preFilterCap=63,
        mode=mode)


def _try_wls_disparity(disp16_l: np.ndarray, gray_l: np.ndarray,
                       gray_r: np.ndarray, guide_bgr: np.ndarray,
                       stereo, *, precise: bool = False) -> tuple[np.ndarray, bool]:
    """Filtre WLS (opencv-contrib) sur les cartes 16 bits brutes du matcher.

    Le filtre attend les sorties CV_16S non divisées par 16 ; le guide couleur
    donne des bords nets alignés sur l'image. Retourne (disparité px, wls_ok).
    """
    try:
        right_matcher = cv.ximgproc.createRightMatcher(stereo)
        disp16_r = right_matcher.compute(gray_r, gray_l)
        wls = cv.ximgproc.createDisparityWLSFilter(matcher_left=stereo)
        if precise:
            wls.setLambda(1200.0)
            wls.setSigmaColor(0.45)
        else:
            wls.setLambda(8000.0)
            wls.setSigmaColor(1.2)
        filtered = wls.filter(disp16_l, guide_bgr,
                              disparity_map_right=disp16_r)
        return filtered.astype(np.float32) / 16.0, True
    except (AttributeError, cv.error):
        return disp16_l.astype(np.float32) / 16.0, False


def _propagate_disparity_holes(disp: np.ndarray, valid: np.ndarray,
                               iterations: int = 12) -> tuple[np.ndarray, np.ndarray]:
    """Remplit les petits trous dans la zone géométriquement valide (voisinage 4-connexe)."""
    out = disp.copy()
    mask = valid.copy()
    for _ in range(iterations):
        acc = np.zeros_like(out, dtype=np.float64)
        cnt = np.zeros_like(out, dtype=np.float64)
        for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            sh = np.roll(np.roll(out, dy, axis=0), dx, axis=1)
            mk = np.roll(np.roll(mask, dy, axis=0), dx, axis=1)
            acc += sh * mk
            cnt += mk
        fillable = (cnt > 0) & (~mask)
        if not fillable.any():
            break
        out[fillable] = (acc[fillable] / cnt[fillable]).astype(np.float32)
        mask |= fillable
    return out, mask


def _compute_disparity_sgbm(rect1, rect2, mtx1, T,
                            z_near_mm: float = 1200.0,
                            z_far_mm: float = 8000.0,
                            fill_holes: bool = False,
                            block_size: int = 7,
                            P1=None,
                            *,
                            precise: bool = False,
                            use_wls: bool = True):
    """Disparité SGBM sur paires rectifiées - sans inventer de pixels par défaut."""
    h, w = rect1.shape[:2]
    gray1 = _prep_stereo_gray_for_match(rect1)
    gray2 = _prep_stereo_gray_for_match(rect2)
    f = _focal_from_projection(P1) if P1 is not None else float(
        (mtx1[0, 0] + mtx1[1, 1]) * 0.5)
    B = _stereo_baseline_mm(T)

    min_d, num_d = _disparity_search_range(
        mtx1, T, w, z_near_mm, z_far_mm, P1=P1)
    stereo = _make_stereo_sgbm(min_d, num_d, block_size, precise=precise)
    disp16 = stereo.compute(gray1, gray2)
    disp_raw = disp16.astype(np.float32) / 16.0
    valid_raw = disp_raw > (min_d + 0.5)
    pct_raw = 100.0 * float(valid_raw.mean())

    if use_wls:
        disp, wls_ok = _try_wls_disparity(
            disp16, gray1, gray2, rect1, stereo, precise=precise)
    else:
        disp, wls_ok = disp_raw, False
    valid = disp > (min_d + 0.5)
    if precise and valid.any():
        kernel = cv.getStructuringElement(cv.MORPH_ELLIPSE, (3, 3))
        eroded = cv.erode(
            valid.astype(np.uint8) * 255, kernel, iterations=1) > 0
        disp = disp.copy()
        disp[~eroded] = 0.0
        valid = eroded
    pct_wls = 100.0 * float(valid.mean())

    if fill_holes:
        disp, valid = _propagate_disparity_holes(disp, valid)

    d_near = (
        _expected_disparity_px_rect(P1, T, max(z_near_mm, z_far_mm))
        if P1 is not None else
        _expected_disparity_px(mtx1, T, max(z_near_mm, z_far_mm)))
    d_far_px = (
        _expected_disparity_px_rect(P1, T, min(z_near_mm, z_far_mm))
        if P1 is not None else
        _expected_disparity_px(mtx1, T, min(z_near_mm, z_far_mm)))
    diag = {
        'pct_raw': pct_raw,
        'pct_wls': pct_wls,
        'wls_ok': wls_ok,
        'min_d': min_d,
        'num_d': num_d,
        'block_size': block_size,
        'precise': precise,
        'z_near_mm': z_near_mm,
        'z_far_mm': z_far_mm,
        'disp_at_z_far': d_near,
        'disp_at_z_near': d_far_px,
    }
    return disp, min_d, num_d, f, B, diag


def _depth_from_disparity(disp: np.ndarray, Q: np.ndarray,
                          min_d: float, z_near_mm: float, z_far_mm: float):
    """Profondeur mm + masque valide (plage Z utilisateur, sans resserrage caché)."""
    points_3d = cv.reprojectImageTo3D(disp, Q)
    depth = points_3d[:, :, 2].astype(np.float32)
    z_lo = max(80.0, min(z_near_mm, z_far_mm))
    z_hi = max(z_lo + 1.0, max(z_near_mm, z_far_mm))
    valid = ((disp > min_d + 0.5) & np.isfinite(depth)
             & (depth >= z_lo) & (depth <= z_hi))
    return depth, valid


def _rect_to_source_maps(mtx, dist, R, P, h: int, w: int):
    """Maps bilinéaires rectifié → image source."""
    xs, ys = np.meshgrid(np.arange(w, dtype=np.float32),
                         np.arange(h, dtype=np.float32))
    pts = np.stack([xs, ys], axis=-1).reshape(-1, 1, 2)
    rect_pts = cv.undistortPoints(pts, mtx, dist, R=R, P=P)
    map_x = rect_pts[:, 0, 0].reshape(h, w).astype(np.float32)
    map_y = rect_pts[:, 0, 1].reshape(h, w).astype(np.float32)
    return map_x, map_y


def _remap_rect_to_distorted(rect_data: np.ndarray, mtx, dist, R, P,
                             is_mask: bool = False) -> np.ndarray:
    """Échantillonne depth ou masque rectifié sur la grille source."""
    h, w = rect_data.shape[:2]
    map_x, map_y = _rect_to_source_maps(mtx, dist, R, P, h, w)
    interp = cv.INTER_NEAREST if is_mask else cv.INTER_LINEAR
    border = 0
    out = cv.remap(rect_data, map_x, map_y, interp,
                   borderMode=cv.BORDER_CONSTANT, borderValue=border)
    if is_mask:
        return out > 0.5
    return out.astype(np.float32)


def _make_depth_visualizations(base_bgr: np.ndarray, depth_mm: np.ndarray,
                               valid: np.ndarray, z_lo: float, z_hi: float,
                               y_skip_frac: float = 0.0) -> dict:
    """Overlay honnête + depth pure - trous = gris, pas de losanges inventés."""
    base = base_bgr.copy()
    depth = depth_mm.astype(np.float32)
    ok = valid.astype(bool)
    h, w = depth.shape

    t = np.zeros((h, w), np.float32)
    if ok.any():
        t[ok] = np.clip((depth[ok] - z_lo) / max(z_hi - z_lo, 1.0), 0.0, 1.0)
    cmap = cv.applyColorMap((t * 255.0).astype(np.uint8), cv.COLORMAP_TURBO)

    overlay = base.copy()
    if ok.any():
        blend = cv.addWeighted(base, 0.42, cmap, 0.58, 0)
        overlay[ok] = blend[ok]
    bad = ~ok
    if bad.any():
        gray = cv.cvtColor(base, cv.COLOR_BGR2GRAY)
        dim = cv.cvtColor((gray.astype(np.float32) * 0.3).astype(np.uint8),
                          cv.COLOR_GRAY2BGR)
        overlay[bad] = dim[bad]

    pure = np.full_like(base, 40)
    if ok.any():
        pure[ok] = cmap[ok]

    if y_skip_frac > 0:
        y0 = int(h * y_skip_frac)
        overlay[:y0] = base[:y0]
        pure[:y0] = base[:y0]

    depth_out = np.zeros_like(depth)
    depth_out[ok] = depth[ok]
    return {'overlay': overlay, 'pure': pure, 'depth': depth_out}


def extract_frames(video_path, output_folder, frame_indices):
    cap = cv.VideoCapture(video_path)
    os.makedirs(output_folder, exist_ok=True)
    saved = []
    for idx in frame_indices:
        cap.set(cv.CAP_PROP_POS_FRAMES, idx)
        ret, frame = cap.read()
        if ret:
            path = os.path.join(output_folder, f"{idx:06d}.png")
            cv.imwrite(path, frame)
            saved.append(path)
    cap.release()
    return saved


# ── Détection du flash de synchronisation ─────────────────────────────────────

def _roi_to_pixels(roi, w: int, h: int):
    """Convertit une ROI normalisée (x, y, w, h) en bornes pixel (x0, y0, x1, y1).

    Retourne None si la ROI est invalide ou couvre toute l'image.
    """
    if roi is None:
        return None
    x0 = int(round(roi[0] * w))
    y0 = int(round(roi[1] * h))
    x1 = int(round((roi[0] + roi[2]) * w))
    y1 = int(round((roi[1] + roi[3]) * h))
    x0, x1 = sorted((max(0, min(w, x0)), max(0, min(w, x1))))
    y0, y1 = sorted((max(0, min(h, y0)), max(0, min(h, y1))))
    if x1 - x0 < 2 or y1 - y0 < 2:
        return None
    return x0, y0, x1, y1


def detect_flash_frame(video_path: str,
                       search_center_s: float = 0.0,
                       window_s: float = 5.0,
                       roi=None,
                       log_fn=None,
                       progress_fn=None,
                       progress_start: int = 0,
                       progress_end: int = 100):
    """Détecte la frame du flash dans une fenêtre temporelle précise.

    Lit TOUTES les frames entre [center - window, center + window] pour une
    détection sub-frame précise sans sur-échantillonnage.
    Si ``roi`` (x, y, w, h normalisés 0..1) est fourni, la luminosité n'est
    mesurée que dans ce cadre - plus robuste quand le flash est toujours
    localisé dans la même zone de l'image.
    Retourne (flash_frame_idx, indices_array, brightness_array).
    """
    cap   = cv.VideoCapture(video_path)
    total = int(cap.get(cv.CAP_PROP_FRAME_COUNT))
    fps   = cap.get(cv.CAP_PROP_FPS) or 30.0
    if total == 0:
        cap.release()
        raise RuntimeError(f"Impossible d'ouvrir : {video_path}")

    center = int(search_center_s * fps)
    half   = int(window_s * fps)
    start  = max(0, center - half)
    end    = min(total - 1, center + half)

    if log_fn:
        log_fn(f"  Fenêtre : frame {start} → {end}  "
               f"({(end - start) / fps:.1f} s à {fps:.0f} fps)")

    cap.set(cv.CAP_PROP_POS_FRAMES, start)
    indices, brightness = [], []
    n_frames = max(end - start, 1)
    roi_px   = None   # bornes pixel calculées sur la 1re frame lue

    for i in range(start, end + 1):
        ret, frame = cap.read()
        if not ret:
            break
        gray = cv.cvtColor(frame, cv.COLOR_BGR2GRAY)
        if roi is not None and roi_px is None:
            roi_px = _roi_to_pixels(roi, gray.shape[1], gray.shape[0])
            if log_fn:
                if roi_px:
                    x0, y0, x1, y1 = roi_px
                    log_fn(f"  ROI détection : x[{x0}:{x1}] y[{y0}:{y1}] "
                           f"({x1 - x0}×{y1 - y0} px)")
                else:
                    log_fn("  ⚠ ROI ignorée (cadre invalide) - image entière utilisée")
        if roi_px is not None:
            x0, y0, x1, y1 = roi_px
            region = gray[y0:y1, x0:x1]
        else:
            region = gray
        indices.append(i)
        brightness.append(float(np.mean(region)))
        if progress_fn:
            pct = progress_start + int(
                (i - start) / n_frames * (progress_end - progress_start))
            progress_fn(min(pct, progress_end - 1))
    cap.release()

    if len(brightness) < 3:
        raise RuntimeError(
            f"Trop peu de frames dans la fenêtre [{start},{end}] - "
            "élargissez la fenêtre ou vérifiez la position du flash.")

    idx = np.array(indices, dtype=int)
    b   = np.array(brightness, dtype=float)

    # Front montant le plus fort = début du flash
    db          = np.diff(b)
    edge_pos    = int(np.argmax(db))
    flash_samp  = min(edge_pos + 1, len(idx) - 1)
    flash_frame = int(idx[flash_samp])

    if log_fn:
        log_fn(f"  Flash frame : {flash_frame}  "
               f"(t={flash_frame / fps:.2f}s  |  "
               f"Δluminosité +{db[edge_pos]:.1f})")
    return flash_frame, idx, b


def _draw_brightness_curves(idx_l, b_l, idx_r, b_r,
                             flash_l: int, flash_r: int) -> np.ndarray:
    """Génère une image BGR montrant les deux courbes de luminosité et les
    frames de flash détectées."""
    W, H    = 960, 180
    img     = np.full((H, W, 3), 28, dtype=np.uint8)
    pad     = 12

    all_b   = np.concatenate([b_l, b_r])
    b_min   = all_b.min()
    b_range = max(all_b.max() - b_min, 1.0)

    total_max = max(int(idx_l[-1]) if len(idx_l) else 1,
                    int(idx_r[-1]) if len(idx_r) else 1)

    def to_xy(idx_arr, b_arr):
        x = (idx_arr / total_max * (W - 2 * pad) + pad).astype(int)
        y = (H - pad - (b_arr - b_min) / b_range * (H - 2 * pad)).astype(int)
        return np.clip(x, 0, W - 1), np.clip(y, 0, H - 1)

    col_l = (90, 90, 220)    # rouge-bleu (gauche)
    col_r = (60, 180, 60)    # vert (droite)

    xl, yl = to_xy(idx_l, b_l)
    for i in range(1, len(xl)):
        cv.line(img, (xl[i - 1], yl[i - 1]), (xl[i], yl[i]), col_l, 1, cv.LINE_AA)

    xr, yr = to_xy(idx_r, b_r)
    for i in range(1, len(xr)):
        cv.line(img, (xr[i - 1], yr[i - 1]), (xr[i], yr[i]), col_r, 1, cv.LINE_AA)

    # Lignes verticales flash
    for frame_no, col, label in [
        (flash_l, col_l, f"L:{flash_l}"),
        (flash_r, col_r, f"R:{flash_r}"),
    ]:
        fx = int(frame_no / total_max * (W - 2 * pad) + pad)
        cv.line(img, (fx, 0), (fx, H), col, 2, cv.LINE_AA)
        cv.putText(img, label, (fx + 4, 22),
                   cv.FONT_HERSHEY_SIMPLEX, 0.45, col, 1, cv.LINE_AA)

    # Légendes
    font = cv.FONT_HERSHEY_SIMPLEX
    cv.rectangle(img, (pad, H - 20), (pad + 14, H - 8), col_l, -1)
    cv.putText(img, "Left",  (pad + 18, H - 8), font, 0.40, (180, 180, 240), 1)
    cv.rectangle(img, (pad + 60, H - 20), (pad + 74, H - 8), col_r, -1)
    cv.putText(img, "Right", (pad + 78, H - 8), font, 0.40, (150, 220, 150), 1)

    return img


# ── Dictionnaires ArUco disponibles ───────────────────────────────────────────

CHARUCO_DICT_MAP: dict[str, int] = {
    "DICT_4X4_50":  cv.aruco.DICT_4X4_50,
    "DICT_4X4_100": cv.aruco.DICT_4X4_100,
    "DICT_5X5_50":  cv.aruco.DICT_5X5_50,
    "DICT_5X5_100": cv.aruco.DICT_5X5_100,
    "DICT_6X6_50":  cv.aruco.DICT_6X6_50,
    "DICT_6X6_100": cv.aruco.DICT_6X6_100,
}


def _make_charuco(squares_x: int, squares_y: int,
                  square_length_mm: float, marker_length_mm: float,
                  aruco_dict_id: int):
    """Crée le board ChArUco + détecteur (API OpenCV 4.8+)."""
    dictionary  = cv.aruco.getPredefinedDictionary(aruco_dict_id)
    board = cv.aruco.CharucoBoard(
        (squares_x, squares_y),
        square_length_mm, marker_length_mm,
        dictionary)
    det_params      = cv.aruco.DetectorParameters()
    # Raffinement sous-pixel des coins ArUco (comme Charuco_Stereo_Calibrator).
    det_params.cornerRefinementMethod = cv.aruco.CORNER_REFINE_SUBPIX
    det_params.cornerRefinementWinSize = 11
    det_params.cornerRefinementMaxIterations = 50
    det_params.cornerRefinementMinAccuracy = 0.01
    charuco_params  = cv.aruco.CharucoParameters()
    # ≥2 marqueurs par coin : avec 1 seul, detectBoard peut coller le point sur un coin ArUco.
    charuco_params.minMarkers      = 2
    charuco_params.tryRefineMarkers = True
    detector        = cv.aruco.CharucoDetector(board, charuco_params, det_params)
    aruco_detector  = cv.aruco.ArucoDetector(dictionary, det_params)
    return board, detector, aruco_detector


# detectBoard interpole les coins depuis les marqueurs ArUco → décalage possible.
# Le raffinement se fait toujours sur l'image ORIGINALE (pas CLAHE : déplace les bords).
_SUBPIX_CRITERIA = (cv.TERM_CRITERIA_EPS + cv.TERM_CRITERIA_MAX_ITER, 40, 0.001)
def _prep_gray_clahe(gray: np.ndarray) -> np.ndarray:
    """Contraste local - uniquement en secours si detectBoard échoue (eau trouble)."""
    if gray.dtype != np.uint8:
        return gray
    clahe = cv.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    return clahe.apply(gray)


def _refine_charuco_corners(gray: np.ndarray, corners: np.ndarray) -> np.ndarray:
    """Raffine les coins sur l'image originale (subpix symétrique G/D pour la stéréo)."""
    if corners is None or len(corners) == 0 or gray.dtype != np.uint8:
        return corners
    pts = corners.astype(np.float32).reshape(-1, 1, 2)
    # Fenêtre modérée : 11×11 attire parfois le point vers un coin ArUco voisin.
    pts = cv.cornerSubPix(gray, pts, (7, 7), (-1, -1), _SUBPIX_CRITERIA)
    return pts


def _marker_corner_points(marker_corners) -> np.ndarray | None:
    if marker_corners is None or len(marker_corners) == 0:
        return None
    chunks = [np.asarray(m, dtype=np.float64).reshape(-1, 2) for m in marker_corners]
    return np.vstack(chunks)


def _filter_charuco_not_on_aruco(charuco_corners, charuco_ids, marker_corners,
                                 min_dist_px: float = 5.0):
    """Retire les coins ChArUco coincés sur un coin de marqueur ArUco (pas le damier)."""
    mc = _marker_corner_points(marker_corners)
    if (mc is None or charuco_corners is None or charuco_ids is None
            or len(charuco_corners) == 0):
        return charuco_corners, charuco_ids, 0
    pts = charuco_corners.reshape(-1, 2)
    ids = np.asarray(charuco_ids).reshape(-1)
    keep_pts, keep_ids = [], []
    rejected = 0
    for pt, cid in zip(pts, ids):
        if float(np.min(np.linalg.norm(mc - pt, axis=1))) < min_dist_px:
            rejected += 1
            continue
        keep_pts.append(pt)
        keep_ids.append(int(cid))
    if not keep_pts:
        return None, None, rejected
    out_c = np.array(keep_pts, dtype=np.float32).reshape(-1, 1, 2)
    out_i = np.array(keep_ids, dtype=np.int32).reshape(-1, 1)
    return out_c, out_i, rejected


def _detect_charuco(gray, board, detector, aruco_detector=None):
    """Détecte les coins ChArUco + raffinement sur croisements damier.
    Retourne (charuco_corners, charuco_ids, n_aruco)."""
    charuco_corners, charuco_ids, marker_corners, marker_ids = \
        detector.detectBoard(gray)
    # Secours CLAHE uniquement si peu de coins (vidéo trouble) - pas sur image nette.
    if charuco_ids is None or len(charuco_ids) < MIN_CALIB_CORNERS:
        gray_clahe = _prep_gray_clahe(gray)
        c2, i2, mc2, mi2 = detector.detectBoard(gray_clahe)
        n2 = len(i2) if i2 is not None else 0
        n1 = len(charuco_ids) if charuco_ids is not None else 0
        if n2 > n1:
            charuco_corners, charuco_ids = c2, i2
            marker_corners, marker_ids = mc2, mi2
    n_aruco = len(marker_ids) if marker_ids is not None else 0
    charuco_corners, charuco_ids, _ = _filter_charuco_not_on_aruco(
        charuco_corners, charuco_ids, marker_corners)
    if charuco_ids is None or len(charuco_ids) < MIN_CALIB_CORNERS:
        return None, None, n_aruco
    if charuco_corners is not None and len(charuco_corners) > 0:
        charuco_corners = _refine_charuco_corners(gray, charuco_corners)
        charuco_corners, charuco_ids, _ = _filter_charuco_not_on_aruco(
            charuco_corners, charuco_ids, marker_corners)
        if charuco_ids is None or len(charuco_ids) < MIN_CALIB_CORNERS:
            return None, None, n_aruco
    return charuco_corners, charuco_ids, n_aruco


def _detect_charuco_fast(gray, board, detector, aruco_detector=None):
    """Détection ChArUco accélérée : skip CLAHE/subpix si 0 marqueur ArUco."""
    charuco_corners, charuco_ids, marker_corners, marker_ids = \
        detector.detectBoard(gray)
    n_aruco = len(marker_ids) if marker_ids is not None else 0
    if n_aruco == 0:
        return None, None, 0
    if charuco_ids is None or len(charuco_ids) < MIN_CALIB_CORNERS:
        gray_clahe = _prep_gray_clahe(gray)
        c2, i2, mc2, mi2 = detector.detectBoard(gray_clahe)
        n2 = len(i2) if i2 is not None else 0
        n1 = len(charuco_ids) if charuco_ids is not None else 0
        if n2 > n1:
            charuco_corners, charuco_ids = c2, i2
            marker_corners, marker_ids = mc2, mi2
        n_aruco = len(marker_ids) if marker_ids is not None else 0
    charuco_corners, charuco_ids, _ = _filter_charuco_not_on_aruco(
        charuco_corners, charuco_ids, marker_corners)
    if charuco_ids is None or len(charuco_ids) < MIN_CALIB_CORNERS:
        return None, None, n_aruco
    if charuco_corners is not None and len(charuco_corners) > 0:
        charuco_corners = _refine_charuco_corners(gray, charuco_corners)
        charuco_corners, charuco_ids, _ = _filter_charuco_not_on_aruco(
            charuco_corners, charuco_ids, marker_corners)
        if charuco_ids is None or len(charuco_ids) < MIN_CALIB_CORNERS:
            return None, None, n_aruco
    return charuco_corners, charuco_ids, n_aruco


def _calib_charuco_opencv_opts(worker) -> dict:
    """Vues max et échelle OpenCV - valeurs du job (préréglage UI)."""
    return {
        'max_views': int(worker.max_intrinsic_views),
        'camera_scale': float(worker.calib_camera_scale),
    }


def calibrate_charuco(image_paths, squares_x, squares_y,
                      square_length_mm, marker_length_mm, aruco_dict_id,
                      log_fn, progress_fn=None, prog_start=0, prog_end=100,
                      cancelled_fn=None, live_fn=None, busy_fn=None,
                      detection_cache=None,
                      max_views: int | None = None,
                      camera_scale: float = 1.0):
    """Calibration intrinsèque via cible ChArUco."""
    objpoints, imgpoints = [], []
    detected_info = []
    w, h = None, None

    if detection_cache:
        log_fn(
            f"  Réutilisation de {len(detection_cache)} détections "
            f"(scan - pas de relecture disque)")
        for entry in detection_cache:
            objpoints.append(entry['obj_pts'])
            imgpoints.append(entry['img_pts'])
            detected_info.append({
                'path':    entry['path'],
                'corners': entry['corners'],
                'ids':     entry['ids'],
            })
            w, h = entry['image_size']
    else:
        board, detector, _aruco_detector = _make_charuco(
            squares_x, squares_y, square_length_mm, marker_length_mm,
            aruco_dict_id)
        detected = 0
        total = len(image_paths)
        log_fn(f"  Détection ChArUco sur {total} frames…")
        for idx, path in enumerate(image_paths):
            if cancelled_fn and cancelled_fn():
                log_fn("  ⛔ Calibration annulée.")
                return None, None, None, None, None, None, None
            frame = cv.imread(path, 1)
            if frame is None:
                continue
            if h is None:
                h, w = frame.shape[:2]
            gray = cv.cvtColor(frame, cv.COLOR_BGR2GRAY)
            charuco_corners, charuco_ids, _ = _detect_charuco(
                gray, board, detector)
            if charuco_ids is None or len(charuco_ids) < MIN_CALIB_CORNERS:
                continue
            obj_pts, img_pts = board.matchImagePoints(
                charuco_corners, charuco_ids)
            if obj_pts is None or _n_calib_points(obj_pts) < MIN_CALIB_CORNERS:
                continue
            objpoints.append(obj_pts)
            imgpoints.append(img_pts)
            detected_info.append({
                'path':    path,
                'corners': charuco_corners.reshape(-1, 1, 2).astype(np.float32),
                'ids':     charuco_ids.copy(),
            })
            detected += 1
            if progress_fn and idx % 10 == 0:
                pct = prog_start + int(
                    (prog_end - prog_start) * 0.5 * idx / max(total, 1))
                progress_fn(pct)
        log_fn(f"  ChArUco detected: {detected}/{total} frames")

    if not objpoints:
        raise RuntimeError(
            "No ChArUco board detected. Vérifiez squares_x/y, tailles "
            "square/marker et le dictionnaire ArUco.")
    if w is None or h is None:
        raise RuntimeError("Aucune image lisible.")

    objpoints, imgpoints, detected_info = _filter_mono_calib_sets(
        objpoints, imgpoints, detected_info, MIN_CALIB_CORNERS, log_fn)
    if not objpoints:
        raise RuntimeError(
            f"Aucune vue avec ≥ {MIN_CALIB_CORNERS} coins ChArUco après filtrage. "
            "Augmentez la visibilité de la mire ou vérifiez la config du board.")

    n_cap = int(max_views) if max_views is not None else None
    if n_cap is not None and n_cap > 0 and len(objpoints) > n_cap:
        objpoints, imgpoints, detected_info = _subsample_calib_views(
            objpoints, imgpoints, detected_info, n_cap, log_fn)

    objpoints, imgpoints, detected_info = _filter_mono_pnp_outliers(
        objpoints, imgpoints, detected_info, (w, h), log_fn)
    if not objpoints:
        raise RuntimeError(
            "Aucune vue mono fiable après élagage PnP - mire trop tronquée ou "
            "détections ChArUco dégénérées (visez ≥ 8 coins visibles par vue).")

    n_views = len(objpoints)
    gc.collect()
    _validate_calib_points(objpoints, imgpoints, log_fn)
    op_full, ip_full = _prepare_calib_buffers(objpoints, imgpoints)
    scale = max(0.25, min(1.0, float(camera_scale)))
    op, ip, calib_size, calib_scale = _downscale_imgpoints_for_calib(
        op_full, ip_full, w, h, scale)
    cw, ch = calib_size
    if calib_scale < 0.999:
        log_fn(
            f"  calibrateCamera en demi-résolution ({cw}×{ch}, scale={calib_scale}) "
            f"→ intrinsiques rescalées en {w}×{h} après optimisation")
    else:
        log_fn(f"  calibrateCamera pleine résolution ({cw}×{h})")
    log_fn(
        f"  calibrateCamera ({n_views} vues, {cw}×{ch}, float32)…")
    hints = _calib_duration_hints(n_views, n_views)
    log_fn(
        f"  Durée typique : {hints['intrinsic_per_cam']} par caméra "
        f"({n_views} vues - OpenCV calcule en silence)")
    log_fn(f"  OpenCV {cv.__version__}")

    if progress_fn:
        progress_fn(prog_start + int((prog_end - prog_start) * 0.65))

    t0 = time.monotonic()
    try:
        pack = _run_opencv_blocking(
            "calibrateCamera",
            lambda: _run_calibrate_camera(op, ip, calib_size, log_fn),
            log_fn,
            progress_fn=progress_fn,
            prog_val=prog_start + int((prog_end - prog_start) * 0.7),
            live_fn=live_fn,
            busy_fn=busy_fn,
            interval_s=3.0)
        ret, mtx, dist, rvecs, tvecs = pack
    except cv.error:
        _log_exception("calibrateCamera (OpenCV)", log_fn)
        raise
    except Exception:
        _log_exception("calibrateCamera", log_fn)
        raise
    elapsed = time.monotonic() - t0
    log_fn(f"  ✓ calibrateCamera terminé ({elapsed:.1f} s)")
    if elapsed > 120:
        log_fn(
            f"  ⚠ Plus lent que prévu ({elapsed:.0f} s) - vérifiez le nombre de vues "
            f"({n_views}) et la version OpenCV ({cv.__version__})")

    mtx = _upscale_intrinsics(mtx, calib_scale)

    total_error = 0
    for i in range(len(op_full)):
        pts2, _ = cv.projectPoints(op_full[i], rvecs[i], tvecs[i], mtx, dist)
        total_error += cv.norm(ip_full[i], pts2, cv.NORM_L2) / len(pts2)
    pixel_rmse = total_error / len(op)
    log_fn(f"  Reprojection error: {pixel_rmse:.4f} pixels")
    if progress_fn:
        progress_fn(prog_end)
    return mtx, dist, pixel_rmse, objpoints, imgpoints, detected_info, (w, h)


def stereo_calibrate_charuco(mtx1, dist1, mtx2, dist2,
                              left_paths, right_paths,
                              squares_x, squares_y,
                              square_length_mm, marker_length_mm,
                              aruco_dict_id, log_fn,
                              progress_fn=None, cancelled_fn=None,
                              live_fn=None, busy_fn=None):
    """Calibration stéréo via cible ChArUco - appariement par ID unique."""
    board, detector, aruco_detector = _make_charuco(
        squares_x, squares_y, square_length_mm, marker_length_mm, aruco_dict_id)

    criteria = (cv.TERM_CRITERIA_EPS + cv.TERM_CRITERIA_MAX_ITER, 100, 0.0001)
    objpoints, imgpoints_left, imgpoints_right = [], [], []
    valid = 0

    for p1, p2 in zip(left_paths, right_paths):
        f1 = cv.imread(p1, 1)
        f2 = cv.imread(p2, 1)
        if f1 is None or f2 is None:
            continue
        g1 = cv.cvtColor(f1, cv.COLOR_BGR2GRAY)
        g2 = cv.cvtColor(f2, cv.COLOR_BGR2GRAY)
        c1, ids1, _ = _detect_charuco(g1, board, detector)
        c2, ids2, _ = _detect_charuco(g2, board, detector)
        if ids1 is None or ids2 is None:
            continue

        # Ne conserver que les coins dont l'ID est visible dans les deux images
        ids1_flat = ids1.flatten()
        ids2_flat = ids2.flatten()
        common_ids = np.intersect1d(ids1_flat, ids2_flat)
        if len(common_ids) < MIN_CALIB_CORNERS:
            continue

        idx1 = np.where(np.isin(ids1_flat, common_ids))[0]
        idx2 = np.where(np.isin(ids2_flat, common_ids))[0]
        sort1 = np.argsort(ids1_flat[idx1])
        sort2 = np.argsort(ids2_flat[idx2])
        matched_c1 = c1[idx1[sort1]]
        matched_c2 = c2[idx2[sort2]]
        matched_ids = common_ids.reshape(-1, 1).astype(np.int32)

        obj_pts, _ = board.matchImagePoints(matched_c1, matched_ids)
        if obj_pts is None or _n_calib_points(obj_pts) < MIN_CALIB_CORNERS:
            continue

        objpoints.append(obj_pts)
        imgpoints_left.append(matched_c1)
        imgpoints_right.append(matched_c2)
        valid += 1

    log_fn(f"  Valid stereo pairs: {valid}/{len(left_paths)}")
    if not objpoints:
        raise RuntimeError("No valid stereo pairs found.")

    objpoints, imgpoints_left, imgpoints_right, _dropped = _prune_stereo_outliers(
        objpoints, imgpoints_left, imgpoints_right, log_fn)

    h, w = cv.imread(left_paths[0]).shape[:2]
    flags = cv.CALIB_FIX_INTRINSIC
    n_pairs = len(objpoints)
    op, _ = _prepare_calib_buffers(objpoints, imgpoints_left)
    ipl, ipr = [], []
    for a, b in zip(imgpoints_left, imgpoints_right):
        ipl.append(np.ascontiguousarray(
            np.asarray(a, dtype=np.float32).reshape(-1, 1, 2)))
        ipr.append(np.ascontiguousarray(
            np.asarray(b, dtype=np.float32).reshape(-1, 1, 2)))

    log_fn(f"  Calcul stereoCalibrate ({n_pairs} paires)…")
    gc.collect()
    t0 = time.monotonic()
    try:
        pack = _run_opencv_blocking(
            "stereoCalibrate",
            lambda: cv.stereoCalibrate(
                op, ipl, ipr,
                mtx1, dist1, mtx2, dist2, (w, h),
                criteria=criteria, flags=flags),
            log_fn,
            progress_fn=progress_fn, prog_val=90,
            live_fn=live_fn, busy_fn=busy_fn, interval_s=3.0)
        ret, _, _, _, _, R, T, E, F = pack
    except cv.error:
        _log_exception("stereoCalibrate (OpenCV)", log_fn)
        raise
    except Exception:
        _log_exception("stereoCalibrate", log_fn)
        raise
    log_fn(f"  ✓ stereoCalibrate terminé ({int(time.monotonic() - t0)} s)")
    log_fn(f"  Stereo RMSE: {ret:.4f} pixels")
    return R, T, F, ret


def _stereo_rmse_quality_label(rmse: float) -> str:
    if rmse < STEREO_RMSE_EXCELLENT_PX:
        return "excellent"
    if rmse < STEREO_RMSE_GOOD_PX:
        return "bon"
    if rmse < STEREO_RMSE_WARN_PX:
        return "acceptable"
    return "faible"


def _run_stereo_calibrate_extended(op, ipl, ipr, mtx1, dist1, mtx2, dist2, image_size,
                                   criteria, flags):
    """stereoCalibrateExtended si dispo, sinon stereoCalibrate (OpenCV 4.13 : R/T requis)."""
    R_guess = np.eye(3, dtype=np.float64)
    T_guess = np.zeros((3, 1), dtype=np.float64)
    if hasattr(cv, 'stereoCalibrateExtended'):
        pack = cv.stereoCalibrateExtended(
            op, ipl, ipr,
            mtx1, dist1, mtx2, dist2,
            image_size, R_guess, T_guess,
            flags=flags, criteria=criteria)
        ret = float(pack[0])
        R, T, E, F = pack[5], pack[6], pack[7], pack[8]
        per_view = pack[11] if len(pack) > 11 else None
        return ret, R, T, E, F, per_view
    pack = cv.stereoCalibrate(
        op, ipl, ipr, mtx1, dist1, mtx2, dist2, image_size,
        R_guess, T_guess, flags=flags, criteria=criteria)
    ret = float(pack[0])
    return ret, pack[5], pack[6], pack[7], pack[8], None


def _per_view_stereo_errors(per_view) -> np.ndarray | None:
    """Erreur scalaire par paire (moyenne G/D) - sortie stereoCalibrateExtended / perViewErr."""
    if per_view is None:
        return None
    arr = np.asarray(per_view, dtype=np.float64)
    if arr.size == 0:
        return None
    if arr.ndim == 1:
        return arr.reshape(-1)
    if arr.shape[1] >= 2:
        return 0.5 * (arr[:, 0] + arr[:, 1])
    return arr.reshape(-1)


def _stereo_calibrate_robust(op, ipl, ipr, mtx1, dist1, mtx2, dist2, image_size,
                             criteria, flags, log_fn, min_views=25, max_rmse=None,
                             outlier_sigma: float = STEREO_OUTLIER_SIGMA):
    """2 passes max (comme Qt) : stereoCalibrate → élagage batch per-view → recalcul."""
    if max_rmse is None:
        max_rmse = MAX_STEREO_RMSE_PX
    obj = list(op)
    pl = list(ipl)
    pr = list(ipr)

    ret, R, T, E, F, per_view = _run_stereo_calibrate_extended(
        obj, pl, pr, mtx1, dist1, mtx2, dist2, image_size, criteria, flags)

    errs = _per_view_stereo_errors(per_view)
    if errs is None or len(errs) != len(obj) or len(obj) <= min_views + 1:
        return ret, R, T, E, F, obj, pl, pr

    mean = float(np.mean(errs))
    std = float(np.std(errs))
    threshold = mean + outlier_sigma * std
    keep = [i for i, e in enumerate(errs) if e <= threshold]

    if len(keep) < min_views or len(keep) == len(obj):
        return ret, R, T, E, F, obj, pl, pr

    dropped = len(obj) - len(keep)
    obj_f = [obj[i] for i in keep]
    pl_f = [pl[i] for i in keep]
    pr_f = [pr[i] for i in keep]
    log_fn(
        f"  Élagage stéréo batch (passe 1, RMSE {ret:.4f} px) : "
        f"{dropped} paire(s) écartée(s) "
        f"(seuil {threshold:.3f}, moy={mean:.3f}) → {len(obj_f)} paires - recalcul…")

    ret2, R, T, E, F, _ = _run_stereo_calibrate_extended(
        obj_f, pl_f, pr_f, mtx1, dist1, mtx2, dist2, image_size, criteria, flags)
    log_fn(
        f"  Stéréo passe 2 : {len(obj_f)} paires · RMSE {ret2:.4f} px "
        f"({_stereo_rmse_quality_label(ret2)})")
    if ret2 > max_rmse:
        log_fn(
            f"  [AVERT] RMSE stéréo {ret2:.2f} px > seuil {max_rmse} px après élagage.")
    return ret2, R, T, E, F, obj_f, pl_f, pr_f


def _subsample_calib_lists(objpoints, imgpoints_left, imgpoints_right, n):
    """Sous-échantillonne uniformément des listes de calibration (même longueur)."""
    if n <= 0 or len(objpoints) <= n:
        return objpoints, imgpoints_left, imgpoints_right
    step = len(objpoints) / n
    idx = [int(i * step) for i in range(n)]
    return (
        [objpoints[i] for i in idx],
        [imgpoints_left[i] for i in idx],
        [imgpoints_right[i] for i in idx],
    )


def stereo_calibrate_charuco_paired(mtx1, dist1, mtx2, dist2,
                                    paired_entries,
                                    squares_x, squares_y,
                                    square_length_mm, marker_length_mm,
                                    aruco_dict_id, log_fn,
                                    progress_fn=None, cancelled_fn=None,
                                    live_fn=None, busy_fn=None,
                                    target_pairs=None):
    """Calibration stéréo sur paires gauche/droite déjà synchronisées (cache scan)."""
    board, _, _ = _make_charuco(
        squares_x, squares_y, square_length_mm, marker_length_mm, aruco_dict_id)

    criteria = (cv.TERM_CRITERIA_EPS + cv.TERM_CRITERIA_MAX_ITER, 50, 1e-5)
    objpoints, imgpoints_left, imgpoints_right = [], [], []

    for le, re in paired_entries:
        res = _stereo_points_from_pair(le, re, board)
        if res is None:
            continue
        obj_pts, mc1, mc2 = res
        objpoints.append(obj_pts)
        imgpoints_left.append(mc1)
        imgpoints_right.append(mc2)

    log_fn(
        f"  Paires stéréo valides : {len(objpoints)}/{len(paired_entries)} "
        f"(appariement temporel + IDs communs)")
    if not objpoints:
        raise RuntimeError("No valid stereo pairs found.")

    objpoints, imgpoints_left, imgpoints_right, _dropped = _prune_stereo_outliers(
        objpoints, imgpoints_left, imgpoints_right, log_fn)

    if target_pairs is not None and len(objpoints) > target_pairs:
        before = len(objpoints)
        objpoints, imgpoints_left, imgpoints_right = _subsample_calib_lists(
            objpoints, imgpoints_left, imgpoints_right, target_pairs)
        log_fn(
            f"  Pool stéréo : {before} paires après élagage "
            f"→ {len(objpoints)} (cible {target_pairs})")
    elif target_pairs is not None and len(objpoints) < target_pairs:
        log_fn(
            f"  [AVERT] {len(objpoints)} paires stéréo après élagage "
            f"(cible {target_pairs}) - pool insuffisant ou trop d'outliers")

    w, h = paired_entries[0][0]['image_size']
    fx1, fx2 = float(mtx1[0, 0]), float(mtx2[0, 0])
    if abs(fx1 - fx2) / max((fx1 + fx2) * 0.5, 1.0) > 0.05:
        log_fn(
            f"  Focales G/D différentes ({fx1:.0f} / {fx2:.0f} px) - "
            "pas de CALIB_SAME_FOCAL_LENGTH (incompatible stereoCalibrateExtended).")
    # NE PAS ajouter CALIB_SAME_FOCAL_LENGTH : avec FIX_INTRINSIC + Extended,
    # OpenCV 4.13 renvoie un RMSE bas mais R/T qui ne rectifient pas (|ΔY| ~50 px).
    flags = cv.CALIB_FIX_INTRINSIC | cv.CALIB_USE_INTRINSIC_GUESS
    op, _ = _prepare_calib_buffers(objpoints, imgpoints_left)
    ipl, ipr = [], []
    for a, b in zip(imgpoints_left, imgpoints_right):
        ipl.append(np.ascontiguousarray(
            np.asarray(a, dtype=np.float32).reshape(-1, 1, 2)))
        ipr.append(np.ascontiguousarray(
            np.asarray(b, dtype=np.float32).reshape(-1, 1, 2)))

    n_pts = sum(len(o) for o in op)
    log_fn(
        f"  Calcul stereoCalibrate ({len(op)} paires, {n_pts} points 3D/2D)…")
    hints = _calib_duration_hints(len(op), len(op))
    log_fn(
        f"  Durée typique : {hints['stereo']} ({len(op)} paires HD) - "
        "2 passes max (calcul + élagage batch si outliers)")
    gc.collect()
    t0 = time.monotonic()

    def _do_stereo():
        return _stereo_calibrate_robust(
            op, ipl, ipr, mtx1, dist1, mtx2, dist2, (w, h),
            criteria, flags, log_fn)

    try:
        ret, R, T, E, F, _, _, _ = _run_opencv_blocking(
            "stereoCalibrate",
            _do_stereo,
            log_fn,
            progress_fn=progress_fn, prog_val=90,
            live_fn=live_fn, busy_fn=busy_fn, interval_s=3.0)
    except cv.error:
        _log_exception("stereoCalibrate (OpenCV)", log_fn)
        raise
    except Exception:
        _log_exception("stereoCalibrate", log_fn)
        raise
    log_fn(f"  ✓ stereoCalibrate terminé ({int(time.monotonic() - t0)} s)")
    log_fn(
        f"  Stereo RMSE: {ret:.4f} px ({_stereo_rmse_quality_label(ret)})")
    dy_stats = _stereo_rectification_dy_stats(
        mtx1, dist1, mtx2, dist2, R, T, (w, h),
        imgpoints_left, imgpoints_right)
    rect_dy_median = None
    if dy_stats:
        med, p95, mx = dy_stats
        rect_dy_median = med
        log_fn(
            f"  Rectification (|ΔY| sur paires calib) : "
            f"médiane {med:.2f} px · p95 {p95:.1f} · max {mx:.1f}")
        if med <= 2.0:
            log_fn("  ✓ Alignement épilolaire rectifié - OK")
        elif med <= MAX_RECT_DY_WARN_PX:
            log_fn(
                f"  [AVERT] |ΔY| {med:.1f} px > 2 px - vérifiez zoom G/D identique.")
        else:
            log_fn(
                f"  ✗ |ΔY| {med:.1f} px - rectification invalide malgré RMSE bas.")
    return R, T, F, ret, rect_dy_median


# ── Worker synchronisation ────────────────────────────────────────────────────

class SyncWorker(QObject):
    log          = pyqtSignal(str)
    progress     = pyqtSignal(int)
    finished     = pyqtSignal()
    error        = pyqtSignal(str)
    result_ready = pyqtSignal(object)

    left_video:     str   = ""
    right_video:    str   = ""
    left_center_s:  float = 0.0
    right_center_s: float = 0.0
    window_s:       float = 5.0
    left_roi              = None   # (x, y, w, h) normalisés ou None
    right_roi             = None

    def run(self):
        try:
            self.log.emit("── Analyse luminosité - Gauche ────────────────────")
            left_flash, idx_l, b_l = detect_flash_frame(
                self.left_video,
                search_center_s=self.left_center_s,
                window_s=self.window_s,
                roi=self.left_roi,
                log_fn=self.log.emit,
                progress_fn=self.progress.emit,
                progress_start=0, progress_end=45)
            self.progress.emit(45)

            self.log.emit("── Analyse luminosité - Droite ────────────────────")
            right_flash, idx_r, b_r = detect_flash_frame(
                self.right_video,
                search_center_s=self.right_center_s,
                window_s=self.window_s,
                roi=self.right_roi,
                log_fn=self.log.emit,
                progress_fn=self.progress.emit,
                progress_start=45, progress_end=88)
            self.progress.emit(88)

            offset = left_flash - right_flash
            if offset > 0:
                direction = f"gauche en avance de {offset} frames"
            elif offset < 0:
                direction = f"droite en avance de {abs(offset)} frames"
            else:
                direction = "vidéos déjà synchronisées"
            self.log.emit(f"  Décalage : {offset:+d} frames  ({direction})")

            ensure_cam_params_dir()
            np.save(cam_param('sync_frames.npy'),
                    np.array([left_flash, right_flash], dtype=int))
            save_videos_txt(self.left_video, self.right_video)
            self.log.emit("  ✓ sync_frames.npy + videos.txt sauvegardés")
            self.progress.emit(90)

            def load_frame(path, fidx):
                cap = cv.VideoCapture(path)
                cap.set(cv.CAP_PROP_POS_FRAMES, fidx)
                ret, frame = cap.read()
                cap.release()
                return frame if ret else None

            left_frame  = load_frame(self.left_video,  left_flash)
            right_frame = load_frame(self.right_video, right_flash)
            brightness_img = _draw_brightness_curves(
                idx_l, b_l, idx_r, b_r, left_flash, right_flash)

            self.progress.emit(100)
            self.result_ready.emit({
                'left_flash_frame':  left_flash,
                'right_flash_frame': right_flash,
                'left_frame':        left_frame,
                'right_frame':       right_frame,
                'brightness_img':    brightness_img,
                'offset':            offset,
            })
        except Exception as exc:
            import traceback
            self.error.emit(f"[ERROR] {exc}\n{traceback.format_exc()}")
        finally:
            self.finished.emit()


# ── Barre In/Out ──────────────────────────────────────────────────────────────

class _InOutBar(QWidget):
    """Fine barre visuelle représentant la zone In→Out sur la durée totale."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._in_f  = 0.0   # fraction 0..1
        self._out_f = 1.0
        self._cur_f = 0.0
        self.setFixedHeight(8)
        self.setToolTip("Zone sélectionnée (orange) - curseur (bleu)")

    def update_state(self, in_frac: float, out_frac: float, cur_frac: float):
        self._in_f  = in_frac
        self._out_f = out_frac
        self._cur_f = cur_frac
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        w, h = self.width(), self.height()
        p.fillRect(0, 0, w, h, QColor(40, 40, 52))
        x1 = int(self._in_f  * w)
        x2 = int(self._out_f * w)
        if x2 > x1:
            p.fillRect(x1, 1, x2 - x1, h - 2, QColor(245, 158, 11, 180))
        cx = int(self._cur_f * w)
        p.fillRect(max(0, cx - 1), 0, 3, h, QColor(56, 189, 248))
        p.end()


# ── Aperçu vidéo avec cadre de détection (ROI) dessinable ─────────────────────

class _RoiThumb(QLabel):
    """QLabel d'aperçu permettant de tracer un cadre (ROI) à la souris.

    La ROI est mémorisée en coordonnées normalisées (x, y, w, h) dans [0,1]
    relatives à l'image affichée - robuste au redimensionnement du widget.
    """

    roi_changed = pyqtSignal()

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self._roi = None            # (x, y, w, h) normalisés ou None
        self._draw_mode = False
        self._drag_a: QPoint | None = None
        self._drag_b: QPoint | None = None

    # ── API ──
    def set_draw_mode(self, on: bool):
        self._draw_mode = bool(on)
        self.setCursor(Qt.CursorShape.CrossCursor if on
                       else Qt.CursorShape.ArrowCursor)
        self.update()

    def get_roi(self):
        return self._roi

    def set_roi(self, roi):
        if roi is not None:
            x, y, w, h = roi
            x = max(0.0, min(1.0, float(x)))
            y = max(0.0, min(1.0, float(y)))
            w = max(0.0, min(1.0 - x, float(w)))
            h = max(0.0, min(1.0 - y, float(h)))
            roi = (x, y, w, h) if (w > 0 and h > 0) else None
        self._roi = roi
        self.update()

    def clear_roi(self):
        self._roi = None
        self.roi_changed.emit()
        self.update()

    # ── Géométrie de l'image réellement affichée (centrée, letterbox) ──
    def _image_rect(self) -> QRect:
        pm = self.pixmap()
        if pm is None or pm.isNull():
            return QRect()
        iw, ih = pm.width(), pm.height()
        ox = (self.width() - iw) // 2
        oy = (self.height() - ih) // 2
        return QRect(ox, oy, iw, ih)

    def _to_norm(self, pt: QPoint):
        r = self._image_rect()
        if r.width() <= 0 or r.height() <= 0:
            return None
        nx = (pt.x() - r.x()) / r.width()
        ny = (pt.y() - r.y()) / r.height()
        return max(0.0, min(1.0, nx)), max(0.0, min(1.0, ny))

    # ── Souris ──
    def mousePressEvent(self, ev):
        if self._draw_mode and ev.button() == Qt.MouseButton.LeftButton:
            self._drag_a = ev.position().toPoint()
            self._drag_b = self._drag_a
            self.update()
        else:
            super().mousePressEvent(ev)

    def mouseMoveEvent(self, ev):
        if self._draw_mode and self._drag_a is not None:
            self._drag_b = ev.position().toPoint()
            self.update()
        else:
            super().mouseMoveEvent(ev)

    def mouseReleaseEvent(self, ev):
        if (self._draw_mode and self._drag_a is not None
                and ev.button() == Qt.MouseButton.LeftButton):
            a = self._to_norm(self._drag_a)
            b = self._to_norm(self._drag_b)
            self._drag_a = self._drag_b = None
            if a and b:
                x0, x1 = sorted((a[0], b[0]))
                y0, y1 = sorted((a[1], b[1]))
                w, h = x1 - x0, y1 - y0
                if w >= 0.02 and h >= 0.02:   # cadre minimal ~2 %
                    self._roi = (x0, y0, w, h)
                    self.roi_changed.emit()
            self.update()
        else:
            super().mouseReleaseEvent(ev)

    # ── Rendu ──
    def paintEvent(self, ev):
        super().paintEvent(ev)
        r = self._image_rect()
        if r.width() <= 0:
            return
        p = QPainter(self)
        if self._roi is not None:
            x, y, w, h = self._roi
            rx = int(r.x() + x * r.width())
            ry = int(r.y() + y * r.height())
            rw = int(w * r.width())
            rh = int(h * r.height())
            p.setPen(QPen(QColor(56, 189, 248), 2))
            p.setBrush(QBrush(QColor(56, 189, 248, 45)))
            p.drawRect(rx, ry, rw, rh)
        if self._drag_a is not None and self._drag_b is not None:
            p.setPen(QPen(QColor(245, 158, 11), 1, Qt.PenStyle.DashLine))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(QRect(self._drag_a, self._drag_b).normalized())
        p.end()


# ── Widget de navigation vidéo avec scrubber ──────────────────────────────────

class VideoScrubWidget(QWidget):
    """Thumbnail vidéo + slider de scrubbing + timecode.

    Émet `position_changed(float)` en secondes quand l'utilisateur relâche
    le slider ou clique dessus.
    """

    position_changed = pyqtSignal(float)   # secondes

    _THUMB_MIN_H = 170

    def __init__(self, side_label: str, parent=None):
        super().__init__(parent)
        self._video_path   = ""
        self._total_frames = 0
        self._fps          = 30.0
        self._in_frame     = 0
        self._out_frame    = 0   # 0 = non défini = fin de vidéo

        # Debounce : charge la frame 80 ms après le dernier déplacement du slider
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(80)
        self._debounce.timeout.connect(self._load_pending)
        self._pending_fidx = 0

        layout = QVBoxLayout(self)
        layout.setSpacing(3)
        layout.setContentsMargins(0, 0, 0, 0)

        # ── Thumbnail (avec ROI dessinable) ──────────────────────────────────
        self._thumb = _RoiThumb(side_label)
        self._thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._thumb.setStyleSheet(
            "background:#1a1a2e; border:1px solid #444; color:#555;")
        self._thumb.setMinimumHeight(self._THUMB_MIN_H)
        layout.addWidget(self._thumb, stretch=1)

        # ── Slider ───────────────────────────────────────────────────────────
        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.setRange(0, 0)
        self._slider.setToolTip("Faites glisser pour naviguer dans la vidéo")
        self._slider.sliderMoved.connect(self._on_moved)
        self._slider.sliderReleased.connect(self._on_released)
        layout.addWidget(self._slider)

        # ── Barre In/Out ─────────────────────────────────────────────────────
        self._inout_bar = _InOutBar()
        layout.addWidget(self._inout_bar)

        # ── Ligne position courante (éditable) ───────────────────────────────
        cur_row = QHBoxLayout()
        cur_row.setContentsMargins(0, 0, 0, 0)
        cur_row.setSpacing(4)

        cur_lbl = QLabel("Frame :")
        cur_lbl.setStyleSheet("color:#64748b; font-size:10px;")
        cur_row.addWidget(cur_lbl)

        self._edit_cur = QLineEdit("--")
        self._edit_cur.setFont(QFont("Courier New", 9))
        self._edit_cur.setFixedWidth(70)
        self._edit_cur.setFixedHeight(20)
        self._edit_cur.setAlignment(Qt.AlignmentFlag.AlignRight)
        self._edit_cur.setStyleSheet(
            "QLineEdit{color:#90caf9; background:#1e293b; border:1px solid #334155;"
            " border-radius:3px; padding:0 3px;}"
            "QLineEdit:focus{border-color:#60a5fa;}")
        self._edit_cur.setToolTip("Numéro de frame - tapez un nombre et appuyez sur Entrée")
        self._edit_cur.returnPressed.connect(self._on_cur_typed)
        cur_row.addWidget(self._edit_cur)

        self._tc_cur = QLabel("--:--.--")
        self._tc_cur.setFont(QFont("Courier New", 9))
        self._tc_cur.setStyleSheet("color:#475569; font-size:9px;")
        cur_row.addWidget(self._tc_cur)

        cur_row.addStretch()

        self._tc_dur = QLabel("/ 0  --:--.--")
        self._tc_dur.setFont(QFont("Courier New", 9))
        self._tc_dur.setStyleSheet("color:#334155; font-size:9px;")
        cur_row.addWidget(self._tc_dur)

        layout.addLayout(cur_row)

        # ── Ligne In / Out (éditables) ────────────────────────────────────────
        inout_row = QHBoxLayout()
        inout_row.setContentsMargins(0, 0, 0, 0)
        inout_row.setSpacing(4)

        btn_in = QPushButton("◀ In")
        btn_in.setFixedWidth(48)
        btn_in.setFixedHeight(20)
        btn_in.setToolTip("Marquer la position courante comme point In")
        btn_in.setStyleSheet(
            "QPushButton{background:#78350f;color:#fde68a;border-radius:3px;font-size:10px;}"
            "QPushButton:hover{background:#92400e;}")
        btn_in.clicked.connect(self._set_in)
        inout_row.addWidget(btn_in)

        self._edit_in = QLineEdit("--")
        self._edit_in.setFont(QFont("Courier New", 9))
        self._edit_in.setFixedWidth(70)
        self._edit_in.setFixedHeight(20)
        self._edit_in.setAlignment(Qt.AlignmentFlag.AlignRight)
        self._edit_in.setStyleSheet(
            "QLineEdit{color:#f59e0b; background:#1e293b; border:1px solid #78350f;"
            " border-radius:3px; padding:0 3px;}"
            "QLineEdit:focus{border-color:#f59e0b;}")
        self._edit_in.setToolTip("Frame de début - tapez un numéro et appuyez sur Entrée")
        self._edit_in.returnPressed.connect(self._on_in_typed)
        inout_row.addWidget(self._edit_in)

        self._tc_in = QLabel("--:--.--")
        self._tc_in.setFont(QFont("Courier New", 9))
        self._tc_in.setStyleSheet("color:#78350f; font-size:9px;")
        inout_row.addWidget(self._tc_in)

        inout_row.addStretch()

        self._tc_out = QLabel("--:--.--")
        self._tc_out.setFont(QFont("Courier New", 9))
        self._tc_out.setStyleSheet("color:#78350f; font-size:9px;")
        inout_row.addWidget(self._tc_out)

        self._edit_out = QLineEdit("--")
        self._edit_out.setFont(QFont("Courier New", 9))
        self._edit_out.setFixedWidth(70)
        self._edit_out.setFixedHeight(20)
        self._edit_out.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self._edit_out.setStyleSheet(
            "QLineEdit{color:#f59e0b; background:#1e293b; border:1px solid #78350f;"
            " border-radius:3px; padding:0 3px;}"
            "QLineEdit:focus{border-color:#f59e0b;}")
        self._edit_out.setToolTip("Frame de fin - tapez un numéro et appuyez sur Entrée")
        self._edit_out.returnPressed.connect(self._on_out_typed)
        inout_row.addWidget(self._edit_out)

        btn_out = QPushButton("Out ▶")
        btn_out.setFixedWidth(48)
        btn_out.setFixedHeight(20)
        btn_out.setToolTip("Marquer la position courante comme point Out")
        btn_out.setStyleSheet(
            "QPushButton{background:#78350f;color:#fde68a;border-radius:3px;font-size:10px;}"
            "QPushButton:hover{background:#92400e;}")
        btn_out.clicked.connect(self._set_out)
        inout_row.addWidget(btn_out)

        btn_reset = QPushButton("✕")
        btn_reset.setFixedWidth(24)
        btn_reset.setFixedHeight(20)
        btn_reset.setToolTip("Réinitialiser les points In/Out")
        btn_reset.setStyleSheet(
            "QPushButton{background:#3f3f46;color:#a1a1aa;border-radius:3px;font-size:10px;}"
            "QPushButton:hover{background:#52525b;}")
        btn_reset.clicked.connect(self._reset_inout)
        inout_row.addWidget(btn_reset)

        layout.addLayout(inout_row)

    # ── API publique ──────────────────────────────────────────────────────────

    def load_video(self, path: str):
        self._video_path   = path
        self._in_frame     = 0
        self._out_frame    = 0
        cap = cv.VideoCapture(path)
        self._total_frames = int(cap.get(cv.CAP_PROP_FRAME_COUNT))
        self._fps          = cap.get(cv.CAP_PROP_FPS) or 30.0
        cap.release()
        self._slider.setRange(0, max(0, self._total_frames - 1))
        self._slider.setValue(0)
        self._tc_dur.setText(
            f"/ {self._total_frames}  {self._fmt(self._total_frames / self._fps)}")
        self._edit_in.setText("--")
        self._tc_in.setText("--:--.--")
        self._edit_out.setText("--")
        self._tc_out.setText("--:--.--")
        self._refresh_bar(0)
        self._load_frame_at(0)

    def get_position_s(self) -> float:
        return self._slider.value() / max(self._fps, 1.0)

    # ── Cadre de détection (ROI) ──────────────────────────────────────────────

    def set_roi_mode(self, on: bool):
        """Active/désactive le tracé du cadre de détection à la souris."""
        self._thumb.set_draw_mode(on)

    def get_roi(self):
        """ROI normalisée (x, y, w, h) dans [0,1], ou None."""
        return self._thumb.get_roi()

    def set_roi(self, roi):
        self._thumb.set_roi(roi)

    def clear_roi(self):
        self._thumb.clear_roi()

    @property
    def roi_thumb(self) -> '_RoiThumb':
        return self._thumb

    def set_position_s(self, t: float):
        fidx = int(t * self._fps)
        self._slider.setValue(max(0, min(fidx, self._total_frames - 1)))
        self._refresh_bar(fidx)
        self._load_frame_at(fidx)

    # ── Accès In/Out ──────────────────────────────────────────────────────────

    def get_in_frame(self) -> int:
        return self._in_frame

    def get_out_frame(self) -> int:
        """Retourne le frame Out, ou le dernier frame si non défini."""
        return self._out_frame if self._out_frame > 0 else max(0, self._total_frames - 1)

    def set_in_frame(self, f: int):
        self._in_frame = max(0, f)
        self._edit_in.setText(str(self._in_frame))
        self._tc_in.setText(self._fmt(self._in_frame / max(self._fps, 1)))
        self._refresh_bar(self._slider.value())

    def set_out_frame(self, f: int):
        self._out_frame = max(0, f)
        self._edit_out.setText(str(self._out_frame))
        self._tc_out.setText(self._fmt(self._out_frame / max(self._fps, 1)))
        self._refresh_bar(self._slider.value())

    def show_frame_result(self, frame: np.ndarray, overlay_text: str = ""):
        """Affiche une frame externe (résultat de détection)."""
        if frame is None:
            return
        frm = frame.copy()
        if overlay_text:
            cv.putText(frm, overlay_text, (10, 30),
                       cv.FONT_HERSHEY_SIMPLEX, 0.85, (0, 255, 80), 2, cv.LINE_AA)
        self._display(frm)

    # ── Slots In/Out ─────────────────────────────────────────────────────────

    def _set_in(self):
        self.set_in_frame(self._slider.value())
        if self._out_frame > 0 and self._out_frame < self._in_frame:
            self.set_out_frame(self._in_frame)

    def _set_out(self):
        self.set_out_frame(self._slider.value())
        if self._out_frame < self._in_frame:
            self.set_in_frame(self._out_frame)

    def _reset_inout(self):
        self._in_frame  = 0
        self._out_frame = 0
        self._edit_in.setText("--")
        self._tc_in.setText("--:--.--")
        self._edit_out.setText("--")
        self._tc_out.setText("--:--.--")
        self._refresh_bar(self._slider.value())

    # ── Slots saisie clavier ──────────────────────────────────────────────────

    def _on_cur_typed(self):
        """L'utilisateur a tapé un numéro de frame dans le champ courant."""
        try:
            f = int(self._edit_cur.text().strip())
            f = max(0, min(f, self._total_frames - 1))
            self._slider.setValue(f)
            self._refresh_bar(f)
            self._load_frame_at(f)
            self.position_changed.emit(f / max(self._fps, 1))
        except ValueError:
            self._edit_cur.setText(str(self._slider.value()))

    def _on_in_typed(self):
        """L'utilisateur a tapé un numéro de frame dans le champ In."""
        try:
            f = int(self._edit_in.text().strip())
            self.set_in_frame(f)
            if self._out_frame > 0 and self._out_frame < self._in_frame:
                self.set_out_frame(self._in_frame)
        except ValueError:
            self._edit_in.setText(str(self._in_frame) if self._in_frame else "--")

    def _on_out_typed(self):
        """L'utilisateur a tapé un numéro de frame dans le champ Out."""
        try:
            f = int(self._edit_out.text().strip())
            self.set_out_frame(f)
            if self._out_frame < self._in_frame:
                self.set_in_frame(self._out_frame)
        except ValueError:
            self._edit_out.setText(str(self._out_frame) if self._out_frame else "--")

    def _refresh_bar(self, cur_f: int):
        total = max(self._total_frames, 1)
        in_frac  = self._in_frame / total
        out_frac = (self._out_frame / total) if self._out_frame > 0 else 1.0
        self._inout_bar.update_state(in_frac, out_frac, cur_f / total)

    # ── Slots internes ────────────────────────────────────────────────────────

    def _on_moved(self, value: int):
        self._pending_fidx = value
        self._edit_cur.setText(str(value))
        self._tc_cur.setText(self._fmt(value / max(self._fps, 1)))
        self._refresh_bar(value)
        self._debounce.start()

    def _on_released(self):
        self._debounce.stop()
        fidx = self._slider.value()
        self._load_frame_at(fidx)
        self.position_changed.emit(fidx / self._fps)

    def _load_pending(self):
        self._load_frame_at(self._pending_fidx)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _load_frame_at(self, fidx: int):
        if not self._video_path:
            return
        cap = cv.VideoCapture(self._video_path)
        cap.set(cv.CAP_PROP_POS_FRAMES, fidx)
        ret, frame = cap.read()
        cap.release()
        if ret:
            t_s = fidx / max(self._fps, 1)
            self._edit_cur.setText(str(fidx))
            self._tc_cur.setText(self._fmt(t_s))
            cv.putText(frame, f"{fidx}  {self._fmt(t_s)}",
                       (10, frame.shape[0] - 12),
                       cv.FONT_HERSHEY_SIMPLEX, 0.65, (0, 200, 255), 2, cv.LINE_AA)
            self._display(frame)

    def _display(self, frame: np.ndarray):
        pix = _cv_to_pixmap(frame)
        self._thumb.setPixmap(
            pix.scaled(self._thumb.width(), self._thumb.height(),
                       Qt.AspectRatioMode.KeepAspectRatio,
                       Qt.TransformationMode.SmoothTransformation))

    @staticmethod
    def _fmt(t_s: float) -> str:
        t_s  = max(0.0, t_s)
        m    = int(t_s // 60)
        s    = int(t_s % 60)
        frac = int((t_s % 1) * 100)
        return f"{m:02d}:{s:02d}.{frac:02d}"


# ── Onglet Synchronisation ────────────────────────────────────────────────────

class SyncTab(QWidget):
    """Onglet 0 - Détection du flash et calcul du décalage de synchronisation."""

    # Émis quand l'utilisateur veut passer à la calibration.
    # Transporte (left_path, right_path).
    go_to_calib = pyqtSignal(str, str)

    def __init__(self):
        super().__init__()
        self._left_video  = ""
        self._right_video = ""

        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.setContentsMargins(12, 12, 12, 12)

        # ── Explication ──────────────────────────────────────────────────────
        info = QLabel(
            "Filmez un flash au début de chaque vidéo. "
            "Indiquez la position approximative du flash, cliquez Preview pour vérifier, "
            "puis Detect - la recherche se fait uniquement dans la fenêtre indiquée.")
        info.setWordWrap(True)
        info.setStyleSheet("color:#a0c4e8; font-style:italic; padding:4px;")
        layout.addWidget(info)

        # ── Sélecteurs vidéo + scrubbers ─────────────────────────────────────
        cameras_row = QHBoxLayout()
        cameras_row.setSpacing(12)

        for side, attr in [('Gauche', 'left'), ('Droite', 'right')]:
            col = QVBoxLayout()
            col.setSpacing(4)

            # Sélecteur fichier
            sel_row = QHBoxLayout()
            btn_sel = QPushButton(f"Vidéo {side}")
            btn_sel.setFixedWidth(140)
            btn_sel.setToolTip(
                f"Fichier de la caméra {side.lower()} - "
                "l'assignation ne dépend pas du nom du dossier.")
            btn_sel.clicked.connect(lambda _, s=attr: self._pick_video(s))
            lbl = QLabel("No file selected")
            lbl.setWordWrap(True)
            sel_row.addWidget(btn_sel)
            sel_row.addWidget(lbl, stretch=1)
            col.addLayout(sel_row)
            setattr(self, f'_lbl_{attr}', lbl)

            # VideoScrubWidget - slider + thumbnail + timecode
            scrub = VideoScrubWidget(f"Caméra {side}")
            scrub.position_changed.connect(
                lambda t, s=attr: self._on_scrub_released(s, t))
            col.addWidget(scrub, stretch=1)
            setattr(self, f'_scrub_{attr}', scrub)

            cameras_row.addLayout(col, stretch=1)

        layout.addLayout(cameras_row)

        # Persistance auto de la ROI dès qu'un cadre est tracé/effacé
        self._scrub_left.roi_thumb.roi_changed.connect(self._on_roi_changed)
        self._scrub_right.roi_thumb.roi_changed.connect(self._on_roi_changed)

        # ── Fenêtre ± + bouton détection ──────────────────────────────────────
        detect_row = QHBoxLayout()
        detect_row.addWidget(QLabel(
            "Placez le curseur près du flash dans chaque vidéo, puis :"))
        detect_row.addSpacing(12)
        detect_row.addWidget(QLabel("Fenêtre ±"))
        self.spin_window = QDoubleSpinBox()
        self.spin_window.setRange(0.5, 120.0)
        self.spin_window.setValue(5.0)
        self.spin_window.setDecimals(1)
        self.spin_window.setSingleStep(0.5)
        self.spin_window.setFixedWidth(65)
        self.spin_window.setToolTip(
            "Plage (en secondes) autour de la position du curseur où chercher le flash")
        detect_row.addWidget(self.spin_window)
        detect_row.addWidget(QLabel("s"))
        detect_row.addSpacing(12)

        # ── Cadre de détection (ROI) ──────────────────────────────────────────
        self.chk_roi = QCheckBox("Cadre de détection")
        self.chk_roi.setToolTip(
            "Limite l'analyse de luminosité à un cadre dessiné sur chaque aperçu.\n"
            "Activez, puis tracez un rectangle à la souris autour de la zone du "
            "flash sur la vidéo gauche et la vidéo droite.")
        self.chk_roi.toggled.connect(self._on_roi_toggled)
        detect_row.addWidget(self.chk_roi)

        self.btn_clear_roi = QPushButton("✕ ROI")
        self.btn_clear_roi.setFixedHeight(28)
        self.btn_clear_roi.setToolTip("Effacer les cadres de détection")
        self.btn_clear_roi.clicked.connect(self._clear_rois)
        self.btn_clear_roi.setEnabled(False)
        detect_row.addWidget(self.btn_clear_roi)
        detect_row.addSpacing(12)

        self.btn_detect = QPushButton("Detect Flash & Compute Sync Offset")
        self.btn_detect.setFixedHeight(34)
        self.btn_detect.clicked.connect(self._run)
        detect_row.addWidget(self.btn_detect)
        detect_row.addStretch()
        layout.addLayout(detect_row)

        # ── Progress + log ────────────────────────────────────────────────────
        self.progress = QProgressBar()
        self.progress.setValue(0)
        layout.addWidget(self.progress)

        self.log_area = QTextEdit()
        self.log_area.setReadOnly(True)
        self.log_area.setFont(QFont("Courier New", 9))
        self.log_area.setMaximumHeight(100)
        layout.addWidget(self.log_area)

        # ── Résultat + courbes ────────────────────────────────────────────────
        self.result_label = QLabel()
        self.result_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.result_label.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        self.result_label.setStyleSheet("color:#4ade80; padding:4px;")
        self.result_label.hide()
        layout.addWidget(self.result_label)

        # ── Offset manuel ─────────────────────────────────────────────────────
        manual_row = QHBoxLayout()
        sep = QLabel("── Offset manuel")
        sep.setStyleSheet("color:#555; font-size:10px;")
        manual_row.addWidget(sep)
        manual_row.addSpacing(8)
        manual_row.addWidget(QLabel("Décalage (frames) :"))
        self.spin_manual = QSpinBox()
        self.spin_manual.setRange(-100000, 100000)
        self.spin_manual.setValue(0)
        self.spin_manual.setFixedWidth(90)
        self.spin_manual.setToolTip(
            "+ = gauche en avance  |  − = droite en avance  |  0 = synchrones")
        manual_row.addWidget(self.spin_manual)
        lbl_hint = QLabel("(+ gauche en avance  |  − droite en avance  |  0 = synchro)")
        lbl_hint.setStyleSheet("color:#666; font-size:10px;")
        manual_row.addWidget(lbl_hint)
        manual_row.addStretch()
        btn_manual = QPushButton("Appliquer offset manuel")
        btn_manual.setFixedHeight(28)
        btn_manual.clicked.connect(self._apply_manual)
        manual_row.addWidget(btn_manual)
        layout.addLayout(manual_row)

        # ── Limites de rognage ────────────────────────────────────────────────
        trim_row = QHBoxLayout()
        sep2 = QLabel("── Limites vidéo (In / Out)")
        sep2.setStyleSheet("color:#555; font-size:10px;")
        trim_row.addWidget(sep2)
        trim_row.addStretch()
        lbl_trim_hint = QLabel(
            "Positionnez le curseur sur chaque scrubber et cliquez ◀ In / Out ▶ "
            "pour définir les bornes d'extraction des frames de calibration.")
        lbl_trim_hint.setStyleSheet("color:#666; font-size:10px;")
        lbl_trim_hint.setWordWrap(True)
        trim_row.addWidget(lbl_trim_hint, stretch=1)
        btn_save_trim = QPushButton("💾  Sauvegarder limites In/Out")
        btn_save_trim.setFixedHeight(28)
        btn_save_trim.setToolTip(
            "Sauvegarde les points In/Out des deux scrubbers dans trim_frames.npy")
        btn_save_trim.clicked.connect(self._save_trim)
        trim_row.addWidget(btn_save_trim)
        layout.addLayout(trim_row)

        # ── Bouton Prochaine étape ────────────────────────────────────────────
        next_row = QHBoxLayout()
        next_row.addStretch()
        self._btn_next = QPushButton("Passer à la calibration  →")
        self._btn_next.setFixedHeight(36)
        self._btn_next.setMinimumWidth(220)
        self._btn_next.setStyleSheet(
            "QPushButton{"
            "  background: qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            "    stop:0 #1d4ed8, stop:1 #2563eb);"
            "  color:#fff; border-radius:5px; font-weight:bold; font-size:12px;"
            "}"
            "QPushButton:hover{background:#2563eb;}"
            "QPushButton:disabled{background:#374151; color:#6b7280;}")
        self._btn_next.setToolTip(
            "Transfère les vidéos, la sync et les limites In/Out vers l'onglet Calibration")
        self._btn_next.clicked.connect(self._go_next)
        next_row.addWidget(self._btn_next)
        layout.addLayout(next_row)

        self.brightness_label = QLabel("Les courbes de luminosité apparaîtront ici.")
        self.brightness_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.brightness_label.setStyleSheet(
            "background:#1a1a2e; border:1px solid #444; color:#555;")
        self.brightness_label.setMinimumHeight(90)
        layout.addWidget(self.brightness_label)

        self._try_restore()

    # ── Navigation vers l'onglet suivant ─────────────────────────────────────

    def _go_next(self):
        if not self._left_video or not self._right_video:
            self.log_area.append(
                "[!] Chargez les deux vidéos avant de passer à la calibration.")
            return
        if not os.path.exists(cam_param('sync_frames.npy')):
            self.log_area.append(
                "[!] Aucune sync sauvegardée - lancez « Detect Flash » ou "
                "« Appliquer offset manuel » avant la calibration.")
            return
        self._save_trim(quiet=True)
        save_videos_txt(self._left_video, self._right_video)
        self.go_to_calib.emit(self._left_video, self._right_video)

    # ── Restauration ─────────────────────────────────────────────────────────

    def _try_restore(self):
        sync_path = cam_param('sync_frames.npy')
        if not os.path.exists(sync_path):
            self.log_area.append(
                "Aucune sync enregistrée - chargez les vidéos puis « Detect Flash ».")
            return
        try:
            frames = np.load(sync_path)
            lf, rf = int(frames[0]), int(frames[1])
            off = lf - rf
            self.spin_manual.setValue(off)
            msg = (f"✓ Sync chargée depuis disque - Gauche frame {lf}  |  "
                   f"Droite frame {rf}  |  Décalage {off:+d} frames")
            self.log_area.append(msg)
            self.result_label.setText(msg)
            self.result_label.setStyleSheet("color:#4ade80; padding:4px;")
            self.result_label.show()
            videos_txt = cam_param('videos.txt')
            if os.path.exists(videos_txt):
                with open(videos_txt, encoding='utf-8') as f:
                    lines = [l.strip() for l in f.readlines() if l.strip()]
                if len(lines) >= 2:
                    left_p, right_p = lines[0], lines[1]
                    if os.path.isfile(left_p) and os.path.isfile(right_p):
                        self._left_video  = left_p
                        self._right_video = right_p
                        self._lbl_left.setText(left_p)
                        self._lbl_right.setText(right_p)
                        self._scrub_left.load_video(left_p)
                        self._scrub_right.load_video(right_p)
                        cap = cv.VideoCapture(left_p)
                        fps = cap.get(cv.CAP_PROP_FPS) or 30.0
                        cap.release()
                        self._scrub_left.set_position_s(lf / fps)
                        self._scrub_right.set_position_s(rf / fps)
                    else:
                        self.log_area.append(
                            "  ⚠ Vidéos de videos.txt introuvables - resélectionnez les fichiers.")
                    trim_path = cam_param('trim_frames.npy')
                    if os.path.exists(trim_path):
                        try:
                            trim = np.load(trim_path)
                            li, lo, ri, ro = (int(trim[0]), int(trim[1]),
                                              int(trim[2]), int(trim[3]))
                            n_l = self._scrub_left._total_frames
                            n_r = self._scrub_right._total_frames
                            if (li >= n_l or lo >= n_l or ri >= n_r or ro >= n_r
                                    or lo < li or ro < ri):
                                clear_trim_frames()
                                self.log_area.append(
                                    "  ⚠ trim_frames.npy ignoré (hors durée ou "
                                    "incohérent) - redéfinissez In/Out si besoin.")
                            else:
                                self._scrub_left.set_in_frame(li)
                                self._scrub_left.set_out_frame(lo)
                                self._scrub_right.set_in_frame(ri)
                                self._scrub_right.set_out_frame(ro)
                                self.log_area.append(
                                    f"  Limites In/Out (calibration) - "
                                    f"Gauche frame {li}→{lo}  |  "
                                    f"Droite frame {ri}→{ro}")
                        except Exception:
                            pass
                    self._restore_rois()
            else:
                self.log_area.append(
                    "  Sync OK mais videos.txt absent - sélectionnez les deux vidéos.")
        except Exception as exc:
            self.log_area.append(f"  [!] Impossible de charger la sync : {exc}")

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _pick_video(self, side: str):
        path, _ = QFileDialog.getOpenFileName(
            self, f"Vidéo {side}", "",
            "Videos (*.mp4 *.avi *.mov *.mkv *.MOV);;All Files (*)")
        if not path:
            return
        clear_trim_frames()
        setattr(self, f'_{side}_video', path)
        getattr(self, f'_lbl_{side}').setText(path)
        getattr(self, f'_scrub_{side}').load_video(path)
        side_fr = 'gauche' if side == 'left' else 'droite'
        self.log_area.append(f"✓ Caméra {side_fr} : {path}")
        if self._left_video and self._right_video:
            save_videos_txt(self._left_video, self._right_video)
            self.log_area.append(
                "  → Assignation enregistrée (videos.txt) - "
                "ligne 1 = gauche, ligne 2 = droite")

    def _on_scrub_released(self, side: str, t_s: float):
        pass  # position lue depuis le scrubber au moment du Detect

    # ── Cadre de détection (ROI) ──────────────────────────────────────────────

    def _on_roi_toggled(self, on: bool):
        self._scrub_left.set_roi_mode(on)
        self._scrub_right.set_roi_mode(on)
        self.btn_clear_roi.setEnabled(on)
        if on:
            self.log_area.append(
                "Mode cadre activé - tracez un rectangle autour du flash sur "
                "chaque aperçu (gauche et droite).")

    def _on_roi_changed(self):
        save_flash_roi(self._scrub_left.get_roi(), self._scrub_right.get_roi())

    def _clear_rois(self):
        self._scrub_left.clear_roi()
        self._scrub_right.clear_roi()
        clear_flash_roi()
        self.log_area.append("Cadres de détection effacés - image entière utilisée.")

    def _restore_rois(self):
        """Recharge les cadres de détection sauvegardés (flash_roi.npy)."""
        left_roi, right_roi = load_flash_roi()
        if left_roi is None and right_roi is None:
            return
        self._scrub_left.set_roi(left_roi)
        self._scrub_right.set_roi(right_roi)
        self.chk_roi.blockSignals(True)
        self.chk_roi.setChecked(True)
        self.chk_roi.blockSignals(False)
        self._scrub_left.set_roi_mode(True)
        self._scrub_right.set_roi_mode(True)
        self.btn_clear_roi.setEnabled(True)
        self.log_area.append("  Cadres de détection (ROI) restaurés depuis le disque.")

    def _save_trim(self, quiet: bool = False):
        """Sauvegarde les points In/Out des deux scrubbers dans trim_frames.npy."""
        li = self._scrub_left.get_in_frame()
        lo = self._scrub_left.get_out_frame()
        ri = self._scrub_right.get_in_frame()
        ro = self._scrub_right.get_out_frame()
        ensure_cam_params_dir()
        np.save(cam_param('trim_frames.npy'),
                np.array([li, lo, ri, ro], dtype=int))
        fps_l = self._scrub_left._fps
        fps_r = self._scrub_right._fps
        def fmt(f, fps):
            t = f / max(fps, 1)
            return f"{int(t//60):02d}:{int(t%60):02d}.{int((t%1)*100):02d}"
        msg = (f"✓ Limites sauvegardées  -  "
               f"Gauche : {fmt(li, fps_l)} → {fmt(lo, fps_l)}  |  "
               f"Droite : {fmt(ri, fps_r)} → {fmt(ro, fps_r)}")
        if not quiet:
            self.log_area.append(msg)
            self.result_label.setText(msg)
            self.result_label.setStyleSheet("color:#818cf8; padding:4px;")
            self.result_label.show()

    def _apply_manual(self):
        """Sauvegarde un offset entré manuellement sans détection automatique."""
        offset = self.spin_manual.value()
        lf = max(0, offset)
        rf = max(0, -offset)
        ensure_cam_params_dir()
        np.save(cam_param('sync_frames.npy'), np.array([lf, rf], dtype=int))
        if self._left_video and self._right_video:
            save_videos_txt(self._left_video, self._right_video)
        msg = (f"✓ Offset manuel appliqué : {offset:+d} frames  "
               f"(Gauche démarre à frame {lf}, Droite à frame {rf})")
        self.log_area.append(msg)
        self.result_label.setText(msg)
        self.result_label.setStyleSheet("color:#fb923c; padding:4px;")
        self.result_label.show()

    # ── Lancement détection ──────────────────────────────────────────────────

    def _run(self):
        if not self._left_video or not self._right_video:
            self.log_area.append("[ERROR] Sélectionnez les deux vidéos d'abord.")
            return
        self.btn_detect.setEnabled(False)
        self.progress.setValue(0)
        self.log_area.clear()
        self.log_area.append("Détection du flash en cours…\n")
        self.result_label.hide()

        self._thread = QThread()
        self._worker = SyncWorker()
        self._worker.left_video     = self._left_video
        self._worker.right_video    = self._right_video
        self._worker.left_center_s  = self._scrub_left.get_position_s()
        self._worker.right_center_s = self._scrub_right.get_position_s()
        self._worker.window_s       = self.spin_window.value()
        use_roi = self.chk_roi.isChecked()
        self._worker.left_roi       = self._scrub_left.get_roi()  if use_roi else None
        self._worker.right_roi      = self._scrub_right.get_roi() if use_roi else None
        if use_roi:
            if self._worker.left_roi is None or self._worker.right_roi is None:
                self.log_area.append(
                    "  ⚠ Cadre manquant sur une vidéo - image entière utilisée "
                    "pour ce côté.")
        self._worker.moveToThread(self._thread)

        self._thread.started.connect(self._worker.run)
        self._worker.log.connect(self.log_area.append)
        self._worker.progress.connect(self.progress.setValue)
        self._worker.error.connect(self.log_area.append)
        self._worker.result_ready.connect(self._on_result)
        self._worker.finished.connect(self._thread.quit)
        self._worker.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.finished.connect(lambda: self.btn_detect.setEnabled(True))

        self._thread.start()

    def _on_result(self, data: dict):
        lf  = data['left_flash_frame']
        rf  = data['right_flash_frame']
        off = data['offset']
        self.result_label.setText(
            f"✓  Flash détecté  -  Gauche : frame {lf}   |   "
            f"Droite : frame {rf}   |   Décalage : {off:+d} frames")
        self.result_label.setStyleSheet("color:#4ade80; padding:4px;")
        self.result_label.show()
        # Remplir le spinbox manuel avec le résultat détecté (pour ajustement fin)
        self.spin_manual.setValue(off)

        bimg = data.get('brightness_img')
        if bimg is not None:
            pix = _cv_to_pixmap(bimg)
            self.brightness_label.setPixmap(
                pix.scaled(self.brightness_label.width(),
                            self.brightness_label.height(),
                            Qt.AspectRatioMode.KeepAspectRatio,
                            Qt.TransformationMode.SmoothTransformation))

        if data.get('left_frame') is not None:
            self._scrub_left.show_frame_result(
                data['left_frame'], f"Flash - frame {lf}")

        if data.get('right_frame') is not None:
            self._scrub_right.show_frame_result(
                data['right_frame'], f"Flash - frame {rf}")


# ── Worker calibration ─────────────────────────────────────────────────────────

def _subsample_detection_cache(cache: list, n: int) -> list:
    if len(cache) <= n:
        return cache
    step = len(cache) / n
    return [cache[int(i * step)] for i in range(n)]


def _subsample_detection_pairs(pairs: list, n: int) -> list:
    if len(pairs) <= n:
        return pairs
    step = len(pairs) / n
    return [pairs[int(i * step)] for i in range(n)]


def _harmonize_identical_camera_focals(mtx1, mtx2, log_fn=None):
    """Capteurs identiques : moyenne fx/fy G+D avant stéréo (améliore |ΔY| rectifié)."""
    fx_l, fx_r = float(mtx1[0, 0]), float(mtx2[0, 0])
    fx = 0.5 * (fx_l + fx_r)
    m1 = np.asarray(mtx1, dtype=np.float64).copy()
    m2 = np.asarray(mtx2, dtype=np.float64).copy()
    m1[0, 0] = m1[1, 1] = fx
    m2[0, 0] = m2[1, 1] = fx
    if log_fn and abs(fx_l - fx_r) > 1.0:
        log_fn(
            f"  Focale harmonisée G+D : {fx:.1f} px "
            f"(étaient {fx_l:.0f} / {fx_r:.0f} - capteurs identiques)")
    return m1, m2


def _select_stereo_pairs_quality(pairs: list, n: int,
                                 min_common: int = STEREO_MIN_COMMON_CORNERS,
                                 log_fn=None) -> list:
    """Sélection stéréo : filtre qualité (IDs communs) + meilleure paire par bin temporel."""
    if n <= 0 or not pairs:
        return []
    scored = []
    for le, re in pairs:
        nc = _count_common_charuco_ids(le, re)
        if nc < min_common:
            continue
        fidx = _frame_num_from_path(le['path'])
        scored.append((nc, fidx, le, re))
    if log_fn and len(scored) < len(pairs):
        log_fn(
            f"  Filtre stéréo (< {min_common} IDs communs) : "
            f"{len(pairs) - len(scored)} paire(s) ignorée(s) "
            f"→ {len(scored)} candidates")
    if not scored:
        if min_common > MIN_CALIB_CORNERS:
            return _select_stereo_pairs_quality(
                pairs, n, min_common=MIN_CALIB_CORNERS, log_fn=log_fn)
        return (_subsample_detection_pairs(pairs, n)
                if len(pairs) > n else list(pairs))
    if len(scored) <= n:
        scored.sort(key=lambda x: x[1])
        return [(le, re) for _, _, le, re in scored]
    scored.sort(key=lambda x: x[1])
    n_bins = n
    t_min, t_max = scored[0][1], scored[-1][1]
    span = max(1, t_max - t_min)
    bins: list[list] = [[] for _ in range(n_bins)]
    for item in scored:
        nc, fidx, le, re = item
        bin_i = min(n_bins - 1, int((fidx - t_min) * n_bins / (span + 1)))
        bins[bin_i].append(item)
    selected = []
    used_ids = set()
    for b in bins:
        if not b:
            continue
        nc, fidx, le, re = max(b, key=lambda x: x[0])
        selected.append((le, re))
        used_ids.add(id(le))
    if len(selected) < n:
        for nc, fidx, le, re in sorted(scored, key=lambda x: -x[0]):
            if len(selected) >= n:
                break
            if id(le) in used_ids:
                continue
            selected.append((le, re))
            used_ids.add(id(le))
    selected.sort(key=lambda p: _frame_num_from_path(p[0]['path']))
    if log_fn:
        commons = [_count_common_charuco_ids(le, re) for le, re in selected]
        med = int(np.median(commons))
        log_fn(
            f"  Sélection stéréo qualité : {len(selected)} paires "
            f"(IDs communs min/méd/max = "
            f"{min(commons)}/{med}/{max(commons)})")
    return selected[:n]


def _save_charuco_detection_entry(frame, fidx, folder, board, detector, det_cache,
                                  detect_fn=_detect_charuco):
    gray = cv.cvtColor(frame, cv.COLOR_BGR2GRAY)
    charuco_corners, charuco_ids, _ = detect_fn(gray, board, detector)
    if charuco_ids is None or len(charuco_ids) < MIN_CALIB_CORNERS:
        return None
    obj_pts, img_pts = board.matchImagePoints(charuco_corners, charuco_ids)
    if obj_pts is None or _n_calib_points(obj_pts) < MIN_CALIB_CORNERS:
        return None
    name = os.path.join(folder, f"frame_{fidx:06d}.jpg")
    cv.imwrite(name, frame, [cv.IMWRITE_JPEG_QUALITY, 95])
    fh, fw = frame.shape[:2]
    entry = {
        'path':        name,
        'corners':     charuco_corners.reshape(-1, 1, 2).astype(np.float32),
        'ids':         charuco_ids.copy(),
        'obj_pts':     obj_pts,
        'img_pts':     img_pts,
        'image_size': (fw, fh),
    }
    det_cache.append(entry)
    return entry


def _scan_stereo_coupled_v2(
        left_video, right_video,
        t0, t1, sync_delta,
        board_chk, detector_chk, aruco_detector_chk,
        tmp_left, tmp_right,
        left_cache, right_cache, coupled_pairs,
        scan_stride_idle: int,
        dense_window: int,
        dense_stride: int,
        cancelled_fn,
        log_fn,
        progress_fn,
        frame_preview_fn,
        prog_start: int = 10,
        prog_end: int = 55,
        scan_counts_fn=None) -> tuple[list, list, dict]:
    """Scan couplé G/D avec pas variable (stride idle + fenêtre dense après hit).

    En rafale (mire détectée), le pas passe à dense_stride (1 = chaque frame,
    3 = 1 frame sur 3) tout en gardant G/D couplés via le même grab/grab.
    """
    cap_l = cv.VideoCapture(left_video)
    cap_r = cv.VideoCapture(right_video)
    stats = {
        'frames_probed': 0,
        'frames_skipped_grab': 0,
        'scan_stride_idle': scan_stride_idle,
        'dense_window': dense_window,
        'dense_stride': dense_stride,
    }
    if not cap_l.isOpened() or not cap_r.isOpened():
        log_fn("  [ERREUR] Impossible d'ouvrir une des vidéos (scan v2)")
        cap_l.release()
        cap_r.release()
        return [], [], stats

    total_l_vid = int(cap_l.get(cv.CAP_PROP_FRAME_COUNT))
    total_r_vid = int(cap_r.get(cv.CAP_PROP_FRAME_COUNT))
    r0 = t0 + sync_delta
    ok_l = cap_l.set(cv.CAP_PROP_POS_FRAMES, t0)
    ok_r = cap_r.set(cv.CAP_PROP_POS_FRAMES, r0)
    log_fn(
        f"  Scan v2 couplé : G seek {t0}→{'OK' if ok_l else 'ÉCHEC'} "
        f"| D seek {r0}→{'OK' if ok_r else 'ÉCHEC'}  "
        f"(pas idle {scan_stride_idle}, rafale {dense_window} pas, "
        f"pas rafale {max(1, int(dense_stride))}, "
        f"offset sync {sync_delta:+d})")
    if t0 >= total_l_vid or r0 >= total_r_vid:
        log_fn(
            "  [ERREUR] Départ hors limites - vidéos différentes du sync ?")
        cap_l.release()
        cap_r.release()
        return [], [], stats

    def _save(frame, fidx, folder, cache):
        return _save_charuco_detection_entry(
            frame, fidx, folder, board_chk, detector_chk, cache,
            _detect_charuco_fast)

    left_paths, right_paths = [], []
    span = max(1, t1 - t0 + 1)
    t = t0
    dense_remaining = 0
    diag_aruco_l = diag_aruco_r = 0
    diag_checked = 0
    grab_fails = 0
    probed = 0
    stride_idle = max(1, int(scan_stride_idle))
    dense_win = max(1, int(dense_window))
    dense_st = max(1, int(dense_stride))
    t_scan_start = time.monotonic()
    _last_counts_emit = 0

    def _maybe_emit_scan_counts(force: bool = False) -> None:
        nonlocal _last_counts_emit
        if not scan_counts_fn:
            return
        n = len(left_cache)
        if not force and n == _last_counts_emit and probed % 20 != 0:
            return
        _last_counts_emit = n
        scan_counts_fn(len(left_cache), len(right_cache), len(coupled_pairs))

    _maybe_emit_scan_counts(force=True)

    while t <= t1:
        if cancelled_fn():
            log_fn(f"  ⛔ Scan v2 annulé à t={t} (G) / {t + sync_delta} (D).")
            break
        stride = dense_st if dense_remaining > 0 else stride_idle
        rt = t + sync_delta
        if not cap_l.grab() or not cap_r.grab():
            grab_fails += 1
            if grab_fails > 5:
                log_fn(
                    f"  [AVERTISSEMENT] grab() échoue à G{t}/D{rt} - fin de plage ?")
                break
            t += 1
            continue
        grab_fails = 0
        ok_l, frame_l = cap_l.retrieve()
        ok_r, frame_r = cap_r.retrieve()
        if not ok_l or frame_l is None or not ok_r or frame_r is None:
            t += stride
            continue
        probed += 1
        stats['frames_probed'] = probed

        if diag_checked < 20:
            gray_l = cv.cvtColor(frame_l, cv.COLOR_BGR2GRAY)
            gray_r = cv.cvtColor(frame_r, cv.COLOR_BGR2GRAY)
            _, _, na_l = _detect_charuco_fast(gray_l, board_chk, detector_chk)
            _, _, na_r = _detect_charuco_fast(gray_r, board_chk, detector_chk)
            if na_l > 0:
                diag_aruco_l += 1
            if na_r > 0:
                diag_aruco_r += 1
            if diag_checked == 0:
                raw_c, raw_ids, _ = aruco_detector_chk.detectMarkers(gray_l)
                ids_list = sorted(raw_ids.flatten().tolist()) if raw_ids is not None else []
                log_fn(
                    f"  Diag G{t}/D{rt}: ArUco G={na_l} D={na_r} marqueurs")
                log_fn(
                    f"  IDs G : {ids_list}  "
                    f"(min={min(ids_list) if ids_list else '?'}, "
                    f"max={max(ids_list) if ids_list else '?'})")
            diag_checked += 1

        lf = t
        rf = rt
        el = _save(frame_l, lf, tmp_left, left_cache)
        er = _save(frame_r, rf, tmp_right, right_cache)
        if el is not None:
            left_paths.append(el['path'])
        if er is not None:
            right_paths.append(er['path'])
        if (el is not None and er is not None
                and _count_common_charuco_ids(el, er) >= MIN_CALIB_CORNERS):
            coupled_pairs.append((el, er))

        if el is not None or er is not None:
            dense_remaining = dense_win
        elif dense_remaining > 0:
            dense_remaining -= 1

        if probed % 40 == 0:
            _maybe_emit_scan_counts(force=True)
            for side, frame, fidx, entry in (
                    ("Gauche", frame_l, lf, el),
                    ("Droite", frame_r, rf, er)):
                prev = frame.copy()
                n_ch = len(entry['ids']) if entry is not None else 0
                if entry is not None:
                    cv.aruco.drawDetectedCornersCharuco(
                        prev, entry['corners'], entry['ids'])
                else:
                    gray_p = cv.cvtColor(frame, cv.COLOR_BGR2GRAY)
                    cc_p, ci_p, _ = _detect_charuco_fast(
                        gray_p, board_chk, detector_chk)
                    if ci_p is not None and len(ci_p) > 0:
                        n_ch = len(ci_p)
                        cv.aruco.drawDetectedCornersCharuco(prev, cc_p, ci_p)
                cv.putText(prev,
                           f"{side} {fidx} | ChArUco={n_ch}",
                           (10, 30), cv.FONT_HERSHEY_SIMPLEX, 0.75,
                           (0, 255, 80) if n_ch >= MIN_CALIB_CORNERS else (0, 80, 255),
                           2, cv.LINE_AA)
                frame_preview_fn((prev, f"{side} - {fidx}/{t1}"))

        _maybe_emit_scan_counts()

        pct = prog_start + int((prog_end - prog_start) * probed / span)
        progress_fn(pct)

        next_t = t + stride
        if stride > 1:
            for skip_t in range(t + 1, min(next_t, t1 + 1)):
                if cancelled_fn():
                    break
                if cap_l.grab() and cap_r.grab():
                    stats['frames_skipped_grab'] += 1
                else:
                    grab_fails += 1
        t = next_t

    if diag_checked > 0 and diag_aruco_l == 0 and diag_aruco_r == 0:
        log_fn(
            f"  ⚠ Aucun marqueur ArUco sur les {diag_checked} premiers instants. "
            f"Vérifiez dictionnaire et visibilité du board "
            f"(G {t0}→{t1}, D {r0}→{t1 + sync_delta}).")
    stats['scan_duration_s'] = round(time.monotonic() - t_scan_start, 1)
    _maybe_emit_scan_counts(force=True)
    cap_l.release()
    cap_r.release()
    return left_paths, right_paths, stats


def _load_calib_timeline(log_fn, progress_fn, left_video, right_video):
    """Étape 1 : sync + trim + plage couplée (partagé moteur v2)."""
    log_fn("── Step 1: Extracting synchronized frames ──────────")
    sync_path = cam_param('sync_frames.npy')
    if os.path.exists(sync_path):
        sync_frames = np.load(sync_path)
        left_sync = int(sync_frames[0])
        right_sync = int(sync_frames[1])
        log_fn(
            f"  Sync offset chargé - Gauche démarre à frame {left_sync}, "
            f"Droite à frame {right_sync}  "
            f"(décalage {left_sync - right_sync:+d} frames)")
    else:
        left_sync = right_sync = 0
        log_fn(
            "  Aucun fichier sync_frames.npy - extraction depuis frame 0 "
            "(lancez l'onglet Sync d'abord pour une précision optimale)")

    cap_l = cv.VideoCapture(left_video)
    cap_r = cv.VideoCapture(right_video)
    total_l = int(cap_l.get(cv.CAP_PROP_FRAME_COUNT))
    total_r = int(cap_r.get(cv.CAP_PROP_FRAME_COUNT))
    cap_l.release()
    cap_r.release()

    trim_path = cam_param('trim_frames.npy')
    if os.path.exists(trim_path):
        trim = np.load(trim_path)
        left_in = int(trim[0])
        left_out = int(trim[1]) if int(trim[1]) > 0 else total_l - 1
        right_in = int(trim[2])
        right_out = int(trim[3]) if int(trim[3]) > 0 else total_r - 1
        log_fn(
            f"  Limites rognage chargées - "
            f"Gauche [{left_in}→{left_out}]  |  Droite [{right_in}→{right_out}]")
    else:
        left_in, left_out = 0, total_l - 1
        right_in, right_out = 0, total_r - 1

    sync_delta = right_sync - left_sync
    t_start = max(max(left_sync, left_in), right_in - sync_delta)
    t_end = min(left_out, right_out - sync_delta,
                 total_l - 1, total_r - 1 - sync_delta)
    if t_start > t_end:
        raise RuntimeError(
            "Plage commune vide après sync + rognage. "
            "Vérifiez sync_frames.npy et trim_frames.npy (onglet Sync).")

    available = t_end - t_start + 1
    right_at_start = t_start + sync_delta
    right_at_end = t_end + sync_delta
    log_fn(
        f"  Plage couplée : {available} instants  "
        f"(Gauche {t_start}→{t_end}  |  Droite {right_at_start}→{right_at_end}, "
        f"offset sync {sync_delta:+d})")
    if abs(left_sync - right_sync) > 60:
        log_fn(
            f"  ⚠ Décalage sync important ({left_sync - right_sync:+d} frames). "
            "Si le RMSE stéréo est mauvais, relancez l'onglet Sync.")
    progress_fn(5)
    return {
        'left_sync': left_sync, 'right_sync': right_sync,
        'sync_delta': sync_delta, 't_start': t_start, 't_end': t_end,
        'available': available,
    }


def _run_board_probe(worker, left_video, t_start, squares_x, squares_y,
                     square_length_mm, marker_length_mm, aruco_dict_id,
                     board_chk, detector_chk, aruco_detector_chk):
    """Auto-test config board sur 1ère frame (partagé moteur v2)."""
    worker.log.emit("  Auto-détection de la config du board sur 1ère frame…")
    cap_probe = cv.VideoCapture(left_video)
    total_probe = int(cap_probe.get(cv.CAP_PROP_FRAME_COUNT))
    if t_start >= total_probe:
        worker.log.emit(
            f"  [ERREUR] Frame de départ ({t_start}) > durée vidéo "
            f"({total_probe} frames).")
        cap_probe.release()
        worker.error.emit(
            f"Trim start ({t_start}) hors limites (vidéo = {total_probe} frames). "
            "Rechargez les vidéos dans l'onglet Sync.")
        return False
    cap_probe.set(cv.CAP_PROP_POS_FRAMES, t_start)
    ret_p, frame_p = cap_probe.read()
    cap_probe.release()
    if not (ret_p and frame_p is not None):
        worker.progress.emit(10)
        return True
    gray_p = cv.cvtColor(frame_p, cv.COLOR_BGR2GRAY)
    raw_c, raw_ids, _ = aruco_detector_chk.detectMarkers(gray_p)
    ids_list = sorted(raw_ids.flatten().tolist()) if raw_ids is not None else []
    n_raw = len(ids_list)
    worker.log.emit(f"  ArUco bruts : {n_raw} marqueurs - IDs : {ids_list}")

    # Config déjà correcte → skip la grille 3–13 × 3–9 (~80 detectBoard, très lent)
    cc_cur, ci_cur, _ = _detect_charuco(gray_p, board_chk, detector_chk)
    n_cur = len(ci_cur) if ci_cur is not None else 0
    if n_cur >= MIN_CALIB_CORNERS:
        worker.log.emit(
            f"  Config actuelle {squares_x}×{squares_y} OK ({n_cur} coins) "
            f"- pas de balayage config.")
        annotated_p = frame_p.copy()
        if raw_ids is not None:
            cv.aruco.drawDetectedMarkers(annotated_p, raw_c, raw_ids)
        if cc_cur is not None and ci_cur is not None:
            cv.aruco.drawDetectedCornersCharuco(annotated_p, cc_cur, ci_cur)
        cv.putText(annotated_p, f"Frame {t_start} - {n_cur} coins ChArUco",
                   (10, 30), cv.FONT_HERSHEY_SIMPLEX, 0.8,
                   (0, 200, 255), 2, cv.LINE_AA)
        worker.frame_preview.emit((annotated_p, f"Probe frame {t_start}"))
        worker.progress.emit(10)
        return True

    if ids_list:
        max_id = max(ids_list)
        worker.log.emit(
            f"  → ID max = {max_id}  →  board probable : "
            f"{max_id + 1} marqueurs total")
    results = []
    for sx in range(3, 14):
        for sy in range(3, 10):
            if sx * sy > 100:
                continue
            try:
                b_t, det_t, _ = _make_charuco(
                    sx, sy, square_length_mm, marker_length_mm, aruco_dict_id)
                cc, ci, _ = _detect_charuco(gray_p, b_t, det_t)
                n = len(ci) if ci is not None else 0
                if n > 0:
                    results.append((n, sx, sy))
            except Exception:
                pass
    if results:
        results.sort(reverse=True)
        n_total_markers = max(len(ids_list), max(ids_list) + 1 if ids_list else 0)
        exact_matches = [
            (n, sx, sy) for n, sx, sy in results
            if (sx * sy) // 2 == n_total_markers]
        chosen = exact_matches[0] if exact_matches else results[0]
        worker.log.emit("  ✓ Config(s) donnant des coins ChArUco :")
        for n, sx, sy in results[:8]:
            expected = (sx * sy) // 2
            tag = ""
            if (n, sx, sy) == chosen:
                tag = (f"  ◀ CORRECT (floor({sx}×{sy}/2)={expected}"
                       f"={n_total_markers} marqueurs)")
            worker.log.emit(f"    colonnes={sx}  lignes={sy}  →  {n} coins{tag}")
        worker.log.emit(
            f"  → Config recommandée : Colonnes={chosen[1]} Lignes={chosen[2]}"
            + (f"  ← ACTUEL OK" if (chosen[1] == squares_x and chosen[2] == squares_y)
               else f"  (actuel {squares_x}×{squares_y})"))
        if chosen[1] != squares_x or chosen[2] != squares_y:
            worker.suggest_config.emit(chosen[1], chosen[2])
    else:
        worker.log.emit("  ✗ Aucune config 3–13 × 3–9 ne donne de coins.")
    annotated_p = frame_p.copy()
    if raw_ids is not None:
        cv.aruco.drawDetectedMarkers(annotated_p, raw_c, raw_ids)
    cv.putText(annotated_p, f"Frame {t_start} - {n_raw} marqueurs ArUco",
               (10, 30), cv.FONT_HERSHEY_SIMPLEX, 0.8,
               (0, 200, 255), 2, cv.LINE_AA)
    worker.frame_preview.emit((annotated_p, f"Probe frame {t_start}"))
    worker.progress.emit(10)
    return True


def _calib_finish_pipeline(
        worker, profile: str,
        left_calib_cache, right_calib_cache, paired_stereo,
        left_calib_paths, right_calib_paths, left_paths,
        board_chk, detector_chk, calib_stats: dict,
        max_stereo_pairs: int,
        meta_extra: dict | None = None):
    """Étapes 3–6 : intrinsèque, stéréo, sauvegarde profil, verify."""
    prof = profile
    if len(paired_stereo) < MIN_STEREO_PAIRS:
        raise RuntimeError(
            f"Pas assez de paires stéréo ({len(paired_stereo)} < {MIN_STEREO_PAIRS}). "
            "Réduisez le pas de scan ou augmentez la visibilité de la mire.")

    worker.phase.emit("Étape 3 - calibration intrinsèque gauche")
    worker.log.emit("")
    worker.log.emit("── Step 3: Intrinsic calibration - Left ───────────")
    try:
        mtx1, dist1, rmse1, objpts_l, imgpts_l, info_l, sz_l = calibrate_charuco(
            left_calib_paths, worker.squares_x, worker.squares_y,
            worker.square_length_mm, worker.marker_length_mm,
            worker.aruco_dict_id, worker.log.emit,
            progress_fn=worker.progress.emit,
            prog_start=57, prog_end=70,
            cancelled_fn=lambda: worker._cancelled,
            live_fn=worker.live_status.emit,
            busy_fn=worker.opencv_busy.emit,
            detection_cache=left_calib_cache,
            **_calib_charuco_opencv_opts(worker))
    except cv.error as _e:
        worker.log.emit(f"[ERREUR OpenCV] {_e}")
        worker.error.emit(str(_e))
        return
    if mtx1 is None:
        return
    worker.log.emit(f"  RMSE left  : {rmse1:.4f} px")
    if not _mono_calib_ok(rmse1, mtx1, dist1, sz_l, "Intrinsèque gauche",
                          worker.log.emit):
        worker.error.emit(
            f"Calibration gauche rejetée : RMSE {rmse1:.2f} px "
            f"(max {MAX_MONO_RMSE_PX} px).")
        return
    np.save(calib_param('mtx1.npy', prof), mtx1)
    np.save(calib_param('dist1.npy', prof), dist1)
    worker.log.emit(f"  ✓ Checkpoint : {prof}/mtx1.npy / dist1.npy (gauche)")
    worker.progress.emit(70)
    worker.stereo_ready.emit({'rmse_left': float(rmse1)})
    gc.collect()

    worker.phase.emit("Étape 3 - calibration intrinsèque droite")
    worker.log.emit("")
    worker.log.emit("── Step 3: Intrinsic calibration - Right ──────────")
    try:
        mtx2, dist2, rmse2, objpts_r, imgpts_r, info_r, sz_r = calibrate_charuco(
            right_calib_paths, worker.squares_x, worker.squares_y,
            worker.square_length_mm, worker.marker_length_mm,
            worker.aruco_dict_id, worker.log.emit,
            progress_fn=worker.progress.emit,
            prog_start=71, prog_end=84,
            cancelled_fn=lambda: worker._cancelled,
            live_fn=worker.live_status.emit,
            busy_fn=worker.opencv_busy.emit,
            detection_cache=right_calib_cache,
            **_calib_charuco_opencv_opts(worker))
    except cv.error as _e:
        worker.log.emit(f"[ERREUR OpenCV] {_e}")
        worker.error.emit(str(_e))
        return
    if mtx2 is None:
        return
    worker.log.emit(f"  RMSE right : {rmse2:.4f} px")
    if not _mono_calib_ok(rmse2, mtx2, dist2, sz_r, "Intrinsèque droite",
                          worker.log.emit):
        worker.error.emit(
            f"Calibration droite rejetée : RMSE {rmse2:.2e} px "
            f"(max {MAX_MONO_RMSE_PX} px).")
        return
    np.save(calib_param('mtx2.npy', prof), mtx2)
    np.save(calib_param('dist2.npy', prof), dist2)
    worker.log.emit(f"  ✓ Checkpoint : {prof}/mtx2.npy / dist2.npy (droite)")
    worker.progress.emit(83)
    worker.stereo_ready.emit({'rmse_right': float(rmse2)})

    mtx1, mtx2 = _harmonize_identical_camera_focals(
        mtx1, mtx2, worker.log.emit)

    worker.phase.emit("Étape 4 - calibration stéréo")
    worker.log.emit("")
    worker.log.emit("── Step 4: Stereo calibration ─────────────────────")
    try:
        R, T, F, rmse3, rect_dy_median = stereo_calibrate_charuco_paired(
            mtx1, dist1, mtx2, dist2, paired_stereo,
            worker.squares_x, worker.squares_y,
            worker.square_length_mm, worker.marker_length_mm,
            worker.aruco_dict_id, worker.log.emit,
            progress_fn=worker.progress.emit,
            cancelled_fn=lambda: worker._cancelled,
            live_fn=worker.live_status.emit,
            busy_fn=worker.opencv_busy.emit,
            target_pairs=max_stereo_pairs)
    except cv.error as _e:
        worker.log.emit(f"[ERREUR OpenCV stéréo] {_e}")
        worker.error.emit(str(_e))
        return
    rt_text, baseline_mm, _ = _stereo_extrinsics_summary(
        R, T, rmse_stereo=rmse3, rmse_left=rmse1, rmse_right=rmse2)
    worker.log.emit("  ── Extrinsèque stéréo (R / T) ──")
    for line in rt_text.splitlines():
        worker.log.emit(f"  {line}")
    worker.stereo_ready.emit({
        'R': R, 'T': T, 'rmse_stereo': rmse3,
        'rmse_left': rmse1, 'rmse_right': rmse2, 'baseline_mm': baseline_mm,
    })
    if rmse3 > MAX_STEREO_RMSE_PX:
        worker.error.emit(
            f"Calibration stéréo rejetée : RMSE {rmse3:.1f} px "
            f"(max {MAX_STEREO_RMSE_PX} px).")
        return
    if rect_dy_median is not None and rect_dy_median > MAX_RECT_DY_REJECT_PX:
        worker.error.emit(
            f"Calibration stéréo rejetée : alignement rectifié "
            f"{rect_dy_median:.1f} px (max {MAX_RECT_DY_REJECT_PX} px).")
        return
    worker.progress.emit(93)

    worker.log.emit("")
    worker.log.emit("── Step 5: Saving parameters ──────────────────────")
    os.makedirs(calib_profile_dir(prof), exist_ok=True)
    np.save(calib_param('mtx1.npy', prof), mtx1)
    np.save(calib_param('dist1.npy', prof), dist1)
    np.save(calib_param('mtx2.npy', prof), mtx2)
    np.save(calib_param('dist2.npy', prof), dist2)
    np.save(calib_param('R.npy', prof), R)
    np.save(calib_param('T.npy', prof), T)
    np.save(calib_param('F.npy', prof), F)
    np.save(calib_param('stereo_rmse.npy', prof), np.array(rmse3))
    np.save(calib_param('left_rmse.npy', prof), np.array(rmse1))
    np.save(calib_param('right_rmse.npy', prof), np.array(rmse2))
    save_charuco_config(
        worker.squares_x, worker.squares_y,
        worker.square_length_mm, worker.marker_length_mm,
        worker.aruco_dict_id, profile=prof)
    save_videos_txt(worker.left_video, worker.right_video)
    worker.log.emit(f"  Profil : {calib_profile_label(prof)} ({calib_profile_dir(prof)})")
    worker.log.emit(f"  stereo_rmse.npy → {rmse3:.4f} pixels")
    if rect_dy_median is not None:
        worker.log.emit(f"  |ΔY| rectifié médian → {rect_dy_median:.2f} px")

    meta = {
        'engine': 'fast_v2' if prof == CALIB_PROFILE_FAST_V2 else prof,
        'profile': prof,
        'stereo_rmse': float(rmse3),
        'rmse_left': float(rmse1),
        'rmse_right': float(rmse2),
        'baseline_mm': float(baseline_mm),
        'rect_dy_median': rect_dy_median,
        **(meta_extra or {}),
        **calib_stats,
    }
    meta_path = calib_param('calib_meta.json', prof)
    with open(meta_path, 'w', encoding='utf-8') as mf:
        json.dump(meta, mf, indent=2)
    worker.log.emit(f"  calib_meta.json → métriques A/B")

    verify_dir = os.path.join(calib_profile_dir(prof), 'verify')
    os.makedirs(verify_dir, exist_ok=True)
    worker.log.emit("")
    worker.log.emit("── Step 6: Saving verify images ───────────────────")

    def _save_verify_png(item, out_path, label):
        item['label'] = label
        frame = cv.imread(item['path'], 1)
        if frame is None:
            return
        ann = cv.aruco.drawDetectedCornersCharuco(
            frame, item['corners'], item['ids'])
        cv.imwrite(out_path, ann)
        item['verify_png'] = out_path

    for i, item in enumerate(info_l):
        _save_verify_png(item, os.path.join(verify_dir, f"left_{i:04d}.png"),
                         f"Left {i+1:03d}")
    for i, item in enumerate(info_r):
        _save_verify_png(item, os.path.join(verify_dir, f"right_{i:04d}.png"),
                         f"Right {i+1:03d}")
    np.save(os.path.join(verify_dir, 'corners_left.npy'),
            np.array([it['corners'] for it in info_l], dtype=object))
    np.save(os.path.join(verify_dir, 'corners_right.npy'),
            np.array([it['corners'] for it in info_r], dtype=object))
    np.save(os.path.join(verify_dir, 'objpoints_left.npy'),
            np.array(objpts_l, dtype=object))
    np.save(os.path.join(verify_dir, 'objpoints_right.npy'),
            np.array(objpts_r, dtype=object))
    np.save(os.path.join(verify_dir, 'image_sizes.npy'),
            np.array([list(sz_l), list(sz_r)]))

    worker.progress.emit(100)
    worker.log.emit("")
    worker.log.emit("══════════════════════════════════════════════════")
    worker.log.emit(
        f"✓ Calibration {calib_profile_label(prof)} terminée - "
        "basculez le profil actif dans Mesure pour tester.")
    worker.log.emit("══════════════════════════════════════════════════")
    worker.verify_ready.emit({
        'left': info_l, 'right': info_r,
        'objpoints_left': objpts_l, 'objpoints_right': objpts_r,
        'image_size_left': sz_l, 'image_size_right': sz_r,
        'stats': calib_stats,
        'R': R, 'T': T,
        'rmse_stereo': rmse3, 'rmse_left': rmse1, 'rmse_right': rmse2,
    })


class CalibrationWorkerFast(QObject):
    """Moteur Rapide v2 : scan stride couplé + cache mono + profil fast_v2."""
    log            = pyqtSignal(str)
    progress       = pyqtSignal(int)
    finished       = pyqtSignal()
    error          = pyqtSignal(str)
    verify_ready   = pyqtSignal(object)
    stereo_ready   = pyqtSignal(object)
    frame_preview  = pyqtSignal(object)
    suggest_config = pyqtSignal(int, int)
    phase          = pyqtSignal(str)
    live_status    = pyqtSignal(str)
    opencv_busy    = pyqtSignal(bool)
    scan_counts    = pyqtSignal(int, int, int)  # détections G, D, paires couplées

    left_video:       str   = ""
    right_video:      str   = ""
    squares_x:        int   = 5
    squares_y:        int   = 7
    square_length_mm: float = 49.5
    marker_length_mm: float = 37.0
    aruco_dict_id:    int   = cv.aruco.DICT_5X5_50
    max_intrinsic_views: int = 80
    max_stereo_pairs:    int = 120
    calib_camera_scale:  float = 1.0
    scan_stride_idle:    int = 5
    dense_window:        int = 25
    dense_stride:        int = 1
    _cancelled:       bool  = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            self._cancelled = False
            # Une seule calibration, un seul dossier : camera_parameters/.
            # Ecrire dans profiles/fast_v2/ produisait deux calibrations pour
            # la meme paire de videos - l'import ZIP atterrissait a la racine
            # pendant que la mesure lisait le profil, sans que rien ne le dise.
            prof = CALIB_PROFILE_CLASSIC
            os.makedirs(calib_profile_dir(prof), exist_ok=True)
            tmp_base = os.path.join(tempfile.gettempdir(), 'stereo_calib_fast_v2')
            tmp_left = os.path.join(tmp_base, 'left')
            tmp_right = os.path.join(tmp_base, 'right')
            MAX_INTRINSIC_VIEWS = max(10, int(self.max_intrinsic_views))
            MAX_STEREO_PAIRS = max(10, int(self.max_stereo_pairs))
            STEREO_POOL_MARGIN = max(15, MAX_STEREO_PAIRS // 6)
            stride_idle = max(1, int(self.scan_stride_idle))
            dense_win = max(10, int(self.dense_window))
            dense_st = max(1, int(self.dense_stride))

            self.log.emit(
                f"═══ Moteur Rapide v2 - stride {stride_idle}, "
                f"rafale {dense_win} pas, pas rafale {dense_st} ═══")
            self.log.emit(
                f"  Précision : intrinsèque {MAX_INTRINSIC_VIEWS} vues/cam  |  "
                f"stéréo {MAX_STEREO_PAIRS} paires "
                f"(pool +{STEREO_POOL_MARGIN})")
            self.log.emit(
                f"  Sortie : {calib_profile_dir(prof)}")
            _dh = _calib_duration_hints(MAX_INTRINSIC_VIEWS, MAX_STEREO_PAIRS)
            self.log.emit(f"  Durée indicative (hors gain scan) : {_dh['total']}")

            self.phase.emit("Étape 1 - chargement sync / trim")
            tl = _load_calib_timeline(
                self.log.emit, self.progress.emit,
                self.left_video, self.right_video)
            t_start, t_end = tl['t_start'], tl['t_end']
            sync_delta = tl['sync_delta']
            left_sync, right_sync = tl['left_sync'], tl['right_sync']
            available = tl['available']

            self.phase.emit("Étape 2 - scan v2 + détection ChArUco")
            self.log.emit("")
            self.log.emit("── Step 2: Scan v2 (stride couplé + cache mono) ──")

            board_chk, detector_chk, aruco_detector_chk = _make_charuco(
                self.squares_x, self.squares_y,
                self.square_length_mm, self.marker_length_mm, self.aruco_dict_id)
            os.makedirs(tmp_left, exist_ok=True)
            os.makedirs(tmp_right, exist_ok=True)
            for d in (tmp_left, tmp_right):
                for f in os.listdir(d):
                    os.remove(os.path.join(d, f))

            if not _run_board_probe(
                    self, self.left_video, t_start,
                    self.squares_x, self.squares_y,
                    self.square_length_mm, self.marker_length_mm,
                    self.aruco_dict_id, board_chk, detector_chk,
                    aruco_detector_chk):
                return

            left_cache, right_cache, coupled_pairs = [], [], []
            self.log.emit(
                f"  Scan v2 couplé ({available} instants, pas idle {stride_idle})…")
            left_paths, right_paths, scan_stats = _scan_stereo_coupled_v2(
                self.left_video, self.right_video,
                t_start, t_end, sync_delta,
                board_chk, detector_chk, aruco_detector_chk,
                tmp_left, tmp_right, left_cache, right_cache, coupled_pairs,
                stride_idle, dense_win, dense_st,
                cancelled_fn=lambda: self._cancelled,
                log_fn=self.log.emit,
                progress_fn=self.progress.emit,
                frame_preview_fn=self.frame_preview.emit,
                prog_start=10, prog_end=55,
                scan_counts_fn=self.scan_counts.emit)
            self.log.emit(
                f"  → {len(left_paths)} détections gauche / "
                f"{len(right_paths)} détections droite")
            self.log.emit(
                f"  Scan v2 : {scan_stats.get('frames_probed', 0)} frames analysées, "
                f"{scan_stats.get('frames_skipped_grab', 0)} sautées (grab), "
                f"{scan_stats.get('scan_duration_s', 0)} s")
            if self._cancelled:
                return
            if not left_paths or not right_paths:
                raise RuntimeError(
                    "Aucune frame avec cible ChArUco détectée (v2). "
                    "Réduisez le pas ou vérifiez la config board.")

            MAX_FRAMES = 50_000
            scan_l, scan_r = len(left_cache), len(right_cache)

            def _cap_cache(cache, n, label):
                if len(cache) <= n:
                    return cache
                step = len(cache) / n
                capped = [cache[int(i * step)] for i in range(n)]
                self.log.emit(
                    f"  [AVERTISSEMENT] {label} : {len(cache)} détections → "
                    f"plafond {n}")
                return capped

            left_cache = _cap_cache(left_cache, MAX_FRAMES, "Gauche")
            right_cache = _cap_cache(right_cache, MAX_FRAMES, "Droite")
            left_paths = [e['path'] for e in left_cache]
            right_paths = [e['path'] for e in right_cache]

            if coupled_pairs:
                paired_all = coupled_pairs
                self.log.emit(
                    f"  Paires couplées (même instant) : {len(paired_all)}")
            else:
                paired_all = _pair_stereo_caches(
                    left_cache, right_cache, left_sync, right_sync)
                self.log.emit(
                    f"  Paires stéréo (re-appariement) : {len(paired_all)}")

            paired_valid = [
                (le, re) for le, re in paired_all
                if _n_calib_points(le.get('obj_pts')) >= MIN_CALIB_CORNERS
                and _n_calib_points(re.get('obj_pts')) >= MIN_CALIB_CORNERS
            ]
            paired_frames = {
                _frame_num_from_path(le['path']) for le, _ in paired_valid}
            mono_only_left = sum(
                1 for e in left_cache
                if _frame_num_from_path(e['path']) not in paired_frames)
            mono_only_right = sum(
                1 for e in right_cache
                if _frame_num_from_path(e['path']) not in paired_frames)
            self.log.emit(
                f"  Cache mono : G-only {mono_only_left}  |  D-only {mono_only_right}  "
                f"|  paires valides {len(paired_valid)}")

            left_calib_cache = _subsample_detection_cache(
                left_cache, MAX_INTRINSIC_VIEWS)
            right_calib_cache = _subsample_detection_cache(
                right_cache, MAX_INTRINSIC_VIEWS)
            stereo_pool_size = min(
                len(paired_valid), MAX_STEREO_PAIRS + STEREO_POOL_MARGIN)
            paired_stereo = _select_stereo_pairs_quality(
                paired_valid, stereo_pool_size, log_fn=self.log.emit)
            left_calib_paths = [e['path'] for e in left_calib_cache]
            right_calib_paths = [e['path'] for e in right_calib_cache]

            self.log.emit(
                f"  Intrinsèque v2 : {len(left_cache)} G → {len(left_calib_cache)}  |  "
                f"{len(right_cache)} D → {len(right_calib_cache)}")
            self.log.emit(
                f"  Stéréo : {len(paired_valid)} paires valides → pool {len(paired_stereo)}")
            calib_stats = {
                'frames_analyzed': available,
                'scan_left': scan_l,
                'scan_right': scan_r,
                'pairs_stereo': len(paired_all),
                'calib_left': len(left_calib_cache),
                'calib_right': len(right_calib_cache),
                'stereo_pairs_used': len(paired_stereo),
                'mono_only_left': mono_only_left,
                'mono_only_right': mono_only_right,
                **scan_stats,
            }
            self.progress.emit(55)

            for _preview_path in left_paths[:1] or right_paths[:1]:
                try:
                    _prev_img = cv.imread(_preview_path)
                    if _prev_img is not None:
                        _pg = cv.cvtColor(_prev_img, cv.COLOR_BGR2GRAY)
                        _pc, _pi, _ = _detect_charuco_fast(
                            _pg, board_chk, detector_chk)
                        _ann = _prev_img.copy()
                        if _pc is not None and _pi is not None:
                            cv.aruco.drawDetectedCornersCharuco(_ann, _pc, _pi)
                        self.frame_preview.emit(
                            (_ann, f"1ère frame v2 - {_preview_path}"))
                except Exception:
                    pass

            _calib_finish_pipeline(
                self, prof,
                left_calib_cache, right_calib_cache, paired_stereo,
                left_calib_paths, right_calib_paths, left_paths,
                board_chk, detector_chk, calib_stats,
                MAX_STEREO_PAIRS, meta_extra={'engine': 'fast_v2'})

        except Exception as exc:
            tb = traceback.format_exc()
            self.log.emit(f"[ERREUR]\n{tb}")
            self.error.emit(f"{exc}\n\n{tb}")
        finally:
            self.finished.emit()


class CalibrationWorker(QObject):
    log            = pyqtSignal(str)
    progress       = pyqtSignal(int)
    finished       = pyqtSignal()
    error          = pyqtSignal(str)
    verify_ready   = pyqtSignal(object)   # emits dict with verify data
    stereo_ready   = pyqtSignal(object)   # R, T, RMSE (dès étape stéréo OK)
    frame_preview  = pyqtSignal(object)   # emits (np.ndarray BGR, str label)
    suggest_config = pyqtSignal(int, int) # emits (best_sx, best_sy)
    phase          = pyqtSignal(str)      # étape courante (lisible UI)
    live_status    = pyqtSignal(str)      # statut temps réel (heartbeat)
    opencv_busy    = pyqtSignal(bool)     # True pendant calibrateCamera, etc.
    scan_counts    = pyqtSignal(int, int, int)  # détections G, D, paires couplées

    left_video:       str   = ""
    right_video:      str   = ""
    squares_x:        int   = 5
    squares_y:        int   = 7
    square_length_mm: float = 49.5
    marker_length_mm: float = 37.0
    aruco_dict_id:    int   = cv.aruco.DICT_5X5_50
    max_intrinsic_views: int = 80
    max_stereo_pairs:    int = 120
    calib_camera_scale:  float = 1.0
    _cancelled:       bool  = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            # Chaque lancement doit repartir en état "non annulé"
            self._cancelled = False
            os.makedirs('camera_parameters', exist_ok=True)
            tmp_base  = os.path.join(tempfile.gettempdir(), 'stereo_calib')
            tmp_left  = os.path.join(tmp_base, 'left')
            tmp_right = os.path.join(tmp_base, 'right')
            MAX_INTRINSIC_VIEWS = max(10, int(self.max_intrinsic_views))
            MAX_STEREO_PAIRS = max(10, int(self.max_stereo_pairs))
            # Marge pool stéréo : l'élagage outliers peut en retirer ~5–10 %.
            STEREO_POOL_MARGIN = max(15, MAX_STEREO_PAIRS // 6)

            # ── Étape 1 : extraction des frames (avec offset de sync) ────────
            self.log.emit(
                f"═══ Moteur calibration v5.2 - rapide + stéréo fiable ═══")
            self.log.emit(
                f"  Précision : intrinsèque {MAX_INTRINSIC_VIEWS} vues  |  "
                f"stéréo {MAX_STEREO_PAIRS} paires "
                f"(pool +{STEREO_POOL_MARGIN} avant élagage outliers)")
            _dh = _calib_duration_hints(MAX_INTRINSIC_VIEWS, MAX_STEREO_PAIRS)
            self.log.emit(
                f"  Durée indicative totale : {_dh['total']} "
                f"(calibrateCamera {_dh['intrinsic_per_cam']}/caméra, "
                f"stereoCalibrate {_dh['stereo']})")
            self.phase.emit("Étape 1 - chargement sync / trim")
            self.log.emit("── Step 1: Extracting synchronized frames ──────────")

            # Lire l'offset de synchronisation si disponible
            sync_path = 'camera_parameters/sync_frames.npy'
            if os.path.exists(sync_path):
                sync_frames = np.load(sync_path)
                left_sync  = int(sync_frames[0])
                right_sync = int(sync_frames[1])
                self.log.emit(
                    f"  Sync offset chargé - Gauche démarre à frame {left_sync}, "
                    f"Droite à frame {right_sync}  "
                    f"(décalage {left_sync - right_sync:+d} frames)")
            else:
                left_sync  = 0
                right_sync = 0
                self.log.emit(
                    "  Aucun fichier sync_frames.npy - extraction depuis frame 0 "
                    "(lancez l'onglet Sync d'abord pour une précision optimale)")

            cap_l = cv.VideoCapture(self.left_video)
            cap_r = cv.VideoCapture(self.right_video)
            total_l = int(cap_l.get(cv.CAP_PROP_FRAME_COUNT))
            total_r = int(cap_r.get(cv.CAP_PROP_FRAME_COUNT))
            cap_l.release()
            cap_r.release()

            # Lire les limites de rognage si disponibles
            trim_path = 'camera_parameters/trim_frames.npy'
            if os.path.exists(trim_path):
                trim = np.load(trim_path)
                left_in  = int(trim[0])
                left_out = int(trim[1]) if int(trim[1]) > 0 else total_l - 1
                right_in = int(trim[2])
                right_out= int(trim[3]) if int(trim[3]) > 0 else total_r - 1
                self.log.emit(
                    f"  Limites rognage chargées - "
                    f"Gauche [{left_in}→{left_out}]  |  Droite [{right_in}→{right_out}]")
            else:
                left_in   = 0
                left_out  = total_l - 1
                right_in  = 0
                right_out = total_r - 1

            # Timeline maîtresse en coordonnées GAUCHE (comme Qt scanStereoSynchronized).
            # Frame droite lue à t + (right_sync - left_sync) pour chaque frame gauche t.
            sync_delta = right_sync - left_sync
            t_start = max(max(left_sync, left_in), right_in - sync_delta)
            t_end = min(left_out, right_out - sync_delta,
                        total_l - 1, total_r - 1 - sync_delta)
            if t_start > t_end:
                raise RuntimeError(
                    "Plage commune vide après sync + rognage. "
                    "Vérifiez sync_frames.npy et trim_frames.npy (onglet Sync).")

            available = t_end - t_start + 1
            right_at_start = t_start + sync_delta
            right_at_end = t_end + sync_delta
            self.log.emit(
                f"  Plage couplée : {available} instants  "
                f"(Gauche {t_start}→{t_end}  |  Droite {right_at_start}→{right_at_end}, "
                f"offset sync {sync_delta:+d})")
            if abs(left_sync - right_sync) > 60:
                self.log.emit(
                    f"  ⚠ Décalage sync important ({left_sync - right_sync:+d} frames). "
                    "Si le RMSE stéréo est mauvais, relancez l'onglet Sync.")
            self.progress.emit(5)

            # ── Étape 2 : scan complet + détection ChArUco inline ────────────
            self.phase.emit("Étape 2 - scan vidéo + détection ChArUco")
            self.log.emit("")
            self.log.emit("── Step 2: Scan complet + détection ChArUco ────────")

            board_chk, detector_chk, aruco_detector_chk = _make_charuco(
                self.squares_x, self.squares_y,
                self.square_length_mm, self.marker_length_mm, self.aruco_dict_id)

            os.makedirs(tmp_left,  exist_ok=True)
            os.makedirs(tmp_right, exist_ok=True)

            # Vider les dossiers temporaires
            for d in (tmp_left, tmp_right):
                for f in os.listdir(d):
                    os.remove(os.path.join(d, f))

            MAX_FRAMES = 50_000   # plafond scan (toutes détections conservées en dessous)

            def _subsample_cache(cache, n):
                if len(cache) <= n:
                    return cache
                step = len(cache) / n
                return [cache[int(i * step)] for i in range(n)]

            def _subsample_pairs(pairs, n):
                if len(pairs) <= n:
                    return pairs
                step = len(pairs) / n
                return [pairs[int(i * step)] for i in range(n)]

            def _save_detection(frame, fidx, folder, det_cache):
                gray = cv.cvtColor(frame, cv.COLOR_BGR2GRAY)
                charuco_corners, charuco_ids, _ = _detect_charuco(
                    gray, board_chk, detector_chk)
                if charuco_ids is None or len(charuco_ids) < MIN_CALIB_CORNERS:
                    return None
                obj_pts, img_pts = board_chk.matchImagePoints(
                    charuco_corners, charuco_ids)
                if obj_pts is None or _n_calib_points(obj_pts) < MIN_CALIB_CORNERS:
                    return None
                name = os.path.join(folder, f"frame_{fidx:06d}.jpg")
                cv.imwrite(name, frame, [cv.IMWRITE_JPEG_QUALITY, 95])
                fh, fw = frame.shape[:2]
                entry = {
                    'path':        name,
                    'corners':     charuco_corners.reshape(-1, 1, 2).astype(np.float32),
                    'ids':         charuco_ids.copy(),
                    'obj_pts':     obj_pts,
                    'img_pts':     img_pts,
                    'image_size': (fw, fh),
                }
                det_cache.append(entry)
                return entry

            def _scan_stereo_coupled(t0, t1, delta, left_cache, right_cache,
                                     coupled_pairs, prog_start, prog_end):
                """Scan couplé G/D : à chaque t (gauche), lit frame droite t + delta."""
                cap_l = cv.VideoCapture(self.left_video)
                cap_r = cv.VideoCapture(self.right_video)
                if not cap_l.isOpened() or not cap_r.isOpened():
                    self.log.emit("  [ERREUR] Impossible d'ouvrir une des vidéos (scan couplé)")
                    cap_l.release()
                    cap_r.release()
                    return [], []

                total_l_vid = int(cap_l.get(cv.CAP_PROP_FRAME_COUNT))
                total_r_vid = int(cap_r.get(cv.CAP_PROP_FRAME_COUNT))
                r0 = t0 + delta
                ok_l = cap_l.set(cv.CAP_PROP_POS_FRAMES, t0)
                ok_r = cap_r.set(cv.CAP_PROP_POS_FRAMES, r0)
                self.log.emit(
                    f"  Scan couplé : G seek {t0}→{'OK' if ok_l else 'ÉCHEC'} "
                    f"| D seek {r0}→{'OK' if ok_r else 'ÉCHEC'}  "
                    f"(offset sync {delta:+d}, {total_l_vid}/{total_r_vid} frames vidéo)")
                if t0 >= total_l_vid or r0 >= total_r_vid:
                    self.log.emit(
                        "  [ERREUR] Départ hors limites - vidéos différentes du sync ? "
                        "Rechargez dans l'onglet Sync.")
                    cap_l.release()
                    cap_r.release()
                    return [], []

                left_paths, right_paths = [], []
                span = max(1, t1 - t0 + 1)
                t = t0
                diag_aruco_l = diag_aruco_r = 0
                diag_checked = 0
                grab_fails = 0
                probed = 0
                _last_counts_emit = 0

                def _emit_scan_counts(force: bool = False) -> None:
                    nonlocal _last_counts_emit
                    n = len(left_cache)
                    if not force and n == _last_counts_emit and probed % 20 != 0:
                        return
                    _last_counts_emit = n
                    self.scan_counts.emit(
                        len(left_cache), len(right_cache), len(coupled_pairs))

                _emit_scan_counts(force=True)

                while t <= t1:
                    if self._cancelled:
                        self.log.emit(f"  ⛔ Scan couplé annulé à t={t} (G) / {t + delta} (D).")
                        break
                    rt = t + delta
                    if not cap_l.grab() or not cap_r.grab():
                        grab_fails += 1
                        if grab_fails > 5:
                            self.log.emit(
                                f"  [AVERTISSEMENT] grab() échoue à G{t}/D{rt} - fin de plage ?")
                            break
                        t += 1
                        continue
                    grab_fails = 0
                    ok_l, frame_l = cap_l.retrieve()
                    ok_r, frame_r = cap_r.retrieve()
                    t += 1
                    if not ok_l or frame_l is None or not ok_r or frame_r is None:
                        continue
                    probed += 1

                    if diag_checked < 20:
                        gray_l = cv.cvtColor(frame_l, cv.COLOR_BGR2GRAY)
                        gray_r = cv.cvtColor(frame_r, cv.COLOR_BGR2GRAY)
                        _, _, na_l = _detect_charuco(gray_l, board_chk, detector_chk)
                        _, _, na_r = _detect_charuco(gray_r, board_chk, detector_chk)
                        if na_l > 0:
                            diag_aruco_l += 1
                        if na_r > 0:
                            diag_aruco_r += 1
                        if diag_checked == 0:
                            raw_c, raw_ids, _ = aruco_detector_chk.detectMarkers(gray_l)
                            ids_list = sorted(raw_ids.flatten().tolist()) if raw_ids is not None else []
                            self.log.emit(
                                f"  Diag G{t - 1}/D{rt}: ArUco G={na_l} D={na_r} marqueurs")
                            self.log.emit(
                                f"  IDs G : {ids_list}  "
                                f"(min={min(ids_list) if ids_list else '?'}, "
                                f"max={max(ids_list) if ids_list else '?'})")
                        diag_checked += 1

                    lf = t - 1
                    rf = rt
                    el = _save_detection(frame_l, lf, tmp_left, left_cache)
                    er = _save_detection(frame_r, rf, tmp_right, right_cache)
                    if el is not None:
                        left_paths.append(el['path'])
                    if er is not None:
                        right_paths.append(er['path'])
                    if (el is not None and er is not None
                            and _count_common_charuco_ids(el, er) >= MIN_CALIB_CORNERS):
                        coupled_pairs.append((el, er))

                    if probed % 40 == 0:
                        _emit_scan_counts(force=True)
                        for side, frame, fidx, entry in (
                                ("Gauche", frame_l, lf, el),
                                ("Droite", frame_r, rf, er)):
                            prev = frame.copy()
                            n_ch = len(entry['ids']) if entry is not None else 0
                            if entry is not None:
                                cv.aruco.drawDetectedCornersCharuco(
                                    prev, entry['corners'], entry['ids'])
                            else:
                                gray_p = cv.cvtColor(frame, cv.COLOR_BGR2GRAY)
                                cc_p, ci_p, _ = _detect_charuco(
                                    gray_p, board_chk, detector_chk)
                                if ci_p is not None and len(ci_p) > 0:
                                    n_ch = len(ci_p)
                                    cv.aruco.drawDetectedCornersCharuco(
                                        prev, cc_p, ci_p)
                            cv.putText(prev,
                                       f"{side} {fidx} | ChArUco={n_ch}",
                                       (10, 30), cv.FONT_HERSHEY_SIMPLEX, 0.75,
                                       (0, 255, 80) if n_ch >= MIN_CALIB_CORNERS else (0, 80, 255),
                                       2, cv.LINE_AA)
                            self.frame_preview.emit((prev, f"{side} - {fidx}/{t1}"))

                    _emit_scan_counts()

                    done = probed
                    pct = prog_start + int((prog_end - prog_start) * done / span)
                    self.progress.emit(pct)

                if diag_checked > 0 and diag_aruco_l == 0 and diag_aruco_r == 0:
                    self.log.emit(
                        f"  ⚠ Aucun marqueur ArUco sur les {diag_checked} premiers instants. "
                        f"Vérifiez dictionnaire et visibilité du board "
                        f"(G {t0}→{t1}, D {r0}→{t1 + delta}).")
                _emit_scan_counts(force=True)
                cap_l.release()
                cap_r.release()
                return left_paths, right_paths

            # ── Auto-test des tailles de board sur la première frame ─────────
            self.log.emit("  Auto-détection de la config du board sur 1ère frame…")
            cap_probe = cv.VideoCapture(self.left_video)
            total_probe = int(cap_probe.get(cv.CAP_PROP_FRAME_COUNT))
            if t_start >= total_probe:
                self.log.emit(
                    f"  [ERREUR] Frame de départ ({t_start}) > durée vidéo "
                    f"({total_probe} frames).\n"
                    f"  → Mauvaise vidéo chargée ou trim défini sur une autre vidéo.\n"
                    f"  → Retournez dans Sync, rechargez les vidéos correctes et "
                    f"recliquez 'Passer à la calibration'.")
                cap_probe.release()
                self.error.emit(
                    f"Trim start ({t_start}) hors limites (vidéo = {total_probe} frames). "
                    "Rechargez les vidéos dans l'onglet Sync.")
                return
            cap_probe.set(cv.CAP_PROP_POS_FRAMES, t_start)
            ret_p, frame_p = cap_probe.read()
            cap_probe.release()
            if ret_p and frame_p is not None:
                gray_p = cv.cvtColor(frame_p, cv.COLOR_BGR2GRAY)
                # Récupérer les IDs ArUco réels
                raw_c, raw_ids, _ = aruco_detector_chk.detectMarkers(gray_p)
                ids_list = sorted(raw_ids.flatten().tolist()) if raw_ids is not None else []
                n_raw = len(ids_list)
                self.log.emit(
                    f"  ArUco bruts : {n_raw} marqueurs - IDs : {ids_list}")
                if ids_list:
                    max_id = max(ids_list)
                    self.log.emit(
                        f"  → ID max = {max_id}  →  board probable : "
                        f"{max_id + 1} marqueurs total")
                results = []
                for sx in range(3, 14):
                    for sy in range(3, 10):
                        if sx * sy > 100:
                            continue
                        try:
                            b_t, det_t, _ = _make_charuco(
                                sx, sy,
                                self.square_length_mm, self.marker_length_mm,
                                self.aruco_dict_id)
                            cc, ci, _ = _detect_charuco(gray_p, b_t, det_t)
                            n = len(ci) if ci is not None else 0
                            if n > 0:
                                results.append((n, sx, sy))
                        except Exception:
                            pass
                if results:
                    results.sort(reverse=True)

                    # ── Trouver la config physiquement correcte ──────────────
                    # Pour n_total marqueurs, floor(sx*sy/2) = n_total
                    # → sx*sy = 2*n_total ou 2*n_total+1
                    # On cherche parmi les résultats celui dont le compte de
                    # marqueurs théorique correspond au nombre réel détecté,
                    # en préférant la config avec le plus de coins.
                    n_total_markers = len(ids_list) if ids_list else 0
                    # Tenir compte du fait qu'un marqueur peut être occulté :
                    # max_id + 1 est le meilleur indicateur du total théorique
                    n_total_markers = max(n_total_markers,
                                         max_id + 1 if ids_list else 0)
                    exact_matches = []
                    for n, sx, sy in results:
                        expected = (sx * sy) // 2
                        if expected == n_total_markers:
                            exact_matches.append((n, sx, sy))
                    chosen = exact_matches[0] if exact_matches else results[0]

                    self.log.emit(
                        f"  ✓ Config(s) donnant des coins ChArUco :")
                    for n, sx, sy in results[:8]:
                        expected = (sx * sy) // 2
                        tag = ""
                        if (n, sx, sy) == chosen:
                            tag = f"  ◀ CORRECT (floor({sx}×{sy}/2)={expected}={n_total_markers} marqueurs)"
                        elif expected == n_total_markers:
                            tag = f"  (floor({sx}×{sy}/2)={expected} marqueurs)"
                        self.log.emit(
                            f"    colonnes={sx}  lignes={sy}  →  {n} coins{tag}")
                    self.log.emit(
                        f"  → Config recommandée : Colonnes={chosen[1]} Lignes={chosen[2]}"
                        + (f"  ← ACTUEL OK" if (chosen[1] == self.squares_x
                                                 and chosen[2] == self.squares_y)
                           else f"  (actuel {self.squares_x}×{self.squares_y})"))
                    if chosen[1] != self.squares_x or chosen[2] != self.squares_y:
                        self.suggest_config.emit(chosen[1], chosen[2])
                else:
                    self.log.emit(
                        "  ✗ Aucune config 3–13 × 3–9 ne donne de coins.")
                    self.log.emit(
                        "    Vérifiez que la cible est bien visible sur "
                        f"la frame {t_start}.")
                # Envoyer l'aperçu de la frame de probe
                annotated_p = frame_p.copy()
                if raw_ids is not None:
                    cv.aruco.drawDetectedMarkers(annotated_p, raw_c, raw_ids)
                cv.putText(annotated_p,
                           f"Frame {t_start} - {n_raw} marqueurs ArUco",
                           (10, 30), cv.FONT_HERSHEY_SIMPLEX, 0.8,
                           (0, 200, 255), 2, cv.LINE_AA)
                self.frame_preview.emit((annotated_p,
                                         f"Probe frame {t_start}"))
            self.progress.emit(10)

            left_cache, right_cache, coupled_pairs = [], [], []

            self.log.emit(f"  Scan couplé synchronisé ({available} instants)…")
            left_paths, right_paths = _scan_stereo_coupled(
                t_start, t_end, sync_delta, left_cache, right_cache,
                coupled_pairs, prog_start=10, prog_end=55)
            self.log.emit(
                f"  → {len(left_paths)} détections gauche / "
                f"{len(right_paths)} détections droite")
            if self._cancelled:
                return

            if self._cancelled:
                return
            if not left_paths or not right_paths:
                raise RuntimeError(
                    "Aucune frame avec cible ChArUco détectée. "
                    "Vérifiez colonnes/lignes, tailles et dictionnaire.")

            scan_l, scan_r = len(left_cache), len(right_cache)

            def _cap_cache(cache, n, label):
                if len(cache) <= n:
                    return cache
                step = len(cache) / n
                capped = [cache[int(i * step)] for i in range(n)]
                self.log.emit(
                    f"  [AVERTISSEMENT] {label} : {len(cache)} détections → "
                    f"plafond {n} (échantillonnage uniforme)")
                return capped

            left_cache  = _cap_cache(left_cache,  MAX_FRAMES, "Gauche")
            right_cache = _cap_cache(right_cache, MAX_FRAMES, "Droite")
            left_paths  = [e['path'] for e in left_cache]
            right_paths = [e['path'] for e in right_cache]

            self.log.emit(
                f"  Scan : {scan_l} détections gauche / {available} frames analysées  |  "
                f"{scan_r} détections droite / {available} frames analysées")
            if scan_l < available * 0.05:
                self.log.emit(
                    f"  ⚠ Peu de détections ({scan_l}/{available}). "
                    "La mire est-elle visible sur toute la plage In→Out ?")

            if coupled_pairs:
                paired_all = coupled_pairs
                self.log.emit(
                    f"  Paires couplées (même instant scan) : {len(paired_all)}")
            else:
                paired_all = _pair_stereo_caches(
                    left_cache, right_cache, left_sync, right_sync)
                self.log.emit(
                    f"  Paires stéréo (re-appariement) : {len(paired_all)} "
                    f"(sur {len(left_cache)} gauche, slack ±{MAX_SYNC_SLACK_FRAMES})")
            if coupled_pairs and len(coupled_pairs) < min(len(left_cache), len(right_cache)) * 0.5:
                self.log.emit(
                    f"  ⚠ Paires couplées : {len(coupled_pairs)} "
                    f"(G={len(left_cache)} D={len(right_cache)}) - "
                    "souvent une seule caméra détecte au même instant.")

            paired_valid = [
                (le, re) for le, re in paired_all
                if _n_calib_points(le.get('obj_pts')) >= MIN_CALIB_CORNERS
                and _n_calib_points(re.get('obj_pts')) >= MIN_CALIB_CORNERS
            ]
            if len(paired_valid) < len(paired_all):
                self.log.emit(
                    f"  Paires filtrées (< {MIN_CALIB_CORNERS} coins) : "
                    f"{len(paired_all) - len(paired_valid)} ignorée(s) "
                    f"→ {len(paired_valid)} restantes")
            paired_intrinsic = _subsample_pairs(paired_valid, MAX_INTRINSIC_VIEWS)
            stereo_pool_size = min(
                len(paired_valid), MAX_STEREO_PAIRS + STEREO_POOL_MARGIN)
            paired_stereo = _select_stereo_pairs_quality(
                paired_valid, stereo_pool_size, log_fn=self.log.emit)
            left_calib_cache = [le for le, _ in paired_intrinsic]
            right_calib_cache = [re for _, re in paired_intrinsic]
            left_calib_paths = [e['path'] for e in left_calib_cache]
            right_calib_paths = [e['path'] for e in right_calib_cache]
            if len(paired_all) > MAX_INTRINSIC_VIEWS:
                self.log.emit(
                    f"  Intrinsèque : {len(paired_all)} paires sync "
                    f"→ {len(paired_intrinsic)} (max {MAX_INTRINSIC_VIEWS})")
            if len(paired_all) > stereo_pool_size:
                self.log.emit(
                    f"  Stéréo      : {len(paired_all)} paires sync "
                    f"→ pool {len(paired_stereo)} (cible {MAX_STEREO_PAIRS} "
                    f"+ marge {STEREO_POOL_MARGIN})")
            self.log.emit(
                f"  → calibrateCamera : {len(left_calib_cache)} G/D  |  "
                f"stereoCalibrate : cible {MAX_STEREO_PAIRS} paires "
                f"(pool {len(paired_stereo)})")
            calib_stats = {
                'frames_analyzed': available,
                'scan_left': scan_l,
                'scan_right': scan_r,
                'pairs_stereo': len(paired_all),
                'calib_left': len(left_calib_cache),
                'calib_right': len(right_calib_cache),
                'stereo_pairs_used': len(paired_stereo),
            }
            self.progress.emit(55)

            # ── Preview de la 1ère frame détectée (coins ChArUco visibles) ──
            for _preview_path in left_paths[:1] or right_paths[:1]:
                try:
                    _prev_img = cv.imread(_preview_path)
                    if _prev_img is not None:
                        _pg = cv.cvtColor(_prev_img, cv.COLOR_BGR2GRAY)
                        _pc, _pi, _ = _detect_charuco(
                            _pg, board_chk, detector_chk)
                        _ann = _prev_img.copy()
                        if _pc is not None and _pi is not None:
                            cv.aruco.drawDetectedCornersCharuco(
                                _ann, _pc, _pi)
                            cv.putText(
                                _ann,
                                f"1ère frame : {len(_pi)} coins ChArUco détectés",
                                (10, 36), cv.FONT_HERSHEY_SIMPLEX,
                                0.9, (0, 255, 80), 2, cv.LINE_AA)
                        else:
                            cv.putText(
                                _ann, "0 coins sur 1ère frame sauvegardée !",
                                (10, 36), cv.FONT_HERSHEY_SIMPLEX,
                                0.9, (0, 60, 255), 2, cv.LINE_AA)
                        self.frame_preview.emit(
                            (_ann, f"1ère frame retenue - {_preview_path}"))
                except Exception:
                    pass

            # ── Étape 3 : calibration intrinsèque ─────────────────────────
            self.phase.emit("Étape 3 - calibration intrinsèque gauche")
            self.log.emit("")
            self.log.emit("── Step 3: Intrinsic calibration - Left ───────────")
            try:
                mtx1, dist1, rmse1, objpts_l, imgpts_l, info_l, sz_l = calibrate_charuco(
                    left_calib_paths, self.squares_x, self.squares_y,
                    self.square_length_mm, self.marker_length_mm,
                    self.aruco_dict_id, self.log.emit,
                    progress_fn=self.progress.emit,
                    prog_start=57, prog_end=70,
                    cancelled_fn=lambda: self._cancelled,
                    live_fn=self.live_status.emit,
                    busy_fn=self.opencv_busy.emit,
                    detection_cache=left_calib_cache,
                    **_calib_charuco_opencv_opts(self))
            except cv.error as _e:
                err = str(_e)
                hint = (
                    "  → Cause probable : mauvais colonnes/lignes.\n"
                    f"  → Actuel : {self.squares_x}×{self.squares_y}. "
                    "Utilisez le bandeau de suggestion ou ajustez manuellement."
                    if "count >= 6" not in err and "6 points" not in err
                    else
                    f"  → Certaines vues avaient < {MIN_CALIB_CORNERS} coins "
                    "(requis par OpenCV). Relancez la calibration : le filtrage "
                    "automatique est maintenant appliqué."
                )
                self.log.emit(f"[ERREUR OpenCV] {_e}\n{hint}")
                self.error.emit(str(_e))
                return
            if mtx1 is None:
                return  # annulé
            self.log.emit(f"  RMSE left  : {rmse1:.4f} px")
            if not _mono_calib_ok(rmse1, mtx1, dist1, sz_l, "Intrinsèque gauche",
                                  self.log.emit):
                self.error.emit(
                    f"Calibration gauche rejetée : RMSE {rmse1:.2f} px "
                    f"(max {MAX_MONO_RMSE_PX} px).")
                return
            np.save('camera_parameters/mtx1.npy', mtx1)
            np.save('camera_parameters/dist1.npy', dist1)
            self.log.emit("  ✓ Checkpoint : mtx1.npy / dist1.npy sauvegardés (gauche)")
            self.progress.emit(70)
            self.stereo_ready.emit({'rmse_left': float(rmse1)})
            gc.collect()

            self.phase.emit("Étape 3 - calibration intrinsèque droite")
            self.log.emit("")
            self.log.emit("── Step 3: Intrinsic calibration - Right ──────────")
            try:
                mtx2, dist2, rmse2, objpts_r, imgpts_r, info_r, sz_r = calibrate_charuco(
                    right_calib_paths, self.squares_x, self.squares_y,
                    self.square_length_mm, self.marker_length_mm,
                    self.aruco_dict_id, self.log.emit,
                    progress_fn=self.progress.emit,
                    prog_start=71, prog_end=84,
                    cancelled_fn=lambda: self._cancelled,
                    live_fn=self.live_status.emit,
                    busy_fn=self.opencv_busy.emit,
                    detection_cache=right_calib_cache,
                    **_calib_charuco_opencv_opts(self))
            except cv.error as _e:
                err = str(_e)
                hint = (
                    "  → Cause probable : mauvais colonnes/lignes.\n"
                    f"  → Actuel : {self.squares_x}×{self.squares_y}. "
                    "Utilisez le bandeau de suggestion ou ajustez manuellement."
                    if "count >= 6" not in err and "6 points" not in err
                    else
                    f"  → Certaines vues avaient < {MIN_CALIB_CORNERS} coins "
                    "(requis par OpenCV). Relancez la calibration : le filtrage "
                    "automatique est maintenant appliqué."
                )
                self.log.emit(f"[ERREUR OpenCV] {_e}\n{hint}")
                self.error.emit(str(_e))
                return
            if mtx2 is None:
                return  # annulé
            self.log.emit(f"  RMSE right : {rmse2:.4f} px")
            if not _mono_calib_ok(rmse2, mtx2, dist2, sz_r, "Intrinsèque droite",
                                  self.log.emit):
                self.log.emit(
                    "  → mtx1/dist1 conservés ; mtx2/dist2 non enregistrés.")
                self.error.emit(
                    f"Calibration droite rejetée : RMSE {rmse2:.2e} px "
                    f"(max {MAX_MONO_RMSE_PX} px). "
                    "Vérifiez visibilité mire caméra droite et config board.")
                return
            np.save('camera_parameters/mtx2.npy', mtx2)
            np.save('camera_parameters/dist2.npy', dist2)
            self.log.emit("  ✓ Checkpoint : mtx2.npy / dist2.npy sauvegardés (droite)")
            self.progress.emit(83)
            self.stereo_ready.emit({'rmse_right': float(rmse2)})

            mtx1, mtx2 = _harmonize_identical_camera_focals(
                mtx1, mtx2, self.log.emit)

            # ── Étape 4 : calibration stéréo ──────────────────────────────
            self.phase.emit("Étape 4 - calibration stéréo")
            self.log.emit("")
            self.log.emit("── Step 4: Stereo calibration ─────────────────────")
            try:
                R, T, F, rmse3, rect_dy_median = stereo_calibrate_charuco_paired(
                    mtx1, dist1, mtx2, dist2,
                    paired_stereo,
                    self.squares_x, self.squares_y,
                    self.square_length_mm, self.marker_length_mm,
                    self.aruco_dict_id, self.log.emit,
                    progress_fn=self.progress.emit,
                    cancelled_fn=lambda: self._cancelled,
                    live_fn=self.live_status.emit,
                    busy_fn=self.opencv_busy.emit,
                    target_pairs=MAX_STEREO_PAIRS)
            except cv.error as _e:
                self.log.emit(
                    f"[ERREUR OpenCV stéréo] {_e}\n"
                    "  → Vérifiez que colonnes/lignes correspondent au board physique.")
                self.error.emit(str(_e))
                return
            rt_text, baseline_mm, _ = _stereo_extrinsics_summary(
                R, T, rmse_stereo=rmse3, rmse_left=rmse1, rmse_right=rmse2)
            self.log.emit("  ── Extrinsèque stéréo (R / T) ──")
            for line in rt_text.splitlines():
                self.log.emit(f"  {line}")
            self.stereo_ready.emit({
                'R': R, 'T': T,
                'rmse_stereo': rmse3,
                'rmse_left': rmse1,
                'rmse_right': rmse2,
                'baseline_mm': baseline_mm,
            })

            if rmse3 > MAX_STEREO_RMSE_PX:
                self.log.emit(
                    f"  ✗ RMSE stéréo {rmse3:.1f} px - invalide (seuil {MAX_STEREO_RMSE_PX} px).")
                self.log.emit(
                    "  → Causes possibles : coins ChArUco imprécis, config board (colonnes/lignes),\n"
                    "     paires G/D aberrantes, ou sync/trim incorrects.\n"
                    "  → Essayez précision « Haute » et vérifiez la config board.\n"
                    "  → mtx/dist intrinsèques conservés ; R/T/F non enregistrés.")
                self.error.emit(
                    f"Calibration stéréo rejetée : RMSE {rmse3:.1f} px "
                    f"(max {MAX_STEREO_RMSE_PX} px).")
                return
            if (rect_dy_median is not None
                    and rect_dy_median > MAX_RECT_DY_REJECT_PX):
                self.log.emit(
                    f"  ✗ Rectification |ΔY| {rect_dy_median:.1f} px "
                    f"(max {MAX_RECT_DY_REJECT_PX} px) - R/T inutilisables.")
                self.log.emit(
                    "  → Vérifiez zoom identique G/D, sync, et visibilité mire des deux côtés.")
                self.error.emit(
                    f"Calibration stéréo rejetée : alignement rectifié "
                    f"{rect_dy_median:.1f} px (max {MAX_RECT_DY_REJECT_PX} px).")
                return
            self.progress.emit(93)

            # ── Étape 5 : sauvegarde ───────────────────────────────────────
            self.log.emit("")
            self.log.emit("── Step 5: Saving parameters ──────────────────────")
            np.save('camera_parameters/mtx1.npy',  mtx1)
            np.save('camera_parameters/dist1.npy', dist1)
            np.save('camera_parameters/mtx2.npy',  mtx2)
            np.save('camera_parameters/dist2.npy', dist2)
            np.save('camera_parameters/R.npy',     R)
            np.save('camera_parameters/T.npy',     T)
            np.save('camera_parameters/F.npy',     F)
            np.save('camera_parameters/stereo_rmse.npy', np.array(rmse3))
            np.save('camera_parameters/left_rmse.npy', np.array(rmse1))
            np.save('camera_parameters/right_rmse.npy', np.array(rmse2))
            save_charuco_config(
                self.squares_x, self.squares_y,
                self.square_length_mm, self.marker_length_mm,
                self.aruco_dict_id)
            save_videos_txt(self.left_video, self.right_video)
            self.log.emit("  mtx1.npy  dist1.npy       → caméra gauche")
            self.log.emit("  mtx2.npy  dist2.npy       → caméra droite")
            self.log.emit("  R.npy  T.npy  F.npy       → stéréo (rotation, translation, fondamentale)")
            self.log.emit(f"  stereo_rmse.npy          → {rmse3:.4f} pixels")
            self.log.emit("  videos.txt                → chemins des vidéos")
            self.log.emit("✓ Paramètres enregistrés")

            # ── Sauvegarde des images de vérification ──────────────────────
            self.log.emit("")
            self.log.emit("── Step 6: Saving verify images ───────────────────")
            verify_dir = 'camera_parameters/verify'
            os.makedirs(verify_dir, exist_ok=True)
            def _save_verify_png(item, out_path, label):
                item['label'] = label
                frame = cv.imread(item['path'], 1)
                if frame is None:
                    self.log.emit(f"  [AVERTISSEMENT] Image illisible : {item['path']}")
                    return
                ann = cv.aruco.drawDetectedCornersCharuco(
                    frame, item['corners'], item['ids'])
                cv.imwrite(out_path, ann)
                item['verify_png'] = out_path
                del frame, ann

            for i, item in enumerate(info_l):
                _save_verify_png(
                    item, os.path.join(verify_dir, f"left_{i:04d}.png"),
                    f"Left {i+1:03d}")
            for i, item in enumerate(info_r):
                _save_verify_png(
                    item, os.path.join(verify_dir, f"right_{i:04d}.png"),
                    f"Right {i+1:03d}")

            # ChArUco : chaque frame peut avoir un nombre différent de coins
            # → dtype=object requis pour les tableaux à tailles variables
            np.save(os.path.join(verify_dir, 'corners_left.npy'),
                    np.array([it['corners'] for it in info_l], dtype=object))
            np.save(os.path.join(verify_dir, 'corners_right.npy'),
                    np.array([it['corners'] for it in info_r], dtype=object))
            np.save(os.path.join(verify_dir, 'objpoints_left.npy'),
                    np.array(objpts_l, dtype=object))
            np.save(os.path.join(verify_dir, 'objpoints_right.npy'),
                    np.array(objpts_r, dtype=object))
            np.save(os.path.join(verify_dir, 'image_sizes.npy'),
                    np.array([list(sz_l), list(sz_r)]))
            np.save(os.path.join(verify_dir, 'calib_stats.npy'),
                    np.array(list(calib_stats.values())),
                    allow_pickle=False)
            with open(os.path.join(verify_dir, 'calib_stats.txt'), 'w',
                      encoding='utf-8') as _sf:
                for k, v in calib_stats.items():
                    _sf.write(f"{k}={v}\n")

            self.log.emit(
                f"  verify/left_XXXX.png   × {len(info_l)} frames gauche")
            self.log.emit(
                f"  verify/right_XXXX.png  × {len(info_r)} frames droite")
            self.log.emit("  verify/corners_left/right.npy  → coins ChArUco détectés")
            self.log.emit("  verify/objpoints_left/right.npy → points 3D ChArUco")
            self.log.emit("  verify/image_sizes.npy          → résolutions")
            self.log.emit("✓ Données de vérification enregistrées")

            self.progress.emit(100)
            self.log.emit("")
            self.log.emit("══════════════════════════════════════════════════")
            self.log.emit("✓ Calibration terminée - tous les paramètres sont enregistrés.")
            self.log.emit("  Redémarrez l'app : la calibration sera rechargée automatiquement.")
            self.log.emit("══════════════════════════════════════════════════")

            # Émettre les données pour la grille de vérification
            self.verify_ready.emit({
                'left':            info_l,
                'right':           info_r,
                'objpoints_left':  objpts_l,
                'objpoints_right':  objpts_r,
                'image_size_left':  sz_l,
                'image_size_right': sz_r,
                'stats':           calib_stats,
                'R': R, 'T': T,
                'rmse_stereo': rmse3,
                'rmse_left': rmse1,
                'rmse_right': rmse2,
            })

        except Exception as exc:
            tb = traceback.format_exc()
            self.log.emit(f"[ERREUR]\n{tb}")
            self.error.emit(f"{exc}\n\n{tb}")
        finally:
            self.finished.emit()


# ── Onglet Calibration ────────────────────────────────────────────────────────

class CalibrationTab(QWidget):
    profile_changed = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.setContentsMargins(12, 12, 12, 12)

        self._left_video  = ""
        self._right_video = ""

        # ── Sélecteurs vidéo ─────────────────────────────────────────────────
        for side in ('Left', 'Right'):
            row = QHBoxLayout()
            btn = QPushButton(f"Select {side} Video")
            btn.setFixedWidth(160)
            btn.clicked.connect(lambda _, s=side.lower(): self._pick_video(s))
            lbl = QLabel("No file selected")
            lbl.setWordWrap(True)
            row.addWidget(btn)
            row.addWidget(lbl, stretch=1)
            layout.addLayout(row)
            setattr(self, f'_lbl_{side.lower()}', lbl)

        # ── Paramètres ChArUco ────────────────────────────────────────────────
        charuco_group = QGroupBox("Cible ChArUco")
        charuco_group.setStyleSheet(
            "QGroupBox{font-weight:bold; color:#93c5fd; border:1px solid #334155;"
            " border-radius:5px; margin-top:6px; padding-top:4px;}"
            "QGroupBox::title{subcontrol-origin:margin; left:8px;}")
        params = QHBoxLayout(charuco_group)
        params.setSpacing(8)

        def _lbl(text):
            l = QLabel(text)
            l.setStyleSheet("color:#cbd5e1; font-weight:normal;")
            return l

        params.addWidget(_lbl("Colonnes (X) :"))
        self.spin_sq_x = QSpinBox()
        self.spin_sq_x.setRange(3, 40)
        self.spin_sq_x.setValue(5)
        self.spin_sq_x.setMinimumWidth(55)
        self.spin_sq_x.setToolTip("Nombre de cases (colonnes) du board ChArUco")
        params.addWidget(self.spin_sq_x)

        params.addWidget(_lbl("Lignes (Y) :"))
        self.spin_sq_y = QSpinBox()
        self.spin_sq_y.setRange(3, 40)
        self.spin_sq_y.setValue(7)
        self.spin_sq_y.setMinimumWidth(55)
        self.spin_sq_y.setToolTip("Nombre de cases (lignes) du board ChArUco")
        params.addWidget(self.spin_sq_y)

        params.addWidget(_lbl("Case (mm) :"))
        self.spin_square = QDoubleSpinBox()
        self.spin_square.setRange(0.1, 1000.0)
        self.spin_square.setValue(49.5)
        self.spin_square.setDecimals(1)
        self.spin_square.setMinimumWidth(75)
        self.spin_square.setToolTip("Taille d'une case du damier en mm")
        params.addWidget(self.spin_square)

        params.addWidget(_lbl("Marqueur (mm) :"))
        self.spin_marker = QDoubleSpinBox()
        self.spin_marker.setRange(0.1, 1000.0)
        self.spin_marker.setValue(37.0)
        self.spin_marker.setDecimals(1)
        self.spin_marker.setMinimumWidth(75)
        self.spin_marker.setToolTip(
            "Taille du marqueur ArUco en mm (doit être < taille case)")
        params.addWidget(self.spin_marker)

        params.addWidget(_lbl("Dict :"))
        self.combo_dict = QComboBox()
        for k in CHARUCO_DICT_MAP:
            self.combo_dict.addItem(k)
        self.combo_dict.setCurrentText("DICT_5X5_50")
        self.combo_dict.setMinimumWidth(120)
        self.combo_dict.setToolTip("Dictionnaire ArUco utilisé pour le board")
        params.addWidget(self.combo_dict)

        params.addStretch()
        layout.addWidget(charuco_group)

        # ── Précision calibration (nb de vues / paires) ─────────────────────
        quality_row = QHBoxLayout()
        ql = QLabel("Précision :")
        ql.setStyleSheet("color:#cbd5e1;")
        quality_row.addWidget(ql)
        self.combo_quality = QComboBox()
        for label, (n_intr, n_stereo) in CALIB_QUALITY_PRESETS.items():
            self.combo_quality.addItem(label, (n_intr, n_stereo))
        self.combo_quality.setCurrentIndex(0)
        self.combo_quality.setMinimumWidth(220)
        self.combo_quality.setToolTip(
            "Nombre de vues intrinsèque (G/D) et de paires stéréo.\n"
            "Plus élevé = meilleure précision potentielle mais scan + calibrateCamera\n"
            "et stereoCalibrate beaucoup plus longs en 1920×1080.\n"
            "Durées typiques HD (CPU bureau) :\n"
            "  Rapide (80/120)     ≈ 25–40 min\n"
            "  Standard (120/160)  ≈ 40–55 min\n"
            "  Haute préc. (160/200) ≈ 50–70 min\n"
            "  Maximum (200/250)   ≈ 65–90 min")
        quality_row.addWidget(self.combo_quality)
        self.lbl_quality_hint = QLabel(
            "80 vues G/D · 120 paires stéréo (défaut)")
        self.lbl_quality_hint.setStyleSheet("color:#94a3b8; font-size:11px;")
        quality_row.addWidget(self.lbl_quality_hint, stretch=1)
        self.combo_quality.currentIndexChanged.connect(self._on_quality_changed)
        layout.addLayout(quality_row)

        # ── Moteur A/B + profil actif Mesure ────────────────────────────────
        engine_row = QHBoxLayout()
        el = QLabel("Moteur :")
        el.setStyleSheet("color:#cbd5e1;")
        engine_row.addWidget(el)
        self.combo_engine = QComboBox()
        self.combo_engine.addItem("Classique (scan 1:1)", "classic")
        self.combo_engine.addItem("Rapide v2 (stride + cache mono)", "fast_v2")
        self.combo_engine.setMinimumWidth(260)
        self.combo_engine.setToolTip(
            "Classique : scan frame par frame (comportement actuel).\n"
            "Rapide v2 : pas couplé configurable + fenêtre dense + vues mono.")
        self.combo_engine.currentIndexChanged.connect(self._on_engine_changed)
        engine_row.addWidget(self.combo_engine)

        engine_row.addWidget(QLabel("Saut sans mire :"))
        self.spin_stride = QSpinBox()
        self.spin_stride.setRange(3, 15)
        self.spin_stride.setValue(5)
        self.spin_stride.setToolTip(
            "Images ignorées entre deux analyses quand la mire n'est pas visible.\n"
            "Plus élevé = moins de détections et scan plus rapide.")
        engine_row.addWidget(self.spin_stride)

        engine_row.addWidget(QLabel("Rafale après mire :"))
        self.spin_dense = QSpinBox()
        self.spin_dense.setRange(10, 60)
        self.spin_dense.setValue(25)
        self.spin_dense.setToolTip(
            "Après une détection, nombre de pas d'analyse en rafale.\n"
            "Plus bas = moins de paires accumulées pendant le scan.")
        engine_row.addWidget(self.spin_dense)

        engine_row.addWidget(QLabel("Pas rafale :"))
        self.spin_dense_stride = QSpinBox()
        self.spin_dense_stride.setRange(1, 10)
        self.spin_dense_stride.setValue(1)
        self.spin_dense_stride.setToolTip(
            "Pendant la rafale après mire : 1 image analysée tous les N frames "
            "(G et D avancent ensemble, sync conservée).\n"
            "1 = chaque frame · 3 = 1 sur 3 (utile en 60 fps).")
        engine_row.addWidget(self.spin_dense_stride)
        engine_row.addStretch()
        layout.addLayout(engine_row)

        profile_row = QHBoxLayout()
        pl = QLabel("Profil actif (Mesure) :")
        pl.setStyleSheet("color:#cbd5e1;")
        profile_row.addWidget(pl)
        self.combo_active_profile = QComboBox()
        for key, label in CALIB_PROFILE_LABELS.items():
            self.combo_active_profile.addItem(label, key)
        self.combo_active_profile.setMinimumWidth(160)
        self.combo_active_profile.setToolTip(
            "Quelle calibration l'onglet Mesure charge (rectification, mesure).")
        self.combo_active_profile.currentIndexChanged.connect(
            self._on_active_profile_changed)
        profile_row.addWidget(self.combo_active_profile)
        self.lbl_profile_status = QLabel()
        self.lbl_profile_status.setStyleSheet("color:#94a3b8; font-size:11px;")
        profile_row.addWidget(self.lbl_profile_status, stretch=1)
        layout.addLayout(profile_row)
        self._refresh_profile_status()
        active = load_active_calib_profile()
        idx = self.combo_active_profile.findData(active)
        if idx >= 0:
            self.combo_active_profile.blockSignals(True)
            self.combo_active_profile.setCurrentIndex(idx)
            self.combo_active_profile.blockSignals(False)

        # ── Boutons Run / Cancel ─────────────────────────────────────────────
        run_row = QHBoxLayout()
        run_row.addStretch()
        self.btn_run = QPushButton("▶  Lancer la calibration ChArUco")
        self.btn_run.setFixedHeight(36)
        self.btn_run.setMinimumWidth(240)
        self.btn_run.setStyleSheet(
            "QPushButton{background:#15803d;color:#fff;border-radius:5px;"
            " font-weight:bold; font-size:12px;}"
            "QPushButton:hover{background:#16a34a;}"
            "QPushButton:disabled{background:#374151; color:#6b7280;}")
        self.btn_run.clicked.connect(self._run)
        run_row.addWidget(self.btn_run)

        self.btn_cancel = QPushButton("✕  Annuler")
        self.btn_cancel.setFixedHeight(36)
        self.btn_cancel.setMinimumWidth(110)
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.setStyleSheet(
            "QPushButton{background:#7f1d1d;color:#fff;border-radius:5px;"
            " font-weight:bold; font-size:12px;}"
            "QPushButton:hover{background:#b91c1c;}"
            "QPushButton:disabled{background:#374151; color:#6b7280;}")
        self.btn_cancel.clicked.connect(self._cancel)
        run_row.addWidget(self.btn_cancel)
        layout.addLayout(run_row)
        self._on_engine_changed()

        # ── Statut temps réel (mis à jour chaque seconde, même si OpenCV ne log pas)
        self.lbl_live_status = QLabel("En attente")
        self.lbl_live_status.setWordWrap(True)
        self.lbl_live_status.setMinimumHeight(36)
        self.lbl_live_status.setStyleSheet(
            "color:#7ec8e3; font-size:13px; font-weight:bold;"
            " background:#0f172a; border:1px solid #334155; border-radius:4px;"
            " padding:6px;")
        layout.addWidget(self.lbl_live_status)

        # ── Progress bar ─────────────────────────────────────────────────────
        self.progress = QProgressBar()
        self.progress.setValue(0)
        layout.addWidget(self.progress)

        # ── R / T stéréo (baseline ≈ 800 mm) ─────────────────────────────────
        self._stereo_group = QGroupBox("Stéréo R / T")
        self._stereo_group.setStyleSheet(
            "QGroupBox{font-weight:bold; color:#86efac; border:1px solid #334155;"
            " border-radius:5px; margin-top:6px; padding-top:4px;}"
            "QGroupBox::title{subcontrol-origin:margin; left:8px;}")
        _stereo_lay = QVBoxLayout(self._stereo_group)
        _stereo_lay.setContentsMargins(8, 6, 8, 6)
        self.lbl_stereo_baseline = QLabel("Baseline : -")
        self.lbl_stereo_baseline.setFont(QFont("Segoe UI", 12, QFont.Weight.Bold))
        self.lbl_stereo_baseline.setStyleSheet("color:#94a3b8;")
        _stereo_lay.addWidget(self.lbl_stereo_baseline)
        self.lbl_stereo_rt = QLabel(
            "R et T apparaîtront ici après calibration stéréo réussie.")
        self.lbl_stereo_rt.setFont(QFont("Courier New", 9))
        self.lbl_stereo_rt.setWordWrap(True)
        self.lbl_stereo_rt.setStyleSheet("color:#cbd5e1;")
        _stereo_lay.addWidget(self.lbl_stereo_rt)
        self._stereo_group.setVisible(True)
        layout.addWidget(self._stereo_group)

        # ── Export / import calibration (.zip) ───────────────────────────────
        io_row = QHBoxLayout()
        io_row.addStretch()
        self.btn_export_calib = QPushButton("💾  Exporter calibration…")
        self.btn_export_calib.setFixedHeight(30)
        self.btn_export_calib.setToolTip(
            "Enregistre mtx/dist, R/T/F, sync et chemins vidéo dans un fichier .zip")
        self.btn_export_calib.clicked.connect(self._export_calibration)
        io_row.addWidget(self.btn_export_calib)
        self.btn_import_calib = QPushButton("📂  Charger calibration…")
        self.btn_import_calib.setFixedHeight(30)
        self.btn_import_calib.setToolTip(
            "Restaure une calibration depuis un .zip (remplace camera_parameters/)")
        self.btn_import_calib.clicked.connect(self._import_calibration)
        io_row.addWidget(self.btn_import_calib)
        layout.addLayout(io_row)

        # ── Bandeau suggestion config ─────────────────────────────────────────
        self._suggest_bar = QWidget()
        self._suggest_bar.setVisible(False)
        self._suggest_bar.setStyleSheet(
            "background:#78350f; border-radius:5px; padding:2px;")
        _sb_layout = QHBoxLayout(self._suggest_bar)
        _sb_layout.setContentsMargins(8, 4, 8, 4)
        self._suggest_lbl = QLabel()
        self._suggest_lbl.setStyleSheet("color:#fde68a; font-weight:bold;")
        _sb_layout.addWidget(self._suggest_lbl, stretch=1)
        self._suggest_btn = QPushButton("✓ Appliquer et relancer")
        self._suggest_btn.setFixedHeight(28)
        self._suggest_btn.setStyleSheet(
            "QPushButton{background:#d97706;color:#fff;border-radius:4px;"
            " font-weight:bold;}"
            "QPushButton:hover{background:#f59e0b;}")
        self._suggest_btn.clicked.connect(self._apply_suggestion)
        _sb_layout.addWidget(self._suggest_btn)
        self._suggest_dismiss = QPushButton("✕")
        self._suggest_dismiss.setFixedSize(28, 28)
        self._suggest_dismiss.setStyleSheet(
            "QPushButton{background:#92400e;color:#fde68a;"
            " border-radius:4px; font-weight:bold;}"
            "QPushButton:hover{background:#b45309;}")
        self._suggest_dismiss.clicked.connect(
            lambda: self._suggest_bar.setVisible(False))
        _sb_layout.addWidget(self._suggest_dismiss)
        self._pending_suggestion = (None, None)
        layout.addWidget(self._suggest_bar)

        # ── Splitter : logs (haut) / grille de vérification (bas) ────────────
        # ── Splitter horizontal : gauche=logs/verify  droite=preview ─────────
        self._h_splitter = QSplitter(Qt.Orientation.Horizontal)

        self._splitter = QSplitter(Qt.Orientation.Vertical)

        self.log_area = QTextEdit()
        self.log_area.setReadOnly(True)
        self.log_area.setFont(QFont("Courier New", 9))
        self._splitter.addWidget(self.log_area)

        # Container pour la VerifyGrid - on y injecte le contenu après calibration
        self._verify_container = QWidget()
        self._verify_container_layout = QVBoxLayout(self._verify_container)
        self._verify_container_layout.setContentsMargins(0, 0, 0, 0)
        self._verify_placeholder = QLabel(
            "La grille de vérification apparaîtra ici après la calibration.")
        self._verify_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._verify_placeholder.setStyleSheet("color:#888; font-size:12px;")
        self._verify_container_layout.addWidget(self._verify_placeholder)

        self._splitter.addWidget(self._verify_container)
        self._splitter.setSizes([300, 0])

        self._h_splitter.addWidget(self._splitter)

        # ── Preview panel ─────────────────────────────────────────────────────
        self._preview_panel = QWidget()
        _prev_layout = QVBoxLayout(self._preview_panel)
        _prev_layout.setContentsMargins(4, 0, 0, 0)
        _prev_layout.setSpacing(4)
        self._preview_title = QLabel("Aperçu - en attente")
        self._preview_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._preview_title.setStyleSheet(
            "color:#aaa; font-size:11px; font-weight:bold;")
        _prev_layout.addWidget(self._preview_title)
        self._preview_lbl = QLabel()
        self._preview_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._preview_lbl.setMinimumSize(320, 240)
        self._preview_lbl.setStyleSheet(
            "background:#111; border:1px solid #333; border-radius:4px;")
        self._preview_lbl.setText("Aucun aperçu")
        _prev_layout.addWidget(self._preview_lbl, stretch=1)
        self._h_splitter.addWidget(self._preview_panel)

        self._h_splitter.setSizes([600, 320])

        layout.addWidget(self._h_splitter, stretch=1)

        # Surveillance UI : alerte si plus aucun log pendant un job long
        self._job_running = False
        self._last_log_at = 0.0
        self._stall_level = 0
        self._job_started_at = 0.0
        self._phase_started_at = 0.0
        self._current_phase = ""
        self._watchdog = QTimer(self)
        self._watchdog.setInterval(3000)
        self._watchdog.timeout.connect(self._check_stall)
        self._ui_tick = QTimer(self)
        self._ui_tick.setInterval(1000)
        self._ui_tick.timeout.connect(self._ui_tick_status)

        # Restauration automatique si une calibration précédente existe
        self._try_restore()

    def _export_calibration(self):
        if not calibration_package_complete(os.path.join(_APP_ROOT, 'camera_parameters')):
            self.log_area.append(
                "[!] Rien à exporter - lancez une calibration stéréo complète d'abord.")
            return
        stamp = datetime.now().strftime('%Y%m%d_%H%M')
        default_name = f"AquaMeasure_calib_{stamp}.zip"
        path, _ = QFileDialog.getSaveFileName(
            self, "Exporter la calibration", default_name,
            "Archive calibration (*.zip);;Tous les fichiers (*)")
        if not path:
            return
        if not path.lower().endswith('.zip'):
            path += '.zip'
        err = export_calibration_package(path)
        if err:
            self.log_area.append(f"[!] Export : {err}")
        else:
            self.log_area.append(f"✓ Calibration exportée : {path}")

    def _import_calibration(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Charger une calibration", "",
            "Archive calibration (*.zip);;Tous les fichiers (*)")
        if not path:
            return
        err = import_calibration_package(path)
        if err:
            self.log_area.append(f"[!] Import : {err}")
            return
        self.log_area.append(f"✓ Calibration importée depuis : {path}")
        self.log_area.append("  Rechargement des paramètres…")
        self._try_restore()

    # ── Restauration au démarrage ─────────────────────────────────────────────

    def _try_restore(self):
        """Recharge la calibration précédente si les fichiers existent."""
        required = [
            'camera_parameters/mtx1.npy',  'camera_parameters/dist1.npy',
            'camera_parameters/mtx2.npy',  'camera_parameters/dist2.npy',
            'camera_parameters/R.npy',     'camera_parameters/T.npy',
        ]
        if any(not os.path.exists(f) for f in required):
            self._load_stereo_panel_from_disk()
            return

        self.log_area.append("✓ Previous calibration found in camera_parameters/")
        self._load_stereo_panel_from_disk()

        # Restaurer les chemins vidéo
        videos_txt = cam_param('videos.txt')
        if os.path.exists(videos_txt):
            try:
                with open(videos_txt, encoding='utf-8') as f:
                    lines = [l.strip() for l in f.readlines() if l.strip()]
                if len(lines) >= 2:
                    left_p, right_p = lines[0], lines[1]
                    self._left_video  = left_p
                    self._right_video = right_p
                    self._lbl_left.setText(left_p)
                    self._lbl_right.setText(right_p)
                    self.log_area.append(f"  Left  : {left_p}")
                    self.log_area.append(f"  Right : {right_p}")
            except Exception:
                pass

        # Restaurer la grille de vérification
        vdir = 'camera_parameters/verify'
        cl   = os.path.join(vdir, 'corners_left.npy')
        cr   = os.path.join(vdir, 'corners_right.npy')
        ol   = os.path.join(vdir, 'objpoints_left.npy')
        orr  = os.path.join(vdir, 'objpoints_right.npy')
        sz   = os.path.join(vdir, 'image_sizes.npy')

        if not all(os.path.exists(p) for p in (cl, cr, ol, orr, sz)):
            self.log_area.append("  (No verify grid - run calibration to generate it)")
            return

        try:
            # allow_pickle=True : nécessaire pour les tableaux à tailles variables
            # (ChArUco détecte un nombre différent de coins par frame)
            corners_l  = np.load(cl,  allow_pickle=True)
            corners_r  = np.load(cr,  allow_pickle=True)
            objpts_l   = list(np.load(ol,  allow_pickle=True))
            objpts_r   = list(np.load(orr, allow_pickle=True))
            sizes      = np.load(sz).astype(int)
            sz_l       = tuple(sizes[0])
            sz_r       = tuple(sizes[1])

            def load_side(corners_arr, prefix, side_name):
                items = []
                for i, c in enumerate(corners_arr):
                    img_path = os.path.join(vdir, f"{prefix}_{i:04d}.png")
                    if not os.path.exists(img_path):
                        continue
                    frame_ann = cv.imread(img_path, 1)
                    if frame_ann is None:
                        continue
                    items.append({
                        'path':            img_path,
                        'frame':           frame_ann,
                        'frame_annotated': frame_ann,
                        'corners':         c.astype(np.float32),
                        'label':           f"{side_name} {i+1:03d}",
                    })
                return items

            info_l = load_side(corners_l, 'left',  'Left')
            info_r = load_side(corners_r, 'right', 'Right')

            stats = {}
            stats_txt = os.path.join(vdir, 'calib_stats.txt')
            if os.path.exists(stats_txt):
                with open(stats_txt, encoding='utf-8') as _sf:
                    for line in _sf:
                        if '=' in line:
                            k, v = line.strip().split('=', 1)
                            stats[k] = int(v)

            self.log_area.append(
                f"  Verify grid: {len(info_l)} vues calib (+ {len(info_r)} droite)"
                + (f"  |  scan {stats.get('scan_left', '?')}/{stats.get('frames_analyzed', '?')}"
                   if stats else "  (stats scan : relancer calibration v5)"))

            if info_l or info_r:
                self._show_verify_grid({
                    'left':             info_l,
                    'right':            info_r,
                    'objpoints_left':   objpts_l,
                    'objpoints_right':  objpts_r,
                    'image_size_left':  sz_l,
                    'image_size_right': sz_r,
                    'stats':            stats,
                })
        except Exception as exc:
            import traceback
            self.log_area.append(
                f"  [RESTORE WARNING] Could not load verify grid: {exc}\n"
                f"{traceback.format_exc()}")

    def import_from_sync(self, left_path: str, right_path: str):
        """Importe les vidéos et l'état de sync/trim depuis l'onglet Sync."""
        self._left_video  = left_path
        self._right_video = right_path
        self._lbl_left.setText(left_path)
        self._lbl_right.setText(right_path)

        lines = [f"✓ Vidéos importées depuis l'onglet Sync :"]
        lines.append(f"   Gauche : {left_path}")
        lines.append(f"   Droite : {right_path}")

        sync_path = 'camera_parameters/sync_frames.npy'
        if os.path.exists(sync_path):
            try:
                sf = np.load(sync_path)
                lf, rf = int(sf[0]), int(sf[1])
                lines.append(f"   Sync  - Gauche frame {lf}  |  Droite frame {rf}  "
                             f"(décalage {lf - rf:+d})")
            except Exception:
                pass

        trim_path = 'camera_parameters/trim_frames.npy'
        if os.path.exists(trim_path):
            try:
                tr = np.load(trim_path)
                li, lo, ri, ro = int(tr[0]), int(tr[1]), int(tr[2]), int(tr[3])
                lines.append(f"   Trim  - Gauche [{li}→{lo}]  |  Droite [{ri}→{ro}]")
            except Exception:
                pass

        lines.append("Prêt - cliquez 'Run Calibration' pour démarrer.")
        self.log_area.clear()
        self.log_area.append("\n".join(lines))

    def _pick_video(self, side: str):
        path, _ = QFileDialog.getOpenFileName(
            self, f"Select {side.capitalize()} Video", "",
            "Videos (*.mp4 *.avi *.mov *.mkv *.MOV);;All Files (*)")
        if path:
            setattr(self, f'_{side}_video', path)
            getattr(self, f'_lbl_{side}').setText(path)

    def _on_quality_changed(self, _index: int = 0):
        data = self.combo_quality.currentData()
        if data:
            intr, stereo = data
            dh = _calib_duration_hints(intr, stereo)
            self.lbl_quality_hint.setText(
                f"{intr} vues G/D · {stereo} paires stéréo - ≈ {dh['total']} (HD)")

    def _on_engine_changed(self, _index: int = 0):
        fast = self.combo_engine.currentData() == "fast_v2"
        self.spin_stride.setEnabled(fast)
        self.spin_dense.setEnabled(fast)
        self.spin_dense_stride.setEnabled(fast)
        if not hasattr(self, 'btn_run'):
            return
        if fast:
            self.btn_run.setText("▶  Lancer calibration Rapide v2")
        else:
            self.btn_run.setText("▶  Lancer la calibration ChArUco")

    def _refresh_profile_status(self):
        parts = []
        for key in CALIB_PROFILE_LABELS:
            tag = "✓" if calib_profile_complete(key) else "-"
            parts.append(f"{CALIB_PROFILE_LABELS[key]} {tag}")
        self.lbl_profile_status.setText("  |  ".join(parts))

    def _on_active_profile_changed(self, _index: int = 0):
        profile = self.combo_active_profile.currentData()
        if not profile:
            return
        save_active_calib_profile(profile)
        self._refresh_profile_status()
        self.profile_changed.emit(profile)

    def _connect_worker(self, worker):
        self._thread.started.connect(worker.run)
        worker.log.connect(self._append_log)
        worker.progress.connect(self._on_progress)
        worker.error.connect(self._append_log)
        worker.phase.connect(self._on_phase)
        worker.live_status.connect(self._on_live_status)
        worker.opencv_busy.connect(self._on_opencv_busy)
        worker.verify_ready.connect(self._show_verify_grid)
        worker.stereo_ready.connect(self._on_stereo_ready)
        worker.frame_preview.connect(self._show_frame_preview)
        worker.suggest_config.connect(self._on_suggest_config)
        worker.finished.connect(self._thread.quit)
        worker.finished.connect(worker.deleteLater)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.finished.connect(self._on_finished)

    def _run(self):
        if not self._left_video or not self._right_video:
            self.log_area.append("[ERROR] Select both videos first.")
            return
        self.btn_run.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        self.progress.setValue(0)
        self.log_area.clear()
        engine = self.combo_engine.currentData() or "classic"
        self.log_area.append(
            f"Starting calibration ({engine})…\n")

        intr, stereo = self.combo_quality.currentData() or (80, 120)

        self._thread = QThread()
        if engine == "fast_v2":
            self._worker = CalibrationWorkerFast()
            self._worker.scan_stride_idle = self.spin_stride.value()
            self._worker.dense_window = self.spin_dense.value()
            self._worker.dense_stride = self.spin_dense_stride.value()
        else:
            self._worker = CalibrationWorker()
        self._worker.left_video       = self._left_video
        self._worker.right_video      = self._right_video
        self._worker.squares_x        = self.spin_sq_x.value()
        self._worker.squares_y        = self.spin_sq_y.value()
        self._worker.square_length_mm = self.spin_square.value()
        self._worker.marker_length_mm = self.spin_marker.value()
        self._worker.aruco_dict_id    = CHARUCO_DICT_MAP[self.combo_dict.currentText()]
        self._worker.max_intrinsic_views = intr
        self._worker.max_stereo_pairs    = stereo
        self._worker.moveToThread(self._thread)

        self._job_running = True
        self._stall_level = 0
        self._job_started_at = time.monotonic()
        self._last_log_at = self._job_started_at
        self._watchdog.start()
        self._ui_tick.start()
        self._current_phase = "Démarrage…"
        self._phase_started_at = time.monotonic()
        self.lbl_live_status.setStyleSheet(
            "color:#7ec8e3; font-size:13px; font-weight:bold;"
            " background:#0f172a; border:1px solid #334155; border-radius:4px;"
            " padding:6px;")
        self.lbl_live_status.setText("⏳ Démarrage calibration…")
        self.progress.setRange(0, 100)
        self.progress.setFormat("Calibration… %p%")

        self._connect_worker(self._worker)
        self._thread.start()

    def _append_log(self, text: str):
        self._last_log_at = time.monotonic()
        self._stall_level = 0
        self.log_area.append(text)
        sb = self.log_area.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _on_phase(self, phase: str):
        self._current_phase = phase
        self._phase_started_at = time.monotonic()
        self._last_log_at = time.monotonic()
        self._on_live_status(f"⏳ {phase}")

    def _on_live_status(self, text: str):
        self._last_log_at = time.monotonic()
        self.lbl_live_status.setText(text)

    def _on_opencv_busy(self, busy: bool):
        if busy:
            self.progress.setRange(0, 0)   # barre indéterminée = calcul en cours
        else:
            self.progress.setRange(0, 100)

    def _ui_tick_status(self):
        """Horloge UI (thread principal) - tourne même si OpenCV ne log rien."""
        if not self._job_running:
            return
        total = int(time.monotonic() - self._job_started_at)
        phase_t = int(time.monotonic() - self._phase_started_at)
        phase = self._current_phase or "Calibration"
        cur = self.lbl_live_status.text()
        # Ne pas écraser un heartbeat récent (< 2 s)
        if time.monotonic() - self._last_log_at < 2.0 and "calibrateCamera" in cur:
            return
        self.lbl_live_status.setText(
            f"⏳ {phase} - {phase_t}s sur cette étape | {total}s total "
            f"| OpenCV calcule (pas de % natif)")

    def _on_progress(self, value: int):
        self._last_log_at = time.monotonic()
        self.progress.setValue(value)
        elapsed = int(time.monotonic() - self._job_started_at)
        self.progress.setFormat(f"Étape en cours - {elapsed}s - %p%")

    def _check_stall(self):
        if not self._job_running:
            return
        idle = time.monotonic() - self._last_log_at
        total = int(time.monotonic() - self._job_started_at)
        if idle < 12:
            return
        if self._stall_level < 1:
            self._stall_level = 1
            self.log_area.append(
                f"\n⚠ Aucun message depuis {int(idle)}s "
                f"(job total {total}s).\n"
                f"  → Si des lignes « … en cours (Xs) » apparaissent encore, "
                f"c'est normal.\n"
                f"  → Sinon possible blocage : cliquez Annuler.")
        elif self._stall_level < 2 and idle >= 180:
            self._stall_level = 2
            self.log_area.append(
                f"\n⚠⚠ Toujours silence depuis {int(idle)}s - "
                f"possible blocage OpenCV (calibrateCamera > 2 min = anormal).\n"
                f"  → Si des lignes « … en cours (Xs) » défilent encore, c'est normal.\n"
                f"  → Sinon : Annuler, réduire la précision ou rogner la vidéo.")
            self.progress.setFormat(f"⚠ Bloqué ? - {int(idle)}s sans activité")

    def _on_finished(self):
        self._watchdog.stop()
        self._ui_tick.stop()
        self._job_running = False
        self.progress.setRange(0, 100)
        self.progress.setFormat("%p%")
        self.lbl_live_status.setText("✓ Terminé ou arrêté - prêt pour une nouvelle calibration")
        self.lbl_live_status.setStyleSheet(
            "color:#86efac; font-size:13px; font-weight:bold;"
            " background:#0f172a; border:1px solid #334155; border-radius:4px;"
            " padding:6px;")
        self.btn_run.setEnabled(True)
        self.btn_cancel.setEnabled(False)
        self._refresh_profile_status()

    def _cancel(self):
        if hasattr(self, '_worker') and self._worker is not None:
            self._worker.cancel()
            self.log_area.clear()
            self.log_area.append(
                "⛔ Annulation demandée - arrêt après la frame courante…")
            self.btn_cancel.setEnabled(False)

    def _on_suggest_config(self, sx: int, sy: int):
        """Affiche le bandeau de suggestion de config si elle diffère de l'actuelle."""
        cur_x = self.spin_sq_x.value()
        cur_y = self.spin_sq_y.value()
        if sx == cur_x and sy == cur_y:
            return
        self._pending_suggestion = (sx, sy)
        self._suggest_lbl.setText(
            f"  Board détecté : {sx} colonnes × {sy} lignes  "
            f"(actuel : {cur_x}×{cur_y})  -  Appliquer pour relancer ?")
        self._suggest_bar.setVisible(True)

    def _apply_suggestion(self):
        sx, sy = self._pending_suggestion
        if sx is None:
            return
        self.spin_sq_x.setValue(sx)
        self.spin_sq_y.setValue(sy)
        self._suggest_bar.setVisible(False)
        self._run()

    def _show_frame_preview(self, payload):
        """Affiche dans le panneau droit l'image annotée émise par le worker."""
        try:
            frame_bgr, label = payload
            h, w = frame_bgr.shape[:2]
            # Convertir BGR → RGB pour Qt
            frame_rgb = cv.cvtColor(frame_bgr, cv.COLOR_BGR2RGB)
            bytes_per_line = 3 * w
            qimg = QImage(frame_rgb.data, w, h, bytes_per_line,
                          QImage.Format.Format_RGB888)
            pix = QPixmap.fromImage(qimg)
            # Mettre à l'échelle pour tenir dans le label
            pix = pix.scaled(self._preview_lbl.size(),
                             Qt.AspectRatioMode.KeepAspectRatio,
                             Qt.TransformationMode.SmoothTransformation)
            self._preview_lbl.setPixmap(pix)
            self._preview_title.setText(label)
            self._preview_title.setStyleSheet(
                "color:#7ec8e3; font-size:11px; font-weight:bold;")
        except Exception:
            pass

    def _on_stereo_ready(self, data: dict):
        self._update_stereo_panel(data)

    def _load_stereo_panel_from_disk(self):
        r_path = cam_param('R.npy')
        t_path = cam_param('T.npy')
        if not os.path.exists(r_path) or not os.path.exists(t_path):
            return
        data = {
            'R': np.load(r_path),
            'T': np.load(t_path),
        }
        rmse_path = cam_param('stereo_rmse.npy')
        if os.path.exists(rmse_path):
            data['rmse_stereo'] = float(np.load(rmse_path))
        _, baseline, b_tag = _stereo_extrinsics_summary(
            data['R'], data['T'], rmse_stereo=data.get('rmse_stereo'))
        if b_tag == "ANORMAL" or (
                data.get('rmse_stereo') is not None
                and data['rmse_stereo'] > MAX_STEREO_RMSE_PX):
            self.log_area.append(
                "  ⚠ R/T sur disque invalides (ancienne calib) - ignorez jusqu'à "
                "une calibration stéréo réussie.")
            return
        self._update_stereo_panel(data)

    def _update_stereo_panel(self, data: dict):
        R = data.get('R')
        T = data.get('T')
        if R is None or T is None:
            return
        text, baseline, b_tag = _stereo_extrinsics_summary(
            R, T,
            rmse_stereo=data.get('rmse_stereo'),
            rmse_left=data.get('rmse_left'),
            rmse_right=data.get('rmse_right'))
        if b_tag == "OK":
            b_style = "color:#4ade80;"
        elif b_tag == "?":
            b_style = "color:#fbbf24;"
        else:
            b_style = "color:#f87171;"
        self.lbl_stereo_baseline.setText(
            f"Baseline ‖T‖ : {baseline:.1f} mm  "
            f"(attendu ≈ {EXPECTED_BASELINE_MM:.0f} mm)")
        self.lbl_stereo_baseline.setStyleSheet(b_style + " font-weight:bold; font-size:12px;")
        self.lbl_stereo_rt.setText(text)
        self._stereo_group.setVisible(True)

    def _show_verify_grid(self, verify_data: dict):
        if verify_data.get('R') is not None and verify_data.get('T') is not None:
            self._update_stereo_panel(verify_data)
        try:
            # Vider le container (supprime le placeholder ou une ancienne grille)
            while self._verify_container_layout.count():
                item = self._verify_container_layout.takeAt(0)
                w = item.widget()
                if w:
                    w.deleteLater()

            grid = VerifyGrid(verify_data, self.log_area.append,
                              parent=self._verify_container)
            self._verify_container_layout.addWidget(grid)

            # Ouvrir le panneau bas du splitter
            total = self._splitter.height()
            top   = max(200, total - 450) if total > 300 else 300
            self._splitter.setSizes([top, 450])
            self._splitter.update()
        except Exception as exc:
            import traceback
            self.log_area.append(f"[VERIFY ERROR] {exc}\n{traceback.format_exc()}")


# ── Signaux thread → UI ────────────────────────────────────────────────────────

class _PCSignals(QObject):
    status      = pyqtSignal(str)
    finished    = pyqtSignal()
    error       = pyqtSignal(str)
    image_ready = pyqtSignal(object)   # émet un np.ndarray BGR pour affichage Qt


class _FishDetectSignals(QObject):
    boxes_ready = pyqtSignal(object)  # (left, right) ou list legacy
    error       = pyqtSignal(str)
    finished    = pyqtSignal()


class _FishTrackSignals(QObject):
    progress = pyqtSignal(int, int)
    done = pyqtSignal(int)
    error = pyqtSignal(str)
    finished = pyqtSignal()



# ── Fenêtre DLT Depth Map ─────────────────────────────────────────────────────

class DepthHoverWidget(QWidget):
    """Image + profondeur : hover → mm au pixel."""

    depth_hover = pyqtSignal(float, int, int)   # (mm, x_orig, y_orig)
    depth_leave = pyqtSignal()

    def __init__(self, overlay_bgr: np.ndarray, depth_map: np.ndarray,
                 parent=None):
        super().__init__(parent)
        self._colored = overlay_bgr
        self._depth   = depth_map.astype(np.float32)
        self._hover   = None
        self.setMouseTracking(True)
        self.setMinimumSize(480, 360)

    def set_images(self, overlay_bgr: np.ndarray, depth_map: np.ndarray):
        self._colored = overlay_bgr
        self._depth   = depth_map.astype(np.float32)
        self._hover   = None
        self.update()

    # ── Transformations ───────────────────────────────────────────────────────

    def _transform(self):
        h, w = self._colored.shape[:2]
        s  = min(self.width() / w, self.height() / h)
        ox = (self.width()  - w * s) / 2
        oy = (self.height() - h * s) / 2
        return s, ox, oy, w, h

    def _to_orig(self, xd, yd):
        s, ox, oy, w, h = self._transform()
        return (max(0, min(int((xd - ox) / s), w - 1)),
                max(0, min(int((yd - oy) / s), h - 1)))

    def _to_disp(self, xo, yo):
        s, ox, oy, *_ = self._transform()
        return ox + xo * s, oy + yo * s

    # ── Dessin ────────────────────────────────────────────────────────────────

    def paintEvent(self, event):
        painter = QPainter(self)
        s, ox, oy, ow, oh = self._transform()
        dw, dh = int(ow * s), int(oh * s)

        # Image
        rgb  = cv.cvtColor(self._colored, cv.COLOR_BGR2RGB)
        qimg = QImage(rgb.tobytes(), ow, oh, 3 * ow, QImage.Format.Format_RGB888)
        pix  = QPixmap.fromImage(qimg).scaled(
            QSize(dw, dh),
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation)
        painter.drawPixmap(int(ox), int(oy), pix)

        # Crosshair de hover (jaune)
        if self._hover is not None:
            hx, hy = self._hover
            painter.setPen(QPen(QColor(255, 255, 0), 1, Qt.PenStyle.DashLine))
            painter.drawLine(int(ox), hy, int(ox + dw), hy)
            painter.drawLine(hx, int(oy), hx, int(oy + dh))

        painter.end()

    # ── Souris ────────────────────────────────────────────────────────────────

    def mouseMoveEvent(self, event):
        px, py = int(event.position().x()), int(event.position().y())
        self._hover = (px, py)
        xo, yo = self._to_orig(px, py)
        d = float(self._depth[yo, xo])
        self.depth_hover.emit(d, xo, yo)
        self.update()

    def leaveEvent(self, event):
        self._hover = None
        self.depth_leave.emit()
        self.update()


class DepthMapWindow(QWidget):
    """Profondeur en overlay sur l'image caméra (pleine résolution)."""

    def __init__(self, data: dict, parent=None):
        super().__init__(parent, Qt.WindowType.Window)
        self.setWindowTitle("AquaMeasure - Depth Map")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setStyleSheet("background:#1e1e1e; color:#ddd;")
        self._data = data

        root = QVBoxLayout(self)
        root.setSpacing(6)
        root.setContentsMargins(8, 8, 8, 8)

        top = QHBoxLayout()
        top.addWidget(QLabel("Caméra :"))
        self._combo_side = QComboBox()
        self._combo_side.addItem("Gauche (rectifiée)", "left_rect")
        self._combo_side.addItem("Droite (rectifiée)", "right_rect")
        self._combo_side.currentIndexChanged.connect(self._on_side_changed)
        top.addWidget(self._combo_side)
        top.addWidget(QLabel("Affichage :"))
        self._combo_mode = QComboBox()
        self._combo_mode.addItem("Overlay honnête", "overlay")
        self._combo_mode.addItem("Depth pure", "pure")
        self._combo_mode.currentIndexChanged.connect(self._on_view_changed)
        top.addWidget(self._combo_mode)
        top.addStretch()
        root.addLayout(top)

        d = data['diag']
        s = data['stats']
        wls_txt = "WLS actif" if d.get('wls_ok') else "WLS absent (pip install opencv-contrib-python)"
        self._diag_lbl = QLabel(
            f"<b>Diagnostic SGBM</b> - block {d.get('block_size', '?')}×{d.get('block_size', '?')}  |  "
            f"brut : {d['pct_raw']:.1f}% valides  |  "
            f"après WLS : {d['pct_wls']:.1f}%  |  {wls_txt}  |  "
            f"disp [{d['min_d']}…{d['min_d'] + d['num_d']}] px  "
            f"(attendu {d.get('disp_at_z_far', 0):.0f}…{d.get('disp_at_z_near', 0):.0f})  |  "
            f"plage {d['z_near_mm']/1000:.2f}–{d['z_far_mm']/1000:.1f} m  |  "
            f"gris = sans données")
        self._diag_lbl.setTextFormat(Qt.TextFormat.RichText)
        self._diag_lbl.setWordWrap(True)
        self._diag_lbl.setStyleSheet("color:#94a3b8; font-size:11px;")
        root.addWidget(self._diag_lbl)

        side0 = self._combo_side.currentData()
        mode0 = self._combo_mode.currentData()
        img0, dep0 = self._pick_view(side0, mode0)
        self._depth_widget = DepthHoverWidget(img0, dep0)
        self._depth_widget.depth_hover.connect(self._on_hover)
        self._depth_widget.depth_leave.connect(
            lambda: self._hover_lbl.setText("Survolez l'image - gris = pas de depth"))
        root.addWidget(self._depth_widget, stretch=1)

        self._hover_lbl = QLabel("Survolez l'image - gris = pas de depth")
        self._hover_lbl.setStyleSheet("color:#94a3b8; font-size:11px;")
        root.addWidget(self._hover_lbl)

        screen = QApplication.primaryScreen().availableGeometry()
        self.resize(min(1100, int(screen.width() * 0.88)),
                    min(780, int(screen.height() * 0.88)))

    def _pick_view(self, side: str, mode: str):
        bucket = self._data['overlays'] if mode == 'overlay' else self._data['pure']
        return bucket[side], self._data['depth_maps'][side]

    def _on_view_changed(self, _idx: int = 0):
        side = self._combo_side.currentData()
        mode = self._combo_mode.currentData()
        img, dep = self._pick_view(side, mode)
        self._depth_widget.set_images(img, dep)

    def _on_side_changed(self, _idx: int):
        self._on_view_changed()

    def _on_hover(self, depth_mm: float, x: int, y: int):
        self._hover_lbl.setText(
            f"Pixel ({x}, {y}) - profondeur : <b>{depth_mm:.0f} mm</b>"
            if depth_mm > 0 else f"Pixel ({x}, {y}) - pas de depth (SGBM)")
        self._hover_lbl.setTextFormat(Qt.TextFormat.RichText)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.close()
        else:
            super().keyPressEvent(event)


# ── Fenêtre de visualisation disparité ────────────────────────────────────────

class DisparityWindow(QWidget):
    """Fenêtre indépendante affichant le composite disparité/rectification."""

    def __init__(self, bgr_image: np.ndarray, parent=None):
        super().__init__(parent, Qt.WindowType.Window)
        self.setWindowTitle("AquaMeasure - Disparity & Rectification Check")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        # Barre d'info en haut
        info = QLabel(
            "Lignes épipolaires vertes alignées horizontalement → rectification OK   |   "
            "Gradient cohérent sur la carte → calibration stéréo OK   |   "
            "Noir = pas de profondeur")
        info.setStyleSheet("color:#aaa; font-size:10px;")
        info.setWordWrap(True)
        layout.addWidget(info)

        scroll = QScrollArea()
        scroll.setWidgetResizable(False)

        lbl = QLabel()
        lbl.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        pix = _cv_to_pixmap(bgr_image)
        lbl.setPixmap(pix)
        lbl.setFixedSize(pix.size())

        scroll.setWidget(lbl)
        layout.addWidget(scroll)

        # Taille fenêtre : max 90 % de l'écran
        screen = QApplication.primaryScreen().availableGeometry()
        self.resize(min(bgr_image.shape[1] + 24, int(screen.width()  * 0.92)),
                    min(bgr_image.shape[0] + 80,  int(screen.height() * 0.88)))

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.close()
        else:
            super().keyPressEvent(event)


# ── ZoomPanel ─────────────────────────────────────────────────────────────────

class ZoomPanel(QLabel):
    """Affiche un patch 4× centré sur la position souris."""

    SIZE = 300

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(self.SIZE, self.SIZE)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet("background:#1e1e1e; border:1px solid #555;")
        self.setText("-")

    def update_zoom(self, image: np.ndarray, x: int, y: int):
        zoomed = get_zoomed_patch(image, x, y, display_size=self.SIZE)
        self.setPixmap(_cv_to_pixmap(zoomed))


# ── VerifyThumbnail ────────────────────────────────────────────────────────────

class VerifyThumbnail(QLabel):
    """Miniature d'une frame de vérification ; hover → zoom, clic → éditeur."""

    hover_at  = pyqtSignal(object, int, int)   # (image, x_orig, y_orig)
    open_edit = pyqtSignal(object)             # verify_item dict

    THUMB = 160

    def __init__(self, item: dict, parent=None):
        super().__init__(parent)
        self._item  = item
        self._image = item.get('frame_annotated')
        if self._image is None:
            src = item.get('verify_png') or item.get('path')
            self._image = cv.imread(src, 1) if src else None
        if self._image is None:
            self._image = np.zeros((64, 64, 3), dtype=np.uint8)
        self.setFixedSize(self.THUMB, self.THUMB)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet("border:2px solid #555; background:#1e1e1e;")
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        thumb = cv.resize(self._image, (self.THUMB, self.THUMB))
        self.setPixmap(_cv_to_pixmap(thumb))

    def _orig(self, pos):
        h, w = self._image.shape[:2]
        x = int(max(0, min(pos.x() * w / self.THUMB, w - 1)))
        y = int(max(0, min(pos.y() * h / self.THUMB, h - 1)))
        return x, y

    def mouseMoveEvent(self, event):
        x, y = self._orig(event.position())
        self.hover_at.emit(self._image, x, y)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.open_edit.emit(self._item)


# ── CornerCanvas ───────────────────────────────────────────────────────────────

class CornerCanvas(QWidget):
    """Widget de dessin interactif pour ajuster les coins du damier."""

    HANDLE_R   = 8
    SNAP_DIST  = 20

    hover_at = pyqtSignal(object, int, int)

    def __init__(self, frame: np.ndarray, corners: np.ndarray, parent=None):
        super().__init__(parent)
        self._frame    = frame
        self._corners  = corners.reshape(-1, 2).astype(float)
        self._orig_corners = self._corners.copy()
        self._selected = None
        self.setMouseTracking(True)
        self.setMinimumSize(600, 400)
        # Pre-build base QPixmap from frame
        rgb  = cv.cvtColor(frame, cv.COLOR_BGR2RGB)
        h, w, ch = rgb.shape
        self._orig_w = w
        self._orig_h = h
        self._base_pix = QPixmap.fromImage(
            QImage(rgb.tobytes(), w, h, ch * w, QImage.Format.Format_RGB888))

    def _transform(self):
        scale = min(self.width() / self._orig_w, self.height() / self._orig_h)
        ox = (self.width()  - self._orig_w * scale) / 2
        oy = (self.height() - self._orig_h * scale) / 2
        return scale, ox, oy

    def _to_disp(self, x, y):
        s, ox, oy = self._transform()
        return ox + x * s, oy + y * s

    def _to_orig(self, x, y):
        s, ox, oy = self._transform()
        return (x - ox) / s, (y - oy) / s

    def paintEvent(self, event):
        painter = QPainter(self)
        s, ox, oy = self._transform()
        dw = int(self._orig_w * s)
        dh = int(self._orig_h * s)
        painter.drawPixmap(
            int(ox), int(oy),
            self._base_pix.scaled(QSize(dw, dh),
                                  Qt.AspectRatioMode.IgnoreAspectRatio,
                                  Qt.TransformationMode.SmoothTransformation))
        n = len(self._corners)
        for i, (cx, cy) in enumerate(self._corners):
            dx, dy = self._to_disp(cx, cy)
            r = self.HANDLE_R + (3 if i == self._selected else 0)
            if i == 0:
                col = QColor(220, 30, 30)
            elif i == n - 1:
                col = QColor(40, 80, 255)
            else:
                col = QColor(255, 210, 0)
            painter.setBrush(QBrush(col))
            painter.setPen(QPen(QColor(255, 255, 255), 1))
            painter.drawEllipse(int(dx - r), int(dy - r), r * 2, r * 2)
            painter.setPen(QPen(QColor(255, 255, 255), 1))
            painter.drawText(int(dx + r + 3), int(dy + 5), str(i))
        painter.end()

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        px, py = event.position().x(), event.position().y()
        self._selected = None
        best = self.HANDLE_R + 6
        for i, (cx, cy) in enumerate(self._corners):
            dx, dy = self._to_disp(cx, cy)
            d = ((dx - px) ** 2 + (dy - py) ** 2) ** 0.5
            if d < best:
                best = d
                self._selected = i
        self.update()

    def mouseMoveEvent(self, event):
        px, py = event.position().x(), event.position().y()
        ox, oy = self._to_orig(px, py)
        if self._selected is not None:
            self._corners[self._selected] = [ox, oy]
            self.update()
        else:
            ix, iy = int(max(0, min(ox, self._orig_w - 1))), \
                     int(max(0, min(oy, self._orig_h - 1)))
            self.hover_at.emit(self._frame, ix, iy)

    def mouseReleaseEvent(self, event):
        if self._selected is not None:
            x, y = self._corners[self._selected]
            s, _, _ = self._transform()
            thresh = self.SNAP_DIST / s
            best, best_d = None, thresh
            for cx, cy in self._orig_corners:
                d = ((cx - x) ** 2 + (cy - y) ** 2) ** 0.5
                if d < best_d:
                    best_d = d
                    best = (cx, cy)
            if best:
                self._corners[self._selected] = list(best)
            self._selected = None
            self.update()

    def get_corners(self) -> np.ndarray:
        return self._corners.reshape(-1, 1, 2).astype(np.float32)

    def reset(self):
        self._corners = self._orig_corners.copy()
        self._selected = None
        self.update()


# ── CornerEditorDialog ─────────────────────────────────────────────────────────

class CornerEditorDialog(QDialog):
    """Vue plein écran avec handles déplaçables et panneau zoom."""

    def __init__(self, item: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Corner Editor - {item['label']}")
        self.resize(1280, 780)

        layout = QVBoxLayout(self)

        ctrl = QHBoxLayout()
        btn_save  = QPushButton("Save adjustments")
        btn_reset = QPushButton("Reset corners")
        btn_save.clicked.connect(self.accept)
        btn_reset.clicked.connect(lambda: self.canvas.reset())
        ctrl.addWidget(btn_save)
        ctrl.addWidget(btn_reset)
        ctrl.addStretch()
        layout.addLayout(ctrl)

        main_row = QHBoxLayout()
        frame = item.get('frame')
        if frame is None:
            src = item.get('path')
            frame = cv.imread(src, 1) if src else None
        if frame is None:
            frame = np.zeros((480, 640, 3), dtype=np.uint8)
        self.canvas = CornerCanvas(frame, item['corners'])
        main_row.addWidget(self.canvas, stretch=1)

        zoom_col = QVBoxLayout()
        zoom_col.addWidget(QLabel("Zoom (4x)"))
        self.zoom = ZoomPanel()
        zoom_col.addWidget(self.zoom)
        zoom_col.addStretch()
        main_row.addLayout(zoom_col)
        layout.addLayout(main_row)

        self.canvas.hover_at.connect(self.zoom.update_zoom)

    def get_adjusted_corners(self) -> np.ndarray:
        return self.canvas.get_corners()


# ── VerifyGrid ─────────────────────────────────────────────────────────────────

class VerifyGrid(QWidget):
    """Grille scrollable de thumbnails + panneau zoom + recalibration."""

    COLS = 5

    def __init__(self, verify_data: dict, log_fn, parent=None):
        super().__init__(parent)
        self._data   = verify_data
        self._log_fn = log_fn
        # Copie locale des imgpoints pour permettre la modification
        self._imgpts_l = [item['corners'].copy() for item in verify_data['left']]
        self._imgpts_r = [item['corners'].copy() for item in verify_data['right']]

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(6)

        # En-tête
        hdr = QHBoxLayout()
        n_l = len(verify_data['left'])
        n_r = len(verify_data['right'])
        stats = verify_data.get('stats') or {}
        analyzed = stats.get('frames_analyzed')
        scan_l = stats.get('scan_left')
        scan_r = stats.get('scan_right')
        pairs = stats.get('pairs_stereo')
        if analyzed and scan_l is not None:
            hdr_txt = (
                f"<b>Vérification :</b>  {n_l} vues utilisées pour la calibration "
                f"(gauche)  |  {n_r} (droite)<br>"
                f"<span style='color:#aaa'>Scan : {scan_l} détections G, {scan_r} D "
                f"sur {analyzed} frames analysées"
                + (f"  |  {pairs} paires stéréo" if pairs else "")
                + "</span>  - cliquez pour ajuster les coins")
        else:
            hdr_txt = (
                f"<b>Vérification :</b>  {n_l} vues calibration (gauche)  |  "
                f"{n_r} (droite)  - relancez une calibration pour voir le détail scan")
        hdr_lbl = QLabel(hdr_txt)
        hdr_lbl.setWordWrap(True)
        hdr.addWidget(hdr_lbl)
        hdr.addStretch()
        self.btn_recalib = QPushButton("Recalibrate with adjusted corners")
        self.btn_recalib.clicked.connect(self._recalibrate)
        hdr.addWidget(self.btn_recalib)
        layout.addLayout(hdr)

        # Grille + zoom
        row = QHBoxLayout()
        row.setSpacing(8)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        grid_w = QWidget()
        grid   = QGridLayout(grid_w)
        grid.setSpacing(6)

        self.zoom_panel = ZoomPanel()

        all_items = [('L', i, it) for i, it in enumerate(verify_data['left'])] + \
                    [('R', i, it) for i, it in enumerate(verify_data['right'])]

        for pos, (side, idx, item) in enumerate(all_items):
            thumb = VerifyThumbnail(item)
            thumb.hover_at.connect(self.zoom_panel.update_zoom)
            thumb.open_edit.connect(
                lambda it, s=side, i=idx: self._open_editor(s, i, it))
            cell = QWidget()
            cl   = QVBoxLayout(cell)
            cl.setContentsMargins(2, 2, 2, 2)
            cl.setSpacing(2)
            lbl = QLabel(item['label'])
            lbl.setFont(QFont("Segoe UI", 8))
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            cl.addWidget(thumb)
            cl.addWidget(lbl)
            grid.addWidget(cell, pos // self.COLS, pos % self.COLS)

        scroll.setWidget(grid_w)
        row.addWidget(scroll, stretch=1)

        zoom_col = QVBoxLayout()
        zoom_col.addWidget(QLabel("<b>Zoom (4x)</b>"))
        zoom_col.addWidget(self.zoom_panel)
        zoom_col.addStretch()
        row.addLayout(zoom_col)
        layout.addLayout(row)

    def _open_editor(self, side: str, idx: int, item: dict):
        dlg = CornerEditorDialog(item, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            adjusted = dlg.get_adjusted_corners()
            item['corners'] = adjusted
            if side == 'L':
                self._imgpts_l[idx] = adjusted
            else:
                self._imgpts_r[idx] = adjusted
            self._log_fn(f"  Corners adjusted for {item['label']}")

    def _recalibrate(self):
        """Relance calibrateCamera avec les imgpoints courants (ajustés ou non)."""
        d = self._data
        try:
            self._log_fn("── Recalibrating with adjusted corners ────────────")
            # Gauche
            ret1, mtx1, dist1, rv1, tv1 = cv.calibrateCamera(
                d['objpoints_left'],
                [p.reshape(-1, 1, 2) for p in self._imgpts_l],
                d['image_size_left'], None, None)
            err1 = sum(
                cv.norm(self._imgpts_l[i].reshape(-1,1,2),
                        cv.projectPoints(d['objpoints_left'][i], rv1[i], tv1[i], mtx1, dist1)[0],
                        cv.NORM_L2) / len(self._imgpts_l[i])
                for i in range(len(d['objpoints_left']))
            ) / len(d['objpoints_left'])
            self._log_fn(f"  Left  RMSE: {err1:.4f} px")
            # Droite
            ret2, mtx2, dist2, rv2, tv2 = cv.calibrateCamera(
                d['objpoints_right'],
                [p.reshape(-1, 1, 2) for p in self._imgpts_r],
                d['image_size_right'], None, None)
            err2 = sum(
                cv.norm(self._imgpts_r[i].reshape(-1,1,2),
                        cv.projectPoints(d['objpoints_right'][i], rv2[i], tv2[i], mtx2, dist2)[0],
                        cv.NORM_L2) / len(self._imgpts_r[i])
                for i in range(len(d['objpoints_right']))
            ) / len(d['objpoints_right'])
            self._log_fn(f"  Right RMSE: {err2:.4f} px")
            # Sauvegarde
            np.save('camera_parameters/mtx1.npy',  mtx1)
            np.save('camera_parameters/dist1.npy', dist1)
            np.save('camera_parameters/mtx2.npy',  mtx2)
            np.save('camera_parameters/dist2.npy', dist2)
            self._log_fn("  Updated camera_parameters/mtx*.npy and dist*.npy")
        except Exception as exc:
            self._log_fn(f"[ERROR] Recalibration: {exc}")


# ── Widget image cliquable ─────────────────────────────────────────────────────

class ScaledImageLabel(QLabel):
    """Affiche une image redimensionnée avec ratio conservé. Émet clicked_at
    en coordonnées de l'image originale."""

    clicked_at = pyqtSignal(float, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pixmap_orig: QPixmap | None = None
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(1, 1)
        self.setStyleSheet("background: #1e1e1e;")

    def set_pixmap(self, pixmap: QPixmap):
        self._pixmap_orig = pixmap
        self._refresh()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._refresh()

    def _refresh(self):
        if self._pixmap_orig is None:
            return
        scaled = self._pixmap_orig.scaled(
            self.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        super().setPixmap(scaled)

    def mousePressEvent(self, event):
        if self._pixmap_orig is None:
            return
        orig_w = self._pixmap_orig.width()
        orig_h = self._pixmap_orig.height()
        scaled = self._pixmap_orig.scaled(
            self.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.FastTransformation,
        )
        sw, sh = scaled.width(), scaled.height()
        ox = (self.width()  - sw) / 2
        oy = (self.height() - sh) / 2
        cx = event.position().x() - ox
        cy = event.position().y() - oy
        if 0 <= cx < sw and 0 <= cy < sh:
            self.clicked_at.emit(cx * orig_w / sw, cy * orig_h / sh)


# ── Widget de mesure stéréo (handles + zoom overlay) ──────────────────────────

class MeasureImageWidget(QWidget):
    """Affiche une frame vidéo avec handles A/B draggables et zoom 4× overlay."""

    HANDLE_R = 8
    HIT_DIST = 15     # px display
    ZOOM_SZ  = 280    # px du panneau zoom

    point_placed    = pyqtSignal(str, float, float)  # (side, x_orig, y_orig)
    handle_released = pyqtSignal()
    fish_box_clicked = pyqtSignal(int)
    fish_track_picked = pyqtSignal(int)

    def __init__(self, side: str, parent=None):
        super().__init__(parent)
        self._side      = side
        self._frame:    np.ndarray | None = None
        self._caption:  str = 'GAUCHE' if side == 'left' else 'DROITE'
        self._handles:  list = [None, None]   # [(x_orig, y_orig) | None]
        self._drag_idx: int | None = None
        self._hover:    tuple | None = None   # (x_disp, y_disp)
        self._epipolar_ys: list[float] = []
        self._show_epipolar = False
        self._point_clamp = None              # (idx, x, y) -> (x, y)
        self._fish_boxes: list = []
        self._selected_fish: int | None = None
        self._selected_track_pick: int | None = None
        self._show_track_trails = False
        self._track_trails: dict[int, list[tuple[float, float]]] = {}
        self.setMouseTracking(True)
        self.setMinimumSize(320, 240)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setStyleSheet("background:#1e1e1e;")

    # ── API ───────────────────────────────────────────────────────────────────

    def set_epipolar_guides(self, ys: list[float], show: bool):
        self._epipolar_ys = list(ys)
        self._show_epipolar = show
        self.update()

    def set_point_clamp(self, fn):
        """Optionnel : contraindre clic/drag (ex. Y épilolaire sur vue droite)."""
        self._point_clamp = fn

    def set_frame(self, frame: np.ndarray):
        self._frame = frame
        self.update()

    def set_caption(self, text: str):
        self._caption = text
        self.update()

    def set_handle(self, idx: int, x_orig: float, y_orig: float):
        self._handles[idx] = (x_orig, y_orig)
        self.update()

    def get_handles(self) -> list:
        return list(self._handles)

    def clear_handles(self):
        self._handles   = [None, None]
        self._drag_idx  = None
        self._hover     = None
        self.update()

    def set_fish_boxes(self, boxes: list):
        self._fish_boxes = list(boxes)
        if self._selected_fish is not None and self._selected_fish >= len(self._fish_boxes):
            self._selected_fish = None
        self.update()

    def clear_fish_boxes(self):
        self._fish_boxes = []
        self._selected_fish = None
        self.update()

    def set_selected_fish(self, idx: int | None):
        self._selected_fish = idx
        self.update()

    def set_selected_track_pick(self, idx: int | None):
        self._selected_track_pick = idx
        self.update()

    def set_show_track_trails(self, show: bool):
        self._show_track_trails = bool(show)
        self.update()

    def set_track_trails(self, trails: dict[int, list[tuple[float, float]]]):
        self._track_trails = {
            int(k): list(v) for k, v in trails.items()
        }
        self.update()

    def get_selected_fish(self) -> int | None:
        return self._selected_fish

    # ── Coordonnées ──────────────────────────────────────────────────────────

    def _transform(self):
        if self._frame is None:
            return 1.0, 0.0, 0.0
        h, w = self._frame.shape[:2]
        s  = min(self.width() / w, self.height() / h)
        ox = (self.width()  - w * s) / 2
        oy = (self.height() - h * s) / 2
        return s, ox, oy

    def _to_disp(self, xo, yo):
        s, ox, oy = self._transform()
        return ox + xo * s, oy + yo * s

    def _to_orig(self, xd, yd, idx: int | None = None):
        s, ox, oy = self._transform()
        if self._frame is None:
            return 0.0, 0.0
        h, w = self._frame.shape[:2]
        xo = max(0.0, min((xd - ox) / s, float(w - 1)))
        yo = max(0.0, min((yd - oy) / s, float(h - 1)))
        if self._point_clamp is not None and idx is not None:
            xo, yo = self._point_clamp(idx, xo, yo)
        return xo, yo

    def _placement_idx(self) -> int:
        return sum(1 for h in self._handles if h is not None)

    def _find_fish_box(self, xd, yd) -> int | None:
        for i, box in enumerate(self._fish_boxes):
            x1, y1 = self._to_disp(box["x1"], box["y1"])
            x2, y2 = self._to_disp(box["x2"], box["y2"])
            if min(x1, x2) <= xd <= max(x1, x2) and min(y1, y2) <= yd <= max(y1, y2):
                return i
        return None

    def _find_handle(self, xd, yd) -> int | None:
        for i, h in enumerate(self._handles):
            if h is None:
                continue
            dx, dy = self._to_disp(h[0], h[1])
            if ((dx - xd) ** 2 + (dy - yd) ** 2) ** 0.5 < self.HIT_DIST + 2:
                return i
        return None

    @staticmethod
    def _trail_color(track_id: int) -> QColor:
        hue = (int(track_id) * 53) % 360
        return QColor.fromHsv(hue, 180, 230, 200)

    def _draw_track_trails(self, painter: QPainter) -> None:
        if not self._show_track_trails or not self._track_trails:
            return
        for tid, pts in self._track_trails.items():
            if len(pts) < 2:
                continue
            col = self._trail_color(tid)
            pen = QPen(col, 2)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            disp_pts = [self._to_disp(x, y) for x, y in pts]
            for i in range(len(disp_pts) - 1):
                x0, y0 = disp_pts[i]
                x1, y1 = disp_pts[i + 1]
                alpha = int(60 + 195 * (i + 1) / max(1, len(disp_pts) - 1))
                c = QColor(col)
                c.setAlpha(alpha)
                painter.setPen(QPen(c, 2))
                painter.drawLine(int(x0), int(y0), int(x1), int(y1))

    # ── Dessin ────────────────────────────────────────────────────────────────

    def paintEvent(self, event):
        if self._frame is None:
            return
        painter = QPainter(self)
        s, ox, oy = self._transform()
        oh, ow = self._frame.shape[:2]
        dw, dh = int(ow * s), int(oh * s)

        # Frame
        rgb  = cv.cvtColor(self._frame, cv.COLOR_BGR2RGB)
        qimg = QImage(rgb.tobytes(), ow, oh, 3 * ow, QImage.Format.Format_RGB888)
        pix  = QPixmap.fromImage(qimg).scaled(
            QSize(dw, dh),
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation)
        painter.drawPixmap(int(ox), int(oy), pix)

        if self._side == "left":
            self._draw_track_trails(painter)

        # Détections poissons (bbox)
        if self._fish_boxes:
            painter.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
            for i, box in enumerate(self._fish_boxes):
                if (self._side == "right"
                        and box.get("stereo_projected") is False):
                    continue
                manual_graze = box.get("manual_grazing", False)
                selected = i == self._selected_fish
                track_pick = i == self._selected_track_pick
                if manual_graze:
                    fish_pen = QPen(QColor(251, 146, 60), 3 if selected else 2)
                elif box.get("stereo_projected") and self._side == "right":
                    fish_pen = QPen(QColor(96, 200, 255), 3 if selected else 2)
                else:
                    fish_pen = QPen(QColor(74, 222, 128), 3 if selected else 2)
                if track_pick:
                    fish_pen = QPen(QColor(250, 204, 21), 4)
                painter.setPen(fish_pen)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                x1, y1 = self._to_disp(box["x1"], box["y1"])
                x2, y2 = self._to_disp(box["x2"], box["y2"])
                painter.drawRect(int(x1), int(y1), int(x2 - x1), int(y2 - y1))
                conf = box.get("conf", 0.0)
                cls_name = box.get("cls_name", "poisson")
                species = box.get("species_name")
                track_id = box.get("track_id")
                label = species or cls_name
                if track_id is not None:
                    label = f"#{track_id} {cls_name}"
                if manual_graze:
                    label += " broute"
                if conf:
                    label += f" {conf:.0%}"
                painter.drawText(int(x1) + 2, int(y1) - 4, label)

        if self._show_epipolar and self._epipolar_ys:
            for i, y in enumerate(self._epipolar_ys):
                _, dy = self._to_disp(0, y)
                epi_col = (QColor(80, 255, 80) if i == 0
                           else QColor(80, 200, 255))
                painter.setPen(QPen(epi_col, 2))
                painter.drawLine(int(ox), int(dy), int(ox + dw), int(dy))

        # Légende caméra (assignation Sync, pas le dossier fichier)
        painter.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        cap_rect = painter.fontMetrics().boundingRect(self._caption)
        pad = 6
        bx, by = int(ox) + 4, int(oy) + 4
        bw = cap_rect.width() + pad * 2
        bh = cap_rect.height() + pad
        painter.fillRect(bx, by, bw, bh, QColor(0, 0, 0, 160))
        painter.setPen(QPen(QColor(255, 220, 80), 1))
        painter.drawText(bx + pad, by + cap_rect.height() + 2, self._caption)

        # Handles + ligne
        col = QColor(220, 30, 30) if self._side == 'left' else QColor(40, 80, 255)
        for i, handle in enumerate(self._handles):
            if handle is None:
                continue
            dx, dy = self._to_disp(handle[0], handle[1])
            r = self.HANDLE_R + (4 if i == self._drag_idx else 0)
            painter.setBrush(QBrush(col))
            painter.setPen(QPen(QColor(255, 255, 255), 1))
            painter.drawEllipse(int(dx - r), int(dy - r), r * 2, r * 2)
            painter.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
            painter.setPen(QPen(QColor(255, 255, 255), 1))
            painter.drawText(int(dx + r + 3), int(dy + 5), "AB"[i])

        if all(h is not None for h in self._handles):
            da = self._to_disp(*self._handles[0])
            db = self._to_disp(*self._handles[1])
            painter.setPen(QPen(col, 2))
            painter.drawLine(int(da[0]), int(da[1]), int(db[0]), int(db[1]))

        # Aperçu du prochain clic
        if self._hover is not None and any(h is None for h in self._handles):
            pidx = self._placement_idx()
            hxo, hyo = self._to_orig(
                *self._hover, pidx if self._point_clamp else None)
            cx, cy = self._to_disp(hxo, hyo)
            painter.setPen(QPen(QColor(255, 255, 0), 1, Qt.PenStyle.DashLine))
            painter.drawLine(int(cx - 12), int(cy), int(cx + 12), int(cy))
            painter.drawLine(int(cx), int(cy - 12), int(cx), int(cy + 12))

        # Zoom overlay (coin haut-droit)
        if self._hover is not None:
            hxd, hyd = self._hover
            hxo, hyo = self._to_orig(hxd, hyd)

            # Dessiner les handles sur la frame avant zoom
            zoom_src = self._frame.copy()
            hcol_bgr = (0, 0, 220) if self._side == 'left' else (255, 80, 40)
            for h in self._handles:
                if h is None:
                    continue
                cv.circle(zoom_src, (int(h[0]), int(h[1])), 5, hcol_bgr, -1)
                cv.circle(zoom_src, (int(h[0]), int(h[1])), 6, (255, 255, 255), 1)

            zoomed = get_zoomed_patch(zoom_src, int(hxo), int(hyo),
                                      display_size=self.ZOOM_SZ)
            zrgb   = cv.cvtColor(zoomed, cv.COLOR_BGR2RGB)
            zqimg  = QImage(zrgb.tobytes(), self.ZOOM_SZ, self.ZOOM_SZ,
                            3 * self.ZOOM_SZ, QImage.Format.Format_RGB888)
            zpix   = QPixmap.fromImage(zqimg)

            zx = self.width()  - self.ZOOM_SZ - 6
            zy = 6
            painter.setOpacity(0.90)
            painter.drawPixmap(zx, zy, zpix)
            painter.setOpacity(1.0)
            painter.setPen(QPen(QColor(200, 200, 200), 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(zx, zy, self.ZOOM_SZ, self.ZOOM_SZ)
            painter.setFont(QFont("Segoe UI", 8))
            painter.setPen(QPen(QColor(255, 255, 0), 1))
            painter.drawText(zx + 4, zy + 14, "Zoom (4×)")

        painter.end()

    # ── Souris ────────────────────────────────────────────────────────────────

    def mousePressEvent(self, event):
        px, py = event.position().x(), event.position().y()
        if (event.button() == Qt.MouseButton.LeftButton
                and event.modifiers() & Qt.KeyboardModifier.ShiftModifier):
            if self._side == 'left' and self._fish_boxes:
                fish_hit = self._find_fish_box(px, py)
                if fish_hit is not None:
                    self._selected_track_pick = fish_hit
                    self.fish_track_picked.emit(fish_hit)
                    self.update()
                    return
        # Clic droit : sélection poisson IA → registre (famille / genre / espèce)
        if event.button() == Qt.MouseButton.RightButton:
            if self._side == 'left' and self._fish_boxes:
                fish_hit = self._find_fish_box(px, py)
                if fish_hit is not None:
                    self._selected_fish = fish_hit
                    self.fish_box_clicked.emit(fish_hit)
                    self.update()
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        # Clic gauche : mesure stéréo uniquement (poignées A/B)
        hit = self._find_handle(px, py)
        if hit is not None:
            self._drag_idx = hit
            self._hover    = (px, py)
            self.update()
        else:
            pidx = self._placement_idx()
            ox, oy = self._to_orig(
                px, py, pidx if self._point_clamp else None)
            self.point_placed.emit(self._side, ox, oy)

    def mouseMoveEvent(self, event):
        px, py = event.position().x(), event.position().y()
        self._hover = (px, py)
        if self._drag_idx is not None:
            ox, oy = self._to_orig(
                px, py, self._drag_idx if self._point_clamp else None)
            self._handles[self._drag_idx] = (ox, oy)
        self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._drag_idx is not None:
            self._drag_idx = None
            self.handle_released.emit()

    def leaveEvent(self, event):
        self._hover    = None
        self._drag_idx = None
        self.update()


def _cv_to_pixmap(bgr_image: np.ndarray) -> QPixmap:
    rgb   = cv.cvtColor(bgr_image, cv.COLOR_BGR2RGB)
    h, w, ch = rgb.shape
    qimg  = QImage(rgb.data, w, h, ch * w, QImage.Format.Format_RGB888)
    return QPixmap.fromImage(qimg)


# ── Onglet Mesure ─────────────────────────────────────────────────────────────

_STEPS_FREE = [
    "Gauche rectifiée - point A",
    "Gauche rectifiée - point B",
    "Droite rectifiée - même coin physique que A",
    "Droite rectifiée - même coin physique que B",
    "Ajustez les poignées si besoin",
]
_STEPS_EPI = [
    "Gauche rectifiée - point A",
    "Gauche rectifiée - point B",
    "Droite - point A sur la ligne verte A (déplacez en X)",
    "Droite - point B sur la ligne verte B (déplacez en X)",
    "Ajustez les poignées - droite verrouillée en Y",
]


class MeasureTab(QWidget):
    def __init__(self):
        super().__init__()

        # Vidéo
        self._cap_left:   cv.VideoCapture | None = None
        self._cap_right:  cv.VideoCapture | None = None
        self._frame_left:  np.ndarray | None = None
        self._frame_right: np.ndarray | None = None
        self._total_frames = 0
        self._frame_idx    = 0
        self._playing      = False
        self._left_path    = ""
        self._right_path   = ""

        # Paramètres caméra
        self.mtx1 = self.dist1 = self.mtx2 = self.dist2 = None
        self.stereo_rmse: float | None = None
        self.R    = self.T = self.F = None

        # Mesure - step contrôle le placement de nouveaux points
        self._step = 0
        self._last_play_idx = -1
        self._left_sync = self._right_sync = 0
        self._left_start = 0
        self._sync_delta = 0
        self._play_count = 0
        self._tri_cache: dict = {}
        self._right_dy_offset = 0.0
        self._last_rect_left: np.ndarray | None = None
        self._last_rect_right: np.ndarray | None = None
        self._fish_detecting = False
        self._fish_detect_seq = 0
        self._fish_signals: _FishDetectSignals | None = None
        self._video_tracker = None
        self._track_batch_running = False
        self._track_signals: _FishTrackSignals | None = None
        self._graze_pick_idx: int | None = None
        self._graze_start_frame: int | None = None
        self._grazing_intervals: list[dict] = []
        self._stereo_midpoint: tuple[float, float, float] | None = None

        # ── Layout ────────────────────────────────────────────────────────────
        outer = QVBoxLayout(self)
        outer.setSpacing(6)
        outer.setContentsMargins(12, 12, 12, 12)

        controls_host = QWidget()
        layout = QVBoxLayout(controls_host)
        layout.setSpacing(6)
        layout.setContentsMargins(0, 0, 0, 0)

        # ── Barre de chargement ───────────────────────────────────────────────
        load_row = QHBoxLayout()
        load_row.setSpacing(6)

        self.btn_load = QPushButton("Charger (Sync)")
        self.btn_load.setToolTip(
            "Charge les vidéos assignées dans l'onglet Sync "
            "(camera_parameters/videos.txt - ligne 1 = gauche, ligne 2 = droite)")
        self.btn_load.clicked.connect(self._load_from_saved)
        load_row.addWidget(self.btn_load)

        self.btn_pick_left = QPushButton("Vidéo gauche…")
        self.btn_pick_left.setToolTip(
            "Remplacer la vidéo gauche et mettre à jour videos.txt")
        self.btn_pick_left.clicked.connect(lambda: self._pick_video('left'))
        load_row.addWidget(self.btn_pick_left)

        self.btn_pick_right = QPushButton("Vidéo droite…")
        self.btn_pick_right.setToolTip(
            "Remplacer la vidéo droite et mettre à jour videos.txt")
        self.btn_pick_right.clicked.connect(lambda: self._pick_video('right'))
        load_row.addWidget(self.btn_pick_right)

        load_row.addStretch()

        self.btn_disp = QPushButton("Show Disparity Map")
        self.btn_disp.setEnabled(False)
        self.btn_disp.setToolTip(
            "Carte de disparité SGBM sur les images rectifiées affichées.")
        self.btn_disp.clicked.connect(self._launch_disparity)
        load_row.addWidget(self.btn_disp)

        self.btn_depth = QPushButton("Depth Map")
        self.btn_depth.setEnabled(False)
        self.btn_depth.setToolTip(
            "Profondeur SGBM sur les images rectifiées affichées (frame courante).")
        self.btn_depth.clicked.connect(self._launch_depth_map)
        load_row.addWidget(self.btn_depth)

        self.btn_reset = QPushButton("Reset Points")
        self.btn_reset.setEnabled(False)
        self.btn_reset.clicked.connect(self._reset_points)
        load_row.addWidget(self.btn_reset)

        layout.addLayout(load_row)

        profile_row = QHBoxLayout()
        self.lbl_calib_profile = QLabel()
        self.lbl_calib_profile.setStyleSheet("color:#94a3b8; font-size:11px;")
        profile_row.addWidget(self.lbl_calib_profile)
        self.btn_reload_calib = QPushButton("Recharger calibration")
        self.btn_reload_calib.setToolTip(
            "Recharge mtx/dist/R/T du profil actif (Classique ou Rapide v2)")
        self.btn_reload_calib.clicked.connect(self.reload_calibration_profile)
        profile_row.addWidget(self.btn_reload_calib)
        profile_row.addStretch()
        layout.addLayout(profile_row)
        self._update_calib_profile_label()

        session_row = QHBoxLayout()
        session_row.addWidget(QLabel("Lieu"))
        self.edit_session_site = QLineEdit()
        self.edit_session_site.setPlaceholderText("Étang, site…")
        self.edit_session_site.setMaximumWidth(160)
        session_row.addWidget(self.edit_session_site)
        session_row.addWidget(QLabel("Titre"))
        self.edit_session_title = QLineEdit()
        self.edit_session_title.setPlaceholderText("Session")
        self.edit_session_title.setMaximumWidth(160)
        session_row.addWidget(self.edit_session_title)
        session_row.addWidget(QLabel("Date"))
        self.edit_session_date = QDateTimeEdit()
        self.edit_session_date.setCalendarPopup(True)
        self.edit_session_date.setDisplayFormat("yyyy-MM-dd HH:mm")
        self.edit_session_date.setDateTime(QDateTime.currentDateTime())
        session_row.addWidget(self.edit_session_date)
        session_row.addWidget(QLabel("Notes"))
        self.edit_session_notes = QLineEdit()
        self.edit_session_notes.setPlaceholderText("Notes terrain")
        session_row.addWidget(self.edit_session_notes, stretch=1)
        self.btn_save_session = QPushButton("Enreg. session")
        self.btn_save_session.setToolTip("Sauvegarde lieu, date et notes dans la base")
        self.btn_save_session.clicked.connect(self._save_session_metadata)
        session_row.addWidget(self.btn_save_session)
        layout.addLayout(session_row)

        # ── Labels état / erreur / hint ───────────────────────────────────────
        self.status_label = QLabel()
        self.status_label.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.hide()
        layout.addWidget(self.status_label)

        self.hint_label = QLabel()
        self.hint_label.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        self.hint_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hint_label.setStyleSheet("color: #f0c060;")
        self.hint_label.hide()
        layout.addWidget(self.hint_label)

        epi_row = QHBoxLayout()
        self.chk_epipolar = QCheckBox("Lignes épilolaires (verrou Y droite)")
        self.chk_epipolar.setToolTip(
            "Affiche les lignes horizontales de A/B sur les deux vues rectifiées.\n"
            "Sur la droite, les points restent sur ces lignes (même hauteur que la gauche).")
        self.chk_epipolar.stateChanged.connect(self._on_epipolar_toggled)
        epi_row.addWidget(self.chk_epipolar)
        epi_row.addStretch()
        layout.addLayout(epi_row)

        fish_row = QHBoxLayout()
        self.chk_fish_ia = QCheckBox("IA poissons")
        self.chk_fish_ia.setChecked(True)
        self.chk_fish_ia.setEnabled(_FISH_DETECT_AVAILABLE)
        self.chk_fish_ia.setToolTip(
            "Active ou désactive toute détection IA (manuelle et automatique).")
        self.chk_fish_ia.toggled.connect(self._on_fish_ia_toggled)
        fish_row.addWidget(self.chk_fish_ia)
        self.chk_auto_fish = QCheckBox("Auto à la pause")
        self.chk_auto_fish.setChecked(True)
        self.chk_auto_fish.setEnabled(_FISH_DETECT_AVAILABLE)
        self.chk_auto_fish.setToolTip(
            "À la pause ou quand vous changez de frame, détecte et entoure "
            "les poissons sur la vue gauche rectifiée (pas en lecture Play).")
        fish_row.addWidget(self.chk_auto_fish)
        self.btn_fish_detect = QPushButton("IA détecter")
        self.btn_fish_detect.setEnabled(_FISH_DETECT_AVAILABLE)
        self.btn_fish_detect.setToolTip(
            "Relance la détection sur la frame courante (pause uniquement)")
        self.btn_fish_detect.clicked.connect(self._run_fish_detect)
        fish_row.addWidget(self.btn_fish_detect)
        self.btn_fish_length = QPushButton("Longueur bbox")
        self.btn_fish_length.setEnabled(_FISH_SPARSE_MEASURE_AVAILABLE)
        self.btn_fish_length.setToolTip(
            "Longueur 3D sparse : extrémités de la bbox IA (rectifiée) "
            "+ template épipolaire. Pause - bbox visible (IA ou tracking).")
        self.btn_fish_length.clicked.connect(self._measure_fish_bbox_length)
        fish_row.addWidget(self.btn_fish_length)
        self.chk_fish_track = QCheckBox("Tracking ByteTrack")
        self.chk_fish_track.setEnabled(_FISH_TRACK_AVAILABLE)
        self.chk_fish_track.setToolTip(
            "Affiche les pistes ByteTrack. En pause : frame courante uniquement, "
            "sauf après « Analyser toute la vidéo ». En lecture : suivi temps réel.")
        self.chk_fish_track.toggled.connect(self._on_fish_track_toggled)
        fish_row.addWidget(self.chk_fish_track)
        self.chk_track_trails = QCheckBox("Traits trajectoire")
        self.chk_track_trails.setEnabled(_FISH_TRACK_AVAILABLE)
        self.chk_track_trails.setToolTip(
            "Affiche le chemin parcouru derrière chaque piste (tracking actif ou analysé).")
        self.chk_track_trails.toggled.connect(self._on_track_trails_toggled)
        fish_row.addWidget(self.chk_track_trails)
        fish_row.addWidget(QLabel("Seuil IA :"))
        self.spin_track_conf = QDoubleSpinBox()
        self.spin_track_conf.setRange(0.05, 0.95)
        self.spin_track_conf.setSingleStep(0.05)
        self.spin_track_conf.setValue(0.25)
        self.spin_track_conf.setToolTip(
            "Baissez si le poisson n'est pas détecté (ex. 0.15). "
            "Augmentez pour moins de faux positifs.")
        fish_row.addWidget(self.spin_track_conf)
        self.btn_track_analyze = QPushButton("Analyser tracking")
        self.btn_track_analyze.setEnabled(_FISH_TRACK_AVAILABLE)
        self.btn_track_analyze.setToolTip(
            "Lance le tracking ByteTrack sur toute la vidéo (en pause).")
        self.btn_track_analyze.clicked.connect(self._run_track_batch)
        fish_row.addWidget(self.btn_track_analyze)
        self.lbl_fish_count = QLabel()
        self.lbl_fish_count.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        self.lbl_fish_count.setStyleSheet("color:#4ade80;")
        fish_row.addWidget(self.lbl_fish_count)
        self.lbl_fish_status = QLabel()
        self.lbl_fish_status.setStyleSheet("color:#94a3b8;")
        fish_row.addWidget(self.lbl_fish_status)
        fish_row.addStretch()
        if not _FISH_DETECT_AVAILABLE:
            fish_row.addWidget(QLabel(
                "IA : installez ultralytics + modèle fish-vision/models/"))
        layout.addLayout(fish_row)

        graze_grp = QGroupBox("Annotation broute (manuelle)")
        graze_layout = QVBoxLayout(graze_grp)
        graze_row = QHBoxLayout()
        self.lbl_graze_track = QLabel("Piste : - (Maj+clic sur une bbox)")
        self.lbl_graze_track.setStyleSheet("color:#e2e8f0;")
        graze_row.addWidget(self.lbl_graze_track)
        self.btn_graze_start = QPushButton("Début broute")
        self.btn_graze_start.setEnabled(_FISH_ANNOTATE_AVAILABLE)
        self.btn_graze_start.setToolTip(
            "Marque la frame courante comme début d'un intervalle de broute "
            "pour la piste sélectionnée.")
        self.btn_graze_start.clicked.connect(self._on_graze_start)
        graze_row.addWidget(self.btn_graze_start)
        self.btn_graze_end = QPushButton("Fin broute")
        self.btn_graze_end.setEnabled(_FISH_ANNOTATE_AVAILABLE)
        self.btn_graze_end.setToolTip(
            "Marque la frame courante comme fin et enregistre l'intervalle en base.")
        self.btn_graze_end.clicked.connect(self._on_graze_end)
        graze_row.addWidget(self.btn_graze_end)
        self.btn_graze_cancel = QPushButton("Annuler")
        self.btn_graze_cancel.clicked.connect(self._on_graze_cancel)
        graze_row.addWidget(self.btn_graze_cancel)
        graze_row.addStretch()
        graze_layout.addLayout(graze_row)
        self.list_graze_intervals = QListWidget()
        self.list_graze_intervals.setMaximumHeight(72)
        self.list_graze_intervals.setToolTip(
            "Intervalles broute enregistrés (double-clic pour supprimer)")
        self.list_graze_intervals.itemDoubleClicked.connect(
            self._on_graze_interval_delete)
        graze_layout.addWidget(self.list_graze_intervals)
        graze_hint = QLabel(
            "Lecture → repérez la broute → In/Out → Analyser tracking sur ce passage → "
            "Maj+clic piste → Début/Fin broute. Poisson invisible : baissez le seuil IA.")
        graze_hint.setStyleSheet("color:#64748b; font-size:11px;")
        graze_layout.addWidget(graze_hint)
        layout.addWidget(graze_grp)

        ann_grp = QGroupBox("Entraînement IA")
        ann_row = QHBoxLayout(ann_grp)
        self.combo_export_rank = QComboBox()
        self.combo_export_rank.addItems(["fish", "family", "genus", "species"])
        self.combo_export_rank.setCurrentText("family")
        self.combo_export_rank.setToolTip("Niveau taxonomique pour export / ré-entraînement")
        ann_row.addWidget(QLabel("Niveau export :"))
        ann_row.addWidget(self.combo_export_rank)
        self.btn_retrain = QPushButton("Exporter & ré-entraîner")
        self.btn_retrain.setEnabled(_FISH_ANNOTATE_AVAILABLE)
        self.btn_retrain.setToolTip(
            "Exporte fish_annotations.db au niveau choisi puis fine-tune le modèle")
        self.btn_retrain.clicked.connect(self._run_retrain_from_db)
        ann_row.addWidget(self.btn_retrain)
        self.lbl_ann_status = QLabel()
        self.lbl_ann_status.setStyleSheet("color:#94a3b8;")
        ann_row.addWidget(self.lbl_ann_status)
        ann_row.addStretch()
        layout.addWidget(ann_grp)

        controls_scroll = QScrollArea()
        controls_scroll.setWidgetResizable(True)
        controls_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        controls_scroll.setFrameShape(QFrame.Shape.NoFrame)
        controls_scroll.setWidget(controls_host)
        controls_scroll.setMaximumHeight(300)
        outer.addWidget(controls_scroll)

        # ── Zone principale : vidéos (haut) | registre DB (bas), poignées drag ─
        main_splitter = QSplitter(Qt.Orientation.Vertical)
        main_splitter.setChildrenCollapsible(False)
        main_splitter.setHandleWidth(8)
        main_splitter.setStyleSheet(
            "QSplitter::handle:vertical { background:#475569; margin:2px 0; }"
            "QSplitter::handle:horizontal { background:#475569; margin:0 2px; }")

        video_panel = QWidget()
        video_panel.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        video_layout = QVBoxLayout(video_panel)
        video_layout.setContentsMargins(0, 0, 0, 0)
        video_layout.setSpacing(4)

        # ── Images côte à côte (splitter horizontal) ──────────────────────────
        img_splitter = QSplitter(Qt.Orientation.Horizontal)
        img_splitter.setChildrenCollapsible(False)
        img_splitter.setHandleWidth(6)
        self.img_left  = MeasureImageWidget('left')
        self.img_right = MeasureImageWidget('right')
        self.img_left.point_placed.connect(self._on_point_placed)
        self.img_right.point_placed.connect(self._on_point_placed)
        self.img_left.fish_box_clicked.connect(self._on_fish_box_clicked)
        self.img_left.fish_track_picked.connect(self._on_fish_track_picked)
        self.img_left.handle_released.connect(self._on_handle_released)
        self.img_right.handle_released.connect(self._on_handle_released)
        img_splitter.addWidget(self.img_left)
        img_splitter.addWidget(self.img_right)
        img_splitter.setStretchFactor(0, 1)
        img_splitter.setStretchFactor(1, 1)
        video_layout.addWidget(img_splitter, stretch=1)

        # ── Panneau résultat (sous les images) ───────────────────────────────
        result_panel = QWidget()
        result_panel.setStyleSheet(
            "background:#111827; border-radius:6px; padding:4px;")
        rp_layout = QVBoxLayout(result_panel)
        rp_layout.setContentsMargins(12, 8, 12, 8)
        rp_layout.setSpacing(3)

        self.distance_label = QLabel()
        self.distance_label.setFont(QFont("Segoe UI", 16, QFont.Weight.Bold))
        self.distance_label.setStyleSheet("color:#4ade80;")
        self.distance_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.distance_label.hide()
        rp_layout.addWidget(self.distance_label)

        self.uncertainty_label = QLabel()
        self.uncertainty_label.setFont(QFont("Segoe UI", 10))
        self.uncertainty_label.setStyleSheet("color:#94a3b8;")
        self.uncertainty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.uncertainty_label.hide()
        rp_layout.addWidget(self.uncertainty_label)

        self.precision_label = QLabel()
        self.precision_label.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        self.precision_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.precision_label.hide()
        rp_layout.addWidget(self.precision_label)

        video_layout.addWidget(result_panel)

        # ── Slider ────────────────────────────────────────────────────────────
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 0)
        self.slider.sliderMoved.connect(self._on_slider_moved)
        video_layout.addWidget(self.slider)

        # ── Contrôles lecture ─────────────────────────────────────────────────
        ctrl_row = QHBoxLayout()
        self.btn_play = QPushButton("▶ Play")
        self.btn_play.setFixedWidth(90)
        self.btn_play.setEnabled(False)
        self.btn_play.clicked.connect(self.toggle_play)
        ctrl_row.addWidget(self.btn_play)

        self.lbl_frame = QLabel("Frame: - / -")
        ctrl_row.addWidget(self.lbl_frame)
        ctrl_row.addStretch()
        video_layout.addLayout(ctrl_row)

        main_splitter.addWidget(video_panel)

        # Le panneau « registre » (fish_registry) a été retiré en phase 7 : le
        # registre de session vit dans l'application PySide6.
        main_splitter.setStretchFactor(0, 1)

        outer.addWidget(main_splitter, stretch=1)

        # ── Timer lecture ─────────────────────────────────────────────────────
        self._timer = QTimer()
        self._timer.setInterval(40)   # ~25 fps
        self._timer.timeout.connect(self._next_frame)

        self._fish_detect_timer = QTimer()
        self._fish_detect_timer.setSingleShot(True)
        self._fish_detect_timer.setInterval(350)
        self._fish_detect_timer.timeout.connect(self._run_fish_detect)

        # Auto-load si videos.txt existe
        self._try_auto_load()

    # ── Chargement ────────────────────────────────────────────────────────────

    def _try_auto_load(self):
        pair = load_videos_txt()
        if pair:
            self._open_videos(pair[0], pair[1])

    def _update_calib_profile_label(self):
        profile = load_active_calib_profile()
        ok = calib_profile_complete(profile)
        suffix = " ✓" if ok else " - non calibré"
        self.lbl_calib_profile.setText(
            f"Profil calib : {calib_profile_label(profile)}{suffix}")

    def _load_calib_params(self) -> bool:
        profile = load_active_calib_profile()
        if not calib_profile_complete(profile):
            return False
        self.mtx1  = np.load(calib_param('mtx1.npy', profile))
        self.dist1 = np.load(calib_param('dist1.npy', profile))
        self.mtx2  = np.load(calib_param('mtx2.npy', profile))
        self.dist2 = np.load(calib_param('dist2.npy', profile))
        self.R     = np.load(calib_param('R.npy', profile))
        self.T     = np.load(calib_param('T.npy', profile))
        self._tri_cache.clear()
        f_path = calib_param('F.npy', profile)
        self.F = np.load(f_path) if os.path.exists(f_path) else None
        rmse_path = calib_param('stereo_rmse.npy', profile)
        self.stereo_rmse = float(np.load(rmse_path)) \
            if os.path.exists(rmse_path) else None
        return True

    def reload_calibration_profile(self, _profile: str | None = None):
        self._update_calib_profile_label()
        if not self._load_calib_params():
            profile = load_active_calib_profile()
            self._show_error(
                f"Calibration « {calib_profile_label(profile)} » incomplète - "
                "lancez la calibration correspondante.")
            return
        self._right_dy_offset = 0.0
        if self._frame_left is not None and self.mtx1 is not None:
            self._refresh_display()
            self._show_status(
                f"Calibration rechargée : {calib_profile_label()}")

    def _load_from_saved(self):
        pair = load_videos_txt()
        if pair is None:
            self._show_error(
                "Aucune assignation Sync - onglet Sync : "
                "cliquez Vidéo gauche / Vidéo droite puis Detect Flash")
            return
        self._open_videos(pair[0], pair[1])

    def _pick_video(self, side: str):
        path, _ = QFileDialog.getOpenFileName(
            self, f"Vidéo {side}", "",
            "Videos (*.mp4 *.avi *.mov *.mkv *.MOV);;All Files (*)")
        if not path:
            return
        if side == 'left':
            self._left_path = path
        else:
            self._right_path = path
        if self._left_path and self._right_path:
            save_videos_txt(self._left_path, self._right_path)
            self._open_videos(self._left_path, self._right_path)

    def _load_sync(self):
        """Charge sync_frames.npy - le trim In/Out reste réservé à la calibration."""
        self._left_sync = self._right_sync = 0

        sync_path = cam_param('sync_frames.npy')
        if os.path.exists(sync_path):
            sf = np.load(sync_path)
            if len(sf) >= 2:
                self._left_sync = int(sf[0])
                self._right_sync = int(sf[1])

    def _left_abs_frame(self, aligned_idx: int) -> int:
        """Frame gauche à l'instant aligné i (timeline maîtresse = gauche)."""
        return self._left_start + max(0, aligned_idx)

    def _right_abs_frame(self, aligned_idx: int) -> int:
        """Frame droite couplée : gauche(i) + offset sync (comme calibration)."""
        return self._left_start + max(0, aligned_idx) + self._sync_delta

    def _has_measurement_handles(self) -> bool:
        return (self._step > 0
                or any(h is not None for h in self.img_left.get_handles())
                or any(h is not None for h in self.img_right.get_handles()))

    def _clear_points_if_frame_changed(self, new_idx: int):
        """Les clics sont liés à une frame : éviter une 2e mesure sur une autre image."""
        if new_idx != self._frame_idx and self._has_measurement_handles():
            self._reset_points()
            self.hint_label.setText(
                "Points effacés - nouvelle frame. Replacez A/B sur les deux vues.")
            self.hint_label.show()

    def _read_stereo_aligned(self, aligned_idx: int):
        li = self._left_abs_frame(aligned_idx)
        ri = self._right_abs_frame(aligned_idx)
        self._cap_left.set(cv.CAP_PROP_POS_FRAMES, li)
        self._cap_right.set(cv.CAP_PROP_POS_FRAMES, ri)
        ret1, f1 = self._cap_left.read()
        ret2, f2 = self._cap_right.read()
        return ret1 and ret2, f1, f2

    def _open_videos(self, left_path: str, right_path: str):
        if not self._load_calib_params():
            profile = load_active_calib_profile()
            self._show_error(
                f"Calibration « {calib_profile_label(profile)} » absente - "
                "lancez la calibration ou changez de profil actif.")
            return

        if self._cap_left:
            self._cap_left.release()
        if self._cap_right:
            self._cap_right.release()

        self._cap_left  = cv.VideoCapture(left_path)
        self._cap_right = cv.VideoCapture(right_path)

        if not self._cap_left.isOpened() or not self._cap_right.isOpened():
            self._show_error(f"Cannot open videos:\n  {left_path}\n  {right_path}")
            return

        self._left_path = left_path
        self._right_path = right_path
        self._right_dy_offset = 0.0
        self._tri_cache.clear()
        self._update_calib_profile_label()
        self.img_left.set_caption(
            f"GAUCHE rect. - {_short_video_name(left_path)}")
        self.img_right.set_caption(
            f"DROITE rect. - {_short_video_name(right_path)}")
        self._reload_grazing_intervals()
        self._shutdown_video_tracker()
        if self.chk_fish_track.isChecked():
            self._ensure_video_tracker()

        self._load_session_metadata()

        self._load_sync()
        n_left  = int(self._cap_left.get(cv.CAP_PROP_FRAME_COUNT))
        n_right = int(self._cap_right.get(cv.CAP_PROP_FRAME_COUNT))

        # Timeline couplée sur la vidéo entière (sync seule ; trim = calibration).
        self._sync_delta = self._right_sync - self._left_sync
        self._left_start = max(self._left_sync, -self._sync_delta)
        t_end = min(n_left - 1, n_right - 1 - self._sync_delta)
        self._total_frames = max(0, t_end - self._left_start + 1)

        self.slider.setRange(0, max(0, self._total_frames - 1))
        self._frame_idx = -1
        self._seek(0)

        offset = -self._sync_delta  # affichage : + = gauche en avance
        self.status_label.setText(
            f"Calib {calib_profile_label()}  |  "
            f"G : {_short_video_name(left_path, 36)}  |  "
            f"D : {_short_video_name(right_path, 36)}  ·  "
            f"sync G frame {self._left_sync} D frame {self._right_sync} "
            f"({offset:+d}) - {self._total_frames}/{min(n_left, n_right)} "
            f"frames alignées (vidéo complète)")
        self.status_label.setStyleSheet("color:#94a3b8;")
        self.status_label.show()

        self.btn_play.setEnabled(True)
        self.btn_reset.setEnabled(True)
        self.btn_disp.setEnabled(True)
        self.btn_depth.setEnabled(True)
        self._reset_points()
        self.hint_label.show()
        self.status_label.show()

    def _load_session_metadata(self):
        try:
            import fish_db_stats as fdb
        except ImportError:
            return
        if not fdb.is_available():
            return
        try:
            from fish_annotate import resolve_media_id
            mid = resolve_media_id(self._left_path, create=True)
            if not mid:
                return
            meta = fdb.get_session_metadata(mid)
            if not meta:
                return
            self.edit_session_site.setText(meta.get("site") or "")
            self.edit_session_title.setText(meta.get("session_title") or "")
            self.edit_session_notes.setText(meta.get("notes") or "")
            sd = meta.get("session_date")
            if sd:
                qdt = QDateTime.fromString(sd[:16], "yyyy-MM-dd HH:mm")
                if not qdt.isValid():
                    qdt = QDateTime.fromString(sd[:10], "yyyy-MM-dd")
                if qdt.isValid():
                    self.edit_session_date.setDateTime(qdt)
        except Exception:
            pass

    def _save_session_metadata(self):
        if not self._left_path:
            self._show_error("Chargez d'abord une vidéo.")
            return
        try:
            import fish_db_stats as fdb
            from fish_annotate import resolve_media_id
        except ImportError:
            self._show_error("Module fish_db_stats indisponible.")
            return
        mid = resolve_media_id(self._left_path, create=True)
        if not mid:
            self._show_error("Impossible d'enregistrer la session en base.")
            return
        ok = fdb.update_session_metadata(
            mid,
            site=self.edit_session_site.text(),
            session_title=self.edit_session_title.text(),
            notes=self.edit_session_notes.text(),
            session_date=self.edit_session_date.dateTime().toString("yyyy-MM-dd HH:mm:ss"),
        )
        if ok:
            self._show_status("Métadonnées session enregistrées.")
        else:
            self._show_error("Échec enregistrement session.")

    def open_video_from_database(self, left_path: str):
        """Ouvre une vidéo depuis l'onglet Base de données (gauche + droite si connue)."""
        left_path = os.path.abspath(left_path)
        right_path = ""
        pair = load_videos_txt()
        if pair and os.path.basename(pair[0]) == os.path.basename(left_path):
            right_path = pair[1]
        elif pair and os.path.basename(pair[1]) == os.path.basename(left_path):
            left_path, right_path = pair[1], pair[0]
        else:
            parent = os.path.dirname(left_path)
            stem = os.path.basename(left_path)
            for cand in (
                stem.replace("left", "right").replace("Left", "Right").replace("gauche", "droite"),
                stem.replace("_L.", "_R.").replace("-L.", "-R."),
            ):
                p = os.path.join(parent, cand)
                if os.path.isfile(p):
                    right_path = p
                    break
        if not right_path or not os.path.isfile(right_path):
            self._show_error(
                f"Vidéo droite introuvable pour :\n{left_path}\n"
                "Assignez la paire dans Sync ou chargez les deux vidéos.")
            if os.path.isfile(left_path):
                self._left_path = left_path
            return
        save_videos_txt(left_path, right_path)
        self._open_videos(left_path, right_path)
        self._show_status(f"Session ouverte : {_short_video_name(left_path)}")

    def _image_size(self) -> tuple[int, int] | None:
        if self._frame_left is None:
            return None
        h, w = self._frame_left.shape[:2]
        return w, h

    def _depth_z_range_mm(self) -> tuple[float, float]:
        return float(DEPTH_Z_FISH_NEAR_MM), float(DEPTH_Z_FISH_FAR_MM)

    def _sgbm_block_size(self, *, precise: bool = False) -> int:
        return 5 if precise else 7

    def _snapshot_rectified_stereo(
        self,
    ) -> tuple[np.ndarray, np.ndarray, dict] | None:
        """Paires rectifiées = celles affichées à l'écran (cache calibration partagé)."""
        if self._frame_left is None or self._frame_right is None or self.mtx1 is None:
            return None
        img_sz = self._image_size()
        if img_sz is None:
            return None
        cache = _ensure_stereo_rect_cache(
            self.mtx1, self.dist1, self.mtx2, self.dist2,
            self.R, self.T, img_sz, self._tri_cache)
        if (
            self._last_rect_left is not None
            and self._last_rect_right is not None
            and self._last_rect_left.shape[:2] == (img_sz[1], img_sz[0])
        ):
            return (
                self._last_rect_left.copy(),
                self._last_rect_right.copy(),
                cache,
            )
        rect_l = cv.remap(
            self._frame_left, cache['map1x'], cache['map1y'], cv.INTER_LINEAR)
        rect_r = cv.remap(
            self._frame_right, cache['map2x'], cache['map2y'], cv.INTER_LINEAR)
        if abs(self._right_dy_offset) > 0.01:
            rect_r = _shift_image_vertical(rect_r, self._right_dy_offset)
        return rect_l, rect_r, cache

    def _clear_fish_boxes_stereo(self) -> None:
        self.img_left.clear_fish_boxes()
        self.img_right.clear_fish_boxes()

    def _set_fish_boxes_stereo(
        self, boxes: list, boxes_right: list | None = None,
    ) -> None:
        self.img_left.set_fish_boxes(boxes)
        if boxes_right is not None:
            self.img_right.set_fish_boxes(list(boxes_right))
            return
        rect_l = self._last_rect_left
        rect_r = self._last_rect_right
        if boxes and rect_l is not None and rect_r is not None:
            right_boxes = _project_boxes_to_right_rect(boxes, rect_l, rect_r)
            self.img_right.set_fish_boxes(right_boxes)
        else:
            self.img_right.set_fish_boxes([])

    def _set_selected_fish_stereo(self, idx: int | None) -> None:
        self.img_left.set_selected_fish(idx)
        self.img_right.set_selected_fish(idx)

    def _set_selected_track_pick_stereo(self, idx: int | None) -> None:
        self.img_left.set_selected_track_pick(idx)
        self.img_right.set_selected_track_pick(idx)

    # ── Lecture vidéo ─────────────────────────────────────────────────────────

    def _seek(self, idx: int):
        idx = max(0, min(idx, max(0, self._total_frames - 1)))
        self._clear_points_if_frame_changed(idx)
        self._frame_idx = idx
        ok, f1, f2 = self._read_stereo_aligned(idx)
        if ok:
            self._frame_left  = f1
            self._frame_right = f2
            self._refresh_display()
        self.slider.setValue(idx)
        li = self._left_abs_frame(idx)
        ri = self._right_abs_frame(idx)
        self.lbl_frame.setText(
            f"Alignée {idx}/{max(0, self._total_frames - 1)}  "
            f"(G:{li}  D:{ri})")

    def _next_frame(self):
        if self._cap_left is None:
            return
        next_idx = self._frame_idx + 1
        if next_idx >= self._total_frames:
            self._timer.stop()
            self._playing = False
            self.btn_play.setText("▶ Play")
            return
        self._clear_points_if_frame_changed(next_idx)
        self._frame_idx = next_idx
        ok, f1, f2 = self._read_stereo_aligned(next_idx)
        if not ok:
            self._timer.stop()
            self._playing = False
            self.btn_play.setText("▶ Play")
            return
        self._frame_left  = f1
        self._frame_right = f2
        self.slider.setValue(self._frame_idx)
        li = self._left_abs_frame(self._frame_idx)
        ri = self._right_abs_frame(self._frame_idx)
        self.lbl_frame.setText(
            f"Alignée {self._frame_idx}/{max(0, self._total_frames - 1)}  "
            f"(G:{li}  D:{ri})")
        self._refresh_display()

    def _on_slider_moved(self, val: int):
        if self._playing:
            self._timer.stop()
            self._playing = False
            self.btn_play.setText("▶ Play")
        if self._cap_left:
            self._seek(val)

    def toggle_play(self):
        if self._cap_left is None or self._track_batch_running:
            return
        if self._playing:
            self._timer.stop()
            self._playing = False
            self.btn_play.setText("▶ Play")
            self._schedule_fish_detect()
        else:
            self._fish_detect_timer.stop()
            self._fish_detect_seq += 1
            self._clear_fish_boxes_stereo()
            self.lbl_fish_count.setText("")
            self.lbl_fish_status.setText("")
            self._timer.start()
            self._playing = True
            self.btn_play.setText("⏸ Pause")

    # ── Affichage ─────────────────────────────────────────────────────────────

    def _refresh_display(self):
        if self._frame_left is None or self.mtx1 is None:
            return
        img_sz = self._image_size()
        if img_sz is None:
            return
        cache = _ensure_stereo_rect_cache(
            self.mtx1, self.dist1, self.mtx2, self.dist2,
            self.R, self.T, img_sz, self._tri_cache)
        rect_l = cv.remap(
            self._frame_left, cache['map1x'], cache['map1y'], cv.INTER_LINEAR)
        rect_r = cv.remap(
            self._frame_right, cache['map2x'], cache['map2y'], cv.INTER_LINEAR)
        if abs(self._right_dy_offset) > 0.01:
            rect_r = _shift_image_vertical(rect_r, self._right_dy_offset)
        self._last_rect_left = rect_l
        self._last_rect_right = rect_r
        self.img_left.set_frame(rect_l)
        self.img_right.set_frame(rect_r)
        self._update_epipolar_ui()
        if self.chk_fish_track.isChecked() and _FISH_TRACK_AVAILABLE:
            self._run_tracking_on_frame()
        elif self.chk_track_trails.isChecked() and self._video_tracker is not None:
            cached = self._video_tracker.get_cached_boxes(self._frame_idx)
            if cached is not None:
                self._show_track_boxes(cached)
        elif (not self._playing
                and self.chk_fish_ia.isChecked()
                and self.chk_auto_fish.isChecked()
                and _FISH_DETECT_AVAILABLE):
            self._fish_detect_timer.start()

    def _shutdown_video_tracker(self) -> None:
        self._video_tracker = None

    def _ensure_video_tracker(self) -> bool:
        # Phase 7 : le tracker ne persiste plus rien en base. L'écriture des
        # pistes est le travail du `TrackingWorker` de l'application PySide6
        # (écriture par lots, fil dédié) - deux chemins concurrents sur le même
        # fichier SQLite valaient mieux qu'un seul, mais dans l'autre sens.
        if not _FISH_TRACK_AVAILABLE or not self._left_path:
            return False
        if self._video_tracker is None:
            self._video_tracker = _fish_track.VideoTracker(
                conf=float(self.spin_track_conf.value()),
            )
        return True

    def _update_track_trail_overlay(self) -> None:
        show = (
            self.chk_track_trails.isChecked()
            and self._video_tracker is not None
        )
        self.img_left.set_show_track_trails(show)
        if not show:
            self.img_left.set_track_trails({})
            return
        trails = self._video_tracker.get_trails_up_to_frame(self._frame_idx)
        self.img_left.set_track_trails(trails)

    def _on_track_trails_toggled(self, _checked: bool) -> None:
        self._update_track_trail_overlay()

    def _on_fish_track_toggled(self, enabled: bool) -> None:
        self._fish_detect_timer.stop()
        self._fish_detect_seq += 1
        if not enabled:
            self._shutdown_video_tracker()
            self._clear_fish_boxes_stereo()
            self.img_left.set_track_trails({})
            self.img_left.set_show_track_trails(False)
            self.lbl_fish_count.setText("")
            self.lbl_fish_status.setText("")
            self._schedule_fish_detect()
            return
        if not _FISH_TRACK_AVAILABLE:
            self.chk_fish_track.setChecked(False)
            return
        if not self._left_path:
            self.lbl_fish_status.setText("Chargez une vidéo pour le tracking")
            self.chk_fish_track.setChecked(False)
            return
        self._shutdown_video_tracker()
        if self._ensure_video_tracker():
            self.lbl_fish_status.setText("Tracking actif - DB tracks")
            if self._last_rect_left is not None:
                self._run_tracking_on_frame()

    def _run_tracking_on_frame(self) -> None:
        if not self.chk_fish_track.isChecked() or self._last_rect_left is None:
            return
        if not self._ensure_video_tracker():
            return
        try:
            cached = self._video_tracker.get_cached_boxes(self._frame_idx)
            if cached is not None:
                boxes = cached
            else:
                boxes = self._video_tracker.process_frame(
                    self._last_rect_left, self._frame_idx)
        except Exception as exc:
            self.lbl_fish_status.setText(f"Tracking : {exc}")
            return
        self._show_track_boxes(boxes)

    def _show_track_boxes(self, boxes: list) -> None:
        if _FISH_ANNOTATE_AVAILABLE and self._left_path:
            enriched = []
            for box in boxes:
                b = dict(box)
                # Le tracker ne rend plus d'identifiant base (phase 7) : le
                # recoupement se fait sur le numéro de piste affiché, que
                # `list_grazing_intervals` renvoie déjà - et qui est unique au
                # sein d'une vidéo, ce qui suffit ici.
                ext_id = b.get("track_id")
                if ext_id is not None and self._grazing_intervals:
                    b["manual_grazing"] = any(
                        row.get("external_track_id") == ext_id
                        and row["frame_start"] <= self._frame_idx <= row["frame_end"]
                        for row in self._grazing_intervals
                    )
                else:
                    b["manual_grazing"] = False
                enriched.append(b)
            boxes = enriched
        self._set_fish_boxes_stereo(boxes)
        self._update_track_trail_overlay()
        if self._graze_pick_idx is not None:
            self._set_selected_track_pick_stereo(self._graze_pick_idx)
        n = len(boxes)
        tracks = self._video_tracker.unique_count if self._video_tracker else 0
        cached = self._video_tracker.cached_frame_count if self._video_tracker else 0
        self.lbl_fish_count.setText(
            f"{n} suivi(s) · {tracks} piste(s) unique(s)")
        if cached >= self._total_frames and self._total_frames > 0:
            self.lbl_fish_status.setText(
                f"Analyse complète ({cached} frames) - naviguez au slider")
        elif self._playing:
            self.lbl_fish_status.setText("Tracking…")
        elif cached > 0:
            self.lbl_fish_status.setText(
                f"{cached}/{self._total_frames} frames analysées")

    def _run_track_batch(self) -> None:
        """Analyse séquentielle de toute la vidéo (pause), sans mode lecture."""
        if not _FISH_TRACK_AVAILABLE or self._track_batch_running:
            return
        if self._playing:
            self.lbl_fish_status.setText("Mettez en pause avant l'analyse")
            return
        if self._cap_left is None or self.mtx1 is None or self._total_frames <= 0:
            self.lbl_fish_status.setText("Chargez une vidéo calibrée d'abord")
            return

        self.chk_fish_track.setChecked(True)
        self._shutdown_video_tracker()
        if not self._ensure_video_tracker():
            return

        self._track_batch_running = True
        self.btn_track_analyze.setEnabled(False)
        self.btn_play.setEnabled(False)
        seg_start = 0
        seg_end = max(0, self._total_frames - 1)
        n_seg = seg_end - seg_start + 1
        self.lbl_fish_status.setText("Analyse tracking sur toute la vidéo…")

        left_path = self._left_path
        right_path = self._right_path
        left_start = self._left_start
        sync_delta = self._sync_delta
        mtx1 = self.mtx1.copy()
        dist1 = self.dist1.copy()
        mtx2 = self.mtx2.copy()
        dist2 = self.dist2.copy()
        R = self.R.copy()
        T = self.T.copy()
        tri_cache: dict = {}
        tracker = self._video_tracker

        self._track_signals = _FishTrackSignals()
        self._track_signals.progress.connect(self._on_track_batch_progress)
        self._track_signals.done.connect(self._on_track_batch_done)
        self._track_signals.error.connect(self._on_track_batch_error)
        self._track_signals.finished.connect(self._on_track_batch_finished)
        signals = self._track_signals

        def worker():
            try:
                cap_l = cv.VideoCapture(left_path)
                cap_r = cv.VideoCapture(right_path)
                if not cap_l.isOpened() or not cap_r.isOpened():
                    raise RuntimeError("Impossible d'ouvrir les vidéos pour l'analyse")

                def read_aligned(idx: int):
                    li = left_start + max(0, idx)
                    ri = left_start + max(0, idx) + sync_delta
                    cap_l.set(cv.CAP_PROP_POS_FRAMES, li)
                    cap_r.set(cv.CAP_PROP_POS_FRAMES, ri)
                    ok1, f1 = cap_l.read()
                    ok2, f2 = cap_r.read()
                    return ok1 and ok2, f1, f2

                for idx in range(seg_start, seg_end + 1):
                    ok, f1, f2 = read_aligned(idx)
                    if not ok:
                        break
                    h, w = f1.shape[:2]
                    cache = _ensure_stereo_rect_cache(
                        mtx1, dist1, mtx2, dist2, R, T, (w, h), tri_cache)
                    rect_l = cv.remap(
                        f1, cache['map1x'], cache['map1y'], cv.INTER_LINEAR)
                    tracker.process_frame(rect_l, idx)
                    local = idx - seg_start
                    if local % 5 == 0 or idx == seg_end:
                        signals.progress.emit(local + 1, n_seg)

                cap_l.release()
                cap_r.release()
                n_tracks = tracker.unique_count
                signals.done.emit(n_tracks)
            except Exception as exc:
                signals.error.emit(str(exc))
            finally:
                signals.finished.emit()

        threading.Thread(target=worker, daemon=True).start()

    def _on_track_batch_progress(self, current: int, total: int) -> None:
        pct = int(100 * current / max(1, total))
        self.lbl_fish_status.setText(
            f"Analyse tracking {pct} % ({current}/{total})")

    def _on_track_batch_done(self, n_tracks: int) -> None:
        if n_tracks == 0:
            self.lbl_fish_status.setText(
                "Aucune piste - baissez le seuil IA ou vérifiez la vidéo")
        else:
            self.lbl_fish_status.setText(f"{n_tracks} piste(s) sur la vidéo")
        self._reload_grazing_intervals()
        self._run_tracking_on_frame()

    def _on_track_batch_error(self, msg: str) -> None:
        self.lbl_fish_status.setText(f"Analyse tracking : {msg}")

    def _on_track_batch_finished(self) -> None:
        self._track_batch_running = False
        self.btn_track_analyze.setEnabled(_FISH_TRACK_AVAILABLE)
        self.btn_play.setEnabled(self._cap_left is not None)

    def _reload_grazing_intervals(self) -> None:
        self._grazing_intervals = []
        self.list_graze_intervals.clear()
        if not _FISH_ANNOTATE_AVAILABLE or not self._left_path:
            return
        try:
            self._grazing_intervals = _fish_annotate.list_grazing_intervals(
                self._left_path)
        except Exception:
            return
        for row in self._grazing_intervals:
            ext = row.get("external_track_id")
            label = (
                f"#{ext}  frames {row['frame_start']}→{row['frame_end']}"
                if ext is not None else
                f"piste {row['track_db_id'][:8]}…  "
                f"{row['frame_start']}→{row['frame_end']}"
            )
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, row["event_id"])
            self.list_graze_intervals.addItem(item)

    def _graze_selected_box(self) -> dict | None:
        if self._graze_pick_idx is None:
            return None
        boxes = self.img_left._fish_boxes
        if self._graze_pick_idx >= len(boxes):
            return None
        return boxes[self._graze_pick_idx]

    def _update_graze_track_label(self) -> None:
        box = self._graze_selected_box()
        if not box:
            self.lbl_graze_track.setText("Piste : - (Maj+clic sur une bbox)")
            return
        ext = box.get("track_id")
        # Le tracker n'écrit plus en base : la piste n'y existe que si le suivi
        # de l'application PySide6 l'y a mise. On le demande à fish_annotate au
        # clic - jamais à chaque frame.
        db = None
        if _FISH_ANNOTATE_AVAILABLE and self._left_path and ext is not None:
            try:
                db = _fish_annotate.resolve_track_db_id(self._left_path, int(ext))
            except Exception:
                db = None
        if db:
            self.lbl_graze_track.setText(f"Piste : #{ext} (en base)")
        else:
            self.lbl_graze_track.setText(
                f"Piste : #{ext} - lancez « Analyser tracking » sur le passage")

    def _on_fish_track_picked(self, idx: int) -> None:
        self._graze_pick_idx = idx
        self._set_selected_track_pick_stereo(idx)
        self._update_graze_track_label()
        if self._graze_start_frame is not None:
            self.lbl_fish_status.setText(
                f"Début broute frame {self._graze_start_frame} - "
                f"allez à la frame de fin")

    def _on_graze_start(self) -> None:
        box = self._graze_selected_box()
        if not box or box.get("track_id") is None:
            self.lbl_fish_status.setText("Maj+clic sur un poisson suivi d'abord")
            return
        self._graze_start_frame = self._frame_idx
        self.lbl_fish_status.setText(
            f"Début broute frame {self._graze_start_frame} - "
            f"naviguez puis « Fin broute »")

    def _on_graze_end(self) -> None:
        if not _FISH_ANNOTATE_AVAILABLE:
            return
        box = self._graze_selected_box()
        if not box:
            self.lbl_fish_status.setText("Sélectionnez une piste (Maj+clic)")
            return
        if self._graze_start_frame is None:
            self.lbl_fish_status.setText("Cliquez « Début broute » d'abord")
            return
        db_id = None
        if box.get("track_id") is not None:
            db_id = _fish_annotate.resolve_track_db_id(
                self._left_path, int(box["track_id"]))
        if not db_id:
            self.lbl_fish_status.setText(
                "Piste absente de la base - lancez le suivi automatique depuis "
                "l'application AquaMeasure (page Mesure)")
            return
        try:
            ev_id = _fish_annotate.add_grazing_interval(
                db_id, self._graze_start_frame, self._frame_idx)
        except Exception as exc:
            self.lbl_fish_status.setText(f"Broute : {exc}")
            return
        self._graze_start_frame = None
        self._reload_grazing_intervals()
        self._run_tracking_on_frame()
        self.lbl_fish_status.setText(f"Intervalle enregistré ({ev_id[:8]}…)")

    def _on_graze_cancel(self) -> None:
        self._graze_start_frame = None
        self.lbl_fish_status.setText("Annotation broute annulée")

    def _on_graze_interval_delete(self, item: QListWidgetItem) -> None:
        if not _FISH_ANNOTATE_AVAILABLE:
            return
        ev_id = item.data(Qt.ItemDataRole.UserRole)
        if not ev_id:
            return
        try:
            _fish_annotate.delete_grazing_interval(ev_id)
        except Exception as exc:
            self.lbl_fish_status.setText(f"Suppression : {exc}")
            return
        self._reload_grazing_intervals()
        self._run_tracking_on_frame()

    def _on_fish_ia_toggled(self, enabled: bool) -> None:
        self.chk_auto_fish.setEnabled(enabled and _FISH_DETECT_AVAILABLE)
        self._fish_detect_timer.stop()
        self._fish_detect_seq += 1
        if not enabled:
            self._clear_fish_boxes_stereo()
            self.lbl_fish_count.setText("")
            self.lbl_fish_status.setText("IA désactivée")
            self.btn_fish_detect.setEnabled(False)
            return
        self.lbl_fish_status.setText("")
        self._on_fish_detect_finished()

    def _schedule_fish_detect(self):
        if self.chk_fish_track.isChecked():
            return
        if (not self._playing
                and self.chk_fish_ia.isChecked()
                and self.chk_auto_fish.isChecked()
                and _FISH_DETECT_AVAILABLE):
            self._fish_detect_timer.start()

    def _run_fish_detect(self):
        if not _FISH_DETECT_AVAILABLE or self._fish_detecting:
            return
        if not self.chk_fish_ia.isChecked() or self.chk_fish_track.isChecked():
            return
        if self._last_rect_left is None or self._last_rect_right is None or self._playing:
            return

        frame_l = self._last_rect_left.copy()
        frame_r = self._last_rect_right.copy()
        conf = float(self.spin_track_conf.value())
        seq = self._fish_detect_seq + 1
        self._fish_detect_seq = seq
        self._fish_detecting = True
        self.btn_fish_detect.setEnabled(False)
        self.lbl_fish_status.setText("Détection…")

        self._fish_signals = _FishDetectSignals()
        self._fish_signals.boxes_ready.connect(self._on_fish_boxes_ready)
        self._fish_signals.error.connect(self._on_fish_detect_error)
        self._fish_signals.finished.connect(self._on_fish_detect_finished)
        signals = self._fish_signals

        def worker():
            try:
                boxes_l = _fish_detect.detect_fish(frame_l, conf=conf)
                boxes_r = _fish_detect.detect_fish(frame_r, conf=conf)
                if seq == self._fish_detect_seq:
                    signals.boxes_ready.emit((boxes_l, boxes_r))
            except Exception as exc:
                if seq == self._fish_detect_seq:
                    signals.error.emit(str(exc))
            finally:
                signals.finished.emit()

        threading.Thread(target=worker, daemon=True).start()

    def _on_fish_boxes_ready(self, payload):
        if self._playing or not self.chk_fish_ia.isChecked():
            return
        if isinstance(payload, tuple):
            boxes_l, boxes_r = payload
        else:
            boxes_l, boxes_r = payload, None
        self._set_fish_boxes_stereo(boxes_l, boxes_r)
        n = len(boxes_l)
        names = {
            b.get("species_name") or b.get("cls_name", "poisson") for b in boxes_l
        }
        if len(names) > 1 or (names and "poisson" not in names and "fish" not in names):
            self.lbl_fish_count.setText(
                f"{n} détecté{'s' if n != 1 else ''} - {', '.join(sorted(names))}")
        else:
            self.lbl_fish_count.setText(
                f"{n} poisson{'s' if n != 1 else ''} détecté{'s' if n != 1 else ''}")
        self.lbl_fish_status.setText("")

    def _run_retrain_from_db(self):
        if not _FISH_ANNOTATE_AVAILABLE:
            return
        rank = self.combo_export_rank.currentText()
        self.btn_retrain.setEnabled(False)
        self.lbl_ann_status.setText(f"Export + entraînement ({rank})…")

        def worker():
            try:
                result = _fish_annotate.export_and_retrain(rank=rank, epochs=50)
                msg = result["stdout"][-400:] if result["stdout"] else ""
                if result["ok"]:
                    status = f"Ré-entraînement terminé ({rank})"
                else:
                    err = result["stderr"] or msg or "échec"
                    status = f"Erreur : {err[:120]}"
            except Exception as exc:
                status = f"Erreur : {exc}"

            def ui_done():
                self.lbl_ann_status.setText(status)
                self.btn_retrain.setEnabled(True)

            QTimer.singleShot(0, ui_done)

        threading.Thread(target=worker, daemon=True).start()

    def _on_fish_box_clicked(self, idx: int):
        if self._playing:
            self.lbl_ann_status.setText("Mettez en pause pour ajouter au registre (clic droit)")
            return
        self._set_selected_fish_stereo(idx)
        # L'ajout au registre (fish_registry) a été retiré en phase 7 : cette
        # fonction vit dans le registre de session de l'application PySide6.
        # Le clic ne fait plus que sélectionner le poisson à l'écran.

    def _measure_fish_bbox_length(self) -> None:
        if not _FISH_SPARSE_MEASURE_AVAILABLE:
            return
        if self._playing:
            self.lbl_fish_status.setText("Mettez en pause pour la longueur bbox")
            return
        if self.mtx1 is None:
            self.lbl_fish_status.setText("Calibrez d'abord")
            return
        snap = self._snapshot_rectified_stereo()
        if snap is None:
            return
        rect_l, rect_r, cache = snap
        boxes = self.img_left._fish_boxes
        if not boxes:
            self.lbl_fish_status.setText(
                "Détectez un poisson (IA ou tracking) avant la mesure")
            return
        idx = self.img_left.get_selected_fish()
        if idx is None or idx >= len(boxes):
            idx = 0
        box = dict(boxes[idx])
        P1, P2 = cache["P1"], cache["P2"]
        result = _fish_sparse_measure.measure_bbox_length_mm(
            rect_l, rect_r, box, P1, P2)
        if result is None:
            self.lbl_fish_status.setText(
                "Longueur bbox : échec match droite - baissez seuil IA ou vérifiez sync")
            return
        length = result["length_mm"]
        pa, pb = result["pixel_a"], result["pixel_b"]
        self.img_left.set_handle(0, pa[0], pa[1])
        self.img_left.set_handle(1, pb[0], pb[1])
        self._step = 4
        self._update_epipolar_ui()
        self.distance_label.setText(f"Longueur poisson :  {length:.1f} mm")
        self.distance_label.show()
        mid = (np.array(result["end_a_mm"]) + np.array(result["end_b_mm"])) * 0.5
        self._stereo_midpoint = tuple(mid.tolist())
        self.lbl_fish_status.setText(
            f"Longueur bbox : {length:.1f} mm "
            f"(scores {result['score_a']:.2f} / {result['score_b']:.2f})")
        self.uncertainty_label.setText(
            "Sparse bbox - poisson en pause recommandé")
        self.uncertainty_label.show()

    def _on_fish_detect_error(self, msg: str):
        self._clear_fish_boxes_stereo()
        self.lbl_fish_count.setText("")
        self.lbl_fish_status.setText(f"IA : {msg}")

    def _on_fish_detect_finished(self):
        self._fish_detecting = False
        if _FISH_DETECT_AVAILABLE:
            self.btn_fish_detect.setEnabled(
                self.chk_fish_ia.isChecked()
                and self._last_rect_left is not None
                and not self._playing)

    def _epipolar_enabled(self) -> bool:
        return self.chk_epipolar.isChecked()

    def _epipolar_line_ys(self) -> list[float]:
        ys = []
        for h in self.img_left.get_handles():
            if h is not None:
                ys.append(float(h[1]))
        return ys

    def _clamp_right_point(self, idx: int, x: float, y: float) -> tuple[float, float]:
        if not self._epipolar_enabled():
            return x, y
        left_h = self.img_left.get_handles()
        if 0 <= idx < len(left_h) and left_h[idx] is not None:
            return x, float(left_h[idx][1])
        return x, y

    def _on_epipolar_toggled(self, _state: int):
        self._update_epipolar_ui()
        if self._epipolar_enabled():
            self._sync_right_handles_y()
        self._try_compute_distance()

    def _update_epipolar_ui(self):
        ys = self._epipolar_line_ys()
        show = self._epipolar_enabled() and bool(ys)
        self.img_left.set_epipolar_guides(ys, show)
        self.img_right.set_epipolar_guides(ys, show)
        if self._epipolar_enabled():
            self.img_right.set_point_clamp(self._clamp_right_point)
        else:
            self.img_right.set_point_clamp(None)
        self._update_hint()
        self.img_left.update()
        self.img_right.update()

    def _sync_right_handles_y(self):
        if not self._epipolar_enabled():
            return
        left_h = self.img_left.get_handles()
        right_h = self.img_right.get_handles()
        for i in range(2):
            if left_h[i] is None or right_h[i] is None:
                continue
            if abs(right_h[i][1] - left_h[i][1]) > 0.01:
                self.img_right.set_handle(i, right_h[i][0], left_h[i][1])

    def _on_handle_released(self):
        self._update_epipolar_ui()
        if self._epipolar_enabled():
            self._sync_right_handles_y()
        self._try_compute_distance()

    # ── Mesure par clics / handles ────────────────────────────────────────────

    def _on_point_placed(self, side: str, x: float, y: float):
        """Reçu depuis MeasureImageWidget quand l'user clique hors d'un handle."""
        if self._frame_left is None:
            return
        if side == 'left' and self._step in (0, 1):
            if self._playing:
                return
            self.img_left.set_handle(self._step, x, y)
            self._step += 1
            self._update_epipolar_ui()
        elif side == 'right' and self._step in (2, 3):
            if self._playing:
                return
            idx = self._step - 2
            x, y = self._clamp_right_point(idx, x, y)
            self.img_right.set_handle(idx, x, y)
            self._step += 1
            self._update_epipolar_ui()
            if self._step == 4:
                self._try_compute_distance()

    def _try_compute_distance(self):
        """Recompute si les 4 handles sont posés (appelé aussi après drag)."""
        if self._epipolar_enabled():
            self._sync_right_handles_y()
        left_h  = self.img_left.get_handles()
        right_h = self.img_right.get_handles()
        if None in left_h or None in right_h:
            return
        if self.mtx1 is None:
            return

        img_sz = self._image_size()
        if img_sz is None:
            return
        cache = _ensure_stereo_rect_cache(
            self.mtx1, self.dist1, self.mtx2, self.dist2,
            self.R, self.T, img_sz, self._tri_cache)
        P1, P2 = cache['P1'], cache['P2']
        rA = self._clamp_right_point(0, right_h[0][0], right_h[0][1])
        rB = self._clamp_right_point(1, right_h[1][0], right_h[1][1])
        p3dA = _triangulate_rect_pixels(left_h[0], rA, P1, P2)
        p3dB = _triangulate_rect_pixels(left_h[1], rB, P1, P2)
        dist = float(np.linalg.norm(p3dB - p3dA))

        if not np.isfinite(dist):
            self.distance_label.setText("Distance A→B :  -")
            self.distance_label.show()
            self._stereo_midpoint = None
            self.uncertainty_label.hide()
            self.precision_label.hide()
            return

        dy_a = abs(float(right_h[0][1]) - float(left_h[0][1]))
        dy_b = abs(float(right_h[1][1]) - float(left_h[1][1]))
        self._stereo_midpoint = tuple(((p3dA + p3dB) * 0.5).tolist())
        self.distance_label.setText(f"Distance A→B :  {dist:.1f} mm")
        self.distance_label.show()
        if not self._epipolar_enabled() and (dy_a > 2.0 or dy_b > 2.0):
            self.uncertainty_label.setText(
                f"ΔY épilolaire élevé (A:{dy_a:.1f}px  B:{dy_b:.1f}px) "
                f"- cochez « Lignes épilolaires » ou recalibrez")
            self.uncertainty_label.show()
        elif self._epipolar_enabled() and self.stereo_rmse is not None:
            self.uncertainty_label.setText(
                "Lignes = hauteur A/B gauche - si elles ne passent pas sur les coins "
                "à droite, la rectification est mauvaise (recalibration).")
            self.uncertainty_label.show()
        else:
            self.uncertainty_label.hide()
        if self.stereo_rmse is not None and self.stereo_rmse > 1.5:
            self.precision_label.setText(
                f"RMSE calib {self.stereo_rmse:.2f} px - recalibration conseillée")
            self.precision_label.show()
        else:
            self.precision_label.hide()

    def _update_hint(self):
        steps = _STEPS_EPI if self._epipolar_enabled() else _STEPS_FREE
        self.hint_label.setText(steps[min(self._step, 4)])

    def _reset_points(self):
        self._step = 0
        self.img_left.clear_handles()
        self.img_right.clear_handles()
        self._update_epipolar_ui()
        steps = _STEPS_EPI if self._epipolar_enabled() else _STEPS_FREE
        self.hint_label.setText(steps[0])
        self.distance_label.hide()
        self.uncertainty_label.hide()
        self.precision_label.hide()

    # ── Nuage de points 3D ────────────────────────────────────────────────────

    # ── Disparity Map ─────────────────────────────────────────────────────────

    def _launch_disparity(self):
        if self._frame_left is None:
            return
        if self._playing:
            self._show_error("Pause the video first")
            return
        snap = self._snapshot_rectified_stereo()
        if snap is None:
            self._show_error("Calibration / vidéo manquante")
            return

        self._show_status("Disparité SGBM (images rectifiées affichées)…")
        self.btn_disp.setEnabled(False)

        rect1, rect2, cache = snap
        mtx1 = self.mtx1
        T = self.T
        z_near, z_far = self._depth_z_range_mm()
        block_sz = self._sgbm_block_size(precise=False)
        P1 = cache['P1']

        self._disp_signals = _PCSignals()
        self._disp_signals.status.connect(self._show_status)
        self._disp_signals.error.connect(self._show_error)
        self._disp_signals.finished.connect(
            lambda: self.btn_disp.setEnabled(True))
        self._disp_signals.image_ready.connect(self._open_disparity_window)
        signals = self._disp_signals

        def compute_and_show():
            try:
                h, w = rect1.shape[:2]

                disp_raw, min_d, num_d, _, _, diag = _compute_disparity_sgbm(
                    rect1, rect2, mtx1, T,
                    z_near_mm=z_near, z_far_mm=z_far, block_size=block_sz,
                    P1=P1)
                blind = min(min_d + num_d, w)
                wls_txt = "WLS" if diag['wls_ok'] else "sans WLS"
                signals.status.emit(
                    f"SGBM block {block_sz}×{block_sz}  |  "
                    f"{z_near/1000:.2f}–{z_far/1000:.1f} m  |  "
                    f"brut {diag['pct_raw']:.1f}%  |  WLS {diag['pct_wls']:.1f}% ({wls_txt})  |  "
                    f"disp [{min_d}…{min_d + num_d}] px  "
                    f"(attendu {diag['disp_at_z_far']:.0f}…{diag['disp_at_z_near']:.0f})  |  "
                    f"zone aveugle ~{100 * blind / w:.0f}%")
                if diag['pct_raw'] < 1.0:
                    signals.status.emit(
                        "⚠ 0 % valide - vérifier assignation G/D Sync ou recalibrer")

                # ── 3. Stats ─────────────────────────────────────────────────
                valid_mask = disp_raw > (min_d + 0.5)
                pct_valid = diag['pct_raw']
                d_min  = float(disp_raw[valid_mask].min())  if valid_mask.any() else 0
                d_max  = float(disp_raw[valid_mask].max())  if valid_mask.any() else 0
                d_mean = float(disp_raw[valid_mask].mean()) if valid_mask.any() else 0
                signals.status.emit(
                    f"Disparity - mean: {d_mean:.1f}px | "
                    f"min: {d_min:.1f} | max: {d_max:.1f} | "
                    f"valid pixels: {pct_valid:.1f}%")

                # ── 4. Colormap TURBO (percentiles 2–98, gris = invalide) ────
                disp_norm = np.zeros_like(disp_raw, dtype=np.uint8)
                if valid_mask.any():
                    vals = disp_raw[valid_mask]
                    d_lo = float(np.percentile(vals, 2.0))
                    d_hi = float(np.percentile(vals, 98.0))
                    t = np.clip((disp_raw - d_lo) / max(d_hi - d_lo, 1e-3),
                                0.0, 1.0)
                    disp_norm = (t * 255.0).astype(np.uint8)
                disp_color = cv.applyColorMap(disp_norm, cv.COLORMAP_TURBO)
                disp_color[~valid_mask] = (40, 40, 40)

                # ── 5. Lignes épipolaires horizontales ───────────────────────
                def draw_epilines(img):
                    out = img.copy()
                    for y in range(0, h, 30):
                        cv.line(out, (0, y), (w, y), (0, 220, 0), 1,
                                cv.LINE_AA)
                    return out

                vis_l = draw_epilines(rect1)
                vis_r = draw_epilines(rect2)

                # ── 6. Composition côte à côte ───────────────────────────────
                label_h = 28
                def add_label(img, text):
                    out = np.zeros((img.shape[0] + label_h, img.shape[1], 3),
                                   dtype=np.uint8)
                    out[label_h:] = img
                    cv.putText(out, text, (8, 20),
                               cv.FONT_HERSHEY_SIMPLEX, 0.65,
                               (200, 200, 200), 1, cv.LINE_AA)
                    return out

                panel_l = add_label(vis_l,    "LEFT rectified + epipolar lines")
                panel_c = add_label(disp_color, "DISPARITY MAP (turbo | gris=invalide)")
                panel_r = add_label(vis_r,    "RIGHT rectified + epipolar lines")

                # Redimensionner à hauteur commune si nécessaire
                target_h = panel_l.shape[0]
                def resize_h(img, th):
                    ratio = th / img.shape[0]
                    return cv.resize(img,
                                     (int(img.shape[1] * ratio), th),
                                     interpolation=cv.INTER_LINEAR)

                composite = np.hstack([
                    resize_h(panel_l, target_h),
                    np.full((target_h, 4, 3), 40, dtype=np.uint8),  # séparateur
                    resize_h(panel_c, target_h),
                    np.full((target_h, 4, 3), 40, dtype=np.uint8),
                    resize_h(panel_r, target_h),
                ])

                signals.image_ready.emit(composite)

            except Exception as exc:
                import traceback
                signals.error.emit(f"[DISP ERROR] {exc}\n{traceback.format_exc()}")
            finally:
                signals.finished.emit()

        threading.Thread(target=compute_and_show, daemon=True).start()

    # ── DLT Depth Map ─────────────────────────────────────────────────────────

    def _launch_depth_map(self):
        if self._frame_left is None:
            return
        if self._playing:
            self._show_error("Pause the video first")
            return
        snap = self._snapshot_rectified_stereo()
        if snap is None:
            self._show_error("Calibration / vidéo manquante")
            return

        self._show_status("Depth map (images rectifiées affichées)…")
        self.btn_depth.setEnabled(False)

        rect1, rect2, cache = snap
        mtx1 = self.mtx1
        T = self.T
        z_near, z_far = self._depth_z_range_mm()
        block_sz = self._sgbm_block_size(precise=True)
        Q = cache['Q']
        P1 = cache['P1']

        self._depth_signals = _PCSignals()
        self._depth_signals.status.connect(self._show_status)
        self._depth_signals.error.connect(self._show_error)
        self._depth_signals.finished.connect(
            lambda: self.btn_depth.setEnabled(True))
        self._depth_signals.image_ready.connect(self._open_depth_window)
        signals = self._depth_signals

        def compute():
            try:
                h, w = rect1.shape[:2]

                disp_raw, min_d, num_d, _, _, diag = _compute_disparity_sgbm(
                    rect1, rect2, mtx1, T,
                    z_near_mm=z_near, z_far_mm=z_far,
                    fill_holes=False, precise=True, use_wls=True,
                    block_size=block_sz, P1=P1)

                depth_map, valid_mask = _depth_from_disparity(
                    disp_raw, Q, min_d, z_near, z_far)

                if valid_mask.any():
                    vals = depth_map[valid_mask]
                    z_lo = float(np.percentile(vals, 2.0))
                    z_hi = float(np.percentile(vals, 98.0))
                    if z_hi - z_lo < 50.0:
                        z_lo, z_hi = z_near, z_far
                else:
                    z_lo, z_hi = z_near, z_far

                y_refl = 0.32
                vis_rect_l = _make_depth_visualizations(
                    rect1, depth_map, valid_mask, z_lo, z_hi, y_refl)
                vis_rect_r = _make_depth_visualizations(
                    rect2, depth_map, valid_mask, z_lo, z_hi, y_refl)
                overlays = {
                    'left_rect': vis_rect_l['overlay'],
                    'right_rect': vis_rect_r['overlay'],
                }
                pure = {
                    'left_rect': vis_rect_l['pure'],
                    'right_rect': vis_rect_r['pure'],
                }
                depth_maps = {
                    'left_rect': vis_rect_l['depth'],
                    'right_rect': vis_rect_r['depth'],
                }

                warn = ""
                if diag['pct_raw'] < 30.0:
                    warn = ("  ⚠ Couverture SGBM < 30 % - "
                            "vérifiez plage Z, sync G/D, recalage vertical.")
                signals.status.emit(
                    f"Depth rectifiée - {w}×{h} px  |  "
                    f"brut {diag['pct_raw']:.1f}%  |  WLS {diag['pct_wls']:.1f}%  |  "
                    f"{z_near/1000:.2f}–{z_far/1000:.1f} m{warn}")

                signals.image_ready.emit({
                    'overlays': overlays,
                    'pure': pure,
                    'depth_maps': depth_maps,
                    'stats': {'z_lo': z_lo, 'z_hi': z_hi},
                    'diag': diag,
                })

            except Exception as exc:
                import traceback
                signals.error.emit(
                    f"[DEPTH ERROR] {exc}\n{traceback.format_exc()}")
            finally:
                signals.finished.emit()

        threading.Thread(target=compute, daemon=True).start()

    def _open_depth_window(self, data: dict):
        self._depth_win = DepthMapWindow(data)
        self._depth_win.show()

    def _open_disparity_window(self, bgr_image: np.ndarray):
        """Ouvre la fenêtre de disparité dans le thread principal (Qt)."""
        self._disp_win = DisparityWindow(bgr_image)
        self._disp_win.show()

    # ── Statut / erreur ───────────────────────────────────────────────────────

    def _show_status(self, msg: str):
        self.status_label.setText(msg)
        self.status_label.setStyleSheet("color: #5ab5f5; font-weight: bold;")
        self.status_label.show()

    def _show_error(self, msg: str):
        self.status_label.setText(msg)
        self.status_label.setStyleSheet("color: #e05555; font-weight: bold;")
        self.status_label.show()


# ── Fenêtre principale ────────────────────────────────────────────────────────

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("AquaMeasure")
        self.resize(1280, 900)

        # ── Widget central ────────────────────────────────────────────────────
        central = QWidget()
        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # ── Bandeau titre ─────────────────────────────────────────────────────
        header = QWidget()
        header.setStyleSheet("background:#0d3b6e;")
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(16, 10, 16, 10)
        header_layout.setSpacing(2)

        title_lbl = QLabel("AquaMeasure")
        title_lbl.setFont(QFont("Segoe UI", 24, QFont.Weight.Bold))
        title_lbl.setStyleSheet("color:#ffffff;")
        title_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        header_layout.addWidget(title_lbl)

        sub_lbl = QLabel("Stereo vision fish measurement tool")
        sub_lbl.setFont(QFont("Segoe UI", 11))
        sub_lbl.setStyleSheet("color:#a0c4e8;")
        sub_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        header_layout.addWidget(sub_lbl)

        root_layout.addWidget(header)

        # ── Onglets ───────────────────────────────────────────────────────────
        self._tabs = QTabWidget()
        self._sync_tab    = SyncTab()
        self._calib_tab   = CalibrationTab()
        self._measure_tab = MeasureTab()
        # L'onglet « Base de données » (fish_database) a été retiré en phase 7 :
        # l'exploration de la base est la page Données de l'application PySide6.
        self._database_tab = None
        self._tabs.addTab(self._sync_tab,    "① Sync")
        self._tabs.addTab(self._calib_tab,   "② Calibration")
        self._tabs.addTab(self._measure_tab, "③ Measure")
        if self._database_tab is not None:
            self._tabs.addTab(self._database_tab, "④ Base de données")
        root_layout.addWidget(self._tabs, stretch=1)

        # Connexion Sync → Calibration
        self._sync_tab.go_to_calib.connect(self._on_go_to_calib)
        self._calib_tab.profile_changed.connect(
            self._measure_tab.reload_calibration_profile)
        if self._database_tab is not None:
            self._database_tab.open_in_measure.connect(self._on_open_in_measure)
            self._database_tab.status_message.connect(
                lambda msg: self.statusBar().showMessage(msg, 5000)
                if self.statusBar() else None)

        self.setCentralWidget(central)

    def _on_go_to_calib(self, left_path: str, right_path: str):
        """Transfère les vidéos dans l'onglet Calibration et bascule dessus."""
        self._calib_tab.import_from_sync(left_path, right_path)
        self._tabs.setCurrentWidget(self._calib_tab)

    def _on_open_in_measure(self, left_path: str):
        self._measure_tab.open_video_from_database(left_path)
        self._tabs.setCurrentWidget(self._measure_tab)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Space:
            self._measure_tab.toggle_play()
        else:
            super().keyPressEvent(event)


# ── Point d'entrée ────────────────────────────────────────────────────────────

if __name__ == '__main__':
    os.chdir(_APP_ROOT)
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
