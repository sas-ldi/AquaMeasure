"""Lecture .npy minimale (tableaux 1D int/double)."""

from __future__ import annotations

import numpy as np


def load_int1d(path: str) -> list[int]:
    arr = np.load(path)
    return [int(x) for x in np.asarray(arr).ravel().tolist()]


def save_int1d(path: str, values: list[int]) -> None:
    np.save(path, np.asarray(values, dtype=np.int64))
