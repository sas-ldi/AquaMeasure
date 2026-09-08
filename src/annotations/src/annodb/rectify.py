"""Rectification stéréo gauche et provenance de calibration.

Les boîtes annotées dans l'application sont tracées sur l'image **stéréo-rectifiée
gauche** (`interface/src/backend/measure_service.py` →
`get_rectified_pair`, `cv2.remap`). Exporter la frame brute décale donc les
boîtes de quelques dizaines à plus de cent pixels sans qu'aucune dimension
n'alerte (même 1920×1080).

Ce module recharge le profil de calibration actif - mêmes fichiers et mêmes
paramètres (`stereoRectify(alpha=0)`) que l'application - et fournit :

- une transformation `frame brute → frame rectifiée gauche` réutilisable
  (`LeftRectifier`, appelable, sûre à passer à `materialize_frame_image`) ;
- les métadonnées de provenance à inscrire dans l'export : nom du profil,
  sha256 du jeu de `.npy`, taille d'image, espace image déclaré.

Convention de répertoire (identique à `interface/src/util/paths.py`) :
`camera_parameters/` à la racine du dépôt, `active_profile.txt` désignant le
profil, profil `classic` = racine, tout autre profil = `profiles/<nom>/`.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

# Espaces image possibles pour une géométrie ou une image exportée.
IMAGE_SPACE_RAW = "raw"
IMAGE_SPACE_RECTIFIED_LEFT = "stereo_rectified_left"

# Profil « classique » : les .npy vivent directement dans camera_parameters/.
CALIB_PROFILE_CLASSIC = "classic"

# alpha de cv2.stereoRectify, identique dans toute l'application (voir
# _build_maps) : inscrit tel quel dans la table `calibrations`.
RECTIFY_ALPHA = 0.0

# Jeu minimal nécessaire à la rectification, dans un ordre fixe (le sha256 en
# dépend : deux profils identiques doivent donner le même condensé).
CALIB_FILES: Tuple[str, ...] = (
    "mtx1.npy", "dist1.npy", "mtx2.npy", "dist2.npy", "R.npy", "T.npy",
)

# Variable d'environnement de secours : utile aux tests et à toute exécution
# hors du dossier de l'application (scripts, batch).
ENV_CAMERA_PARAMS = "AQUAMEASURE_CAMERA_PARAMS"

# Le dépôt : annotations/src/annodb/rectify.py -> racine du dépôt.
_REPO_ROOT = Path(__file__).resolve().parents[3]

_MAPS_CACHE: Dict[tuple, Any] = {}


def camera_params_candidates() -> list[Path]:
    """Emplacements testés pour `camera_parameters`, du plus explicite au moins.

    Ordre : variable d'environnement, puis **racine des données configurée**
    (page Paramètres), puis répertoire courant, puis racine du dépôt. La
    racine configurée passe avant le répertoire courant parce qu'elle est un
    choix explicite de l'utilisateur, là où le `cwd` n'est qu'un accident de
    lancement.
    """
    out: list[Path] = []
    env = os.environ.get(ENV_CAMERA_PARAMS)
    if env:
        out.append(Path(env))
    from .storage_config import configured_data_root

    configured = configured_data_root()
    if configured is not None:
        out.append(configured / "camera_parameters")
    out.append(Path.cwd() / "camera_parameters")
    out.append(_REPO_ROOT / "camera_parameters")
    return out


def camera_params_dir(explicit: Optional[Path] = None) -> Optional[Path]:
    """Répertoire `camera_parameters` utilisable, ou None s'il n'en existe pas."""
    if explicit is not None:
        p = Path(explicit)
        return p if p.is_dir() else None
    for cand in camera_params_candidates():
        if cand.is_dir():
            return cand
    return None


def active_profile_name(base: Optional[Path] = None) -> str:
    """Nom du profil de calibration actif (`active_profile.txt`)."""
    base = camera_params_dir(base)
    if base is None:
        return CALIB_PROFILE_CLASSIC
    path = base / "active_profile.txt"
    if not path.is_file():
        return CALIB_PROFILE_CLASSIC
    try:
        name = path.read_text(encoding="utf-8").strip()
    except OSError:
        return CALIB_PROFILE_CLASSIC
    return name or CALIB_PROFILE_CLASSIC


def profile_dir(base: Path, profile: str) -> Path:
    """Répertoire du profil - `classic` vit à la racine, les autres dans profiles/."""
    if not profile or profile == CALIB_PROFILE_CLASSIC:
        return Path(base)
    return Path(base) / "profiles" / profile


def _sha256_of_files(directory: Path) -> tuple[str, Dict[str, str]]:
    """Condensé du jeu de calibration + condensé par fichier.

    Le nom de fichier est intégré au flux : deux profils qui échangeraient
    `mtx1`/`mtx2` ne doivent pas produire le même condensé.
    """
    whole = hashlib.sha256()
    per_file: Dict[str, str] = {}
    for name in CALIB_FILES:
        data = (directory / name).read_bytes()
        whole.update(name.encode("utf-8"))
        whole.update(data)
        per_file[name] = hashlib.sha256(data).hexdigest()
    return whole.hexdigest(), per_file


@dataclass
class CalibrationProfile:
    """Profil de calibration chargé depuis le disque."""

    name: str
    directory: Path
    sha256: str
    file_sha256: Dict[str, str] = field(default_factory=dict)

    def matrices(self) -> Dict[str, Any]:
        import numpy as np

        return {
            "mtx1": np.load(self.directory / "mtx1.npy"),
            "dist1": np.load(self.directory / "dist1.npy"),
            "mtx2": np.load(self.directory / "mtx2.npy"),
            "dist2": np.load(self.directory / "dist2.npy"),
            "R": np.load(self.directory / "R.npy"),
            "T": np.load(self.directory / "T.npy"),
        }

    def metadata(self) -> Dict[str, Any]:
        return {
            "profile_name": self.name,
            "calibration_sha256": self.sha256,
            "calibration_files_sha256": dict(self.file_sha256),
            "calibration_dir": str(self.directory),
        }

    def baseline_mm(self) -> Optional[float]:
        """Écartement des deux caméras, norme du vecteur de translation T."""
        try:
            import numpy as np

            t = np.load(self.directory / "T.npy")
            return float(np.linalg.norm(np.asarray(t, dtype=float).ravel()))
        except Exception:  # noqa: BLE001 - profil illisible : pas de baseline
            return None

    def stereo_rmse(self) -> Optional[float]:
        """Erreur de reprojection de la calibration stéréo, si elle a été écrite.

        Même fichier et même convention de profil que
        `interface/src/util/paths.py::stereo_rmse_if_exists` - relu ici
        pour que `annotations` reste utilisable sans l'application Qt.
        """
        path = self.directory / "stereo_rmse.npy"
        if not path.is_file():
            return None
        try:
            import numpy as np

            flat = np.asarray(np.load(path)).ravel()
            return float(flat[0]) if flat.size else None
        except Exception:  # noqa: BLE001
            return None

    def summary(self) -> Dict[str, Any]:
        """Fiche d'identité pour la table `calibrations` (phase 1)."""
        return {
            "profile_name": self.name,
            "calibration_sha256": self.sha256,
            "alpha": RECTIFY_ALPHA,
            "baseline_mm": self.baseline_mm(),
            "stereo_rmse": self.stereo_rmse(),
        }


def profile_complete(directory: Path) -> bool:
    return all((Path(directory) / name).is_file() for name in CALIB_FILES)


def load_calibration_profile(
    base: Optional[Path] = None,
    profile: Optional[str] = None,
) -> Optional[CalibrationProfile]:
    """Charge le profil demandé (ou l'actif) ; None si la calibration manque."""
    root = camera_params_dir(base)
    if root is None:
        return None
    name = profile or active_profile_name(root)
    directory = profile_dir(root, name)
    if not profile_complete(directory):
        return None
    try:
        sha, per_file = _sha256_of_files(directory)
    except OSError:
        return None
    return CalibrationProfile(
        name=name, directory=directory, sha256=sha, file_sha256=per_file,
    )


def _build_maps(calib: CalibrationProfile, image_size: Tuple[int, int]):
    """stereoRectify + initUndistortRectifyMap - mêmes réglages que l'app."""
    key = (str(calib.directory), calib.sha256, int(image_size[0]), int(image_size[1]))
    cached = _MAPS_CACHE.get(key)
    if cached is not None:
        return cached
    import cv2

    m = calib.matrices()
    w, h = int(image_size[0]), int(image_size[1])
    # alpha=0 : identique à aquameasure._ensure_stereo_rect_cache et à
    # stereo_utils.ensure_stereo_rect_cache. Toute divergence ici décalerait
    # l'export par rapport à ce que l'opérateur a vu à l'écran.
    R1, _R2, P1, _P2, _Q, _roi1, _roi2 = cv2.stereoRectify(
        m["mtx1"], m["dist1"], m["mtx2"], m["dist2"], (w, h), m["R"], m["T"],
        alpha=0,
    )
    map_x, map_y = cv2.initUndistortRectifyMap(
        m["mtx1"], m["dist1"], R1, P1, (w, h), cv2.CV_32FC1,
    )
    _MAPS_CACHE[key] = (map_x, map_y)
    return map_x, map_y


class LeftRectifier:
    """Transformation `frame brute gauche → frame stéréo-rectifiée gauche`.

    Appelable comme une fonction (`rectifier(bgr) -> bgr`) pour être passée
    telle quelle à `materialize_frame_image`. Les cartes de remap sont
    construites à la demande **par taille d'image** : un export peut mélanger
    des médias de résolutions différentes.
    """

    image_space = IMAGE_SPACE_RECTIFIED_LEFT

    def __init__(self, calib: CalibrationProfile):
        self._calib = calib
        self._sizes: set[Tuple[int, int]] = set()

    @property
    def profile_name(self) -> str:
        return self._calib.name

    @property
    def calibration_sha256(self) -> str:
        return self._calib.sha256

    @property
    def image_sizes(self) -> list[Tuple[int, int]]:
        return sorted(self._sizes)

    def __call__(self, bgr):
        import cv2

        h, w = bgr.shape[:2]
        map_x, map_y = _build_maps(self._calib, (w, h))
        self._sizes.add((w, h))
        return cv2.remap(bgr, map_x, map_y, cv2.INTER_LINEAR)

    def metadata(self) -> Dict[str, Any]:
        meta = self._calib.metadata()
        meta.update({
            "image_space": self.image_space,
            "image_sizes": [list(s) for s in self.image_sizes],
        })
        return meta


def load_left_rectifier(
    base: Optional[Path] = None,
    profile: Optional[str] = None,
) -> Optional[LeftRectifier]:
    """Rectifieur gauche du profil actif, ou None si rien n'est exploitable.

    None signifie « pas de calibration » : l'appelant doit alors exporter la
    frame brute **et le déclarer** (`image_space='raw'`), jamais laisser croire
    que l'image est rectifiée.
    """
    calib = load_calibration_profile(base, profile)
    if calib is None:
        return None
    try:
        import cv2  # noqa: F401
    except ImportError:
        return None
    return LeftRectifier(calib)


def raw_metadata(reason: str = "calibration_absente") -> Dict[str, Any]:
    """Métadonnées à écrire quand aucune rectification n'est possible."""
    return {
        "image_space": IMAGE_SPACE_RAW,
        "profile_name": None,
        "calibration_sha256": None,
        "raw_reason": reason,
    }


def transform_metadata(transform: Any) -> Dict[str, Any]:
    """Métadonnées de la transformation appliquée (rectifieur ou None)."""
    if transform is None:
        return raw_metadata()
    meta = getattr(transform, "metadata", None)
    if callable(meta):
        return dict(meta())
    return {
        "image_space": getattr(transform, "image_space", "transformed"),
        "profile_name": None,
        "calibration_sha256": None,
    }
