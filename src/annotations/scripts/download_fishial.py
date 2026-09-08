#!/usr/bin/env python3
"""Télécharge les modèles pré-entraînés Fishial (MIT) pour identification espèce."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODELS = ROOT / "models"

# https://github.com/fishial/fish-identification — section Pre-trained Models
PACKS = {
    "classification": {
        "label": "DinoV2+ViT — 866 espèces (v0.10.2, TorchScript)",
        "url": "https://storage.googleapis.com/fishial-ml-resources/classification_model_v0.10.2.zip",
        "zip_name": "fishial_classification_v0.10.2.zip",
        "out_dir": "fishial_classification_v0.10.2",
    },
    "detector": {
        "label": "YOLO v26 détecteur poisson (TorchScript)",
        "url": "https://storage.googleapis.com/fishial-ml-resources/detector_v26_n3.zip",
        "zip_name": "fishial_detector_v26.zip",
        "out_dir": "fishial_detector_v26",
    },
    "segmentation": {
        "label": "FPN ResNet18 segmentation poisson 416px (TorchScript)",
        "url": "https://storage.googleapis.com/fishial-ml-resources/segmentator_fpn_res18_416_1.zip",
        "zip_name": "fishial_segmentator_fpn_res18.zip",
        "out_dir": "fishial_segmentator_fpn_res18",
    },
}

LABELS_URL = (
    "https://raw.githubusercontent.com/fishial/fish-identification/main/labels.json"
)


def _download(url: str, dest: Path, label: str = "fichier") -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.is_file() and dest.stat().st_size > 1024:
        print(f"  Déjà présent : {dest.name} ({dest.stat().st_size // (1024 * 1024)} Mo)")
        return

    print(f"Téléchargement {label}…")
    print(f"  -> {dest}")

    def _progress(block_num: int, block_size: int, total_size: int) -> None:
        if total_size > 0 and block_num % 100 == 0:
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


def _extract_zip(zip_path: Path, out_dir: Path) -> None:
    marker = out_dir / ".extracted"
    if marker.is_file():
        print(f"  Déjà extrait : {out_dir}")
        return
    print(f"Extraction -> {out_dir}")
    out_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(out_dir)
    marker.write_text(zip_path.name, encoding="utf-8")
    print("  OK")


def _download_labels() -> Path:
    dest = MODELS / "fishial_labels.json"
    _download(LABELS_URL, dest, "labels.json (866 espèces)")
    data = json.loads(dest.read_text(encoding="utf-8"))
    print(f"  {len(data)} classes dans labels.json")
    return dest


def download_pack(key: str, *, extract: bool = True) -> Path:
    spec = PACKS[key]
    zip_path = MODELS / spec["zip_name"]
    out_dir = MODELS / spec["out_dir"]
    print(f"\n=== {spec['label']} ===")
    _download(spec["url"], zip_path, spec["label"])
    if extract:
        _extract_zip(zip_path, out_dir)
    return out_dir if extract else zip_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Télécharge modèles Fishial pré-entraînés")
    parser.add_argument(
        "--pack",
        choices=["classification", "detector", "segmentation", "all"],
        default="classification",
        help="Pack à télécharger (défaut: classification 866 espèces)",
    )
    parser.add_argument("--no-extract", action="store_true", help="Ne pas extraire le zip")
    parser.add_argument("--labels-only", action="store_true", help="labels.json uniquement")
    args = parser.parse_args()

    MODELS.mkdir(parents=True, exist_ok=True)

    if args.labels_only:
        _download_labels()
        return 0

    _download_labels()

    if args.pack in ("classification", "all"):
        download_pack("classification", extract=not args.no_extract)
    if args.pack in ("detector", "all"):
        download_pack("detector", extract=not args.no_extract)
    if args.pack in ("segmentation", "all"):
        download_pack("segmentation", extract=not args.no_extract)

    print("\nTerminé. Modèles dans :", MODELS)
    print("Lancez aquameasure.py — l'IA proposera les espèces Fishial sur chaque détection.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
