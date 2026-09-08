"""Lecture vidéo séquentielle avec cache de frames décodées.

`cv2.VideoCapture.set(CAP_PROP_POS_FRAMES)` force un re-décodage depuis la
keyframe précédente : sur du H.264 4K, un seek par frame rend le pas-à-pas et
le tracking inutilisables. Ce lecteur privilégie l'avance séquentielle et
conserve les dernières frames décodées pour que le retour arrière soit gratuit.
"""

from __future__ import annotations

from collections import OrderedDict

import cv2
import numpy as np

_DEFAULT_BUDGET_MB = 384
_MIN_CACHE = 4
_MAX_CACHE = 240


class SequentialFrameReader:
    """Lecteur d'une vidéo, optimisé pour un parcours majoritairement en avant.

    `max_forward_grab` borne le nombre de frames que l'on accepte de traverser
    en `grab()` (décodage sans conversion) plutôt que de payer un seek complet.
    """

    def __init__(
        self,
        path: str,
        *,
        memory_budget_mb: int = _DEFAULT_BUDGET_MB,
        max_forward_grab: int = 48,
    ):
        self._path = path
        self._budget_bytes = max(1, int(memory_budget_mb)) * 1024 * 1024
        self._max_forward_grab = max(0, int(max_forward_grab))
        self._cap: cv2.VideoCapture | None = None
        self._cache: OrderedDict[int, np.ndarray] = OrderedDict()
        self._capacity = _MIN_CACHE
        self._next_pos = -1
        self._frame_count = 0
        self._fps = 0.0
        self._width = 0
        self._height = 0

    def open(self) -> bool:
        self.close()
        if not self._path:
            return False
        cap = cv2.VideoCapture(self._path)
        if not cap.isOpened():
            cap.release()
            return False
        self._cap = cap
        self._frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        self._fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        self._width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        self._height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        self._next_pos = 0
        self._update_capacity()
        return True

    def close(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None
        self._cache.clear()
        self._next_pos = -1

    @property
    def is_open(self) -> bool:
        return self._cap is not None

    @property
    def path(self) -> str:
        return self._path

    @property
    def frame_count(self) -> int:
        return self._frame_count

    @property
    def fps(self) -> float:
        return self._fps

    @property
    def width(self) -> int:
        return self._width

    @property
    def height(self) -> int:
        return self._height

    @property
    def cached_count(self) -> int:
        return len(self._cache)

    def clear_cache(self) -> None:
        self._cache.clear()

    def _update_capacity(self) -> None:
        per_frame = max(1, self._width * self._height * 3)
        self._capacity = max(
            _MIN_CACHE, min(_MAX_CACHE, self._budget_bytes // per_frame)
        )

    def _store(self, index: int, frame: np.ndarray) -> None:
        self._cache[index] = frame
        self._cache.move_to_end(index)
        while len(self._cache) > self._capacity:
            self._cache.popitem(last=False)

    def read(self, abs_frame: int) -> np.ndarray | None:
        """Retourne la frame BGR demandée, ou None si illisible."""
        if self._cap is None:
            return None
        index = max(0, int(abs_frame))
        cached = self._cache.get(index)
        if cached is not None:
            self._cache.move_to_end(index)
            return cached

        gap = index - self._next_pos
        if self._next_pos < 0 or gap < 0 or gap > self._max_forward_grab:
            if not self._cap.set(cv2.CAP_PROP_POS_FRAMES, index):
                return None
            self._next_pos = index
            gap = 0

        for _ in range(gap):
            if not self._cap.grab():
                self._next_pos = -1
                return None
            self._next_pos += 1

        ok, frame = self._cap.read()
        if not ok or frame is None:
            self._next_pos = -1
            return None
        self._next_pos = index + 1
        self._store(index, frame)
        return frame

    def read_next(self) -> tuple[int, np.ndarray] | None:
        """Lit la frame suivante en flux - le chemin le plus rapide."""
        if self._cap is None or self._next_pos < 0:
            return None
        index = self._next_pos
        ok, frame = self._cap.read()
        if not ok or frame is None:
            self._next_pos = -1
            return None
        self._next_pos = index + 1
        self._store(index, frame)
        return index, frame

    def __enter__(self) -> SequentialFrameReader:
        self.open()
        return self

    def __exit__(self, *_exc) -> None:
        self.close()
