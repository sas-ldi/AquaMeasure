"""Spatial annotation CRUD."""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from .frame_ref import FRAME_REF_ABSOLUTE
from .models import (
    GENERIC_TAXON_ID,
    IDENTIFICATION_STATUSES,
    STATUS_AMBIGUOUS,
    STATUS_IDENTIFIED,
    STATUS_UNIDENTIFIABLE,
    STATUS_UNREVIEWED,
    SpatialAnnotation,
)
from .rectify import IMAGE_SPACE_RAW, IMAGE_SPACE_RECTIFIED_LEFT

# Unités déclarées dans geometry_json. Sans elles, deux formats cohabitaient
# dans la même colonne (`cx,cy,w,h` normalisé et `x_min…` pixels), distingués
# par reniflage de magnitude - un poisson de moins d'un pixel de large aurait
# suffi à tromper le test.
UNITS_PX = "px"
UNITS_NORMALIZED = "normalized"


def make_bbox_geometry(
    x_min: float,
    y_min: float,
    x_max: float,
    y_max: float,
    *,
    space: str = IMAGE_SPACE_RAW,
    ref_width: Optional[int] = None,
    ref_height: Optional[int] = None,
    units: str = UNITS_PX,
) -> Dict[str, Any]:
    """Géométrie déclarée : unités, espace image et taille de référence.

    `space` vaut `stereo_rectified_left` quand la boîte a été tracée sur
    l'image rectifiée affichée (rectifieur actif), `raw` sinon.
    """
    geom: Dict[str, Any] = {
        "x_min": float(x_min),
        "y_min": float(y_min),
        "x_max": float(x_max),
        "y_max": float(y_max),
        "units": units,
        "space": space or IMAGE_SPACE_RAW,
    }
    if ref_width:
        geom["ref_width"] = int(ref_width)
    if ref_height:
        geom["ref_height"] = int(ref_height)
    return geom


def geometry_is_declared(geom: Dict[str, Any]) -> bool:
    """Vrai si la géométrie porte ses unités (écriture récente)."""
    return bool(geom) and "units" in geom


def geometry_units(geom: Dict[str, Any]) -> Optional[str]:
    units = (geom or {}).get("units")
    return str(units) if units else None


def geometry_space(geom: Dict[str, Any], default: Optional[str] = None) -> Optional[str]:
    """Espace image déclaré, ou `default` pour une ligne historique."""
    space = (geom or {}).get("space")
    return str(space) if space else default


def geometry_ref_size(geom: Dict[str, Any]) -> tuple[Optional[int], Optional[int]]:
    ref_w = (geom or {}).get("ref_width")
    ref_h = (geom or {}).get("ref_height")
    try:
        return (int(ref_w) if ref_w else None, int(ref_h) if ref_h else None)
    except (TypeError, ValueError):
        return None, None


def add_spatial_annotation(
    session: Session,
    *,
    media_id: str,
    geom_type: str,
    geometry: Dict[str, Any],
    frame_index: int = 0,
    taxon_node_id: Optional[str] = None,
    track_id: Optional[str] = None,
    confidence: Optional[float] = None,
    is_provisional: bool = False,
    author: Optional[str] = None,
    source: str = "manual",
    # Dépréciés (phase 7, voir `models.SpatialAnnotation`) : `cvat_task_id`
    # n'est passé par aucun appelant, `cvat_shape_id` par le seul import CVAT
    # XML. Ne pas s'appuyer dessus en lecture - ils sont presque toujours nuls.
    cvat_task_id: Optional[int] = None,
    cvat_shape_id: Optional[int] = None,
    measurement_mm: Optional[float] = None,
    position_x_mm: Optional[float] = None,
    position_y_mm: Optional[float] = None,
    position_z_mm: Optional[float] = None,
    ann_id: Optional[str] = None,
    frame_ref: str = FRAME_REF_ABSOLUTE,
    model_id: Optional[str] = None,
    model_sha256: Optional[str] = None,
    model_conf_threshold: Optional[float] = None,
    identification_status: Optional[str] = None,
    reviewed_by: Optional[str] = None,
    reviewed_at: Optional[datetime] = None,
) -> SpatialAnnotation:
    """Insère une annotation spatiale.

    `model_id` / `model_sha256` / `model_conf_threshold` tracent le détecteur
    qui a proposé la boîte (source `model`). Ils sont posés **une fois**, à
    l'écriture : rien dans la chaîne de validation ne doit les réécrire.

    `identification_status` vaut `unreviewed` par défaut - une boîte fraîchement
    écrite n'a, par construction, pas encore été relue.
    """
    now = datetime.utcnow()
    ann = SpatialAnnotation(
        id=ann_id or str(uuid.uuid4()),
        media_id=media_id,
        frame_index=frame_index,
        frame_ref=frame_ref,
        geom_type=geom_type,
        geometry_json=json.dumps(geometry),
        taxon_node_id=taxon_node_id,
        track_id=track_id,
        confidence=confidence,
        is_provisional=is_provisional,
        author=author,
        source=source,
        cvat_task_id=cvat_task_id,
        cvat_shape_id=cvat_shape_id,
        measurement_mm=measurement_mm,
        position_x_mm=position_x_mm,
        position_y_mm=position_y_mm,
        position_z_mm=position_z_mm,
        model_id=model_id,
        model_sha256=model_sha256,
        model_conf_threshold=model_conf_threshold,
        identification_status=identification_status or STATUS_UNREVIEWED,
        reviewed_by=reviewed_by,
        reviewed_at=reviewed_at,
        updated_at=now,
    )
    session.add(ann)
    session.flush()
    return ann


def status_for_taxon(taxon_node_id: Optional[str]) -> str:
    """Statut d'une saisie humaine selon la finesse du taxon retenu.

    Même règle que le backfill de la phase 1 : une boîte rattachée au nœud
    générique (ou sans taxon) n'a rien de déterminé, quel que soit l'auteur.
    """
    if not taxon_node_id or taxon_node_id == GENERIC_TAXON_ID:
        return STATUS_UNREVIEWED
    return STATUS_IDENTIFIED


def mark_reviewed(
    ann: SpatialAnnotation,
    *,
    status: str,
    reviewed_by: Optional[str] = None,
    when: Optional[datetime] = None,
) -> SpatialAnnotation:
    """Pose le statut d'identification et la trace de révision - **par ajout**.

    Règle d'or de la traçabilité : la validation humaine n'efface jamais
    `model_id`, `model_sha256`, `model_conf_threshold` ni `confidence`. C'est ce
    qui permet de mesurer après coup si le modèle avait raison, et d'éviter de
    le réentraîner sur ses propres erreurs présentées comme de la vérité
    terrain.
    """
    if status not in IDENTIFICATION_STATUSES:
        raise ValueError(f"Statut d'identification inconnu : {status}")
    moment = when or datetime.utcnow()
    ann.identification_status = status
    if status in (STATUS_IDENTIFIED, STATUS_AMBIGUOUS, STATUS_UNIDENTIFIABLE):
        ann.reviewed_by = reviewed_by
        ann.reviewed_at = moment
    ann.updated_at = moment
    return ann


def list_spatial_for_media(
    session: Session,
    media_id: str,
    frame_index: Optional[int] = None,
) -> List[SpatialAnnotation]:
    q = select(SpatialAnnotation).where(SpatialAnnotation.media_id == media_id)
    if frame_index is not None:
        q = q.where(SpatialAnnotation.frame_index == frame_index)
    return list(session.scalars(q.order_by(SpatialAnnotation.frame_index)))


def parse_geometry(ann: SpatialAnnotation) -> Dict[str, Any]:
    return json.loads(ann.geometry_json)


def bbox_from_geometry(geom: Dict[str, Any], img_w: int, img_h: int) -> tuple[float, float, float, float]:
    """Return cx, cy, w, h in normalized YOLO format.

    Les champs déclarés (`units`, `ref_width`, `ref_height`) priment. Le
    reniflage de magnitude (`max <= 1.0`) ne sert plus qu'aux lignes
    historiques écrites sans `units`.
    """
    if geometry_is_declared(geom):
        return _bbox_from_declared(geom, img_w, img_h)

    if "cx" in geom:
        return geom["cx"], geom["cy"], geom["w"], geom["h"]
    if "x_min" in geom:
        x1, y1 = geom["x_min"], geom["y_min"]
        x2, y2 = geom["x_max"], geom["y_max"]
        if geom.get("normalized", False) or max(x1, y1, x2, y2) <= 1.0:
            cx = (x1 + x2) / 2
            cy = (y1 + y2) / 2
            w = x2 - x1
            h = y2 - y1
            return cx, cy, w, h
        cx = (x1 + x2) / 2 / img_w
        cy = (y1 + y2) / 2 / img_h
        w = (x2 - x1) / img_w
        h = (y2 - y1) / img_h
        return cx, cy, w, h
    raise ValueError(f"Unsupported geometry: {geom}")


def _bbox_from_declared(
    geom: Dict[str, Any], img_w: int, img_h: int,
) -> tuple[float, float, float, float]:
    """cx, cy, w, h normalisés depuis une géométrie qui déclare ses unités."""
    if "x_min" in geom:
        x1, y1 = float(geom["x_min"]), float(geom["y_min"])
        x2, y2 = float(geom["x_max"]), float(geom["y_max"])
    elif "cx" in geom:
        cx, cy = float(geom["cx"]), float(geom["cy"])
        bw, bh = float(geom["w"]), float(geom["h"])
        x1, y1, x2, y2 = cx - bw / 2, cy - bh / 2, cx + bw / 2, cy + bh / 2
    else:
        raise ValueError(f"Unsupported geometry: {geom}")

    if geometry_units(geom) == UNITS_NORMALIZED:
        return (x1 + x2) / 2, (y1 + y2) / 2, x2 - x1, y2 - y1

    # Pixels : on normalise par la taille de référence déclarée, pas par celle
    # de l'image exportée - c'est ce qui permet de rester juste si l'image a
    # été redimensionnée entre l'annotation et l'export.
    ref_w, ref_h = geometry_ref_size(geom)
    den_w = float(ref_w or img_w or 1)
    den_h = float(ref_h or img_h or 1)
    return (
        (x1 + x2) / 2 / den_w,
        (y1 + y2) / 2 / den_h,
        (x2 - x1) / den_w,
        (y2 - y1) / den_h,
    )


def bbox_pixels_from_geometry(
    geom: Dict[str, Any], img_w: int, img_h: int,
) -> tuple[float, float, float, float]:
    """(x1, y1, x2, y2) en pixels de l'image de taille (img_w, img_h)."""
    cx, cy, bw, bh = bbox_from_geometry(geom, img_w, img_h)
    return (
        (cx - bw / 2) * img_w,
        (cy - bh / 2) * img_h,
        (cx + bw / 2) * img_w,
        (cy + bh / 2) * img_h,
    )


__all__ = [
    "IDENTIFICATION_STATUSES",
    "IMAGE_SPACE_RAW",
    "IMAGE_SPACE_RECTIFIED_LEFT",
    "STATUS_AMBIGUOUS",
    "STATUS_IDENTIFIED",
    "STATUS_UNIDENTIFIABLE",
    "STATUS_UNREVIEWED",
    "UNITS_NORMALIZED",
    "UNITS_PX",
    "add_spatial_annotation",
    "mark_reviewed",
    "status_for_taxon",
    "bbox_from_geometry",
    "bbox_pixels_from_geometry",
    "geometry_is_declared",
    "geometry_ref_size",
    "geometry_space",
    "geometry_units",
    "list_spatial_for_media",
    "make_bbox_geometry",
    "parse_geometry",
]
