#!/usr/bin/env python3
"""Download public fish datasets for YOLO bootstrap training."""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]

# Presets: detection bootstrap (1 class) vs species (multi-class)
DATASETS = {
    "fish416": {
        "label": "Roboflow Fish 416 (~680 img, 1 classe par defaut)",
        "roboflow": {"workspace": "roboflow-jvuqo", "project": "fish-1yfbm", "version": 1},
        "out_subdir": "fish",
        "single_class": True,
    },
    "fish_families": {
        "label": "Roboflow Fish 416 multi-classes (familles recifales, noms latins)",
        "roboflow": {"workspace": "roboflow-jvuqo", "project": "fish-1yfbm", "version": 1},
        "out_subdir": "fish_families",
        "single_class": False,
    },
    "deepfish": {
        "label": "DeepFish via Roboflow (~4.9k img, 1 classe, bacs marché)",
        "roboflow": {"workspace": "fish-igbc7", "project": "deepfish-aemtr", "version": 1},
        "out_subdir": "deepfish",
        "single_class": True,
    },
    "fish4knowledge": {
        "label": "Fish4Knowledge via Roboflow (multi-classes, espèces tropicales)",
        "roboflow": {"workspace": "g18l5754", "project": "fish4knowledge-dataset", "version": 1},
        "out_subdir": "fish4knowledge",
        "single_class": False,
    },
}

ZENODO = {
    "obsea": {
        "label": "OBSEA Méditerranée YOLO (~3.8 Go, 23 espèces, format YOLO prêt)",
        "url": "https://zenodo.org/records/14888440/files/obsea_split_YOLO.zip?download=1",
        "out_subdir": "obsea",
    },
    "kakadu": {
        "label": "Kakadu Australie tropicale COCO (~6 Go, 23 espèces, conversion requise)",
        "url": "https://zenodo.org/records/7250921/files/202210-KakaduFishAI-TrainingData.zip?download=1",
        "out_subdir": "kakadu_raw",
        "coco_json": "annotations.json",
    },
}

DIRECT_ZIPS = {
    "fishinv": {
        "label": "FishInv recif tropical Orange/Tenaka (YOLO, 17 especes)",
        "url": (
            "https://stpubtenakanclyw.blob.core.windows.net/marine-detect/"
            "FishInv-dataset.zip?sv=2022-11-02&ss=bf&srt=co&sp=rltf&se=2099-12-31T18:55:46Z"
            "&st=2025-02-03T10:55:46Z&spr=https,http&sig=w%2FTQzrECsYsjtkBXNnnuFtn%2BC06PkjgLxDgRw%2FaUUKI%3D"
        ),
        "out_subdir": "fishinv",
    },
}


def _download_url(url: str, dest: Path, label: str = "file") -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    print(f"Downloading {label}...")
    print(f"  URL: {url[:80]}...")

    def _progress(block_num, block_size, total_size):
        if total_size > 0 and block_num % 50 == 0:
            pct = min(100, block_num * block_size * 100 // total_size)
            print(f"\r  Progress: {pct}%", end="", flush=True)

    urllib.request.urlretrieve(url, dest, reporthook=_progress)
    print()
    return dest


def extract_zip(zip_path: Path, dest: Path) -> None:
    print(f"Extracting {zip_path.name}...")
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(dest)


def find_yolo_root(extracted: Path) -> Path:
    """Find folder containing data.yaml and train/images (searches nested zips)."""
    if (extracted / "train" / "images").exists():
        return extracted
    for data_yaml in extracted.rglob("data.yaml"):
        parent = data_yaml.parent
        if (parent / "train" / "images").exists():
            return parent
    for train_images in extracted.rglob("train/images"):
        if train_images.is_dir():
            return train_images.parent.parent
    if (extracted / "data.yaml").exists():
        return extracted
    return extracted


def normalize_valid_split(root: Path) -> None:
    """Ultralytics expects val/; some datasets ship valid/."""
    valid = root / "valid"
    val = root / "val"
    if not valid.exists():
        # Repair prior bad move (valid nested under val/).
        nested = val / "valid"
        if nested.exists() and not (val / "images").exists():
            shutil.move(str(nested), str(root / "_valid_tmp"))
            if val.exists():
                shutil.rmtree(val)
            (root / "_valid_tmp").rename(val)
        return
    if (val / "images").exists():
        shutil.rmtree(valid)
        return
    if val.exists():
        shutil.rmtree(val)
    valid.rename(val)


def collapse_to_single_class(root: Path) -> None:
    """Remap all class ids to 0 for detection bootstrap."""
    for split in ("train", "val", "valid", "test"):
        lbl_dir = root / split / "labels"
        if not lbl_dir.exists():
            continue
        for txt in lbl_dir.glob("*.txt"):
            lines = []
            for line in txt.read_text(encoding="utf-8").strip().splitlines():
                parts = line.split()
                if len(parts) >= 5:
                    parts[0] = "0"
                    lines.append(" ".join(parts))
            txt.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    data_yaml = root / "data.yaml"
    if data_yaml.exists():
        import yaml
        cfg = yaml.safe_load(data_yaml.read_text(encoding="utf-8")) or {}
        cfg["names"] = {0: "fish"}
        cfg["nc"] = 1
        with data_yaml.open("w", encoding="utf-8") as f:
            yaml.dump(cfg, f, default_flow_style=False)


def _format_data_yaml(dest: Path, cfg: dict) -> str:
    val_dir = "val" if (dest / "val" / "images").exists() else "valid"
    names = cfg.get("names") or {0: "fish"}
    if isinstance(names, list):
        names = {i: n for i, n in enumerate(names)}
    lines = [
        f"path: {dest.resolve().as_posix()}",
        "train: train/images",
        f"val: {val_dir}/images",
        "",
        "names:",
    ]
    for k in sorted(names, key=lambda x: int(x) if str(x).isdigit() else x):
        lines.append(f"  {k}: {names[k]}")
    return "\n".join(lines) + "\n"


def write_config(dest: Path, single_class: bool = True, family_config: bool = False) -> None:
    import yaml

    data_yaml = dest / "data.yaml"
    if not data_yaml.exists():
        names = {0: "fish"}
        cfg = {
            "path": dest.resolve().as_posix(),
            "train": "train/images",
            "val": "val/images" if (dest / "val" / "images").exists() else "valid/images",
            "names": names,
        }
        with data_yaml.open("w", encoding="utf-8") as f:
            yaml.dump(cfg, f, default_flow_style=False)
    elif single_class:
        collapse_to_single_class(dest)

    cfg = yaml.safe_load(data_yaml.read_text(encoding="utf-8")) or {}
    yaml_text = _format_data_yaml(dest, cfg)

    if family_config or (not single_class and int(cfg.get("nc", len(cfg.get("names", {})) or 1)) > 1):
        (ROOT / "configs" / "data_family.yaml").write_text(yaml_text, encoding="utf-8")
    else:
        (ROOT / "configs" / "data_public.yaml").write_text(yaml_text, encoding="utf-8")


def download_roboflow_dataset(preset: dict, dest: Path, api_key: str) -> Path:
    try:
        from roboflow import Roboflow
    except ImportError:
        print("Install roboflow: pip install roboflow")
        raise SystemExit(1)

    rf = Roboflow(api_key=api_key)
    meta = preset["roboflow"]
    project = rf.workspace(meta["workspace"]).project(meta["project"])
    version = project.version(meta["version"])
    location = str(dest.parent)
    version.download("yolov8", location=location)
    downloaded = dest.parent / f"{meta['project']}-{meta['version']}"
    if downloaded.exists() and downloaded != dest:
        if dest.exists():
            shutil.rmtree(dest)
        shutil.move(str(downloaded), str(dest))
    return dest


def convert_kakadu_coco(raw_dir: Path, out_dir: Path) -> Path:
    """Convert Kakadu COCO export to YOLO layout."""
    import json

    coco_files = list(raw_dir.rglob("*.json"))
    if not coco_files:
        raise FileNotFoundError(f"No COCO JSON in {raw_dir}")
    coco_path = coco_files[0]
    print(f"Converting COCO {coco_path.name} -> YOLO...")

    try:
        from ultralytics.data.converter import convert_coco
        convert_coco(
            labels_dir=str(coco_path.parent),
            save_dir=str(out_dir),
            use_segments=False,
            cls91to80=False,
        )
    except Exception as e:
        print(f"Ultralytics convert_coco failed ({e}), using manual converter...")
        _manual_coco_to_yolo(coco_path, raw_dir, out_dir)

    return find_yolo_root(out_dir)


def _manual_coco_to_yolo(coco_path: Path, images_root: Path, out_dir: Path) -> None:
    import json

    data = json.loads(coco_path.read_text(encoding="utf-8"))
    images = {im["id"]: im for im in data["images"]}
    categories = {c["id"]: c["name"] for c in data.get("categories", [])}
    by_image: dict = {}
    for ann in data["annotations"]:
        by_image.setdefault(ann["image_id"], []).append(ann)

    train_img = out_dir / "train" / "images"
    train_lbl = out_dir / "train" / "labels"
    val_img = out_dir / "val" / "images"
    val_lbl = out_dir / "val" / "labels"
    for d in (train_img, train_lbl, val_img, val_lbl):
        d.mkdir(parents=True, exist_ok=True)

    ids = list(images.keys())
    split = int(len(ids) * 0.85)
    name_to_id = {name: i for i, name in enumerate(sorted(set(categories.values())))}

    for i, img_id in enumerate(ids):
        im = images[img_id]
        fname = im["file_name"]
        src = images_root / fname
        if not src.exists():
            for found in images_root.rglob(Path(fname).name):
                src = found
                break
        if not src.exists():
            continue
        is_train = i < split
        dst_img = (train_img if is_train else val_img) / src.name
        dst_lbl = (train_lbl if is_train else val_lbl) / f"{src.stem}.txt"
        shutil.copy2(src, dst_img)
        w, h = im["width"], im["height"]
        lines = []
        for ann in by_image.get(img_id, []):
            if "bbox" not in ann:
                continue
            x, y, bw, bh = ann["bbox"]
            cx = (x + bw / 2) / w
            cy = (y + bh / 2) / h
            cls = name_to_id.get(categories.get(ann["category_id"], "fish"), 0)
            lines.append(f"{cls} {cx:.6f} {cy:.6f} {bw / w:.6f} {bh / h:.6f}")
        dst_lbl.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")

    import yaml
    (out_dir / "data.yaml").write_text(
        yaml.dump(
            {
                "path": out_dir.resolve().as_posix(),
                "train": "train/images",
                "val": "val/images",
                "names": name_to_id,
            },
            default_flow_style=False,
        ),
        encoding="utf-8",
    )


def create_minimal_dataset(dest: Path) -> None:
    import cv2
    import numpy as np

    print("WARNING: Creating minimal synthetic dataset (pipeline test only, not useful for real training).")
    print("  Use: --dataset deepfish + ROBOFLOW_API_KEY, or --dataset obsea, or --zip your_export.zip")
    for split, n in [("train", 8), ("valid", 2)]:
        img_dir = dest / split / "images"
        lbl_dir = dest / split / "labels"
        img_dir.mkdir(parents=True, exist_ok=True)
        lbl_dir.mkdir(parents=True, exist_ok=True)
        for i in range(n):
            img = np.zeros((416, 416, 3), dtype=np.uint8)
            img[:, :] = (40, 80, 120)
            x1, y1 = 80 + i * 10, 150 + (i % 3) * 20
            x2, y2 = x1 + 120, y1 + 60
            cv2.rectangle(img, (x1, y1), (x2, y2), (90, 170, 210), -1)
            name = f"fish_{split}_{i:03d}"
            cv2.imwrite(str(img_dir / f"{name}.jpg"), img)
            cx = ((x1 + x2) / 2) / 416
            cy = ((y1 + y2) / 2) / 416
            bw = (x2 - x1) / 416
            bh = (y2 - y1) / 416
            (lbl_dir / f"{name}.txt").write_text(f"0 {cx:.6f} {cy:.6f} {bw:.6f} {bh:.6f}\n")


def list_datasets() -> None:
    print("Datasets disponibles (--dataset):\n")
    print("  BOOTSTRAP détection (1 classe « poisson »):")
    for key in ("fish416", "deepfish"):
        print(f"    {key:16} {DATASETS[key]['label']}")
    print("\n  Familles / multi-classes (fine-tuning taxonomique):")
    for key in ("fish_families", "fish4knowledge"):
        print(f"    {key:16} {DATASETS[key]['label']}")
    print("\n  Zenodo (téléchargement direct, pas de clé API):")
    for key, meta in ZENODO.items():
        print(f"    {key:16} {meta['label']}")
    print("\n  Autres:")
    for key, meta in DIRECT_ZIPS.items():
        print(f"    {key:16} {meta['label']}")
    print("\n  Roboflow: compte gratuit + clé API -> https://app.roboflow.com/settings/api")
    print("  Variable: set ROBOFLOW_API_KEY=ta_cle")


def main() -> int:
    parser = argparse.ArgumentParser(description="Download public fish YOLO datasets")
    parser.add_argument(
        "--dataset",
        choices=[*DATASETS.keys(), *ZENODO.keys(), *DIRECT_ZIPS.keys(), "synthetic"],
        default="deepfish",
        help="Preset dataset (default: deepfish)",
    )
    parser.add_argument("--list", action="store_true", help="List available datasets")
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--zip", type=Path, default=None, help="Local YOLO zip export")
    parser.add_argument("--api-key", default=os.environ.get("ROBOFLOW_API_KEY"))
    parser.add_argument("--single-class", action="store_true", help="Force 1 class (fish) for multi-class sets")
    parser.add_argument(
        "--keep-classes",
        action="store_true",
        help="Keep original class names (alias for not using --single-class on multi-class presets)",
    )
    parser.add_argument("--allow-synthetic", action="store_true", help="Allow synthetic fallback")
    args = parser.parse_args()

    if args.list:
        list_datasets()
        return 0

    if args.zip and args.zip.exists():
        dest = args.out or (ROOT / "data" / "public" / "custom_zip")
        dest.mkdir(parents=True, exist_ok=True)
        extract_zip(args.zip, dest)
        root = find_yolo_root(dest)
        normalize_valid_split(root)
        keep = args.keep_classes and not args.single_class
        write_config(root, single_class=not keep and args.single_class, family_config=keep)
        print(f"Dataset ready: {root}")
        return 0

    if args.dataset in DATASETS:
        preset = DATASETS[args.dataset]
        dest = args.out or (ROOT / "data" / "public" / preset["out_subdir"])
        if not args.api_key:
            print(f"Dataset: {preset['label']}")
            print("ROBOFLOW_API_KEY manquante.")
            print("  1. Créer compte gratuit https://app.roboflow.com")
            print("  2. set ROBOFLOW_API_KEY=ta_cle")
            print(f"  3. Relancer: python scripts/download_public_dataset.py --dataset {args.dataset}")
            print("\nAlternatives sans clé: --dataset obsea  ou  --dataset fishinv")
            return 1
        download_roboflow_dataset(preset, dest, args.api_key)
        root = find_yolo_root(dest.parent)
        if root != dest and root.exists():
            dest = root
        normalize_valid_split(dest)
        if args.keep_classes:
            single = False
        else:
            single = preset.get("single_class", False) or args.single_class
        write_config(dest, single_class=single, family_config=not single)
        print(f"Dataset ready: {dest}")
        if not single:
            print("  -> configs/data_family.yaml mis a jour (multi-classes)")
        return 0

    if args.dataset in ZENODO:
        meta = ZENODO[args.dataset]
        cache = ROOT / "data" / "downloads"
        cache.mkdir(parents=True, exist_ok=True)
        zip_path = cache / f"{args.dataset}.zip"
        if not zip_path.exists():
            _download_url(meta["url"], zip_path, meta["label"])
        raw = ROOT / "data" / "public" / meta["out_subdir"]
        if raw.exists():
            shutil.rmtree(raw)
        extract_zip(zip_path, raw)
        if args.dataset == "kakadu":
            out = args.out or (ROOT / "data" / "public" / "kakadu")
            convert_kakadu_coco(raw, out)
            normalize_valid_split(out)
            if args.single_class:
                collapse_to_single_class(out)
            write_config(out, single_class=args.single_class)
            print(f"Dataset ready: {out}")
        else:
            root = find_yolo_root(raw)
            normalize_valid_split(root)
            if args.single_class:
                collapse_to_single_class(root)
            write_config(root, single_class=args.single_class)
            print(f"Dataset ready: {root}")
        return 0

    if args.dataset in DIRECT_ZIPS:
        meta = DIRECT_ZIPS[args.dataset]
        cache = ROOT / "data" / "downloads"
        cache.mkdir(parents=True, exist_ok=True)
        zip_path = cache / f"{args.dataset}.zip"
        if not zip_path.exists():
            _download_url(meta["url"], zip_path, meta["label"])
        dest = args.out or (ROOT / "data" / "public" / meta["out_subdir"])
        if dest.exists():
            shutil.rmtree(dest)
        extract_zip(zip_path, dest)
        root = find_yolo_root(dest)
        normalize_valid_split(root)
        write_config(root, single_class=args.single_class)
        print(f"Dataset ready: {root}")
        return 0

    if args.dataset == "synthetic" or args.allow_synthetic:
        dest = args.out or (ROOT / "data" / "public" / "fish")
        create_minimal_dataset(dest)
        write_config(dest)
        print(f"Synthetic dataset: {dest}")
        return 0

    print("No dataset downloaded. Use --list or provide --zip / ROBOFLOW_API_KEY")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
