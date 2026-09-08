"""Politique unique d'autorité taxonomique, y compris pour les bases legacy."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from .models import (
    GENERIC_TAXON_ID,
    STATUS_IDENTIFIED,
    SpatialAnnotation,
    Track,
)


LEGACY_IDENTIFIED_SOURCES = ("validated", "manual", "cvat")


def is_authoritative_identification(value: Any) -> bool:
    """Vrai pour une identification relue ou un legacy humain sûr.

    Un statut explicite est toujours souverain. Le repli par ``source`` ne
    s'applique qu'à un vrai SQL ``NULL`` et jamais à ``unreviewed``.
    """
    node_id = getattr(value, "taxon_node_id", None)
    if not node_id or node_id == GENERIC_TAXON_ID:
        return False
    status = getattr(value, "identification_status", None)
    if status is not None:
        return status == STATUS_IDENTIFIED
    return getattr(value, "source", None) in LEGACY_IDENTIFIED_SOURCES


def authoritative_identification_clause(model: Any):
    """Expression SQLAlchemy strictement équivalente au prédicat Python."""
    return and_(
        model.taxon_node_id.isnot(None),
        model.taxon_node_id != GENERIC_TAXON_ID,
        or_(
            model.identification_status == STATUS_IDENTIFIED,
            and_(
                model.identification_status.is_(None),
                model.source.in_(LEGACY_IDENTIFIED_SOURCES),
            ),
        ),
    )


def resolve_track_taxa(
    session: Session,
    tracks: Iterable[Track],
) -> dict[str, str | None]:
    """Résout l'unique taxon autoritaire de chaque piste.

    Un statut de piste explicite est souverain. Pour une piste historique dont
    le statut est SQL NULL, seules toutes les annotations autoritaires liées
    votent : un taxon unique est accepté, zéro ou plusieurs donnent ``None``.
    Le taxon historique porté par la piste n'intervient jamais dans ce repli.
    """
    rows = list(tracks)
    track_ids = [track.id for track in rows]
    votes: dict[str, set[str]] = defaultdict(set)
    if track_ids:
        for track_id, taxon_node_id in session.execute(
            select(
                SpatialAnnotation.track_id,
                SpatialAnnotation.taxon_node_id,
            ).where(
                SpatialAnnotation.track_id.in_(track_ids),
                authoritative_identification_clause(SpatialAnnotation),
            )
        ):
            if track_id and taxon_node_id:
                votes[track_id].add(taxon_node_id)

    resolved: dict[str, str | None] = {}
    for track in rows:
        if track.identification_status is not None:
            resolved[track.id] = (
                track.taxon_node_id
                if is_authoritative_identification(track)
                else None
            )
            continue
        linked_taxa = votes.get(track.id, set())
        resolved[track.id] = (
            next(iter(linked_taxa)) if len(linked_taxa) == 1 else None
        )
    return resolved
