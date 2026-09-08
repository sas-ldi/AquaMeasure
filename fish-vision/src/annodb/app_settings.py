"""Application settings persisted as JSON."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

_PKG_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_PATH = _PKG_ROOT / "data" / "app_settings.json"

# Même rôle que FISH_VISION_DB pour la base : permet aux tests (et à toute
# exécution hors application) d'isoler les réglages du fichier de l'utilisateur.
ENV_SETTINGS_PATH = "FISH_VISION_SETTINGS"

_DEFAULTS: dict[str, Any] = {
    "fishial_min_refs": 5,
    "gallery_similarity_threshold": 0.55,
    # Dernier annotateur utilisé (annotators.id) - évite de redemander
    # l'identité à chaque démarrage.
    "current_annotator_id": "",
}


def settings_path() -> Path:
    env = os.environ.get(ENV_SETTINGS_PATH)
    if env:
        return Path(env)
    return _DEFAULT_PATH


def load_settings() -> dict[str, Any]:
    path = settings_path()
    if not path.is_file():
        return dict(_DEFAULTS)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return dict(_DEFAULTS)
    out = dict(_DEFAULTS)
    out.update({k: v for k, v in data.items() if k in _DEFAULTS})
    return out


def save_settings(updates: dict[str, Any]) -> dict[str, Any]:
    current = load_settings()
    for key, value in updates.items():
        if key in _DEFAULTS:
            current[key] = value
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(current, indent=2) + "\n", encoding="utf-8")
    return current


def get_setting(key: str, default: Any = None) -> Any:
    return load_settings().get(key, default)
