"""Outils communs aux tests : racine du paquet, base jetable, calibration factice.

Aucun test ne doit toucher `src/annotations/data/fish_annotations.db` : tout passe
par une base temporaire pointée par `FISH_VISION_DB`.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

FV_ROOT = (Path(__file__).resolve().parents[2] / "src" / "annotations")
if str(FV_ROOT) not in sys.path:
    sys.path.insert(0, str(FV_ROOT))


class TempDirCase(unittest.TestCase):
    """Répertoire de travail jetable, nettoyé en fin de test."""

    def setUp(self) -> None:
        super().setUp()
        self._tmp = tempfile.TemporaryDirectory(prefix="fishvision_test_")
        self.tmp_path = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        # Aucun test ne doit lire — ni écrire — la configuration de stockage
        # réelle de l'utilisateur (%APPDATA%/AquaMeasure/storage.json) : elle
        # déplacerait la racine des données sous les pieds des tests.
        self.storage_config_path = self.tmp_path / "storage.json"
        set_storage_config_env(self, self.storage_config_path)


class TempDbCase(TempDirCase):
    """Base SQLite jetable + `FISH_VISION_DB` positionné sur elle."""

    def setUp(self) -> None:
        super().setUp()
        self.db_path = self.tmp_path / "test_annotations.db"
        previous = os.environ.get("FISH_VISION_DB")
        os.environ["FISH_VISION_DB"] = str(self.db_path)
        self.addCleanup(self._restore_env, "FISH_VISION_DB", previous)
        # Enregistré après le nettoyage du répertoire, donc exécuté avant lui :
        # sous Windows, un moteur encore ouvert empêche la suppression.
        self.addCleanup(dispose_engine)

    @staticmethod
    def _restore_env(name: str, value: str | None) -> None:
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value


def dispose_engine() -> None:
    """Ferme le moteur SQLAlchemy global et oublie les migrations appliquées."""
    from src.annodb import connection

    if connection._engine is not None:
        connection._engine.dispose()
        connection._engine = None
        connection._SessionLocal = None
    connection._migrated_paths.clear()


def set_storage_config_env(case: unittest.TestCase, path: Path | None) -> None:
    """Déroute `storage.json` vers un fichier jetable (et vide le cache)."""
    from src.annodb import storage_config

    name = storage_config.ENV_CONFIG_PATH
    previous = os.environ.get(name)

    def restore() -> None:
        if previous is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = previous
        storage_config.invalidate_cache()

    case.addCleanup(restore)
    if path is None:
        os.environ.pop(name, None)
    else:
        os.environ[name] = str(path)
    storage_config.invalidate_cache()


def set_camera_params_env(case: unittest.TestCase, directory: Path | None) -> None:
    """Force le répertoire `camera_parameters` vu par le code sous test."""
    from src.annodb.rectify import ENV_CAMERA_PARAMS

    previous = os.environ.get(ENV_CAMERA_PARAMS)

    def restore() -> None:
        if previous is None:
            os.environ.pop(ENV_CAMERA_PARAMS, None)
        else:
            os.environ[ENV_CAMERA_PARAMS] = previous

    case.addCleanup(restore)
    if directory is None:
        os.environ.pop(ENV_CAMERA_PARAMS, None)
    else:
        os.environ[ENV_CAMERA_PARAMS] = str(directory)


def write_sync_frames(directory: Path, left: int, right: int) -> Path:
    """Écrit `sync_frames.npy` comme le fait l'onglet Synchronisation."""
    import numpy as np

    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "sync_frames.npy"
    np.save(path, np.array([left, right], dtype=np.int64))
    return path


def write_fake_calibration(
    directory: Path,
    *,
    width: int = 64,
    height: int = 48,
    baseline_mm: float = 60.0,
    profile: str | None = None,
) -> Path:
    """Calibration factice : deux caméras identiques, alignées, sans distorsion.

    Un banc parfaitement aligné rend la rectification quasi neutre : c'est ce
    qui rend le test vérifiable sans image de référence.
    """
    import numpy as np

    directory.mkdir(parents=True, exist_ok=True)
    target = directory
    if profile:
        (directory / "active_profile.txt").write_text(profile, encoding="utf-8")
        if profile != "classic":
            target = directory / "profiles" / profile
            target.mkdir(parents=True, exist_ok=True)

    fx = fy = float(max(width, height))
    mtx = np.array(
        [[fx, 0.0, width / 2.0], [0.0, fy, height / 2.0], [0.0, 0.0, 1.0]],
        dtype=np.float64,
    )
    dist = np.zeros((1, 5), dtype=np.float64)
    np.save(target / "mtx1.npy", mtx)
    np.save(target / "mtx2.npy", mtx.copy())
    np.save(target / "dist1.npy", dist)
    np.save(target / "dist2.npy", dist.copy())
    np.save(target / "R.npy", np.eye(3, dtype=np.float64))
    np.save(target / "T.npy", np.array([[-baseline_mm], [0.0], [0.0]], dtype=np.float64))
    return target


def write_test_image(path: Path, width: int = 64, height: int = 48):
    """Image BGR déterministe, écrite sur disque."""
    import cv2
    import numpy as np

    img = np.zeros((height, width, 3), dtype=np.uint8)
    img[:, :, 0] = np.linspace(0, 255, width, dtype=np.uint8)[None, :]
    img[:, :, 1] = np.linspace(0, 255, height, dtype=np.uint8)[:, None]
    img[height // 3: 2 * height // 3, width // 3: 2 * width // 3] = (255, 255, 255)
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), img)
    return img
