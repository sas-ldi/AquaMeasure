"""Façade côté application vers la configuration de stockage.

L'implémentation vit dans `annotations/src/annodb/storage_config.py` : c'est
la couche basse (`connection.get_db_path`) qui doit pouvoir résoudre la racine
des données sans importer Qt ni l'interface. Le sens des dépendances du projet
est `interface` → `annotations`, jamais l'inverse - dupliquer la
lecture du fichier ici ferait deux vérités pour un seul réglage.

Ce module n'ajoute donc rien : il rend l'implémentation importable depuis
l'application (en s'assurant que `annotations` est sur le `sys.path`, comme le
font déjà les contrôleurs avec `_ensure_fv`) et expose la même API.

    from src.util import storage_config
    storage_config.data_root()
"""

from __future__ import annotations

import sys
from pathlib import Path


def _ensure_fish_vision() -> None:
    """Rend `src.annodb` importable même hors du point d'entrée `main.py`."""
    # interface/src/util/storage_config.py -> racine du dépôt
    repo = Path(__file__).resolve().parents[3]
    fv_root = repo / "annotations"
    fv_src = fv_root / "src"
    if fv_root.is_dir() and str(fv_root) not in sys.path:
        sys.path.insert(0, str(fv_root))
    # `main.py` fabrique un espace de noms `src` couvrant les deux arbres ;
    # hors de lui (outils, scripts), on l'élargit à la volée.
    pkg = sys.modules.get("src")
    if pkg is not None and hasattr(pkg, "__path__"):
        if fv_src.is_dir() and str(fv_src) not in list(pkg.__path__):
            pkg.__path__.append(str(fv_src))


_ensure_fish_vision()

from src.annodb.storage_config import (  # noqa: E402  (import après bootstrap)
    APP_DIR_NAME,
    CONFIG_FILE_NAME,
    ENV_CONFIG_PATH,
    KEY_DATA_ROOT,
    KEY_LAST_BACKUP_AT,
    KEY_LAST_BACKUP_PATH,
    StorageRootError,
    annotations_db_path,
    backup_age_days,
    camera_params_dir,
    config_path,
    configured_data_root,
    copy_data_tree,
    copyable_sources,
    data_root,
    default_data_root,
    described_locations,
    directory_size,
    exports_dir,
    human_size,
    invalidate_cache,
    is_default_root,
    last_backup,
    load_config,
    media_dir,
    record_backup,
    save_config,
    set_data_root,
    validate_root,
)

__all__ = [
    "APP_DIR_NAME",
    "CONFIG_FILE_NAME",
    "ENV_CONFIG_PATH",
    "KEY_DATA_ROOT",
    "KEY_LAST_BACKUP_AT",
    "KEY_LAST_BACKUP_PATH",
    "StorageRootError",
    "annotations_db_path",
    "backup_age_days",
    "camera_params_dir",
    "config_path",
    "configured_data_root",
    "copy_data_tree",
    "copyable_sources",
    "data_root",
    "default_data_root",
    "described_locations",
    "directory_size",
    "exports_dir",
    "human_size",
    "invalidate_cache",
    "is_default_root",
    "last_backup",
    "load_config",
    "media_dir",
    "record_backup",
    "save_config",
    "set_data_root",
    "validate_root",
]
