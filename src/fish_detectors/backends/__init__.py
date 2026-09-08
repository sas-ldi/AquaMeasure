"""Backends de detection livres avec l'application + decouverte des plugins tiers.

Trois facons d'ajouter une architecture de detection :

1. Deposer un fichier .py dans `plugins/detectors/` a la racine du depot
   (ou dans le dossier pointe par AQUAMEASURE_DETECTOR_PLUGINS).
2. Publier un paquet Python declarant un entry point `aquameasure.detectors`.
3. Ajouter un module ici et l'importer dans _load_builtin().

Dans les trois cas, le module appelle `register_backend(MonBackend)`.
"""

from __future__ import annotations

import importlib
import importlib.util
import os
import sys
from pathlib import Path

from fish_detectors.backends.base import (
    DetectorBackend,
    all_backends,
    get_backend,
    register_backend,
)

__all__ = [
    "DetectorBackend",
    "all_backends",
    "get_backend",
    "register_backend",
    "load_all",
    "plugin_errors",
]

_BUILTIN = (
    "fish_detectors.backends.ultralytics_yolo",
    "fish_detectors.backends.onnx_yolo",
    "fish_detectors.backends.rfdetr_detr",
    "fish_detectors.backends.yolov5_hub",
    "fish_detectors.backends.sam3",
)

_ENTRY_POINT_GROUP = "aquameasure.detectors"
_loaded = False
_errors: list[str] = []


def plugin_errors() -> list[str]:
    return list(_errors)


def _load_builtin() -> None:
    for name in _BUILTIN:
        try:
            importlib.import_module(name)
        except Exception as exc:
            _errors.append(f"{name}: {exc}")


def _plugin_dirs(repo_root: Path | None) -> list[Path]:
    dirs: list[Path] = []
    if repo_root is not None:
        dirs.append(repo_root / "plugins" / "detectors")
    env = os.environ.get("AQUAMEASURE_DETECTOR_PLUGINS", "")
    dirs.extend(Path(p) for p in env.split(os.pathsep) if p.strip())
    return dirs


def _load_plugin_dirs(repo_root: Path | None) -> None:
    for directory in _plugin_dirs(repo_root):
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.py")):
            if path.name.startswith("_"):
                continue
            mod_name = f"aquameasure_detector_plugin_{path.stem}"
            try:
                spec = importlib.util.spec_from_file_location(mod_name, path)
                if spec is None or spec.loader is None:
                    continue
                module = importlib.util.module_from_spec(spec)
                sys.modules[mod_name] = module
                spec.loader.exec_module(module)
            except Exception as exc:
                _errors.append(f"{path.name}: {exc}")


def _load_entry_points() -> None:
    try:
        from importlib.metadata import entry_points
    except ImportError:
        return
    try:
        points = entry_points(group=_ENTRY_POINT_GROUP)
    except TypeError:
        points = entry_points().get(_ENTRY_POINT_GROUP, [])
    for point in points:
        try:
            loaded = point.load()
        except Exception as exc:
            _errors.append(f"{point.name}: {exc}")
            continue
        if isinstance(loaded, type) and issubclass(loaded, DetectorBackend):
            register_backend(loaded)
        elif callable(loaded):
            loaded(register_backend)


def load_all(repo_root: Path | None = None) -> dict[str, type[DetectorBackend]]:
    """Charge une fois tous les backends disponibles."""
    global _loaded
    if not _loaded:
        _loaded = True
        _load_builtin()
        _load_plugin_dirs(repo_root)
        _load_entry_points()
    return all_backends()
