"""Validation commune des embeddings Fishial persistés."""

from __future__ import annotations

from collections import Counter
from typing import Iterable

import numpy as np


def normalize_embedding(
    value,
    *,
    expected_dimension: int | None = None,
) -> np.ndarray | None:
    """Retourne un vecteur float32 fini, non nul et de dimension attendue."""
    try:
        if isinstance(value, (bytes, bytearray, memoryview)):
            blob = bytes(value)
            if not blob or len(blob) % np.dtype(np.float32).itemsize:
                return None
            vector = np.frombuffer(blob, dtype=np.float32)
        else:
            vector = np.asarray(value, dtype=np.float32)
    except (TypeError, ValueError, OverflowError):
        return None
    if vector.ndim != 1 or vector.size == 0:
        return None
    if expected_dimension is not None and vector.size != int(expected_dimension):
        return None
    if not np.all(np.isfinite(vector)):
        return None
    if float(np.linalg.norm(vector)) <= 1e-8:
        return None
    return np.ascontiguousarray(vector, dtype=np.float32)


def canonical_embedding_dimension(values: Iterable) -> int | None:
    """Dimension majoritaire stable des vecteurs utilisables existants."""
    dimensions = Counter()
    for value in values:
        vector = normalize_embedding(value)
        if vector is not None:
            dimensions[int(vector.size)] += 1
    if not dimensions:
        return None
    return min(dimensions, key=lambda size: (-dimensions[size], size))
