"""Taxonomy tree operations."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Dict, List, Optional

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .models import SpatialAnnotation, TaxonNode, Track

RANK_ORDER = ["family", "genus", "species", "provisional"]


def get_node(session: Session, node_id: str) -> Optional[TaxonNode]:
    return session.get(TaxonNode, node_id)


def get_node_by_name(session: Session, scientific_name: str) -> Optional[TaxonNode]:
    return session.scalar(
        select(TaxonNode).where(TaxonNode.scientific_name == scientific_name)
    )


def list_children(session: Session, parent_id: Optional[str]) -> List[TaxonNode]:
    q = select(TaxonNode).where(TaxonNode.parent_id == parent_id).order_by(TaxonNode.scientific_name)
    return list(session.scalars(q))


def add_taxon(
    session: Session,
    *,
    rank: str,
    scientific_name: str,
    parent_id: Optional[str] = None,
    common_name: Optional[str] = None,
    is_provisional: bool = False,
    node_id: Optional[str] = None,
) -> TaxonNode:
    node = TaxonNode(
        id=node_id or str(uuid.uuid4()),
        parent_id=parent_id,
        rank=rank,
        scientific_name=scientific_name,
        common_name=common_name,
        is_provisional=is_provisional,
    )
    session.add(node)
    session.flush()
    return node


def _ancestors(session: Session, node_id: str) -> List[TaxonNode]:
    chain: List[TaxonNode] = []
    current = session.get(TaxonNode, node_id)
    while current:
        chain.append(current)
        if not current.parent_id:
            break
        current = session.get(TaxonNode, current.parent_id)
    return chain


def resolve_to_rank(session: Session, node_id: str, target_rank: str) -> Optional[str]:
    """
    Walk up the tree to find the ancestor at target_rank.
    For target_rank='fish', return generic fish node id.
    """
    if target_rank == "fish":
        generic = get_node_by_name(session, "fish")
        return generic.id if generic else node_id

    for node in _ancestors(session, node_id):
        if node.rank == target_rank:
            return node.id
    return None


def resolve_to_rank_name(session: Session, node_id: str, target_rank: str) -> str:
    resolved_id = resolve_to_rank(session, node_id, target_rank)
    if not resolved_id:
        node = session.get(TaxonNode, node_id)
        return node.scientific_name if node else "unknown"
    node = session.get(TaxonNode, resolved_id)
    return node.scientific_name if node else "unknown"


def reclassify_annotations(
    session: Session,
    old_node_id: str,
    new_node_id: str,
    *,
    include_tracks: bool = True,
) -> int:
    """Move all spatial annotations (and optionally tracks) from old to new taxon."""
    count = 0
    for ann in session.scalars(
        select(SpatialAnnotation).where(SpatialAnnotation.taxon_node_id == old_node_id)
    ):
        ann.taxon_node_id = new_node_id
        ann.is_provisional = False
        count += 1
    if include_tracks:
        session.execute(
            update(Track)
            .where(Track.taxon_node_id == old_node_id)
            .values(taxon_node_id=new_node_id)
        )
    old = session.get(TaxonNode, old_node_id)
    if old:
        old.updated_at = datetime.utcnow()
    session.flush()
    return count


def build_class_map(session: Session, target_rank: str) -> Dict[str, int]:
    """Map taxon scientific_name at target_rank -> class index."""
    if target_rank == "fish":
        return {"fish": 0}

    nodes = list(
        session.scalars(
            select(TaxonNode).where(TaxonNode.rank == target_rank).order_by(TaxonNode.scientific_name)
        )
    )
    return {n.scientific_name: i for i, n in enumerate(nodes)}


def taxonomy_tree_dict(session: Session, root_id: Optional[str] = None) -> List[dict]:
    """Return nested dict for display."""
    def _build(parent_id: Optional[str]) -> List[dict]:
        out = []
        for node in list_children(session, parent_id):
            out.append(
                {
                    "id": node.id,
                    "rank": node.rank,
                    "scientific_name": node.scientific_name,
                    "common_name": node.common_name,
                    "is_provisional": node.is_provisional,
                    "children": _build(node.id),
                }
            )
        return out

    return _build(root_id)
