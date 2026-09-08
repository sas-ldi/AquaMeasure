"""Racine des données choisie par l'utilisateur - « où c'est enregistré ».

Pourquoi ce module existe
-------------------------
Jusqu'ici, l'endroit où l'application écrivait dépendait du **répertoire
courant** (`aquameasure-pyside/src/util/paths.py::app_data_root` renvoyait
`os.getcwd()`). Lancer l'application depuis un autre dossier déplaçait
silencieusement `camera_parameters/` : les calibrations atterrissaient là où le
raccourci avait été cliqué, et l'utilisateur ne pouvait ni le voir ni le
choisir (audit `docs/refonte-donnees/02-audit-etat-des-lieux.md`, §chemins).

Ce module porte donc **un** réglage : la racine des données. Il est écrit en
pur `stdlib`, sans Qt et sans dépendance à `aquameasure-pyside`, pour que la
couche base de données (`connection.get_db_path`) puisse le consulter sans
tirer l'interface - le sens des dépendances du projet est
`aquameasure-pyside` → `fish-vision`, jamais l'inverse.

Où vit le fichier de configuration
----------------------------------
`%APPDATA%/AquaMeasure/storage.json` sous Windows, `~/.config/AquaMeasure/
storage.json` ailleurs. **Jamais dans le dépôt** et **jamais relatif au
répertoire courant** : c'est précisément le piège qu'on répare. La variable
d'environnement `AQUAMEASURE_STORAGE_CONFIG` le déroute (tests, exécutions
isolées).

Ce que porte la racine choisie
------------------------------
    <racine>/fish_annotations.db     base d'annotations
    <racine>/camera_parameters/      calibrations et synchronisation
    <racine>/data/exports/           datasets et CSV exportés
    <racine>/data/media/             images matérialisées, vignettes

**Sans configuration, rien ne bouge** : on retombe exactement sur les
emplacements historiques (`fish-vision/data/fish_annotations.db`,
`<dépôt>/camera_parameters`, `<dépôt>/data/exports`, `<dépôt>/data/media`).

Priorités
---------
Pour la base : `FISH_VISION_DB` > configuration > défaut. La variable
d'environnement reste souveraine - c'est elle qui protège la vraie base
pendant les tests.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any, Callable, Dict, Optional

# fish-vision/src/annodb/storage_config.py -> fish-vision/
_PKG_ROOT = Path(__file__).resolve().parents[2]
# -> racine du dépôt (test/ ou test-dev/)
_REPO_ROOT = _PKG_ROOT.parent

# Nom du dossier de configuration, commun à toutes les plateformes.
APP_DIR_NAME = "AquaMeasure"
CONFIG_FILE_NAME = "storage.json"

# Déroutage du fichier de configuration - même rôle que `FISH_VISION_DB` pour
# la base : aucun test ne doit lire ni écrire la configuration réelle de
# l'utilisateur.
ENV_CONFIG_PATH = "AQUAMEASURE_STORAGE_CONFIG"

KEY_DATA_ROOT = "data_root"
KEY_LAST_BACKUP_AT = "last_backup_at"
KEY_LAST_BACKUP_PATH = "last_backup_path"

_KNOWN_KEYS = (KEY_DATA_ROOT, KEY_LAST_BACKUP_AT, KEY_LAST_BACKUP_PATH)

# Sous-chemins, relatifs à la racine choisie.
DB_FILE_NAME = "fish_annotations.db"
CAMERA_PARAMS_SUBDIR = "camera_parameters"
EXPORTS_SUBDIR = Path("data") / "exports"
MEDIA_SUBDIR = Path("data") / "media"

# Cache mémoire : `app_data_root()` est appelé à chaque accès à un fichier de
# calibration ; relire le JSON à chaque fois serait absurde. La clé porte la
# signature du fichier (chemin, mtime, taille) : une édition extérieure est
# donc reprise sans redémarrer.
_cache_key: Optional[tuple] = None
_cache_value: Dict[str, Any] = {}


def config_dir() -> Path:
    """Dossier de configuration par UTILISATEUR (jamais dans le dépôt)."""
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / APP_DIR_NAME
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / APP_DIR_NAME
    return Path.home() / ".config" / APP_DIR_NAME


def config_path() -> Path:
    env = os.environ.get(ENV_CONFIG_PATH)
    if env:
        return Path(env)
    return config_dir() / CONFIG_FILE_NAME


def _signature(path: Path) -> tuple:
    try:
        st = path.stat()
        return (str(path), st.st_mtime_ns, st.st_size)
    except OSError:
        return (str(path), None, None)


def load_config() -> Dict[str, Any]:
    """Configuration lue sur disque - dictionnaire vide si aucune n'existe.

    Un fichier illisible ou corrompu **n'empêche jamais l'application de
    démarrer** : on retombe sur le comportement par défaut, qui est
    l'historique.
    """
    global _cache_key, _cache_value
    path = config_path()
    key = _signature(path)
    if key == _cache_key:
        return dict(_cache_value)
    data: Dict[str, Any] = {}
    if path.is_file():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                data = {k: v for k, v in raw.items() if k in _KNOWN_KEYS}
        except (OSError, ValueError):
            data = {}
    _cache_key = key
    _cache_value = data
    return dict(data)


def save_config(updates: Dict[str, Any]) -> Dict[str, Any]:
    """Écrit les clés fournies, conserve les autres, renvoie l'état complet."""
    global _cache_key
    current = load_config()
    for key, value in updates.items():
        if key not in _KNOWN_KEYS:
            continue
        if value is None:
            current.pop(key, None)
        else:
            current[key] = value
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(current, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    _cache_key = None  # la signature vient de changer : on relira
    return current


def invalidate_cache() -> None:
    """Force la relecture du fichier au prochain accès (tests, changement externe)."""
    global _cache_key, _cache_value
    _cache_key = None
    _cache_value = {}


# ── Racine des données ────────────────────────────────────────────────────


def default_data_root() -> Path:
    """Racine historique : la racine du dépôt.

    C'est ce que `os.getcwd()` valait en pratique, puisque `main.py` fait
    `os.chdir(repo)` au démarrage - mais sans dépendre du répertoire courant.
    Dans la distribution Windows portable, le dépôt est remplacé par le
    dossier qui contient ``AquaMeasure.exe`` : les données ne doivent jamais
    être écrites dans le répertoire temporaire interne de PyInstaller.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return _REPO_ROOT


def configured_data_root() -> Optional[Path]:
    """Racine choisie par l'utilisateur, ou `None` si aucune ne l'est."""
    value = load_config().get(KEY_DATA_ROOT)
    if not value:
        return None
    try:
        return Path(str(value))
    except (TypeError, ValueError):
        return None


def data_root() -> Path:
    root = configured_data_root()
    return root if root is not None else default_data_root()


def is_default_root() -> bool:
    return configured_data_root() is None


def set_data_root(root: Optional[Path]) -> Dict[str, Any]:
    """Fixe (ou efface, avec `None`) la racine des données."""
    return save_config({KEY_DATA_ROOT: None if root is None else str(Path(root))})


# ── Emplacements dérivés ──────────────────────────────────────────────────
#
# Chaque fonction a la même forme : racine configurée -> sous-chemin de la
# racine ; pas de configuration -> emplacement historique, au bit près.


def configured_db_path() -> Optional[Path]:
    root = configured_data_root()
    return None if root is None else root / DB_FILE_NAME


def default_db_path() -> Path:
    if getattr(sys, "frozen", False):
        return default_data_root() / "fish-vision" / "data" / DB_FILE_NAME
    return _PKG_ROOT / "data" / DB_FILE_NAME


def annotations_db_path() -> Path:
    """Base d'annotations effective - `FISH_VISION_DB` reste prioritaire."""
    env = os.environ.get("FISH_VISION_DB")
    if env:
        return Path(env)
    configured = configured_db_path()
    return configured if configured is not None else default_db_path()


def camera_params_dir() -> Path:
    root = configured_data_root()
    base = root if root is not None else default_data_root()
    return base / CAMERA_PARAMS_SUBDIR


def exports_dir() -> Path:
    root = configured_data_root()
    base = root if root is not None else default_data_root()
    return base / EXPORTS_SUBDIR


def media_dir() -> Path:
    root = configured_data_root()
    base = root if root is not None else default_data_root()
    return base / MEDIA_SUBDIR


def described_locations() -> list[dict]:
    """Les emplacements réels, prêts à être affichés dans la page Paramètres.

    Une seule source de vérité pour l'interface : si un chemin change ici, la
    page le montre sans qu'on ait à la modifier.
    """
    return [
        {
            "key": "database",
            "label": "Base d'annotations",
            "path": str(annotations_db_path()),
            "is_file": True,
        },
        {
            "key": "calibrations",
            "label": "Calibrations",
            "path": str(camera_params_dir()),
            "is_file": False,
        },
        {
            "key": "exports",
            "label": "Exports",
            "path": str(exports_dir()),
            "is_file": False,
        },
        {
            "key": "media",
            "label": "Médias et vignettes",
            "path": str(media_dir()),
            "is_file": False,
        },
    ]


# ── Trace de la dernière sauvegarde ───────────────────────────────────────


def record_backup(path: Path, when: Optional[str] = None) -> Dict[str, Any]:
    """Mémorise date et emplacement de la dernière sauvegarde de la base.

    Sans cette trace, « ai-je sauvegardé récemment ? » n'avait aucune réponse
    dans l'application : la seule protection contre la perte des annotations
    reposait sur la mémoire de l'utilisateur.
    """
    from datetime import datetime

    stamp = when or datetime.now().isoformat(timespec="seconds")
    return save_config({KEY_LAST_BACKUP_AT: stamp, KEY_LAST_BACKUP_PATH: str(path)})


def last_backup() -> Dict[str, str]:
    cfg = load_config()
    return {
        "at": str(cfg.get(KEY_LAST_BACKUP_AT) or ""),
        "path": str(cfg.get(KEY_LAST_BACKUP_PATH) or ""),
    }


def backup_age_days() -> Optional[float]:
    """Âge de la dernière sauvegarde en jours, ou `None` si jamais sauvegardé."""
    from datetime import datetime

    stamp = last_backup()["at"]
    if not stamp:
        return None
    try:
        when = datetime.fromisoformat(stamp)
    except ValueError:
        return None
    return max(0.0, (datetime.now() - when).total_seconds() / 86400.0)


# ── Mesure et copie ───────────────────────────────────────────────────────


def directory_size(path: Path) -> int:
    """Taille cumulée d'un dossier (ou d'un fichier), octets, erreurs ignorées.

    Les liens matériels produits par le noyau d'export sont comptés autant de
    fois qu'ils apparaissent : c'est ce que montre l'explorateur de fichiers,
    donc ce que l'utilisateur reconnaîtra.
    """
    p = Path(path)
    if p.is_file():
        try:
            return p.stat().st_size
        except OSError:
            return 0
    if not p.is_dir():
        return 0
    total = 0
    for root, _dirs, files in os.walk(p, onerror=lambda _e: None):
        for name in files:
            try:
                total += (Path(root) / name).stat().st_size
            except OSError:
                continue
    return total


def human_size(num_bytes: Optional[int]) -> str:
    """« 1,4 Go » - lisible, virgule décimale française."""
    if num_bytes is None:
        return "-"
    value = float(num_bytes)
    for unit in ("o", "ko", "Mo", "Go", "To"):
        if value < 1024.0 or unit == "To":
            if unit == "o":
                return f"{int(value)} o"
            return f"{value:.1f} {unit}".replace(".", ",")
        value /= 1024.0
    return f"{value:.1f} To".replace(".", ",")


class StorageRootError(RuntimeError):
    """Racine refusée : inaccessible, non inscriptible, ou imbriquée."""


def validate_root(candidate: Path) -> Path:
    """Vérifie qu'une racine est utilisable - crée le dossier si nécessaire.

    Refuse aussi une racine **à l'intérieur** de la racine actuelle (ou
    l'inverse) : copier un dossier dans lui-même est une récursion infinie
    déguisée en clic anodin.
    """
    root = Path(candidate).expanduser()
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise StorageRootError(
            f"Dossier impossible à créer : {root}\n{exc}"
        ) from exc
    if not root.is_dir():
        raise StorageRootError(f"Ce n'est pas un dossier : {root}")
    probe = root / ".aquameasure_write_test"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        raise StorageRootError(
            f"Dossier non inscriptible : {root}\n{exc}"
        ) from exc

    current = data_root().resolve()
    resolved = root.resolve()
    if resolved == current:
        raise StorageRootError("C'est déjà la racine des données actuelle.")
    if _is_within(resolved, current) or _is_within(current, resolved):
        raise StorageRootError(
            "La nouvelle racine ne peut pas contenir l'actuelle, ni être "
            "contenue par elle - la copie tournerait en rond."
        )
    return resolved


def _is_within(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def copyable_sources() -> list[tuple[Path, Path]]:
    """Couples (source absolue, destination relative) à recopier vers une racine.

    On ne recopie que ce que l'application sait produire : base, calibrations,
    exports, médias - pris à leur emplacement **effectif** du moment. Le reste
    du dossier de travail ne la regarde pas.
    """
    pairs: list[tuple[Path, Path]] = []
    db = annotations_db_path()
    if db.is_file():
        pairs.append((db, Path(DB_FILE_NAME)))
        # Le journal WAL et l'index de rollback font partie de la base : les
        # oublier livre une copie amputée des dernières écritures.
        for suffix in ("-wal", "-shm"):
            side = Path(str(db) + suffix)
            if side.is_file():
                pairs.append((side, Path(DB_FILE_NAME + suffix)))
    for src, dst in (
        (camera_params_dir(), Path(CAMERA_PARAMS_SUBDIR)),
        (exports_dir(), EXPORTS_SUBDIR),
        (media_dir(), MEDIA_SUBDIR),
    ):
        if src.is_dir():
            pairs.append((src, dst))
    return pairs


def copy_data_tree(
    destination: Path,
    *,
    sources: Optional[list[tuple[Path, Path]]] = None,
    progress: Optional[Callable[[str, int, int], None]] = None,
) -> Dict[str, Any]:
    """Copie les données actuelles vers `destination`. **Ne supprime rien.**

    L'ancien emplacement reste intact : un déplacement silencieux qui échoue à
    mi-chemin perdrait les annotations. On copie, on vérifie, et c'est
    l'utilisateur qui décide plus tard de faire le ménage.
    """
    dest = Path(destination)
    dest.mkdir(parents=True, exist_ok=True)
    pairs = sources if sources is not None else copyable_sources()

    files: list[tuple[Path, Path]] = []
    for src, rel in pairs:
        if src.is_file():
            files.append((src, dest / rel))
        elif src.is_dir():
            for path in sorted(src.rglob("*")):
                if path.is_file():
                    files.append((path, dest / rel / path.relative_to(src)))

    total = len(files)
    copied = 0
    copied_bytes = 0
    errors: list[str] = []
    for index, (src, dst) in enumerate(files, start=1):
        if progress is not None:
            progress(src.name, index, total)
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            copied += 1
            copied_bytes += dst.stat().st_size
        except OSError as exc:
            errors.append(f"{src} : {exc}")
    return {
        "destination": str(dest),
        "file_count": total,
        "copied": copied,
        "copied_bytes": copied_bytes,
        "errors": errors,
    }
