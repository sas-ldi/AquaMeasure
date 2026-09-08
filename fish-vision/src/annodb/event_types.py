"""Catalogue des types d'événements comportementaux."""

from __future__ import annotations

import re
import uuid
from typing import Any, Dict, List, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import EventType, SpatialBehaviorFlag, TemporalEvent

GLYPHS = ["●", "◆", "■", "▲", "★", "✚", "▼", "◐", "❋", "⬢"]

PALETTE = [
    "#f59e0b", "#4ade80", "#60c8ff", "#c084fc",
    "#fb7185", "#facc15", "#2dd4bf", "#f97316",
]

_BUILTIN = (
    {
        "key": "grazing",
        "label": "Broutage",
        "color": "#f59e0b",
        "symbol": "◆",
        "shortcut": "B",
        "scope": "interval",
        "description": "Le poisson broute le substrat.",
        "sort_order": 0,
    },
    {
        "key": "bite",
        "label": "Bouchée",
        "color": "#f59e0b",
        "symbol": "●",
        "shortcut": None,
        "scope": "instant",
        "description": "Un coup de bouche, marqué à l'image où il se produit.",
        "sort_order": 1,
    },
)

_KEY_RE = re.compile(r"[^a-z0-9_]+")

# Vocabulaire des portées. Le chantier « bouchées » parle de portée « point » ;
# la base, elle, écrit « instant » depuis le premier jour et c'est cette valeur
# que pousse le formulaire des réglages. Un type créé par script avec
# scope="point" serait donc invisible pour tout le code qui filtre sur
# "instant" : on accepte les deux mots en entrée, on n'en écrit jamais qu'un.
SCOPE_INTERVAL = "interval"
SCOPE_POINT = "instant"

_SCOPE_ALIASES = {
    SCOPE_INTERVAL: SCOPE_INTERVAL,
    "intervalle": SCOPE_INTERVAL,
    SCOPE_POINT: SCOPE_POINT,
    "point": SCOPE_POINT,
    "ponctuel": SCOPE_POINT,
}


def normalize_scope(scope: Optional[str]) -> str:
    """Portée canonique (« interval » ou « instant »), sinon ValueError."""
    resolved = _SCOPE_ALIASES.get((scope or "").strip().lower())
    if resolved is None:
        raise ValueError("Le type doit être ponctuel ou intervalle")
    return resolved


def is_point_scope(scope: Optional[str]) -> bool:
    """True pour une portée ponctuelle, quel que soit le mot employé."""
    return _SCOPE_ALIASES.get((scope or "").strip().lower()) == SCOPE_POINT


def normalize_key(text: str) -> str:
    key = _KEY_RE.sub("_", (text or "").strip().lower()).strip("_")
    return key or "event"


def ensure_builtin_types(session: Session) -> int:
    """Insère les types livrés avec l'app s'ils manquent. Retourne le nombre créé."""
    created = 0
    for spec in _BUILTIN:
        exists = session.scalar(select(EventType).where(EventType.key == spec["key"]))
        if exists:
            continue
        if spec["key"] == "bite":
            # Réutiliser un type ponctuel déjà créé par l'utilisateur.
            # Une ancienne « Bouchée » à intervalle garde sa portée et ses données.
            points = session.scalars(select(EventType).where(
                EventType.scope.in_(("instant", "point", "ponctuel"))
            ))
            if any(row.label.strip().casefold() == "bouchée" for row in points):
                continue
        session.add(EventType(id=str(uuid.uuid4()), is_builtin=True, **spec))
        created += 1
    if created:
        session.flush()
    return created


def as_dict(et: EventType, *, usage: int = 0) -> Dict[str, Any]:
    return {
        "id": et.id,
        "key": et.key,
        "label": et.label,
        "color": et.color,
        "symbol": et.symbol,
        "shortcut": et.shortcut or "",
        "scope": et.scope,
        "description": et.description or "",
        "isBuiltin": bool(et.is_builtin),
        "isActive": bool(et.is_active),
        "sortOrder": et.sort_order,
        "usage": usage,
    }


def list_event_types(
    session: Session, *, active_only: bool = False, with_usage: bool = False
) -> List[Dict[str, Any]]:
    stmt = select(EventType).order_by(EventType.sort_order, EventType.label)
    if active_only:
        stmt = stmt.where(EventType.is_active.is_(True))
    rows = list(session.scalars(stmt))

    usage: Dict[str, int] = {}
    if with_usage and rows:
        interval_counts = session.execute(
            select(TemporalEvent.event_type, func.count(TemporalEvent.id))
            .group_by(TemporalEvent.event_type)
        ).all()
        instant_counts = session.execute(
            select(SpatialBehaviorFlag.event_type, func.count())
            .group_by(SpatialBehaviorFlag.event_type)
        ).all()
        for key, count in (*interval_counts, *instant_counts):
            usage[key] = usage.get(key, 0) + int(count)
    return [as_dict(et, usage=usage.get(et.key, 0)) for et in rows]


def get_by_key(session: Session, key: str) -> Optional[EventType]:
    return session.scalar(select(EventType).where(EventType.key == key))


def _next_sort_order(session: Session) -> int:
    current = session.scalar(select(func.max(EventType.sort_order)))
    return int(current or 0) + 1


def _free_key(session: Session, base: str) -> str:
    key = base
    suffix = 2
    while get_by_key(session, key) is not None:
        key = f"{base}_{suffix}"
        suffix += 1
    return key


def create_event_type(
    session: Session,
    *,
    label: str,
    color: str = "#f59e0b",
    symbol: str = "●",
    shortcut: Optional[str] = None,
    scope: str = "interval",
    description: Optional[str] = None,
    key: Optional[str] = None,
) -> EventType:
    label = (label or "").strip()
    if not label:
        raise ValueError("Le nom de l'événement est obligatoire")
    scope = normalize_scope(scope)
    et = EventType(
        id=str(uuid.uuid4()),
        key=_free_key(session, normalize_key(key or label)),
        label=label,
        color=color or "#f59e0b",
        symbol=symbol or "●",
        shortcut=(shortcut or "").strip().upper()[:1] or None,
        scope=scope,
        description=description,
        is_builtin=False,
        is_active=True,
        sort_order=_next_sort_order(session),
    )
    session.add(et)
    session.flush()
    return et


def update_event_type(session: Session, type_id: str, **fields: Any) -> Optional[EventType]:
    et = session.get(EventType, type_id)
    if et is None:
        return None
    for name in ("label", "color", "symbol", "description", "scope", "sort_order"):
        if name in fields and fields[name] is not None:
            value = fields[name]
            if name == "scope":
                value = normalize_scope(value)
            setattr(et, name, value)
    if "shortcut" in fields:
        raw = (fields["shortcut"] or "").strip().upper()
        et.shortcut = raw[:1] or None
    if "is_active" in fields:
        et.is_active = bool(fields["is_active"])
    session.flush()
    return et


def delete_event_type(session: Session, type_id: str) -> bool:
    """Retire un type personnalisé ; un type utilisé est seulement désactivé."""
    et = session.get(EventType, type_id)
    if et is None:
        return False
    if et.is_builtin:
        return False
    interval_usage = session.scalar(
        select(func.count(TemporalEvent.id)).where(TemporalEvent.event_type == et.key)
    )
    instant_usage = session.scalar(
        select(func.count()).select_from(SpatialBehaviorFlag).where(
            SpatialBehaviorFlag.event_type == et.key
        )
    )
    if int(interval_usage or 0) + int(instant_usage or 0) > 0:
        et.is_active = False
        session.flush()
        return False
    session.delete(et)
    session.flush()
    return True
