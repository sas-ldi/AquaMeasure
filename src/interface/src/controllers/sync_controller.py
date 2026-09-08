from __future__ import annotations

from PySide6.QtCore import Property, QObject, QTimer, Qt, Signal, Slot
from PySide6.QtWidgets import QFileDialog

from src.backend.sync_service import SyncService
from src.backend.video_probe import probe_video
from src.util import npy_io, paths
from src.util.log_model import LogModel


class SyncController(QObject):
    stepChanged = Signal()
    progressChanged = Signal()
    busyChanged = Signal()
    leftVideoChanged = Signal()
    rightVideoChanged = Signal()
    syncOffsetChanged = Signal()
    previewUpdated = Signal(str)
    curvesChanged = Signal()
    leftMetaChanged = Signal()
    rightMetaChanged = Signal()
    leftCurrentFrameChanged = Signal()
    leftInFrameChanged = Signal()
    leftOutFrameChanged = Signal()
    leftPinFrameChanged = Signal()
    leftPlayingChanged = Signal()
    rightCurrentFrameChanged = Signal()
    rightInFrameChanged = Signal()
    rightOutFrameChanged = Signal()
    rightPinFrameChanged = Signal()
    rightPlayingChanged = Signal()
    videosReadyChanged = Signal()
    bothVideosSelectedChanged = Signal()
    detectWindowSChanged = Signal()
    roiModeChanged = Signal()
    leftRoiChanged = Signal()
    rightRoiChanged = Signal()
    trimDirtyChanged = Signal()
    syncDirtyChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._logs = LogModel(self)
        self._service = SyncService(self)
        self._service.logLine.connect(self._logs.append, Qt.ConnectionType.QueuedConnection)
        self._service.progress.connect(self._on_progress)
        self._service.finished.connect(self._on_finished)
        self._service.error.connect(self._on_sync_error)
        self._service.syncResult.connect(self._on_sync_result)
        self._service.previewImageReady.connect(self._on_preview)

        self._images = None
        self._step = 0
        self._progress = 0
        self._busy = False
        self._left = ""
        self._right = ""
        self._sync_offset = 0
        self._left_fc = 0
        self._left_fps = 30.0
        self._left_w = 0
        self._left_h = 0
        self._left_cur = -1
        self._left_in = 0
        self._left_out = 0
        self._left_pin = 0
        self._left_playing = False
        self._right_fc = 0
        self._right_fps = 30.0
        self._right_w = 0
        self._right_h = 0
        self._right_cur = -1
        self._right_in = 0
        self._right_out = 0
        self._right_pin = 0
        self._right_playing = False
        self._curves_available = False
        self._curves_tick = 0
        self._detect_window_s = 5.0
        self._roi_mode = False
        self._left_roi: tuple[float, float, float, float] | None = None
        self._right_roi: tuple[float, float, float, float] | None = None
        # Derniers reglages reellement ecrits sur disque : c'est la reference
        # qui permet de dire << modifie, pas encore enregistre >>.
        self._saved_trim: tuple[int, int, int, int] | None = None
        self._trim_dirty = False
        self._sync_dirty = False

        for signal in (
            self.leftInFrameChanged,
            self.leftOutFrameChanged,
            self.rightInFrameChanged,
            self.rightOutFrameChanged,
        ):
            signal.connect(self._refresh_trim_dirty)
        for signal in (
            self.leftCurrentFrameChanged,
            self.rightCurrentFrameChanged,
            self.syncOffsetChanged,
            self.stepChanged,
        ):
            signal.connect(self._refresh_sync_dirty)

        QTimer.singleShot(0, self.reloadSavedVideos)

    def set_image_provider(self, provider):
        self._images = provider
        self._load_saved_curves()

    # ── Reglages modifies mais pas encore enregistres ────────────────────

    def _current_trim(self) -> tuple[int, int, int, int]:
        return (self._left_in, self._left_out, self._right_in, self._right_out)

    def _refresh_trim_dirty(self) -> None:
        """Les poignees In/Out ont bouge depuis le dernier enregistrement."""
        if self._left_fc <= 0 or self._right_fc <= 0:
            dirty = False
        elif self._saved_trim is None:
            # Rien n'a jamais ete enregistre : seule une decoupe reelle compte,
            # sinon le bouton clignoterait des l'ouverture d'une paire.
            dirty = self._current_trim() != (
                0, max(0, self._left_fc - 1), 0, max(0, self._right_fc - 1)
            )
        else:
            dirty = self._current_trim() != self._saved_trim
        if dirty != self._trim_dirty:
            self._trim_dirty = dirty
            self.trimDirtyChanged.emit()

    def _refresh_sync_dirty(self) -> None:
        """Les deux vues ne sont plus sur le decalage enregistre.

        On compare le decalage qu'appliquerait << Appliquer sync >> maintenant
        au decalage deja retenu : avancer les deux vues du meme nombre
        d'images ne change rien, seul un decalage relatif compte.
        """
        dirty = bool(
            self._step >= 2
            and self._left_cur >= 0
            and self._right_cur >= 0
            and (self._right_cur - self._left_cur) != self._sync_offset
        )
        if dirty != self._sync_dirty:
            self._sync_dirty = dirty
            self.syncDirtyChanged.emit()

    @Property(bool, notify=trimDirtyChanged)
    def trimDirty(self):
        return self._trim_dirty

    @Property(bool, notify=syncDirtyChanged)
    def syncDirty(self):
        return self._sync_dirty

    @Property(int, notify=syncDirtyChanged)
    def pendingSyncOffset(self):
        """Decalage qu'appliquerait << Appliquer sync >> avec les vues actuelles."""
        if self._left_cur < 0 or self._right_cur < 0:
            return self._sync_offset
        return self._right_cur - self._left_cur

    @Property(int, notify=stepChanged)
    def step(self):
        return self._step

    @Property(int, notify=progressChanged)
    def progress(self):
        return self._progress

    @Property(bool, notify=busyChanged)
    def busy(self):
        return self._busy

    @Property(str, notify=leftVideoChanged)
    def leftVideo(self):
        return self._left

    @Property(str, notify=rightVideoChanged)
    def rightVideo(self):
        return self._right

    @Property(int, notify=syncOffsetChanged)
    def syncOffset(self):
        return self._sync_offset

    @Property(QObject, constant=True)
    def logs(self):
        return self._logs

    @Property(int, notify=leftMetaChanged)
    def leftFrameCount(self):
        return self._left_fc

    @Property(float, notify=leftMetaChanged)
    def leftFps(self):
        return self._left_fps

    @Property(int, notify=leftMetaChanged)
    def leftVideoWidth(self):
        return self._left_w

    @Property(int, notify=leftMetaChanged)
    def leftVideoHeight(self):
        return self._left_h

    @Property(int, notify=leftCurrentFrameChanged)
    def leftCurrentFrame(self):
        return self._left_cur

    @Property(int, notify=leftInFrameChanged)
    def leftInFrame(self):
        return self._left_in

    @Property(int, notify=leftOutFrameChanged)
    def leftOutFrame(self):
        return self._left_out

    @Property(int, notify=leftPinFrameChanged)
    def leftPinFrame(self):
        return self._left_pin

    @Property(bool, notify=leftPlayingChanged)
    def leftPlaying(self):
        return self._left_playing

    @Property(int, notify=rightMetaChanged)
    def rightFrameCount(self):
        return self._right_fc

    @Property(float, notify=rightMetaChanged)
    def rightFps(self):
        return self._right_fps

    @Property(int, notify=rightMetaChanged)
    def rightVideoWidth(self):
        return self._right_w

    @Property(int, notify=rightMetaChanged)
    def rightVideoHeight(self):
        return self._right_h

    @Property(int, notify=rightCurrentFrameChanged)
    def rightCurrentFrame(self):
        return self._right_cur

    @Property(int, notify=rightInFrameChanged)
    def rightInFrame(self):
        return self._right_in

    @Property(int, notify=rightOutFrameChanged)
    def rightOutFrame(self):
        return self._right_out

    @Property(int, notify=rightPinFrameChanged)
    def rightPinFrame(self):
        return self._right_pin

    @Property(bool, notify=rightPlayingChanged)
    def rightPlaying(self):
        return self._right_playing

    @Property(bool, notify=videosReadyChanged)
    def videosReady(self):
        return self._left_fc > 0 and self._right_fc > 0

    @Property(bool, notify=bothVideosSelectedChanged)
    def bothVideosSelected(self):
        return bool(self._left and self._right)

    @Property(float, notify=detectWindowSChanged)
    def detectWindowS(self):
        return self._detect_window_s

    @detectWindowS.setter  # type: ignore[misc]
    def detectWindowS(self, value: float):
        self.setDetectWindowS(value)

    @Slot(float)
    def setDetectWindowS(self, value: float):
        v = max(0.5, min(120.0, float(value)))
        if abs(self._detect_window_s - v) > 1e-9:
            self._detect_window_s = v
            self.detectWindowSChanged.emit()

    @Property(bool, notify=roiModeChanged)
    def roiModeEnabled(self):
        return self._roi_mode

    @Property("QVariantList", notify=leftRoiChanged)
    def leftRoi(self):
        if self._left_roi is None:
            return []
        return list(self._left_roi)

    @Property("QVariantList", notify=rightRoiChanged)
    def rightRoi(self):
        if self._right_roi is None:
            return []
        return list(self._right_roi)

    def _pack_roi(self, roi: tuple[float, float, float, float] | None):
        if roi is None:
            return None
        x, y, w, h = roi
        x = max(0.0, min(1.0, float(x)))
        y = max(0.0, min(1.0, float(y)))
        w = max(0.0, min(1.0 - x, float(w)))
        h = max(0.0, min(1.0 - y, float(h)))
        if w < 0.01 or h < 0.01:
            return None
        return (x, y, w, h)

    def _save_rois(self) -> None:
        try:
            from aquameasure import save_flash_roi

            save_flash_roi(self._left_roi, self._right_roi)
        except Exception as exc:  # noqa: BLE001
            self._logs.append(f"[!] Sauvegarde ROI flash : {exc}")

    def _load_persisted_roi(self) -> None:
        try:
            from aquameasure import load_flash_roi

            left_roi, right_roi = load_flash_roi()
        except Exception:
            return
        if left_roi is None and right_roi is None:
            return
        self._left_roi = self._pack_roi(left_roi)
        self._right_roi = self._pack_roi(right_roi)
        self.leftRoiChanged.emit()
        self.rightRoiChanged.emit()
        if self._left_roi or self._right_roi:
            self._roi_mode = True
            self.roiModeChanged.emit()
            self._logs.append("Cadres de detection flash restaures depuis le disque.")

    @Slot(bool)
    def setRoiMode(self, on: bool):
        on = bool(on)
        if self._roi_mode == on:
            return
        self._roi_mode = on
        self.roiModeChanged.emit()
        if on:
            self._logs.append(
                "Mode cadre active - tracez un rectangle autour du flash sur G et D."
            )

    @Slot()
    def clearFlashRois(self):
        self._left_roi = None
        self._right_roi = None
        self.leftRoiChanged.emit()
        self.rightRoiChanged.emit()
        try:
            from aquameasure import clear_flash_roi

            clear_flash_roi()
        except Exception:
            pass
        self._logs.append("Cadres de detection effaces - image entiere utilisee.")

    @Slot(float, float, float, float)
    def setLeftRoi(self, x: float, y: float, w: float, h: float):
        roi = self._pack_roi((x, y, w, h))
        if self._left_roi == roi:
            return
        self._left_roi = roi
        self.leftRoiChanged.emit()
        self._save_rois()

    @Slot(float, float, float, float)
    def setRightRoi(self, x: float, y: float, w: float, h: float):
        roi = self._pack_roi((x, y, w, h))
        if self._right_roi == roi:
            return
        self._right_roi = roi
        self.rightRoiChanged.emit()
        self._save_rois()

    @Slot("QVariantList")
    def setLeftRoiFromList(self, roi):
        if not roi or len(roi) < 4:
            self.setLeftRoi(0, 0, 0, 0)
            return
        self.setLeftRoi(float(roi[0]), float(roi[1]), float(roi[2]), float(roi[3]))

    @Slot("QVariantList")
    def setRightRoiFromList(self, roi):
        if not roi or len(roi) < 4:
            self.setRightRoi(0, 0, 0, 0)
            return
        self.setRightRoi(float(roi[0]), float(roi[1]), float(roi[2]), float(roi[3]))

    def _set_step(self, s: int):
        if self._step != s:
            self._step = s
            self.stepChanged.emit()

    def _on_progress(self, p: int):
        self._progress = p
        self.progressChanged.emit()

    def _on_finished(self):
        self._busy = False
        self.busyChanged.emit()

    def _on_sync_error(self, msg: str):
        self._logs.append(msg)
        self._on_finished()

    def _on_sync_result(self, lf: int, rf: int, offset: int):
        self._sync_offset = offset
        self.syncOffsetChanged.emit()
        self.setLeftPin(lf)
        self.setRightPin(rf)
        self.seekLeft(lf)
        self.seekRight(rf)
        self._set_step(2)

    def _on_preview(self, image_id: str, qimg):
        if self._images:
            self._images.set_image(image_id, qimg)
        if image_id == "sync_curves":
            self._curves_available = True
            self._curves_tick += 1
            self.curvesChanged.emit()
        self.previewUpdated.emit(image_id)

    def _load_saved_curves(self):
        png = paths.cam_param("sync_curves.png")
        if not png.is_file() or self._images is None:
            return
        from PySide6.QtGui import QImage

        img = QImage(str(png))
        if img.isNull():
            return
        if img.format() != QImage.Format_RGB32:
            img = img.convertToFormat(QImage.Format_RGB32)
        self._images.set_image("sync_curves", img)
        self._curves_available = True
        self._curves_tick += 1
        self.curvesChanged.emit()

    @Property(bool, notify=curvesChanged)
    def curvesAvailable(self):
        return self._curves_available

    @Property(int, notify=curvesChanged)
    def curvesTick(self):
        return self._curves_tick

    def _refresh_left_meta(self):
        meta = probe_video(self._left)
        if not meta.valid and self._left:
            self._logs.append(f"[!] Impossible d'ouvrir la video gauche : {self._left}")
        self._left_fc = meta.frame_count
        self._left_fps = meta.fps
        self._left_w = meta.width
        self._left_h = meta.height
        self._left_in = 0
        self._left_out = max(0, meta.frame_count - 1)
        self._left_pin = 0
        self._left_cur = 0 if meta.frame_count > 0 else -1
        self.leftMetaChanged.emit()
        self.leftCurrentFrameChanged.emit()
        self.leftInFrameChanged.emit()
        self.leftOutFrameChanged.emit()
        self.leftPinFrameChanged.emit()
        self.videosReadyChanged.emit()

    def _refresh_right_meta(self):
        meta = probe_video(self._right)
        if not meta.valid and self._right:
            self._logs.append(f"[!] Impossible d'ouvrir la video droite : {self._right}")
        self._right_fc = meta.frame_count
        self._right_fps = meta.fps
        self._right_w = meta.width
        self._right_h = meta.height
        self._right_in = 0
        self._right_out = max(0, meta.frame_count - 1)
        self._right_pin = 0
        self._right_cur = 0 if meta.frame_count > 0 else -1
        self.rightMetaChanged.emit()
        self.rightCurrentFrameChanged.emit()
        self.rightInFrameChanged.emit()
        self.rightOutFrameChanged.emit()
        self.rightPinFrameChanged.emit()
        self.videosReadyChanged.emit()
        if meta.valid:
            self._load_persisted_trim()
            self._load_persisted_roi()

    def _load_persisted_trim(self):
        self._saved_trim = None
        trim = paths.cam_param("trim_frames.npy")
        if trim.is_file():
            try:
                t = npy_io.load_int1d(str(trim))
                if len(t) >= 4:
                    if self._left_fc > 0:
                        self._left_in = max(0, min(t[0], self._left_fc - 1))
                        self._left_out = (
                            max(0, min(t[1], self._left_fc - 1)) if t[1] > 0 else self._left_fc - 1
                        )
                    if self._right_fc > 0:
                        self._right_in = max(0, min(t[2], self._right_fc - 1))
                        self._right_out = (
                            max(0, min(t[3], self._right_fc - 1))
                            if t[3] > 0
                            else self._right_fc - 1
                        )
                    self._saved_trim = self._current_trim()
                    self.leftInFrameChanged.emit()
                    self.leftOutFrameChanged.emit()
                    self.rightInFrameChanged.emit()
                    self.rightOutFrameChanged.emit()
            except OSError:
                pass
        sync = paths.cam_param("sync_frames.npy")
        if sync.is_file():
            try:
                s = npy_io.load_int1d(str(sync))
                if len(s) >= 2:
                    lf = int(s[0])
                    rf = int(s[1])
                    self._sync_offset = rf - lf
                    self.syncOffsetChanged.emit()
                    self.setLeftPin(lf)
                    self.setRightPin(rf)
                    if self._left_fc > 0:
                        self.seekLeft(max(0, min(lf, self._left_fc - 1)))
                    if self._right_fc > 0:
                        self.seekRight(max(0, min(rf, self._right_fc - 1)))
                    self._set_step(2)
            except OSError:
                pass
        self._refresh_trim_dirty()
        self._refresh_sync_dirty()

    @Slot()
    def pickLeftVideo(self):
        path, _ = QFileDialog.getOpenFileName(
            None, "Video gauche", "", "Videos (*.mp4 *.avi *.mov *.mkv);;Tous (*.*)"
        )
        if path:
            self._left = path
            self.leftVideoChanged.emit()
            self.bothVideosSelectedChanged.emit()
            self._refresh_left_meta()
            if self._left and self._right:
                self.saveCurrentVideos()

    @Slot()
    def pickRightVideo(self):
        path, _ = QFileDialog.getOpenFileName(
            None, "Video droite", "", "Videos (*.mp4 *.avi *.mov *.mkv);;Tous (*.*)"
        )
        if path:
            self._right = path
            self.rightVideoChanged.emit()
            self.bothVideosSelectedChanged.emit()
            self._refresh_right_meta()
            if self._left and self._right:
                self.saveCurrentVideos()

    @Slot()
    def saveCurrentVideos(self):
        if self._left and self._right:
            self._service.save_videos_list(self._left, self._right)

    @Slot()
    def reloadSavedVideos(self):
        p = paths.cam_param("videos.txt")
        if not p.is_file():
            return
        lines = p.read_text(encoding="utf-8").splitlines()
        if lines and lines[0].strip():
            self._left = lines[0].strip()
            self.leftVideoChanged.emit()
            self._refresh_left_meta()
        if len(lines) > 1 and lines[1].strip():
            self._right = lines[1].strip()
            self.rightVideoChanged.emit()
            self._refresh_right_meta()
        self.bothVideosSelectedChanged.emit()
        if self._left and self._right:
            self._load_persisted_roi()

    @Slot(float, float, float)
    def runDetect(self, left_center_s: float, right_center_s: float, window_s: float):
        if not self._left or not self._right:
            self._logs.append("[!] Selectionnez les deux videos")
            return
        self._busy = True
        self.busyChanged.emit()
        self._progress = 0
        self.progressChanged.emit()
        self._set_step(1)
        left_roi = self._left_roi if self._roi_mode else None
        right_roi = self._right_roi if self._roi_mode else None
        if self._roi_mode:
            if left_roi is None:
                self._logs.append("[!] Cadre gauche manquant - image entiere utilisee.")
            if right_roi is None:
                self._logs.append("[!] Cadre droite manquant - image entiere utilisee.")
        self._service.run_sync(
            self._left,
            self._right,
            left_center_s,
            right_center_s,
            window_s,
            left_roi=left_roi,
            right_roi=right_roi,
        )

    @Slot()
    def runDetectFromUi(self):
        lfps = max(self._left_fps, 1e-6)
        rfps = max(self._right_fps, 1e-6)
        lc = self._left_pin / lfps
        rc = self._right_pin / rfps
        self.runDetect(lc, rc, self._detect_window_s)

    @Slot(int, int)
    def applyManual(self, left_frame: int, right_frame: int):
        if self._left_fc <= 0 or self._right_fc <= 0:
            self._logs.append("[!] Videos non pretes - attendez le chargement.")
            return
        left_frame = max(0, min(int(left_frame), self._left_fc - 1))
        right_frame = max(0, min(int(right_frame), self._right_fc - 1))
        paths.ensure_camera_params_dir()
        npy_io.save_int1d(str(paths.cam_param("sync_frames.npy")), [left_frame, right_frame])
        if self._left and self._right:
            self._service.save_videos_list(self._left, self._right)
        self._sync_offset = right_frame - left_frame
        self.syncOffsetChanged.emit()
        self.setLeftPin(left_frame)
        self.setRightPin(right_frame)
        self.seekLeft(left_frame)
        self.seekRight(right_frame)
        self._set_step(2)
        self._logs.append(
            f"Sync manuelle : offset {self._sync_offset:+d} frames "
            f"(G f{left_frame}, D f{right_frame})"
        )

    @Slot()
    def applyManualFromCurrent(self):
        if self._left_fc <= 0 or self._right_fc <= 0:
            self._logs.append("[!] Videos non pretes - attendez le chargement.")
            return
        left_frame = self._left_cur if self._left_cur >= 0 else 0
        right_frame = self._right_cur if self._right_cur >= 0 else 0
        self.applyManual(left_frame, right_frame)

    @Slot()
    def saveTrim(self):
        self._service.save_trim(self._left_in, self._left_out, self._right_in, self._right_out)
        self._saved_trim = self._current_trim()
        self._refresh_trim_dirty()

    @Slot(int)
    def seekLeft(self, frame: int):
        if self._left_fc <= 0:
            return
        frame = max(0, min(frame, self._left_fc - 1))
        if self._left_cur != frame:
            self._left_cur = frame
            self.leftCurrentFrameChanged.emit()

    @Slot(int)
    def seekRight(self, frame: int):
        if self._right_fc <= 0:
            return
        frame = max(0, min(frame, self._right_fc - 1))
        if self._right_cur != frame:
            self._right_cur = frame
            self.rightCurrentFrameChanged.emit()

    @Slot(int)
    def stepLeft(self, delta: int):
        self.seekLeft(self._left_cur + delta)

    @Slot(int)
    def stepRight(self, delta: int):
        self.seekRight(self._right_cur + delta)

    @Slot(int)
    def setLeftIn(self, frame: int):
        self._left_in = frame
        self.leftInFrameChanged.emit()

    @Slot(int)
    def setLeftOut(self, frame: int):
        self._left_out = frame
        self.leftOutFrameChanged.emit()

    @Slot(int)
    def setLeftPin(self, frame: int):
        self._left_pin = frame
        self.leftPinFrameChanged.emit()

    @Slot(int)
    def setRightIn(self, frame: int):
        self._right_in = frame
        self.rightInFrameChanged.emit()

    @Slot(int)
    def setRightOut(self, frame: int):
        self._right_out = frame
        self.rightOutFrameChanged.emit()

    @Slot(int)
    def setRightPin(self, frame: int):
        self._right_pin = frame
        self.rightPinFrameChanged.emit()

    @Slot()
    def toggleLeftPlay(self):
        self._left_playing = not self._left_playing
        self.leftPlayingChanged.emit()

    @Slot()
    def toggleRightPlay(self):
        self._right_playing = not self._right_playing
        self.rightPlayingChanged.emit()

    @Slot()
    def setLeftInAtCurrent(self):
        self.setLeftIn(self._left_cur)

    @Slot()
    def setLeftOutAtCurrent(self):
        self.setLeftOut(self._left_cur)

    @Slot()
    def setRightInAtCurrent(self):
        self.setRightIn(self._right_cur)

    @Slot()
    def setRightOutAtCurrent(self):
        self.setRightOut(self._right_cur)

    @Slot()
    def setLeftPinAtCurrent(self):
        self.setLeftPin(self._left_cur)

    @Slot()
    def setRightPinAtCurrent(self):
        self.setRightPin(self._right_cur)

    @Slot(int, float, result=str)
    def formatTimecode(self, frame: int, fps: float) -> str:
        if fps <= 0:
            fps = 30.0
        t = frame / fps
        m = int(t // 60)
        s = t - m * 60
        return f"{m:02d}:{s:05.2f}"
