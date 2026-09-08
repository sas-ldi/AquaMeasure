"""Drapeaux de comportements ponctuels sur les observations spatiales."""

from __future__ import annotations

from typing import Any, Dict, Iterable, List

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .models import EventType, SpatialAnnotation, SpatialBehaviorFlag


def list_flags(
    session: Session, annotation_ids: Iterable[str]
) -> Dict[str, List[Dict[str, Any]]]:
    ids = [str(value) for value in annotation_ids if value]
    if not ids:
        return {}
    rows = session.execute(
        select(SpatialBehaviorFlag, EventType)
        .join(EventType, SpatialBehaviorFlag.event_type == EventType.key)
        .where(SpatialBehaviorFlag.spatial_annotation_id.in_(ids))
        .order_by(EventType.sort_order, EventType.label)
    ).all()
    out: Dict[str, List[Dict[str, Any]]] = {ann_id: [] for ann_id in ids}
    for flag, event_type in rows:
        out.setdefault(flag.spatial_annotation_id, []).append({
            "key": flag.event_type,
            "label": event_type.label,
            "symbol": event_type.symbol or "●",
            "color": event_type.color or "#f59e0b",
            "scope": event_type.scope,
            "author": flag.author or "",
            "createdAt": flag.created_at.isoformat() if flag.created_at else "",
        })
    return out


def toggle_flag(
    session: Session,
    *,
    annotation_id: str,
    event_type: str,
    author: str | None = None,
) -> bool:
    """Inverse un flag et retourne son nouvel état.

    Un flag existant peut toujours être retiré, même si son type a ensuite été
    désactivé. Toute nouvelle pose exige en revanche un type ponctuel actif.
    """
    key = (event_type or "").strip()
    # Prendre le verrou d'écriture SQLite avant de lire le couple. Deux toggles
    # concurrents voient ainsi l'état laissé par leur prédécesseur au lieu de
    # lire tous deux « absent » puis de se heurter à la clé primaire.
    session.execute(
        update(EventType)
        .where(EventType.key == key)
        .values(is_active=EventType.is_active)
    )
    if author is None:
        from .annotators import current_author_name

        author = current_author_name(session)
    flag = session.get(SpatialBehaviorFlag, (annotation_id, key))
    if flag is not None:
        session.delete(flag)
        session.flush()
        return False

    annotation = session.get(SpatialAnnotation, annotation_id)
    if annotation is None:
        raise ValueError(f"Observation introuvable: {annotation_id}")
    behavior = session.scalar(select(EventType).where(EventType.key == key))
    if behavior is None or not behavior.is_active:
        raise ValueError(f"Comportement indisponible: {key}")
    if behavior.scope != "instant":
        raise ValueError("Seul un comportement ponctuel peut marquer une observation")
    session.add(SpatialBehaviorFlag(
        spatial_annotation_id=annotation_id,
        event_type=key,
        author=(author or "").strip() or None,
    ))
    session.flush()
    return True


__all__ = ["list_flags", "toggle_flag"]
