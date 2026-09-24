from __future__ import annotations

import math
from pathlib import Path

import numpy as np
from PySide6.QtCore import Property, QObject, Qt, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices, QImage
from PySide6.QtWidgets import QFileDialog

from src.backend.calib_service import CalibJobParams, CalibService
from src.backend.video_probe import probe_video
from src.controllers.settings_controller import SettingsController
from src.util import npy_io, paths
from src.util.calib_file_logger import CalibFileLogger
from src.util.log_model import CalibLogModel


def _rotation_angles(r) -> tuple[float, str]:
    """Angle total entre les deux cameras et sa decomposition par axe.

    Le total melange tout : sur un support fixe, seule la part horizontale
    doit porter l'ecartement prevu, le vertical et le roulis restent pres
    de zero. Axes OpenCV de la camera gauche : x (tangage), y (lacet),
    z (roulis).
    """
    import cv2

    rm = np.asarray(r, dtype=np.float64).reshape(3, 3)
    total = math.degrees(
        math.acos(max(-1.0, min(1.0, (float(np.trace(rm)) - 1.0) / 2.0)))
    )
    pitch, yaw, roll = cv2.RQDecomp3x3(rm)[0]
    detail = (
        f"horizontal {abs(yaw):.2f}° · vertical {abs(pitch):.2f}°"
        f" · roulis {abs(roll):.2f}°"
    )
    return float(total), detail


class CalibrationController(QObject):
    stepChanged = Signal()
    progressChanged = Signal()
    progressActiveChanged = Signal()
    workStageChanged = Signal()
    detailChanged = Signal()
    busyChanged = Signal()
    phaseChanged = Signal()
    leftVideoChanged = Signal()
    rightVideoChanged = Signal()
    bothVideosSelectedChanged = Signal()
    rmseStereoChanged = Signal()
    rmseLeftChanged = Signal()
    rmseRightChanged = Signal()
    baselineMmChanged = Signal()
    rotationDegChanged = Signal()
    focalLeftPxChanged = Signal()
    focalRightPxChanged = Signal()
    calibSummaryChanged = Signal()
    calibrationStatsChanged = Signal()
    overallQualityChanged = Signal()
    calibrationComplete = Signal()
    calibrationCancelled = Signal()
    savedCalibrationChanged = Signal()
    previewTickChanged = Signal()
    lastCalibLogPathChanged = Signal()
    syncImportChanged = Signal()
    charucoSuggestionChanged = Signal()
    lastRunErrorChanged = Signal()

    def __init__(self, settings: SettingsController, parent=None):
        super().__init__(parent)
        self._settings = settings
        self._file_logger = CalibFileLogger()
        self._logs = CalibLogModel(self._file_logger, self)
        self._last_calib_log = ""
        self._service = CalibService(self)
        self._service.logLine.connect(self._logs.append, Qt.ConnectionType.QueuedConnection)
        self._service.progress.connect(self._on_progress)
        self._service.phaseChanged.connect(self._on_phase)
        self._service.liveStatus.connect(self._on_live_status)
        self._service.opencvBusy.connect(self._on_opencv_busy)
        self._service.scanCountsUpdated.connect(self._on_scan_counts)
        self._service.stereoReady.connect(self._on_stereo_ready)
        self._service.verifyReady.connect(self._on_verify_ready)
        self._service.previewImageReady.connect(self._on_preview)
        self._service.suggestConfig.connect(self._on_suggest_config)
        self._service.error.connect(self._on_error)
        self._service.finished.connect(self._on_finished)
        self._images = None
        self._step = 0
        self._progress = 0
        self._progress_active = False
        self._work_stage = 0
        self._detail = ""
        self._busy = False
        self._phase = ""
        self._last_run_error = ""
        self._r_stamp = None
        self._left = ""
        self._right = ""
        self._rmse_stereo = -1.0
        self._rmse_left = -1.0
        self._rmse_right = -1.0
        self._baseline = -1.0
        self._rotation = -1.0
        self._rotation_detail = ""
        self._focal_l = -1.0
        self._focal_r = -1.0
        self._summary = ""
        self._det_l = -1
        self._det_r = -1
        self._pairs = -1
        self._quality_tier = 0
        self._quality_label = ""
        self._quality_hint = ""
        self._quality_color = ""
        self._quality_bg = ""
        self._preview_tick = 0
        self._left_caption = ""
        self._right_caption = ""
        self._videos_from_sync = False
        self._sync_delta = 0
        self._sync_left_frames = 0
        self._sync_right_frames = 0
        self._sync_available = False
        self._suggested_sx = 0
        self._suggested_sy = 0
        self._settings.charucoSettingsChanged.connect(self._drop_matching_suggestion)
        self._refresh_saved_state()
        self._refresh_sync_state()

    def set_image_provider(self, provider):
        self._images = provider

    @Property(int, notify=stepChanged)
    def step(self):
        return self._step

    @Property(int, notify=progressChanged)
    def progress(self):
        return self._progress

    @Property(bool, notify=progressActiveChanged)
    def progressActive(self):
        return self._progress_active

    @Property(int, notify=workStageChanged)
    def workStage(self):
        return self._work_stage

    @Property(str, notify=detailChanged)
    def detail(self):
        return self._detail

    @Property(bool, notify=busyChanged)
    def busy(self):
        return self._busy

    @Property(str, notify=phaseChanged)
    def phase(self):
        return self._phase

    @Property(str, notify=leftVideoChanged)
    def leftVideo(self):
        return self._left

    @Property(str, notify=rightVideoChanged)
    def rightVideo(self):
        return self._right

    @Property(bool, notify=bothVideosSelectedChanged)
    def bothVideosSelected(self):
        return bool(self._left and self._right)

    # ── Import « depuis Synchronisation » ────────────────────────────────
    # La paire synchronisée est écrite par l'étape Synchronisation dans
    # videos.txt (chemins) + sync_frames.npy (frame du flash de chaque
    # caméra). Le décalage est la différence entre ces deux frames.

    @Property(bool, notify=syncImportChanged)
    def syncImportAvailable(self):
        """Une paire synchronisée existe et ses deux fichiers sont lisibles."""
        return self._sync_available

    @Property(bool, notify=syncImportChanged)
    def videosFromSync(self):
        """La paire actuellement chargée est bien celle de la synchronisation."""
        return self._videos_from_sync

    @Property(str, notify=syncImportChanged)
    def syncOffsetLabel(self):
        """Décalage entre les deux caméras, en frames (« +12 fr », « 0 fr »)."""
        sign = "+" if self._sync_delta > 0 else ""
        return f"{sign}{self._sync_delta} fr"

    @Property(str, notify=syncImportChanged)
    def syncFramesLabel(self):
        """Nombre total de frames des deux vidéos (« 20000 fr » ou « G/D »)."""
        left, right = self._sync_left_frames, self._sync_right_frames
        if left <= 0 and right <= 0:
            return ""
        if left == right:
            return f"{left} fr"
        return f"{left} / {right} fr (G/D)"

    @Property(float, notify=rmseStereoChanged)
    def rmseStereo(self):
        return self._rmse_stereo

    @Property(float, notify=rmseLeftChanged)
    def rmseLeft(self):
        return self._rmse_left

    @Property(float, notify=rmseRightChanged)
    def rmseRight(self):
        return self._rmse_right

    @Property(float, notify=baselineMmChanged)
    def baselineMm(self):
        return self._baseline

    @Property(str, notify=lastRunErrorChanged)
    def lastRunError(self):
        """Dernier lancement sans nouvelle calibration : l'ancienne reste affichee."""
        return self._last_run_error

    def _r_file_stamp(self):
        r = paths.cam_param("R.npy")
        return r.stat().st_mtime_ns if r.is_file() else None

    @Property(float, notify=rotationDegChanged)
    def rotationDeg(self):
        return self._rotation

    @Property(str, notify=rotationDegChanged)
    def rotationDetail(self):
        return self._rotation_detail

    @Property(float, notify=focalLeftPxChanged)
    def focalLeftPx(self):
        return self._focal_l

    @Property(float, notify=focalRightPxChanged)
    def focalRightPx(self):
        return self._focal_r

    @Property(str, notify=calibSummaryChanged)
    def calibSummary(self):
        return self._summary

    @Property(int, notify=calibrationStatsChanged)
    def detectionsLeft(self):
        return self._det_l

    @Property(int, notify=calibrationStatsChanged)
    def detectionsRight(self):
        return self._det_r

    @Property(int, notify=calibrationStatsChanged)
    def pairsSynced(self):
        return self._pairs

    @Property(str, notify=calibrationStatsChanged)
    def scanCountsLabel(self):
        if self._det_l < 0:
            return ""
        if self._pairs >= 0:
            return (
                f"{self._det_l} détections G · {self._det_r} D · "
                f"{self._pairs} paires trouvées"
            )
        return f"{self._det_l} détections G · {self._det_r} D"

    @Property(int, notify=overallQualityChanged)
    def overallQualityTier(self):
        return self._quality_tier

    @Property(str, notify=overallQualityChanged)
    def overallQualityLabel(self):
        return self._quality_label

    @Property(str, notify=overallQualityChanged)
    def overallQualityHint(self):
        return self._quality_hint

    @Property(str, notify=overallQualityChanged)
    def overallQualityColor(self):
        return self._quality_color

    @Property(str, notify=overallQualityChanged)
    def overallQualityBg(self):
        return self._quality_bg

    @Property(bool, notify=savedCalibrationChanged)
    def hasSavedCalibration(self):
        return paths.calibration_exists()

    @Property(int, notify=previewTickChanged)
    def previewTick(self):
        return self._preview_tick

    @Property(str, notify=previewTickChanged)
    def leftPreviewCaption(self):
        return self._left_caption

    @Property(str, notify=previewTickChanged)
    def rightPreviewCaption(self):
        return self._right_caption

    @Property(QObject, constant=True)
    def logs(self):
        return self._logs

    @Property(str, notify=lastCalibLogPathChanged)
    def lastCalibLogPath(self):
        return self._last_calib_log

    @Slot()
    def openCalibLogsFolder(self):
        folder = paths.calib_logs_dir()
        folder.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder.resolve())))

    def _refresh_saved_state(self):
        if self._busy:
            self.savedCalibrationChanged.emit()
            return

        changed = False
        rmse_s = paths.stereo_rmse_if_exists()
        if rmse_s >= 0 and self._rmse_stereo != rmse_s:
            self._rmse_stereo = rmse_s
            self.rmseStereoChanged.emit()
            changed = True

        rmse_l = paths.npy_scalar_if_exists("left_rmse.npy")
        if rmse_l >= 0 and self._rmse_left != rmse_l:
            self._rmse_left = rmse_l
            self.rmseLeftChanged.emit()
            changed = True

        rmse_r = paths.npy_scalar_if_exists("right_rmse.npy")
        if rmse_r >= 0 and self._rmse_right != rmse_r:
            self._rmse_right = rmse_r
            self.rmseRightChanged.emit()
            changed = True

        meta = paths.load_calib_meta()
        if meta:
            for key, attr, sig in (
                ("rmse_left", "_rmse_left", self.rmseLeftChanged),
                ("rmse_right", "_rmse_right", self.rmseRightChanged),
                ("stereo_rmse", "_rmse_stereo", self.rmseStereoChanged),
                ("baseline_mm", "_baseline", self.baselineMmChanged),
            ):
                val = meta.get(key)
                if val is None:
                    continue
                fval = float(val)
                if getattr(self, attr) != fval:
                    setattr(self, attr, fval)
                    sig.emit()
                    changed = True
            det_l = meta.get("calib_left", meta.get("scan_left"))
            det_r = meta.get("calib_right", meta.get("scan_right"))
            pairs = meta.get("stereo_pairs_used", meta.get("pairs_stereo"))
            stats_changed = False
            if det_l is not None and self._det_l != int(det_l):
                self._det_l = int(det_l)
                stats_changed = True
            if det_r is not None and self._det_r != int(det_r):
                self._det_r = int(det_r)
                stats_changed = True
            if pairs is not None and self._pairs != int(pairs):
                self._pairs = int(pairs)
                stats_changed = True
            if stats_changed:
                self.calibrationStatsChanged.emit()
                changed = True

        if paths.calibration_exists():
            self._load_geometry_from_disk()
            changed = True

        if rmse_s >= 0:
            summary = f"Calibration enregistree (RMSE {rmse_s:.3f} px)"
            if self._summary != summary:
                self._summary = summary
                self.calibSummaryChanged.emit()
                changed = True
        elif self.hasSavedCalibration and not self._summary:
            self._summary = "Calibration enregistree dans camera_parameters/"
            self.calibSummaryChanged.emit()
            changed = True

        if changed:
            self._update_overall_quality()
            if self._step < 5 and self.hasSavedCalibration:
                self._step = 5
                self.stepChanged.emit()

        self.savedCalibrationChanged.emit()

    def _load_geometry_from_disk(self) -> None:
        base = paths.camera_params_dir()
        try:
            r = np.load(base / "R.npy")
            t = np.load(base / "T.npy")
            t_vec = np.asarray(t).reshape(-1)
            baseline = float(np.linalg.norm(t_vec))
            rotation, detail = _rotation_angles(r)
            if self._baseline != baseline:
                self._baseline = baseline
                self.baselineMmChanged.emit()
            if self._rotation != rotation or self._rotation_detail != detail:
                self._rotation = rotation
                self._rotation_detail = detail
                self.rotationDegChanged.emit()
        except OSError:
            pass

        for fname, attr, sig in (
            ("mtx1.npy", "_focal_l", self.focalLeftPxChanged),
            ("mtx2.npy", "_focal_r", self.focalRightPxChanged),
        ):
            try:
                mtx = np.load(base / fname)
                focal = float((float(mtx[0, 0]) + float(mtx[1, 1])) / 2.0)
                if getattr(self, attr) != focal:
                    setattr(self, attr, focal)
                    sig.emit()
            except OSError:
                pass

    def _update_overall_quality(self) -> None:
        tiers: list[int] = []
        for rmse in (self._rmse_left, self._rmse_right, self._rmse_stereo):
            tier = self._overall_tier_from_rmse(rmse)
            if tier > 0:
                tiers.append(tier)
        if not tiers:
            self._quality_tier = 0
            self._quality_label = ""
            self._quality_hint = ""
            self._quality_color = "#B8C5D6"
            self._quality_bg = "#1A2030"
            self.overallQualityChanged.emit()
            return

        worst = max(tiers)
        labels = {
            1: "Excellente",
            2: "Bonne",
            3: "Correcte",
            4: "Limite",
            5: "Insuffisante",
        }
        hints = {
            1: "Reprojection tres precise - pret pour la mesure.",
            2: "Bonne precision - mesure fiable en conditions normales.",
            3: "Utilisable ; verifiez la couverture de la mire sur toute l'image.",
            4: "Precision faible - repassez la sync ou rescannez avec plus de poses.",
            5: "Calibration peu fiable - revoyez mire, eclairage et parametres ChArUco.",
        }
        colors = {1: "#22C55E", 2: "#22C55E", 3: "#F59E0B", 4: "#EF4444", 5: "#EF4444"}
        bgs = {1: "#143322", 2: "#143322", 3: "#2A2210", 4: "#2A1416", 5: "#2A1416"}
        self._quality_tier = worst
        self._quality_label = labels.get(worst, "")
        self._quality_hint = hints.get(worst, "")
        self._quality_color = colors.get(worst, "#B8C5D6")
        self._quality_bg = bgs.get(worst, "#1A2030")
        self.overallQualityChanged.emit()

    @staticmethod
    def _overall_tier_from_rmse(rmse_px: float) -> int:
        if rmse_px < 0:
            return 0
        if rmse_px < 1.0:
            return 1
        if rmse_px < 3.0:
            return 2
        if rmse_px < 5.0:
            return 3
        if rmse_px < 15.0:
            return 4
        return 5

    def _clear_live_results(self) -> None:
        """Remet à zéro les métriques affichées (nouvelle calibration)."""
        self._rmse_stereo = -1.0
        self._rmse_left = -1.0
        self._rmse_right = -1.0
        self._baseline = -1.0
        self._rotation = -1.0
        self._rotation_detail = ""
        self._focal_l = -1.0
        self._focal_r = -1.0
        self._det_l = -1
        self._det_r = -1
        self._pairs = -1
        self._summary = ""
        self.rmseStereoChanged.emit()
        self.rmseLeftChanged.emit()
        self.rmseRightChanged.emit()
        self.baselineMmChanged.emit()
        self.rotationDegChanged.emit()
        self.focalLeftPxChanged.emit()
        self.focalRightPxChanged.emit()
        self.calibrationStatsChanged.emit()
        self.calibSummaryChanged.emit()
        self._update_overall_quality()

    # ── Etat de l'import « depuis Synchronisation » ─────────────────────

    @staticmethod
    def _normalize_path(path: str) -> str:
        if not path:
            return ""
        try:
            return str(Path(path).resolve())
        except OSError:
            return path

    def _saved_sync_paths(self) -> tuple[str, str]:
        """Paire enregistree par l'etape Synchronisation (videos.txt)."""
        p = paths.cam_param("videos.txt")
        if not p.is_file():
            return "", ""
        try:
            lines = [
                ln.strip()
                for ln in p.read_text(encoding="utf-8").splitlines()
                if ln.strip()
            ]
        except OSError:
            return "", ""
        left = lines[0] if lines else ""
        right = lines[1] if len(lines) > 1 else ""
        return left, right

    def _read_sync_delta(self) -> int:
        """Decalage droite - gauche, en frames, lu depuis sync_frames.npy."""
        sync = paths.cam_param("sync_frames.npy")
        if not sync.is_file():
            return 0
        try:
            values = npy_io.load_int1d(str(sync))
        except (OSError, ValueError):
            return 0
        if len(values) < 2:
            return 0
        return int(values[1]) - int(values[0])

    def _refresh_sync_state(self) -> None:
        """Recalcule disponibilite de l'import, decalage et nombre de frames.

        Appele a chaque changement de paire video : c'est ce qui alimente le
        bandeau « Paire synchronisee » du panneau lateral.
        """
        saved_left, saved_right = self._saved_sync_paths()
        has_sync = paths.cam_param("sync_frames.npy").is_file()
        self._sync_available = bool(
            saved_left
            and saved_right
            and Path(saved_left).is_file()
            and Path(saved_right).is_file()
        )
        self._sync_delta = self._read_sync_delta()

        cur_left = self._normalize_path(self._left)
        cur_right = self._normalize_path(self._right)
        self._videos_from_sync = bool(
            cur_left
            and cur_right
            and has_sync
            and cur_left == self._normalize_path(saved_left)
            and cur_right == self._normalize_path(saved_right)
        )

        if self._videos_from_sync:
            meta_l = probe_video(self._left)
            meta_r = probe_video(self._right)
            self._sync_left_frames = meta_l.frame_count if meta_l.valid else 0
            self._sync_right_frames = meta_r.frame_count if meta_r.valid else 0
        else:
            self._sync_left_frames = 0
            self._sync_right_frames = 0

        self.syncImportChanged.emit()

    def _set_video_pair(self, left: str, right: str) -> None:
        """Point de passage unique : evite d'oublier un signal quelque part."""
        if left:
            self._left = left
            self.leftVideoChanged.emit()
        if right:
            self._right = right
            self.rightVideoChanged.emit()
        self.bothVideosSelectedChanged.emit()
        self._refresh_sync_state()

    @Slot(str, str)
    def importFromSync(self, left: str, right: str):
        self._set_video_pair(left, right)
        self._logs.append("Videos importees depuis Sync")

    @Slot()
    def loadVideosFromSync(self):
        """Bouton « Importer les videos » du panneau lateral Calibration.

        Reprend la paire enregistree a l'etape Synchronisation, en disant
        explicitement pourquoi ca echoue le cas echeant : l'ancien menu
        « Recharger derniere paire » sortait sans un mot quand videos.txt
        etait absent.
        """
        left, right = self._saved_sync_paths()
        if not left or not right:
            self._logs.append(
                "[!] Aucune paire synchronisee - terminez l'etape "
                "Synchronisation (Appliquer sync / Enregistrer In-Out)."
            )
            return
        missing = [p for p in (left, right) if not Path(p).is_file()]
        if missing:
            details = "\n".join(f"  {p}" for p in missing)
            self._logs.append(f"[!] Fichier video introuvable :\n{details}")
            return
        self._set_video_pair(left, right)
        self._logs.append(
            f"Videos importees depuis Sync : decalage {self.syncOffsetLabel}"
            f" | {self.syncFramesLabel or 'nombre de frames inconnu'}"
        )

    @Slot()
    def reloadSavedVideos(self):
        left, right = self._saved_sync_paths()
        if not left and not right:
            return
        self._set_video_pair(left, right)

    def _estimate_trim_span(self) -> int:
        if not self._left or not self._right:
            return 0
        meta_l = probe_video(self._left)
        meta_r = probe_video(self._right)
        if not meta_l.valid or not meta_r.valid:
            return 0
        left_in, left_out = 0, max(0, meta_l.frame_count - 1)
        right_in, right_out = 0, max(0, meta_r.frame_count - 1)
        trim = paths.cam_param("trim_frames.npy")
        if trim.is_file():
            try:
                t = npy_io.load_int1d(str(trim))
                if len(t) >= 4:
                    left_in = max(0, min(int(t[0]), left_out))
                    left_out = (
                        max(0, min(int(t[1]), meta_l.frame_count - 1))
                        if int(t[1]) > 0
                        else left_out
                    )
                    right_in = max(0, min(int(t[2]), right_out))
                    right_out = (
                        max(0, min(int(t[3]), meta_r.frame_count - 1))
                        if int(t[3]) > 0
                        else right_out
                    )
            except OSError:
                pass
        span_l = max(0, left_out - left_in + 1)
        span_r = max(0, right_out - right_in + 1)
        if span_l <= 0 and span_r <= 0:
            return 0
        if span_l <= 0:
            return span_r
        if span_r <= 0:
            return span_l
        return min(span_l, span_r)

    @Slot()
    def pickLeftVideo(self):
        path, _ = QFileDialog.getOpenFileName(
            None, "Video gauche", "", "Videos (*.mp4 *.avi *.mov *.mkv);;Tous (*.*)"
        )
        if path:
            self._left = path
            self.leftVideoChanged.emit()
            self.bothVideosSelectedChanged.emit()
            self._refresh_sync_state()

    @Slot()
    def pickRightVideo(self):
        path, _ = QFileDialog.getOpenFileName(
            None, "Video droite", "", "Videos (*.mp4 *.avi *.mov *.mkv);;Tous (*.*)"
        )
        if path:
            self._right = path
            self.rightVideoChanged.emit()
            self.bothVideosSelectedChanged.emit()
            self._refresh_sync_state()

    @Slot()
    def runCalibration(self):
        if not self.bothVideosSelected:
            self._logs.append("[!] Selectionnez les deux videos avant de lancer la calibration.")
            return
        if self._service.is_running():
            self._logs.append("[!] Calibration deja en cours.")
            return

        s = self._settings
        self._clear_charuco_suggestion()
        self._busy = True
        self._progress = 0
        self._progress_active = True
        self._work_stage = 0
        self._step = 1
        self._phase = "Demarrage…"
        self._r_stamp = self._r_file_stamp()
        self._last_run_error = ""
        self.lastRunErrorChanged.emit()
        self._detail = ""
        self._left_caption = ""
        self._right_caption = ""
        # Sans ca, l'apercu reaffiche la derniere image du calcul precedent
        # (autre paire video) jusqu'a la premiere detection du nouveau.
        if self._images is not None:
            self._images.set_image("calib_left", QImage())
            self._images.set_image("calib_right", QImage())
        self._preview_tick += 1
        self.previewTickChanged.emit()
        self._clear_live_results()
        self._det_l = 0
        self._det_r = 0
        self._pairs = 0
        self.calibrationStatsChanged.emit()
        self.busyChanged.emit()
        self.progressChanged.emit()
        self.progressActiveChanged.emit()
        self.workStageChanged.emit()
        self.stepChanged.emit()
        self.phaseChanged.emit()
        self.detailChanged.emit()

        params = CalibJobParams(
            left_video=self._left,
            right_video=self._right,
            squares_x=s.charucoSquaresX,
            squares_y=s.charucoSquaresY,
            square_length_mm=s.charucoSquareLengthMm,
            marker_length_mm=s.charucoMarkerLengthMm,
            aruco_dict_name=s.charucoDictName,
            max_intrinsic_views=s.maxCalibViews,
            max_stereo_pairs=s.maxStereoPairs,
            scan_stride=s.calibScanStride,
            dense_window=s.calibDenseWindow,
            dense_stride=s.calibDenseStride,
            calib_camera_scale=s.calibCameraScale,
        )
        log_path = self._file_logger.start_session(
            {
                "Preregle": s.calibQualityPresetLabel,
                "Dossier": str(paths.camera_params_dir()),
                "Video gauche": self._left,
                "Video droite": self._right,
                "Mire ChArUco": f"{s.charucoSquaresX}x{s.charucoSquaresY}",
                "Cote carre (mm)": s.charucoSquareLengthMm,
                "Marqueur (mm)": s.charucoMarkerLengthMm,
                "Dictionnaire": s.charucoDictName,
                "Vues max": s.maxCalibViews,
                "Paires stereo max": s.maxStereoPairs,
                "Saut sans mire": s.calibScanStride,
                "Rafale apres mire": s.calibDenseWindow,
                "Pas rafale": s.calibDenseStride,
                "Echelle OpenCV": s.calibCameraScale,
            }
        )
        self._last_calib_log = str(log_path)
        self.lastCalibLogPathChanged.emit()
        self._service.run_calibration(params)
        span = self._estimate_trim_span()
        self._logs.append(
            f"Préréglage {s.calibQualityPresetLabel} - "
            f"{s.maxCalibViews} vues, {s.maxStereoPairs} paires, "
            f"pas={s.calibScanStride}, rafale={s.calibDenseWindow}, "
            f"pas rafale={s.calibDenseStride}"
        )
        if span > 0:
            self._logs.append(
                f"Plage trim Sync : ~{span} frames"
                + (" - rognez dans Sync si > 9000 (~5 min)" if span > 9000 else "")
            )
        else:
            self._logs.append(
                "[!] Trim Sync inconnu - la calibration utilisera toute la video (lent)."
            )
        self._logs.append("Suivez la progression et les logs ci-dessous.")

    @Slot()
    def cancel(self):
        if self._service.is_running():
            self._service.cancel()
            self._logs.append("Annulation demandee - arret apres la frame courante…")
        self.calibrationCancelled.emit()

    def _on_progress(self, value: int) -> None:
        self._progress = max(0, min(100, int(value)))
        self._progress_active = True
        self._work_stage = self._stage_from_progress(self._progress)
        self.progressChanged.emit()
        self.progressActiveChanged.emit()
        self.workStageChanged.emit()

    def _on_phase(self, phase: str) -> None:
        self._phase = phase
        self._work_stage = self._stage_from_phase(phase, self._progress)
        self.phaseChanged.emit()
        self.workStageChanged.emit()

    def _on_live_status(self, text: str) -> None:
        self._detail = text
        self.detailChanged.emit()

    def _on_scan_counts(self, left: int, right: int, pairs: int) -> None:
        changed = False
        if self._det_l != left:
            self._det_l = left
            changed = True
        if self._det_r != right:
            self._det_r = right
            changed = True
        if self._pairs != pairs:
            self._pairs = pairs
            changed = True
        if changed:
            self.calibrationStatsChanged.emit()

    def _on_opencv_busy(self, busy: bool) -> None:
        self._progress_active = not busy
        self.progressActiveChanged.emit()

    def _on_stereo_ready(self, data: dict) -> None:
        self._apply_stereo_data(data)

    def _on_verify_ready(self, data: dict) -> None:
        self._apply_stereo_data(data)
        stats = data.get("stats") or {}
        self._det_l = int(stats.get("calib_left", stats.get("scan_left", -1)))
        self._det_r = int(stats.get("calib_right", stats.get("scan_right", -1)))
        self._pairs = int(stats.get("stereo_pairs_used", stats.get("pairs_stereo", -1)))
        self._step = 5
        self._work_stage = 5
        self._progress = 100
        self._summary = (
            f"Calibration terminee - RMSE stéréo {self._rmse_stereo:.3f} px"
            if self._rmse_stereo >= 0
            else "Calibration terminee"
        )
        self.calibrationStatsChanged.emit()
        self.stepChanged.emit()
        self.workStageChanged.emit()
        self.progressChanged.emit()
        self.calibSummaryChanged.emit()
        self._refresh_saved_state()
        self.calibrationComplete.emit()

    def _on_preview(self, slot_id: str, image: QImage, caption: str) -> None:
        if self._images is not None:
            self._images.set_image(slot_id, image)
        if slot_id == "calib_left":
            self._left_caption = caption
        else:
            self._right_caption = caption
        self._preview_tick += 1
        self.previewTickChanged.emit()

    def _on_suggest_config(self, sx: int, sy: int) -> None:
        """La sonde a trouve une grille qui marche mieux que celle saisie.

        L'analyse teste d'abord la configuration saisie sur une frame sonde.
        Si elle n'y voit pas assez de coins, elle balaie les grilles de 3 a 13
        colonnes sur 3 a 9 lignes - a taille de case et dictionnaire ArUco
        inchanges - et retient celle dont le nombre de marqueurs attendus
        correspond aux identifiants ArUco reellement vus.

        Le resultat n'etait qu'une ligne de console, noyee dans le journal :
        la calibration continuait avec la mauvaise grille sans que personne
        ne s'en apercoive. Il est desormais publie a l'ecran, avec un bouton
        pour l'appliquer.
        """
        cur_x = self._settings.charucoSquaresX
        cur_y = self._settings.charucoSquaresY
        if sx == cur_x and sy == cur_y:
            return
        self._suggested_sx = int(sx)
        self._suggested_sy = int(sy)
        self.charucoSuggestionChanged.emit()
        self._logs.append(
            f"[!] Mire detectee {sx}x{sy} - config actuelle {cur_x}x{cur_y}. "
            "Voir « Mire ChArUco » dans le panneau de gauche pour l'appliquer."
        )

    def _drop_matching_suggestion(self) -> None:
        """La suggestion disparait des que les reglages la rejoignent."""
        if not self._suggested_sx:
            return
        if (
            self._suggested_sx == self._settings.charucoSquaresX
            and self._suggested_sy == self._settings.charucoSquaresY
        ):
            self._clear_charuco_suggestion()

    def _clear_charuco_suggestion(self) -> None:
        if not self._suggested_sx and not self._suggested_sy:
            return
        self._suggested_sx = 0
        self._suggested_sy = 0
        self.charucoSuggestionChanged.emit()

    @Property(bool, notify=charucoSuggestionChanged)
    def charucoSuggestionAvailable(self):
        return bool(self._suggested_sx and self._suggested_sy)

    @Property(str, notify=charucoSuggestionChanged)
    def charucoSuggestionLabel(self):
        if not self.charucoSuggestionAvailable:
            return ""
        return f"{self._suggested_sx}×{self._suggested_sy}"

    @Slot()
    def applyCharucoSuggestion(self):
        if not self.charucoSuggestionAvailable:
            return
        sx, sy = self._suggested_sx, self._suggested_sy
        self._settings.charucoSquaresX = sx
        self._settings.charucoSquaresY = sy
        self._logs.append(f"Mire ChArUco reglee sur {sx}x{sy} - relancez la calibration.")
        self._clear_charuco_suggestion()

    @Slot()
    def dismissCharucoSuggestion(self):
        self._clear_charuco_suggestion()

    def _on_error(self, text: str) -> None:
        if text and not text.startswith("[!]"):
            self._logs.append(text)

    def _on_finished(self) -> None:
        self._busy = False
        self._progress_active = False
        if self._progress < 100 and self._step < 5:
            self._phase = "Arrete ou erreur"
        if self._r_file_stamp() == self._r_stamp:
            # Plage vide, calibration rejetee ou arret : rien d'ecrit. Sans ce
            # message, la page reaffichait l'ancien resultat comme le nouveau.
            self._last_run_error = (
                "Calibration non enregistrée : le résultat affiché est le "
                "précédent. Cause dans le journal."
            )
            self.lastRunErrorChanged.emit()
        self.busyChanged.emit()
        self.progressActiveChanged.emit()
        self.phaseChanged.emit()
        self._finalize_calib_log()

    def _finalize_calib_log(self) -> None:
        if not self._file_logger.is_active():
            return
        if self._step >= 5 and self._progress >= 100:
            status = "succes"
            parts = [self._summary or "Calibration terminee."]
            if self._rmse_left >= 0:
                parts.append(f"RMSE gauche {self._rmse_left:.3f} px.")
            if self._rmse_right >= 0:
                parts.append(f"RMSE droite {self._rmse_right:.3f} px.")
            if self._rmse_stereo >= 0:
                parts.append(f"RMSE stereo {self._rmse_stereo:.3f} px.")
            summary = " ".join(parts)
        else:
            status = "interrompu"
            summary = self._phase or "Calibration arretee."
        path = self._file_logger.end_session(status, summary)
        if path is None:
            return
        self._last_calib_log = str(path)
        self.lastCalibLogPathChanged.emit()
        rel = self._rel_log_path(path)
        self._logs.append(f"Journal enregistre : {rel}")
        self._logs.append(
            f"Dernier log (copie) : {self._rel_log_path(paths.calib_logs_dir() / 'calib_latest.log')}"
        )

    @staticmethod
    def _rel_log_path(path) -> str:
        try:
            return str(path.relative_to(paths.app_data_root()))
        except ValueError:
            return str(path)

    def _apply_stereo_data(self, data: dict) -> None:
        if data.get("rmse_left") is not None:
            self._rmse_left = float(data["rmse_left"])
            self.rmseLeftChanged.emit()
        if data.get("rmse_right") is not None:
            self._rmse_right = float(data["rmse_right"])
            self.rmseRightChanged.emit()
        if data.get("rmse_stereo") is not None:
            self._rmse_stereo = float(data["rmse_stereo"])
            self.rmseStereoChanged.emit()
        if data.get("baseline_mm") is not None:
            self._baseline = float(data["baseline_mm"])
            self.baselineMmChanged.emit()
        r = data.get("R")
        t = data.get("T")
        if r is not None and t is not None and data.get("baseline_mm") is None:
            t_vec = np.asarray(t).reshape(-1)
            self._baseline = float(np.linalg.norm(t_vec))
            self._rotation, self._rotation_detail = _rotation_angles(r)
            self.baselineMmChanged.emit()
            self.rotationDegChanged.emit()
        for key, attr, sig in (
            ("mtx1", "_focal_l", self.focalLeftPxChanged),
            ("mtx2", "_focal_r", self.focalRightPxChanged),
        ):
            mtx = data.get(key)
            if mtx is None:
                continue
            m = np.asarray(mtx)
            focal = float((float(m[0, 0]) + float(m[1, 1])) / 2.0)
            if getattr(self, attr) != focal:
                setattr(self, attr, focal)
                sig.emit()
        self._update_overall_quality()

    @staticmethod
    def _stage_from_progress(progress: int) -> int:
        if progress < 10:
            return 0
        if progress < 55:
            return 1
        if progress < 68:
            return 2
        if progress < 82:
            return 3
        if progress < 93:
            return 4
        return 5

    @staticmethod
    def _stage_from_phase(phase: str, progress: int) -> int:
        p = phase.lower()
        if "step 1" in p or "etape 1" in p or "sync" in p:
            return 0
        if "step 2" in p or "etape 2" in p or "scan" in p:
            return 1
        if "intrinsic" in p or "focal" in p or "calibratecamera" in p:
            if "droite" in p or "right" in p or " d" in p:
                return 3
            return 2
        if "stereo" in p or "step 4" in p:
            return 4
        if "saving" in p or "step 5" in p or "step 6" in p or "verify" in p:
            return 5
        return CalibrationController._stage_from_progress(progress)

    @Slot()
    def exportCalibrationPackage(self):
        path, _ = QFileDialog.getSaveFileName(
            None,
            "Exporter la calibration",
            "AquaMeasure_calib.zip",
            "Archive ZIP (*.zip);;Tous (*.*)",
        )
        if not path:
            return
        from src.util import calib_package_io

        err = calib_package_io.export_calibration_package(path)
        if err:
            self._logs.append(f"[!] Export calibration : {err}")
        else:
            self._logs.append(f"Calibration exportee : {path}")

    @Slot()
    def importCalibrationPackage(self):
        path, _ = QFileDialog.getOpenFileName(
            None,
            "Importer une configuration",
            "",
            "Archive ZIP (*.zip);;Tous (*.*)",
        )
        if not path:
            return
        from src.util import calib_package_io

        err = calib_package_io.import_calibration_package(path)
        if err:
            self._logs.append(f"[!] Import calibration : {err}")
        else:
            self._refresh_saved_state()
            self._logs.append(f"Calibration importee depuis {path}")

    @Slot()
    def saveCalibrationBackup(self):
        path, _ = QFileDialog.getSaveFileName(
            None,
            "Enregistrer la calibration",
            "AquaMeasure_calib_backup.zip",
            "Archive ZIP (*.zip);;Tous (*.*)",
        )
        if not path:
            return
        from src.util import calib_package_io

        err = calib_package_io.export_calibration_package(path)
        if err:
            self._logs.append(f"[!] Sauvegarde calibration : {err}")
        else:
            self._logs.append(f"Calibration sauvegardee : {path}")

    @Slot(float, float, int)
    def exportCharucoBoardPng(self, page_w_mm: float, page_h_mm: float, dpi: int):
        folder = QFileDialog.getExistingDirectory(
            None,
            "Choisir le dossier de téléchargement",
        )
        if not folder:
            return
        s = self._settings
        try:
            from charuco_board_export import write_charuco_board_png

            path, dict_used = write_charuco_board_png(
                folder,
                cols=s.charucoSquaresX,
                rows=s.charucoSquaresY,
                square_mm=s.charucoSquareLengthMm,
                marker_mm=s.charucoMarkerLengthMm,
                page_w_mm=page_w_mm,
                page_h_mm=page_h_mm,
                dpi=dpi,
                dict_name=s.charucoDictName,
                auto_dict=True,
            )
            self._logs.append(f"Mire enregistree : {path}")
            if dict_used != s.charucoDictName:
                self._logs.append(
                    f"[!] Dictionnaire utilise : {dict_used} "
                    f"(grille trop grande pour {s.charucoDictName} - mettre a jour la calibration)")
        except Exception as exc:
            self._logs.append(f"[!] Export mire : {exc}")

    @Slot(str, bool, float, int)
    def printCharucoBoard(self, page_format: str, landscape: bool, margin_mm: float, dpi: int):
        """Compatibilité - délégué vers export PNG si encore appelé."""
        _ = page_format, landscape, margin_mm
        w, h = 297.0, 420.0
        if page_format == "A4":
            w, h = (297.0, 210.0) if landscape else (210.0, 297.0)
        elif page_format == "A3":
            w, h = (420.0, 297.0) if landscape else (297.0, 420.0)
        elif page_format == "A2":
            w, h = (594.0, 420.0) if landscape else (420.0, 594.0)
        elif page_format == "Letter":
            w, h = (279.4, 215.9) if landscape else (215.9, 279.4)
        avail_w = max(1.0, w - 2 * margin_mm)
        avail_h = max(1.0, h - 2 * margin_mm)
        self.exportCharucoBoardPng(avail_w, avail_h, dpi)

    @Slot()
    def reloadSavedCalibration(self):
        self._refresh_saved_state()
        self._logs.append("Etat calibration recharge")

    @Slot(float, result=int)
    def rmseQualityTier(self, rmse_px: float) -> int:
        if rmse_px < 0:
            return 0
        if rmse_px < 0.5:
            return 3
        if rmse_px < 1.0:
            return 2
        if rmse_px < 2.0:
            return 1
        return 0

    @Slot(float, result=str)
    def rmseQualityLabel(self, rmse_px: float) -> str:
        tier = self.rmseQualityTier(rmse_px)
        return ["Faible", "Moyen", "Bon", "Excellent"][tier]

    @Slot(float, result=str)
    def rmseQualityHint(self, rmse_px: float) -> str:
        return ""

    @Slot(float, result=str)
    def rmseQualityColor(self, rmse_px: float) -> str:
        tier = self.rmseQualityTier(rmse_px)
        return ["#f87171", "#fbbf24", "#4ade80", "#22c55e"][tier]

    @Slot(float, result=str)
    def rmseQualityBg(self, rmse_px: float) -> str:
        return "#1a2030"
