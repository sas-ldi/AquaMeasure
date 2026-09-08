"""Track and trajectory sample CRUD."""

from __future__ import annotations

import json
import uuid
from typing import Any, Dict, Iterable, List, Optional, Tuple

from sqlalchemy import delete as sa_delete, func, select
from sqlalchemy.orm import Session

from .models import (
    STATUS_AMBIGUOUS,
    SpatialAnnotation,
    TaxonNode,
    TemporalEvent,
    Track,
    TrackSample,
)


def get_or_create_track(
    session: Session,
    *,
    media_id: str,
    external_track_id: int,
    source: str = "bytetrack",
    taxon_node_id: Optional[str] = None,
) -> Track:
    existing = session.scalar(
        select(Track).where(
            Track.media_id == media_id,
            Track.external_track_id == external_track_id,
            Track.source == source,
        )
    )
    if existing:
        return existing
    track = Track(
        id=str(uuid.uuid4()),
        media_id=media_id,
        external_track_id=external_track_id,
        source=source,
        taxon_node_id=taxon_node_id,
        first_frame=0,
        last_frame=0,
    )
    session.add(track)
    session.flush()
    return track


def set_track_taxon(
    session: Session,
    track_id: Optional[str],
    taxon_node_id: Optional[str],
    *,
    identification_status: Optional[str] = None,
) -> bool:
    """Pose le taxon sur une piste - propagation depuis une observation validée.

    `tracks.taxon_node_id` était vide à 100 % : `max_per_species` (le MaxN par
    espèce) ne pouvait donc rien produire. Identifier un poisson sur une bbox
    suivie renseigne maintenant sa piste.
    """
    if not track_id:
        return False
    track = session.get(Track, track_id)
    if track is None:
        return False
    if (
        track.taxon_node_id == taxon_node_id
        and track.identification_status == identification_status
    ):
        return False
    track.taxon_node_id = taxon_node_id
    track.identification_status = identification_status
    session.flush()
    return True


def recompute_track_taxonomy(session: Session, track_id: Optional[str]) -> bool:
    """Recalcule l'autorité d'une piste depuis ses observations restantes.

    Une détermination unique est propagée. Des taxons humains conflictuels ne
    sont jamais départagés arbitrairement : la piste devient ``ambiguous`` et
    ne porte plus de taxon, donc elle ne peut pas gonfler les statistiques ou
    être exportée comme espèce identifiée. Sans autorité, les deux champs sont
    effacés.
    """
    if not track_id:
        return False
    track = session.get(Track, track_id)
    if track is None:
        return False
    from .identification import is_authoritative_identification

    annotations = session.scalars(
        select(SpatialAnnotation)
        .where(SpatialAnnotation.track_id == track_id)
        .order_by(SpatialAnnotation.id)
    ).all()
    authorities = [ann for ann in annotations if is_authoritative_identification(ann)]
    taxa = sorted({str(ann.taxon_node_id) for ann in authorities})
    if not taxa:
        taxon_id = None
        status = None
    elif len(taxa) == 1:
        taxon_id = taxa[0]
        status = "identified"
    else:
        taxon_id = None
        status = STATUS_AMBIGUOUS
    return set_track_taxon(
        session,
        track_id,
        taxon_id,
        identification_status=status,
    )


def _bbox_json(bbox: Tuple[float, float, float, float]) -> str:
    x1, y1, x2, y2 = bbox
    return json.dumps({"x_min": x1, "y_min": y1, "x_max": x2, "y_max": y2})


def _widen_track_bounds(track: Optional[Track], frame_index: int) -> None:
    if track is None:
        return
    if track.first_frame == 0 and track.last_frame == 0:
        track.first_frame = frame_index
        track.last_frame = frame_index
    else:
        track.first_frame = min(track.first_frame, frame_index)
        track.last_frame = max(track.last_frame, frame_index)


def add_track_sample(
    session: Session,
    *,
    track_id: str,
    frame_index: int,
    cx: float,
    cy: float,
    bbox: Tuple[float, float, float, float],
    origin: str = "auto",
    edited_by: Optional[str] = None,
    overwrite_keyframe: bool = False,
) -> TrackSample:
    """Insère ou met à jour l'échantillon (track_id, frame_index).

    Une keyframe posée à la main n'est pas écrasée par le tracker : seul un
    appel explicite avec `overwrite_keyframe` peut la remplacer.
    """
    existing = session.scalar(
        select(TrackSample).where(
            TrackSample.track_id == track_id,
            TrackSample.frame_index == frame_index,
        )
    )
    if existing is not None:
        protected = existing.origin == "keyframe" and origin != "keyframe"
        if protected and not overwrite_keyframe:
            return existing
        existing.cx = cx
        existing.cy = cy
        existing.bbox_json = _bbox_json(bbox)
        existing.origin = origin
        if edited_by is not None:
            existing.edited_by = edited_by
        _widen_track_bounds(session.get(Track, track_id), frame_index)
        session.flush()
        return existing

    sample = TrackSample(
        track_id=track_id,
        frame_index=frame_index,
        cx=cx,
        cy=cy,
        bbox_json=_bbox_json(bbox),
        origin=origin,
        edited_by=edited_by,
    )
    session.add(sample)
    _widen_track_bounds(session.get(Track, track_id), frame_index)
    session.flush()
    return sample


def add_track_samples_bulk(
    session: Session,
    *,
    track_id: str,
    rows: Iterable[Tuple[int, float, float, Tuple[float, float, float, float]]],
    origin: str = "auto",
) -> int:
    """Écrit un lot d'échantillons en une passe (un seul flush)."""
    rows = list(rows)
    if not rows:
        return 0
    frames = [int(r[0]) for r in rows]
    existing = {
        s.frame_index: s
        for s in session.scalars(
            select(TrackSample).where(
                TrackSample.track_id == track_id,
                TrackSample.frame_index.in_(frames),
            )
        )
    }
    track = session.get(Track, track_id)
    written = 0
    for frame_index, cx, cy, bbox in rows:
        frame_index = int(frame_index)
        current = existing.get(frame_index)
        if current is not None:
            if current.origin == "keyframe" and origin != "keyframe":
                continue
            current.cx = cx
            current.cy = cy
            current.bbox_json = _bbox_json(bbox)
            current.origin = origin
        else:
            session.add(
                TrackSample(
                    track_id=track_id,
                    frame_index=frame_index,
                    cx=cx,
                    cy=cy,
                    bbox_json=_bbox_json(bbox),
                    origin=origin,
                )
            )
        _widen_track_bounds(track, frame_index)
        written += 1
    session.flush()
    return written


def update_track_sample_3d(
    session: Session,
    sample_id: int,
    *,
    position_x_mm: Optional[float],
    position_y_mm: Optional[float],
    position_z_mm: Optional[float],
    match_score: Optional[float] = None,
    match_method: Optional[str] = None,
) -> None:
    sample = session.get(TrackSample, sample_id)
    if sample is None:
        return
    sample.position_x_mm = position_x_mm
    sample.position_y_mm = position_y_mm
    sample.position_z_mm = position_z_mm
    sample.match_score = match_score
    sample.match_method = match_method
    session.flush()


def list_track_samples(session: Session, track_id: str) -> List[TrackSample]:
    return list(
        session.scalars(
            select(TrackSample)
            .where(TrackSample.track_id == track_id)
            .order_by(TrackSample.frame_index)
        )
    )


def list_tracks_for_media(session: Session, media_id: str) -> List[Track]:
    return list(session.scalars(select(Track).where(Track.media_id == media_id)))


def trajectory_dict(session: Session, track_id: str) -> List[Dict]:
    out = []
    for s in list_track_samples(session, track_id):
        bbox = json.loads(s.bbox_json)
        out.append(
            {
                "frame": s.frame_index,
                "cx": s.cx,
                "cy": s.cy,
                "bbox": bbox,
                "origin": s.origin,
                "position_x_mm": s.position_x_mm,
                "position_y_mm": s.position_y_mm,
                "position_z_mm": s.position_z_mm,
                "match_score": s.match_score,
                "match_method": s.match_method,
            }
        )
    return out


def refresh_track_bounds(session: Session, track_id: str) -> None:
    """Recale first_frame/last_frame après une suppression d'échantillons."""
    track = session.get(Track, track_id)
    if track is None:
        return
    bounds = session.execute(
        select(func.min(TrackSample.frame_index), func.max(TrackSample.frame_index))
        .where(TrackSample.track_id == track_id)
    ).one()
    track.first_frame = int(bounds[0] or 0)
    track.last_frame = int(bounds[1] or 0)
    session.flush()


def _neighbour_keyframe(
    session: Session, track_id: str, frame_index: int, *, before: bool
) -> Optional[TrackSample]:
    stmt = select(TrackSample).where(
        TrackSample.track_id == track_id,
        TrackSample.origin == "keyframe",
    )
    if before:
        stmt = stmt.where(TrackSample.frame_index < frame_index).order_by(
            TrackSample.frame_index.desc()
        )
    else:
        stmt = stmt.where(TrackSample.frame_index > frame_index).order_by(
            TrackSample.frame_index
        )
    return session.scalars(stmt.limit(1)).first()


def _lerp_bbox(a: Dict[str, float], b: Dict[str, float], t: float) -> Dict[str, float]:
    """Interpole via centre/taille : plus stable visuellement que coin par coin."""
    def cx(box):
        return (box["x_min"] + box["x_max"]) * 0.5

    def cy(box):
        return (box["y_min"] + box["y_max"]) * 0.5

    ax, ay = cx(a), cy(a)
    aw, ah = a["x_max"] - a["x_min"], a["y_max"] - a["y_min"]
    bx, by = cx(b), cy(b)
    bw, bh = b["x_max"] - b["x_min"], b["y_max"] - b["y_min"]

    mx = ax + (bx - ax) * t
    my = ay + (by - ay) * t
    mw = aw + (bw - aw) * t
    mh = ah + (bh - ah) * t
    return {
        "x_min": mx - mw * 0.5,
        "y_min": my - mh * 0.5,
        "x_max": mx + mw * 0.5,
        "y_max": my + mh * 0.5,
    }


def interpolate_between(
    session: Session,
    track_id: str,
    frame_a: int,
    frame_b: int,
    *,
    img_w: int,
    img_h: int,
) -> int:
    """Remplit les frames strictement comprises entre deux échantillons connus.

    Les keyframes présentes dans l'intervalle sont préservées.
    """
    if frame_b - frame_a <= 1:
        return 0
    ends = {
        s.frame_index: s
        for s in session.scalars(
            select(TrackSample).where(
                TrackSample.track_id == track_id,
                TrackSample.frame_index.in_([frame_a, frame_b]),
            )
        )
    }
    start, end = ends.get(frame_a), ends.get(frame_b)
    if start is None or end is None:
        return 0

    box_a = json.loads(start.bbox_json)
    box_b = json.loads(end.bbox_json)
    span = float(frame_b - frame_a)
    rows = []
    for frame in range(frame_a + 1, frame_b):
        box = _lerp_bbox(box_a, box_b, (frame - frame_a) / span)
        cx = (box["x_min"] + box["x_max"]) * 0.5 / max(1, img_w)
        cy = (box["y_min"] + box["y_max"]) * 0.5 / max(1, img_h)
        rows.append(
            (frame, cx, cy, (box["x_min"], box["y_min"], box["x_max"], box["y_max"]))
        )
    return add_track_samples_bulk(
        session, track_id=track_id, rows=rows, origin="interpolated"
    )


def set_keyframe(
    session: Session,
    track_id: str,
    frame_index: int,
    bbox: Tuple[float, float, float, float],
    *,
    img_w: int,
    img_h: int,
    author: Optional[str] = None,
    reinterpolate: bool = True,
) -> TrackSample:
    """Pose une position validée à la main et ré-interpole les segments voisins."""
    x1, y1, x2, y2 = bbox
    cx = (x1 + x2) * 0.5 / max(1, img_w)
    cy = (y1 + y2) * 0.5 / max(1, img_h)
    sample = add_track_sample(
        session,
        track_id=track_id,
        frame_index=frame_index,
        cx=cx,
        cy=cy,
        bbox=bbox,
        origin="keyframe",
        edited_by=author,
        overwrite_keyframe=True,
    )
    if reinterpolate:
        previous = _neighbour_keyframe(session, track_id, frame_index, before=True)
        following = _neighbour_keyframe(session, track_id, frame_index, before=False)
        if previous is not None:
            interpolate_between(
                session, track_id, previous.frame_index, frame_index,
                img_w=img_w, img_h=img_h,
            )
        if following is not None:
            interpolate_between(
                session, track_id, frame_index, following.frame_index,
                img_w=img_w, img_h=img_h,
            )
    return sample


def delete_samples_range(
    session: Session, track_id: str, frame_start: int, frame_end: int
) -> int:
    result = session.execute(
        sa_delete(TrackSample).where(
            TrackSample.track_id == track_id,
            TrackSample.frame_index >= frame_start,
            TrackSample.frame_index <= frame_end,
        )
    )
    session.flush()
    refresh_track_bounds(session, track_id)
    return int(result.rowcount or 0)


def split_track(session: Session, track_id: str, frame_index: int) -> Optional[Track]:
    """Coupe une piste : les frames >= frame_index partent dans une piste neuve.

    Sert quand le tracker a confondu deux poissons sous un même identifiant.
    """
    source = session.get(Track, track_id)
    if source is None:
        return None
    tail = list(
        session.scalars(
            select(TrackSample).where(
                TrackSample.track_id == track_id,
                TrackSample.frame_index >= frame_index,
            )
        )
    )
    if not tail:
        return None

    next_ext = session.scalar(
        select(func.max(Track.external_track_id)).where(Track.media_id == source.media_id)
    )
    new_track = Track(
        id=str(uuid.uuid4()),
        media_id=source.media_id,
        external_track_id=int(next_ext or 0) + 1,
        source=source.source,
        taxon_node_id=source.taxon_node_id,
        first_frame=0,
        last_frame=0,
    )
    session.add(new_track)
    session.flush()
    for sample in tail:
        sample.track_id = new_track.id
    session.flush()
    refresh_track_bounds(session, track_id)
    refresh_track_bounds(session, new_track.id)
    return new_track


def merge_tracks(session: Session, source_track_id: str, target_track_id: str) -> int:
    """Recolle une piste sur une autre - cas d'un identifiant perdu puis recréé.

    En cas de collision sur une frame, l'échantillon de la piste cible gagne.
    """
    if source_track_id == target_track_id:
        return 0
    target_frames = {
        f for (f,) in session.execute(
            select(TrackSample.frame_index).where(
                TrackSample.track_id == target_track_id
            )
        )
    }
    moved = 0
    for sample in list_track_samples(session, source_track_id):
        if sample.frame_index in target_frames:
            session.delete(sample)
            continue
        sample.track_id = target_track_id
        moved += 1
    session.flush()

    source = session.get(Track, source_track_id)
    if source is not None:
        session.delete(source)
    session.flush()
    refresh_track_bounds(session, target_track_id)
    return moved


# ── Validation de pistes : lecture, drapeaux, suppression ──────────────────
#
# Le tracker produit beaucoup de bruit : sur la base réelle, 246 pistes ne
# comptent qu'une seule frame (faux positifs probables), 23 s'étendent sur plus
# de 7000 frames (fusions d'identités probables) et 61 % ont des trous. Rien
# dans l'interface ne permettait de le voir, encore moins de le corriger, alors
# que `split_track` / `merge_tracks` / `set_keyframe` existent depuis
# longtemps. Ces fonctions-ci ne réécrivent aucune logique d'édition : elles
# donnent de quoi **repérer** les pistes douteuses et supprimer celles qui n'ont
# rien à faire là.

# Seuils par défaut, en frames. Ils ne sont pas des vérités : un trou d'une
# seconde (30 images) est déjà suspect sur une nage continue, et une piste de
# plus d'une minute sur un poisson qui traverse le champ l'est tout autant.
DEFAULT_GAP_THRESHOLD = 30
DEFAULT_LONG_TRACK_FRAMES = 1800


def _track_gaps(frames: List[int]) -> Tuple[int, int]:
    """(nombre de trous, plus grand trou) - un « trou » = plus d'une frame sautée.

    Le suivi échantillonne souvent une frame sur deux : compter cela comme un
    trou noierait les vraies pertes de piste sous des centaines de faux
    positifs. Le pas médian sert donc de référence, et seul un écart qui le
    dépasse franchement compte.
    """
    if len(frames) < 2:
        return 0, 0
    steps = [b - a for a, b in zip(frames, frames[1:])]
    ordered = sorted(steps)
    median = ordered[len(ordered) // 2]
    baseline = max(1, median)
    gaps = [step - baseline for step in steps if step > baseline]
    return len(gaps), (max(gaps) if gaps else 0)


def track_overview(
    session: Session,
    media_id: str,
    *,
    gap_threshold: int = DEFAULT_GAP_THRESHOLD,
    long_track_frames: int = DEFAULT_LONG_TRACK_FRAMES,
) -> List[Dict[str, Any]]:
    """Les pistes d'un média, avec de quoi juger lesquelles sont douteuses.

    Trié par **suspicion décroissante** : l'opérateur doit tomber d'abord sur
    ce qui mérite son attention, pas sur la première piste par ordre
    d'identifiant. Le score est explicite et borné (0-100) ; les drapeaux
    (`single_frame`, `has_gaps`, `very_long`) disent lequel de ses termes pèse,
    pour qu'on ne demande jamais de faire confiance à un nombre seul.
    """
    tracks = list_tracks_for_media(session, media_id)
    if not tracks:
        return []
    ids = [track.id for track in tracks]

    frames_by_track: Dict[str, List[int]] = {track_id: [] for track_id in ids}
    origins_by_track: Dict[str, Dict[str, int]] = {track_id: {} for track_id in ids}
    for track_id, frame_index, origin in session.execute(
        select(TrackSample.track_id, TrackSample.frame_index, TrackSample.origin)
        .where(TrackSample.track_id.in_(ids))
        .order_by(TrackSample.track_id, TrackSample.frame_index)
    ):
        frames_by_track[track_id].append(int(frame_index))
        counts = origins_by_track[track_id]
        counts[origin or "auto"] = counts.get(origin or "auto", 0) + 1

    annotations: Dict[str, int] = {track_id: 0 for track_id in ids}
    for track_id, count in session.execute(
        select(SpatialAnnotation.track_id, func.count(SpatialAnnotation.id))
        .where(SpatialAnnotation.track_id.in_(ids))
        .group_by(SpatialAnnotation.track_id)
    ):
        annotations[track_id] = int(count)

    events: Dict[str, int] = {track_id: 0 for track_id in ids}
    manual_events: Dict[str, int] = {track_id: 0 for track_id in ids}
    for track_id, source, count in session.execute(
        select(TemporalEvent.track_id, TemporalEvent.source, func.count(TemporalEvent.id))
        .where(TemporalEvent.track_id.in_(ids))
        .group_by(TemporalEvent.track_id, TemporalEvent.source)
    ):
        events[track_id] = events.get(track_id, 0) + int(count)
        if source == "manual":
            manual_events[track_id] = manual_events.get(track_id, 0) + int(count)

    taxa = {
        node.id: node for node in session.scalars(
            select(TaxonNode).where(TaxonNode.id.in_([
                t.taxon_node_id for t in tracks if t.taxon_node_id
            ] or [""]))
        )
    }

    rows: List[Dict[str, Any]] = []
    for track in tracks:
        frames = frames_by_track[track.id]
        samples = len(frames)
        first = frames[0] if frames else int(track.first_frame or 0)
        last = frames[-1] if frames else int(track.last_frame or 0)
        span = max(0, last - first) + (1 if frames else 0)
        coverage = (samples / span) if span else 0.0
        gap_count, max_gap = _track_gaps(frames)
        taxon = taxa.get(track.taxon_node_id) if track.taxon_node_id else None

        single_frame = samples <= 1
        very_long = span >= long_track_frames
        has_gaps = max_gap >= gap_threshold

        # Score : la somme de ce qui rend une piste suspecte, plafonnée à 100.
        # Une piste d'une frame est le cas le plus net (faux positif) ; une
        # piste très longue vient ensuite (fusion d'identités probable).
        score = 0.0
        if single_frame:
            score += 60.0
        if very_long:
            score += 45.0
        if has_gaps:
            score += 25.0 + min(20.0, max_gap / max(1, gap_threshold) * 5.0)
        if samples > 1 and coverage < 0.5:
            score += (0.5 - coverage) * 20.0

        rows.append({
            "track_id": track.id,
            "external_track_id": int(track.external_track_id or 0),
            "media_id": track.media_id,
            "source": track.source,
            "first_frame": first,
            "last_frame": last,
            "span": span,
            "sample_count": samples,
            "coverage": round(coverage, 4),
            "gap_count": gap_count,
            "max_gap": max_gap,
            "origins": dict(sorted(origins_by_track[track.id].items())),
            "taxon_node_id": track.taxon_node_id,
            "taxon": taxon.scientific_name if taxon is not None else None,
            "annotation_count": annotations.get(track.id, 0),
            "event_count": events.get(track.id, 0),
            "manual_event_count": manual_events.get(track.id, 0),
            "single_frame": single_frame,
            "has_gaps": has_gaps,
            "very_long": very_long,
            "suspicion": round(min(100.0, score), 1),
        })

    rows.sort(
        key=lambda row: (-row["suspicion"], row["external_track_id"], row["track_id"])
    )
    return rows


def delete_track(
    session: Session, track_id: str, *, force: bool = False,
) -> Dict[str, Any]:
    """Supprime une piste et ses échantillons. Retour : ce qui a été fait.

    Trois garde-fous, parce qu'une piste n'est pas isolée :

    - les **observations** rattachées (`spatial_annotations.track_id`) sont
      détachées, pas supprimées : ce sont des identifications humaines, elles
      valent indépendamment de la trajectoire ;
    - les **événements manuels** bloquent la suppression sauf `force=True` : un
      intervalle de broute annoté à la main est du travail humain, il ne
      disparaît pas au détour d'un ménage de pistes ;
    - les événements `heuristic` partent avec la piste - ils en sont dérivés et
      n'ont aucun sens sans elle.
    """
    track = session.get(Track, track_id)
    if track is None:
        return {"deleted": False, "reason": "piste introuvable"}

    manual = int(session.scalar(
        select(func.count(TemporalEvent.id)).where(
            TemporalEvent.track_id == track_id,
            TemporalEvent.source == "manual",
        )
    ) or 0)
    if manual and not force:
        return {
            "deleted": False,
            "reason": (
                f"{manual} événement(s) annoté(s) à la main sur cette piste - "
                "supprimez-les d'abord, ou confirmez la suppression"
            ),
            "manual_events": manual,
        }

    detached = 0
    for ann in session.scalars(
        select(SpatialAnnotation).where(SpatialAnnotation.track_id == track_id)
    ):
        ann.track_id = None
        detached += 1

    removed_events = int(session.execute(
        sa_delete(TemporalEvent).where(TemporalEvent.track_id == track_id)
    ).rowcount or 0)
    removed_samples = int(session.execute(
        sa_delete(TrackSample).where(TrackSample.track_id == track_id)
    ).rowcount or 0)
    session.delete(track)
    session.flush()
    return {
        "deleted": True,
        "reason": "",
        "samples": removed_samples,
        "events": removed_events,
        "annotations_detached": detached,
        "manual_events": manual,
    }
