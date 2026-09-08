"""Export / import ZIP d'un jeu de calibration."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from src.util import paths

PACKAGE_FILES = (
    "mtx1.npy",
    "dist1.npy",
    "mtx2.npy",
    "dist2.npy",
    "R.npy",
    "T.npy",
    "F.npy",
    "stereo_rmse.npy",
    "left_rmse.npy",
    "right_rmse.npy",
    "videos.txt",
    "charuco_config.json",
    "calib_meta.json",
    "sync_frames.npy",
    "trim_frames.npy",
)


def calibration_package_complete(dir_path: Path) -> bool:
    required = ("mtx1.npy", "dist1.npy", "mtx2.npy", "dist2.npy", "R.npy", "T.npy")
    return all((dir_path / name).is_file() for name in required)


def _copy_checked(src: Path, dst: Path) -> str | None:
    try:
        if dst.exists():
            dst.unlink()
        shutil.copy2(src, dst)
    except OSError as exc:
        return f"Copie echouee {src} : {exc}"
    return None


def _write_manifest(staging: Path) -> str | None:
    manifest = {
        "format": "aquameasure-calib-v1",
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "stereo_rmse": paths.stereo_rmse_if_exists(),
    }
    try:
        (staging / "manifest.json").write_text(
            json.dumps(manifest, indent=2), encoding="utf-8"
        )
    except OSError as exc:
        return f"manifest.json : {exc}"
    return None


def _zip_dir(src_dir: Path, dest_zip: Path) -> str | None:
    dest_zip.parent.mkdir(parents=True, exist_ok=True)
    if dest_zip.exists():
        try:
            dest_zip.unlink()
        except OSError:
            return "Fichier de destination verrouille"
    ps = (
        f"Compress-Archive -Path '{src_dir}\\*' "
        f"-DestinationPath '{dest_zip}' -Force"
    )
    proc = subprocess.run(
        ["powershell", "-NoProfile", "-Command", ps],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if proc.returncode != 0 or not dest_zip.is_file():
        err = (proc.stderr or proc.stdout or "").strip()
        return f"Creation du zip echouee : {err or 'PowerShell'}"
    return None


def _unzip_to(zip_path: Path, dest_dir: Path) -> str | None:
    dest_dir.mkdir(parents=True, exist_ok=True)
    ps = (
        f"Expand-Archive -Path '{zip_path}' "
        f"-DestinationPath '{dest_dir}' -Force"
    )
    proc = subprocess.run(
        ["powershell", "-NoProfile", "-Command", ps],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        return f"Extraction du zip echouee : {err or 'PowerShell'}"
    return None


def export_calibration_package(dest_zip: str) -> str | None:
    src_dir = paths.camera_params_dir()
    if not calibration_package_complete(src_dir):
        return "Aucune calibration complete dans camera_parameters/"

    dest = Path(dest_zip)
    with tempfile.TemporaryDirectory() as tmp:
        staging = Path(tmp)
        for name in PACKAGE_FILES:
            src = src_dir / name
            if src.is_file():
                err = _copy_checked(src, staging / name)
                if err:
                    return err
        err = _write_manifest(staging)
        if err:
            return err
        return _zip_dir(staging, dest)


def import_calibration_package(source_zip: str) -> str | None:
    zip_path = Path(source_zip)
    if not zip_path.is_file():
        return "Fichier introuvable"

    with tempfile.TemporaryDirectory() as tmp:
        extract_dir = Path(tmp)
        err = _unzip_to(zip_path, extract_dir)
        if err:
            return err

        payload = extract_dir
        if not calibration_package_complete(payload):
            for sub in extract_dir.iterdir():
                if sub.is_dir() and calibration_package_complete(sub):
                    payload = sub
                    break
        if not calibration_package_complete(payload):
            return "Archive invalide (fichiers mtx/R manquants)"

        paths.ensure_camera_params_dir()
        dest_dir = paths.camera_params_dir()
        for item in payload.iterdir():
            if item.is_file() and item.suffix in (".npy", ".txt", ".json"):
                err = _copy_checked(item, dest_dir / item.name)
                if err:
                    return err
    return None
