"""Provenance de calibration - quelle géométrie a produit quelles boîtes.

Avant cette table, le profil actif ne vivait que sur disque
(`camera_parameters/active_profile.txt` + les `.npy`) : rien en base ne disait
avec quelle calibration une boîte avait été tracée. Une recalibration
invalidait donc silencieusement tout l'historique, sans qu'aucun contrôle ne
puisse le détecter a posteriori.

Règle structurante : **une calibration = un sha256**. Le condensé porte le jeu
complet de `.npy` (cf. `rectify.CALIB_FILES`), donc deux sessions calibrées à
l'identique partagent une seule ligne, et le moindre recalibrage en crée une
nouvelle.
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Calibration


def find_by_sha256(db: Session, sha256: Optional[str]) -> Optional[Calibration]:
    """Calibration déjà enregistrée pour ce condensé, ou None."""
    sha = (sha256 or "").strip()
    if not sha:
        return None
    return db.scalar(select(Calibration).where(Calibration.sha256 == sha))


def register_calibration(
    db: Session,
    *,
    profile_name: str,
    sha256: str,
    alpha: Optional[float] = None,
    image_width: Optional[int] = None,
    image_height: Optional[int] = None,
    baseline_mm: Optional[float] = None,
    stereo_rmse: Optional[float] = None,
    calibration_id: Optional[str] = None,
) -> Optional[Calibration]:
    """Retrouve (par sha256) ou enregistre la calibration ; None sans condensé.

    Le dédoublonnage est fait ici **et** garanti par l'index unique sur
    `sha256` : deux appels concurrents ne peuvent pas créer deux lignes.

    Une ligne existante n'est complétée que sur ses champs vides : on ne
    réécrit jamais une taille d'image ou une RMSE déjà connues sous une session
    qui s'en sert.
    """
    sha = (sha256 or "").strip()
    if not sha:
        return None

    existing = find_by_sha256(db, sha)
    if existing is not None:
        if image_width and not existing.image_width:
            existing.image_width = int(image_width)
        if image_height and not existing.image_height:
            existing.image_height = int(image_height)
        if baseline_mm is not None and existing.baseline_mm is None:
            existing.baseline_mm = float(baseline_mm)
        if stereo_rmse is not None and existing.stereo_rmse is None:
            existing.stereo_rmse = float(stereo_rmse)
        if alpha is not None and existing.alpha is None:
            existing.alpha = float(alpha)
        db.flush()
        return existing

    row = Calibration(
        id=calibration_id or str(uuid.uuid4()),
        profile_name=(profile_name or "").strip() or "classic",
        sha256=sha,
        alpha=None if alpha is None else float(alpha),
        image_width=int(image_width) if image_width else None,
        image_height=int(image_height) if image_height else None,
        baseline_mm=None if baseline_mm is None else float(baseline_mm),
        stereo_rmse=None if stereo_rmse is None else float(stereo_rmse),
    )
    db.add(row)
    db.flush()
    return row


def register_active_calibration(
    db: Session,
    *,
    image_width: Optional[int] = None,
    image_height: Optional[int] = None,
    stereo_rmse: Optional[float] = None,
) -> Optional[Calibration]:
    """Enregistre le profil de calibration **actif** sur disque, ou None.

    `stereo_rmse` peut être imposé par l'appelant (l'application Qt le connaît
    déjà via `paths.stereo_rmse_if_exists`) ; à défaut il est relu dans le
    profil.
    """
    from .rectify import load_calibration_profile

    calib = load_calibration_profile()
    if calib is None:
        return None
    summary = calib.summary()
    return register_calibration(
        db,
        profile_name=summary["profile_name"],
        sha256=summary["calibration_sha256"],
        alpha=summary.get("alpha"),
        image_width=image_width,
        image_height=image_height,
        baseline_mm=summary.get("baseline_mm"),
        stereo_rmse=stereo_rmse if stereo_rmse is not None else summary.get("stereo_rmse"),
    )


def calibration_as_dict(row: Optional[Calibration]) -> Dict[str, Any]:
    if row is None:
        return {}
    return {
        "calibration_id": row.id,
        "profile_name": row.profile_name,
        "sha256": row.sha256,
        "alpha": row.alpha,
        "image_width": row.image_width,
        "image_height": row.image_height,
        "baseline_mm": row.baseline_mm,
        "stereo_rmse": row.stereo_rmse,
    }


__all__ = [
    "calibration_as_dict",
    "find_by_sha256",
    "register_active_calibration",
    "register_calibration",
]
