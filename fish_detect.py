"""Detection poissons - facade historique au-dessus du registre de detecteurs.

Le choix du modele, son telechargement et son backend sont geres par
`fish_detectors`. Ce module conserve l'API attendue par les appelants
existants (aquameasure.py, fish_track.py) et applique la classification
d'espece Fishial en option.
"""

from __future__ import annotations

from typing import Any

import numpy as np

import fish_detectors as fd


def default_model_path() -> str:
    """Chemin des poids du modele actif (chaine vide si non installe)."""
    path = fd.registry().weights_path()
    return str(path) if path else ""


def active_detector_id() -> str:
    return fd.registry().active_id


def is_available() -> bool:
    return fd.is_available()


def unavailable_reason() -> str:
    return fd.unavailable_reason()


def get_class_names(model_path: str | None = None) -> dict[int, str]:
    try:
        return fd.class_names()
    except Exception:
        return {0: "fish"}


def tracking_model():
    """Modele natif Ultralytics pour ByteTrack - leve si aucun n'est installe."""
    model, _spec = fd.registry().tracking_model()
    return model


def detect_fish(
    bgr: np.ndarray,
    conf: float = 0.25,
    model_path: str | None = None,
    *,
    classify_species: bool = True,
) -> list[dict[str, Any]]:
    """Detecte les poissons sur une image BGR.

    Retourne [{x1,y1,x2,y2,conf,cls_id,cls_name}, ...].
    `model_path` est ignore : le modele provient du registre (parametre conserve
    pour compatibilite avec les anciens appels).
    """
    if bgr is None or bgr.size == 0:
        return []

    out = fd.detect(bgr, conf=conf)

    if classify_species and out:
        try:
            import fishial_classify as _fc
            if _fc.is_available():
                out = _fc.classify_boxes(bgr, out)
        except Exception as exc:
            # La detection reste exploitable si le second etage Fishial ne
            # peut pas charger un crop ou ses poids. On conserve la cause sur
            # chaque boite au lieu de faire echouer toute l'image.
            out = [dict(box, species_error=str(exc)) for box in out]
    return out
