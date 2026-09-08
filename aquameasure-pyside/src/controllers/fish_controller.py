from __future__ import annotations

from bisect import bisect_left, bisect_right
import sys
from pathlib import Path

import cv2
import numpy as np
from PySide6.QtCore import QCoreApplication, Property, QObject, QThread, QTimer, Signal, Slot

from src.backend.tracking_worker import TrackingWorker
from src.controllers.measure_controller import MeasureController
from src.util import paths
from src.util.log_model import LogModel


# ── Suivi assiste In -> Out ───────────────────────────────────────────────────
# Le tracker suit seul ; des qu'il perd le poisson il rend la main, la lecture
# s'arrete sur la frame fautive et l'utilisateur retrace la bbox. Le suivi
# repart alors de ce rectangle.
#
# Quand le tracker ne redemarre pas du tout sur cette frame, le rectangle
# trace fait foi pour elle et on avance d'une image : l'utilisateur pointe
# alors image par image aussi longtemps qu'il le faut, sans jamais rester
# bloque. C'est le meme etat d'attente - inutile d'en distinguer un second.
ASSIST_IDLE = "idle"
ASSIST_RUNNING = "running"
ASSIST_WAITING = "waiting"   # perdu : on attend une bbox de l'utilisateur

# Recouvrement minimal pour considérer qu'une bbox de l'image et la bbox
# enregistrée d'une observation désignent le même poisson.
FOCUS_MATCH_IOU = 0.3

# Comete des pistes non travaillees : trois secondes a 30 img/s. Au-dela,
# l'ecran devient illisible des qu'il y a plusieurs poissons.
TRAIL_SPAN_FRAMES = 90
# Plafond de points d'un trajet peint. La piste travaillee montre tout son
# parcours ; a 30 img/s cela fait 400 points au bout de treize secondes, et
# repeindre plus que cela soixante fois par seconde coute plus cher que le
# decodage video lui-meme.
TRAIL_MAX_POINTS = 400


def _empty_focus_box() -> dict:
    return {
        "valid": False,
        "x1": 0.0,
        "y1": 0.0,
        "x2": 0.0,
        "y2": 0.0,
        "frame": -1,
        "label": "",
        "annId": "",
    }


def _box_iou(a: dict, b: dict) -> float:
    ix1 = max(float(a["x1"]), float(b["x1"]))
    iy1 = max(float(a["y1"]), float(b["y1"]))
    ix2 = min(float(a["x2"]), float(b["x2"]))
    iy2 = min(float(a["y2"]), float(b["y2"]))
    iw = ix2 - ix1
    ih = iy2 - iy1
    if iw <= 0 or ih <= 0:
        return 0.0
    inter = iw * ih
    area_a = max(0.0, float(a["x2"]) - float(a["x1"])) * max(0.0, float(a["y2"]) - float(a["y1"]))
    area_b = max(0.0, float(b["x2"]) - float(b["x1"])) * max(0.0, float(b["y2"]) - float(b["y1"]))
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


class _DetectWorker(QThread):
    finished_ok = Signal(list, object)
    failed = Signal(str)

    def __init__(self, frame_bgr: np.ndarray, conf: float, parent=None):
        super().__init__(parent)
        self._frame = frame_bgr
        self._conf = conf

    def run(self):
        try:
            import fish_detect
            import fish_detectors as fd

            reg = fd.registry()
            if not reg.is_available():
                spec = reg.active_spec()
                label = spec.label if spec else "Aucun modele"
                self.failed.emit(f"{label} - {reg.unavailable_reason()}")
                return
            # Chaine produit complete : detecteur actif, puis classification
            # Fishial de chaque crop. Appeler directement reg.detect() ici
            # court-circuitait silencieusement tout le second etage.
            boxes = fish_detect.detect_fish(
                self._frame,
                conf=self._conf,
                classify_species=True,
            )
            self.finished_ok.emit(boxes or [], self._frame)
        except Exception as exc:
            self.failed.emit(str(exc))


class _FishialWarmupWorker(QThread):
    """Charge le second etage Fishial hors du fil d'interface, une fois.

    Mesure dans ce venv : 7,75 s au PREMIER appel (chargement du modele),
    0,02 s ensuite. Payer ces huit secondes au milieu d'un lasso etait
    inacceptable ; on les paie ici, pendant que l'utilisateur cale sa video.

    Le worker ne fait rien d'autre que classer une vignette noire : c'est la
    meme chaine que la detection (`fishial_classify.classify_boxes`, galerie
    comprise), donc le meme moteur reste en cache derriere.
    """

    finished_ok = Signal(bool, str)

    def run(self):
        try:
            import fishial_classify as fc

            if not fc.is_available():
                self.finished_ok.emit(
                    False,
                    "poids Fishial absents (fish-vision/scripts/download_fishial.bat)",
                )
                return
            probe = np.zeros((64, 64, 3), dtype=np.uint8)
            result = fc.classify_boxes(
                probe, [{"x1": 0.0, "y1": 0.0, "x2": 63.0, "y2": 63.0}],
            )
            error = str(result[0].get("species_error") or "") if result else ""
            self.finished_ok.emit(not error, error)
        except Exception as exc:
            # Un prechauffage rate ne doit jamais empecher l'application de
            # servir : on retient la cause et on continue sans proposition.
            self.finished_ok.emit(False, str(exc))


def _initial_confidence() -> float:
    """Seuil par defaut du modele actif, avec repli si le registre echoue."""
    try:
        import fish_detectors as fd

        return float(fd.registry().confidence_for_active())
    except Exception:
        return 0.25


class FishController(QObject):
    fishIaEnabledChanged = Signal()
    autoOnPauseChanged = Signal()
    trackingEnabledChanged = Signal()
    showTrailsChanged = Signal()
    confidenceChanged = Signal()
    fishCountChanged = Signal()
    frameCountReadyChanged = Signal()
    statusTextChanged = Signal()
    busyChanged = Signal()
    detectionChanged = Signal()
    overlayChanged = Signal()
    selectedFishIndexChanged = Signal()
    focusBoxChanged = Signal()
    trackSelected = Signal(int)
    followingChanged = Signal()
    assistChanged = Signal()
    grazingWorkflowChanged = Signal()
    behaviorSeedChanged = Signal()

    def __init__(self, measure: MeasureController, parent=None):
        super().__init__(parent)
        self._measure = measure
        self._logs = LogModel(self)
        self._fish_ia = True
        self._auto_pause = True
        self._tracking = False
        self._show_trails = True
        self._confidence = _initial_confidence()
        self._fish_count = 0
        self._frame_count_ready = False
        self._status = ""
        self._busy = False
        self._last_boxes: list = []
        self._manual_by_frame: dict[int, list] = {}
        self._manual_boxes: list = []
        # Corrections de cadres IA, limitées à leur image et à la paire ouverte.
        # None masque une proposition supprimée ; un dict conserve sa taxonomie.
        self._auto_box_edits: dict[int, dict[tuple, dict | None]] = {}
        self._selected_fish_index = -1
        self._overlay_boxes: list[dict] = []
        self._overlay_boxes_right: list[dict] = []
        self._overlay_trails: list[dict] = []
        self._overlay_behavior_markers: list[dict] = []
        self._overlay_frame = -1
        self._focus_box: dict = _empty_focus_box()
        self._grazing_cache: list[dict] | None = None
        self._grazing_cache_path = ""
        self._grazing_index: dict[tuple[str, object], list[dict]] = {}
        self._grazing_index_source: list[dict] | None = None
        self._annotation_overlay_cache: dict | None = None
        self._annotation_overlay_cache_path = ""
        # Meme index, mais recale sur l'image brute affichee pendant la
        # lecture. Il est construit une fois par (video, calibration) et
        # jamais image par image : convertir des dizaines de milliers
        # d'echantillons soixante fois par seconde etait exclu.
        self._display_overlay_cache: dict | None = None
        self._display_overlay_token: tuple | None = None
        # Piste travaillee : son trajet reste visible pendant la lecture meme
        # sur les images ou elle n'a pas d'echantillon. Sans cela le trajet
        # clignotait des que le suivi avait un trou.
        self._pinned_track_key = ""
        self._following = False
        self._follow_lost_frame = -1
        self._follow_frames = 0
        # Suivi assiste In -> Out : machine a etats (voir _assist_*).
        self._assist_state = ASSIST_IDLE
        self._assist_start = -1
        self._assist_end = -1
        self._assist_cursor = -1
        self._assist_corrections = 0
        self._assist_message = ""
        self._assist_track_external_id = -1
        # Une seule machine à états, une seule issue. Il y en avait deux, que
        # départageait un drapeau `_track_only` : « Début / Fin et analyser »
        # écrivait toujours un intervalle de comportement, « In / Out » ne
        # rattachait que la piste. Le client n'a jamais voulu du premier
        # (« il fallait inventer une broute pour obtenir une piste ») : le
        # cycle et son drapeau ont disparu, le suivi ne fait plus qu'une
        # chose, produire la piste et la donner au poisson du registre.
        self._graze_state = "idle"
        self._graze_message = "Sélectionnez un poisson puis marquez le début."
        self._graze_behavior: tuple[str, str] | None = None
        self._graze_existing_track: dict | None = None
        self._graze_seed_box: dict | None = None
        # Observation du registre figée en même temps que la bbox d'amorce :
        # c'est elle qui recevra la piste produite par le suivi. La sélection
        # peut changer entre « Début » et « Fin », le lien ne doit pas suivre.
        self._graze_seed_ann_id = ""
        self._graze_start_abs = -1
        self._graze_end_abs = -1
        self._tracker = None
        self._worker: _DetectWorker | None = None
        # Second etage Fishial pour les bbox tracees a la main. None tant que
        # le prechauffage n'a pas repondu, True/False ensuite.
        self._fishial_ready: bool | None = None
        self._fishial_reason = ""
        self._fishial_warmup: _FishialWarmupWorker | None = None
        self._fishial_warmup_done = False
        app = QCoreApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self._finish_fishial_warmup)
        # « La fiche le dit une fois » : repeter l'absence de Fishial a chaque
        # rectangle transformerait un avertissement utile en bruit.
        self._fishial_notice_shown = False
        self._detect_seq = 0
        self._active_detect_seq = -1
        self._active_detect_frame: tuple[int, int] | None = None
        self._detect_rerun_pending = False
        self._active_follow_generation = -1
        self._active_worker_generation = -1
        self._active_persistence_generation = -1
        self._persistence_error_generation = -1
        self._active_range_generation = -1
        self._data = None
        self._auto_detect_timer = QTimer(self)
        self._auto_detect_timer.setSingleShot(True)
        self._auto_detect_timer.setInterval(350)
        self._auto_detect_timer.timeout.connect(self._run_auto_detect)
        # « Un poisson est-il désigné ? » se déduit de trois sources (bbox
        # choisie, bbox unique, ligne du registre affichée) : les boutons QML
        # ont besoin d'une seule notification, pas de recopier la règle.
        self.overlayChanged.connect(self.behaviorSeedChanged)
        self.selectedFishIndexChanged.connect(self.behaviorSeedChanged)
        self.focusBoxChanged.connect(self.behaviorSeedChanged)
        measure.frameIndexChanged.connect(self._on_frame_changed)
        measure.playingChanged.connect(self._on_play_state_changed)
        measure.leftVideoChanged.connect(self._on_videos_changed)
        measure.rightVideoChanged.connect(self._on_videos_changed)
        measure.framesUpdated.connect(self._on_measure_frames_updated)

    @Property(QObject, constant=True)
    def logs(self):
        return self._logs

    def set_data_controller(self, data) -> None:
        self._data = data
        signal = getattr(data, "grazingOverlayChanged", None)
        if signal is not None:
            signal.connect(self.invalidateGrazingCache)
        signal = getattr(data, "registryChanged", None)
        if signal is not None:
            signal.connect(self.invalidateGrazingCache)

    def _invalidate_box_selection(self) -> None:
        """Invalide Fish et Data avant de remplacer une collection de bbox."""
        if self._selected_fish_index >= 0:
            self.setSelectedFishIndex(-1)
        if self._data is not None and self._data.selectedBoxIndex >= 0:
            self._data.selectBoxIndex(-1)

    def _replace_last_boxes(self, boxes: list) -> None:
        self._invalidate_box_selection()
        self._last_boxes = self._apply_auto_box_edits(boxes)

    @staticmethod
    def _auto_box_key(box: dict) -> tuple:
        # Le nom proposé peut varier lors d'une nouvelle détection. Il ne fait
        # donc pas partie de l'identité du cadre dont l'utilisateur a pris la main.
        return tuple(box.get("_edit_source_key") or (
            str(box.get("db_track_id") or ""),
            int(box.get("track_id", -1) if box.get("track_id") is not None else -1),
            *(round(float(box.get(axis, 0)), 3) for axis in ("x1", "y1", "x2", "y2")),
        ))

    def _apply_auto_box_edits(self, boxes: list) -> list:
        edits = self._auto_box_edits.get(int(self._measure.frameIndex), {})
        if not edits:
            return list(boxes)
        result = []
        for box in boxes:
            corrected = edits.get(self._auto_box_key(box), box)
            if corrected is not None:
                result.append(dict(corrected))
        return result

    @Slot(int)
    def annotateBoxAtIndex(self, box_index: int):
        """Clic droit sur bbox - ajout au registre session (vue gauche, pause)."""
        if self._measure.playing:
            self._set_status("Mettez en pause pour ajouter au registre")
            return
        if box_index < 0:
            self._set_status("Aucune bbox sous le curseur")
            return
        self.setSelectedFishIndex(box_index)
        self._logs.append(f"Clic droit → registre (bbox #{box_index})")
        self._set_status(f"Ajout bbox #{box_index}…")
        if self._data is None:
            self._set_status("Registre indisponible")
            return
        self._data.addObservationAtBoxIndex(box_index)

    @Property(bool, notify=fishIaEnabledChanged)
    def fishIaEnabled(self):
        return self._fish_ia

    @fishIaEnabled.setter
    def fishIaEnabled(self, v: bool):
        if self._fish_ia != v:
            self._fish_ia = v
            self._auto_detect_timer.stop()
            self._detect_seq += 1
            if not v:
                self._replace_last_boxes([])
                self._publish_overlay()
                self._set_status("IA desactivee")
            self.fishIaEnabledChanged.emit()
            if v:
                self._schedule_auto_detect()

    @Property(bool, notify=autoOnPauseChanged)
    def autoOnPause(self):
        return self._auto_pause

    @autoOnPause.setter
    def autoOnPause(self, v: bool):
        if self._auto_pause != v:
            self._auto_pause = v
            self.autoOnPauseChanged.emit()
            if v:
                self._schedule_auto_detect()

    @Property(bool, notify=trackingEnabledChanged)
    def trackingEnabled(self):
        return self._tracking

    @trackingEnabled.setter
    def trackingEnabled(self, v: bool):
        if self._tracking != v:
            self._tracking = v
            self.trackingEnabledChanged.emit()
            if v:
                self._init_tracker()
            else:
                self._shutdown_tracker()

    @Property(bool, notify=showTrailsChanged)
    def showTrails(self):
        return self._show_trails

    @showTrails.setter
    def showTrails(self, v: bool):
        if self._show_trails != v:
            self._show_trails = v
            self.showTrailsChanged.emit()
            self._publish_overlay()

    @Property(float, notify=confidenceChanged)
    def confidence(self):
        return self._confidence

    @confidence.setter
    def confidence(self, v: float):
        v = max(0.05, min(0.95, float(v)))
        if abs(self._confidence - v) > 1e-6:
            self._confidence = v
            self.confidenceChanged.emit()

    @Property(int, notify=fishCountChanged)
    def fishCount(self):
        return self._fish_count

    @Property(bool, notify=frameCountReadyChanged)
    def frameCountReady(self):
        return self._frame_count_ready

    def _set_frame_count_ready(self, value: bool) -> None:
        value = bool(value)
        if self._frame_count_ready != value:
            self._frame_count_ready = value
            self.frameCountReadyChanged.emit()

    @Property(str, notify=statusTextChanged)
    def statusText(self):
        return self._status

    @Property(bool, notify=busyChanged)
    def busy(self):
        return self._busy

    @Property(int, notify=overlayChanged)
    def lastBoxCount(self):
        return len(self._overlay_boxes)

    @Property(list, notify=overlayChanged)
    def overlayBoxes(self):
        return self._overlay_boxes

    @Property(list, notify=overlayChanged)
    def overlayBoxesRight(self):
        return self._overlay_boxes_right

    @Property(list, notify=overlayChanged)
    def overlayTrails(self):
        return self._overlay_trails

    @Property(list, notify=overlayChanged)
    def overlayBehaviorMarkers(self):
        """Pictogrammes ponctuels sans bbox interactive correspondante."""
        return self._overlay_behavior_markers

    @Property(int, notify=selectedFishIndexChanged)
    def selectedFishIndex(self):
        return self._selected_fish_index

    @Property("QVariantMap", notify=focusBoxChanged)
    def focusBox(self):
        """BBox de l'observation ouverte depuis le registre - repere visuel persistant."""
        return self._focus_box

    @Slot(str, int, float, float, float, float, str)
    def focusAnnotationBox(
        self,
        ann_id: str,
        frame: int,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        label: str,
    ):
        self._focus_box = {
            "valid": True,
            "x1": float(min(x1, x2)),
            "y1": float(min(y1, y2)),
            "x2": float(max(x1, x2)),
            "y2": float(max(y1, y2)),
            "frame": int(frame),
            "label": str(label),
            "annId": str(ann_id),
        }
        self.focusBoxChanged.emit()
        self.overlayChanged.emit()
        self._apply_focus_selection()

    @Slot()
    def clearFocusBox(self):
        if not self._focus_box.get("valid"):
            return
        self._focus_box = _empty_focus_box()
        self.focusBoxChanged.emit()
        self.overlayChanged.emit()

    def _apply_focus_selection(self):
        """Aligne la selection courante sur la bbox de la base quand l'IA a detecte le poisson."""
        if not self._focus_box.get("valid"):
            return
        if self._overlay_frame != self._focus_box.get("frame"):
            return
        best_idx = -1
        best_iou = 0.0
        for i, box in enumerate(self._overlay_boxes):
            iou = _box_iou(self._focus_box, box)
            if iou > best_iou:
                best_idx, best_iou = i, iou
        if best_idx < 0 or best_iou < FOCUS_MATCH_IOU:
            self._invalidate_box_selection()
            return
        self.setSelectedFishIndex(best_idx)
        if self._data is not None:
            self._data.selectBoxIndex(best_idx)

    @Slot()
    def invalidateGrazingCache(self):
        """À appeler après toute écriture de comportement, ponctuel ou suivi."""
        self._grazing_cache = None
        self._grazing_index = {}
        self._grazing_index_source = None
        self._annotation_overlay_cache = None
        # Le miroir recale derive de ce cache-la : le laisser en place aurait
        # rejoue les positions d'avant l'ecriture, recalees mais perimees.
        self._display_overlay_cache = None
        self._display_overlay_token = None
        self._publish_overlay()

    def tracking_flusher(self):
        """Callable forçant l'écriture des pistes en attente, ou None.

        Le worker de tracking écrit par lots : au moment où l'opérateur ajoute
        une observation, la piste n'est pas encore en base et l'annotation
        partait sans `track_id`. On remet ce déclencheur au thread qui écrit
        l'observation, pour qu'il flushe juste avant d'insérer.
        """
        worker = self._tracker
        if worker is None or not worker.isRunning():
            return None
        return worker.flush_now

    def _grazing_intervals(self) -> list[dict]:
        """Intervalles de broute de la vidéo courante, relus au minimum.

        Cette liste était rechargée depuis SQLite à chaque publication
        d'overlay, donc à chaque frame.
        """
        path = self._measure.leftVideo
        if not path:
            return []
        if self._grazing_cache is not None and self._grazing_cache_path == path:
            return self._grazing_cache
        try:
            import fish_annotate as fa

            self._ensure_fv_path()
            rows = fa.list_grazing_intervals(path)
        except Exception:
            rows = []
        self._grazing_cache = rows
        self._grazing_cache_path = path
        return rows

    @staticmethod
    def _grazing_covers(row: dict, abs_frame: int) -> bool:
        """Intervalle de broute couvrant cette frame ABSOLUE.

        Les intervalles historiques (index timeline) sont convertis en amont
        par fish_annotate ; ceux qui ne sont pas convertibles ne sont pas
        compares, faute de quoi la surbrillance porterait sur une autre image.
        """
        start = row.get("frame_start_abs")
        end = row.get("frame_end_abs")
        if start is None or end is None:
            return False
        return start <= abs_frame <= end

    @staticmethod
    def _behavior_badge(row: dict) -> dict:
        """Format minimal et stable consommé par le Canvas QML."""
        return {
            "key": str(row.get("event_type", row.get("key", "")) or ""),
            "label": str(row.get("event_label", row.get("label", "")) or ""),
            "symbol": str(row.get("event_symbol", row.get("symbol", "●")) or "●"),
            "color": str(row.get("event_color", row.get("color", "#f59e0b")) or "#f59e0b"),
            "scope": str(row.get("scope", "interval") or "interval"),
        }

    def _active_interval_badges(
        self, db_track_id: str, external_track_id: int, abs_frame: int,
    ) -> list[dict]:
        badges: list[dict] = []
        seen: set[str] = set()
        rows = self._grazing_intervals()
        if self._grazing_index_source is not rows:
            index: dict[tuple[str, object], list[dict]] = {}
            for row in rows:
                track_db_id = str(row.get("track_db_id", "") or "")
                if track_db_id:
                    index.setdefault(("db", track_db_id), []).append(row)
                track_id = row.get("external_track_id")
                if track_id is not None:
                    index.setdefault(("external", int(track_id)), []).append(row)
            self._grazing_index = index
            self._grazing_index_source = rows
        candidates = self._grazing_index.get(("db", db_track_id), [])
        if not candidates and external_track_id >= 0:
            candidates = self._grazing_index.get(("external", external_track_id), [])
        for row in candidates:
            if not self._grazing_covers(row, abs_frame):
                continue
            badge = self._behavior_badge(row)
            key = badge["key"] or badge["label"]
            if key in seen:
                continue
            seen.add(key)
            badges.append(badge)
        return badges

    def _annotation_overlays(self) -> dict:
        """Index en mémoire pour une relecture fluide, sans SQL par frame."""
        path = self._measure.leftVideo
        if (
            self._annotation_overlay_cache is not None
            and self._annotation_overlay_cache_path == path
        ):
            return self._annotation_overlay_cache

        cache = {
            "tracksByFrame": {},
            "instantByFrame": {},
            "trackPoints": {},
            "identificationsByDb": {},
            "identificationsByExternal": {},
        }
        self._annotation_overlay_cache = cache
        self._annotation_overlay_cache_path = path
        if not path:
            return cache
        try:
            import fish_annotate as fa

            self._ensure_fv_path()
            payload = fa.list_video_annotation_overlays(path)
        except Exception as exc:
            self._logs.append(f"[!] Relecture annotations : {exc}")
            return cache

        for identity in payload.get("trackIdentifications", []):
            cache["identificationsByDb"][identity["trackDbId"]] = identity
            cache["identificationsByExternal"][identity["trackId"]] = identity

        for sample in payload.get("trackSamples", []):
            frame = int(sample.get("frameIndexAbs", -1))
            if frame < 0:
                continue
            box = {
                "x1": float(sample.get("x1", 0.0)),
                "y1": float(sample.get("y1", 0.0)),
                "x2": float(sample.get("x2", 0.0)),
                "y2": float(sample.get("y2", 0.0)),
                "track_id": int(sample.get("trackId", -1)),
                "db_track_id": str(sample.get("trackDbId", "") or ""),
                "cls_name": "fish",
                "conf": 0.0,
                "replay_track": True,
            }
            cache["tracksByFrame"].setdefault(frame, []).append(box)
            track_key = box["db_track_id"] or f"external:{box['track_id']}"
            points = cache["trackPoints"].setdefault(track_key, {
                "trackId": box["track_id"], "frames": [], "points": [],
            })
            points["frames"].append(frame)
            points["points"].append({
                "x": (box["x1"] + box["x2"]) * 0.5,
                "y": (box["y1"] + box["y2"]) * 0.5,
            })

        for marker in payload.get("instantAnnotations", []):
            frame = int(marker.get("frameIndexAbs", -1))
            if frame >= 0:
                cache["instantByFrame"].setdefault(frame, []).append(marker)
        return cache

    def _geometry_bbox_pixels(self, geometry: dict) -> tuple[float, float, float, float] | None:
        if not geometry:
            return None
        width = float(self._measure.frameWidth or 0)
        height = float(self._measure.frameHeight or 0)
        if width <= 0 or height <= 0:
            return None
        if "x_min" in geometry:
            values = (
                geometry.get("x_min"), geometry.get("y_min"),
                geometry.get("x_max"), geometry.get("y_max"),
            )
        elif "cx" in geometry:
            cx, cy = float(geometry.get("cx", 0)), float(geometry.get("cy", 0))
            bw, bh = float(geometry.get("w", 0)), float(geometry.get("h", 0))
            values = (cx - bw * 0.5, cy - bh * 0.5, cx + bw * 0.5, cy + bh * 0.5)
        else:
            return None
        if any(value is None for value in values):
            return None
        x1, y1, x2, y2 = (float(value) for value in values)
        units = str(geometry.get("units", "") or "").lower()
        normalized = units == "normalized" or (
            not units and max(abs(x1), abs(y1), abs(x2), abs(y2)) <= 1.5
        )
        if normalized:
            x1, x2 = x1 * width, x2 * width
            y1, y2 = y1 * height, y2 * height
        else:
            ref_width = float(geometry.get("ref_width", 0) or 0)
            ref_height = float(geometry.get("ref_height", 0) or 0)
            if ref_width > 0 and ref_height > 0:
                x1, x2 = x1 * width / ref_width, x2 * width / ref_width
                y1, y2 = y1 * height / ref_height, y2 * height / ref_height
        return self._clamp_to_frame(x1, y1, x2, y2)

    def _instant_behavior_markers(
        self, boxes: list[dict], cache: dict, abs_frame: int,
    ) -> list[dict]:
        """Colle un picto à la bbox suivie, sinon garde la bbox annotée."""
        markers: list[dict] = []
        remap = (
            self._measure.rect_mapping(True)
            if self._measure.overlay_remap_active() else None
        )
        for row in cache["instantByFrame"].get(abs_frame, []):
            bbox = self._geometry_bbox_pixels(row.get("geometry", {}))
            if bbox is None:
                continue
            x1, y1, x2, y2 = bbox
            if remap is not None:
                # Les annotations ponctuelles sont enregistrees en pixels
                # rectifies comme le reste. Il y en a au plus deux ou trois
                # par image : les convertir ici coute moins que d'en tenir un
                # second index, et l'appariement par recouvrement avec les
                # boites deja recalees ne marcherait pas autrement.
                x1, y1, x2, y2 = remap.map_box(x1, y1, x2, y2)
            candidate = {
                "x1": x1, "y1": y1, "x2": x2, "y2": y2,
                "trackId": int(row.get("trackId", -1)),
                "dbTrackId": str(row.get("trackDbId", "") or ""),
                "annotationId": str(row.get("annotationId", "") or ""),
            }
            match = -1
            for index, box in enumerate(boxes):
                same_db = candidate["dbTrackId"] and (
                    candidate["dbTrackId"] == box.get("dbTrackId", "")
                )
                same_external = (
                    candidate["trackId"] >= 0
                    and candidate["trackId"] == box.get("trackId", -1)
                    and not (candidate["dbTrackId"] and box.get("dbTrackId", ""))
                )
                if same_db or same_external or _box_iou(candidate, box) >= 0.35:
                    match = index
                    break
            badge = self._behavior_badge(row.get("behavior", {}))
            if match >= 0:
                current = list(boxes[match].get("behaviorBadges", []))
                if not any(item.get("key") == badge["key"] for item in current):
                    current.append(badge)
                boxes[match]["behaviorBadges"] = current
                continue
            marker_match = next((
                marker for marker in markers
                if (
                    candidate["annotationId"]
                    and candidate["annotationId"] == marker.get("annotationId", "")
                ) or _box_iou(candidate, marker) >= 0.9
            ), None)
            if marker_match is not None:
                current = list(marker_match.get("behaviorBadges", []))
                if not any(item.get("key") == badge["key"] for item in current):
                    current.append(badge)
                marker_match["behaviorBadges"] = current
            else:
                markers.append({**candidate, "behaviorBadges": [badge]})
        return markers

    @staticmethod
    def _persisted_trails(
        cache: dict, boxes: list[dict], abs_frame: int, span: int = TRAIL_SPAN_FRAMES,
        pinned_key: str = "", max_points: int = TRAIL_MAX_POINTS,
    ) -> list[dict]:
        start = int(abs_frame) - max(0, int(span))
        trails: list[dict] = []
        visible_keys = {
            str(box.get("db_track_id", "") or "")
            for box in boxes
            if box.get("db_track_id")
        }
        pinned_key = str(pinned_key or "")
        # La piste travaillee reste dans la liste meme sans echantillon sur
        # cette image : le suivi a des trous, et le trajet ne doit pas
        # clignoter a chacun d'eux pendant la lecture.
        if pinned_key:
            visible_keys.add(pinned_key)
        for track_key in visible_keys:
            row = cache["trackPoints"].get(track_key)
            if row is None:
                continue
            frames = row["frames"]
            # « Qu'on voie le trajet s'afficher en live » : la piste travaillee
            # montre tout son parcours depuis son debut, qui se prolonge image
            # apres image. Les autres gardent la comete de trois secondes,
            # sinon l'ecran devient illisible des qu'il y a du monde.
            left = 0 if track_key == pinned_key else bisect_left(frames, start)
            right = bisect_right(frames, int(abs_frame))
            points = row["points"][left:right]
            if len(points) < 2:
                continue
            if len(points) > max_points:
                # Un trajet de plusieurs milliers de points repeint soixante
                # fois par seconde couterait plus cher que le decodage video.
                # On garde la forme, pas chaque point - et toujours le
                # dernier, celui qui dit ou est le poisson maintenant.
                stride = (len(points) + max_points - 1) // max_points
                sampled = points[::stride]
                if sampled[-1] is not points[-1]:
                    sampled.append(points[-1])
                points = sampled
            trails.append({"trackId": row["trackId"], "points": points})
        return trails

    # ── Overlay recale sur l'image reellement affichee ─────────────────

    def _display_overlays(self) -> dict:
        """L'index d'annotations, dans l'espace de l'image affichee.

        A l'arret la vue montre l'image rectifiee : c'est deja le bon espace,
        on rend l'index tel quel, sans copie ni conversion. Pendant la lecture
        elle montre la video brute, et il faut le meme index recale. Il est
        construit une fois par (video, calibration), jamais par image.
        """
        cache = self._annotation_overlays()
        if not self._measure.overlay_remap_active():
            return cache
        token = (
            self._annotation_overlay_cache_path,
            int(self._measure.rect_mapping_token()),
        )
        if (
            self._display_overlay_cache is not None
            and self._display_overlay_token == token
        ):
            return self._display_overlay_cache
        built = self._build_display_overlays(cache)
        self._display_overlay_cache = built
        self._display_overlay_token = token
        return built

    def _build_display_overlays(self, cache: dict) -> dict:
        mapping = self._measure.rect_mapping(True)
        flat: list[dict] = []
        for frame_boxes in cache["tracksByFrame"].values():
            flat.extend(frame_boxes)
        converted = mapping.map_boxes(
            [(b["x1"], b["y1"], b["x2"], b["y2"]) for b in flat]
        ) if flat else []
        tracks_by_frame: dict[int, list[dict]] = {}
        cursor = 0
        for frame, frame_boxes in cache["tracksByFrame"].items():
            rows = []
            for box in frame_boxes:
                x1, y1, x2, y2 = converted[cursor]
                cursor += 1
                rows.append({**box, "x1": x1, "y1": y1, "x2": x2, "y2": y2})
            tracks_by_frame[frame] = rows
        track_points: dict[str, dict] = {}
        for key, row in cache["trackPoints"].items():
            moved = mapping.map_points([(p["x"], p["y"]) for p in row["points"]])
            track_points[key] = {
                "trackId": row["trackId"],
                "frames": row["frames"],
                "points": [{"x": x, "y": y} for x, y in moved],
            }
        # Les annotations ponctuelles gardent leur geometrie d'origine : elles
        # sont peu nombreuses par image et converties au moment de peindre.
        return {
            "tracksByFrame": tracks_by_frame,
            "instantByFrame": cache["instantByFrame"],
            "trackPoints": track_points,
        }

    @Slot(str)
    def pinTrackTrail(self, db_track_id: str):
        """Designe la piste dont le trajet reste visible pendant la lecture."""
        key = str(db_track_id or "")
        if key == self._pinned_track_key:
            return
        self._pinned_track_key = key
        if self._measure.playing:
            self._publish_playback_overlay()

    @Property(str, notify=overlayChanged)
    def pinnedTrackId(self):
        return self._pinned_track_key

    def _validated_track_label(self, db_id: str, external_id: int) -> tuple[str, str]:
        # Les identifications relues sont chargees avec les pistes, jamais en
        # SQL par image. Une prediction Fishial ne remplace pas cette autorite.
        cache = self._annotation_overlays()
        identity = (
            cache.get("identificationsByDb", {}).get(db_id) if db_id
            else cache.get("identificationsByExternal", {}).get(external_id)
        )
        if identity:
            if identity.get("status") == "ambiguous":
                return "Identification à revoir", "ambiguous"
            if identity.get("name"):
                return str(identity["name"]), "validated"
        # Pendant le calcul du suivi, la piste n'est pas encore rattachee en
        # base. Seul l'identifiant logique du poisson engage recoit son taxon.
        if (self._graze_seed_ann_id and self._data is not None
                and self._graze_state in ("tracking", "waiting")
                and external_id >= 0 and external_id == self._assist_track_external_id):
            from types import SimpleNamespace
            from src.annodb.identification import is_authoritative_identification
            index = self._data._registry_index_by_ann_id(self._graze_seed_ann_id)
            row = self._data.registry.row_at(index) if index >= 0 else {}
            if is_authoritative_identification(SimpleNamespace(**row)):
                name = row.get("species") or row.get("genus") or row.get("family")
                if name:
                    return str(name), "validated"
        return "", ""

    def _boxes_for_qml(self, boxes: list) -> list[dict]:
        frame = self._measure.leftAbsFrame

        out = []
        for b in boxes:
            db_id = str(b.get("db_track_id", "") or "")
            external_id = b.get("track_id", b.get("trackId", -1))
            external_id = int(external_id) if external_id is not None else -1
            identity_name, identity_source = self._validated_track_label(db_id, external_id)
            behavior_badges = self._active_interval_badges(db_id, external_id, frame)
            manual_grazing = any(
                badge.get("key") == "grazing" for badge in behavior_badges
            )
            out.append({
                "x1": float(b.get("x1", 0)),
                "y1": float(b.get("y1", 0)),
                "x2": float(b.get("x2", 0)),
                "y2": float(b.get("y2", 0)),
                "trackId": external_id,
                "conf": float(b.get("conf", 0) or 0),
                "clsName": str(b.get("cls_name", b.get("clsName", "fish"))),
                "dbTrackId": db_id,
                "speciesName": identity_name or str(b.get("species_name", b.get("speciesName", "")) or ""),
                "identificationSource": identity_source,
                "speciesConf": 0.0 if identity_source else float(b.get("species_conf", b.get("speciesConf", 0)) or 0),
                "speciesError": str(b.get("species_error", b.get("speciesError", "")) or ""),
                "manualGrazing": manual_grazing,
                "manualDraw": bool(b.get("manual_draw", False)),
                "stereoProjected": bool(b.get("stereo_projected", False)),
                "replayTrack": bool(b.get("replay_track", False)),
                "behaviorBadges": behavior_badges,
            })
        return out

    def _ensure_repo_path(self):
        root = paths.app_root()
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))

    def _combined_boxes(self) -> list:
        return list(self._last_boxes) + list(self._manual_boxes)

    def _load_manual_boxes_for_frame(self):
        fi = self._measure.frameIndex
        self._manual_boxes = list(self._manual_by_frame.get(fi, []))

    @Slot(int, result=bool)
    def isManualBoxIndex(self, index: int) -> bool:
        return 0 <= index < len(self._combined_boxes()) and index >= len(self._last_boxes)

    def _clamp_to_frame(
        self, x1: float, y1: float, x2: float, y2: float
    ) -> tuple[float, float, float, float]:
        """Borne une bbox aux dimensions de la frame affichee.

        Un glisser-deposer qui sort du cadre produisait des coordonnees
        negatives ou superieures a la taille de l'image : la base gardait une
        boite qui n'existe pas, et c'est l'export qui la rattrapait - trop tard
        pour que quiconque le remarque. L'audit `audit_bbox_bounds.py` a montre
        que la saisie manuelle n'etait pas la cause des cas historiques, mais
        rien n'empechait qu'elle le devienne.

        Sans dimensions connues (aucune video chargee), on ne borne pas :
        inventer une taille serait pire que ne rien faire.
        """
        width = int(self._measure.frameWidth or 0)
        height = int(self._measure.frameHeight or 0)
        if width <= 0 or height <= 0:
            return x1, y1, x2, y2
        return (
            min(max(x1, 0.0), float(width)),
            min(max(y1, 0.0), float(height)),
            min(max(x2, 0.0), float(width)),
            min(max(y2, 0.0), float(height)),
        )

    # ── Espece proposee sur une bbox tracee a la main ────────────
    # Une bbox au lasso ne passe pas par `fish_detect.detect_fish`, donc jamais
    # par le second etage Fishial : sa fiche n'avait aucune proposition. C'est
    # pourtant le cas « poisson rate par l'IA », celui ou la proposition sert
    # le plus. On rejoue donc ici exactement ce que fait `detect_fish` apres le
    # detecteur - `fishial_classify.classify_boxes`, galerie comprise - et rien
    # de plus : surtout pas `propose_taxon_from_box`, qui appelle
    # `ensure_fishial_taxonomy_if_needed()` et peut partir TELECHARGER un
    # referentiel au milieu d'un geste.

    def warm_fishial(self) -> None:
        """Charge le modele Fishial en tache de fond, une seule fois.

        Declenche a l'ouverture d'une paire de videos : le premier appel coute
        pres de 8 s, personne ne doit les payer en tracant un rectangle. Un
        echec n'interrompt rien, il retire seulement la proposition d'espece.
        """
        if self._fishial_warmup_done:
            return
        self._fishial_warmup_done = True
        worker = _FishialWarmupWorker(self)
        worker.finished_ok.connect(self._on_fishial_warm)
        self._fishial_warmup = worker
        worker.start()

    def _finish_fishial_warmup(self) -> None:
        worker = self._fishial_warmup
        if worker is not None and worker.isRunning():
            worker.wait()

    def _on_fishial_warm(self, ready: bool, reason: str) -> None:
        self._fishial_ready = bool(ready)
        self._fishial_reason = str(reason or "")
        if ready:
            self._logs.append("Fishial prêt pour les bbox tracées à la main")
        else:
            self._logs.append(f"[!] Fishial indisponible : {self._fishial_reason}")

    def _frame_for_classification(self) -> np.ndarray | None:
        """Image gauche courante, sans rouvrir la video si on peut l'eviter.

        `_read_left_frame` retombe sur un `cv2.VideoCapture` neuf quand il n'y
        a pas de calibration : ouvrir et repositionner un fichier de plusieurs
        Go dans un geste interactif se sent. Le service, lui, garde ses
        lecteurs ouverts et son cache de paires rectifiees.
        """
        index = self._measure.frameIndex
        try:
            rect_l, _r, _p1, _p2 = self._measure.rectified_pair(index)
            if rect_l is not None:
                return rect_l
        except Exception:
            pass
        raw_pair = getattr(self._measure, "raw_pair", None)
        if callable(raw_pair):
            try:
                left, _right = raw_pair(index)
                if left is not None:
                    return left
            except Exception:
                pass
        try:
            return self._read_left_frame()
        except Exception:
            # Aucune image lisible : la bbox reste utilisable, simplement sans
            # proposition d'espece. Un lasso ne doit jamais lever.
            return None

    def _classify_manual_box(self, box: dict) -> dict:
        """Enrichit une bbox manuelle de son espece proposee - aucune ecriture.

        La boite reste un brouillon jusqu'a « Enregistrer le poisson » : ici on
        ne touche ni la base, ni le referentiel de taxonomie.
        """
        if self._fishial_ready is False:
            return box
        frame = self._frame_for_classification()
        if frame is None:
            return box
        try:
            import fishial_classify as fc

            if not fc.is_available():
                self._fishial_ready = False
                self._fishial_reason = "poids Fishial absents"
                return box
            enriched = fc.classify_boxes(frame, [box])
        except Exception as exc:
            self._fishial_ready = False
            self._fishial_reason = str(exc)
            self._logs.append(f"[!] Espèce proposée (bbox manuelle) : {exc}")
            return box
        self._fishial_ready = True
        if not enriched:
            return box
        out = dict(enriched[0])
        if out.get("species_error"):
            self._fishial_reason = str(out["species_error"])
            self._fishial_ready = False
        species = str(out.get("species_name") or "").strip()
        if species:
            self._logs.append(
                f"BBox manuelle : espèce proposée « {species} » "
                f"({float(out.get('species_conf') or 0):.2f})"
            )
        return out

    def _fishial_notice(self) -> str:
        """Avertissement d'indisponibilite, une seule fois par session."""
        if self._fishial_ready is not False or self._fishial_notice_shown:
            return ""
        self._fishial_notice_shown = True
        reason = self._fishial_reason or "moteur indisponible"
        return f" · Fishial indisponible ({reason}) : aucune espèce proposée"

    @Slot(float, float, float, float)
    def addManualBox(self, x1: float, y1: float, x2: float, y2: float):
        if self._measure.playing:
            self._set_status("Mettez en pause pour encadrer un poisson")
            return
        x1, x2 = sorted((float(x1), float(x2)))
        y1, y2 = sorted((float(y1), float(y2)))
        x1, y1, x2, y2 = self._clamp_to_frame(x1, y1, x2, y2)
        if x2 - x1 < 6 or y2 - y1 < 6:
            return
        box = {
            "x1": x1,
            "y1": y1,
            "x2": x2,
            "y2": y2,
            "track_id": -1,
            "cls_name": "fish",
            "conf": 0.0,
            "manual_draw": True,
        }
        box = self._classify_manual_box(box)
        fi = self._measure.frameIndex
        rows = list(self._manual_by_frame.get(fi, []))
        rows.append(box)
        self._manual_by_frame[fi] = rows
        self._manual_boxes = rows
        self._set_frame_count_ready(True)
        idx = len(self._last_boxes) + len(rows) - 1
        self.setSelectedFishIndex(idx)
        self._publish_overlay()
        self._logs.append(f"BBox manuelle frame {fi} : ({x1:.0f},{y1:.0f})-({x2:.0f},{y2:.0f})")
        if self._assist_on_manual_box():
            self._set_status("Reprise du suivi depuis votre rectangle…")
        else:
            species = str(box.get("species_name") or "").strip()
            proposal = f" · espèce proposée : {species}" if species else ""
            self._set_status(
                "BBox manuelle sélectionnée - mesurez puis enregistrez"
                f"{proposal}{self._fishial_notice()}"
            )

    def _manual_row_index(self, box_index: int) -> int:
        """Convertit l'index de l'overlay en index de bbox manuelle."""
        manual_index = int(box_index) - len(self._last_boxes)
        if 0 <= manual_index < len(self._manual_boxes):
            return manual_index
        return -1

    @Slot(int, float, float, float, float, result=bool)
    def updateManualBox(
        self, box_index: int, x1: float, y1: float, x2: float, y2: float
    ) -> bool:
        """Entrée historique, réservée aux cadres tracés à la main."""
        if self._manual_row_index(box_index) < 0:
            return False
        return self.updateBox(box_index, x1, y1, x2, y2)

    @Slot(int, float, float, float, float, result=bool)
    def updateBox(
        self, box_index: int, x1: float, y1: float, x2: float, y2: float
    ) -> bool:
        """Corrige la géométrie du cadre sans relancer son identification."""
        if self._measure.playing:
            self._set_status("Mettez en pause pour modifier une bbox")
            return False
        boxes = self._combined_boxes()
        if not 0 <= box_index < len(boxes):
            return False

        if not all(np.isfinite(value) for value in (x1, y1, x2, y2)):
            return False
        x1, x2 = sorted((float(x1), float(x2)))
        y1, y2 = sorted((float(y1), float(y2)))
        x1, y1, x2, y2 = self._clamp_to_frame(x1, y1, x2, y2)
        if x2 - x1 < 6 or y2 - y1 < 6:
            self._set_status("BBox trop petite - modification annulee")
            return False

        fi = self._measure.frameIndex
        box = dict(boxes[box_index])
        source_key = self._auto_box_key(box)
        box.update({"x1": x1, "y1": y1, "x2": x2, "y2": y2})
        if self._data is not None:
            self._data.invalidateMeasurementForBoxEdit(int(box_index), box)
        manual_index = self._manual_row_index(box_index)
        if manual_index >= 0:
            rows = list(self._manual_boxes)
            rows[manual_index] = box
            self._manual_by_frame[fi] = rows
            self._manual_boxes = rows
        else:
            box["_edit_source_key"] = source_key
            self._auto_box_edits.setdefault(fi, {})[source_key] = box
            self._last_boxes = list(self._last_boxes)
            self._last_boxes[box_index] = box
        self._set_frame_count_ready(True)
        self.setSelectedFishIndex(int(box_index))
        self._publish_overlay()
        self._logs.append(
            f"BBox modifiee frame {fi} (identification conservee) : "
            f"({x1:.0f},{y1:.0f})-({x2:.0f},{y2:.0f})"
        )
        self._set_status("Bbox modifiée - mesurez à nouveau")
        return True

    @Slot(int, result=bool)
    def removeManualBox(self, box_index: int) -> bool:
        """Entrée historique, réservée aux cadres tracés à la main."""
        if self._manual_row_index(box_index) < 0:
            return False
        return self.removeBox(box_index)

    @Slot(int, result=bool)
    def removeBox(self, box_index: int) -> bool:
        """Retire un cadre de l'image, comme pour un rectangle manuel."""
        if self._measure.playing:
            self._set_status("Mettez en pause pour supprimer une bbox")
            return False
        boxes = self._combined_boxes()
        if not 0 <= box_index < len(boxes):
            return False

        fi = self._measure.frameIndex
        self._invalidate_box_selection()
        manual_index = self._manual_row_index(box_index)
        if manual_index >= 0:
            rows = list(self._manual_boxes)
            rows.pop(manual_index)
            if rows:
                self._manual_by_frame[fi] = rows
            else:
                self._manual_by_frame.pop(fi, None)
            self._manual_boxes = rows
        else:
            key = self._auto_box_key(boxes[box_index])
            self._auto_box_edits.setdefault(fi, {})[key] = None
            self._last_boxes = list(self._last_boxes)
            self._last_boxes.pop(box_index)
        self._set_frame_count_ready(True)
        self.setSelectedFishIndex(-1)
        self._publish_overlay()
        self._logs.append(f"BBox supprimee frame {fi}")
        self._set_status("BBox supprimée")
        return True

    @Slot()
    def refreshOverlay(self):
        self._publish_overlay()

    @Slot(str)
    def onDetectorChanged(self, detector_id: str):
        """Le modele actif a change : on repart d'une detection propre."""
        import fish_detectors as fd

        self._auto_detect_timer.stop()
        self._detect_seq += 1
        self._replace_last_boxes([])
        self._shutdown_tracker()
        self._publish_overlay()

        spec = fd.registry().spec(detector_id)
        if spec is not None:
            self.confidence = spec.default_conf
            self._set_status(f"Modele « {spec.label} » actif")
            self._logs.append(f"Detecteur : {spec.label} ({spec.backend})")
        if self._tracking:
            self._init_tracker()
        self._schedule_auto_detect()

    @Slot(int, result="QVariantMap")
    def lastBoxAtIndex(self, index: int):
        boxes = self._combined_boxes()
        if 0 <= index < len(boxes):
            return self._boxes_for_qml([boxes[index]])[0]
        return {}

    def _publish_overlay(self):
        if self._measure.playing:
            self._publish_playback_overlay()
            return
        abs_frame = int(self._measure.leftAbsFrame)
        annotation_cache = self._annotation_overlays()
        boxes = self._last_boxes
        cached = None
        if self._tracker is not None and (
            self._tracking
            or self._following
            or self._assist_state != ASSIST_IDLE
        ):
            cached = self._tracker.cached_boxes(abs_frame)
        if cached:
            if self._apply_auto_box_edits(cached) != self._last_boxes:
                self._replace_last_boxes(cached)
            boxes = self._last_boxes
        combined = list(boxes) + list(self._manual_boxes)
        qml_boxes = self._boxes_for_qml(combined)
        self._overlay_behavior_markers = self._instant_behavior_markers(
            qml_boxes, annotation_cache, abs_frame,
        )
        self._overlay_boxes = qml_boxes
        # Encadrement IA uniquement sur la vue gauche : la projection stereo
        # vers la droite (matching disparite par bbox) ralentissait l'affichage.
        # La mesure stereo reste disponible a la demande via
        # measureSelectedBoxLength (match ponctuel des points A/B seulement).
        self._overlay_boxes_right = []
        trails_out: list[dict] = []
        if self._tracker is not None and self._show_trails:
            trails_out = self._tracker.trails(abs_frame)
        self._overlay_trails = trails_out
        self._overlay_frame = self._measure.frameIndex
        self._fish_count = len(self._overlay_boxes)
        self.fishCountChanged.emit()
        self.overlayChanged.emit()
        self._apply_focus_selection()

    def _publish_playback_overlay(self):
        """Rejoue le cache persistant sans toucher aux chemins d'analyse.

        Cette voie courte est appelée par le playhead natif : aucune lecture
        SQLite, aucun accès au worker de tracking, aucune image OpenCV et aucun
        calcul de rectification ne doivent s'y produire.
        """
        abs_frame = int(self._measure.leftAbsFrame)
        # L'image affichee pendant la lecture est la video brute, pas l'image
        # rectifiee : les coordonnees enregistrees doivent etre recalees, sans
        # quoi tout l'overlay tombe a cote du poisson.
        annotation_cache = self._display_overlays()
        boxes = annotation_cache["tracksByFrame"].get(abs_frame, [])
        qml_boxes = self._boxes_for_qml(boxes)
        self._overlay_behavior_markers = self._instant_behavior_markers(
            qml_boxes, annotation_cache, abs_frame,
        )
        self._overlay_boxes = qml_boxes
        self._overlay_boxes_right = []
        self._overlay_trails = (
            self._persisted_trails(
                annotation_cache, boxes, abs_frame,
                pinned_key=self._pinned_track_key,
            )
            if self._show_trails else []
        )
        self._overlay_frame = self._measure.frameIndex
        fish_count = len(qml_boxes)
        if fish_count != self._fish_count:
            self._fish_count = fish_count
            self.fishCountChanged.emit()
        self.overlayChanged.emit()

    @Slot(int)
    def setSelectedFishIndex(self, index: int):
        idx = int(index)
        if self._selected_fish_index != idx:
            self._selected_fish_index = idx
            self.selectedFishIndexChanged.emit()
            self.overlayChanged.emit()

    @Slot(int, int)
    def selectTrackFromBox(self, track_id: int, box_index: int):
        self.setSelectedFishIndex(box_index)
        track_id = int(track_id)
        self.trackSelected.emit(track_id)
        self._set_status(
            f"Piste #{track_id} selectionnee"
            if track_id >= 0 else "Cadre selectionne (sans piste)"
        )

    def _set_busy(self, v: bool):
        if self._busy != v:
            self._busy = v
            self.busyChanged.emit()

    def _set_status(self, msg: str):
        self._status = msg
        self.statusTextChanged.emit()

    def _repo(self) -> Path:
        return paths.app_root()

    def _ensure_fv_path(self):
        fv = self._repo() / "fish-vision"
        if fv.is_dir() and str(fv) not in sys.path:
            sys.path.insert(0, str(fv))

    def current_frame(self) -> np.ndarray | None:
        """Frame gauche courante (rectifiee si la calibration est chargee)."""
        return self._read_left_frame()

    def _read_left_frame(self) -> np.ndarray | None:
        rect_l, _, _, _ = self._measure.rectified_pair(self._measure.frameIndex)
        if rect_l is not None:
            return rect_l
        left = self._measure.leftVideo
        if not left:
            return None
        cap = cv2.VideoCapture(left)
        if not cap.isOpened():
            return None
        abs_f = self._measure.leftAbsFrame
        cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, abs_f))
        ok, frame = cap.read()
        cap.release()
        return frame if ok else None

    def _on_videos_changed(self):
        # Une paire de videos vient de s'ouvrir : c'est le moment de charger le
        # modele Fishial, pendant que l'utilisateur cale sa lecture. Sinon la
        # premiere bbox tracee a la main paierait les 8 s de chargement.
        if self._measure.leftVideo:
            self.warm_fishial()
        self._auto_detect_timer.stop()
        self._detect_seq += 1
        self._replace_last_boxes([])
        self._manual_by_frame = {}
        self._manual_boxes = []
        self._auto_box_edits = {}
        self._grazing_cache = None
        self._grazing_cache_path = ""
        self._grazing_index = {}
        self._grazing_index_source = None
        self._annotation_overlay_cache = None
        self._annotation_overlay_cache_path = ""
        self._display_overlay_cache = None
        self._display_overlay_token = None
        self._pinned_track_key = ""
        self._shutdown_tracker()
        self.clearFocusBox()
        self._publish_overlay()

    def _on_measure_frames_updated(self):
        if self._fish_ia and not self._measure.playing and not self._tracking:
            self._schedule_auto_detect()

    def _schedule_auto_detect(self):
        if (
            not self._fish_ia
            or self._measure.playing
            or self._tracking
            or not self._auto_pause
            or not self._measure.leftVideo
        ):
            return
        self._auto_detect_timer.start()

    def _on_play_state_changed(self):
        self._auto_detect_timer.stop()
        self._detect_seq += 1
        self._set_frame_count_ready(False)
        self._detect_rerun_pending = False
        if self._measure.playing:
            # Play est un transport pur : aucune analyse standard ne continue
            # et aucun résultat déjà lancé ne peut repeupler l'overlay.
            if self._worker is not None and self._worker.isRunning():
                self._worker.requestInterruption()
            self._active_detect_seq = -1
            if self._tracker is not None:
                self._tracker.cancel()
            self._invalidate_tracking_generations()
            if self._tracking:
                self._tracking = False
                self.trackingEnabledChanged.emit()
            if self._following or self._assist_state != ASSIST_IDLE:
                self._following = False
                self.followingChanged.emit()
                self._set_assist(ASSIST_IDLE, "")
            if self._graze_state in ("tracking", "waiting"):
                self._graze_seed_ann_id = ""
                self._set_grazing_workflow(
                    "cancelled", "Analyse annulée par la lecture."
                )
            self._set_busy(False)
            # Comme aquameasure.py : pas de bbox IA en lecture → fluide.
            self._replace_last_boxes([])
            self._publish_overlay()
            self._set_status("")
            return
        # Retour en pause : la frame d'arrivee n'a rien recalcule pendant la
        # lecture, il faut donc rejouer ici ce que _on_frame_changed a saute.
        self._load_manual_boxes_for_frame()
        if (
            self._focus_box.get("valid")
            and self._focus_box.get("frame") != self._measure.frameIndex
        ):
            self.clearFocusBox()
        self._publish_overlay()
        if not self._fish_ia:
            return
        if self._tracking and self._tracker is not None:
            self._run_track_frame()
        else:
            self._schedule_auto_detect()

    def _run_auto_detect(self):
        if self._busy:
            self._detect_rerun_pending = True
            return
        if not self._fish_ia or self._measure.playing or self._tracking:
            return
        self.detectCurrentFrame()

    def _init_tracker(self):
        if self._tracker is not None:
            return
        left = self._measure.leftVideo
        if not left:
            self._logs.append("[!] Chargez les videos avant d'activer le tracking")
            return
        try:
            from fish_track import is_available

            if not is_available():
                self._logs.append("[!] Tracking indisponible")
                return
        except Exception as exc:
            self._logs.append(f"[!] Tracking : {exc}")
            return

        self._ensure_fv_path()
        # Les bbox doivent tomber sur l'image rectifiée affichée, pas sur la
        # frame brute : sans calibration chargée elles seraient décalées.
        rectifier = self._measure.left_rectifier()
        if rectifier is None:
            self._measure.loadCalibration()
            rectifier = self._measure.left_rectifier()
        if rectifier is None:
            self._logs.append("[!] Sans calibration, les bbox suivent l'image brute")

        worker = TrackingWorker(self)
        worker.frameTracked.connect(self._on_frame_tracked)
        worker.trackerReset.connect(self._on_tracker_reset)
        worker.rangeProgress.connect(self._on_range_progress)
        worker.rangeFinished.connect(self._on_range_finished)
        worker.followProgress.connect(self._on_follow_progress)
        worker.followLost.connect(self._on_follow_lost)
        worker.followFinished.connect(self._on_follow_finished)
        worker.failed.connect(self._on_track_failed)
        worker.persistenceFinished.connect(self._on_persistence_finished)
        worker.start()
        self._active_worker_generation = worker.configure(
            left,
            conf=self._confidence,
            persist_db=True,
            transform=rectifier,
        )
        self._tracker = worker
        self._logs.append("Tracking demarre (thread dedie, ecriture par lot)")

    def _shutdown_tracker(self):
        worker = self._tracker
        self._tracker = None
        self._active_follow_generation = -1
        self._active_worker_generation = -1
        self._active_range_generation = -1
        self._active_persistence_generation = -1
        self._persistence_error_generation = -1
        if worker is None:
            return
        worker.stop()
        if not worker.wait(4000):
            self._logs.append("[!] Arret du tracking plus long que prevu")

    def _on_frame_tracked(self, generation: int, abs_frame: int, boxes: list):
        if generation != self._active_worker_generation:
            return
        self._active_worker_generation = -1
        if self._measure.playing or not self._tracking:
            return
        if abs_frame != self._measure.leftAbsFrame:
            # L'utilisateur a bougé entre-temps : le résultat reste en cache
            # pour un retour, et on réaffiche ce qui correspond à la frame vue.
            self._publish_overlay()
            return
        self._set_frame_count_ready(True)
        self._replace_last_boxes(boxes)
        self._publish_overlay()
        self._set_status(f"{len(boxes)} piste(s) suivie(s)")

    def _on_tracker_reset(self, generation: int, abs_frame: int):
        if generation != self._active_worker_generation:
            return
        self._logs.append(
            f"Saut dans la timeline - tracking reinitialise a la frame {abs_frame}"
        )

    def _on_range_progress(self, generation: int, done: int, total: int):
        if generation != self._active_range_generation or self._measure.playing:
            return
        self._set_status(f"Analyse {done}/{total} frames…")

    def _on_range_finished(self, generation: int, frames: int, tracks: int):
        if generation != self._active_range_generation:
            return
        self._finalize_tracking_generation(generation)
        if self._measure.playing:
            return
        self._publish_overlay()
        self._set_status(f"Segment analyse : {frames} frames, {tracks} piste(s)")
        self._logs.append(f"Analyse tracking : {frames} frames, {tracks} pistes")

    def _finalize_tracking_generation(self, generation: int) -> None:
        """Ferme exactement les états portés par une génération terminale."""
        if generation == self._active_worker_generation:
            self._active_worker_generation = -1
        if generation == self._active_range_generation:
            self._active_range_generation = -1
        if generation == self._active_follow_generation:
            self._active_follow_generation = -1
            if self._following:
                self._following = False
                self.followingChanged.emit()
        self._set_busy(False)

    def _invalidate_tracking_generations(self) -> None:
        """Révoque toutes les sorties d'une intention de tracking abandonnée."""
        self._active_worker_generation = -1
        self._active_range_generation = -1
        self._active_follow_generation = -1
        self._active_persistence_generation = -1
        self._persistence_error_generation = -1

    def _on_track_failed(self, generation: int, msg: str):
        persistence_failure = (
            generation == self._active_persistence_generation
            and msg.startswith("Ecriture pistes :")
        )
        if generation != self._active_worker_generation and not persistence_failure:
            return
        if persistence_failure:
            self._persistence_error_generation = generation
            if generation in (
                self._active_worker_generation,
                self._active_range_generation,
                self._active_follow_generation,
            ):
                # Le calcul long est terminé même si son lot reste à écrire.
                # L'identité de persistance demeure active pour un retry ciblé.
                self._finalize_tracking_generation(generation)
        else:
            self._finalize_tracking_generation(generation)
            if generation == self._active_persistence_generation:
                self._active_persistence_generation = -1
        if not self._measure.playing:
            self._set_status(msg)
        self._logs.append(f"[!] Tracking : {msg}")
        if self._graze_state in ("tracking", "waiting"):
            self._graze_seed_ann_id = ""
            self._set_grazing_workflow("error", f"Analyse impossible : {msg}")

    def _on_persistence_finished(self, generation: int):
        if generation != self._active_persistence_generation:
            return
        self._active_persistence_generation = -1
        if generation == self._persistence_error_generation:
            self._persistence_error_generation = -1
            if not self._measure.playing:
                self._set_status("Pistes enregistrées")

    def _on_frame_changed(self):
        self._auto_detect_timer.stop()
        self._detect_seq += 1
        if self._active_detect_seq >= 0:
            self._detect_rerun_pending = True
        # L'identité de frame invalide immédiatement toute sortie IA. Les
        # rectangles manuels sont rechargés séparément pour la frame courante.
        self._set_frame_count_ready(False)
        self._replace_last_boxes([])
        if self._measure.playing:
            # Recharger les bbox puis republier l'overlay complet a chaque
            # notification du lecteur natif coutait plus cher que le decodage
            # video lui-meme : c'est ce qui saccadait la lecture. D'ou la voie
            # d'affichage seule ci-dessous - dictionnaires deja en memoire et
            # Canvas, sans SQLite ni OpenCV. Le traitement complet reste
            # reporte au retour en pause.
            self._publish_playback_overlay()
            return
        self._load_manual_boxes_for_frame()
        if self._focus_box.get("valid") and self._focus_box.get("frame") != self._measure.frameIndex:
            self.clearFocusBox()
        if self._selected_fish_index >= 0:
            self.setSelectedFishIndex(-1)
        self._publish_overlay()
        if not self._fish_ia:
            return
        if self._tracking and self._tracker is not None:
            self._run_track_frame()
            return
        if self._auto_pause:
            self._schedule_auto_detect()

    @Slot()
    def detectCurrentFrame(self):
        if self._busy or self._following:
            return
        if not self._fish_ia or self._measure.playing:
            return
        frame = self._read_left_frame()
        if frame is None:
            self._set_status("Pas de frame")
            return
        self._detect_seq += 1
        seq = self._detect_seq
        frame_identity = (
            int(self._measure.frameIndex), int(self._measure.leftAbsFrame),
        )
        self._active_detect_seq = seq
        self._active_detect_frame = frame_identity
        self._detect_rerun_pending = False
        self._set_busy(True)
        self._set_status("Detection en cours…")
        self._worker = _DetectWorker(frame, self._confidence, self)
        self._worker.finished_ok.connect(
            lambda boxes, _frame, s=seq, identity=frame_identity:
                self._on_detect_ok(boxes, s, identity)
        )
        self._worker.failed.connect(
            lambda msg, s=seq, identity=frame_identity:
                self._on_detect_fail(msg, s, identity)
        )
        self._worker.finished.connect(lambda s=seq: self._finish_detect(s))
        self._worker.start()

    def _finish_detect(self, seq: int) -> None:
        if seq != self._active_detect_seq:
            return
        self._active_detect_seq = -1
        self._active_detect_frame = None
        self._set_busy(False)
        if self._detect_rerun_pending:
            self._detect_rerun_pending = False
            self._schedule_auto_detect()

    def _on_detect_ok(
        self, boxes: list, seq: int, frame_identity: tuple[int, int],
    ):
        current_identity = (
            int(self._measure.frameIndex), int(self._measure.leftAbsFrame),
        )
        if (
            seq != self._detect_seq
            or frame_identity != current_identity
            or self._measure.playing
            or not self._fish_ia
        ):
            if not self._measure.playing and self._fish_ia:
                self._detect_rerun_pending = True
            return
        # Même cardinalité ne signifie pas même ordre : les deux sélections
        # sont annulées avant que QML ne voie la nouvelle collection.
        self._replace_last_boxes(boxes)
        self._set_frame_count_ready(True)
        self.detectionChanged.emit()
        self._publish_overlay()
        self._set_status(f"{len(boxes)} poisson(s) detecte(s)")
        self._logs.append(f"Detection frame {self._measure.frameIndex} : {len(boxes)} bbox")

    def _on_detect_fail(
        self,
        msg: str,
        seq: int | None = None,
        frame_identity: tuple[int, int] | None = None,
    ):
        current_identity = (
            int(self._measure.frameIndex), int(self._measure.leftAbsFrame),
        )
        if (
            seq is not None
            and (
                seq != self._detect_seq
                or seq != self._active_detect_seq
                or frame_identity != current_identity
                or self._measure.playing
            )
        ):
            if not self._measure.playing and self._fish_ia:
                self._detect_rerun_pending = True
            return
        self._set_status(msg)
        self._logs.append(f"[!] {msg}")

    def _run_track_frame(self):
        if self._tracker is None:
            return
        cached = self._tracker.cached_boxes(self._measure.leftAbsFrame)
        if cached is not None:
            self._replace_last_boxes(cached)
            self._publish_overlay()
            return
        self._active_worker_generation = self._tracker.request_frame(
            self._measure.leftAbsFrame
        )
        self._active_persistence_generation = self._active_worker_generation

    # Il y avait ici quatre chemins de suivi concurrents : le segment
    # multi-poissons « Suivi automatique (comptage) », le cycle d'intervalle de
    # comportement en double, le suivi assisté du menu, et le suivi In / Out.
    # Le client n'en veut qu'un, celui qui rattache la piste à la fiche du
    # poisson : les autres sont partis avec leurs sections, leur entrée de menu
    # et leurs bornes `_track_in` / `_track_out`, qui n'etaient plus lues.

    @Slot()
    def cancelTracking(self):
        if self._tracker is not None:
            self._tracker.cancel()
        self._invalidate_tracking_generations()
        self._set_busy(False)
        self._set_status("Analyse interrompue")

    # --- suivi d'un seul poisson ------------------------------------------

    @Property(bool, notify=followingChanged)
    def following(self):
        return self._following

    @Property(int, notify=followingChanged)
    def followLostFrame(self):
        return self._follow_lost_frame

    @Slot()
    def stopFollow(self):
        if self._tracker is not None:
            self._tracker.cancel()
        self._invalidate_tracking_generations()
        self._following = False
        # Abandonner le suivi ferme aussi l'incident : sans cette remise a -1,
        # le bandeau « suivi perdu » restait affiche indefiniment.
        self._follow_lost_frame = -1
        self.followingChanged.emit()
        self._set_busy(False)

    # --- suivi assiste In -> Out ------------------------------------------

    @Property(str, notify=assistChanged)
    def assistState(self):
        return self._assist_state

    @Property(bool, notify=assistChanged)
    def assistActive(self):
        return self._assist_state != ASSIST_IDLE

    @Property(bool, notify=assistChanged)
    def assistWaiting(self):
        """Le suivi est interrompu : l'utilisateur doit retracer la bbox."""
        return self._assist_state == ASSIST_WAITING

    @Property(str, notify=assistChanged)
    def assistMessage(self):
        return self._assist_message

    @Property(int, notify=assistChanged)
    def assistCorrections(self):
        return self._assist_corrections

    @Property(int, notify=assistChanged)
    def assistProgress(self):
        span = self._assist_end - self._assist_start
        if span <= 0 or self._assist_cursor < 0:
            return 0
        done = self._assist_cursor - self._assist_start
        return max(0, min(100, int(100 * done / span)))

    @Property(str, notify=grazingWorkflowChanged)
    def grazingWorkflowState(self):
        return self._graze_state

    @Property(str, notify=grazingWorkflowChanged)
    def grazingWorkflowMessage(self):
        return self._graze_message

    # Trois propriétés disaient la même chose sous trois noms, parce que deux
    # cycles se partageaient la machine : `grazingStartMarked` (« marked » et
    # pas un suivi nu), `behaviorWorkflowLocked` (un cycle tourne) et
    # `behaviorWorkflowTypeKey` (le comportement figé par le cycle). Avec un
    # seul cycle, il ne reste que les deux ci-dessous.

    @Property(bool, notify=grazingWorkflowChanged)
    def trackFollowStartMarked(self):
        """« In » est posé : il ne manque plus que « Out »."""
        return self._graze_state == "marked"

    @Property(bool, notify=grazingWorkflowChanged)
    def trackFollowActive(self):
        """Un suivi est engagé : In posé, en cours, ou en attente de reprise."""
        return self._graze_state in ("marked", "tracking", "waiting")

    @Property(int, notify=grazingWorkflowChanged)
    def grazingStartFrame(self):
        return self._graze_start_abs

    def _set_grazing_workflow(self, state: str, message: str) -> None:
        self._graze_state = state
        self._graze_message = message
        self.grazingWorkflowChanged.emit()

    def _behavior_seed(self) -> tuple[dict | None, str]:
        """BBox d'amorce du suivi, et observation du registre à rattacher.

        Le client démarre la broute « sur le poisson sélectionné » : une ligne
        du registre désigne le poisson aussi légitimement qu'un clic droit sur
        une bbox, et c'est le geste qu'il cherche. Quand les deux désignent le
        même poisson, la bbox de l'overlay amorce mieux le tracker sur l'image
        courante, mais c'est l'observation qui recevra la piste produite.
        """
        focus = self._focus_box
        focus_box = None
        if (
            bool(focus.get("valid"))
            and int(focus.get("frame", -1)) == int(self._measure.frameIndex)
        ):
            focus_box = {
                "x1": float(focus["x1"]),
                "y1": float(focus["y1"]),
                "x2": float(focus["x2"]),
                "y2": float(focus["y2"]),
                "track_id": -1,
                "cls_name": "fish",
                "conf": 0.0,
            }
        box = self._selected_box()
        if (
            focus_box is not None
            and self._assist_state != ASSIST_WAITING
            and self._selected_fish_index < 0
        ):
            # Aucune bbox n'a été désignée au clic : la ligne du registre est
            # souveraine. Le repli « l'unique bbox de l'image » amorcerait
            # sinon le suivi sur un autre poisson que celui de la fiche.
            box = focus_box
        if box is None:
            return None, ""
        # Ne relier l'observation que si l'amorce est bien ce poisson-là :
        # rattacher une fiche à la piste du voisin fausserait tout l'export.
        ann_id = ""
        if focus_box is not None and _box_iou(focus_box, box) >= FOCUS_MATCH_IOU:
            ann_id = str(focus.get("annId") or "")
        return box, ann_id

    @Property(bool, notify=behaviorSeedChanged)
    def behaviorSeedReady(self):
        """Un poisson est désigné : bbox choisie, bbox unique, ou ligne ouverte."""
        box, _ann_id = self._behavior_seed()
        return box is not None

    def _resolved_seed(self) -> tuple[dict | None, str]:
        """Amorce du suivi, en ramenant si besoin l'affichage sur le poisson.

        Le poisson du registre est sélectionné mais on a navigué ailleurs : sa
        propre image est le début naturel du suivi, et le tracker a besoin
        d'une bbox sur l'image affichée. On y revient plutôt que de renvoyer
        l'utilisateur chercher la frame.
        """
        box, ann_id = self._behavior_seed()
        if box is not None:
            return box, ann_id
        refocus = getattr(self._data, "focusSelectedObservation", None)
        if refocus is None or not getattr(self._data, "selectedAnnId", ""):
            return None, ""
        refocus()
        return self._behavior_seed()

    @Property(str, notify=grazingWorkflowChanged)
    def trackFollowBehaviorKey(self):
        return self._graze_behavior[0] if self._graze_behavior else ""

    @Slot(str, result=bool)
    def beginBehaviorFollow(self, event_key: str) -> bool:
        """Suit une action sur une durée, avec son type figé dès In."""
        types = getattr(self._data, "behaviorTypes", [])
        event = next((row for row in types
                      if row.get("key") == event_key
                      and row.get("scope") == "interval"
                      and row.get("isActive", True)), None)
        if event is None:
            self._set_status("Choisissez un comportement sur une durée.")
            return False
        if self.trackFollowActive:
            self._set_status("Terminez le suivi engagé ou annulez-le.")
            return False
        if getattr(self._data, "selectedTrackDbId", ""):
            return self._begin_behavior_on_existing_track((event_key, event["label"]))
        return self._begin_track_follow((event_key, event["label"]))

    def _begin_behavior_on_existing_track(self, behavior: tuple[str, str]) -> bool:
        """Borne une action sur la piste liée, sans recalculer sa trajectoire."""
        try:
            from sqlalchemy import select, func
            from src.annodb.connection import session_scope
            from src.annodb.models import SpatialAnnotation, Track, TrackSample

            ann_id = self._data.selectedAnnId
            with session_scope(paths.annotations_db_path()) as session:
                ann = session.get(SpatialAnnotation, ann_id)
                track = session.get(Track, ann.track_id) if ann and ann.track_id else None
                if track is None:
                    raise ValueError("La piste de ce poisson n'est plus disponible.")
                first, last = session.execute(select(
                    func.min(TrackSample.frame_index), func.max(TrackSample.frame_index),
                ).where(TrackSample.track_id == track.id)).one()
                if first is None:
                    raise ValueError("Cette piste ne contient pas de positions.")
                existing = {"id": track.id, "external": track.external_track_id,
                            "first": int(first), "last": int(last)}
            current = int(self._measure.leftAbsFrame)
            if not existing["first"] <= current < existing["last"]:
                raise ValueError(f"Posez In dans la piste : images {first}–{last}, avant sa fin.")
        except Exception as exc:
            self._set_grazing_workflow("error", str(exc))
            self._set_status(self._graze_message)
            return False
        if self._measure.playing:
            self._measure.togglePlay()
        self._graze_behavior = behavior
        self._graze_existing_track = existing
        self._graze_seed_ann_id = ann_id
        self._graze_start_abs = current
        self._graze_end_abs = -1
        self._set_grazing_workflow("marked", f"{behavior[1]} · In : image {current}. Posez Out sur cette piste.")
        self._set_status(self._graze_message)
        return True

    @Slot()
    def beginTrackFollow(self):
        """Suit uniquement la trajectoire, sans comportement implicite."""
        self._begin_track_follow()

    def _begin_track_follow(self, behavior: tuple[str, str] | None = None) -> bool:
        """Mémorise l'observation, sa boîte et, si demandé, le comportement."""
        if self._graze_state in ("marked", "tracking", "waiting"):
            self._set_status(
                "Un suivi est déjà engagé : terminez-le ou annulez-le."
            )
            return False
        if self._measure.playing:
            self._measure.togglePlay()
        box, ann_id = self._resolved_seed()
        if box is None:
            self._set_grazing_workflow(
                "error",
                "Sélectionnez le poisson : une ligne du registre, ou un clic "
                "droit sur son cadre.",
            )
            self._set_status(self._graze_message)
            return False
        if not ann_id:
            self._set_grazing_workflow(
                "error",
                "Enregistrez d'abord ce poisson : le suivi se rattache à sa "
                "fiche, il lui faut une ligne du registre.",
            )
            self._set_status(self._graze_message)
            return False
        self._graze_behavior = behavior
        self._graze_existing_track = None
        self._graze_seed_box = dict(box)
        self._graze_seed_ann_id = ann_id
        self._graze_start_abs = int(self._measure.leftAbsFrame)
        self._graze_end_abs = -1
        self._set_grazing_workflow(
            "marked",
            f"In : image {self._graze_start_abs}. Avancez puis posez Out.",
        )
        self._set_status(self._graze_message)

        return True

    @Slot()
    def finishTrackFollow(self):
        """Suit de In à Out avec le choix mémorisé au début."""
        if self._graze_state != "marked":
            self._set_status("Posez d'abord « Début du suivi (In) »")
            return
        if self._graze_existing_track:
            existing = self._graze_existing_track
            end = int(self._measure.leftAbsFrame)
            if not self._graze_start_abs < end <= existing["last"]:
                self._set_status(f"Out doit suivre In et rester dans la piste, jusqu'à l'image {existing['last']}.")
                return
            self._graze_end_abs = end
            self._assist_track_external_id = int(existing["external"])
            self._finish_track_follow(existing["id"])
            return
        if self._graze_seed_box is None:
            self._set_status("Amorce du suivi perdue : reprenez à « In »")
            return
        end_abs = int(self._measure.leftAbsFrame)
        if end_abs <= self._graze_start_abs:
            self._set_status("Out doit être après In")
            return
        if self._tracker is None:
            self._init_tracker()
        if self._tracker is None:
            self._set_grazing_workflow("error", "Tracking indisponible.")
            self._set_status(self._graze_message)
            return
        self._graze_end_abs = end_abs
        self._assist_start = self._graze_start_abs
        self._assist_end = self._graze_end_abs
        self._assist_cursor = self._assist_start
        self._assist_corrections = 0
        self._assist_track_external_id = -1
        self._follow_lost_frame = -1
        self._set_grazing_workflow(
            "tracking",
            f"Suivi du poisson f{self._graze_start_abs}→{self._graze_end_abs}…",
        )
        self._launch_assist_segment(self._graze_seed_box, self._assist_start)

    @Slot()
    def cancelGrazingAnalysis(self):
        """Abandonne le suivi et son éventuelle annotation sur durée."""
        existing = self._graze_existing_track is not None
        self._graze_behavior = None
        self._graze_existing_track = None
        if self._tracker is not None:
            self._tracker.cancel()
        self._invalidate_tracking_generations()
        self._following = False
        self.followingChanged.emit()
        self._set_busy(False)
        self._set_assist(ASSIST_IDLE, "")
        self._graze_seed_box = None
        self._graze_seed_ann_id = ""
        self._set_grazing_workflow(
            "idle", "Durée annulée ; piste conservée." if existing
            else "Suivi abandonné : aucune piste enregistrée.",
        )

    def _set_assist(self, state: str, message: str = "") -> None:
        self._assist_state = state
        self._assist_message = message
        self.assistChanged.emit()

    def _launch_assist_segment(self, box: dict, start_abs: int) -> None:
        """Lance (ou relance) le tracker de start_abs jusqu'a la fin du segment."""
        self._following = True
        self._follow_lost_frame = -1
        self._follow_frames = 0
        self.followingChanged.emit()
        self._set_busy(True)
        self._set_assist(ASSIST_RUNNING, "Suivi en cours…")
        self._set_status("Suivi en cours…")
        logical_id = (
            self._assist_track_external_id
            if self._assist_track_external_id >= 0
            else None
        )
        generation = self._tracker.request_follow(
            box, start_abs, self._assist_end, logical_id,
        )
        self._active_follow_generation = generation
        self._active_worker_generation = generation
        self._active_persistence_generation = generation
        if self._graze_state in ("waiting", "tracking"):
            self._set_grazing_workflow(
                "tracking",
                f"Suivi de ce poisson en cours ({self.assistProgress} %).",
            )

    @Slot()
    def stopAssistedTracking(self):
        if self._tracker is not None:
            self._tracker.cancel()
        self._invalidate_tracking_generations()
        self._following = False
        self.followingChanged.emit()
        self._set_busy(False)
        done = self._assist_cursor - self._assist_start
        self._set_assist(ASSIST_IDLE, "")
        self._set_status(f"Suivi assiste arrete ({max(0, done)} frames)")
        if self._graze_state in ("tracking", "waiting"):
            self._graze_seed_ann_id = ""
            self._set_grazing_workflow(
                "cancelled",
                "Suivi interrompu : la piste n'a pas été rattachée au poisson.",
            )

    @Slot(result=bool)
    @Slot(int, result=bool)
    def resumeAssistFromBox(self, box_index: int = -1):
        """Reprend avec le cadre cliqué, sans changer de fiche ni de piste.

        Sans index, conserve la reprise depuis le dernier rectangle manuel.
        Un index explicite prime sur ce rectangle et sur la sélection courante.
        """
        if (self._assist_state != ASSIST_WAITING or self._busy
                or self._measure.playing or self._tracker is None):
            return False
        if box_index != -1:
            boxes = self._combined_boxes()
            if not 0 <= box_index < len(boxes):
                return False
            box = boxes[box_index]
        else:
            box = self._selected_box()
        if box is None:
            self._set_status("Cliquez sur un cadre ou encadrez le poisson pour reprendre le suivi")
            return False
        start = self._measure.leftAbsFrame
        if not self._assist_start <= start <= self._assist_end:
            self._set_status("Revenez entre In et Out pour reprendre le suivi")
            return False
        self._assist_corrections += 1
        self._logs.append(f"Reprise du suivi a f{start} (correction {self._assist_corrections})")
        self._launch_assist_segment(dict(box), start)
        return True

    def _assist_on_lost(self, abs_frame: int, reason: str) -> None:
        """Le tracker a rendu la main : on attend une correction manuelle."""
        self._assist_cursor = abs_frame

        if self._follow_frames == 0 and abs_frame < self._assist_end:
            # Le tracker n'a pas repris du tout sur cette frame : le rectangle
            # que l'utilisateur vient de tracer fait foi pour elle, et on passe
            # a la suivante. Sans cela on resterait bloque sur la meme image.
            self._measure.seekFromLeftAbsFrame(abs_frame + 1)
            self._assist_cursor = abs_frame + 1
            self._set_assist(
                ASSIST_WAITING,
                f"Tracking perdu (frame {self._measure.frameIndex}) - le suivi ne "
                "repart pas ici. Cliquez sur son cadre ou réencadrez-le sur cette image.",
            )
            if self._graze_state == "tracking":
                self._set_grazing_workflow("waiting", self._assist_message)
            return

        self._set_assist(
            ASSIST_WAITING,
            f"Tracking perdu (frame {self._measure.frameIndex}) - {reason}. "
            "Cliquez sur son cadre pour reprendre, ou réencadrez le poisson.",
        )
        if self._graze_state == "tracking":
            self._set_grazing_workflow("waiting", self._assist_message)

    def _assist_on_finished(self) -> None:
        """Fin de segment : soit la cible est atteinte, soit on attend l'utilisateur."""
        if self._assist_state == ASSIST_IDLE:
            return
        if self._follow_lost_frame < 0:
            # Segment terminé sans perte : Out est atteint.
            self._assist_cursor = self._assist_end
            corrections = self._assist_corrections
            self._set_assist(ASSIST_IDLE, "")
            suffix = f" apres {corrections} correction(s)" if corrections else ""
            self._set_status(f"Suivi termine jusqu'a la frame de fin{suffix}")
            self._logs.append(f"Suivi assiste termine{suffix}")
            if self._graze_state == "tracking":
                db_track_id = ""
                if self._tracker is not None and self._assist_track_external_id >= 0:
                    db_track_id = self._tracker.db_track_id(
                        self._assist_track_external_id
                    )
                self._finish_track_follow(db_track_id)

    def _finish_track_follow(self, db_track_id: str) -> None:
        """Rattache la piste puis enregistre uniquement la durée demandée à In."""
        behavior = self._graze_behavior
        self._graze_behavior = None
        self._graze_existing_track = None
        if not db_track_id:
            self._graze_seed_ann_id = ""
            self._set_grazing_workflow(
                "error", "Aucune piste produite : recommencez le suivi.",
            )
            self._set_status(self._graze_message)
            return
        outcome, link = self._link_seed_observation(db_track_id)
        self._graze_seed_ann_id = ""
        linked = outcome in ("linked", "already")
        event_saved = True
        if behavior and linked:
            event_saved = self._data.completeAssistedBehavior(
                self._assist_track_external_id, db_track_id,
                self._graze_start_abs, self._graze_end_abs,
                behavior[0], behavior[1],
            )
        if not linked:
            message = f"Piste produite mais non rattachée.{link}"
        elif not event_saved:
            message = "Piste enregistrée, mais le comportement n'a pas été enregistré."
        else:
            label = behavior[1] if behavior else "Trajectoire"
            message = (f"{label} · images {self._graze_start_abs}–{self._graze_end_abs}"
                       f" · piste #{self._assist_track_external_id}")
        self._set_grazing_workflow(
            "success" if linked and event_saved else "warning", message,
        )
        self._set_status(self._graze_message)

    # Ce que dit la fin du suivi selon le sort de l'observation engagée.
    _LINK_MESSAGES = {
        "linked": " Le poisson du registre porte désormais cette piste.",
        "already": " Le poisson du registre portait déjà cette piste.",
        "conflict": (
            " Attention : le poisson du registre appartient déjà à une autre "
            "piste, rien n'a été modifié sur sa fiche."
        ),
        "missing": "",
        "error": " Le poisson du registre n'a pas pu être rattaché à cette piste.",
    }

    def _link_seed_observation(self, track_db_id: str) -> tuple[str, str]:
        """Donne la piste produite au poisson du registre engagé au « Début ».

        C'est le geste qui manquait : on mesurait et identifiait un poisson
        d'un côté, on analysait une broute de l'autre, et les deux ne se
        rejoignaient nulle part. Une observation déjà rattachée à une AUTRE
        piste n'est pas réécrite : l'utilisateur doit le savoir, pas le
        découvrir plus tard dans un export faux.

        Retourne (sort, fragment de phrase à coller au message de fin).
        """
        if not self._graze_seed_ann_id or self._data is None:
            return "missing", ""
        attach = getattr(self._data, "attachObservationToTrack", None)
        if attach is None:
            return "missing", ""
        outcome = str(attach(self._graze_seed_ann_id, track_db_id) or "error")
        return outcome, self._LINK_MESSAGES.get(
            outcome, self._LINK_MESSAGES["error"],
        )

    def _assist_on_manual_box(self) -> bool:
        """Une bbox vient d'etre tracee : relance le suivi si on l'attendait.

        Retourne True si la bbox a ete consommee par le suivi assiste.
        """
        if self._assist_state != ASSIST_WAITING:
            return False
        # Laisse l'overlay se rafraichir avant de relancer le tracker.
        QTimer.singleShot(0, self.resumeAssistFromBox)
        return True

    def _selected_box(self) -> dict | None:
        """Bbox servant d'amorce au suivi.

        Hors reprise, l'index choisi au clic droit est souverain. Pendant
        ``ASSIST_WAITING``, la dernière bbox manuelle est précisément le geste
        par lequel l'utilisateur redésigne son poisson.
        """
        if self._assist_state == ASSIST_WAITING and self._manual_boxes:
            return self._manual_boxes[-1]
        boxes = self._combined_boxes()
        idx = self._selected_fish_index
        if 0 <= idx < len(boxes):
            return boxes[idx]
        return boxes[0] if len(boxes) == 1 else None

    def _on_follow_progress(self, generation: int, abs_frame: int, box: dict):
        if generation != self._active_follow_generation or self._measure.playing:
            return
        self._follow_frames += 1
        external_id = box.get("track_id")
        self._assist_track_external_id = int(external_id) if external_id is not None else -1
        self._replace_last_boxes([box])
        self._measure.seekFromLeftAbsFrame(abs_frame)
        self._publish_overlay()
        if self._assist_state == ASSIST_RUNNING:
            self._assist_cursor = abs_frame
            # La barre de progression suit le curseur.
            self.assistChanged.emit()

    def _on_follow_lost(self, generation: int, abs_frame: int, reason: str):
        if generation != self._active_follow_generation or self._measure.playing:
            return
        self._follow_lost_frame = abs_frame
        self._measure.seekFromLeftAbsFrame(abs_frame)
        self._set_status(f"Frame {abs_frame} - {reason}")
        self._logs.append(f"Suivi perdu frame {abs_frame} : {reason}")
        if self._assist_state != ASSIST_IDLE:
            self._assist_on_lost(abs_frame, reason)
        self._finalize_tracking_generation(generation)

    def _on_follow_finished(self, generation: int, frames: int, last_frame: int):
        if generation != self._active_follow_generation:
            return
        self._finalize_tracking_generation(generation)
        if self._measure.playing:
            return
        self._publish_overlay()
        if self._assist_state != ASSIST_IDLE:
            self._assist_on_finished()
        elif self._follow_lost_frame < 0:
            self._set_status(f"Suivi termine : {frames} frames")
        self._logs.append(f"Suivi : {frames} frames jusqu'a f{last_frame}")

    def _measure_target_box(self) -> tuple[dict | None, int]:
        """BBox à mesurer : ligne focalisée, sinon sélection explicite ou bbox unique."""
        boxes = self._combined_boxes()
        focus = self._focus_box
        if focus.get("valid") and focus.get("frame") == self._measure.frameIndex:
            return {
                "x1": focus["x1"],
                "y1": focus["y1"],
                "x2": focus["x2"],
                "y2": focus["y2"],
                "track_id": -1,
                "cls_name": "fish",
                "conf": 0.0,
            }, -1
        idx = self._selected_fish_index
        if 0 <= idx < len(boxes):
            return dict(boxes[idx]), idx
        if len(boxes) == 1:
            return dict(boxes[0]), 0
        return None, -1

    @Slot()
    def measureSelectedBoxLength(self):
        if self._measure.playing:
            self._set_status("Mettez en pause pour mesurer la bbox")
            return
        bbox, idx = self._measure_target_box()
        if bbox is None:
            self._set_status(
                "Selectionnez une ligne du registre, encadrez ou detectez un poisson"
            )
            return
        try:
            import fish_sparse_measure as fsm
        except ImportError:
            self._logs.append("[!] fish_sparse_measure indisponible")
            self._set_status("Module mesure sparse indisponible")
            return
        # Utiliser la calibration de l'image affichée. La recharger ici
        # rafraîchissait les images et relançait la détection automatique :
        # ses nouveaux cadres effaçaient le brouillon juste après la mesure.
        rect_l, rect_r, P1, P2 = self._measure.rectified_pair(self._measure.frameIndex)
        if rect_l is None or rect_r is None or P1 is None or P2 is None:
            self._set_status("Calibration / frames indisponibles")
            return
        result = fsm.measure_bbox_length_mm(rect_l, rect_r, bbox, P1, P2)
        if result is None:
            self._set_status("Echec mesure - baissez seuil IA ou verifiez sync")
            self._logs.append("[!] Mesure sparse echouee")
            return
        pa = result["pixel_a"]
        pb = result["pixel_b"]
        length = float(result["length_mm"])
        # Le signal distance est synchrone : fixer la cible avant de l'émettre
        # permet à Data de persister ou d'attacher la mesure au bon cadre.
        if idx >= 0:
            self.setSelectedFishIndex(idx)
            if self._data is not None:
                self._data.selectBoxIndex(idx)
        try:
            self._ensure_repo_path()
            from stereo_utils import stereo_match_disparity_rect

            ma = stereo_match_disparity_rect(rect_l, rect_r, pa[0], pa[1])
            mb = stereo_match_disparity_rect(rect_l, rect_r, pb[0], pb[1])
            if ma is None or mb is None:
                self._measure.set_distance_mm(length)
                self._set_status(f"Longueur {length:.1f} mm - points droits non trouves")
                return
            ra_pt, _score_a = ma
            rb_pt, _score_b = mb
            self._measure.applySparseBBoxMeasure(
                pa[0], pa[1], pb[0], pb[1],
                ra_pt[0], ra_pt[1], rb_pt[0], rb_pt[1],
                length,
            )
        except Exception:
            self._measure.set_distance_mm(length)
        if self._data is not None and self._data.pendingStereoMeasureMm > 0:
            self._set_status(
                f"Mesure {length:.1f} mm - liée au cadre à ajouter"
            )
        else:
            self._set_status(f"Mesure {length:.1f} mm - enregistrée")

    @Slot()
    def runSparseLength(self):
        self.measureSelectedBoxLength()

    def best_bbox(self) -> dict | None:
        return self._last_boxes[0] if self._last_boxes else None
