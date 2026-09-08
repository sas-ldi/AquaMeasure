"""Telechargement des poids a la demande, avec verification d'integrite."""

from __future__ import annotations

import hashlib
import shutil
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable

from fish_detectors.spec import DetectorSpec

ProgressFn = Callable[[int, int], None]
_CHUNK = 1 << 20
_USER_AGENT = "AquaMeasure/1.0 (+https://www.ird.fr)"


class DownloadError(RuntimeError):
    pass


def sha256_of(path: Path, progress: ProgressFn | None = None) -> str:
    digest = hashlib.sha256()
    total = path.stat().st_size
    done = 0
    with path.open("rb") as fh:
        while chunk := fh.read(_CHUNK):
            digest.update(chunk)
            done += len(chunk)
            if progress:
                progress(done, total)
    return digest.hexdigest()


def _fetch(url: str, dest: Path, progress: ProgressFn | None, cancelled: Callable[[], bool] | None) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            total = int(response.headers.get("Content-Length") or 0)
            done = 0
            with tmp.open("wb") as out:
                while chunk := response.read(_CHUNK):
                    if cancelled and cancelled():
                        raise DownloadError("Telechargement annule")
                    out.write(chunk)
                    done += len(chunk)
                    if progress:
                        progress(done, total)
        tmp.replace(dest)
    except DownloadError:
        tmp.unlink(missing_ok=True)
        raise
    except Exception as exc:
        tmp.unlink(missing_ok=True)
        raise DownloadError(f"Echec telechargement : {exc}") from exc


def _extract_member(archive: Path, member_hint: str, dest: Path) -> None:
    with zipfile.ZipFile(archive) as zf:
        names = [n for n in zf.namelist()
                 if not n.startswith("__MACOSX") and not n.endswith("/")]
        wanted = [n for n in names if Path(n).name == member_hint]
        if not wanted:
            raise DownloadError(f"'{member_hint}' introuvable dans {archive.name}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(wanted[0]) as src, dest.open("wb") as out:
            shutil.copyfileobj(src, out)


def download(
    spec: DetectorSpec,
    models_dir: Path,
    *,
    progress: ProgressFn | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> Path:
    """Recupere les poids du modele et retourne leur chemin local."""
    if not spec.downloadable:
        raise DownloadError(f"{spec.label} n'a pas de source de telechargement")
    target = spec.target_path(models_dir)
    if target is None:
        raise DownloadError(f"{spec.label} n'a pas de nom de fichier cible")

    archive_kind = spec.options.get("archive")
    if archive_kind == "zip":
        archive = models_dir / f"{spec.id}.zip"
        _fetch(spec.download_url, archive, progress, cancelled)
        try:
            member = str(spec.options.get("archive_member") or target.name)
            _extract_member(archive, member, target)
        finally:
            archive.unlink(missing_ok=True)
    else:
        _fetch(spec.download_url, target, progress, cancelled)

    if spec.sha256:
        actual = sha256_of(target)
        if actual.lower() != spec.sha256.lower():
            target.unlink(missing_ok=True)
            raise DownloadError(
                f"Empreinte incorrecte pour {spec.label} - fichier supprime "
                f"(attendu {spec.sha256[:12]}…, obtenu {actual[:12]}…)"
            )
    elif spec.size_bytes and target.stat().st_size < spec.size_bytes * 0.5:
        target.unlink(missing_ok=True)
        raise DownloadError(f"Fichier tronque pour {spec.label} - telechargement supprime")

    return target
