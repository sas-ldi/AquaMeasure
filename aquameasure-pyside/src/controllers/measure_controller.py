from __future__ import annotations

from pathlib import Path

import cv2
from PySide6.QtCore import Property, QObject, QPointF, QTimer, Qt, Signal, Slot
from PySide6.QtWidgets import QFileDialog

from src.backend.measure_service import MeasureService
from src.imaging.qimage_util import bgr_array_to_qimage
from src.imaging.rect_mapping import RectMapping
from src.util import paths
from src.util.log_model import LogModel


class MeasureController(QObject):
    distanceMmChanged = Signal()
    frameIndexChanged = Signal()
    frameCountChanged = Signal()
    leftVideoChanged = Signal()
    rightVideoChanged = Signal()
    framesUpdated = Signal()
    previewTickChanged = Signal()
    videoMetaChanged = Signal()
    measureStepChanged = Signal()
    pointsChanged = Signal()
    playingChanged = Signal()
    epipolarChanged = Signal()
    epipolarLinesChanged = Signal()
    # « L'image affichee est-elle l'image rectifiee ? » change quand on lance
    # la lecture, quand la calibration change, et quand la rectification
    # cesse d'etre disponible. Tout ce qui peint par-dessus la video ecoute
    # ce signal-la, pas trois signaux mal correles.
    overlaySpaceChanged = Signal()

    _INVALID = -1.0

    def __init__(self, parent=None):
        super().__init__(parent)
        self._logs = LogModel(self)
        self._service = MeasureService(self)
        self._service.distanceMm.connect(self._on_distance)
        self._service.logLine.connect(self._logs.append, Qt.ConnectionType.QueuedConnection)
        self._service.error.connect(self._on_service_error, Qt.ConnectionType.QueuedConnection)
        self._images = None
        self._distance_mm = 0.0
        self._frame_index = 0
        self._frame_count = 0
        self._left = ""
        self._right = ""
        self._left_source_hint = ""
        self._right_source_hint = ""
        self._videos_from_sync = False
        self._measure_step = 0
        self._left_a = QPointF(self._INVALID, self._INVALID)
        self._left_b = QPointF(self._INVALID, self._INVALID)
        self._right_a = QPointF(self._INVALID, self._INVALID)
        self._right_b = QPointF(self._INVALID, self._INVALID)
        self._playing = False
        self._epipolar = False
        self._epipolar_ys: list[float] = []
        self._preview_tick = 0
        self._frames_ready = False
        self._refresh_busy = False
        # Cartes rectifie -> brut, construites a la demande et jetees des que
        # la calibration ou la paire de videos change. Le jeton sert aux
        # caches d'overlay : ils savent ainsi que leurs coordonnees converties
        # ne valent plus rien, sans avoir a comparer des tableaux numpy.
        self._rect_mapping: dict[bool, RectMapping | None] = {True: None, False: None}
        self._rect_mapping_token = 0
        self._play_timer = QTimer(self)
        self._play_timer.timeout.connect(self._on_play_tick)
        self.frameIndexChanged.connect(self._refresh_frames)

    def set_image_provider(self, provider):
        self._images = provider

    def _on_distance(self, d: float):
        self._distance_mm = d
        self.distanceMmChanged.emit()

    @Slot(str)
    def _on_service_error(self, msg: str):
        self._logs.append(msg)

    @Property(float, notify=distanceMmChanged)
    def distanceMm(self):
        return self._distance_mm

    @Property(int, notify=frameIndexChanged)
    def frameIndex(self):
        return self._frame_index

    @frameIndex.setter
    def frameIndex(self, i: int):
        if self._frame_count <= 0:
            return
        i = max(0, min(i, self._frame_count - 1))
        if self._frame_index != i:
            self._frame_index = i
            self.frameIndexChanged.emit()

    @Property(int, notify=frameCountChanged)
    def frameCount(self):
        return self._frame_count

    @Property(int, notify=previewTickChanged)
    def previewTick(self):
        return self._preview_tick

    @Property(bool, notify=previewTickChanged)
    def rectifiedReady(self):
        return self._frames_ready

    @Property(str, notify=leftVideoChanged)
    def leftVideo(self):
        return self._left

    @Property(str, notify=rightVideoChanged)
    def rightVideo(self):
        return self._right

    @Property(QObject, constant=True)
    def logs(self):
        return self._logs

    @Property(float, notify=videoMetaChanged)
    def leftFps(self):
        return self._service.left_fps()

    @Property(float, notify=videoMetaChanged)
    def rightFps(self):
        return self._service.right_fps()

    @Property(int, notify=frameIndexChanged)
    def leftAbsFrame(self):
        return self._service.left_abs_frame(self._frame_index)

    @Slot(int, result=int)
    def leftAbsFrameAt(self, index: int) -> int:
        return self._service.left_abs_frame(index)

    @Slot(int, result=int)
    def alignedIndexFromLeftAbs(self, abs_frame: int) -> int:
        """Index timeline correspondant a une frame absolue du fichier gauche.

        Conversion inverse de leftAbsFrameAt, bornee a la timeline courante :
        c'est ce qui permet de rejouer une annotation enregistree en index
        absolu sans deplacer la lecture (seekFromLeftAbsFrame, lui, deplace).
        """
        return self._service.aligned_index_from_left_abs(abs_frame)

    @Property(int, notify=frameIndexChanged)
    def rightAbsFrame(self):
        return self._service.right_abs_frame(self._frame_index)

    @Property(int, notify=videoMetaChanged)
    def leftSyncFrame(self):
        return self._service.left_sync_frame()

    @Property(int, notify=videoMetaChanged)
    def rightSyncFrame(self):
        return self._service.right_sync_frame()

    @Property(int, notify=videoMetaChanged)
    def syncOffset(self):
        return self._service.sync_offset()

    @Property(str, notify=videoMetaChanged)
    def syncOffsetLabel(self):
        off = self._service.sync_offset()
        sign = "+" if off > 0 else ""
        return f"{sign}{off} fr"

    @Property(bool, notify=videoMetaChanged)
    def videosFromSync(self):
        return self._videos_from_sync

    @Property(str, notify=videoMetaChanged)
    def leftVideoSourceHint(self):
        return self._left_source_hint

    @Property(str, notify=videoMetaChanged)
    def rightVideoSourceHint(self):
        return self._right_source_hint

    @Property(int, notify=videoMetaChanged)
    def frameWidth(self):
        return self._service.frame_width()

    @Property(int, notify=videoMetaChanged)
    def frameHeight(self):
        return self._service.frame_height()

    @Property(int, notify=videoMetaChanged)
    def leftVideoFrameCount(self):
        return self._service.left_video_frame_count()

    @Property(int, notify=videoMetaChanged)
    def rightVideoFrameCount(self):
        return self._service.right_video_frame_count()

    @Property(int, notify=measureStepChanged)
    def measureStep(self):
        return self._measure_step

    @Property(str, notify=measureStepChanged)
    def measureHint(self):
        hints = [
            "Cliquez le point A sur l'image gauche rectifiee",
            "Cliquez le point B sur l'image gauche rectifiee",
            "Cliquez le point A sur l'image droite rectifiee",
            "Cliquez le point B sur l'image droite rectifiee",
            "Faites glisser les poignees A/B pour ajuster la mesure",
        ]
        idx = min(self._measure_step, len(hints) - 1)
        return hints[idx]

    @Property(QPointF, notify=pointsChanged)
    def leftPointA(self):
        return self._left_a

    @Property(QPointF, notify=pointsChanged)
    def leftPointB(self):
        return self._left_b

    @Property(QPointF, notify=pointsChanged)
    def rightPointA(self):
        return self._right_a

    @Property(QPointF, notify=pointsChanged)
    def rightPointB(self):
        return self._right_b

    @Property(bool, notify=pointsChanged)
    def canCompute(self):
        return self._point_valid(self._left_a) and self._point_valid(self._left_b) and (
            self._point_valid(self._right_a) and self._point_valid(self._right_b)
        )

    @Property(bool, notify=playingChanged)
    def playing(self):
        return self._playing

    @Property(bool, notify=epipolarChanged)
    def epipolar(self):
        return self._epipolar

    @epipolar.setter
    def epipolar(self, v: bool):
        if self._epipolar != v:
            self._epipolar = v
            self._refresh_epipolar_lines()
            self.epipolarChanged.emit()

    def _refresh_epipolar_lines(self):
        ys: list[float] = []
        if self._epipolar:
            if self._point_valid(self._left_a):
                ys.append(float(self._left_a.y()))
            if self._point_valid(self._left_b):
                ys.append(float(self._left_b.y()))
        if ys != self._epipolar_ys:
            self._epipolar_ys = ys
            self.epipolarLinesChanged.emit()

    @Property(list, notify=epipolarLinesChanged)
    def epipolarLinesY(self):
        return self._epipolar_ys

    def _on_play_tick(self):
        if self._refresh_busy:
            return
        if self._frame_count <= 0:
            self.togglePlay()
            return
        if self._frame_index >= self._frame_count - 1:
            self.togglePlay()
            return
        self.frameIndex = self._frame_index + 1

    def _point_valid(self, p: QPointF) -> bool:
        return p.x() >= 0 and p.y() >= 0

    def _next_measure_step(self) -> int:
        """Return the first missing stereo point in placement order."""
        points = (self._left_a, self._left_b, self._right_a, self._right_b)
        for index, point in enumerate(points):
            if not self._point_valid(point):
                return index
        return 4

    def _clamp_right_y(self, idx: int, x: float, y: float) -> QPointF:
        if self._epipolar:
            if idx == 0 and self._point_valid(self._left_a):
                y = float(self._left_a.y())
            elif idx == 1 and self._point_valid(self._left_b):
                y = float(self._left_b.y())
        return QPointF(x, y)

    def _sync_right_y_from_left(self) -> None:
        if not self._epipolar:
            return
        if self._point_valid(self._left_a) and self._point_valid(self._right_a):
            self._right_a = QPointF(self._right_a.x(), self._left_a.y())
        if self._point_valid(self._left_b) and self._point_valid(self._right_b):
            self._right_b = QPointF(self._right_b.x(), self._left_b.y())

    def _refresh_frames(self) -> None:
        # Lecture : vidéo brute (SyncVideoPlayer) - pas de remap OpenCV par frame.
        if self._playing:
            return
        tick_changed = False
        was_ready = self._frames_ready
        self._refresh_busy = True
        try:
            if self._images is None or self._frame_count <= 0 or not self._service.calibration_loaded():
                if self._frames_ready and not self._playing:
                    self._frames_ready = False
                    tick_changed = True
            else:
                rect_l, rect_r, _, _ = self._service.get_rectified_pair(self._frame_index)
                if rect_l is None or rect_r is None:
                    # En lecture, garder la dernière frame rectifiée (évite le basculement
                    # vers SyncVideoPlayer / vidéo brute si un read() échoue ponctuellement).
                    if self._frames_ready and not self._playing:
                        self._frames_ready = False
                        tick_changed = True
                else:
                    self._images.set_image("measure_left", bgr_array_to_qimage(rect_l))
                    self._images.set_image("measure_right", bgr_array_to_qimage(rect_r))
                    self._frames_ready = True
                    self._preview_tick += 1
                    tick_changed = True
            if tick_changed:
                self.previewTickChanged.emit()
                self.framesUpdated.emit()
        finally:
            self._refresh_busy = False
        # Perdre la rectification a l'arret bascule la vue sur la video brute :
        # l'overlay change d'espace exactement comme au lancement du play.
        if was_ready != self._frames_ready:
            self.overlaySpaceChanged.emit()
            self.pointsChanged.emit()

    def _saved_sync_paths(self) -> tuple[str, str]:
        p = paths.cam_param("videos.txt")
        if not p.is_file():
            return "", ""
        lines = [ln.strip() for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]
        left = lines[0] if lines else ""
        right = lines[1] if len(lines) > 1 else ""
        return left, right

    def _has_sync_metadata(self) -> bool:
        return paths.cam_param("sync_frames.npy").is_file()

    def _normalize_path(self, path: str) -> str:
        if not path:
            return ""
        try:
            return str(Path(path).resolve())
        except OSError:
            return path

    def _resolve_video_path(self, path: str) -> str:
        if not path:
            return ""
        candidates = [Path(path)]
        if not candidates[0].is_absolute():
            candidates.append(paths.app_data_root() / path)
            candidates.append(paths.app_root() / path)
        for cand in candidates:
            try:
                if cand.is_file():
                    return str(cand.resolve())
            except OSError:
                continue
        return path

    def _read_videos_txt(self) -> tuple[str, str]:
        left, right = self._saved_sync_paths()
        return self._resolve_video_path(left), self._resolve_video_path(right)

    def _update_video_source_hints(self) -> None:
        saved_left, saved_right = self._saved_sync_paths()
        has_sync = self._has_sync_metadata()
        cur_left = self._normalize_path(self._left)
        cur_right = self._normalize_path(self._right)
        saved_left_n = self._normalize_path(saved_left)
        saved_right_n = self._normalize_path(saved_right)

        left_from_sync = bool(cur_left and saved_left_n and cur_left == saved_left_n and has_sync)
        right_from_sync = bool(cur_right and saved_right_n and cur_right == saved_right_n and has_sync)
        both_sync = left_from_sync and right_from_sync and bool(cur_left and cur_right)
        self._videos_from_sync = both_sync

        if left_from_sync:
            self._left_source_hint = "" if both_sync else "Importée depuis Sync"
        elif cur_left:
            self._left_source_hint = "Import manuel (PC)"
        else:
            self._left_source_hint = ""

        if right_from_sync:
            self._right_source_hint = "" if both_sync else "Importée depuis Sync"
        elif cur_right:
            self._right_source_hint = "Import manuel (PC)"
        else:
            self._right_source_hint = ""

    def _apply_videos(self):
        if not self._left or not self._right:
            return
        left = self._resolve_video_path(self._left)
        right = self._resolve_video_path(self._right)
        self._left = left
        self._right = right
        self._logs.append("── Chargement videos mesure ──")
        self._service.set_videos(left, right)
        # Les cartes dependent de la taille de l'image : changer de paire les
        # perime autant que changer de calibration.
        self._invalidate_rect_mapping()
        count = self._service.frame_count()
        if count != self._frame_count:
            self._frame_count = count
            self.frameCountChanged.emit()
        if count <= 0:
            self._logs.append(
                "[!] Timeline vide ou videos illisibles - verifiez sync_frames.npy "
                "et les chemins dans videos.txt")
        self.videoMetaChanged.emit()
        if self._frame_count > 0 and self._frame_index >= self._frame_count:
            self.frameIndex = 0
        self._update_video_source_hints()
        self._refresh_frames()

    def _log_video_origin(self) -> None:
        """Journalise la paire chargee et sa provenance.

        « Quelle video est chargee, d'ou, de sync ? d'avant, genre une
        ancienne session ? » - la console ne le disait nulle part.
        """
        saved_left, saved_right = self._saved_sync_paths()
        has_sync = self._has_sync_metadata()
        for side, current, saved in (
            ("G", self._left, saved_left),
            ("D", self._right, saved_right),
        ):
            same = bool(
                current
                and saved
                and self._normalize_path(current) == self._normalize_path(saved)
            )
            if same and has_sync:
                origin = "paire synchronisee (videos.txt + sync_frames.npy)"
            elif same:
                origin = "videos.txt, mais aucune synchro enregistree"
            else:
                origin = "choix manuel - hors paire synchronisee"
            self._logs.append(f"  Video {side} : {current or '(aucune)'}")
            self._logs.append(f"    provenance : {origin}")

    @Property(str, notify=videoMetaChanged)
    def calibrationSummary(self):
        """« 19/08/2026 19:03 · calcul local · RMSE 0.54 px », ou vide."""
        return self._service.calibration_summary()

    @Property(str, notify=videoMetaChanged)
    def calibrationDir(self):
        return self._service.calibration_dir()

    @Slot()
    def loadCalibration(self):
        self._service.load_calibration()
        # Nouvelle calibration = nouvelles cartes de rectification. Sans cette
        # invalidation, l'overlay serait recale avec les cartes de la
        # calibration precedente - une erreur silencieuse de quelques pixels.
        self._invalidate_rect_mapping()
        self.videoMetaChanged.emit()
        self._refresh_frames()

    @Slot()
    def togglePlay(self):
        if self._playing:
            self._playing = False
            self._play_timer.stop()
            self._refresh_frames()
        elif self._frame_count > 0:
            self._playing = True
        self.playingChanged.emit()
        # Lecture et arret n'affichent pas la meme image : les poignees A/B
        # dessinees changent d'espace, l'overlay aussi. Sans ces deux
        # notifications, les liaisons QML gardaient les coordonnees de l'etat
        # precedent et les marqueurs restaient a cote du poisson.
        self.overlaySpaceChanged.emit()
        self.pointsChanged.emit()

    @Slot(int)
    def seekFromLeftAbsFrame(self, abs_frame: int) -> None:
        """Sync timeline depuis le lecteur vidéo (frame absolue fichier gauche)."""
        if self._frame_count <= 0:
            return
        idx = self._service.aligned_index_from_left_abs(abs_frame)
        if self._frame_index == idx:
            return
        self._frame_index = idx
        self.frameIndexChanged.emit()
        if not self._playing:
            self._refresh_frames()

    @Slot(int)
    def stepFrame(self, delta: int):
        if self._playing:
            self.togglePlay()
        self.frameIndex = self._frame_index + delta

    @Slot()
    def toggleEpipolar(self):
        self.epipolar = not self._epipolar

    @Slot()
    def pickLeftVideo(self):
        path, _ = QFileDialog.getOpenFileName(
            None, "Video gauche", "", "Videos (*.mp4 *.avi *.mov *.mkv);;Tous (*.*)"
        )
        if path:
            self._left = path
            self.leftVideoChanged.emit()
            self._apply_videos()

    @Slot()
    def pickRightVideo(self):
        path, _ = QFileDialog.getOpenFileName(
            None, "Video droite", "", "Videos (*.mp4 *.avi *.mov *.mkv);;Tous (*.*)"
        )
        if path:
            self._right = path
            self.rightVideoChanged.emit()
            self._apply_videos()

    @Slot()
    def loadVideosFromSync(self):
        left, right = self._read_videos_txt()
        if not left or not right:
            self._logs.append(
                "[!] videos.txt absent ou incomplet - enregistrez la sync "
                "(Appliquer sync / Enregistrer In-Out)")
            return
        if not Path(left).is_file() or not Path(right).is_file():
            self._logs.append(
                f"[!] Fichier video introuvable :\n  G: {left}\n  D: {right}")
            return
        self.refresh(left, right)

    def set_distance_mm(self, mm: float):
        self._distance_mm = mm
        self.distanceMmChanged.emit()

    @Slot(
        float, float, float, float,
        float, float, float, float, float,
    )
    def applySparseBBoxMeasure(
        self,
        left_ax: float,
        left_ay: float,
        left_bx: float,
        left_by: float,
        right_ax: float,
        right_ay: float,
        right_bx: float,
        right_by: float,
        length_mm: float,
    ):
        self._left_a = QPointF(left_ax, left_ay)
        self._left_b = QPointF(left_bx, left_by)
        self._right_a = QPointF(right_ax, right_ay)
        self._right_b = QPointF(right_bx, right_by)
        self._measure_step = 4
        self._distance_mm = float(length_mm)
        self.measureStepChanged.emit()
        self.pointsChanged.emit()
        self._refresh_epipolar_lines()
        self.distanceMmChanged.emit()
        self._logs.append(f"Mesure : {length_mm:.1f} mm")

    def rectified_pair(self, frame_index: int):
        return self._service.get_rectified_pair(frame_index)

    def raw_pair(self, frame_index: int):
        return self._service.get_raw_pair(frame_index)

    def left_rectifier(self):
        """Fonction de rectification de la vue gauche, ou None sans calibration.

        Utilisable depuis un autre thread : les cartes sont en lecture seule.
        """
        maps = self._service.left_rect_maps()
        if maps is None:
            return None
        map_x, map_y = maps

        def rectify(frame):
            return cv2.remap(frame, map_x, map_y, cv2.INTER_LINEAR)

        return rectify

    # ── Recalage de l'overlay entre image rectifiee et image brute ─────

    def _invalidate_rect_mapping(self) -> None:
        self._rect_mapping = {True: None, False: None}
        self._rect_mapping_token += 1
        self.overlaySpaceChanged.emit()
        self.pointsChanged.emit()

    def rect_mapping(self, left: bool = True) -> RectMapping:
        """Conversion rectifie -> image affichee pour une vue, jamais None.

        Sans calibration ou sans video, elle est l'identite exacte : l'image
        affichee est alors la meme dans les deux etats et il n'y a rien a
        recaler. C'est la seule fabrique de cette conversion dans
        l'application - tout le reste s'appuie dessus.
        """
        left = bool(left)
        cached = self._rect_mapping.get(left)
        if cached is not None:
            return cached
        mapping = RectMapping.identity()
        try:
            maps = (
                self._service.left_rect_maps() if left
                else self._service.right_rect_maps()
            )
            if maps is not None:
                mapping = RectMapping(
                    maps[0], maps[1],
                    frame_size=(
                        self._service.frame_width(), self._service.frame_height(),
                    ),
                )
        except Exception as exc:
            # Une calibration illisible ne doit pas emporter l'affichage avec
            # elle : sans cartes, on retombe sur l'identite et l'overlay reste
            # peint - au mauvais endroit pendant la lecture, mais visible, et
            # la console dit pourquoi.
            self._logs.append(f"[!] Recalage overlay indisponible : {exc}")
        self._rect_mapping[left] = mapping
        return mapping

    def rect_mapping_token(self) -> int:
        """Change des que les cartes changent - clef de cache pour l'overlay."""
        return self._rect_mapping_token

    def overlay_remap_active(self) -> bool:
        """Vrai quand l'overlay doit etre recale sur l'image brute affichee.

        La vue affiche l'image rectifiee seulement a l'arret et quand elle est
        prete : `showRectified = rectifiedReady && !playing`. Dans tous les
        autres cas c'est la video brute qui est a l'ecran, et l'overlay, lui,
        raisonne en pixels rectifies.
        """
        if self._frames_ready and not self._playing:
            # Image rectifiee a l'ecran : rien a recaler, et surtout aucune
            # carte a construire. Cette sortie precoce est ce qui garde le
            # chemin d'arret exactement aussi rapide qu'avant.
            return False
        if self._frame_count <= 0 or not self._service.calibration_loaded():
            # Sans paire de videos ou sans calibration il n'existe pas de
            # cartes : l'image affichee est la meme dans les deux etats.
            return False
        return not self.rect_mapping(True).is_identity

    @Property(bool, notify=overlaySpaceChanged)
    def overlayRemapActive(self):
        return self.overlay_remap_active()

    @Slot(bool, float, float, result=QPointF)
    def mapRectToDisplay(self, left: bool, x: float, y: float) -> QPointF:
        """Un point rectifie dans l'espace de l'image affichee, ou lui-meme."""
        if not self.overlay_remap_active():
            return QPointF(float(x), float(y))
        dx, dy = self.rect_mapping(left).map_point(float(x), float(y))
        return QPointF(dx, dy)

    def _display_point(self, left: bool, point: QPointF) -> QPointF:
        if not self._point_valid(point) or not self.overlay_remap_active():
            return point
        dx, dy = self.rect_mapping(left).map_point(point.x(), point.y())
        return QPointF(dx, dy)

    # Les quatre poignees de mesure restent stockees en rectifie - c'est
    # l'espace ou l'operateur les a posees et ou la triangulation les lit.
    # Seul le dessin passe par ces proprietes-la, pour que les points ne
    # glissent pas hors du poisson quand la lecture montre l'image brute.
    @Property(QPointF, notify=pointsChanged)
    def leftPointADisplay(self):
        return self._display_point(True, self._left_a)

    @Property(QPointF, notify=pointsChanged)
    def leftPointBDisplay(self):
        return self._display_point(True, self._left_b)

    @Property(QPointF, notify=pointsChanged)
    def rightPointADisplay(self):
        return self._display_point(False, self._right_a)

    @Property(QPointF, notify=pointsChanged)
    def rightPointBDisplay(self):
        return self._display_point(False, self._right_b)

    @Slot()
    def clearPoints(self):
        self._measure_step = 0
        inv = QPointF(self._INVALID, self._INVALID)
        self._left_a = self._left_b = self._right_a = self._right_b = inv
        self._distance_mm = 0.0
        self.measureStepChanged.emit()
        self.pointsChanged.emit()
        self._refresh_epipolar_lines()
        self.distanceMmChanged.emit()
        self._logs.append("Points effaces")

    @Slot(bool, float, float)
    def placePoint(self, left: bool, x: float, y: float):
        if self._frame_count <= 0 or self._playing:
            return
        idx = 0
        if left:
            if self._measure_step == 0:
                idx = 0
            elif self._measure_step == 1:
                idx = 1
            else:
                return
        elif self._measure_step == 2:
            idx = 0
        elif self._measure_step == 3:
            idx = 1
        else:
            return
        pt = self._clamp_right_y(idx, x, y) if not left else QPointF(x, y)
        if left:
            if self._measure_step == 0:
                self._left_a = pt
            elif self._measure_step == 1:
                self._left_b = pt
            else:
                return
            self._sync_right_y_from_left()
        elif self._measure_step == 2:
            self._right_a = pt
        elif self._measure_step == 3:
            self._right_b = pt
        else:
            return
        self._measure_step = self._next_measure_step()
        self.measureStepChanged.emit()
        self.pointsChanged.emit()
        self._refresh_epipolar_lines()
        side = "gauche" if left else "droite"
        label = "A" if self._measure_step <= 2 else "B"
        self._logs.append(f"Point {side} {label} : ({pt.x():.1f}, {pt.y():.1f})")
        if self._measure_step == 4:
            self.computeMeasure()

    @Slot(bool, int)
    def removePoint(self, left: bool, idx: int):
        if self._frame_count <= 0 or self._playing or idx not in (0, 1):
            return
        current = (
            (self._left_a, self._left_b) if left else (self._right_a, self._right_b)
        )[idx]
        if not self._point_valid(current):
            return
        invalid = QPointF(self._INVALID, self._INVALID)
        if left and idx == 0:
            self._left_a = invalid
        elif left:
            self._left_b = invalid
        elif idx == 0:
            self._right_a = invalid
        else:
            self._right_b = invalid
        self._measure_step = self._next_measure_step()
        self._distance_mm = 0.0
        self.measureStepChanged.emit()
        self.pointsChanged.emit()
        self._refresh_epipolar_lines()
        self.distanceMmChanged.emit()
        side = "gauche" if left else "droite"
        label = "A" if idx == 0 else "B"
        self._logs.append(f"Point {side} {label} efface")

    @Slot(bool, int, float, float)
    def movePoint(self, left: bool, idx: int, x: float, y: float):
        if self._frame_count <= 0 or self._playing:
            return
        if idx not in (0, 1):
            return
        current = (
            (self._left_a, self._left_b) if left else (self._right_a, self._right_b)
        )[idx]
        if not self._point_valid(current):
            return
        pt = self._clamp_right_y(idx, x, y) if not left else QPointF(x, y)
        if left:
            if idx == 0:
                self._left_a = pt
            else:
                self._left_b = pt
            self._sync_right_y_from_left()
        elif idx == 0:
            self._right_a = pt
        else:
            self._right_b = pt
        self.pointsChanged.emit()
        self._refresh_epipolar_lines()

    @Slot()
    def finishPointDrag(self):
        if not self.canCompute:
            return
        self._sync_right_y_from_left()
        self.pointsChanged.emit()
        self._refresh_epipolar_lines()
        self.computeMeasure()

    @Slot()
    def computeMeasure(self):
        if not self.canCompute:
            self._logs.append("[!] Placez les 4 points (A et B sur chaque vue)")
            return
        self._logs.append("── Calcul longueur 3D ──")
        self._service.measure_segment(self._left_a, self._left_b, self._right_a, self._right_b)

    @Slot(str, str)
    def refresh(self, left_fallback: str = "", right_fallback: str = ""):
        self._logs.append("═══ Preparation onglet Mesure ═══")
        txt_left, txt_right = self._read_videos_txt()
        left = self._resolve_video_path(left_fallback) or txt_left or self._left
        right = self._resolve_video_path(right_fallback) or txt_right or self._right
        if not left or not right:
            self._logs.append("[!] Aucune video - importez depuis Sync/Calibration")
            self._frame_count = 0
            self.frameCountChanged.emit()
            self._update_video_source_hints()
            self.videoMetaChanged.emit()
            return
        same = left == self._left and right == self._right
        prev = self._frame_index
        if left != self._left:
            self._left = left
            self.leftVideoChanged.emit()
        if right != self._right:
            self._right = right
            self.rightVideoChanged.emit()
        self._apply_videos()
        self._log_video_origin()
        self.loadCalibration()
        if not same:
            self.clearPoints()
        if self._frame_count > 0:
            self.frameIndex = prev if same else 0

    @Slot()
    def resetForNewSession(self):
        """Vide l'écran de travail ; ne supprime ni vidéo ni annotation."""
        if self._playing:
            self._playing = False
            self._play_timer.stop()
            self.playingChanged.emit()
        self._service.clear_videos()
        self._invalidate_rect_mapping()
        self._left = ""
        self._right = ""
        self._frame_index = 0
        self._frame_count = 0
        self._frames_ready = False
        self.clearPoints()
        self.leftVideoChanged.emit()
        self.rightVideoChanged.emit()
        self.frameIndexChanged.emit()
        self.frameCountChanged.emit()
        self.previewTickChanged.emit()
        self.videoMetaChanged.emit()
