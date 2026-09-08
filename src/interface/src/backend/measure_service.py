from __future__ import annotations

import sys
from collections import OrderedDict
from pathlib import Path

import cv2
import numpy as np
from PySide6.QtCore import QObject, QPointF, Signal, Slot

from src.backend.frame_reader import SequentialFrameReader
from src.util import npy_io, paths

# Une frame 1080p brute pèse ~6 Mo : ce budget couvre une soixantaine de frames
# par vidéo, soit deux bonnes secondes d'aller-retour sans re-décodage.
_READER_BUDGET_MB = 384

# Les paires déjà rectifiées sont plus lourdes (deux images) et le remap est
# rapide : on en garde peu, juste de quoi couvrir un va-et-vient serré.
_RECT_CACHE_FRAMES = 12


class MeasureService(QObject):
    distanceMm = Signal(float)
    logLine = Signal(str)
    error = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._left_path = ""
        self._right_path = ""
        self._left_cap: SequentialFrameReader | None = None
        self._right_cap: SequentialFrameReader | None = None
        self._left_fps = 30.0
        self._right_fps = 30.0
        self._left_fc = 0
        self._right_fc = 0
        self._width = 0
        self._height = 0
        self._left_sync = 0
        self._right_sync = 0
        self._sync_delta = 0
        self._left_start = 0
        self._total_frames = 0
        self._calib_ok = False
        # Resume de la calibration reellement en memoire - pas celle qui est
        # sur le disque a l'instant ou l'on regarde. Sans cela, l'ecran
        # affichait la nouvelle calibration alors que les matrices utilisees
        # etaient encore les anciennes.
        self._calib_summary = ""
        self._calib_dir = ""
        self._tri_cache: dict = {}
        self._rect_maps: dict | None = None
        self._rect_cache: OrderedDict[int, tuple] = OrderedDict()

    def _repo_root(self) -> Path:
        return paths.app_root()

    def _ensure_aquameasure_import(self):
        root = str(self._repo_root())
        if root not in sys.path:
            sys.path.insert(0, root)

    def _load_sync(self) -> None:
        """Charge sync_frames.npy - le trim In/Out reste réservé à la calibration."""
        self._left_sync = self._right_sync = 0
        sync = paths.cam_param("sync_frames.npy")
        if sync.is_file():
            try:
                s = npy_io.load_int1d(str(sync))
                if len(s) >= 2:
                    self._left_sync, self._right_sync = int(s[0]), int(s[1])
            except OSError:
                pass

    def _recompute_timeline(self) -> None:
        """Timeline couplée sur la vidéo entière (comme aquameasure.py Mesure)."""
        self._sync_delta = self._right_sync - self._left_sync
        self._left_start = max(self._left_sync, -self._sync_delta)
        t_end = min(self._left_fc - 1, self._right_fc - 1 - self._sync_delta)
        self._total_frames = max(0, t_end - self._left_start + 1)

    def set_videos(self, left: str, right: str) -> None:
        self._close_caps()
        self._left_path = left
        self._right_path = right
        self._left_fc = self._right_fc = 0
        self._total_frames = 0
        self._rect_cache.clear()
        self._left_cap = SequentialFrameReader(left, memory_budget_mb=_READER_BUDGET_MB)
        self._right_cap = SequentialFrameReader(right, memory_budget_mb=_READER_BUDGET_MB)
        if not self._left_cap.open() or not self._right_cap.open():
            self.error.emit(
                f"Impossible d'ouvrir les videos :\n  G: {left}\n  D: {right}")
            self._close_caps()
            return
        self._left_fc = self._left_cap.frame_count
        self._right_fc = self._right_cap.frame_count
        self._left_fps = self._left_cap.fps or 30.0
        self._right_fps = self._right_cap.fps or 30.0
        self._width = self._left_cap.width
        self._height = self._left_cap.height
        self._load_sync()
        self._recompute_timeline()
        self.logLine.emit(
            f"Videos chargees - {self._total_frames} frames alignees "
            f"(G sync f{self._left_sync}, D sync f{self._right_sync}, "
            f"offset {self._sync_delta:+d})"
        )

    def clear_videos(self) -> None:
        """Ferme la prise courante sans toucher aux fichiers sur disque."""
        self._close_caps()
        self._left_path = ""
        self._right_path = ""
        self._left_fc = self._right_fc = 0
        self._total_frames = 0
        self._width = self._height = 0
        self._rect_cache.clear()

    def _close_caps(self) -> None:
        for cap in (self._left_cap, self._right_cap):
            if cap is not None:
                cap.close()
        self._left_cap = self._right_cap = None

    def _calib_paths(self) -> tuple[str, Path]:
        return "", paths.camera_params_dir()

    def load_calibration(self) -> bool:
        profile, base = self._calib_paths()
        if not paths.calib_profile_complete(profile):
            self.error.emit(
                f"Calibration incomplete dans {base} - lancez l'onglet Calibration")
            self._calib_ok = False
            self._calib_summary = ""
            self._calib_dir = ""
            return False
        self._calib_ok = True
        self._tri_cache.clear()
        self._rect_maps = None
        self._rect_cache.clear()
        # Tracabilite : sans la date et le dossier, deux calibrations pour la
        # meme paire de videos etaient impossibles a distinguer a l'ecran.
        summary = paths.calibration_summary(profile)
        self._calib_summary = summary
        self._calib_dir = str(base)
        self.logLine.emit(
            f"Calibration chargee - {summary}" if summary else "Calibration chargee"
        )
        self.logLine.emit(f"  dossier : {base}")
        return True

    def calibration_summary(self) -> str:
        return self._calib_summary

    def calibration_dir(self) -> str:
        return self._calib_dir

    def frame_count(self) -> int:
        return self._total_frames

    def left_abs_frame(self, index: int) -> int:
        return self._left_start + max(0, index)

    def aligned_index_from_left_abs(self, abs_frame: int) -> int:
        if self._total_frames <= 0:
            return 0
        idx = int(abs_frame) - self._left_start
        return max(0, min(self._total_frames - 1, idx))

    def right_abs_frame(self, index: int) -> int:
        return self._left_start + max(0, index) + self._sync_delta

    def left_sync_frame(self) -> int:
        return self._left_sync

    def right_sync_frame(self) -> int:
        return self._right_sync

    def sync_offset(self) -> int:
        return self._sync_delta

    def left_fps(self) -> float:
        return self._left_fps

    def right_fps(self) -> float:
        return self._right_fps

    def frame_width(self) -> int:
        return self._width

    def frame_height(self) -> int:
        return self._height

    def left_video_frame_count(self) -> int:
        return self._left_fc

    def right_video_frame_count(self) -> int:
        return self._right_fc

    @Slot("QPointF", "QPointF", "QPointF", "QPointF")
    def measure_segment(self, left_a: QPointF, left_b: QPointF, right_a: QPointF, right_b: QPointF):
        if not self._calib_ok:
            self.error.emit("Calibration non chargee")
            return
        self._ensure_aquameasure_import()
        try:
            from aquameasure import _ensure_stereo_rect_cache, _triangulate_rect_pixels
        except ImportError as exc:
            self.error.emit(f"Import aquameasure echoue : {exc}")
            return

        _, base = self._calib_paths()
        mtx1 = np.load(base / "mtx1.npy")
        dist1 = np.load(base / "dist1.npy")
        mtx2 = np.load(base / "mtx2.npy")
        dist2 = np.load(base / "dist2.npy")
        R = np.load(base / "R.npy")
        T = np.load(base / "T.npy")
        img_sz = (self._width, self._height)
        cache = _ensure_stereo_rect_cache(
            mtx1, dist1, mtx2, dist2, R, T, img_sz, self._tri_cache
        )
        P1, P2 = cache["P1"], cache["P2"]

        def pt(qp: QPointF) -> tuple[float, float]:
            return float(qp.x()), float(qp.y())

        p3d_a = _triangulate_rect_pixels(pt(left_a), pt(right_a), P1, P2)
        p3d_b = _triangulate_rect_pixels(pt(left_b), pt(right_b), P1, P2)
        dist = float(np.linalg.norm(p3d_b - p3d_a))
        self.distanceMm.emit(dist)
        self.logLine.emit(f"Distance A→B : {dist:.1f} mm")

    def _stereo_cache(self) -> dict | None:
        if not self._calib_ok:
            return None
        if self._rect_maps is not None:
            return self._rect_maps
        self._ensure_aquameasure_import()
        from aquameasure import _ensure_stereo_rect_cache

        _, base = self._calib_paths()
        mtx1 = np.load(base / "mtx1.npy")
        dist1 = np.load(base / "dist1.npy")
        mtx2 = np.load(base / "mtx2.npy")
        dist2 = np.load(base / "dist2.npy")
        R = np.load(base / "R.npy")
        T = np.load(base / "T.npy")
        img_sz = (self._width, self._height)
        self._rect_maps = _ensure_stereo_rect_cache(
            mtx1, dist1, mtx2, dist2, R, T, img_sz, self._tri_cache
        )
        return self._rect_maps

    def get_rectified_pair(self, frame_index: int):
        """Retourne (rect_l, rect_r, P1, P2) ou (None, None, None, None).

        La paire rectifiée est mise en cache pour la dernière frame demandée :
        l'affichage, la projection droite et la détection IA partagent ainsi
        un unique seek+read+remap (comme aquameasure.py qui réutilise la frame
        déjà en mémoire).
        """
        if not self._left_cap or not self._right_cap:
            return None, None, None, None
        cached = self._rect_cache.get(frame_index)
        if cached is not None:
            self._rect_cache.move_to_end(frame_index)
            rect_l, rect_r, P1, P2 = cached
            return rect_l, rect_r, P1, P2
        cache = self._stereo_cache()
        if cache is None:
            return None, None, None, None
        raw_l = self._left_cap.read(self.left_abs_frame(frame_index))
        raw_r = self._right_cap.read(self.right_abs_frame(frame_index))
        if raw_l is None or raw_r is None:
            return None, None, None, None
        rect_l = cv2.remap(raw_l, cache["map1x"], cache["map1y"], cv2.INTER_LINEAR)
        rect_r = cv2.remap(raw_r, cache["map2x"], cache["map2y"], cv2.INTER_LINEAR)
        self._store_rect(frame_index, rect_l, rect_r, cache["P1"], cache["P2"])
        return rect_l, rect_r, cache["P1"], cache["P2"]

    def get_raw_pair(self, frame_index: int):
        """Retourne la paire brute, y compris quand aucune calibration n'existe."""
        if not self._left_cap or not self._right_cap:
            return None, None
        return (
            self._left_cap.read(self.left_abs_frame(frame_index)),
            self._right_cap.read(self.right_abs_frame(frame_index)),
        )

    def _store_rect(self, frame_index: int, rect_l, rect_r, P1, P2) -> None:
        self._rect_cache[frame_index] = (rect_l, rect_r, P1, P2)
        self._rect_cache.move_to_end(frame_index)
        while len(self._rect_cache) > _RECT_CACHE_FRAMES:
            self._rect_cache.popitem(last=False)

    def left_rect_maps(self):
        """Cartes de rectification gauche, ou None si la calibration manque.

        Exposées pour que le tracking, exécuté dans son propre thread, puisse
        rectifier ses frames sans repasser par le cache de paire du service.
        """
        cache = self._stereo_cache()
        if cache is None:
            return None
        return cache["map1x"], cache["map1y"]

    def right_rect_maps(self):
        """Cartes de rectification droite, ou None si la calibration manque.

        Meme role que `left_rect_maps` pour la vue droite : le recalage de
        l'overlay pendant la lecture s'applique aux deux vues, chacune ayant
        sa propre carte.
        """
        cache = self._stereo_cache()
        if cache is None:
            return None
        return cache["map2x"], cache["map2y"]

    def calibration_loaded(self) -> bool:
        return self._calib_ok
