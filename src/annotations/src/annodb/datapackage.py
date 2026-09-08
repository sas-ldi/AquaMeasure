"""Descripteurs Frictionless Data Package autour des CSV produits.

Un CSV nu ne dit pas ce qu'il contient. `measurement_mm` est-il en millimètres
ou en pixels ? `frame_index` compte-t-il depuis le début du fichier ou depuis
le repère de synchronisation ? `is_grazing` vaut-il 0/1 ou vrai/faux ? Six
mois plus tard, ou entre deux laboratoires, ces questions n'ont plus de
réponse - c'est exactement le genre de perte que la refonte cherche à éviter.

Un **Data Package** (specs.frictionlessdata.io) est un simple fichier JSON
posé à côté du CSV, qui déclare : le nom et le titre de la ressource, son
encodage, ses colonnes avec leur type, leur description en français et leur
unité, sa clé primaire quand il en existe une, et sa licence.

Choix de mise en œuvre
----------------------
- **Aucune dépendance pip nouvelle** : le descripteur est un dictionnaire
  Python écrit à la main. Il reste validable par l'outil officiel
  (`pip install frictionless && frictionless validate ...`) pour qui le veut.
- **Les colonnes déclarées sont celles réellement écrites** : elles sont
  dérivées des mêmes constantes que les exporteurs (`TIMELINE_CSV_FIELDS`,
  `ABUNDANCE_CSV_FIELDS`, `GRAZING_CSV_FIELDS`, `AVA_COLUMNS`,
  `ACTIONS_COLUMNS`) et un test compare l'en-tête du CSV réel au descripteur.
  Une colonne ajoutée sans description fait échouer ce test - c'est voulu.
- **Un descripteur par CSV** (`<nom>.datapackage.json`) quand plusieurs CSV
  sans lien cohabitent dans un même dossier d'exports, et **un seul
  `datapackage.json`** à la racine d'un export scellé, qui décrit l'ensemble
  de ses tables (il entre alors dans `files[]` du manifeste).

Licence : **non tranchée**. Le champ est présent, avec un TODO explicite -
mieux vaut une licence déclarée « à définir » qu'un fichier muet dont personne
ne saura s'il est diffusable.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

PROFILE = "tabular-data-package"
RESOURCE_PROFILE = "tabular-data-resource"

# Format des horodatages écrits par les exporteurs (`datetime.isoformat(
# sep=" ", timespec="seconds")`). Déclaré explicitement : la valeur `default`
# de Frictionless attend un « T » séparateur, ce que nous n'écrivons pas.
DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S"

# Une cellule vide se lit comme « donnée absente ». Attention : « NA » n'est
# **pas** une valeur manquante ici - c'est une détermination taxonomique
# revendiquée (« l'observateur n'a pas pu descendre à ce rang »), et la
# confondre avec un trou fausserait toute analyse.
MISSING_VALUES = [""]

# TODO(client) - la licence de diffusion n'a pas encore été tranchée
# (docs/refonte-donnees/05-decisions.md). Remplacer ce bloc par la licence
# retenue (par exemple `{"name": "CC-BY-4.0", "path":
# "https://creativecommons.org/licenses/by/4.0/", "title": "..."}`) avant
# toute diffusion hors de l'équipe.
LICENSE_TODO: List[Dict[str, str]] = [
    {
        "name": "a-definir",
        "title": (
            "À définir - la licence de diffusion de ces données n'a pas encore "
            "été décidée avec le client. TODO : remplacer par la licence "
            "retenue avant toute publication ou tout partage externe."
        ),
    }
]


def _field(
    name: str,
    ftype: str,
    description: str,
    *,
    unit: Optional[str] = None,
    fformat: Optional[str] = None,
) -> Dict[str, Any]:
    field: Dict[str, Any] = {"name": name, "type": ftype, "description": description}
    if unit:
        field["unit"] = unit
    if fformat:
        field["format"] = fformat
    return field


# ── Colonnes communes aux CSV de session ──────────────────────────────────

_SESSION_FIELDS = [
    _field(
        "session_date", "datetime",
        "Date et heure de la sortie terrain, telles qu'enregistrées sur le média.",
        fformat=DATETIME_FORMAT,
    ),
    _field("site", "string", "Lieu de la sortie (nom du site de plongée)."),
    _field("session_title", "string", "Intitulé libre de la session."),
    _field("video_name", "string", "Nom du fichier vidéo source."),
    _field(
        "media_id", "string",
        "Identifiant du média en base (table media_assets) - c'est lui qui "
        "relie cette ligne à la vidéo, même si le fichier est renommé.",
    ),
]

_FRAME_FIELDS = [
    _field(
        "frame_index", "integer",
        "Numéro d'image ABSOLU dans le fichier vidéo source (la première image "
        "du fichier porte le numéro 0).",
        unit="images",
    ),
    _field(
        "frame_ref", "string",
        "Référentiel d'origine de la ligne : « absolute » (écrite en index "
        "absolu) ou « timeline_legacy » (écrite avant la phase 0 en index "
        "relatif au repère de synchronisation, puis convertie à la lecture).",
    ),
    _field(
        "time_offset_s", "number",
        "Instant correspondant depuis le début de la vidéo.",
        unit="s",
    ),
]

_TAXON_FIELDS = [
    _field(
        "family", "string",
        "Famille déterminée, ou « NA » si l'observateur n'a pas pu descendre à "
        "ce rang. « NA » est une information, pas une donnée manquante.",
    ),
    _field("genus", "string", "Genre déterminé, ou « NA »."),
    _field("species", "string", "Espèce déterminée, ou « NA »."),
    _field("common_name", "string", "Nom vernaculaire de l'espèce, ou « NA »."),
]


# ── Ressource « chronologie » (timeline) ──────────────────────────────────

TIMELINE_FIELDS: List[Dict[str, Any]] = [
    *_SESSION_FIELDS,
    *_FRAME_FIELDS,
    _field(
        "wall_clock_time", "datetime",
        "Heure réelle estimée de l'image (date de session + décalage).",
        fformat=DATETIME_FORMAT,
    ),
    _field(
        "track_id", "string",
        "Identifiant en base de la piste (le même poisson suivi d'une image à "
        "l'autre) ; vide si la ligne ne vient pas d'un suivi.",
    ),
    _field(
        "external_track_id", "string",
        "Numéro de piste affiché à l'écran par le suivi automatique. Il n'est "
        "unique qu'au sein d'une vidéo.",
    ),
    *_TAXON_FIELDS,
    _field(
        "is_grazing", "integer",
        "1 si un événement de broutage couvre cette image pour cette piste, "
        "0 sinon.",
    ),
    _field(
        "grazing_event_id", "string",
        "Identifiant de l'événement qui couvre l'image ; vide si aucun.",
    ),
    _field(
        "event_type", "string",
        "Clé du type d'événement couvrant l'image, prise dans le catalogue "
        "event_types (« grazing », …) ; vide si aucun. Colonne ajoutée en "
        "phase 0bis : le catalogue de comportements est ouvert, la broute "
        "n'est que le premier.",
    ),
    _field("bbox_x1", "number", "Bord gauche de la boîte englobante.", unit="px"),
    _field("bbox_y1", "number", "Bord haut de la boîte englobante.", unit="px"),
    _field("bbox_x2", "number", "Bord droit de la boîte englobante.", unit="px"),
    _field("bbox_y2", "number", "Bord bas de la boîte englobante.", unit="px"),
    _field(
        "geometry_space", "string",
        "Espace image dans lequel la boîte a été tracée : "
        "« stereo_rectified_left » (image rectifiée gauche, le cas normal) ou "
        "« raw » (image brute). Colonne ajoutée en phase 0 : sans elle, les "
        "coordonnées se comparaient d'un espace à l'autre à quelques dizaines "
        "de pixels près, sans que rien n'alerte.",
    ),
    _field("cx", "number", "Abscisse du centre de la boîte.", unit="px"),
    _field("cy", "number", "Ordonnée du centre de la boîte.", unit="px"),
    _field(
        "measurement_mm", "number",
        "Longueur mesurée par triangulation stéréo ; vide si non mesurée.",
        unit="mm",
    ),
    _field(
        "position_x_mm", "number",
        "Position du poisson dans le repère de la caméra gauche, axe X.",
        unit="mm",
    ),
    _field("position_y_mm", "number", "Idem, axe Y.", unit="mm"),
    _field(
        "position_z_mm", "number",
        "Idem, axe Z (distance au banc stéréo).", unit="mm",
    ),
]

# ── Ressource « broutes / événements » ────────────────────────────────────

GRAZING_FIELDS: List[Dict[str, Any]] = [
    _field("media_id", "string", "Identifiant du média en base."),
    _field("track_id", "string", "Identifiant en base de la piste concernée."),
    _field(
        "external_track_id", "string",
        "Numéro de piste affiché à l'écran par le suivi automatique.",
    ),
    _field(
        "event_id", "string",
        "Identifiant de l'événement en base - clé primaire de ce tableau.",
    ),
    _field(
        "frame_start", "integer",
        "Première image de l'intervalle, en index ABSOLU du fichier source.",
        unit="images",
    ),
    _field(
        "frame_end", "integer",
        "Dernière image de l'intervalle, incluse, en index ABSOLU.",
        unit="images",
    ),
    _field(
        "frame_ref", "string",
        "Référentiel d'origine de la ligne : « absolute » ou "
        "« timeline_legacy ». Une ligne historique dont l'offset de "
        "synchronisation est introuvable sort avec ses bornes d'origine et le "
        "signale ici plutôt que de se faire passer pour de l'absolu.",
    ),
    _field(
        "duration_frames", "integer",
        "Durée de l'intervalle, bornes incluses : frame_end − frame_start + 1.",
        unit="images",
    ),
    _field(
        "source", "string",
        "Origine de l'événement : « manual » (annoté par un opérateur) ou "
        "« heuristic » (présomption automatique). Seuls les événements "
        "manuels entrent dans les datasets d'apprentissage.",
    ),
]

# ── Ressource « abondance par image » ─────────────────────────────────────

ABUNDANCE_FIELDS: List[Dict[str, Any]] = [
    *_SESSION_FIELDS,
    *_FRAME_FIELDS,
    _field(
        "ai_count", "integer",
        "Nombre de poissons trouvés par la détection automatique sur cette "
        "image.",
    ),
    _field(
        "manual_count", "integer",
        "Comptage corrigé par l'opérateur ; vide s'il n'a pas corrigé.",
    ),
    _field(
        "count_used", "integer",
        "Comptage qui fait foi : celui de l'opérateur s'il existe, sinon celui "
        "de l'IA. C'est sur cette colonne que se calcule le MaxN (plus grand "
        "nombre de poissons vus simultanément).",
    ),
    _field(
        "count_source", "string",
        "D'où vient count_used : « manual » ou « ai ».",
    ),
    _field(
        "validated", "integer",
        "1 si l'opérateur a validé le comptage de cette image, 0 sinon.",
    ),
    _field(
        "updated_at", "datetime",
        "Dernière modification du comptage.",
        fformat=DATETIME_FORMAT,
    ),
]

# ── Ressources du noyau scellé : comportement type AVA ────────────────────

AVA_EVENTS_FIELDS: List[Dict[str, Any]] = [
    _field(
        "video_id", "string",
        "Identifiant de la vidéo dans cet export (media_id).",
    ),
    _field(
        "timestamp_s", "number",
        "Instant de l'observation depuis le début de la vidéo.",
        unit="s",
    ),
    _field(
        "x1", "number",
        "Bord gauche de la boîte, NORMALISÉ par la largeur du média (0 à 1).",
    ),
    _field("y1", "number", "Bord haut, normalisé par la hauteur (0 à 1)."),
    _field("x2", "number", "Bord droit, normalisé par la largeur (0 à 1)."),
    _field("y2", "number", "Bord bas, normalisé par la hauteur (0 à 1)."),
    _field(
        "action_id", "integer",
        "Index dense du comportement, propre à cet export. L'identité durable "
        "est la colonne « key » de actions.csv.",
    ),
    _field(
        "track_id", "integer",
        "Numéro d'instance du poisson suivi, le même que dans l'export de "
        "suivi (COCO-VID / MOT).",
    ),
]

AVA_ACTIONS_FIELDS: List[Dict[str, Any]] = [
    _field(
        "action_id", "integer",
        "Index dense du comportement dans cet export - clé primaire.",
    ),
    _field(
        "key", "string",
        "Clé durable du type d'événement (« grazing », …), stable d'un export "
        "à l'autre.",
    ),
    _field("label", "string", "Libellé français affiché dans l'application."),
    _field(
        "scope", "string",
        "Portée de l'annotation : « interval » (début / fin) ou « instant ».",
    ),
    _field(
        "is_builtin", "integer",
        "1 si le type est livré avec l'application, 0 s'il a été ajouté.",
    ),
    _field(
        "event_type_id", "string",
        "Identifiant du type dans la table event_types.",
    ),
    _field("description", "string", "Description du comportement, en français."),
]


# ── Catalogue des ressources ──────────────────────────────────────────────

RESOURCE_SPECS: Dict[str, Dict[str, Any]] = {
    "timeline": {
        "name": "chronologie",
        "title": "Chronologie détaillée de la session",
        "description": (
            "Une ligne par poisson et par image : qui a été vu, quand, où dans "
            "l'image, à quelle taille, et s'il broutait. Quand la session a été "
            "suivie automatiquement, les lignes viennent des positions de "
            "pistes ; sinon, elles viennent des poissons encadrés à la main.\n"
            "Pas de clé primaire déclarée : plusieurs poissons peuvent être "
            "annotés sur la même image sans piste, la combinaison "
            "(media_id, frame_index, track_id) n'est donc pas garantie unique. "
            "La déclarer serait une promesse fausse."
        ),
        "fields": TIMELINE_FIELDS,
        "primaryKey": None,
        "encoding": "utf-8-sig",
    },
    "grazing": {
        "name": "evenements",
        "title": "Événements marqués (broutes et autres comportements)",
        "description": (
            "Un intervalle par ligne : le poisson concerné, l'image de début, "
            "l'image de fin et la durée. Sert à comparer la pression de "
            "broutage entre sites."
        ),
        "fields": GRAZING_FIELDS,
        "primaryKey": ["event_id"],
        "encoding": "utf-8-sig",
    },
    "abundance": {
        "name": "abondance",
        "title": "Abondance image par image (MaxN)",
        "description": (
            "Une ligne par image comptée : ce que l'IA a trouvé, ce que "
            "l'opérateur a corrigé, et lequel fait foi. Le maximum de "
            "count_used sur la session est le MaxN, l'indicateur d'abondance "
            "standard des vidéos sous-marines - il ne compte jamais deux fois "
            "le même individu."
        ),
        "fields": ABUNDANCE_FIELDS,
        # Garantie par la contrainte UNIQUE(media_id, frame_index) en base.
        "primaryKey": ["media_id", "frame_index"],
        "encoding": "utf-8-sig",
    },
    "ava_events": {
        "name": "evenements_ava",
        "title": "Comportements au format AVA",
        "description": (
            "« Ce poisson-là, à cet instant-là, avait ce comportement. » "
            "Format tabulaire attendu par les modèles de reconnaissance "
            "d'actions. L'en-tête est présent : les chargeurs AVA le sautent "
            "avec skiprows=1."
        ),
        "fields": AVA_EVENTS_FIELDS,
        "primaryKey": None,
        # Écrit par `export_behavior.write_ava` en UTF-8 sans BOM.
        "encoding": "utf-8",
    },
    "ava_actions": {
        "name": "comportements",
        "title": "Catalogue des comportements annotables",
        "description": (
            "La table de correspondance entre les numéros d'action de "
            "events.csv et les comportements du catalogue event_types."
        ),
        "fields": AVA_ACTIONS_FIELDS,
        "primaryKey": ["action_id"],
        "encoding": "utf-8",
    },
}


def field_names(kind: str) -> List[str]:
    """Noms des colonnes déclarées pour une ressource, dans l'ordre."""
    return [f["name"] for f in RESOURCE_SPECS[kind]["fields"]]


def build_resource(kind: str, path: str) -> Dict[str, Any]:
    """Descripteur d'une ressource tabulaire, pointant sur `path` (relatif)."""
    spec = RESOURCE_SPECS[kind]
    resource: Dict[str, Any] = {
        "name": spec["name"],
        "title": spec["title"],
        "description": spec["description"],
        "profile": RESOURCE_PROFILE,
        "path": path,
        "format": "csv",
        "mediatype": "text/csv",
        "encoding": spec["encoding"],
        "dialect": {"delimiter": ",", "header": True},
        "schema": {
            "fields": [dict(f) for f in spec["fields"]],
            "missingValues": list(MISSING_VALUES),
        },
    }
    if spec.get("primaryKey"):
        resource["schema"]["primaryKey"] = list(spec["primaryKey"])
    return resource


def build_datapackage(
    resources: Iterable[tuple[str, str]],
    *,
    name: str,
    title: str,
    description: str = "",
    created: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Descripteur complet. `resources` = suite de (kind, chemin relatif).

    `created` est **fourni par l'appelant** et jamais calculé ici : un
    descripteur écrit dans un export scellé entre dans le condensé du
    manifeste, et un horodatage spontané rendrait deux exports identiques
    différents (règle posée en phase 2).
    """
    payload: Dict[str, Any] = {
        "profile": PROFILE,
        "name": name,
        "title": title,
        "licenses": [dict(entry) for entry in LICENSE_TODO],
        "resources": [build_resource(kind, path) for kind, path in resources],
    }
    if description:
        payload["description"] = description
    if created:
        payload["created"] = created
    if extra:
        payload.update(extra)
    return payload


def write_datapackage(path: Path, payload: Dict[str, Any]) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return path


def descriptor_path_for_csv(csv_path: Path) -> Path:
    """`sortie.csv` → `sortie.datapackage.json`, dans le même dossier.

    Plusieurs CSV sans lien cohabitent dans `data/exports` : un
    `datapackage.json` unique y serait écrasé par le prochain export.
    """
    csv_path = Path(csv_path)
    # Pas de `with_suffix` : un nom de vidéo comme « Runcam6_0004 (1).MP4 »
    # laisse des points dans le tronc, et `with_suffix` en mangerait un
    # morceau.
    return csv_path.parent / (csv_path.stem + ".datapackage.json")


def write_datapackage_for_csv(
    kind: str,
    csv_path: Path,
    *,
    created: Optional[str] = None,
) -> Path:
    """Écrit le descripteur d'un CSV isolé, à côté de lui."""
    spec = RESOURCE_SPECS[kind]
    csv_path = Path(csv_path)
    payload = build_datapackage(
        [(kind, csv_path.name)],
        name=spec["name"],
        title=spec["title"],
        description=spec["description"],
        created=created,
    )
    return write_datapackage(descriptor_path_for_csv(csv_path), payload)


def write_behavior_datapackage(
    staging: Path,
    *,
    events_csv: str,
    actions_csv: str,
    created: Optional[str] = None,
    dataset_name: str = "comportement",
    dataset_version: str = "",
) -> Path:
    """Descripteur unique d'un export de comportement scellé.

    Écrit à la racine du dossier d'export, avant scellement : il entre donc de
    lui-même dans `files[]` du manifeste et dans son condensé.
    """
    title = "Comportements annotés (AVA) - " + dataset_name
    if dataset_version:
        title = f"{title} v{dataset_version}"
    payload = build_datapackage(
        [("ava_events", events_csv), ("ava_actions", actions_csv)],
        name=dataset_name or "comportement",
        title=title,
        description=(
            "Les deux tables CSV de cet export : les observations de "
            "comportement et le catalogue des comportements annotables. "
            "Le manifeste (manifest.json) décrit le reste de l'export."
        ),
        created=created,
    )
    return write_datapackage(Path(staging) / "datapackage.json", payload)
