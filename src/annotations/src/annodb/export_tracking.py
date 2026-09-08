"""Suivi : COCO-VID (pivot) et son dérivé MOTChallenge.

Ce module ne fait qu'**écrire** : la collecte, le découpage par groupe, la
matérialisation des images et le manifeste restent l'affaire de
`export_core.py`. Le rapport entre les deux formats est exactement celui de
COCO et YOLO en phase 2 : **un seul chemin de vérité**. Les boîtes MOT sont
recopiées de la liste COCO-VID déjà bornée, jamais relues depuis la base - deux
lectures divergeraient tôt ou tard.

Ce que la donnée de suivi est réellement
---------------------------------------
Les trajectoires vivent dans `track_samples`, pas dans `spatial_annotations` :
une ligne par piste et par frame, `bbox_json` en pixels de l'image **rectifiée**
que le tracker a vue, `origin` disant qui l'a produite (`auto` = ByteTrack,
`interpolated` = calculé entre deux keyframes, `keyframe` = posé à la main).
C'est donc `track_samples` que ces exports décrivent.

Trous et couverture
-------------------
Le suivi n'a été lancé que sur des portions de vidéo, souvent une frame sur
deux, et 61 % des pistes ont des trous. Un export de suivi doit dire la vérité
là-dessus : on n'invente aucune position, et chaque séquence déclare sa
**couverture** (frames exportées / étendue couverte). Les numéros de frame MOT
étant contigus par construction (base 1, sans trou), `frames_map.csv` donne le
retour vers l'index absolu du fichier vidéo - sans lui, un `gt.txt` à trous est
un mensonge sur le temps.
"""

from __future__ import annotations

import json
import logging
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import frame_ref as fref
from .export_media import (
    REASON_MEDIA_INTROUVABLE,
    REASON_MEDIA_TYPE,
    ExportExclusions,
    ExportFrame,
)
from .models import MediaAsset, Track, TrackSample

log = logging.getLogger(__name__)

# Visibilité MOT : ce n'est pas une occultation mesurée, c'est la **confiance
# dans la position**, seule information dont on dispose. L'échelle est déclarée
# dans le manifeste pour que personne ne l'interprète comme un taux
# d'occultation.
VISIBILITY_BY_ORIGIN: Dict[str, float] = {
    "keyframe": 1.0,      # position posée ou corrigée par un humain
    "interpolated": 0.9,  # calculée entre deux keyframes humaines
    "auto": 0.7,          # sortie brute du tracker, jamais relue
}
DEFAULT_VISIBILITY = 0.7

VISIBILITY_RULE = (
    "visibility = confiance dans la position, pas taux d'occultation : "
    "keyframe (humain) 1.0, interpolated 0.9, auto (tracker) 0.7"
)

# Raison d'exclusion propre au suivi : MOT identifie chaque boîte par une piste.
REASON_SANS_PISTE = "echantillon_sans_piste_connue"

MOT_GT_COLUMNS = ("frame", "id", "x", "y", "w", "h", "conf", "class", "visibility")

COCO_VID_NAME = "coco_vid.json"
TRACK_EVENTS_NAME = "track_events.jsonl"
EVENT_SCHEMA = "aquameasure.track_events.v1"
MOT_ROOT = "mot"
MOT_IMAGE_DIR = "img1"
FRAMES_MAP_NAME = "frames_map.csv"

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def sequence_name(media: Optional[MediaAsset], media_id: str) -> str:
    """Nom de séquence MOT : lisible, sûr sur disque, et unique.

    Le nom du fichier seul ne suffit pas - deux sessions peuvent avoir toutes
    deux une `droite.MP4`. Les 8 premiers caractères du `media_id` lèvent
    l'ambiguïté sans rendre le dossier illisible.
    """
    stem = Path(media.rel_path).stem if media is not None and media.rel_path else media_id
    safe = _UNSAFE.sub("_", str(stem)).strip("_") or "sequence"
    return f"{safe}_{str(media_id)[:8]}"


def collect_track_frames(
    session: Session,
    project_ids: Optional[Sequence[str]] = None,
    *,
    media_ids: Optional[Sequence[str]] = None,
    exclusions: Optional[ExportExclusions] = None,
) -> Tuple[List[ExportFrame], Dict[str, Track], Dict[str, MediaAsset]]:
    """Regroupe les `track_samples` en frames exportables.

    Renvoie `(frames, pistes par id, médias par id)`. Chaque `ExportFrame` porte
    ses **échantillons** dans `annotations` : le champ est typé
    `SpatialAnnotation` mais seul `.id` en est lu (par `ExportExclusions`), et
    réutiliser `ExportFrame` fait profiter le suivi de toute la mécanique déjà
    éprouvée - décimation, matérialisation rectifiée, exclusions motivées.

    `track_samples.frame_index` est **absolu** par construction
    (`tracking_worker` lit le fichier directement) : aucune conversion
    `timeline_legacy` ici, contrairement aux annotations et aux événements.
    """
    media_by_id: Dict[str, MediaAsset] = {
        m.id: m for m in session.scalars(select(MediaAsset))
    }
    tracks_by_id: Dict[str, Track] = {}
    skipped_tracks: Dict[str, str] = {}
    wanted_media = set(media_ids or [])

    for track in session.scalars(select(Track)):
        media = media_by_id.get(track.media_id)
        if media is None:
            skipped_tracks[track.id] = REASON_MEDIA_INTROUVABLE
            continue
        if project_ids and media.project_id not in project_ids:
            # Filtre demandé par l'appelant : ce n'est pas une perte.
            skipped_tracks[track.id] = ""
            continue
        if wanted_media and media.id not in wanted_media:
            skipped_tracks[track.id] = ""
            continue
        if media.media_type != "video":
            # Une piste sur une photo n'a pas de sens : rien à suivre.
            skipped_tracks[track.id] = REASON_MEDIA_TYPE
            continue
        tracks_by_id[track.id] = track

    buckets: Dict[Tuple[str, int], ExportFrame] = {}
    for sample in session.scalars(select(TrackSample).order_by(TrackSample.frame_index)):
        track = tracks_by_id.get(sample.track_id)
        if track is None:
            reason = skipped_tracks.get(sample.track_id)
            if reason and exclusions is not None:
                exclusions.add(
                    f"track_sample:{sample.id}", reason,
                    frame_index=sample.frame_index,
                    detail=f"piste {sample.track_id} non exportable",
                )
            continue
        key = (track.media_id, int(sample.frame_index))
        frame = buckets.get(key)
        if frame is None:
            frame = ExportFrame(
                media_id=track.media_id,
                frame_index=int(sample.frame_index),
                media_type="video",
                frame_ref=fref.FRAME_REF_ABSOLUTE,
            )
            buckets[key] = frame
        frame.annotations.append(sample)

    frames = sorted(buckets.values(), key=lambda f: (f.media_id, f.frame_index))
    return frames, tracks_by_id, media_by_id


def instance_ids(tracks: Iterable[Track]) -> Dict[str, int]:
    """`track_id` (UUID base) → `instance_id` entier, déterministe.

    COCO-VID, MOT et le CSV AVA désignent tous la même piste par le **même**
    entier : sans cela, recouper trois fichiers du même export demanderait une
    table de correspondance que personne n'écrirait. L'identité durable reste
    l'UUID, repris tel quel dans `coco_vid.json["tracks"]` et dans le JSONL.
    """
    ordered = sorted(
        tracks, key=lambda t: (t.media_id or "", int(t.external_track_id or 0), t.id)
    )
    return {track.id: index + 1 for index, track in enumerate(ordered)}


def clamp_pixel_box(
    x1: float, y1: float, x2: float, y2: float, width: float, height: float,
) -> Tuple[Tuple[float, float, float, float], bool]:
    """Borne une boîte en pixels. Retour : `((x1, y1, x2, y2), bornée)`.

    Même intention que `export_core.clamp_box_to_image`, en coordonnées coin à
    coin : une boîte de suivi qui mord le bord de l'image donnerait des scores
    d'aire fantaisistes à l'évaluation MOT. Le seuil est **sous-pixel** : un
    écart de 10⁻⁷ pixel est du bruit d'arrondi, pas un défaut à signaler.
    """
    cx1 = min(max(x1, 0.0), float(width))
    cy1 = min(max(y1, 0.0), float(height))
    cx2 = min(max(x2, 0.0), float(width))
    cy2 = min(max(y2, 0.0), float(height))
    changed = any(
        abs(before - after) > 1e-3
        for before, after in ((x1, cx1), (y1, cy1), (x2, cx2), (y2, cy2))
    )
    return (cx1, cy1, cx2, cy2), changed


def sample_box_pixels(
    sample: TrackSample, *, ref_width: float, ref_height: float,
    image_width: int, image_height: int,
) -> Optional[Tuple[float, float, float, float]]:
    """Boîte de l'échantillon, ramenée aux dimensions de l'image exportée.

    `bbox_json` est en pixels de l'image que le tracker a vue. On normalise par
    cette taille de référence avant de multiplier par celle de l'image
    réellement écrite : si l'export redimensionnait un jour ses images, les
    boîtes suivraient au lieu de se décaler en silence.
    """
    try:
        box = json.loads(sample.bbox_json)
        x1 = float(box["x_min"])
        y1 = float(box["y_min"])
        x2 = float(box["x_max"])
        y2 = float(box["y_max"])
    except (TypeError, ValueError, KeyError):
        return None
    sx = float(image_width) / float(ref_width or image_width or 1)
    sy = float(image_height) / float(ref_height or image_height or 1)
    if x2 < x1:
        x1, x2 = x2, x1
    if y2 < y1:
        y1, y2 = y2, y1
    return x1 * sx, y1 * sy, x2 * sx, y2 * sy


def visibility_for(origin: Optional[str]) -> float:
    return VISIBILITY_BY_ORIGIN.get(str(origin or ""), DEFAULT_VISIBILITY)


def build_videos(
    images: Sequence[Dict[str, Any]],
    media_by_id: Dict[str, MediaAsset],
    *,
    boxes: Sequence[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """Table `videos` de COCO-VID, une entrée par média réellement exporté.

    La **couverture** y figure dès ici : nombre de frames exportées, étendue
    couverte (dernière − première + 1), et leur rapport. Un consommateur qui lit
    `frames=812` sans savoir qu'elles s'étalent sur 26 000 croirait tenir une
    vidéo continue.
    """
    frames_by_media: Dict[str, List[int]] = defaultdict(list)
    split_of_media: Dict[str, str] = {}
    size_of_media: Dict[str, Tuple[int, int]] = {}
    for image in images:
        media_id = image["media_id"]
        frames_by_media[media_id].append(int(image["frame_index"]))
        split_of_media.setdefault(media_id, image["split"])
        size_of_media.setdefault(media_id, (int(image["width"]), int(image["height"])))

    boxes_by_media: Dict[str, int] = defaultdict(int)
    tracks_by_media: Dict[str, set] = defaultdict(set)
    for box in boxes:
        boxes_by_media[box["media_id"]] += 1
        tracks_by_media[box["media_id"]].add(box["instance_id"])

    videos: List[Dict[str, Any]] = []
    video_id_of_media: Dict[str, int] = {}
    for index, media_id in enumerate(sorted(frames_by_media)):
        media = media_by_id.get(media_id)
        indexes = sorted(frames_by_media[media_id])
        width, height = size_of_media[media_id]
        span = indexes[-1] - indexes[0] + 1 if indexes else 0
        video_id = index + 1
        video_id_of_media[media_id] = video_id
        videos.append({
            "id": video_id,
            "name": sequence_name(media, media_id),
            "media_id": media_id,
            "file_name": Path(media.rel_path).name if media is not None else None,
            "width": width,
            "height": height,
            "fps": float(media.fps) if media is not None and media.fps else None,
            "frame_count": int(media.frame_count) if media is not None and media.frame_count else None,
            "split": split_of_media.get(media_id),
            "frame_index_convention": "absolute",
            "exported_frames": len(indexes),
            "first_frame": indexes[0] if indexes else None,
            "last_frame": indexes[-1] if indexes else None,
            "covered_span": span,
            "coverage_ratio": round(len(indexes) / span, 4) if span else 0.0,
            "annotation_count": boxes_by_media.get(media_id, 0),
            "track_count": len(tracks_by_media.get(media_id, ())),
        })
    return videos, video_id_of_media


def build_track_events(
    events: Sequence[Dict[str, Any]],
    tracks_payload: Sequence[Dict[str, Any]],
    type_rows: Sequence[Dict[str, Any]],
    *,
    resolve_image: Callable[[str, int], Dict[str, Any]],
    exclusions: ExportExclusions,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Relie les actions aux identifiants COCO-VID, sans arrondir leurs frames.

    Le référentiel temporel a déjà été normalisé par collect_events. Une image
    de marqueur peut être distincte des images échantillonnées du tracking.
    """
    tracks = {row["track_db_id"]: row for row in tracks_payload}
    types = {row["key"]: row for row in type_rows}
    payload: List[Dict[str, Any]] = []
    used_types: Dict[str, Dict[str, Any]] = {}
    for event in sorted(events, key=lambda row: (row["media_id"], row["frame_start"], row["event_id"])):
        track = tracks.get(event["track_id"])
        if track is None:
            exclusions.add(event["event_id"], "piste_absente_de_l_export",
                           media_id=event["media_id"], frame_index=event["frame_start"])
            continue
        start, end = int(event["frame_start"]), int(event["frame_end"])
        key = event["event_type"]
        kind = types.get(key, {})
        scope = kind.get("scope") or ("instant" if start == end else "interval")
        if scope in ("point", "ponctuel"):
            scope = "instant"
        used_types[key] = {
            "key": key, "event_type_id": kind.get("id"),
            "label": kind.get("label") or key, "scope": scope,
            "symbol": kind.get("symbol"), "color": kind.get("color"),
            "is_active": kind.get("isActive"),
        }
        start_image = resolve_image(event["media_id"], start)
        end_image = start_image if start == end else resolve_image(event["media_id"], end)
        fps = event.get("fps")
        payload.append({
            "id": event["event_id"], "event_type": key,
            "label": used_types[key]["label"], "scope": scope,
            "video_id": track["video_id"], "media_id": event["media_id"],
            "track_id": track["id"], "track_db_id": event["track_id"],
            "frame_index": start if scope == "instant" else None,
            "frame_start": start, "frame_end": end,
            "frame_index_convention": "absolute_zero_based",
            "frame_ref_source": event["frame_ref_source"],
            "start_time_s": round(start / fps, 6) if fps else None,
            "end_time_s": round(end / fps, 6) if fps else None,
            "duration_frames": event["duration_frames"],
            "start_image_id": start_image.get("image_id"),
            "end_image_id": end_image.get("image_id"),
            "start_image_file_name": start_image.get("file_name"),
            "end_image_file_name": end_image.get("file_name"),
            "source": event["source"], "author": event["author"],
            "confidence": event["confidence"], "created_at": event["created_at"],
        })
    return payload, [used_types[key] for key in sorted(used_types)]


def write_coco_vid(
    staging: Path,
    *,
    videos: List[Dict[str, Any]],
    images: List[Dict[str, Any]],
    boxes: List[Dict[str, Any]],
    tracks_payload: List[Dict[str, Any]],
    categories: List[Dict[str, Any]],
    space_summary: Dict[str, Any],
    exclusions: ExportExclusions,
    dataset_name: str,
    dataset_version: str,
    created_at: str,
    year: int,
    events: Optional[List[Dict[str, Any]]] = None,
    event_types: Optional[List[Dict[str, Any]]] = None,
    excluded_events: Optional[List[Dict[str, Any]]] = None,
) -> str:
    """Écrit `coco_vid.json` - le pivot du suivi (structure TAO/COCO-VID).

    Un seul fichier : `videos`, `images` (portant `video_id` et `frame_id`),
    `tracks` (l'identité des pistes) et `annotations` (portant `instance_id`).
    Les identifiants d'image et d'annotation sont ceux du dérivé MOT, à la
    conversion de numéro de frame près - documentée dans `frames_map.csv`.
    L'extension AquaMeasure `events` décrit les points et durées sur les pistes ;
    `event_ids` les relie aux pistes et aux boîtes des frames concernées.
    """
    events = events or []
    events_by_track: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
    for event in events:
        events_by_track[event["track_id"]].append(event)
    frame_of_image = {image["id"]: image["frame_id"] for image in images}
    payload = {
        "info": {
            "description": f"{dataset_name} - export de suivi annotations (COCO-VID / TAO)",
            "version": dataset_version,
            "date_created": created_at,
            "year": year,
            **space_summary,
            "tracking_source": "track_samples",
            "visibility_rule": VISIBILITY_RULE,
            "frame_id_convention": (
                "frame_id = index absolu dans le fichier vidéo source"
            ),
            "event_extension": {
                "schema": EVENT_SCHEMA,
                "file": TRACK_EVENTS_NAME,
                "frame_index_convention": "absolute_zero_based",
                "interval_bounds": "inclusive",
                "image_file_names_relative_to": "dataset_root",
                "source_rule": "manual = pointage humain ; les autres sources restent explicitement indiquées",
            },
        },
        "licenses": [],
        "categories": categories,
        "videos": videos,
        "images": [
            {k: v for k, v in image.items() if k != "group"} for image in images
        ],
        "tracks": [
            {**track, "event_ids": [event["id"] for event in events_by_track[track["id"]]]}
            for track in tracks_payload
        ],
        "annotations": [
            {
                "id": box["id"],
                "image_id": box["image_id"],
                "video_id": box["video_id"],
                "category_id": box["category_id"],
                "instance_id": box["instance_id"],
                "track_id": box["instance_id"],
                "bbox": box["bbox"],
                "area": box["area"],
                "iscrowd": box["iscrowd"],
                "ignore": box["ignore"],
                "attributes": {
                    **box["attributes"],
                    "event_ids": [
                        event["id"] for event in events_by_track[box["instance_id"]]
                        if event["frame_start"] <= frame_of_image[box["image_id"]] <= event["frame_end"]
                    ],
                },
            }
            for box in boxes
        ],
        # Champ libre : un export ne doit jamais laisser croire qu'il a tout
        # sorti (les lecteurs COCO ignorent les clés inconnues).
        "excluded_annotations": exclusions.rows(),
        "events": events,
        "event_types": event_types or [],
        "excluded_events": excluded_events or [],
    }
    path = Path(staging) / COCO_VID_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8",
    )
    with (Path(staging) / TRACK_EVENTS_NAME).open("w", encoding="utf-8", newline="\n") as handle:
        for event in events:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")
    return COCO_VID_NAME


def write_mot(
    staging: Path,
    *,
    videos: List[Dict[str, Any]],
    images: List[Dict[str, Any]],
    boxes: List[Dict[str, Any]],
    link_image: Optional[Any] = None,
) -> Dict[str, Any]:
    """Dérive l'arborescence MOTChallenge **depuis la sortie COCO-VID**.

    Une séquence par média : `gt/gt.txt` (frame en base 1, relative à la
    séquence), `seqinfo.ini`, `frames_map.csv` et `img1/` (liens matériels vers
    les images déjà écrites, copie à défaut). `seqmaps/<split>.txt` liste les
    séquences de chaque découpage, comme l'attend TrackEval.

    Le numéro de frame MOT est **relatif** : les frames exportées, triées, sont
    numérotées 1..N. Elles ne sont pas contiguës dans la vidéo - d'où
    `frames_map.csv`, sans lequel une durée lue dans `gt.txt` serait fausse.
    """
    root = Path(staging) / MOT_ROOT
    images_by_media: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for image in images:
        images_by_media[image["media_id"]].append(image)
    boxes_by_media: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for box in boxes:
        boxes_by_media[box["media_id"]].append(box)

    sequences: List[Dict[str, Any]] = []
    seqmaps: Dict[str, List[str]] = defaultdict(list)
    total_lines = 0

    for video in videos:
        media_id = video["media_id"]
        seq = video["name"]
        seq_dir = root / seq
        frames = sorted(images_by_media[media_id], key=lambda img: int(img["frame_index"]))
        # Le cœur du format : index absolu → numéro MOT (base 1, contigu).
        mot_of_absolute = {
            int(img["frame_index"]): rank + 1 for rank, img in enumerate(frames)
        }

        (seq_dir / "gt").mkdir(parents=True, exist_ok=True)
        lines: List[str] = []
        for box in sorted(
            boxes_by_media[media_id],
            key=lambda b: (int(b["frame_index"]), int(b["instance_id"])),
        ):
            mot_frame = mot_of_absolute.get(int(box["frame_index"]))
            if mot_frame is None:
                continue
            x, y, w, h = box["bbox"]
            lines.append(
                f"{mot_frame},{box['instance_id']},{x:.2f},{y:.2f},{w:.2f},{h:.2f},"
                f"1,{box['category_id']},{box['visibility']:.2f}"
            )
        (seq_dir / "gt" / "gt.txt").write_text(
            "\n".join(lines) + ("\n" if lines else ""), encoding="utf-8",
        )
        total_lines += len(lines)

        # Retour vers le temps réel : sans lui, « frame 12 » ne veut rien dire.
        map_rows = ["frame_mot,frame_absolue,timestamp_s,image"]
        fps = video.get("fps") or 0.0
        for img in frames:
            absolute = int(img["frame_index"])
            stamp = f"{absolute / fps:.3f}" if fps else ""
            map_rows.append(
                f"{mot_of_absolute[absolute]},{absolute},{stamp},{img['file_name']}"
            )
        (seq_dir / FRAMES_MAP_NAME).write_text(
            "\n".join(map_rows) + "\n", encoding="utf-8",
        )

        if link_image is not None:
            img_dir = seq_dir / MOT_IMAGE_DIR
            img_dir.mkdir(parents=True, exist_ok=True)
            for img in frames:
                link_image(
                    Path(staging) / "images" / img["file_name"],
                    img_dir / f"{mot_of_absolute[int(img['frame_index'])]:06d}.jpg",
                )

        coverage = video.get("coverage_ratio") or 0.0
        seqinfo = "\n".join([
            "[Sequence]",
            f"name={seq}",
            f"imDir={MOT_IMAGE_DIR}",
            f"frameRate={video['fps'] if video.get('fps') else ''}",
            f"seqLength={len(frames)}",
            f"imWidth={video['width']}",
            f"imHeight={video['height']}",
            f"imExt=.jpg",
            "",
            "; --- Lisez ceci avant d'interpreter gt.txt ---",
            "; seqLength = nombre de frames EXPORTEES, pas la duree de la video.",
            f"; La video source compte {video.get('frame_count')} frame(s) ;"
            f" le suivi couvre {video.get('exported_frames')} frame(s)"
            f" reparties sur {video.get('covered_span')} (couverture"
            f" {coverage:.1%}).",
            "; Les numeros de frame sont RELATIFS a la sequence exportee"
            " (base 1, contigus) :",
            f"; la correspondance vers l'index absolu du fichier video est dans"
            f" {FRAMES_MAP_NAME}.",
            f"; split={video.get('split')}",
            f"; visibility : {VISIBILITY_RULE}",
            "",
        ])
        (seq_dir / "seqinfo.ini").write_text(seqinfo, encoding="utf-8")

        if video.get("split"):
            seqmaps[str(video["split"])].append(seq)
        sequences.append({
            "name": seq,
            "media_id": media_id,
            "split": video.get("split"),
            "seq_length": len(frames),
            "frame_rate": video.get("fps"),
            "gt_lines": len(lines),
            "track_count": video.get("track_count"),
            "first_frame_absolute": video.get("first_frame"),
            "last_frame_absolute": video.get("last_frame"),
            "covered_span": video.get("covered_span"),
            "coverage_ratio": video.get("coverage_ratio"),
        })

    if seqmaps:
        maps_dir = root / "seqmaps"
        maps_dir.mkdir(parents=True, exist_ok=True)
        for split, names in sorted(seqmaps.items()):
            (maps_dir / f"{split}.txt").write_text(
                "name\n" + "\n".join(sorted(names)) + "\n", encoding="utf-8",
            )

    return {
        "root": MOT_ROOT,
        "columns": list(MOT_GT_COLUMNS),
        "frame_base": 1,
        "frame_scope": "relative_a_la_sequence_exportee",
        "frames_map": FRAMES_MAP_NAME,
        "visibility_rule": VISIBILITY_RULE,
        "visibility_by_origin": dict(VISIBILITY_BY_ORIGIN),
        "conf_value": 1,
        "class_column": "category_id de coco_vid.json",
        "sequence_count": len(sequences),
        "gt_line_count": total_lines,
        "sequences": sequences,
    }
