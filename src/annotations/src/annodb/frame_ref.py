"""Convention d'index de frame : absolu fichier source, partout.

Deux référentiels ont coexisté sans marquage :

- `track_samples.frame_index` = index **absolu** dans le fichier vidéo
  (`tracking_worker.py`, lecture directe par `SequentialFrameReader`) ;
- annotations, broutes et abondance = index **timeline relative**, c'est-à-dire
  décalé du début de la fenêtre synchronisée (`data_controller.py`).

Avec `sync_frames = [2440, 2552]`, l'écart est de 2440 frames : pistes et
annotations ne pointent alors plus la même image, sans que rien ne le signale.

La cible est l'index absolu partout. Les lignes écrites avant la bascule
portent `frame_ref='timeline_legacy'` et doivent être converties à la lecture,
avec un avertissement - ou signalées exclues quand le décalage n'est pas
déterminable.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from .rectify import camera_params_dir

log = logging.getLogger(__name__)

FRAME_REF_ABSOLUTE = "absolute"
FRAME_REF_TIMELINE_LEGACY = "timeline_legacy"

FRAME_REF_VALUES = (FRAME_REF_ABSOLUTE, FRAME_REF_TIMELINE_LEGACY)

# Raison d'exclusion normalisée (reprise telle quelle dans les rapports).
REASON_OFFSET_INDETERMINABLE = "index_timeline_legacy_sans_offset_sync"


def normalize(value: Optional[str]) -> str:
    """Valeur de `frame_ref` fiable.

    Une valeur absente vaut `timeline_legacy` : c'est ce que la migration a
    posé sur l'existant, et supposer l'inverse ferait passer un index décalé
    pour un index absolu - l'erreur exacte que ce champ existe pour empêcher.
    """
    text = (value or "").strip()
    if text == FRAME_REF_ABSOLUTE:
        return FRAME_REF_ABSOLUTE
    return FRAME_REF_TIMELINE_LEGACY


def is_legacy(value: Optional[str]) -> bool:
    return normalize(value) == FRAME_REF_TIMELINE_LEGACY


def read_sync_frames(base: Optional[Path] = None) -> Optional[tuple[int, int]]:
    """(sync gauche, sync droite) depuis `camera_parameters/sync_frames.npy`."""
    root = camera_params_dir(base)
    if root is None:
        return None
    path = root / "sync_frames.npy"
    if not path.is_file():
        return None
    try:
        import numpy as np

        arr = np.asarray(np.load(path)).ravel()
    except (OSError, ValueError):
        return None
    if arr.size < 2:
        return None
    return int(arr[0]), int(arr[1])


def timeline_offset(base: Optional[Path] = None) -> Optional[int]:
    """Décalage `absolu = timeline + offset`, ou None si indéterminable.

    Reproduit `MeasureService._recompute_timeline` : la timeline démarre à
    `left_start = max(left_sync, -(right_sync - left_sync))`.

    None quand `sync_frames.npy` est absent : on ne peut pas savoir avec quelle
    synchro les lignes historiques ont été écrites, et supposer 0 exporterait
    silencieusement de mauvaises frames.
    """
    sync = read_sync_frames(base)
    if sync is None:
        return None
    left_sync, right_sync = sync
    delta = right_sync - left_sync
    return max(left_sync, -delta)


def to_absolute(
    frame_index: Optional[int],
    frame_ref: Optional[str],
    offset: Optional[int],
) -> Optional[int]:
    """Index absolu fichier, ou None si la conversion n'est pas possible."""
    if frame_index is None:
        return None
    if normalize(frame_ref) == FRAME_REF_ABSOLUTE:
        return int(frame_index)
    if offset is None:
        return None
    return int(frame_index) + int(offset)


def to_timeline(
    abs_frame: Optional[int],
    offset: Optional[int],
) -> Optional[int]:
    """Index timeline depuis un index absolu (retour d'affichage)."""
    if abs_frame is None or offset is None:
        return None
    return int(abs_frame) - int(offset)


def describe(frame_ref: Optional[str], offset: Optional[int]) -> str:
    """Message court pour un log ou une barre de statut."""
    if normalize(frame_ref) == FRAME_REF_ABSOLUTE:
        return "index absolu"
    if offset is None:
        return "index timeline historique - offset de sync indisponible"
    return f"index timeline historique converti (+{offset})"
