"""Descripteur Croissant (MLCommons) à la racine de chaque export scellé.

Le `manifest.json` du noyau dit tout - mais il le dit dans un vocabulaire
maison (`fishvision/export-manifest/1`). Aucun outil extérieur ne sait le lire.
**Croissant** (MLCommons, JSON-LD sur `schema.org/Dataset`) est le format que
lisent Hugging Face, Kaggle, OpenML et TensorFlow Datasets pour répondre à
trois questions : qu'est-ce que ce jeu de données, quels fichiers le
composent, et que contient chaque table.

Ce que ce module fait - et ne fait pas
--------------------------------------
- Il **décrit ce qui existe**, il ne fabrique rien. Le nom, la version, la
  date, le rang taxonomique, le découpage, les effectifs par classe et les
  empreintes de fichiers sont **repris du manifeste** ; aucune valeur n'est
  recalculée, aucun horodatage n'est pris à l'instant présent (règle de la
  phase 2 : un fichier haché ne contient jamais de `datetime.now()`).
- `distribution[]` reprend `files[]` du manifeste, un `cr:FileObject` par
  fichier, avec sa `sha256` et sa taille. Le seul fichier absent de la liste
  est `croissant.json` lui-même : il ne peut pas contenir son propre
  condensé. Le manifeste, lui, le hache comme les autres - il est écrit
  **avant** le scellement, exactement comme `datapackage.json` en phase 4a.
- `recordSet[]` est **minimal et honnête**, un par famille de formats :
  - COCO (`coco`) : `images`, `annotations`, `categories` extraits de
    `instances_fish.json`, avec les types de chaque champ ;
  - YOLO (`yolo`) : l'export contient d'abord son COCO, dont le dérivé
    `yolo/` est une conversion **avec perte** (ni `ignore`, ni attributs) -
    les `recordSet` renvoient donc vers les fichiers COCO, qui font foi ;
  - suivi (`coco_vid`, `mot`) : `videos`, `images`, `tracks`, `annotations`
    extraits de `coco_vid.json`, le pivot ; `mot/` en est dérivé ;
  - comportement (`ava`, `events_jsonl`) : les tables sont des CSV, déjà
    décrites colonne par colonne dans `datapackage.json` (Frictionless,
    phase 4a) - Croissant y renvoie plutôt que d'en donner une seconde
    version qui divergerait.

Licence : **non tranchée**, comme pour le Data Package. Le champ porte le même
placeholder explicite (`datapackage.LICENSE_TODO`) - mieux vaut une licence
déclarée « à définir » qu'un dataset muet dont personne ne saura s'il est
diffusable (décision client n° 4, `docs/refonte-donnees/05-decisions.md`).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from . import datapackage

CROISSANT_NAME = "croissant.json"
CONFORMS_TO = "http://mlcommons.org/croissant/1.0"

# Contexte JSON-LD de Croissant 1.0, recopié tel quel : c'est lui qui donne un
# sens aux préfixes `cr:` / `sc:` employés plus bas.
CROISSANT_CONTEXT: Dict[str, Any] = {
    "@language": "fr",
    "@vocab": "https://schema.org/",
    "citeAs": "cr:citeAs",
    "column": "cr:column",
    "conformsTo": "dct:conformsTo",
    "cr": "http://mlcommons.org/croissant/",
    "data": {"@id": "cr:data", "@type": "@json"},
    "dataBiases": "cr:dataBiases",
    "dataCollection": "cr:dataCollection",
    "dataType": {"@id": "cr:dataType", "@type": "@vocab"},
    "dct": "http://purl.org/dc/terms/",
    "extract": "cr:extract",
    "field": "cr:field",
    "fileProperty": "cr:fileProperty",
    "fileObject": "cr:fileObject",
    "fileSet": "cr:fileSet",
    "format": "cr:format",
    "includes": "cr:includes",
    "isLiveDataset": "cr:isLiveDataset",
    "jsonPath": "cr:jsonPath",
    "key": "cr:key",
    "md5": "cr:md5",
    "parentField": "cr:parentField",
    "path": "cr:path",
    "personalSensitiveInformation": "cr:personalSensitiveInformation",
    "recordSet": "cr:recordSet",
    "references": "cr:references",
    "regex": "cr:regex",
    "repeated": "cr:repeated",
    "replace": "cr:replace",
    "sc": "https://schema.org/",
    "separator": "cr:separator",
    "source": "cr:source",
    "subField": "cr:subField",
    "transform": "cr:transform",
}

# Types MIME par extension - Croissant exige `encodingFormat` sur chaque
# `cr:FileObject`. Une extension inconnue tombe sur `application/octet-stream`
# plutôt que sur une valeur inventée.
_MEDIA_TYPES: Dict[str, str] = {
    ".json": "application/json",
    ".jsonl": "application/jsonl",
    ".csv": "text/csv",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".yaml": "application/yaml",
    ".yml": "application/yaml",
    ".ini": "text/plain",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}
_DEFAULT_MEDIA_TYPE = "application/octet-stream"

# Fichiers pivots : ce sont eux que les `recordSet` interrogent.
COCO_FISH_FILE = "instances_fish.json"
COCO_VID_FILE = "coco_vid.json"


def media_type_for(path: str) -> str:
    return _MEDIA_TYPES.get(Path(path).suffix.lower(), _DEFAULT_MEDIA_TYPE)


def license_statement() -> str:
    """Même placeholder qu'en phase 4a, sous la forme attendue par schema.org."""
    entry = datapackage.LICENSE_TODO[0]
    return f"{entry['name']} - {entry['title']}"


# ── Distribution : un FileObject par fichier de l'export ───────────────────


def build_distribution(files: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """`files[]` du manifeste → `distribution[]` Croissant, sans rien inventer.

    `croissant.json` est exclu : il ne peut pas porter son propre condensé.
    """
    out: List[Dict[str, Any]] = []
    for row in files:
        path = str(row.get("path", ""))
        if not path or path == CROISSANT_NAME:
            continue
        entry: Dict[str, Any] = {
            "@type": "cr:FileObject",
            "@id": path,
            "name": path,
            "contentUrl": path,
            "encodingFormat": media_type_for(path),
        }
        if row.get("sha256"):
            entry["sha256"] = row["sha256"]
        if row.get("bytes") is not None:
            entry["contentSize"] = f"{int(row['bytes'])} B"
        out.append(entry)
    return out


# ── RecordSets ─────────────────────────────────────────────────────────────


def _field(
    record: str, name: str, dtype: str, description: str, *,
    source_file: str, json_path: str,
) -> Dict[str, Any]:
    return {
        "@type": "cr:Field",
        "@id": f"{record}/{name}",
        "name": name,
        "description": description,
        "dataType": dtype,
        "source": {
            "fileObject": {"@id": source_file},
            "extract": {"jsonPath": json_path},
        },
    }


def _coco_record_sets(source_file: str = COCO_FISH_FILE) -> List[Dict[str, Any]]:
    """Les trois tables d'un COCO : images, annotations, catégories."""
    img = "images"
    ann = "annotations"
    cat = "categories"
    return [
        {
            "@type": "cr:RecordSet",
            "@id": img,
            "name": img,
            "description": (
                "Une image livrée par image annotée : fichier, dimensions, "
                "split d'appartenance et provenance (média, index de frame "
                "ABSOLU dans la vidéo source, espace image)."
            ),
            "key": {"@id": f"{img}/id"},
            "field": [
                _field(img, "id", "sc:Integer", "Identifiant de l'image dans cet export.",
                       source_file=source_file, json_path="$.images[*].id"),
                _field(img, "file_name", "sc:Text",
                       "Chemin de l'image, relatif à la racine de l'export.",
                       source_file=source_file, json_path="$.images[*].file_name"),
                _field(img, "width", "sc:Integer", "Largeur en pixels.",
                       source_file=source_file, json_path="$.images[*].width"),
                _field(img, "height", "sc:Integer", "Hauteur en pixels.",
                       source_file=source_file, json_path="$.images[*].height"),
                _field(img, "split", "cr:Split",
                       "train, val ou test - l'affectation complète, groupe par "
                       "groupe, est dans splits.json.",
                       source_file=source_file, json_path="$.images[*].split"),
                _field(img, "media_id", "sc:Text",
                       "Identifiant du média source en base (table media_assets).",
                       source_file=source_file, json_path="$.images[*].media_id"),
                _field(img, "frame_index", "sc:Integer",
                       "Index de frame ABSOLU dans le fichier vidéo source.",
                       source_file=source_file, json_path="$.images[*].frame_index"),
                _field(img, "image_space", "sc:Text",
                       "Espace image des coordonnées : stereo_rectified_left "
                       "(cas normal) ou raw.",
                       source_file=source_file, json_path="$.images[*].image_space"),
            ],
        },
        {
            "@type": "cr:RecordSet",
            "@id": ann,
            "name": ann,
            "description": (
                "Une boîte englobante par poisson observé. `ignore=1` "
                "(avec `iscrowd=1`) marque un poisson vu mais non identifiable "
                "au rang demandé : la zone est exclue de la perte et de "
                "l'évaluation, elle n'est jamais de l'arrière-plan."
            ),
            "key": {"@id": f"{ann}/id"},
            "field": [
                _field(ann, "id", "sc:Integer", "Identifiant de l'annotation.",
                       source_file=source_file, json_path="$.annotations[*].id"),
                _field(ann, "image_id", "sc:Integer",
                       "Image portant cette boîte (images/id).",
                       source_file=source_file, json_path="$.annotations[*].image_id"),
                _field(ann, "category_id", "sc:Integer",
                       "Catégorie de la boîte (categories/id).",
                       source_file=source_file, json_path="$.annotations[*].category_id"),
                _field(ann, "bbox", "cr:BoundingBox",
                       "Boîte au format COCO [x, y, largeur, hauteur], en pixels "
                       "de l'image livrée.",
                       source_file=source_file, json_path="$.annotations[*].bbox"),
                _field(ann, "area", "sc:Float", "Aire de la boîte, en pixels carrés.",
                       source_file=source_file, json_path="$.annotations[*].area"),
                _field(ann, "iscrowd", "sc:Integer",
                       "1 pour une zone traitée comme une foule (ici : non "
                       "identifiable), 0 sinon.",
                       source_file=source_file, json_path="$.annotations[*].iscrowd"),
                _field(ann, "ignore", "sc:Integer",
                       "1 = boîte exclue de la perte et de l'évaluation, 0 sinon.",
                       source_file=source_file, json_path="$.annotations[*].ignore"),
            ],
        },
        {
            "@type": "cr:RecordSet",
            "@id": cat,
            "name": cat,
            "description": (
                "Les classes du jeu de catégories. `instances_fish.json` n'en "
                "déclare qu'une (« fish », zéro ignore) ; le fichier "
                "`instances_<rang>.json`, quand il existe, porte les classes "
                "taxonomiques réellement atteintes plus « unidentified »."
            ),
            "key": {"@id": f"{cat}/id"},
            "field": [
                _field(cat, "id", "sc:Integer", "Identifiant de la catégorie.",
                       source_file=source_file, json_path="$.categories[*].id"),
                _field(cat, "name", "sc:Text", "Nom de la classe.",
                       source_file=source_file, json_path="$.categories[*].name"),
                _field(cat, "supercategory", "sc:Text",
                       "Rang taxonomique dont relève la classe.",
                       source_file=source_file, json_path="$.categories[*].supercategory"),
            ],
        },
    ]


def _tracking_record_sets(source_file: str = COCO_VID_FILE) -> List[Dict[str, Any]]:
    """Les quatre tables du pivot de suivi COCO-VID (style TAO)."""
    vid, img, trk, ann = "videos", "images", "tracks", "annotations"
    return [
        {
            "@type": "cr:RecordSet",
            "@id": vid,
            "name": vid,
            "description": "Une ligne par vidéo source entrée dans l'export.",
            "key": {"@id": f"{vid}/id"},
            "field": [
                _field(vid, "id", "sc:Integer", "Identifiant de la vidéo dans cet export.",
                       source_file=source_file, json_path="$.videos[*].id"),
                _field(vid, "name", "sc:Text", "Nom de la vidéo.",
                       source_file=source_file, json_path="$.videos[*].name"),
                _field(vid, "media_id", "sc:Text",
                       "Identifiant du média en base (table media_assets).",
                       source_file=source_file, json_path="$.videos[*].media_id"),
            ],
        },
        {
            "@type": "cr:RecordSet",
            "@id": img,
            "name": img,
            "description": (
                "Une image par frame livrée. `frame_id` est l'index ABSOLU dans "
                "le fichier vidéo source ; le dérivé MOT renumérote en base 1 et "
                "livre la correspondance dans frames_map.csv."
            ),
            "key": {"@id": f"{img}/id"},
            "field": [
                _field(img, "id", "sc:Integer", "Identifiant de l'image.",
                       source_file=source_file, json_path="$.images[*].id"),
                _field(img, "video_id", "sc:Integer", "Vidéo dont vient l'image.",
                       source_file=source_file, json_path="$.images[*].video_id"),
                _field(img, "frame_id", "sc:Integer",
                       "Index de frame ABSOLU dans le fichier vidéo source.",
                       source_file=source_file, json_path="$.images[*].frame_id"),
                _field(img, "file_name", "sc:Text",
                       "Chemin de l'image, relatif à la racine de l'export.",
                       source_file=source_file, json_path="$.images[*].file_name"),
                _field(img, "split", "cr:Split", "train, val ou test.",
                       source_file=source_file, json_path="$.images[*].split"),
            ],
        },
        {
            "@type": "cr:RecordSet",
            "@id": trk,
            "name": trk,
            "description": (
                "L'identité des pistes. `id` est un entier dense propre à cet "
                "export ; l'identité durable est `track_db_id` (UUID en base) et "
                "`external_track_id` (numéro affiché par le suivi, unique dans "
                "une seule vidéo)."
            ),
            "key": {"@id": f"{trk}/id"},
            "field": [
                _field(trk, "id", "sc:Integer",
                       "instance_id dense de la piste dans cet export.",
                       source_file=source_file, json_path="$.tracks[*].id"),
                _field(trk, "video_id", "sc:Integer", "Vidéo de la piste.",
                       source_file=source_file, json_path="$.tracks[*].video_id"),
                _field(trk, "category_id", "sc:Integer", "Classe de la piste.",
                       source_file=source_file, json_path="$.tracks[*].category_id"),
                _field(trk, "track_db_id", "sc:Text",
                       "Identifiant durable de la piste en base (UUID).",
                       source_file=source_file, json_path="$.tracks[*].track_db_id"),
            ],
        },
        {
            "@type": "cr:RecordSet",
            "@id": ann,
            "name": ann,
            "description": (
                "Une boîte par poisson et par frame, rattachée à sa piste par "
                "`instance_id`."
            ),
            "key": {"@id": f"{ann}/id"},
            "field": [
                _field(ann, "id", "sc:Integer", "Identifiant de la boîte.",
                       source_file=source_file, json_path="$.annotations[*].id"),
                _field(ann, "image_id", "sc:Integer", "Image portant la boîte.",
                       source_file=source_file, json_path="$.annotations[*].image_id"),
                _field(ann, "instance_id", "sc:Integer",
                       "Piste à laquelle appartient la boîte (tracks/id).",
                       source_file=source_file, json_path="$.annotations[*].instance_id"),
                _field(ann, "category_id", "sc:Integer", "Classe de la boîte.",
                       source_file=source_file, json_path="$.annotations[*].category_id"),
                _field(ann, "bbox", "cr:BoundingBox",
                       "Boîte au format COCO [x, y, largeur, hauteur], en pixels.",
                       source_file=source_file, json_path="$.annotations[*].bbox"),
                _field(ann, "ignore", "sc:Integer",
                       "1 = boîte exclue de la perte et de l'évaluation.",
                       source_file=source_file, json_path="$.annotations[*].ignore"),
            ],
        },
    ]


# Formats du noyau → (record sets, phrase de renvoi). Les clés sont celles de
# `export_core.SUPPORTED_FORMATS` ; un format inconnu sort sans recordSet
# plutôt qu'avec une description fausse.
_FORMAT_NOTES: Dict[str, str] = {
    "coco": (
        "Pivot COCO. Deux jeux de catégories partagent les mêmes images et les "
        "mêmes identifiants d'annotation : instances_fish.json (une classe, "
        "zéro ignore) et instances_<rang>.json quand un rang taxonomique a été "
        "demandé. Les recordSet ci-dessous décrivent le premier."
    ),
    "yolo": (
        "Dérivé YOLO. Le dossier yolo/ est une conversion AVEC PERTE du COCO "
        "livré dans le même export : YOLO ne sait pas exprimer l'ignore, les "
        "images à poisson non identifié en sont donc écartées en entier, et les "
        "attributs d'annotation sont perdus. Les recordSet décrivent les "
        "fichiers COCO, qui font foi ; yolo/data.yaml décrit le dérivé."
    ),
    "coco_vid": (
        "Pivot de suivi COCO-VID (style TAO). Les recordSet décrivent "
        "coco_vid.json."
    ),
    "mot": (
        "Dérivé MOTChallenge (mot/<séquence>/gt/gt.txt + seqinfo.ini), produit "
        "à partir du pivot coco_vid.json livré dans le même export - jamais "
        "relu depuis la base. Ses numéros de frame sont relatifs (base 1) ; la "
        "correspondance avec les index absolus est dans frames_map.csv. Les "
        "recordSet décrivent le pivot, qui fait foi."
    ),
    "ava": (
        "Comportements au format AVA. Les tables sont des CSV (events.csv, "
        "actions.csv) : leurs colonnes sont décrites une à une - type, unité, "
        "description - dans datapackage.json (Frictionless Data Package) livré "
        "à la racine de cet export. Croissant y renvoie plutôt que d'en donner "
        "une seconde description qui divergerait."
    ),
    "events_jsonl": (
        "Intervalles de comportement, une ligne JSON par événement dans "
        "events.jsonl (revue et métriques écologiques). Chaque ligne porte sa "
        "source (manual / heuristic) et son appartenance au dataset AVA. Le "
        "manifeste (manifest.json, section `behavior`) en donne les "
        "conventions."
    ),
}


def record_sets_for(fmt: str) -> List[Dict[str, Any]]:
    if fmt in ("coco", "yolo"):
        return _coco_record_sets()
    if fmt in ("coco_vid", "mot"):
        return _tracking_record_sets()
    # Comportement : tables CSV décrites par datapackage.json, ou JSONL.
    return []


# ── Assemblage ─────────────────────────────────────────────────────────────


def build_croissant(
    manifest_core: Dict[str, Any],
    files: Iterable[Dict[str, Any]],
) -> Dict[str, Any]:
    """Descripteur Croissant d'un export, **entièrement dérivé du manifeste**.

    Aucune valeur n'est recalculée depuis la base : si le manifeste et ce
    fichier divergeaient, l'un des deux mentirait. `files` est le `files[]`
    que le manifeste s'apprête à porter.
    """
    fmt = str(manifest_core.get("format", ""))
    name = str(manifest_core.get("dataset_name", "")) or "export"
    version = str(manifest_core.get("dataset_version", ""))
    created = str(manifest_core.get("created_at", ""))
    rank = str(manifest_core.get("taxonomy_rank", ""))
    source = manifest_core.get("source") or {}
    split = manifest_core.get("split") or {}
    coordinate = manifest_core.get("coordinate_frame") or {}

    parts = [
        "Jeu de données exporté par AquaMeasure / fish-vision depuis sa base "
        "d'annotations SQLite, qui reste la source de vérité : un export est un "
        "artefact dérivé, immuable et régénérable.",
        _FORMAT_NOTES.get(fmt, f"Format « {fmt} »."),
    ]
    if rank:
        parts.append(f"Rang taxonomique demandé : {rank}.")
    if split.get("split_by"):
        parts.append(
            "Découpage train/val/test PAR GROUPE "
            f"({split['split_by']}), graine {split.get('seed')} : deux frames "
            "voisines d'une même vidéo ne peuvent pas se retrouver de part et "
            "d'autre du découpage. Val et test sont intégralement identifiés."
        )
    if coordinate.get("image_space"):
        parts.append(
            f"Espace image des coordonnées : {coordinate['image_space']} ; "
            "les index de frame sont absolus dans le fichier vidéo source."
        )
    parts.append(
        "Le manifeste manifest.json de cet export porte le détail complet "
        "(empreinte de la base, commit git, exclusions motivées, effectifs par "
        "classe, condensé content_sha256)."
    )

    payload: Dict[str, Any] = {
        "@context": dict(CROISSANT_CONTEXT),
        "@type": "sc:Dataset",
        "conformsTo": CONFORMS_TO,
        "name": f"{name}_v{version}" if version else name,
        "description": " ".join(parts),
        "license": license_statement(),
        "version": version,
        "dateCreated": created,
        "creator": {
            "@type": "sc:Organization",
            "name": "AquaMeasure / fish-vision",
        },
        "keywords": [
            kw for kw in (
                "poissons", "vidéo sous-marine", "stéréo", "détection", rank, fmt,
            ) if kw
        ],
        "distribution": build_distribution(files),
    }
    if manifest_core.get("git_commit"):
        payload["citeAs"] = (
            f"{name} v{version} - export fish-vision du {created} "
            f"(commit {manifest_core['git_commit']})."
        )
    counts = {
        key: source[key]
        for key in ("media_count", "image_count", "annotation_count", "event_count")
        if source.get(key) is not None
    }
    if counts:
        # Champ libre schema.org : les effectifs exacts vivent dans le
        # manifeste, on n'en donne ici qu'un résumé lisible.
        payload["measurementTechnique"] = ", ".join(
            f"{key} = {value}" for key, value in counts.items()
        )
    records = record_sets_for(fmt)
    if records:
        payload["recordSet"] = records
    return payload


def write_croissant(
    staging: Path,
    *,
    manifest_core: Dict[str, Any],
    files: Iterable[Dict[str, Any]],
) -> Path:
    """Écrit `croissant.json` à la racine du dossier d'export, avant scellement."""
    path = Path(staging) / CROISSANT_NAME
    payload = build_croissant(manifest_core, files)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8",
    )
    return path


def load_croissant(export_dir: Path) -> Optional[Dict[str, Any]]:
    """Relit le descripteur d'un export scellé (None s'il n'y en a pas)."""
    path = Path(export_dir) / CROISSANT_NAME
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))
