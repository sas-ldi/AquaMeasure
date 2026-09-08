"""Tables de session pour Excel/R : une ligne par observation, action ou position.

La base reste la source. Aucune vidéo n'est nécessaire, aucune position n'est
interpolée et une action n'est pas répétée pour chaque mesure du même poisson.
"""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from sqlalchemy import func, select

from . import frame_ref as fref
from .identification import is_authoritative_identification, resolve_track_taxa
from .models import (
    EventType, FrameAbundance, MediaAsset, SpatialAnnotation,
    SpatialBehaviorFlag, TemporalEvent, Track, TrackSample,
)
from .session_stats import _bbox_csv_fields, _resolve_hierarchy, _taxon_fields
from .sessions import frame_offset_for_media


CONTEXT = "session_id session_name site session_date operator pair_number camera_role media_id video_name".split()
TAXON = "taxon_node_id family genus species common_name identification_status identification_authoritative".split()
FRAME = "frame_index frame_ref frame_index_source frame_ref_source frame_status time_s".split()
POSITION = "position_x_mm position_y_mm position_z_mm".split()
BBOX = "bbox_x1 bbox_y1 bbox_x2 bbox_y2 geometry_space cx cy".split()
OBSERVATION_FIELDS = CONTEXT + ["annotation_id", "fish_key"] + FRAME + TAXON + [
    "measurement_mm", "has_measurement", *POSITION, *BBOX, "geometry_json",
    "track_id", "external_track_id", "has_tracking", "track_link_status",
    "track_start_frame", "track_end_frame", "track_sample_count",
    "has_observation_actions", "has_track_actions", "behaviors",
    "max_n_media", "max_n_session", "confidence", "source", "author",
    "is_provisional", "family_is_na", "genus_is_na", "species_is_na",
    "model_id", "model_sha256", "model_conf_threshold", "reviewed_by",
    "reviewed_at", "created_at", "updated_at",
]
TRACK_FIELDS = CONTEXT + ["track_id", "external_track_id", "fish_key"] + TAXON + [
    "proposed_taxon_node_id", "has_tracking", "first_frame", "last_frame",
    "start_time_s", "end_time_s", "sample_count", "linked_observation_count",
    "source", "created_at",
]
SAMPLE_FIELDS = CONTEXT + ["sample_id", "track_id", "external_track_id", "fish_key"] + FRAME + [
    "bbox_x1", "bbox_y1", "bbox_x2", "bbox_y2", "geometry_space", "bbox_json",
    "cx_normalized", "cy_normalized", *POSITION, "match_score", "match_method",
    "origin", "edited_by",
]
EVENT_FIELDS = CONTEXT + [
    "event_id", "source_table", "annotation_id", "track_id", "external_track_id", "fish_key",
    "event_type", "event_type_id", "label", "scope", "catalog_scope", "symbol", "color", "type_active",
    "frame_start", "frame_end", "frame_start_source", "frame_end_source", "frame_ref",
    "frame_ref_source", "frame_status", "start_time_s", "end_time_s", "duration_frames", "duration_s",
    *TAXON, "source", "author", "confidence", "metadata_json", "created_at",
]
COUNT_FIELDS = CONTEXT + ["count_id"] + FRAME + [
    "ai_count", "manual_count", "count_used", "count_source", "validated", "max_n_media", "updated_at",
]
VIDEO_FIELDS = CONTEXT + [
    "video_path", "video_available", "sha256", "media_type", "fps", "frame_count",
    "duration_s", "width_px", "height_px", "captured_at", "frame_offset",
    "calibration_id", "calibration_profile", "calibration_sha256", "max_n_media",
    "validated_frame_count", "notes",
]
SUMMARY_FIELDS = CONTEXT[:5] + [
    "status", "notes", "pair_count", "media_count", "observation_count", "measurement_count",
    "track_count", "track_with_samples_count", "event_count", "counted_frame_count",
    "validated_frame_count", "max_n_session", "created_at", "updated_at",
]

TABLES = {
    "session.csv": (OBSERVATION_FIELDS, "Une observation enregistrée, avec sa mesure éventuelle"),
    "pistes.csv": (TRACK_FIELDS, "Une piste, même sans mesure ou sans position enregistrée"),
    "positions_pistes.csv": (SAMPLE_FIELDS, "Une position enregistrée d'une piste à une frame"),
    "evenements.csv": (EVENT_FIELDS, "Une action ponctuelle ou une période, écrite une seule fois"),
    "comptages.csv": (COUNT_FIELDS, "Un comptage sur une image, validé ou non"),
    "videos.csv": (VIDEO_FIELDS, "Une vidéo attachée à la session"),
    "resume_session.csv": (SUMMARY_FIELDS, "La session et ses effectifs enregistrés"),
}

# Définitions partagées entre tables ; les unités restent hors des cellules.
DESCRIPTIONS = {
    "session_id": "Identifiant stable de la session", "session_name": "Nom de la session",
    "site": "Site de la session", "session_date": "Date de la session", "operator": "Opérateur de la session",
    "pair_number": "Numéro de prise stéréo, à partir de 1", "camera_role": "Caméra gauche ou droite",
    "media_id": "Identifiant stable du média", "video_name": "Nom du fichier vidéo",
    "annotation_id": "Identifiant unique de l'observation, clé de session.csv",
    "fish_key": "track:UUID si une piste est liée, sinon observation:UUID ; identité locale, sans rapprochement entre pistes",
    "frame_index": "Index absolu dans la vidéo, à partir de zéro ; vide si conversion impossible",
    "frame_ref": "Référentiel exporté, absolute si la conversion est connue",
    "frame_ref_source": "Référentiel enregistré en base avant conversion",
    "frame_index_source": "Index enregistré en base avant conversion",
    "frame_status": "ok, offset_unknown, outside_video ou invalid_interval ; la donnée source reste conservée",
    "time_s": "Temps de la frame absolue dans la vidéo ; vide si FPS ou index inconnu",
    "taxon_node_id": "Identifiant du taxon retenu", "family": "Famille", "genus": "Genre",
    "species": "Espèce", "common_name": "Nom commun",
    "identification_status": "Statut enregistré de l'identification",
    "identification_authoritative": "1 si une identification humaine est retenue, 0 sinon",
    "measurement_mm": "Longueur enregistrée dans la fiche ; vide si non mesurée",
    "has_measurement": "1 si une longueur est enregistrée, 0 sinon",
    "geometry_space": "Espace des coordonnées du cadre, brut ou rectifié",
    "geometry_json": "Géométrie originale complète de l'observation",
    "track_id": "Identifiant stable de la piste, clé de pistes.csv",
    "external_track_id": "Numéro visible de piste, pas unique entre vidéos",
    "has_tracking": "1 si la piste contient des positions, 0 sinon",
    "track_link_status": "none, tracked, linked_without_samples ou missing_track",
    "track_start_frame": "Première frame absolue déclarée de la piste",
    "track_end_frame": "Dernière frame absolue déclarée de la piste",
    "track_sample_count": "Nombre de positions enregistrées de la piste, répété pour ses observations",
    "has_observation_actions": "1 si cette observation porte des actions ponctuelles",
    "has_track_actions": "1 si la piste liée porte des événements",
    "behaviors": "Libellés des actions de la fiche ; détail exploitable dans evenements.csv",
    "max_n_media": "Maximum des comptages VALIDÉS de cette vidéo ; vide sans validation ; ne pas additionner",
    "max_n_session": "Maximum des comptages VALIDÉS de la session ; vide sans validation ; ne pas additionner",
    "confidence": "Confiance enregistrée", "source": "Origine enregistrée, manual, model, heuristic, etc.",
    "author": "Auteur de l'annotation ou de l'action", "is_provisional": "Statut provisoire enregistré",
    "family_is_na": "Décision explicite NA pour la famille ; vide si non renseignée",
    "genus_is_na": "Décision explicite NA pour le genre ; vide si non renseignée",
    "species_is_na": "Décision explicite NA pour l'espèce ; vide si non renseignée",
    "model_id": "Identifiant du modèle à l'origine de la proposition",
    "model_sha256": "Empreinte des poids du modèle", "model_conf_threshold": "Seuil de confiance du modèle à l'écriture",
    "reviewed_by": "Auteur de la révision humaine", "reviewed_at": "Date de révision",
    "created_at": "Date de création", "updated_at": "Date de dernière mise à jour",
    "proposed_taxon_node_id": "Taxon stocké sur la piste, y compris une proposition non validée",
    "first_frame": "Première frame absolue déclarée de la piste", "last_frame": "Dernière frame absolue déclarée de la piste",
    "start_time_s": "Temps du début dans la vidéo source", "end_time_s": "Temps de la fin dans la vidéo source",
    "sample_count": "Nombre de positions enregistrées", "linked_observation_count": "Nombre de fiches liées à cette piste",
    "sample_id": "Identifiant unique de la position", "bbox_json": "Cadre original de la position",
    "cx_normalized": "Centre horizontal enregistré, fraction de la largeur", "cy_normalized": "Centre vertical enregistré, fraction de la hauteur",
    "match_score": "Score de correspondance stéréo", "match_method": "Méthode de correspondance stéréo",
    "origin": "Origine de la position : auto, keyframe ou interpolated", "edited_by": "Auteur de la correction de position",
    "event_id": "Identifiant unique de l'action, conservé pour les événements sur piste",
    "source_table": "temporal_events pour une piste, spatial_behavior_flags pour une fiche",
    "event_type": "Clé stable du type d'action", "event_type_id": "Identifiant du type d'action",
    "label": "Nom du type, y compris personnalisé", "scope": "instant pour un point, interval pour une durée",
    "catalog_scope": "Portée enregistrée dans le catalogue, conservée même pour un ancien libellé de fiche",
    "symbol": "Symbole du marqueur", "color": "Couleur du marqueur", "type_active": "Type encore actif ou conservé dans l'historique",
    "frame_start": "Frame absolue de début, borne incluse", "frame_end": "Frame absolue de fin, borne incluse",
    "frame_start_source": "Frame de début enregistrée avant conversion", "frame_end_source": "Frame de fin enregistrée avant conversion",
    "duration_frames": "Nombre de frames incluses ; un point vaut 1, sans interpréter une durée comportementale",
    "duration_s": "Durée inclusive d'un intervalle ; vide pour un point ou si FPS inconnu",
    "metadata_json": "Métadonnées originales de l'action",
    "count_id": "Identifiant unique du comptage", "ai_count": "Comptage proposé par l'IA",
    "manual_count": "Correction de l'opérateur ; vide en son absence, zéro conservé",
    "count_used": "Correction manuelle si présente, sinon proposition IA", "count_source": "manual ou ai",
    "validated": "1 si le comptage est validé et contribue au MaxN, 0 sinon",
    "video_path": "Chemin enregistré vers la vidéo", "video_available": "Fichier source actuellement accessible",
    "sha256": "Empreinte du média", "media_type": "Type de média", "fps": "Fréquence d'image",
    "frame_count": "Nombre total de frames", "width_px": "Largeur de la vidéo", "height_px": "Hauteur de la vidéo",
    "captured_at": "Date de capture enregistrée", "frame_offset": "Décalage de timeline figé sur la prise",
    "calibration_id": "Identifiant de calibration", "calibration_profile": "Nom du profil de calibration",
    "calibration_sha256": "Empreinte de la calibration", "notes": "Notes enregistrées", "status": "État de la session",
    "pair_count": "Nombre de prises", "media_count": "Nombre de médias",
    "observation_count": "Nombre de fiches, pas un nombre garanti d'animaux distincts",
    "measurement_count": "Nombre de fiches avec une mesure", "track_count": "Nombre de pistes",
    "track_with_samples_count": "Nombre de pistes contenant des positions", "event_count": "Nombre d'actions enregistrées, toutes sources",
    "counted_frame_count": "Nombre d'images avec un comptage", "validated_frame_count": "Nombre d'images dont le comptage est validé",
}
for _axis in ("x", "y", "z"):
    DESCRIPTIONS[f"position_{_axis}_mm"] = f"Position stéréo {_axis.upper()} enregistrée ; vide si absente"
for _name in ("bbox_x1", "bbox_y1", "bbox_x2", "bbox_y2", "cx", "cy"):
    DESCRIPTIONS[_name] = "Coordonnée du cadre ou de son centre, dans geometry_space"


def _json_object(text):
    try:
        obj = json.loads(text or "{}")
        return obj if isinstance(obj, dict) else {}
    except (ValueError, TypeError):
        return {}


def _csv_value(value):
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, datetime):
        return value.isoformat(timespec="microseconds")
    # Excel ne doit pas interpréter un nom ou une note comme une formule.
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def _write_csv(path, fields, rows):
    count = 0
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: _csv_value(value) for key, value in row.items()})
            count += 1
    return count


def export_session_tables(db, capture, preview, media_rows, output_dir):
    """Exporte toutes les lignes de la session, même sans vidéo disponible."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    selection = {str(row["media_id"]): row for row in media_rows}
    media_ids = list(selection)
    media_by_id = {row.id: row for row in db.scalars(select(MediaAsset).where(MediaAsset.id.in_(media_ids)))}
    pair_by_media = {
        mid: pair for pair in preview.get("pairs", [])
        for mid in (pair.get("left_media_id"), pair.get("right_media_id")) if mid
    }
    offsets = {mid: frame_offset_for_media(db, mid) for mid in media_ids}
    base = dict(zip(CONTEXT[:5], (capture.id, capture.name, capture.site, preview.get("session_date"), capture.operator)))

    def context(mid):
        source, media = selection[mid], media_by_id[mid]
        return {**base, "pair_number": source.get("pair_number"), "camera_role": source.get("role"),
                "media_id": mid, "video_name": Path(media.rel_path).name}

    def seconds(mid, frame):
        fps = media_by_id[mid].fps
        return frame / fps if frame is not None and fps and fps > 0 else None

    def frame_values(mid, frame, ref):
        original_ref = fref.normalize(ref)
        absolute = fref.to_absolute(frame, original_ref, offsets[mid])
        media = media_by_id[mid]
        status = "ok" if absolute is not None else "offset_unknown"
        if absolute is not None and (absolute < 0 or (media.frame_count and absolute >= media.frame_count)):
            status = "outside_video"
        return {"frame_index": absolute, "frame_ref": "absolute" if absolute is not None else original_ref,
                "frame_index_source": frame, "frame_ref_source": original_ref, "frame_status": status,
                "time_s": seconds(mid, absolute)}

    hierarchy_cache = {}

    def taxonomy(node_id, status, authoritative):
        if node_id not in hierarchy_cache:
            hierarchy_cache[node_id] = _taxon_fields(_resolve_hierarchy(db, node_id))
        return {"taxon_node_id": node_id, **hierarchy_cache[node_id], "identification_status": status,
                "identification_authoritative": bool(authoritative)}

    annotations = list(db.scalars(select(SpatialAnnotation).where(
        SpatialAnnotation.media_id.in_(media_ids)).order_by(SpatialAnnotation.media_id, SpatialAnnotation.frame_index, SpatialAnnotation.id)))
    annotations_by_id = {row.id: row for row in annotations}
    tracks = list(db.scalars(select(Track).where(Track.media_id.in_(media_ids)).order_by(Track.media_id, Track.external_track_id, Track.id)))
    track_by_id = {row.id: row for row in tracks}
    track_taxa = resolve_track_taxa(db, tracks)
    sample_counts = dict(db.execute(select(TrackSample.track_id, func.count(TrackSample.id))
        .join(Track, TrackSample.track_id == Track.id).where(Track.media_id.in_(media_ids)).group_by(TrackSample.track_id)).all())
    linked = defaultdict(list)
    for annotation in annotations:
        if annotation.track_id:
            linked[annotation.track_id].append(annotation.id)

    counts = list(db.scalars(select(FrameAbundance).where(FrameAbundance.media_id.in_(media_ids))
                           .order_by(FrameAbundance.media_id, FrameAbundance.frame_index, FrameAbundance.id)))
    validated = defaultdict(list)
    for count in counts:
        if count.validated:
            value = count.manual_count if count.manual_count is not None else count.ai_count
            if value is not None:
                validated[count.media_id].append(value)
    max_by_media = {mid: max(validated[mid], default=None) for mid in media_ids}
    max_session = max((value for value in max_by_media.values() if value is not None), default=None)
    types = {row.key: row for row in db.scalars(select(EventType))}
    event_rows = []
    flags_by_annotation = defaultdict(list)
    events_by_track = defaultdict(list)

    def event_row(mid, event_id, key, start, end, ref, **data):
        kind = types.get(key)
        scope = data.pop("scope", None) or (kind.scope if kind else ("instant" if start == end else "interval"))
        if scope in ("point", "ponctuel"):
            scope = "instant"
        first, last = frame_values(mid, start, ref), frame_values(mid, end, ref)
        status = first["frame_status"] if first["frame_status"] != "ok" else last["frame_status"]
        if end < start:
            status = "invalid_interval"
        frames = end - start + 1 if end >= start else None
        return {**context(mid), "event_id": event_id, "event_type": key,
                "event_type_id": kind.id if kind else None, "label": kind.label if kind else key,
                "scope": scope, "catalog_scope": kind.scope if kind else None,
                "symbol": kind.symbol if kind else None, "color": kind.color if kind else None,
                "type_active": kind.is_active if kind else None,
                "frame_start": first["frame_index"], "frame_end": last["frame_index"],
                "frame_start_source": start, "frame_end_source": end,
                "frame_ref": first["frame_ref"], "frame_ref_source": first["frame_ref_source"],
                "frame_status": status, "start_time_s": first["time_s"], "end_time_s": last["time_s"],
                "duration_frames": frames, "duration_s": seconds(mid, frames) if scope == "interval" else None, **data}

    for event in db.scalars(select(TemporalEvent).join(Track, TemporalEvent.track_id == Track.id)
                           .where(Track.media_id.in_(media_ids)).order_by(Track.media_id, TemporalEvent.frame_start, TemporalEvent.id)):
        track = track_by_id[event.track_id]
        row = event_row(track.media_id, event.id, event.event_type, event.frame_start, event.frame_end, event.frame_ref,
            source_table="temporal_events", annotation_id=None, track_id=track.id,
            external_track_id=track.external_track_id, fish_key="track:" + track.id,
            **taxonomy(track_taxa[track.id], track.identification_status, track_taxa[track.id] is not None),
            source=event.source, author=event.author, confidence=event.confidence,
            metadata_json=event.metadata_json, created_at=event.created_at)
        event_rows.append(row)
        events_by_track[track.id].append(row)
    for flag in db.scalars(select(SpatialBehaviorFlag).join(SpatialAnnotation,
            SpatialBehaviorFlag.spatial_annotation_id == SpatialAnnotation.id)
            .where(SpatialAnnotation.media_id.in_(media_ids))
            .order_by(SpatialBehaviorFlag.spatial_annotation_id, SpatialBehaviorFlag.event_type)):
        annotation = annotations_by_id[flag.spatial_annotation_id]
        track = track_by_id.get(annotation.track_id)
        row = event_row(annotation.media_id, f"observation:{annotation.id}:{flag.event_type}", flag.event_type,
            annotation.frame_index, annotation.frame_index, annotation.frame_ref, scope="instant",
            source_table="spatial_behavior_flags", annotation_id=annotation.id, track_id=annotation.track_id,
            external_track_id=track.external_track_id if track else None,
            fish_key="track:" + track.id if track else "observation:" + annotation.id,
            **taxonomy(annotation.taxon_node_id, annotation.identification_status, is_authoritative_identification(annotation)),
            source="manual", author=flag.author, confidence=None, metadata_json=None, created_at=flag.created_at)
        event_rows.append(row)
        flags_by_annotation[annotation.id].append(row)

    def observation_rows():
        for ann in annotations:
            track = track_by_id.get(ann.track_id)
            samples = sample_counts.get(ann.track_id, 0)
            geom = _json_object(ann.geometry_json)
            yield {**context(ann.media_id), "annotation_id": ann.id,
                "fish_key": "track:" + track.id if track else "observation:" + ann.id,
                **frame_values(ann.media_id, ann.frame_index, ann.frame_ref),
                **taxonomy(ann.taxon_node_id, ann.identification_status, is_authoritative_identification(ann)),
                "measurement_mm": ann.measurement_mm, "has_measurement": ann.measurement_mm is not None,
                **{key: getattr(ann, key) for key in POSITION}, **_bbox_csv_fields(geom, media_by_id[ann.media_id]),
                "geometry_space": geom.get("space"), "geometry_json": ann.geometry_json,
                "track_id": ann.track_id, "external_track_id": track.external_track_id if track else None,
                "has_tracking": samples > 0,
                "track_link_status": ("tracked" if samples else "linked_without_samples") if track else ("missing_track" if ann.track_id else "none"),
                "track_start_frame": track.first_frame if track else None, "track_end_frame": track.last_frame if track else None,
                "track_sample_count": samples, "has_observation_actions": bool(flags_by_annotation[ann.id]),
                "has_track_actions": bool(events_by_track[ann.track_id]),
                "behaviors": " | ".join(row["label"] for row in flags_by_annotation[ann.id]),
                "max_n_media": max_by_media[ann.media_id], "max_n_session": max_session,
                **{key: getattr(ann, key) for key in (
                    "confidence", "source", "author", "is_provisional", "family_is_na", "genus_is_na", "species_is_na",
                    "model_id", "model_sha256", "model_conf_threshold", "reviewed_by", "reviewed_at", "created_at", "updated_at")}}

    def track_rows():
        for track in tracks:
            yield {**context(track.media_id), "track_id": track.id, "external_track_id": track.external_track_id,
                "fish_key": "track:" + track.id, **taxonomy(track_taxa[track.id], track.identification_status, track_taxa[track.id] is not None),
                "proposed_taxon_node_id": track.taxon_node_id, "has_tracking": sample_counts.get(track.id, 0) > 0,
                "first_frame": track.first_frame, "last_frame": track.last_frame,
                "start_time_s": seconds(track.media_id, track.first_frame), "end_time_s": seconds(track.media_id, track.last_frame),
                "sample_count": sample_counts.get(track.id, 0), "linked_observation_count": len(linked[track.id]),
                "source": track.source, "created_at": track.created_at}

    def position_rows():
        query = select(TrackSample).join(Track, TrackSample.track_id == Track.id).where(Track.media_id.in_(media_ids))
        query = query.order_by(TrackSample.track_id, TrackSample.frame_index, TrackSample.id).execution_options(yield_per=1000)
        for sample in db.scalars(query):
            track = track_by_id[sample.track_id]
            bbox = _json_object(sample.bbox_json)
            yield {**context(track.media_id), "sample_id": sample.id, "track_id": track.id,
                "external_track_id": track.external_track_id, "fish_key": "track:" + track.id,
                **frame_values(track.media_id, sample.frame_index, "absolute"),
                **dict(zip(("bbox_x1", "bbox_y1", "bbox_x2", "bbox_y2"), (bbox.get(key) for key in ("x_min", "y_min", "x_max", "y_max")))),
                "geometry_space": "stereo_rectified_left", "bbox_json": sample.bbox_json,
                "cx_normalized": sample.cx, "cy_normalized": sample.cy,
                **{key: getattr(sample, key) for key in POSITION + ["match_score", "match_method", "origin", "edited_by"]}}

    def count_rows():
        for count in counts:
            yield {**context(count.media_id), "count_id": count.id,
                **frame_values(count.media_id, count.frame_index, count.frame_ref),
                "ai_count": count.ai_count, "manual_count": count.manual_count,
                "count_used": count.manual_count if count.manual_count is not None else count.ai_count,
                "count_source": "manual" if count.manual_count is not None else "ai", "validated": count.validated,
                "max_n_media": max_by_media[count.media_id], "updated_at": count.updated_at}

    def video_rows():
        for mid, media in media_by_id.items():
            pair = pair_by_media.get(mid, {})
            yield {**context(mid), "video_path": media.rel_path, "video_available": selection[mid].get("available"),
                "sha256": media.sha256, "media_type": media.media_type, "fps": media.fps, "frame_count": media.frame_count,
                "duration_s": seconds(mid, media.frame_count), "width_px": media.width, "height_px": media.height,
                "captured_at": media.captured_at, "frame_offset": offsets[mid],
                "calibration_id": media.calibration_id or pair.get("calibration_id"),
                "calibration_profile": pair.get("calibration_profile"), "calibration_sha256": pair.get("calibration_sha256"),
                "max_n_media": max_by_media[mid], "validated_frame_count": len(validated[mid]), "notes": media.notes}

    summary = {**base, "status": capture.status, "notes": capture.notes,
        "pair_count": preview.get("pair_count"), "media_count": len(media_by_id), "observation_count": len(annotations),
        "measurement_count": sum(ann.measurement_mm is not None for ann in annotations),
        "track_count": len(tracks), "track_with_samples_count": len(sample_counts), "event_count": len(event_rows),
        "counted_frame_count": len(counts), "validated_frame_count": sum(count.validated for count in counts),
        "max_n_session": max_session, "created_at": capture.created_at, "updated_at": capture.updated_at}
    row_sources = [observation_rows(), track_rows(), position_rows(), event_rows, count_rows(), video_rows(), [summary]]
    exported = {}
    dictionary = []
    for (name, (fields, grain)), rows in zip(TABLES.items(), row_sources):
        exported[name] = _write_csv(output_dir / name, fields, rows)
        for key in fields:
            unit = "mm" if key.endswith("_mm") else ("s" if key.endswith("_s") else ("pixel" if key in BBOX[:4] + ["cx", "cy", "width_px", "height_px"] else ""))
            description = DESCRIPTIONS[key]
            if name == "videos.csv" and key == "duration_s":
                description = "Durée du fichier : nombre de frames / FPS"
            dictionary.append({"file": name, "row_means": grain, "column": key, "unit": unit, "description": description})
    _write_csv(output_dir / "dictionnaire.csv", ["file", "row_means", "column", "unit", "description"], dictionary)
    return {"schema": "aquameasure.session_csv.v2", "files": exported,
            "delimiter": ",", "encoding": "utf-8-sig", "dictionary": "dictionnaire.csv",
            "max_n_session": max_session, "event_count": len(event_rows),
            "unresolved_event_frames": sum(row["frame_status"] != "ok" for row in event_rows)}


CSV_README = (
    "session.csv : une ligne par observation avec mesure, identification et lien de piste.\n"
    "pistes.csv et positions_pistes.csv : pistes et toutes leurs positions enregistrées.\n"
    "evenements.csv : chaque action une seule fois, sur piste ou sur fiche, avec type et frames.\n"
    "comptages.csv : comptages IA, corrections manuelles, validation et MaxN par vidéo.\n"
    "videos.csv et resume_session.csv : prises, calibration et métadonnées de session.\n"
    "dictionnaire.csv : définition et unité de chaque colonne du paquet CSV.\n\n"
    "Excel : Données > À partir d'un fichier texte/CSV, encodage UTF-8, séparateur virgule. "
    "Les décimales utilisent un point ; choisir une locale anglaise pour les colonnes numériques "
    "si Excel français ne les reconnaît pas. Les identifiants sont du texte.\n"
    "Une cellule vide est une donnée absente, zéro reste une valeur. "
    "Un texte commençant par =, +, - ou @ est préfixé d'une apostrophe pour empêcher une formule Excel.\n"
    "Relier les fichiers par annotation_id, track_id et media_id. Compter les événements dans evenements.csv, "
    "pas dans les positions de piste. fish_key regroupe les fiches d'une même piste ; "
    "il ne reconnaît pas un animal entre deux pistes ou deux vidéos.\n"
    "MaxN = maximum d'un comptage validé, correction manuelle prioritaire sur l'IA. "
    "Il reste vide sans validation et ne s'additionne pas entre les lignes. "
    "frame_index est absolu à partir de zéro ; frame_index_source et frame_ref_source gardent l'original. "
    "Si l'offset historique manque, la frame absolue reste vide et frame_status l'indique.\n"
)
