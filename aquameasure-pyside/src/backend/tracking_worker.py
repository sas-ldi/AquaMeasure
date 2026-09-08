"""Tracking poisson exécuté hors du thread d'interface.

Trois garanties que l'appel direct depuis un Slot ne donnait pas :

- ByteTrack ne reçoit jamais de frames dans le désordre. Un saut dans la
  timeline réinitialise explicitement son état plutôt que de le laisser
  associer des positions incohérentes (première cause des décrochages).
- les demandes de frame sont fusionnées : pendant un scrub, seule la dernière
  position demandée est calculée.
- l'écriture en base se fait par lots, pas une transaction par frame.
"""

from __future__ import annotations

import queue
import threading
from typing import Any, Callable

import numpy as np
from PySide6.QtCore import QThread, Signal

from src.backend.frame_reader import SequentialFrameReader

# Seuil volontairement haut : sur une scène chargée une frame produit déjà
# 30 lignes. Le retour au repos déclenche de toute façon un flush, donc en
# usage interactif rien n'attend ce seuil.
_FLUSH_EVERY = 1500
_PROGRESS_EVERY = 10

# En dessous de ce recouvrement avec la position précédente, on considère
# que le poisson est perdu plutôt que d'accrocher un voisin par erreur.
_FOLLOW_MIN_IOU = 0.35


class _PersistenceError(RuntimeError):
    def __init__(self, generation: int, message: str):
        super().__init__(message)
        self.generation = int(generation)


def _iou(a: dict, b: dict) -> float:
    ix1 = max(a["x1"], b["x1"])
    iy1 = max(a["y1"], b["y1"])
    ix2 = min(a["x2"], b["x2"])
    iy2 = min(a["y2"], b["y2"])
    if ix2 <= ix1 or iy2 <= iy1:
        return 0.0
    inter = (ix2 - ix1) * (iy2 - iy1)
    area_a = (a["x2"] - a["x1"]) * (a["y2"] - a["y1"])
    area_b = (b["x2"] - b["x1"]) * (b["y2"] - b["y1"])
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


class TrackingWorker(QThread):
    """Boucle de tracking pilotée par une file de commandes."""

    frameTracked = Signal(int, int, list)
    trackerReset = Signal(int, int)
    rangeProgress = Signal(int, int, int)
    rangeFinished = Signal(int, int, int)
    followProgress = Signal(int, int, dict)
    followLost = Signal(int, int, str)
    followFinished = Signal(int, int, int)
    videoOpened = Signal(int, int, int, int)
    failed = Signal(int, str)
    persistenceFinished = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._queue: queue.Queue[tuple] = queue.Queue()
        self._stop = threading.Event()
        self._command_lock = threading.Lock()
        self._command_generation = 0
        self._executing_cancel: threading.Event | None = None
        self._pending_long_cancel: threading.Event | None = None

        self._video_path = ""
        self._conf = 0.25
        self._tracker_cfg = "bytetrack.yaml"
        self._persist_db = False
        self._project = "madagascar_measure"
        self._transform: Callable[[np.ndarray], np.ndarray] | None = None

        self._model = None
        self._imgsz = 0
        self._reader: SequentialFrameReader | None = None
        self._last_frame = -1
        self._id_offset = 0
        self._max_seen_id = 0

        self._frame_tracks: dict[int, list[dict[str, Any]]] = {}
        self._pending_rows: list[tuple[int, int, float, float, tuple]] = []
        self._pending_generation = -1
        self._db_track_ids: dict[int, str] = {}
        self._db_path = None
        self._media_id: str | None = None

    # --- API appelée depuis le thread UI (jamais bloquante) ----------------

    def configure(
        self,
        video_path: str,
        *,
        conf: float = 0.25,
        tracker_cfg: str = "bytetrack.yaml",
        persist_db: bool = False,
        project: str = "madagascar_measure",
        transform: Callable[[np.ndarray], np.ndarray] | None = None,
    ) -> int:
        generation, token = self._new_command_token()
        self._queue.put((
            "configure", generation, token, video_path, conf, tracker_cfg,
            persist_db, project, transform,
        ))
        return generation

    def request_frame(self, abs_frame: int) -> int:
        generation, token = self._new_command_token()
        self._queue.put(("frame", generation, token, int(abs_frame)))
        return generation

    def _new_command_token(self) -> tuple[int, threading.Event]:
        with self._command_lock:
            self._command_generation += 1
            generation = self._command_generation
            token = threading.Event()
        return generation, token

    def request_range(self, start: int, end: int) -> int:
        generation, token = self._new_command_token()
        with self._command_lock:
            self._pending_long_cancel = token
        self._queue.put(("range", generation, token, int(start), int(end)))
        return generation

    def request_follow(
        self,
        seed_box: dict,
        start: int,
        end: int,
        logical_track_id: int | None = None,
    ) -> int:
        """Suit un seul poisson, désigné par une bbox de départ.

        L'amorce est une bbox et non un identifiant : après une
        réinitialisation du tracker les identifiants changent, alors que la
        position, elle, reste celle que l'utilisateur a désignée à l'écran.
        """
        generation, token = self._new_command_token()
        with self._command_lock:
            self._pending_long_cancel = token
        self._queue.put((
            "follow", generation, token, dict(seed_box), int(start), int(end),
            logical_track_id,
        ))
        return generation

    def cancel(self) -> None:
        with self._command_lock:
            token = self._executing_cancel or self._pending_long_cancel
        if token is not None:
            token.set()

    def reset_state(self) -> int:
        generation, token = self._new_command_token()
        self._queue.put(("reset", generation, token))
        return generation

    def flush(self) -> int:
        generation, token = self._new_command_token()
        self._queue.put(("flush", generation, token, None))
        return generation

    def flush_now(self, timeout: float = 5.0) -> bool:
        """Écrit tout de suite les pistes en attente et attend la fin.

        Le lot n'était vidé qu'à 1500 lignes ou au repos : au moment où
        l'opérateur ajoutait une observation, la piste n'existait pas encore en
        base et le lien annotation↔piste était perdu (`track_id` NULL à 100 %).
        Appelable depuis un autre thread : file de commandes + Event, aucun
        objet Qt touché. Retourne False si le flush n'a pas abouti dans le
        délai - l'appelant enregistre alors sans lien plutôt que de bloquer.
        """
        if not self.isRunning() or self._stop.is_set():
            return False
        done = threading.Event()
        result: list[bool] = []
        generation, token = self._new_command_token()
        self._queue.put(("flush", generation, token, done, result))
        return done.wait(timeout) and result == [True]

    def stop(self) -> None:
        self.cancel()
        self._stop.set()
        generation, token = self._new_command_token()
        self._queue.put(("stop", generation, token))

    def db_track_id(self, external_track_id: int) -> str:
        """UUID base de la piste, connu seulement après écriture (flush)."""
        return self._db_track_ids.get(int(external_track_id), "") or ""

    def cached_boxes(self, abs_frame: int) -> list[dict[str, Any]] | None:
        """Lecture depuis l'UI - le dict n'est muté que par remplacement de clé."""
        cached = self._frame_tracks.get(int(abs_frame))
        if not cached:
            return None
        # Les identifiants base n'existent qu'après le flush : on les recolle
        # ici, sinon une bbox détectée avant l'écriture resterait à jamais
        # « sans piste » à l'écran.
        return [self._with_db_id(box) for box in cached]

    def _with_db_id(self, box: dict[str, Any]) -> dict[str, Any]:
        out = dict(box)
        if not out.get("db_track_id"):
            out["db_track_id"] = self._db_track_ids.get(int(out.get("track_id", -1)))
        return out

    @property
    def cached_frame_count(self) -> int:
        return len(self._frame_tracks)

    @property
    def unique_track_count(self) -> int:
        return len({
            box["track_id"]
            for boxes in self._frame_tracks.values()
            for box in boxes
        })

    def trails(
        self, abs_frame: int, *, span: int = 90, visible_only: bool = False
    ) -> list[dict[str, Any]]:
        """Trainée autour de la frame courante, keyframes de perte incluses.

        Contrairement à l'ancien calcul, la trainée d'une piste qui vient de
        décrocher reste affichée : c'est justement l'information utile.
        """
        end = int(abs_frame)
        start = end - max(0, int(span))
        visible = {
            box["track_id"] for box in self._frame_tracks.get(end, [])
        } if visible_only else None

        points: dict[int, list[tuple[int, float, float]]] = {}
        for frame in range(start, end + 1):
            for box in self._frame_tracks.get(frame, ()):
                tid = int(box["track_id"])
                if visible is not None and tid not in visible:
                    continue
                cx = (float(box["x1"]) + float(box["x2"])) * 0.5
                cy = (float(box["y1"]) + float(box["y2"])) * 0.5
                points.setdefault(tid, []).append((frame, cx, cy))

        out = []
        for tid, pts in points.items():
            out.append({
                "trackId": tid,
                "lost": pts[-1][0] < end,
                "lastFrame": pts[-1][0],
                "points": [{"x": x, "y": y} for _f, x, y in pts],
            })
        return out

    # --- boucle worker -----------------------------------------------------

    def run(self) -> None:
        while not self._stop.is_set():
            command = self._next_command()
            if command is None:
                continue
            try:
                self._dispatch(command)
            except Exception as exc:
                generation = int(getattr(exc, "generation", command[1]))
                self.failed.emit(generation, str(exc))
        try:
            self._flush_pending()
        except Exception as exc:
            if self._pending_generation >= 0:
                self.failed.emit(self._pending_generation, str(exc))
        self._close_reader()

    def _next_command(self, timeout: float = 0.2) -> tuple | None:
        try:
            command = self._queue.get(timeout=timeout)
        except queue.Empty:
            try:
                self._flush_pending()
            except Exception as exc:
                if self._pending_generation >= 0:
                    self.failed.emit(self._pending_generation, str(exc))
            return None
        if command[0] != "frame":
            return command

        latest = command
        deferred: list[tuple] = []
        while True:
            try:
                pending = self._queue.get_nowait()
            except queue.Empty:
                break
            if pending[0] == "frame":
                latest = pending
            else:
                deferred.append(pending)
        for item in deferred:
            self._queue.put(item)
        return latest

    def _dispatch(self, command: tuple) -> None:
        kind = command[0]
        generation = int(command[1])
        token = command[2]
        long_running = kind in ("range", "follow")
        if long_running:
            with self._command_lock:
                self._executing_cancel = token
                if self._pending_long_cancel is token:
                    self._pending_long_cancel = None
        try:
            if kind == "stop":
                self._stop.set()
            elif kind == "configure":
                self._apply_configure(generation, *command[3:])
            elif kind == "frame":
                self._track_single(command[3], generation)
            elif kind == "range":
                self._track_range(command[3], command[4], generation, token)
            elif kind == "follow":
                self._follow_track(
                    command[3], command[4], command[5], command[6], generation, token,
                )
            elif kind == "reset":
                self._reset_tracker_state()
            elif kind == "flush":
                done = command[3] if len(command) > 3 else None
                result = command[4] if len(command) > 4 else None
                try:
                    self._flush_pending(generation)
                    if result is not None:
                        result.append(True)
                except Exception:
                    if result is not None:
                        result.append(False)
                    raise
                finally:
                    # Réveil et succès sont distincts : l'appelant reçoit False
                    # après une erreur d'écriture, sans attendre le timeout.
                    if done is not None:
                        done.set()
        finally:
            if long_running:
                with self._command_lock:
                    if self._executing_cancel is token:
                        self._executing_cancel = None

    def _apply_configure(
        self, generation, video_path, conf, tracker_cfg, persist_db, project, transform
    ) -> None:
        self._flush_pending()
        changed = video_path != self._video_path
        self._video_path = video_path
        self._conf = float(conf)
        self._tracker_cfg = tracker_cfg
        self._persist_db = bool(persist_db)
        self._project = project
        self._transform = transform
        if changed:
            self._close_reader()
            self._frame_tracks.clear()
            self._db_track_ids.clear()
            self._media_id = None
            self._id_offset = 0
            self._max_seen_id = 0
        self._reset_tracker_state()
        if self._ensure_reader(generation):
            reader = self._reader
            self.videoOpened.emit(generation, reader.frame_count, reader.width, reader.height)

    def _ensure_reader(self, generation: int = 0) -> bool:
        if self._reader is not None and self._reader.is_open:
            return True
        if not self._video_path:
            return False
        reader = SequentialFrameReader(self._video_path)
        if not reader.open():
            raise RuntimeError(f"Video illisible : {self._video_path}")
        self._reader = reader
        return True

    def _close_reader(self) -> None:
        if self._reader is not None:
            self._reader.close()
            self._reader = None

    def _ensure_model(self, generation: int = 0) -> bool:
        """Charge une instance de modèle réservée à ce thread.

        Partager celle du registre ferait tourner deux inférences concurrentes
        sur le même objet (crash natif), et un simple predict() de la détection
        réinitialiserait l'état ByteTrack qui vit dans ce même modèle.
        """
        if self._model is not None:
            return True
        import fish_detectors as fd
        from ultralytics import YOLO

        registry = fd.registry()
        _shared, spec = registry.tracking_model()
        weights = registry.weights_path(spec.id)
        if weights is None:
            raise RuntimeError("Poids du modele de tracking introuvables")
        self._model = YOLO(str(weights))
        self._imgsz = int(spec.imgsz or 0)
        return True

    def _reset_tracker_state(self) -> None:
        """Repart d'un état vierge, en décalant les identifiants déjà émis.

        Sans ce décalage, ByteTrack renumérote à partir de 1 et les nouvelles
        pistes viendraient se confondre avec celles déjà écrites en base.
        """
        predictor = getattr(self._model, "predictor", None)
        trackers = getattr(predictor, "trackers", None) if predictor else None
        for tracker in trackers or ():
            if hasattr(tracker, "reset"):
                tracker.reset()
        self._id_offset = self._max_seen_id
        self._last_frame = -1

    def _read_frame(
        self, abs_frame: int, generation: int = 0,
    ) -> np.ndarray | None:
        if not self._ensure_reader(generation):
            return None
        frame = self._reader.read(abs_frame)
        if frame is None:
            return None
        return self._transform(frame) if self._transform else frame

    def _track_single(self, abs_frame: int, generation: int) -> None:
        # Une frame demandee par l'interface doit suivre la meme chaine que la
        # detection simple : localisation/tracking, puis identification
        # Fishial. Les analyses de longues plages restent, elles, consacrees
        # au suivi pour ne pas lancer le lourd classifieur sur chaque frame.
        boxes = self._process(
            abs_frame,
            generation=generation,
            classify_species=True,
        )
        if boxes is not None:
            self.frameTracked.emit(generation, abs_frame, boxes)

    def _track_range(
        self,
        start: int,
        end: int,
        generation: int,
        cancel_token: threading.Event,
    ) -> None:
        start, end = min(start, end), max(start, end)
        total = end - start + 1
        self._reset_tracker_state()
        done = 0
        for frame in range(start, end + 1):
            if cancel_token.is_set() or self._stop.is_set():
                return
            boxes = self._process(frame, persist=False, generation=generation)
            if cancel_token.is_set() or self._stop.is_set():
                return
            if boxes is None:
                continue
            self._commit_frame(frame, boxes, generation)
            done += 1
            if done % _PROGRESS_EVERY == 0:
                self.rangeProgress.emit(generation, done, total)
        self._flush_pending(generation)
        self.rangeProgress.emit(generation, done, total)
        if not cancel_token.is_set() and not self._stop.is_set():
            self.rangeFinished.emit(generation, done, self.unique_track_count)

    def _follow_track(
        self,
        seed_box: dict,
        start: int,
        end: int,
        logical_track_id: int | None = None,
        generation: int = 0,
        cancel_token: threading.Event | None = None,
    ) -> None:
        """Suit un poisson jusqu'à sa perte, puis rend la main.

        S'arrêter net vaut mieux que continuer sur un voisin : c'est à ce
        moment précis que l'utilisateur doit repositionner la bbox.
        """
        self._reset_tracker_state()
        reference = dict(seed_box)
        tracked_id: int | None = None
        followed = 0
        frame = start
        token = cancel_token or threading.Event()
        lost_terminal: tuple[int, str] | None = None

        while frame <= end and not token.is_set() and not self._stop.is_set():
            # Le suivi assisté persiste uniquement la cible choisie. Les
            # autres détections de la frame ne doivent ni créer des pistes
            # parasites ni fragmenter la piste à chaque reprise manuelle.
            boxes = self._process(
                frame, persist=False, generation=generation,
            )
            if token.is_set() or self._stop.is_set():
                break
            if boxes is None:
                lost_terminal = (frame, "frame illisible")
                break

            match = None
            if tracked_id is not None:
                match = next(
                    (b for b in boxes if b["track_id"] == tracked_id), None
                )
            if match is None:
                best, best_iou = None, 0.0
                for box in boxes:
                    score = _iou(box, reference)
                    if score > best_iou:
                        best, best_iou = box, score
                if best is not None and best_iou >= _FOLLOW_MIN_IOU:
                    match = best
                    tracked_id = int(best["track_id"])

            if match is None:
                reason = (
                    "aucune detection" if not boxes
                    else "poisson perdu - repositionnez la bbox"
                )
                lost_terminal = (frame, reason)
                break

            reference = match
            if logical_track_id is None:
                logical_track_id = int(match["track_id"])
            match = dict(match)
            match["track_id"] = int(logical_track_id)
            match["db_track_id"] = self._db_track_ids.get(int(logical_track_id))
            self._frame_tracks[frame] = [match]
            if self._persist_db:
                reader = self._reader
                self._queue_persist(
                    frame,
                    [match],
                    reader.width if reader is not None else 0,
                    reader.height if reader is not None else 0,
                    generation,
                )
            followed += 1
            self.followProgress.emit(generation, frame, dict(match))
            frame += 1

        self._flush_pending(generation)
        if token.is_set() or self._stop.is_set():
            return
        if lost_terminal is not None:
            lost_frame, reason = lost_terminal
            self.followLost.emit(generation, lost_frame, reason)
        else:
            self.followFinished.emit(generation, followed, frame - 1)

    def _process(
        self,
        abs_frame: int,
        *,
        persist: bool = True,
        generation: int = 0,
        classify_species: bool = False,
    ) -> list[dict[str, Any]] | None:
        if not self._ensure_model(generation):
            return None
        frame = self._read_frame(abs_frame, generation)
        if frame is None:
            return None

        if self._last_frame >= 0 and abs_frame != self._last_frame + 1:
            self._reset_tracker_state()
            self.trackerReset.emit(generation, abs_frame)

        height, width = frame.shape[:2]
        track_options: dict[str, Any] = {
            "persist": True,
            "tracker": self._tracker_cfg,
            "conf": self._conf,
            "verbose": False,
        }
        if self._imgsz:
            track_options["imgsz"] = self._imgsz
        results = self._model.track(frame, **track_options)
        self._last_frame = abs_frame
        boxes = self._boxes_from_result(results[0] if results else None, width, height)
        published = [self._with_db_id(b) for b in boxes]
        if classify_species and published:
            try:
                import fishial_classify as fc

                if fc.is_available():
                    published = fc.classify_boxes(frame, published)
            except Exception as exc:
                published = [
                    dict(box, species_error=str(exc)) for box in published
                ]
        if persist:
            self._commit_frame(abs_frame, published, generation, width, height)
        return published

    def _commit_frame(
        self,
        abs_frame: int,
        boxes: list[dict[str, Any]],
        generation: int,
        width: int | None = None,
        height: int | None = None,
    ) -> None:
        """Publie et met en persistance un calcul encore annulable."""
        self._frame_tracks[abs_frame] = [dict(box) for box in boxes]
        if not self._persist_db or not boxes:
            return
        reader = self._reader
        self._queue_persist(
            abs_frame,
            boxes,
            int(width if width is not None else (reader.width if reader else 0)),
            int(height if height is not None else (reader.height if reader else 0)),
            generation,
        )

    def _boxes_from_result(self, result, width: int, height: int) -> list[dict[str, Any]]:
        if result is None or result.boxes is None or len(result.boxes) == 0:
            return []
        raw_ids = result.boxes.id
        if raw_ids is None:
            return []
        xyxy = result.boxes.xyxy.cpu().numpy()
        ids = raw_ids.cpu().numpy().astype(int)
        confs = (
            result.boxes.conf.cpu().numpy()
            if result.boxes.conf is not None
            else np.ones(len(xyxy))
        )
        cls_ids = (
            result.boxes.cls.cpu().numpy().astype(int)
            if result.boxes.cls is not None
            else np.zeros(len(xyxy), dtype=int)
        )
        names = getattr(result, "names", None) or {}

        out: list[dict[str, Any]] = []
        for i, box in enumerate(xyxy):
            track_id = int(ids[i]) + self._id_offset
            self._max_seen_id = max(self._max_seen_id, track_id)
            cls_id = int(cls_ids[i])
            # Bornage a la source : ces coordonnees partent telles quelles dans
            # `track_samples`, et de la dans les exports de suivi. Un detecteur
            # qui rend une boite mordant le bord de l'image rectifiee ecrirait
            # sinon en base une position qui n'existe pas.
            x1 = min(max(float(box[0]), 0.0), float(width))
            y1 = min(max(float(box[1]), 0.0), float(height))
            x2 = min(max(float(box[2]), 0.0), float(width))
            y2 = min(max(float(box[3]), 0.0), float(height))
            out.append({
                "track_id": track_id,
                "db_track_id": self._db_track_ids.get(track_id),
                "x1": x1,
                "y1": y1,
                "x2": x2,
                "y2": y2,
                "cx": (x1 + x2) * 0.5 / max(1, width),
                "cy": (y1 + y2) * 0.5 / max(1, height),
                "cls_id": cls_id,
                "cls_name": names.get(cls_id, "fish") if isinstance(names, dict) else "fish",
                "conf": float(confs[i]),
            })
        return out

    # --- persistance -------------------------------------------------------

    def _queue_persist(
        self,
        abs_frame: int,
        boxes: list[dict[str, Any]],
        width: int,
        height: int,
        generation: int = 0,
    ) -> None:
        self._pending_generation = generation
        for box in boxes:
            self._pending_rows.append((
                abs_frame,
                int(box["track_id"]),
                float(box["cx"]),
                float(box["cy"]),
                (box["x1"], box["y1"], box["x2"], box["y2"]),
            ))
        if len(self._pending_rows) >= _FLUSH_EVERY:
            self._flush_pending(generation)

    def _flush_pending(self, generation: int | None = None) -> None:
        failure_generation = self._pending_generation
        if failure_generation < 0 and generation is not None:
            failure_generation = int(generation)
        if not self._persist_db or not self._video_path:
            self._pending_rows.clear()
            self._pending_generation = -1
            return
        if not self._pending_rows:
            return
        rows, self._pending_rows = self._pending_rows, []
        try:
            self._write_rows(rows)
        except Exception as exc:
            self._pending_rows = rows + self._pending_rows
            self._pending_generation = failure_generation
            raise _PersistenceError(
                failure_generation, f"Ecriture pistes : {exc}",
            ) from exc
        self._pending_generation = -1
        self.persistenceFinished.emit(failure_generation)

    def _write_rows(self, rows: list[tuple[int, int, float, float, tuple]]) -> None:
        from src.annodb.connection import ensure_schema, session_scope
        from src.annodb.projects import get_or_create_project, register_media
        from src.annodb.tracks import add_track_samples_bulk, get_or_create_track

        if self._db_path is None:
            from src.annodb.connection import get_db_path

            self._db_path = get_db_path()
            ensure_schema(self._db_path)

        by_track: dict[int, list] = {}
        for frame_index, ext_id, cx, cy, bbox in rows:
            by_track.setdefault(ext_id, []).append((frame_index, cx, cy, bbox))

        with session_scope(self._db_path) as session:
            if self._media_id is None:
                from pathlib import Path

                project = get_or_create_project(session, self._project)
                media = register_media(
                    session,
                    project_id=project.id,
                    file_path=Path(self._video_path),
                    copy_into_store=False,
                )
                self._media_id = media.id
            for ext_id, samples in by_track.items():
                db_id = self._db_track_ids.get(ext_id)
                if db_id is None:
                    db_id = get_or_create_track(
                        session,
                        media_id=self._media_id,
                        external_track_id=ext_id,
                        source="bytetrack",
                    ).id
                    self._db_track_ids[ext_id] = db_id
                add_track_samples_bulk(
                    session, track_id=db_id, rows=samples, origin="auto"
                )
