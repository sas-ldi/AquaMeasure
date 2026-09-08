"""Identité de l'annotateur - qui a réellement déterminé quoi.

`spatial_annotations.author` et `temporal_events.author` valaient `'operator'`
en dur sur 100 % des lignes : impossible de savoir qui avait identifié un
poisson, impossible de créditer qui que ce soit dans un jeu de données publié,
impossible d'arbitrer deux déterminations contradictoires.

Deux morceaux :

- la table `annotators` (nom affiché, ORCID facultatif) ;
- le **dernier annotateur utilisé**, mémorisé dans `app_settings.json` (même
  mécanisme que `fishial_min_refs`), pour ne pas redemander l'identité à chaque
  démarrage.

L'ORCID (https://orcid.org) est un identifiant international de chercheur : il
survit aux homonymes, aux changements de nom et de laboratoire. Facultatif.
"""

from __future__ import annotations

import re
import unicodedata
import uuid
from typing import Any, Dict, List, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .app_settings import get_setting, save_settings
from .models import Annotator, CaptureSession

SETTING_CURRENT = "current_annotator_id"

# 16 chiffres groupés par 4, dernier caractère éventuellement « X ».
_ORCID_RE = re.compile(r"^\d{4}-\d{4}-\d{4}-\d{3}[\dX]$")


def normalize_orcid(value: Any) -> Optional[str]:
    """Normalise un ORCID saisi (URL, espaces, sans tirets) ; None si vide.

    Lève `ValueError` sur une saisie non vide qui n'est pas un ORCID : mieux
    vaut refuser tout de suite qu'enregistrer un identifiant faux et le publier.
    """
    text = str(value or "").strip()
    if not text:
        return None
    text = text.replace("https://orcid.org/", "").replace("http://orcid.org/", "")
    text = text.replace(" ", "").upper()
    if _ORCID_RE.match(text):
        return text
    digits = text.replace("-", "")
    if len(digits) == 16 and digits[:15].isdigit() and (digits[15].isdigit() or digits[15] == "X"):
        grouped = "-".join(digits[i:i + 4] for i in range(0, 16, 4))
        return grouped
    raise ValueError(
        "ORCID invalide - format attendu : 0000-0002-1825-0097 (laissez vide si "
        "vous n'en avez pas)"
    )


def count_annotators(db: Session) -> int:
    return int(db.scalar(select(func.count(Annotator.id))) or 0)


def annotator_as_dict(row: Optional[Annotator]) -> Dict[str, Any]:
    if row is None:
        return {}
    return {
        "annotator_id": row.id,
        "display_name": row.display_name,
        "orcid": row.orcid or "",
    }


def list_annotators(db: Session) -> List[Dict[str, Any]]:
    rows = db.scalars(select(Annotator).order_by(Annotator.display_name)).all()
    return [annotator_as_dict(row) for row in rows]


def _clean_display_name(value: Any) -> str:
    """Nettoie un nom sans modifier la casse choisie par son auteur."""
    return " ".join(str(value or "").split())


def _display_name_key(value: Any) -> str:
    """Clé Unicode stable pour comparer casse et espaces indifféremment."""
    return unicodedata.normalize("NFKC", _clean_display_name(value)).casefold()


def list_known_annotator_names(db: Session) -> List[str]:
    """Noms proposés par l'interface, y compris ceux des anciennes sessions.

    Les sessions ont porté le nom de l'opérateur avant l'arrivée de la table
    ``annotators``. Les relire évite de demander à nouveau la même identité et
    de créer des variantes qui ne diffèrent que par la casse ou les espaces.
    """
    values = [
        *db.scalars(select(Annotator.display_name)).all(),
        *db.scalars(select(CaptureSession.operator)).all(),
    ]
    by_key: Dict[str, str] = {}
    for value in values:
        name = _clean_display_name(value)
        key = _display_name_key(name)
        if key and key not in by_key:
            by_key[key] = name
    return sorted(by_key.values(), key=lambda value: value.casefold())


def get_annotator(db: Session, annotator_id: Optional[str]) -> Optional[Annotator]:
    if not annotator_id:
        return None
    return db.get(Annotator, str(annotator_id))


def create_annotator(
    db: Session,
    *,
    display_name: str,
    orcid: Optional[str] = None,
    annotator_id: Optional[str] = None,
) -> Annotator:
    """Crée une identité. Le nom affiché est obligatoire.

    Un nom déjà présent renvoie la ligne existante (complétée de son ORCID s'il
    manquait) : deux « Pierrick » distincts en base seraient pires qu'un seul.
    """
    name = _clean_display_name(display_name)
    if not name:
        raise ValueError("Le nom de l'annotateur est obligatoire")
    normalized = normalize_orcid(orcid)

    # SQLite ne fait pas un lower() Unicode fiable et une contrainte NOCASE ne
    # couvre pas tous les noms français. La liste est minuscule : comparer en
    # Python avec casefold() donne le bon résultat pour les accents aussi.
    wanted_key = _display_name_key(name)
    existing = next(
        (
            row
            for row in db.scalars(select(Annotator)).all()
            if _display_name_key(row.display_name) == wanted_key
        ),
        None,
    )
    if existing is not None:
        if normalized and not existing.orcid:
            existing.orcid = normalized
            db.flush()
        return existing

    # Si ce nom n'existe que dans une ancienne session, conserver son écriture
    # historique. Ainsi, retaper « Thomas Lamy » ne crée pas une nouvelle
    # variante de l'auteur « thomas lamy » déjà présente dans les données.
    historical_name = next(
        (
            _clean_display_name(value)
            for value in db.scalars(select(CaptureSession.operator)).all()
            if _display_name_key(value) == wanted_key
        ),
        "",
    )
    if historical_name:
        name = historical_name

    row = Annotator(
        id=annotator_id or str(uuid.uuid4()),
        display_name=name,
        orcid=normalized,
    )
    db.add(row)
    db.flush()
    return row


def current_annotator(db: Session) -> Optional[Annotator]:
    """Annotateur retenu, ou None tant qu'aucune identité n'a été choisie.

    Repli utile : si une seule identité existe en base, c'est forcément
    celle-là - on ne va pas ouvrir un dialogue pour un choix unique.
    """
    row = get_annotator(db, get_setting(SETTING_CURRENT, "") or "")
    if row is not None:
        return row
    if count_annotators(db) == 1:
        return db.scalars(select(Annotator).limit(1)).first()
    return None


def set_current_annotator(db: Session, annotator_id: Optional[str]) -> Optional[Annotator]:
    """Mémorise l'annotateur courant (réglage `app_settings.json`)."""
    row = get_annotator(db, annotator_id)
    save_settings({SETTING_CURRENT: row.id if row is not None else ""})
    return row


def current_author_name(db: Session) -> Optional[str]:
    """Valeur à écrire dans `author`, ou None si personne ne s'est identifié.

    None plutôt que `'operator'` : une identité inventée vaut moins qu'une
    absence d'identité assumée.
    """
    row = current_annotator(db)
    return row.display_name if row is not None else None


def needs_identity(db: Session) -> bool:
    """Vrai au tout premier lancement : aucune identité connue."""
    return current_annotator(db) is None


__all__ = [
    "SETTING_CURRENT",
    "annotator_as_dict",
    "count_annotators",
    "create_annotator",
    "current_annotator",
    "current_author_name",
    "get_annotator",
    "list_annotators",
    "list_known_annotator_names",
    "needs_identity",
    "normalize_orcid",
    "set_current_annotator",
]
