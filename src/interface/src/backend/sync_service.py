from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np
from PySide6.QtCore import QObject, QThread, Qt, Signal
from PySide6.QtGui import QImage

from src.imaging.qimage_util import bgr_array_to_qimage
from src.util import npy_io, paths


class SyncWorker(QThread):
    logLine = Signal(str)
    progress = Signal(int)
    finished_ok = Signal()
    error = Signal(str)
    syncResult = Signal(int, int, int)
    previewImageReady = Signal(str, QImage)

    def __init__(
        self,
        left_video: str,
        right_video: str,
        left_center_s: float,
        right_center_s: float,
        window_s: float,
        left_roi=None,
        right_roi=None,
        parent=None,
    ):
        super().__init__(parent)
        self._left = left_video
        self._right = right_video
        self._lc = left_center_s
        self._rc = right_center_s
        self._window = window_s
        self._left_roi = left_roi
        self._right_roi = right_roi

    def run(self):
        self.progress.emit(1)
        root = str(paths.app_root())
        if root not in sys.path:
            sys.path.insert(0, root)
        try:
            from aquameasure import _draw_brightness_curves, detect_flash_frame

            paths.bind_engine_root()
        except ImportError as exc:
            self.error.emit(f"Import aquameasure : {exc}")
            self.finished_ok.emit()
            return

        try:
            self.progress.emit(5)
            self.logLine.emit("Analyse flash - camera gauche…")
            lf, idx_l, b_l = detect_flash_frame(
                self._left,
                search_center_s=self._lc,
                window_s=self._window,
                roi=self._left_roi,
                log_fn=self.logLine.emit,
                progress_fn=self.progress.emit,
                progress_start=5,
                progress_end=45,
            )
            self.progress.emit(45)
            self.logLine.emit("Analyse flash - camera droite…")
            rf, idx_r, b_r = detect_flash_frame(
                self._right,
                search_center_s=self._rc,
                window_s=self._window,
                roi=self._right_roi,
                log_fn=self.logLine.emit,
                progress_fn=self.progress.emit,
                progress_start=45,
                progress_end=88,
            )
            offset = rf - lf
            self.progress.emit(88)
            paths.ensure_camera_params_dir()
            npy_io.save_int1d(str(paths.cam_param("sync_frames.npy")), [lf, rf])
            try:
                curves = _draw_brightness_curves(idx_l, b_l, idx_r, b_r, lf, rf)
                if curves is not None:
                    qimg = bgr_array_to_qimage(curves)
                    if not qimg.isNull():
                        self.previewImageReady.emit("sync_curves", qimg)
                    out = paths.cam_param("sync_curves.png")
                    paths.ensure_camera_params_dir()
                    cv2.imwrite(str(out), curves)
            except Exception as exc:
                self.logLine.emit(f"[!] Courbes luminosite : {exc}")
            self.progress.emit(100)
            self.syncResult.emit(lf, rf, offset)
            self.logLine.emit(
                f"Sync OK - flash G f{lf}, D f{rf}, offset {offset:+d} frames"
            )
            self.finished_ok.emit()
        except Exception as exc:  # noqa: BLE001
            self.error.emit(f"[!] Sync : {exc}")
            self.finished_ok.emit()


class SyncService(QObject):
    logLine = Signal(str)
    progress = Signal(int)
    finished = Signal()
    error = Signal(str)
    syncResult = Signal(int, int, int)
    previewImageReady = Signal(str, QImage)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._worker: SyncWorker | None = None

    def save_videos_list(self, left: str, right: str) -> None:
        paths.ensure_camera_params_dir()
        p = paths.cam_param("videos.txt")
        p.write_text(f"{left}\n{right}\n", encoding="utf-8")

    def run_sync(
        self,
        left_video: str,
        right_video: str,
        left_center_s: float,
        right_center_s: float,
        window_s: float,
        left_roi=None,
        right_roi=None,
    ) -> None:
        if self._worker and self._worker.isRunning():
            self.error.emit("[!] Sync deja en cours")
            return
        self._worker = SyncWorker(
            left_video,
            right_video,
            left_center_s,
            right_center_s,
            window_s,
            left_roi=left_roi,
            right_roi=right_roi,
        )
        self._worker.logLine.connect(self.logLine, Qt.ConnectionType.QueuedConnection)
        self._worker.progress.connect(self.progress, Qt.ConnectionType.QueuedConnection)
        self._worker.error.connect(self.error, Qt.ConnectionType.QueuedConnection)
        self._worker.syncResult.connect(self.syncResult, Qt.ConnectionType.QueuedConnection)
        self._worker.previewImageReady.connect(
            self.previewImageReady, Qt.ConnectionType.QueuedConnection
        )
        self._worker.finished_ok.connect(self.finished, Qt.ConnectionType.QueuedConnection)
        self._worker.start()

    def save_trim(
        self,
        left_in: int,
        left_out: int,
        right_in: int,
        right_out: int,
    ) -> None:
        paths.ensure_camera_params_dir()
        npy_io.save_int1d(
            str(paths.cam_param("trim_frames.npy")),
            [left_in, left_out, right_in, right_out],
        )
        self.logLine.emit("Limites In/Out enregistrees")
