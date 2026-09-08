"""Comportement : CSV type AVA (spatio-temporel) et JSONL d'intervalles.

Deux vues du **même** contenu - `temporal_events`, l'intervalle sur piste, seule
forme réellement éditée par l'opérateur :

- `events.csv` + `actions.csv` - vue **apprentissage** : « cette boîte, cet
  individu, cette action, cet instant ». C'est ce que demande un modèle de
  reconnaissance d'action ; ActivityNet ou THUMOS ne savent dire que « il y a du
  broutage quelque part dans la vidéo », ce qui ne permet pas d'attribuer une
  bouchée à un poisson.
- `events.jsonl` - vue **revue humaine et écologie** : un intervalle par ligne,
  avec sa durée, sa piste, son taxon et son auteur. C'est là qu'on lit la durée
  totale de broutage et la fréquence de bouchées par minute.

Deux règles non négociables
---------------------------
1. **Seuls les événements `source='manual'` entrent dans le dataset AVA.** Les
   événements `heuristic` sont des présomptions produites par une règle
   automatique ; les apprendre comme vérité terrain reviendrait à entraîner un
   modèle sur les sorties d'un autre, sans jamais mesurer l'écart. Ils sont
   exclus **avec leur raison** et comptés au manifeste, jamais effacés.
2. **On n'invente pas de position.** La densification demande une boîte à un
   instant donné ; s'il n'y a pas d'échantillon à cette frame, on interpole
   entre les deux voisins **et seulement** s'ils sont assez proches (1 s par
   défaut). Au-delà, la ligne est sautée et comptée : un trou de suivi de dix
   secondes ne doit pas devenir une trajectoire lissée.
"""

from __future__ import annotations

import json
import logging
from bisect import bisect_left
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import frame_ref as fref
from . import sessions as sessions_mod
from .event_types import list_event_types
from .export_media import ExportExclusions
from .models import MediaAsset, TaxonNode, TemporalEvent, Track, TrackSample

log = logging.getLogger(__name__)

EVENTS_CSV = "events.csv"
ACTIONS_CSV = "actions.csv"
EVENTS_JSONL = "events.jsonl"

# En-têtes AVA. L'ordre est le contrat ; le nom des colonnes est écrit en
# première ligne parce qu'un CSV que personne ne peut lire sans documentation
# n'est pas un livrable (les chargeurs AVA lisent avec `skiprows=1`).
AVA_COLUMNS = (
    "video_id", "timestamp_s", "x1", "y1", "x2", "y2", "action_id", "track_id",
)
ACTIONS_COLUMNS = (
    "action_id", "key", "label", "scope", "is_builtin", "event_type_id", "description",
)

DEFAULT_AVA_HZ = 1.0
DEFAULT_AVA_MAX_GAP_S = 1.0

SOURCE_MANUAL = "manual"

REASON_EVENT_NON_MANUEL = "evenement_non_manuel"
REASON_EVENT_PISTE_INTROUVABLE = "evenement_sans_piste_connue"
REASON_EVENT_MEDIA_INTROUVABLE = "evenement_sans_media_connu"
REASON_EVENT_FPS_INCONNU = "media_sans_frequence_image"
REASON_EVENT_TYPE_INACTIF = "type_d_evenement_inconnu_ou_inactif"
REASON_EVENT_OFFSET = fref.REASON_OFFSET_INDETERMINABLE
REASON_AVA_SANS_BOITE = "aucune_bbox_a_cet_instant"

INTERPOLATION_RULE = (
    "bbox absente à la frame demandée : interpolation linéaire entre les deux "
    "échantillons voisins de la piste si l'écart entre eux ne dépasse pas "
    "ava_max_gap_s ; au-delà la ligne est sautée et comptée (aucune position "
    "n'est inventée sur un trou de suivi)"
)


def action_ids(rows: Sequence[Dict[str, Any]]) -> Dict[str, int]:
    """`key` de type d'événement → `action_id` entier, déterministe.

    AVA numérote ses actions ; la base, elle, identifie un type par son `key`
    et son UUID. `action_id` est donc un **index dense propre à cet export**,
    calculé sur `(sort_order, key)` : la même base ré-exportée donne les mêmes
    numéros. L'identité durable reste `key` / `event_type_id`, tous deux écrits
    dans `actions.csv` - sans quoi ajouter un comportement décalerait
    silencieusement la signification des anciens fichiers.
    """
    ordered = sorted(rows, key=lambda r: (int(r.get("sortOrder") or 0), str(r.get("key"))))
    return {str(row["key"]): index + 1 for index, row in enumerate(ordered)}


def active_action_table(
    session: Session,
    referenced_keys: Optional[Set[str]] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """Labels actifs, plus les types désactivés référencés par l'historique."""
    referenced = referenced_keys or set()
    rows = [
        row for row in list_event_types(session, active_only=False, with_usage=True)
        if row.get("isActive") or row.get("key") in referenced
    ]
    return rows, action_ids(rows)


def _samples_by_track(session: Session, track_ids: Sequence[str]) -> Dict[str, List[Tuple[int, Tuple[float, float, float, float], str]]]:
    """Trajectoires triées, prêtes pour la recherche dichotomique."""
    wanted = set(track_ids)
    out: Dict[str, List[Tuple[int, Tuple[float, float, float, float], str]]] = defaultdict(list)
    if not wanted:
        return out
    for sample in session.scalars(select(TrackSample).order_by(TrackSample.frame_index)):
        if sample.track_id not in wanted:
            continue
        try:
            box = json.loads(sample.bbox_json)
            corners = (
                float(box["x_min"]), float(box["y_min"]),
                float(box["x_max"]), float(box["y_max"]),
            )
        except (TypeError, ValueError, KeyError):
            continue
        out[sample.track_id].append((int(sample.frame_index), corners, sample.origin))
    for rows in out.values():
        rows.sort(key=lambda row: row[0])
    return out


def box_at_frame(
    samples: Sequence[Tuple[int, Tuple[float, float, float, float], str]],
    frame: int,
    *,
    max_gap_frames: int,
) -> Tuple[Optional[Tuple[float, float, float, float]], str]:
    """Boîte de la piste à cette frame. Retour : `(boîte ou None, méthode)`.

    `méthode` vaut `exact`, `interpolated` ou une raison d'échec - c'est elle
    que le manifeste compte.
    """
    if not samples:
        return None, "piste_sans_echantillon"
    frames = [row[0] for row in samples]
    position = bisect_left(frames, frame)
    if position < len(frames) and frames[position] == frame:
        return samples[position][1], "exact"
    if position == 0 or position >= len(frames):
        # Hors de l'étendue suivie : extrapoler inventerait une position.
        return None, "hors_etendue_de_la_piste"
    before_frame, before_box, _ = samples[position - 1]
    after_frame, after_box, _ = samples[position]
    if after_frame - before_frame > max_gap_frames:
        return None, "trou_de_suivi_trop_large"
    ratio = (frame - before_frame) / float(after_frame - before_frame)
    return tuple(
        before_box[i] + (after_box[i] - before_box[i]) * ratio for i in range(4)
    ), "interpolated"  # type: ignore[return-value]


def collect_events(
    session: Session,
    project_ids: Optional[Sequence[str]] = None,
    *,
    media_ids: Optional[Sequence[str]] = None,
    exclusions: Optional[ExportExclusions] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, MediaAsset], Dict[str, Track]]:
    """Intervalles exportables, index de frame ramenés en **absolu**.

    `temporal_events.frame_ref` peut valoir `timeline_legacy` : l'offset figé de
    la session prime, `sync_frames.npy` sert de repli, et un événement dont
    l'offset reste indéterminable est **exclu avec sa raison** plutôt
    qu'exporté quelques milliers de frames à côté.
    """
    tracks_by_id = {t.id: t for t in session.scalars(select(Track))}
    media_by_id = {m.id: m for m in session.scalars(select(MediaAsset))}
    taxa = {t.id: t for t in session.scalars(select(TaxonNode))}
    current_sync = fref.timeline_offset()
    offset_cache: Dict[str, Optional[int]] = {}
    wanted_media = set(media_ids or [])

    def _offset_for(media_id: str) -> Optional[int]:
        if media_id not in offset_cache:
            frozen = sessions_mod.frame_offset_for_media(session, media_id)
            offset_cache[media_id] = frozen if frozen is not None else current_sync
        return offset_cache[media_id]

    rows: List[Dict[str, Any]] = []
    for event in session.scalars(
        select(TemporalEvent).order_by(TemporalEvent.frame_start, TemporalEvent.id)
    ):
        track = tracks_by_id.get(event.track_id)
        if track is None:
            if exclusions is not None:
                exclusions.add(
                    event.id, REASON_EVENT_PISTE_INTROUVABLE,
                    detail=f"piste {event.track_id}",
                )
            continue
        media = media_by_id.get(track.media_id)
        if media is None:
            if exclusions is not None:
                exclusions.add(
                    event.id, REASON_EVENT_MEDIA_INTROUVABLE, media_id=track.media_id,
                )
            continue
        if wanted_media and media.id not in wanted_media:
            continue
        if project_ids and media.project_id not in project_ids:
            continue

        row_ref = fref.normalize(getattr(event, "frame_ref", None))
        offset = _offset_for(media.id)
        start = fref.to_absolute(event.frame_start, row_ref, offset)
        end = fref.to_absolute(event.frame_end, row_ref, offset)
        if start is None or end is None:
            if exclusions is not None:
                exclusions.add(
                    event.id, REASON_EVENT_OFFSET, media_id=media.id,
                    frame_index=event.frame_start,
                    detail="sync_frames.npy absent - offset timeline inconnu",
                )
            continue
        if end < start:
            start, end = end, start

        capture = sessions_mod.find_session_for_media(session, media.id)
        taxon = taxa.get(track.taxon_node_id) if track.taxon_node_id else None
        fps = float(media.fps) if media.fps else None
        frames = end - start + 1
        rows.append({
            "event_id": event.id,
            "event_type": event.event_type,
            "track_id": track.id,
            "external_track_id": track.external_track_id,
            "media_id": media.id,
            "media_name": Path(media.rel_path).name if media.rel_path else None,
            "session_id": capture.id if capture is not None else None,
            "session_name": capture.name if capture is not None else None,
            "frame_start": start,
            "frame_end": end,
            "frame_ref_source": row_ref,
            "duration_frames": frames,
            # Convention inclusive : une broute d'une seule frame dure 1/fps
            # seconde, pas zéro.
            "duration_s": round(frames / fps, 4) if fps else None,
            "fps": fps,
            "width": int(media.width) if media.width else None,
            "height": int(media.height) if media.height else None,
            "taxon_node_id": track.taxon_node_id,
            "taxon": taxon.scientific_name if taxon is not None else None,
            "taxon_rank": taxon.rank if taxon is not None else None,
            "source": event.source,
            "author": event.author,
            "confidence": event.confidence,
            "created_at": event.created_at.isoformat() if event.created_at else None,
        })
    return rows, media_by_id, tracks_by_id


def build_ava_rows(
    session: Session,
    events: Sequence[Dict[str, Any]],
    action_of_key: Dict[str, int],
    instance_of_track: Dict[str, int],
    *,
    ava_hz: float = DEFAULT_AVA_HZ,
    max_gap_s: float = DEFAULT_AVA_MAX_GAP_S,
    exclusions: Optional[ExportExclusions] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Densifie les intervalles manuels en lignes AVA, sans rien inventer."""
    kept = []
    stats: Counter = Counter()
    skipped: Counter = Counter()
    for event in events:
        if event["source"] != SOURCE_MANUAL:
            stats["evenements_non_manuels"] += 1
            if exclusions is not None:
                exclusions.add(
                    event["event_id"], REASON_EVENT_NON_MANUEL,
                    media_id=event["media_id"], frame_index=event["frame_start"],
                    detail=(
                        f"source '{event['source']}' - seule une annotation "
                        "humaine entre dans un dataset de comportement"
                    ),
                )
            continue
        if event["event_type"] not in action_of_key:
            stats["types_inconnus_ou_inactifs"] += 1
            if exclusions is not None:
                exclusions.add(
                    event["event_id"], REASON_EVENT_TYPE_INACTIF,
                    media_id=event["media_id"], detail=str(event["event_type"]),
                )
            continue
        if not event["fps"]:
            stats["medias_sans_fps"] += 1
            if exclusions is not None:
                exclusions.add(
                    event["event_id"], REASON_EVENT_FPS_INCONNU,
                    media_id=event["media_id"],
                    detail="timestamp_s impossible sans fréquence d'image",
                )
            continue
        kept.append(event)

    samples = _samples_by_track(session, [event["track_id"] for event in kept])
    rows: List[Dict[str, Any]] = []
    hz = max(float(ava_hz), 1e-6)

    for event in kept:
        fps = float(event["fps"])
        step = max(1, int(round(fps / hz)))
        max_gap_frames = max(1, int(round(float(max_gap_s) * fps)))
        width = float(event["width"] or 0) or None
        height = float(event["height"] or 0) or None
        trail = samples.get(event["track_id"], [])
        for frame in range(event["frame_start"], event["frame_end"] + 1, step):
            box, how = box_at_frame(trail, frame, max_gap_frames=max_gap_frames)
            if box is None or width is None or height is None:
                reason = how if box is None else "media_sans_dimensions"
                skipped[reason] += 1
                if exclusions is not None:
                    exclusions.add(
                        event["event_id"], REASON_AVA_SANS_BOITE,
                        media_id=event["media_id"], frame_index=frame,
                        detail=reason,
                    )
                continue
            x1 = min(max(box[0] / width, 0.0), 1.0)
            y1 = min(max(box[1] / height, 0.0), 1.0)
            x2 = min(max(box[2] / width, 0.0), 1.0)
            y2 = min(max(box[3] / height, 0.0), 1.0)
            stats[f"lignes_{how}"] += 1
            rows.append({
                "video_id": event["media_id"],
                "frame_index": frame,
                "timestamp_s": round(frame / fps, 3),
                "x1": round(x1, 6), "y1": round(y1, 6),
                "x2": round(x2, 6), "y2": round(y2, 6),
                "action_id": action_of_key[event["event_type"]],
                "action_key": event["event_type"],
                "track_id": instance_of_track.get(event["track_id"], 0),
                "track_db_id": event["track_id"],
                "event_id": event["event_id"],
                "box_method": how,
            })

    report = {
        "hz": float(ava_hz),
        "max_gap_s": float(max_gap_s),
        "interpolation_rule": INTERPOLATION_RULE,
        "events_total": len(events),
        "events_kept": len(kept),
        "row_count": len(rows),
        "rows_exact": stats.get("lignes_exact", 0),
        "rows_interpolated": stats.get("lignes_interpolated", 0),
        "rows_skipped": sum(skipped.values()),
        "rows_skipped_by_reason": dict(sorted(skipped.items())),
        "events_excluded": {
            "non_manuels": stats.get("evenements_non_manuels", 0),
            "types_inconnus_ou_inactifs": stats.get("types_inconnus_ou_inactifs", 0),
            "medias_sans_fps": stats.get("medias_sans_fps", 0),
        },
    }
    return rows, report


def write_ava(
    staging: Path,
    *,
    rows: Sequence[Dict[str, Any]],
    actions: Sequence[Dict[str, Any]],
    action_of_key: Dict[str, int],
) -> List[str]:
    """Écrit `events.csv` (8 colonnes AVA) et `actions.csv` (table de labels)."""
    import csv

    staging = Path(staging)
    staging.mkdir(parents=True, exist_ok=True)

    events_path = staging / EVENTS_CSV
    with events_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(AVA_COLUMNS)
        for row in sorted(
            rows, key=lambda r: (r["video_id"], r["frame_index"], r["track_id"], r["action_id"])
        ):
            writer.writerow([
                row["video_id"], f"{row['timestamp_s']:.3f}",
                f"{row['x1']:.6f}", f"{row['y1']:.6f}",
                f"{row['x2']:.6f}", f"{row['y2']:.6f}",
                row["action_id"], row["track_id"],
            ])

    actions_path = staging / ACTIONS_CSV
    with actions_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(ACTIONS_COLUMNS)
        for action in sorted(actions, key=lambda a: action_of_key[str(a["key"])]):
            writer.writerow([
                action_of_key[str(action["key"])], action["key"], action["label"],
                action.get("scope", ""), int(bool(action.get("isBuiltin"))),
                action.get("id", ""), action.get("description", ""),
            ])
    return [EVENTS_CSV, ACTIONS_CSV]


def events_metrics(events: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Durée totale et fréquence par minute - les métriques écologiques visées.

    La fréquence est rapportée à la durée **de la vidéo**, pas à la durée
    suivie : c'est la convention des comptages de bouchées, et la couverture du
    suivi est déclarée à part (voir l'export de suivi).
    """
    per_type: Dict[str, Dict[str, Any]] = {}
    per_media_minutes: Dict[str, float] = {}
    for event in events:
        media_id = event["media_id"]
        if media_id not in per_media_minutes and event["fps"]:
            frames = event.get("media_frame_count")
            per_media_minutes[media_id] = (
                float(frames) / float(event["fps"]) / 60.0 if frames else 0.0
            )
        row = per_type.setdefault(event["event_type"], {
            "events": 0, "total_duration_s": 0.0, "media": set(), "tracks": set(),
        })
        row["events"] += 1
        row["total_duration_s"] += float(event["duration_s"] or 0.0)
        row["media"].add(media_id)
        row["tracks"].add(event["track_id"])

    minutes = sum(per_media_minutes.values())
    out: Dict[str, Any] = {}
    for key, row in sorted(per_type.items()):
        out[key] = {
            "events": row["events"],
            "total_duration_s": round(row["total_duration_s"], 3),
            "mean_duration_s": round(row["total_duration_s"] / row["events"], 3),
            "media": len(row["media"]),
            "tracks": len(row["tracks"]),
            "events_per_minute": round(row["events"] / minutes, 4) if minutes else None,
        }
    return {
        "by_type": out,
        "video_minutes": round(minutes, 3) if minutes else None,
        "note": (
            "events_per_minute rapporté à la durée des vidéos concernées ; "
            "None quand la durée n'est pas connue (fps ou frame_count absent)"
        ),
    }


def write_events_jsonl(
    staging: Path,
    *,
    events: Sequence[Dict[str, Any]],
    instance_of_track: Dict[str, int],
) -> str:
    """Écrit `events.jsonl` - un intervalle par ligne, tel qu'il est en base.

    **Tous** les événements y figurent, chacun portant sa `source` et
    `in_ava_dataset` : c'est la vue de revue, elle doit montrer aussi ce que le
    dataset d'apprentissage écarte, sans quoi personne ne saurait qu'il y a
    quelque chose à relire.
    """
    path = Path(staging) / EVENTS_JSONL
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for event in sorted(
            events, key=lambda e: (e["media_id"], e["frame_start"], e["event_id"])
        ):
            handle.write(json.dumps({
                "event_id": event["event_id"],
                "event_type": event["event_type"],
                "track_id": event["track_id"],
                "instance_id": instance_of_track.get(event["track_id"]),
                "external_track_id": event["external_track_id"],
                "media_id": event["media_id"],
                "media_name": event["media_name"],
                "session_id": event["session_id"],
                "session_name": event["session_name"],
                "frame_start": event["frame_start"],
                "frame_end": event["frame_end"],
                "frame_ref_source": event["frame_ref_source"],
                "frame_index_convention": "absolute",
                "duration_frames": event["duration_frames"],
                "duration_s": event["duration_s"],
                "fps": event["fps"],
                "taxon": event["taxon"],
                "taxon_rank": event["taxon_rank"],
                "taxon_node_id": event["taxon_node_id"],
                "source": event["source"],
                "author": event["author"],
                "confidence": event["confidence"],
                "created_at": event["created_at"],
                "in_ava_dataset": event["source"] == SOURCE_MANUAL,
            }, ensure_ascii=False) + "\n")
    return EVENTS_JSONL
