from __future__ import annotations

import sys
from dataclasses import dataclass

from PySide6.QtCore import QObject, QThread, Qt, Signal
from PySide6.QtGui import QImage

from src.imaging.qimage_util import bgr_array_to_qimage
from src.util import paths


class _CallbackSignal:
    """Pont emit() PyQt6 → callback Python (PySide6)."""

    def __init__(self, callback):
        self._callback = callback

    def emit(self, *args):
        self._callback(*args)

    def connect(self, _slot):
        return None


@dataclass
class CalibJobParams:
    left_video: str
    right_video: str
    squares_x: int
    squares_y: int
    square_length_mm: float
    marker_length_mm: float
    aruco_dict_name: str
    max_intrinsic_views: int
    max_stereo_pairs: int
    scan_stride: int
    dense_window: int
    dense_stride: int = 1
    calib_camera_scale: float = 1.0


class CalibWorker(QThread):
    logLine = Signal(str)
    progress = Signal(int)
    phaseChanged = Signal(str)
    liveStatus = Signal(str)
    opencvBusy = Signal(bool)
    scanCountsUpdated = Signal(int, int, int)
    stereoReady = Signal(object)
    verifyReady = Signal(object)
    previewImageReady = Signal(str, QImage, str)
    suggestConfig = Signal(int, int)
    finished_ok = Signal()
    error = Signal(str)

    def __init__(self, params: CalibJobParams, parent=None):
        super().__init__(parent)
        self._params = params
        self._cancelled = False
        self._worker = None

    def cancel(self):
        self._cancelled = True
        if self._worker is not None:
            self._worker._cancelled = True

    def _wire_worker(self, worker) -> None:
        worker.log = _CallbackSignal(self.logLine.emit)
        worker.progress = _CallbackSignal(self.progress.emit)
        worker.error = _CallbackSignal(self._on_worker_error)
        worker.phase = _CallbackSignal(self.phaseChanged.emit)
        worker.live_status = _CallbackSignal(self.liveStatus.emit)
        worker.opencv_busy = _CallbackSignal(self.opencvBusy.emit)
        worker.scan_counts = _CallbackSignal(
            lambda left, right, pairs: self.scanCountsUpdated.emit(
                int(left), int(right), int(pairs)
            )
        )
        worker.stereo_ready = _CallbackSignal(self.stereoReady.emit)
        worker.verify_ready = _CallbackSignal(self.verifyReady.emit)
        worker.suggest_config = _CallbackSignal(
            lambda sx, sy: self.suggestConfig.emit(int(sx), int(sy))
        )
        worker.finished = _CallbackSignal(self._on_worker_finished)
        worker.frame_preview = _CallbackSignal(self._on_frame_preview)
        self._worker = worker

    def _on_worker_error(self, text: str) -> None:
        self.logLine.emit(text)
        self.error.emit(text)

    def _on_worker_finished(self) -> None:
        self.finished_ok.emit()

    def _on_frame_preview(self, payload) -> None:
        try:
            frame_bgr, label = payload
            qimg = bgr_array_to_qimage(frame_bgr)
            if qimg.isNull():
                return
            side = "calib_left" if "gauche" in label.lower() or "left" in label.lower() else "calib_right"
            self.previewImageReady.emit(side, qimg, label)
        except Exception as exc:  # noqa: BLE001
            self.logLine.emit(f"[!] Apercu calibration : {exc}")

    def run(self) -> None:
        root = str(paths.app_root())
        if root not in sys.path:
            sys.path.insert(0, root)
        try:
            import cv2 as cv
            from aquameasure import CHARUCO_DICT_MAP, CalibrationWorkerFast
        except ImportError as exc:
            self.error.emit(f"Import aquameasure : {exc}")
            self.finished_ok.emit()
            return

        p = self._params
        dict_id = CHARUCO_DICT_MAP.get(p.aruco_dict_name, cv.aruco.DICT_5X5_100)

        worker = CalibrationWorkerFast.__new__(CalibrationWorkerFast)
        worker.scan_stride_idle = p.scan_stride
        worker.dense_window = p.dense_window
        worker.dense_stride = p.dense_stride

        worker._cancelled = False
        worker.left_video = p.left_video
        worker.right_video = p.right_video
        worker.squares_x = p.squares_x
        worker.squares_y = p.squares_y
        worker.square_length_mm = p.square_length_mm
        worker.marker_length_mm = p.marker_length_mm
        worker.aruco_dict_id = dict_id
        worker.max_intrinsic_views = p.max_intrinsic_views
        worker.max_stereo_pairs = p.max_stereo_pairs
        worker.calib_camera_scale = p.calib_camera_scale

        self._wire_worker(worker)
        self.logLine.emit(
            f"  Scan : saut sans mire={p.scan_stride}, "
            f"rafale={p.dense_window} pas, pas rafale={p.dense_stride}"
        )
        self.logLine.emit(
            f"  Precisions : {p.max_intrinsic_views} vues/cam, "
            f"{p.max_stereo_pairs} paires stereo"
        )
        self.logLine.emit(
            f"Démarrage calibration - {p.max_intrinsic_views} vues/cam, "
            f"{p.max_stereo_pairs} paires"
        )
        try:
            worker.run()
        except Exception as exc:  # noqa: BLE001
            import traceback

            tb = traceback.format_exc()
            self.logLine.emit(f"[ERREUR]\n{tb}")
            self.error.emit(f"{exc}\n\n{tb}")
            self.finished_ok.emit()


class CalibService(QObject):
    logLine = Signal(str)
    progress = Signal(int)
    phaseChanged = Signal(str)
    liveStatus = Signal(str)
    opencvBusy = Signal(bool)
    scanCountsUpdated = Signal(int, int, int)
    stereoReady = Signal(object)
    verifyReady = Signal(object)
    previewImageReady = Signal(str, QImage, str)
    suggestConfig = Signal(int, int)
    finished = Signal()
    error = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._worker: CalibWorker | None = None

    def is_running(self) -> bool:
        return self._worker is not None and self._worker.isRunning()

    def run_calibration(self, params: CalibJobParams) -> None:
        if self.is_running():
            self.error.emit("[!] Calibration deja en cours")
            return
        paths.ensure_camera_params_dir()
        paths.cam_param("videos.txt").write_text(
            f"{params.left_video}\n{params.right_video}\n", encoding="utf-8"
        )
        self._worker = CalibWorker(params)
        w = self._worker
        w.logLine.connect(self.logLine, Qt.ConnectionType.QueuedConnection)
        w.progress.connect(self.progress, Qt.ConnectionType.QueuedConnection)
        w.phaseChanged.connect(self.phaseChanged, Qt.ConnectionType.QueuedConnection)
        w.liveStatus.connect(self.liveStatus, Qt.ConnectionType.QueuedConnection)
        w.opencvBusy.connect(self.opencvBusy, Qt.ConnectionType.QueuedConnection)
        w.scanCountsUpdated.connect(self.scanCountsUpdated, Qt.ConnectionType.QueuedConnection)
        w.stereoReady.connect(self.stereoReady, Qt.ConnectionType.QueuedConnection)
        w.verifyReady.connect(self.verifyReady, Qt.ConnectionType.QueuedConnection)
        w.previewImageReady.connect(self.previewImageReady, Qt.ConnectionType.QueuedConnection)
        w.suggestConfig.connect(self.suggestConfig, Qt.ConnectionType.QueuedConnection)
        w.error.connect(self.error, Qt.ConnectionType.QueuedConnection)
        w.finished_ok.connect(self.finished, Qt.ConnectionType.QueuedConnection)
        w.start()

    def cancel(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
