"""Fusion des sources de catalogue de modeles.

Trois couches, la derniere gagne en cas d'id identique :

1. `fish_detectors/catalog.json` - livre avec l'application
2. `camera_parameters/detectors.remote.json` - recu d'une URL de mise a jour
3. `camera_parameters/detectors.local.json` - ajouts de l'utilisateur

Consequence : de nouveaux modeles peuvent arriver chez un client deja installe
sans nouvelle version du logiciel, et l'utilisateur peut toujours pointer ses
propres poids en local.
"""

from __future__ import annotations

import urllib.request
from typing import Any

from fish_detectors import store
from fish_detectors.spec import DetectorSpec

_USER_AGENT = "AquaMeasure/1.0 (+https://www.ird.fr)"


def _parse(data: Any, source: str) -> list[DetectorSpec]:
    if not isinstance(data, dict):
        return []
    specs: list[DetectorSpec] = []
    for entry in data.get("detectors") or []:
        if not isinstance(entry, dict):
            continue
        try:
            payload = dict(entry)
            payload["source"] = source
            specs.append(DetectorSpec.from_dict(payload))
        except (ValueError, TypeError):
            continue
    return specs


def load_specs() -> list[DetectorSpec]:
    merged: dict[str, DetectorSpec] = {}
    layers = (
        (store.builtin_catalog_path(), "builtin"),
        (store.remote_catalog_path(), "remote"),
        (store.user_catalog_path(), "user"),
    )
    for path, source in layers:
        for spec in _parse(store.read_json(path), source):
            merged[spec.id] = spec
    return list(merged.values())


def catalog_version() -> int:
    for path in (store.remote_catalog_path(), store.builtin_catalog_path()):
        data = store.read_json(path)
        if isinstance(data, dict) and data.get("catalog_version"):
            return int(data["catalog_version"])
    return 0


def fetch_remote(url: str, *, timeout: int = 20) -> tuple[int, str]:
    """Telecharge un catalogue distant. Retourne (nb modeles, message)."""
    if not url:
        return 0, "Aucune URL de catalogue configuree"
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = response.read().decode("utf-8")
    except Exception as exc:
        return 0, f"Mise a jour impossible : {exc}"

    import json

    try:
        data = json.loads(payload)
    except ValueError as exc:
        return 0, f"Catalogue distant illisible : {exc}"

    specs = _parse(data, "remote")
    if not specs:
        return 0, "Catalogue distant vide ou invalide - rien applique"
    if not store.write_json(store.remote_catalog_path(), data):
        return 0, "Ecriture du catalogue impossible"
    return len(specs), f"Catalogue mis a jour - {len(specs)} modele(s) disponibles"


def _user_payload() -> dict[str, Any]:
    data = store.read_json(store.user_catalog_path())
    if not isinstance(data, dict):
        return {"detectors": []}
    data.setdefault("detectors", [])
    return data


def add_user_spec(entry: dict[str, Any]) -> DetectorSpec:
    """Ajoute ou remplace un modele dans le catalogue utilisateur."""
    spec = DetectorSpec.from_dict({**entry, "source": "user"})
    data = _user_payload()
    others = [e for e in data["detectors"] if isinstance(e, dict) and e.get("id") != spec.id]
    payload = spec.to_dict()
    payload.pop("source", None)
    data["detectors"] = [*others, payload]
    if not store.write_json(store.user_catalog_path(), data):
        raise OSError("Ecriture du catalogue utilisateur impossible")
    return spec


def remove_user_spec(detector_id: str) -> bool:
    data = _user_payload()
    remaining = [e for e in data["detectors"] if isinstance(e, dict) and e.get("id") != detector_id]
    if len(remaining) == len(data["detectors"]):
        return False
    data["detectors"] = remaining
    return store.write_json(store.user_catalog_path(), data)
