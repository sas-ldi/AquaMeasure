#!/usr/bin/env python3
"""Télécharge les modèles IA pré-entraînés (aucun entraînement local).

- YOLO poisson → fish-vision/models/fish_detect_public.pt
- Fishial 866 espèces → fish-vision/models/fishial_classification_v0.10.2/
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODELS = ROOT / "fish-vision" / "models"
FISHIAL_SCRIPT = ROOT / "fish-vision" / "scripts" / "download_fishial.py"

# Détecteur officiel Fishial (YOLO26 nano, TorchScript-ready .pt, classe « Fish »).
# Bien plus précis que le yolo11n public : c'est le détecteur utilisé en production.
FISHIAL_DETECTOR = {
    "zip": MODELS / "detector_v26_n3.zip",
    "dir": MODELS / "detector_v26_n3",
    "model": MODELS / "detector_v26_n3" / "model.pt",
    "url": "https://storage.googleapis.com/fishial-ml-resources/detector_v26_n3.zip",
    "label": "Détecteur poisson Fishial (YOLO26 nano)",
    "min_bytes": 100_000_000,
}


def _download(url: str, dest: Path, label: str, min_bytes: int = 1024) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.is_file() and dest.stat().st_size >= min_bytes:
        mb = dest.stat().st_size // (1024 * 1024)
        print(f"  Déjà présent : {dest.name} ({mb} Mo)")
        return

    print(f"Téléchargement {label}…")
    print(f"  -> {dest}")

    def _progress(block_num: int, block_size: int, total_size: int) -> None:
        if total_size > 0 and block_num % 80 == 0:
            pct = min(100, block_num * block_size * 100 // total_size)
            mb = block_num * block_size // (1024 * 1024)
            print(f"\r  {pct}% ({mb} Mo)", end="", flush=True)

    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        urllib.request.urlretrieve(url, tmp, reporthook=_progress)
        print()
        tmp.replace(dest)
    except Exception:
        if tmp.is_file():
            tmp.unlink(missing_ok=True)
        raise


def download_yolo(*, force: bool = False) -> Path:
    import shutil
    import zipfile

    spec = FISHIAL_DETECTOR
    print("\n=== Détection poisson (Fishial YOLO26) ===")
    if force:
        if spec["zip"].is_file():
            spec["zip"].unlink()
        if spec["dir"].is_dir():
            shutil.rmtree(spec["dir"], ignore_errors=True)
    if spec["model"].is_file():
        mb = spec["model"].stat().st_size // (1024 * 1024)
        print(f"  Déjà présent : {spec['model'].relative_to(MODELS)} ({mb} Mo)")
        return spec["model"]
    _download(spec["url"], spec["zip"], spec["label"], spec["min_bytes"])
    print("  Extraction…")
    spec["dir"].mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(spec["zip"]) as z:
        for name in z.namelist():
            if name.startswith("__MACOSX"):
                continue
            z.extract(name, spec["dir"])
    spec["zip"].unlink(missing_ok=True)
    return spec["model"]


def download_fishial(*, force: bool = False) -> None:
    print("\n=== Classification espèce (Fishial) ===")
    if not FISHIAL_SCRIPT.is_file():
        raise FileNotFoundError(f"Script introuvable : {FISHIAL_SCRIPT}")
    cmd = [sys.executable, str(FISHIAL_SCRIPT), "--pack", "classification"]
    if force:
        marker = MODELS / "fishial_classification_v0.10.2" / ".extracted"
        zip_path = MODELS / "fishial_classification_v0.10.2.zip"
        labels = MODELS / "fishial_labels.json"
        for p in (marker, zip_path, labels):
            if p.is_file():
                p.unlink()
        fishial_dir = MODELS / "fishial_classification_v0.10.2"
        if fishial_dir.is_dir():
            import shutil
            shutil.rmtree(fishial_dir, ignore_errors=True)
    subprocess.run(cmd, check=True, cwd=ROOT / "fish-vision")


def verify() -> list[str]:
    missing: list[str] = []
    if not FISHIAL_DETECTOR["model"].is_file():
        missing.append("detector_v26_n3/model.pt")
    fishial = MODELS / "fishial_classification_v0.10.2" / "model.pt"
    if not fishial.is_file():
        missing.append("fishial_classification_v0.10.2/model.pt")
    return missing


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Télécharge modèles pré-entraînés AquaMeasure (sans entraînement)",
    )
    parser.add_argument("--yolo-only", action="store_true")
    parser.add_argument("--fishial-only", action="store_true")
    parser.add_argument("--force", action="store_true", help="Re-télécharger même si présent")
    parser.add_argument("--check", action="store_true", help="Vérifier seulement")
    args = parser.parse_args()

    MODELS.mkdir(parents=True, exist_ok=True)

    if args.check:
        missing = verify()
        if missing:
            print("Manquant :", ", ".join(missing))
            return 1
        print("OK — tous les modèles pré-entraînés sont présents.")
        return 0

    if not args.fishial_only:
        download_yolo(force=args.force)
    if not args.yolo_only:
        download_fishial(force=args.force)

    missing = verify()
    if missing:
        print("\nERREUR — encore manquant :", ", ".join(missing))
        return 1

    print("\nTerminé. Modèles dans :", MODELS)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
