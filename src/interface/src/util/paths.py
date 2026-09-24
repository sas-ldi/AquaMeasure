"""Chemins projet - meme convention que aquameasure.py / projectpaths.cpp.

`app_data_root()` renvoyait `os.getcwd()`. C'était **le** point fragile relevé
par l'audit (`docs/refonte-donnees/02-audit-etat-des-lieux.md`, §chemins) : le
répertoire courant dépend de la façon dont l'application est lancée, donc les
calibrations atterrissaient là où le raccourci avait été cliqué. Un utilisateur
qui lançait `run.bat` depuis un autre dossier repartait de zéro sans
comprendre pourquoi, et rien dans l'interface ne montrait l'emplacement réel.

Désormais : **racine configurée par l'utilisateur** (page Paramètres →
`annotations/src/annodb/storage_config.py`, fichier
`%APPDATA%/AquaMeasure/storage.json`) si elle existe, sinon la racine du dépôt
(`app_root()`). En pratique, sans configuration, le comportement est identique
à l'ancien puisque `main.py` fait `os.chdir(repo)` au démarrage - mais il ne
dépend plus du répertoire courant.
"""

from __future__ import annotations

import sys
from pathlib import Path


def app_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    # interface/src/util/paths.py -> repo test/
    return Path(__file__).resolve().parents[3]


def _storage_config():
    """Configuration de stockage, ou `None` si `annotations` est indisponible.

    Import paresseux et tolérant : la résolution d'un chemin ne doit jamais
    empêcher l'application de démarrer, même sur une installation incomplète.
    """
    try:
        from src.util import storage_config

        return storage_config
    except Exception:  # noqa: BLE001 - repli sur l'historique, jamais d'arrêt
        return None


def app_data_root() -> Path:
    """Racine des données : celle choisie par l'utilisateur, sinon le dépôt."""
    cfg = _storage_config()
    if cfg is not None:
        configured = cfg.configured_data_root()
        if configured is not None:
            return configured
    return app_root()


def bind_engine_root() -> None:
    """Fait travailler le moteur (aquameasure.py) dans la racine des donnees.

    Il fige sa racine a l'import (dossier de l'exe) sans lire storage.json :
    une fois installe, il ecrivait la calibration a cote du programme et
    lisait la fenetre In/Out la-bas, pendant que l'appli regardait Documents.
    A appeler avant chaque usage : la racine peut changer dans Parametres.
    """
    import aquameasure

    aquameasure._APP_ROOT = str(app_data_root())


def camera_params_dir() -> Path:
    return app_data_root() / "camera_parameters"


def exports_dir() -> Path:
    """Racine des exports - `<racine des données>/data/exports`.

    Centralisée ici pour que les quatre contrôleurs qui l'écrivaient en dur
    (`données`, `explorateur`, `outils pro`, `sessions`) suivent la racine
    configurée sans diverger.
    """
    return app_data_root() / "data" / "exports"


def media_dir() -> Path:
    """Images matérialisées et vignettes - `<racine des données>/data/media`."""
    return app_data_root() / "data" / "media"


def annotations_db_path() -> Path:
    """Fichier de la base d'annotations réellement utilisé."""
    cfg = _storage_config()
    if cfg is not None:
        return cfg.annotations_db_path()
    return app_root() / "annotations" / "data" / "fish_annotations.db"


# Une seule calibration, un seul dossier : camera_parameters/.
#
# Il y avait auparavant deux emplacements - la racine (moteur « Classique » et
# import ZIP) et camera_parameters/profiles/fast_v2/ (moteur « Rapide v2 ») -
# departages par active_profile.txt. Consequence vecue : une calibration
# importee en ZIP atterrissait a la racine pendant que la mesure continuait de
# lire le profil fast_v2. Deux calibrations pour la meme paire de videos, et
# aucun moyen de savoir laquelle servait.
#
# `profile` reste accepte par les fonctions ci-dessous pour ne pas casser les
# appels existants, mais il est ignore.

CALIB_REQUIRED_FILES = (
    "mtx1.npy",
    "dist1.npy",
    "mtx2.npy",
    "dist2.npy",
    "R.npy",
    "T.npy",
)

LEGACY_PROFILE_DIRNAME = "profiles"


def cam_param(name: str) -> Path:
    return camera_params_dir() / name


def calib_profile_dir(profile: str | None = None) -> Path:  # noqa: ARG001
    return camera_params_dir()


def calib_param(name: str, profile: str | None = None) -> Path:  # noqa: ARG001
    return camera_params_dir() / name


def calib_profile_complete(profile: str | None = None) -> bool:  # noqa: ARG001
    d = camera_params_dir()
    return all((d / name).is_file() for name in CALIB_REQUIRED_FILES)


def _dir_has_calibration(d: Path) -> bool:
    return d.is_dir() and all((d / name).is_file() for name in CALIB_REQUIRED_FILES)


def legacy_profile_dirs() -> list[Path]:
    """Anciens dossiers camera_parameters/profiles/<nom>/ encore presents."""
    base = camera_params_dir() / LEGACY_PROFILE_DIRNAME
    if not base.is_dir():
        return []
    return sorted(d for d in base.iterdir() if _dir_has_calibration(d))


def migrate_legacy_calib_profile() -> str:
    """Remonte une calibration coincee dans profiles/ vers camera_parameters/.

    Ne remonte que si la racine n'a pas deja une calibration complete : on ne
    remplace jamais silencieusement celle que l'utilisateur voit. Rien n'est
    supprime - les anciens dossiers restent en place, simplement inutilises.

    Renvoie une ligne a journaliser, ou une chaine vide s'il n'y a rien a dire.
    """
    import shutil

    legacy = legacy_profile_dirs()
    if not legacy:
        return ""
    base = camera_params_dir()
    if calib_profile_complete():
        names = ", ".join(d.name for d in legacy)
        return (
            f"[!] Anciens profils de calibration ignores ({names}) : "
            f"la mesure utilise desormais {base}"
        )
    source = max(legacy, key=lambda d: (d / "stereo_rmse.npy").stat().st_mtime
                 if (d / "stereo_rmse.npy").is_file() else 0)
    base.mkdir(parents=True, exist_ok=True)
    copied = 0
    for item in source.iterdir():
        if item.is_file() and item.suffix in (".npy", ".txt", ".json"):
            try:
                shutil.copy2(item, base / item.name)
                copied += 1
            except OSError:
                pass
    if not copied:
        return ""
    return (
        f"Calibration reprise depuis l'ancien profil « {source.name} » "
        f"vers {base} ({copied} fichiers)"
    )


def ensure_camera_params_dir() -> None:
    camera_params_dir().mkdir(parents=True, exist_ok=True)


def calib_logs_dir() -> Path:
    return camera_params_dir() / "calib_logs"


def sync_exists() -> bool:
    return cam_param("sync_frames.npy").is_file()


def calibration_exists() -> bool:
    return calib_profile_complete()


def stereo_rmse_if_exists(profile: str | None = None) -> float:
    return npy_scalar_if_exists("stereo_rmse.npy", profile=profile)


def npy_scalar_if_exists(name: str, profile: str | None = None) -> float:
    path = calib_param(name, profile)
    if not path.is_file():
        return -1.0
    try:
        import numpy as np

        arr = np.load(path)
        flat = np.asarray(arr).ravel()
        return float(flat[0]) if flat.size else -1.0
    except OSError:
        return -1.0


def calibration_origin(profile: str | None = None) -> dict:
    """Provenance de la calibration utilisée : dossier, date, origine.

    Deux calibrations pour la même paire de vidéos étaient impossibles à
    distinguer : rien ne disait laquelle la mesure chargeait vraiment. On
    publie donc, en clair : où elle est, quand elle a été faite, et si elle
    vient d'un calcul local ou d'une archive importée.

    La date est celle de stereo_rmse.npy, écrit en fin de calibration. Une
    archive importée conserve les dates d'origine (copy2) : c'est bien la date
    de la calibration, pas celle de l'import.
    """
    from datetime import datetime

    base = calib_profile_dir(profile)
    result = {
        "dir": str(base),
        "complete": calib_profile_complete(profile),
        "date": "",
        "source": "",
        "rmse": stereo_rmse_if_exists(profile),
    }
    stamp = base / "stereo_rmse.npy"
    if not stamp.is_file():
        stamp = base / "R.npy"
    if stamp.is_file():
        try:
            result["date"] = datetime.fromtimestamp(stamp.stat().st_mtime).strftime(
                "%d/%m/%Y %H:%M"
            )
        except OSError:
            pass

    manifest = base / "manifest.json"
    if manifest.is_file():
        result["source"] = "archive importée"
        try:
            import json

            exported = json.loads(manifest.read_text(encoding="utf-8")).get(
                "exported_at", ""
            )
            if exported:
                result["exported_at"] = exported
        except (OSError, ValueError):
            pass
    elif result["complete"]:
        result["source"] = "calcul local"
    return result


def calibration_summary(profile: str | None = None) -> str:
    """Une ligne lisible : « 19/08/2026 19:03 · calcul local · RMSE 0.54 px »."""
    info = calibration_origin(profile)
    if not info["complete"]:
        return ""
    parts = [info["date"] or "date inconnue"]
    if info["source"]:
        parts.append(info["source"])
    if info["rmse"] >= 0:
        parts.append(f"RMSE {info['rmse']:.2f} px")
    return " · ".join(parts)


def load_calib_meta(profile: str | None = None) -> dict:
    path = calib_param("calib_meta.json", profile)
    if not path.is_file():
        return {}
    try:
        import json

        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
