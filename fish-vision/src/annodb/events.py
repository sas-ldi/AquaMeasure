"""Temporal event (grazing, etc.) CRUD."""

from __future__ import annotations

import json
import uuid
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from .frame_ref import FRAME_REF_ABSOLUTE
from .models import TemporalEvent


def add_temporal_event(
    session: Session,
    *,
    track_id: str,
    frame_start: int,
    frame_end: int,
    event_type: str = "grazing",
    confidence: Optional[float] = None,
    source: str = "manual",
    author: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    event_id: Optional[str] = None,
    frame_ref: str = FRAME_REF_ABSOLUTE,
) -> TemporalEvent:
    ev = TemporalEvent(
        id=event_id or str(uuid.uuid4()),
        track_id=track_id,
        event_type=event_type,
        frame_start=frame_start,
        frame_end=frame_end,
        frame_ref=frame_ref,
        confidence=confidence,
        source=source,
        author=author,
        metadata_json=json.dumps(metadata) if metadata else None,
    )
    session.add(ev)
    session.flush()
    return ev


def list_events_for_track(session: Session, track_id: str) -> List[TemporalEvent]:
    return list(
        session.scalars(
            select(TemporalEvent)
            .where(TemporalEvent.track_id == track_id)
            .order_by(TemporalEvent.frame_start)
        )
    )


def list_events_for_media(session: Session, media_id: str) -> List[TemporalEvent]:
    from .models import Track

    track_ids = [t.id for t in session.scalars(select(Track).where(Track.media_id == media_id))]
    if not track_ids:
        return []
    return list(
        session.scalars(
            select(TemporalEvent)
            .where(TemporalEvent.track_id.in_(track_ids))
            .order_by(TemporalEvent.frame_start)
        )
    )


def delete_temporal_event(session: Session, event_id: str) -> bool:
    ev = session.get(TemporalEvent, event_id)
    if not ev:
        return False
    session.delete(ev)
    session.flush()
    return True


def is_grazing_at_frame(session: Session, track_id: str, frame_index: int) -> bool:
    for ev in list_events_for_track(session, track_id):
        if ev.event_type == "grazing" and ev.frame_start <= frame_index <= ev.frame_end:
            return True
    return False
