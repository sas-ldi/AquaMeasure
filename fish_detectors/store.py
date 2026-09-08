"""Emplacements sur disque du systeme de detecteurs.

Ou vivent les poids
-------------------
Les fichiers de poids pesent de 100 Mo a 400 Mo : ils sont exclus de git
(`.gitignore`, `fish-vision/models/*`). Historiquement ils etaient cherches
dans **le depot courant** et nulle part ailleurs. Consequence vecue : ouvrir
une autre branche dans un second worktree donnait une application ou aucun
modele n'etait installe, alors que le code, lui, etait bien la. Le catalogue
s'affichait vide de tout modele utilisable et il fallait tout retelecharger.

D'ou une recherche sur **plusieurs emplacements**, du plus explicite au plus
implicite (`model_search_dirs`) :

1. ``AQUAMEASURE_MODELS_DIR`` - deroutage explicite, souverain (tests, poste
   partage, disque secondaire) ;
2. ``<racine de donnees configuree>/models`` - la racine choisie en page
   Parametres. C'est **le** dossier partage : il ne depend d'aucun depot, donc
   toutes les copies de travail y trouvent les memes poids ;
3. ``<depot>/fish-vision/models`` - emplacement historique, conserve pour ne
   rien casser sur une installation existante ;
4. ``<dossier courant>/models`` - distribution livree, a cote de l'exe.

Le telechargement ecrit dans `models_dir()`, c'est-a-dire le premier
emplacement de la liste qui existe deja, sinon le premier tout court.

- reglages : `camera_parameters/` (deja la convention de l'app, hors git)
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from app_paths import app_root

CATALOG_FILE = "catalog.json"
USER_CATALOG_FILE = "detectors.local.json"
REMOTE_CATALOG_FILE = "detectors.remote.json"
STATE_FILE = "detectors.state.json"

ENV_MODELS_DIR = "AQUAMEASURE_MODELS_DIR"

MODELS_SUBDIR = "models"


def repo_root() -> Path:
    return Path(app_root())


def _configured_data_root() -> Path | None:
    """Racine des donnees choisie par l'utilisateur, si `fish-vision` repond.

    Import paresseux et tolerant : `fish_detectors` sert aussi en ligne de
    commande, hors application, ou le namespace `src` n'est pas monte. Une
    installation incomplete ne doit jamais empecher la resolution d'un chemin.
    """
    try:
        from src.util import storage_config
    except Exception:  # noqa: BLE001 - repli sur les emplacements historiques
        return None
    try:
        return storage_config.configured_data_root()
    except Exception:  # noqa: BLE001
        return None


def model_search_dirs() -> tuple[Path, ...]:
    """Emplacements ou chercher des poids, du plus prioritaire au moins."""
    candidates: list[Path] = []

    override = os.environ.get(ENV_MODELS_DIR, "").strip()
    if override:
        candidates.append(Path(override).expanduser())

    data_root = _configured_data_root()
    if data_root is not None:
        candidates.append(Path(data_root) / MODELS_SUBDIR)

    candidates.append(repo_root() / "fish-vision" / MODELS_SUBDIR)
    candidates.append(Path.cwd() / MODELS_SUBDIR)

    ordered: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        try:
            key = str(candidate.resolve())
        except OSError:
            key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        ordered.append(candidate)
    return tuple(ordered)


def models_dir() -> Path:
    """Dossier d'ecriture des poids telecharges.

    Le premier emplacement de recherche qui existe deja, faute de quoi le
    premier de la liste - celui-ci sera cree au telechargement.
    """
    dirs = model_search_dirs()
    for candidate in dirs:
        if candidate.is_dir():
            return candidate
    return dirs[0]


def config_dir() -> Path:
    return Path.cwd() / "camera_parameters"


def builtin_catalog_path() -> Path:
    return Path(__file__).resolve().parent / CATALOG_FILE


def user_catalog_path() -> Path:
    return config_dir() / USER_CATALOG_FILE


def remote_catalog_path() -> Path:
    return config_dir() / REMOTE_CATALOG_FILE


def state_path() -> Path:
    return config_dir() / STATE_FILE


def read_json(path: Path) -> Any:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def write_json(path: Path, data: Any) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return True
    except OSError:
        return False
