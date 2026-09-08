"""Tracking poissons ByteTrack.

`is_available()` sert à l'application PySide6 (bouton « Suivi automatique »
grisé sans modèle utilisable) ; `VideoTracker` reste le tracker incrémental de
l'ancienne interface PyQt6 et de `track_video()`.

**Ce module n'écrit plus en base** (phase 7). `VideoTracker` persistait
autrefois chaque frame dans `spatial_annotations` / `track_samples` via
`src.track_store.TrackStore`, ouvrant une session SQLAlchemy *par frame*, en
parallèle du `TrackingWorker` de l'application PySide6 qui, lui, écrit par
lots dans un fil dédié. Deux chemins d'écriture concurrents sur le même
fichier SQLite, dont un seul était utilisé par l'application : le second est
supprimé plutôt que dupliqué. Le suivi persistant, c'est
`interface/src/backend/tracking_worker.py`, et lui seul.

`src/track_store.py` reste en place : il sert au script d'inférence en ligne
de commande `annotations/scripts/infer_track_count.py`, qui ouvre sa propre
session et n'entre jamais en concurrence avec l'application.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Optional

import numpy as np


def is_available() -> bool:
    import fish_detectors as fd

    reg = fd.registry()
    return any(
        (status := reg.status(spec.id)) and status.usable and spec.backend == "ultralytics"
        for spec in reg.specs()
    )


def _pick_device() -> str | int:
    try:
        import torch
        if torch.cuda.is_available():
            return 0
    except ImportError:
        pass
    return "cpu"


def extract_tracks_from_result(result, img_w: int, img_h: int) -> list[tuple[int, float, float, tuple]]:
    out = []
    if result.boxes is None or len(result.boxes) == 0:
        return out
    boxes = result.boxes.xyxy.cpu().numpy()
    ids = result.boxes.id
    if ids is None:
        return out
    ids = ids.cpu().numpy().astype(int)
    cls_ids = result.boxes.cls.cpu().numpy().astype(int) if result.boxes.cls is not None else [0] * len(boxes)
    names = {}
    if hasattr(result, "names"):
        names = result.names or {}
    for i, box in enumerate(boxes):
        x1, y1, x2, y2 = box
        cx = (x1 + x2) / 2 / img_w
        cy = (y1 + y2) / 2 / img_h
        cls_id = int(cls_ids[i])
        cls_name = names.get(cls_id, "fish") if isinstance(names, dict) else "fish"
        out.append((
            int(ids[i]),
            float(cx),
            float(cy),
            (float(x1), float(y1), float(x2), float(y2)),
            cls_id,
            cls_name,
        ))
    return out


class VideoTracker:
    """Tracker incremental frame par frame (ByteTrack via Ultralytics)."""

    def __init__(
        self,
        model_path: str | None = None,  # ignore : le modele vient du registre
        conf: float = 0.25,
        tracker: str = "bytetrack.yaml",
    ):
        from fish_detect import tracking_model  # noqa: WPS433

        self._model = tracking_model()
        self._conf = conf
        self._tracker = tracker
        self._trajectories: dict[int, list[dict]] = {}
        self._unique_ids: set[int] = set()
        self._frame_tracks: dict[int, list] = {}

    @property
    def trajectories(self) -> dict[int, list[dict]]:
        return self._trajectories

    @property
    def unique_count(self) -> int:
        return len(self._unique_ids)

    def reset(self) -> None:
        self._trajectories.clear()
        self._unique_ids.clear()
        self._frame_tracks.clear()

    def process_frame(self, bgr: np.ndarray, frame_index: int) -> list[dict[str, Any]]:
        if bgr is None or bgr.size == 0:
            return []
        h, w = bgr.shape[:2]
        results = self._model.track(
            bgr,
            persist=True,
            tracker=self._tracker,
            conf=self._conf,
            verbose=False,
            device=_pick_device(),
        )
        if not results:
            return []
        tracks = extract_tracks_from_result(results[0], w, h)
        boxes: list[dict[str, Any]] = []
        for ext_id, cx, cy, bbox, cls_id, cls_name in tracks:
            self._unique_ids.add(ext_id)
            self._trajectories.setdefault(ext_id, []).append({
                "frame": frame_index,
                "cx": cx,
                "cy": cy,
            })
            boxes.append({
                "track_id": ext_id,
                "x1": bbox[0],
                "y1": bbox[1],
                "x2": bbox[2],
                "y2": bbox[3],
                "cx": cx,
                "cy": cy,
                "cls_id": cls_id,
                "cls_name": cls_name,
                "conf": 1.0,
            })
        self._frame_tracks[frame_index] = boxes
        return boxes

    def get_cached_boxes(self, frame_index: int) -> list[dict[str, Any]] | None:
        cached = self._frame_tracks.get(frame_index)
        if cached is None:
            return None
        return [dict(b) for b in cached]

    def get_trails_up_to_frame(
        self,
        frame_index: int,
        *,
        visible_only: bool = True,
        max_points: int = 80,
    ) -> dict[int, list[tuple[float, float]]]:
        """Centroïdes pixel (rectifié) — uniquement pistes visibles à frame_index."""
        current = self._frame_tracks.get(frame_index) or []
        visible_ids = {
            int(b["track_id"])
            for b in current
            if b.get("track_id") is not None
        }
        if visible_only and not visible_ids:
            return {}

        trails: dict[int, list[tuple[float, float]]] = {}
        for fidx in sorted(self._frame_tracks.keys()):
            if fidx > frame_index:
                break
            for box in self._frame_tracks[fidx]:
                tid = box.get("track_id")
                if tid is None:
                    continue
                tid = int(tid)
                if visible_only and tid not in visible_ids:
                    continue
                cx = (float(box["x1"]) + float(box["x2"])) * 0.5
                cy = (float(box["y1"]) + float(box["y2"])) * 0.5
                trails.setdefault(tid, []).append((cx, cy))
        if max_points > 0:
            for tid in trails:
                trails[tid] = trails[tid][-max_points:]
        return trails

    @property
    def cached_frame_count(self) -> int:
        return len(self._frame_tracks)


def track_video(
    source: str | int,
    *,
    model_path: str | None = None,
    max_frames: Optional[int] = None,
    on_frame: Optional[Callable[[int, np.ndarray, list[dict[str, Any]]], None]] = None,
) -> dict[str, Any]:
    """Traite une video complete et retourne trajectoires."""
    import cv2

    tracker = VideoTracker(model_path=model_path)
    cap = cv2.VideoCapture(source)
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open video: {source}")

    frame_idx = 0
    while cap.isOpened():
        ok, frame = cap.read()
        if not ok:
            break
        if max_frames and frame_idx >= max_frames:
            break
        boxes = tracker.process_frame(frame, frame_idx)
        if on_frame:
            on_frame(frame_idx, frame, boxes)
        frame_idx += 1
    cap.release()
    return {
        "frames": frame_idx,
        "unique_tracks": tracker.unique_count,
        "trajectories": {str(k): v for k, v in tracker.trajectories.items()},
    }
