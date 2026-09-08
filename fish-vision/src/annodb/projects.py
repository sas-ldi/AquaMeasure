"""Project and media asset helpers."""

from __future__ import annotations

import hashlib
import shutil
import uuid
from pathlib import Path
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import MediaAsset, Project

_PKG_ROOT = Path(__file__).resolve().parents[2]
_MEDIA_ROOT = _PKG_ROOT / "data" / "media"
_APP_ROOT = _PKG_ROOT.parent


def media_root() -> Path:
    _MEDIA_ROOT.mkdir(parents=True, exist_ok=True)
    return _MEDIA_ROOT


def resolve_project_ids(
    session: Session, names: Optional[list[str]],
) -> Optional[list[str]]:
    """Noms **ou** identifiants de projets → identifiants réels.

    Les filtres d'export attendent des `project_id` (des UUID). Passer
    directement ce que l'opérateur a tapé (`--project "Sortie mai"`) comparait
    un nom à un UUID : le filtre ne retenait rien et l'export sortait vide,
    sans le moindre message. Un nom inconnu est signalé ; si **aucun** n'est
    reconnu, on renvoie `None` (pas de filtre) plutôt qu'une liste vide, qui
    filtrerait tout.
    """
    if not names:
        return None
    ids: list[str] = []
    for name in names:
        row = session.scalar(
            select(Project).where((Project.id == name) | (Project.name == name))
        )
        if row is None:
            print(f"[!] Projet inconnu, ignoré : {name}")
            continue
        if row.id not in ids:
            ids.append(row.id)
    if not ids:
        print("[!] Aucun projet reconnu - export sur la base entière.")
        return None
    return ids


def get_or_create_project(
    session: Session,
    name: str,
    description: Optional[str] = None,
    project_id: Optional[str] = None,
) -> Project:
    existing = session.scalar(select(Project).where(Project.name == name))
    if existing:
        return existing
    proj = Project(
        id=project_id or str(uuid.uuid4()),
        name=name,
        description=description,
    )
    session.add(proj)
    session.flush()
    return proj


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def file_signature(path: Path) -> tuple[Optional[int], Optional[float]]:
    """(taille, date de modification) - la signature qui valide le cache sha256."""
    try:
        st = path.stat()
    except OSError:
        return None, None
    # mtime arrondi : les systèmes de fichiers ne conservent pas tous la même
    # précision, et une différence de microseconde ne veut rien dire ici.
    return int(st.st_size), round(float(st.st_mtime), 3)


def _signature_matches(media: MediaAsset, size: Optional[int], mtime: Optional[float]) -> bool:
    if not media.sha256 or media.sha256_size is None or media.sha256_mtime is None:
        return False
    if size is None or mtime is None:
        return False
    return int(media.sha256_size) == int(size) and abs(
        float(media.sha256_mtime) - float(mtime)
    ) < 0.002


def sha256_for_path(
    session: Session,
    path: Path,
    *,
    project_id: Optional[str] = None,
    candidates: Optional[list[MediaAsset]] = None,
) -> str:
    """sha256 du fichier, **réutilisé** si (taille, mtime) n'ont pas bougé.

    Le condensé d'une vidéo de plusieurs Go était recalculé à chaque annotation
    et à chaque enregistrement de session, sur le thread UI : c'est le gel
    observé sur `saveSession`. Le cache est porté par `media_assets`
    (`sha256_size`, `sha256_mtime`) : il survit au redémarrage, et la moindre
    réécriture du fichier l'invalide.
    """
    size, mtime = file_signature(path)
    rows = list(candidates) if candidates is not None else _media_candidates(
        session, path, project_id,
    )
    for media in rows:
        if _signature_matches(media, size, mtime):
            return str(media.sha256)

    sha = _sha256_file(path)
    for media in rows:
        if media.sha256 == sha or not media.sha256:
            media.sha256 = sha
            media.sha256_size = size
            media.sha256_mtime = mtime
    return sha


def _media_candidates(
    session: Session, path: Path, project_id: Optional[str],
) -> list[MediaAsset]:
    """Lignes média susceptibles de décrire ce fichier (mêmes chemins connus)."""
    rel_candidates = {str(path), path.name}
    try:
        rel_candidates.add(str(path.relative_to(media_root())))
    except ValueError:
        pass
    q = select(MediaAsset).where(MediaAsset.rel_path.in_(sorted(rel_candidates)))
    if project_id:
        q = q.where(MediaAsset.project_id == project_id)
    return list(session.scalars(q))


def remember_sha256(media: MediaAsset, path: Path, sha: str) -> None:
    """Grave le condensé et la signature du fichier qui l'a produit."""
    size, mtime = file_signature(path)
    media.sha256 = sha
    media.sha256_size = size
    media.sha256_mtime = mtime


def register_media(
    session: Session,
    *,
    project_id: str,
    file_path: Path,
    media_type: Optional[str] = None,
    copy_into_store: bool = True,
) -> MediaAsset:
    file_path = file_path.resolve()
    if not file_path.exists():
        raise FileNotFoundError(file_path)

    if media_type is None:
        media_type = "video" if file_path.suffix.lower() in {".mp4", ".avi", ".mov", ".mkv"} else "image"

    if copy_into_store:
        dest_dir = media_root() / project_id
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / file_path.name
        if dest.resolve() != file_path and not dest.exists():
            shutil.copy2(file_path, dest)
        rel_path = f"{project_id}/{file_path.name}"
        stored = dest
    else:
        try:
            rel_path = str(file_path.relative_to(media_root()))
        except ValueError:
            rel_path = str(file_path.resolve())
        stored = file_path

    # Condensé d'abord : s'il est en cache, on ne relit pas le fichier - et si
    # la ligne existe déjà avec ses dimensions, on évite aussi d'ouvrir la
    # vidéo pour redemander une taille qu'on connaît.
    known = _media_candidates(session, stored, project_id)
    sha = sha256_for_path(session, stored, project_id=project_id, candidates=known)

    by_hash = session.scalar(
        select(MediaAsset).where(
            MediaAsset.project_id == project_id,
            MediaAsset.sha256 == sha,
        )
    )
    if by_hash is not None and by_hash.width:
        if by_hash.rel_path != rel_path:
            by_hash.rel_path = rel_path
        remember_sha256(by_hash, stored, sha)
        session.flush()
        return by_hash

    width = height = frame_count = None
    fps = None
    try:
        import cv2
        if media_type == "image":
            img = cv2.imread(str(stored))
            if img is not None:
                height, width = img.shape[:2]
        else:
            cap = cv2.VideoCapture(str(stored))
            if cap.isOpened():
                width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                fps = cap.get(cv2.CAP_PROP_FPS) or None
                frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            cap.release()
    except ImportError:
        pass

    if by_hash:
        if by_hash.rel_path != rel_path:
            by_hash.rel_path = rel_path
        if width and not by_hash.width:
            by_hash.width = width
            by_hash.height = height
            by_hash.fps = fps
            by_hash.frame_count = frame_count
        remember_sha256(by_hash, stored, sha)
        session.flush()
        return by_hash

    existing = session.scalar(
        select(MediaAsset).where(
            MediaAsset.project_id == project_id,
            MediaAsset.rel_path == rel_path,
        )
    )
    if existing:
        remember_sha256(existing, stored, sha)
        session.flush()
        return existing

    media = MediaAsset(
        id=str(uuid.uuid4()),
        project_id=project_id,
        media_type=media_type,
        rel_path=rel_path,
        width=width,
        height=height,
        fps=fps,
        frame_count=frame_count,
    )
    remember_sha256(media, stored, sha)
    session.add(media)
    session.flush()
    return media


def resolve_media_path(media: MediaAsset) -> Path:
    """Resolve on-disk path: absolute rel_path, media store, or known video list."""
    p = Path(media.rel_path)
    if p.is_absolute() and p.is_file():
        return p
    candidate = media_root() / media.rel_path
    if candidate.is_file():
        return candidate
    if p.is_file():
        return p

    fname = p.name
    videos_txt = _APP_ROOT / "camera_parameters" / "videos.txt"
    if videos_txt.is_file():
        for line in videos_txt.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            vp = Path(line)
            if vp.is_file() and vp.name == fname:
                return vp

    for hit in media_root().rglob(fname):
        if hit.is_file():
            return hit

    return candidate
