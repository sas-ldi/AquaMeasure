#!/usr/bin/env python3
"""Build family-level YOLO dataset from FishInv (or any multi-class YOLO folder)."""

from __future__ import annotations

import argparse
import shutil
import sys
import zipfile
from collections import Counter
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.download_public_dataset import (
    find_yolo_root,
    normalize_valid_split,
    write_config,
)

# FishInv raw class name -> family scientific_name for YOLO export
FISHINV_TO_FAMILY = {
    "bolbometopon_muricatum": "Scaridae",
    "chaetodontidae": "Chaetodontidae",
    "cheilinus_undulatus": "Labridae",
    "cromileptes_altivelis": "Serranidae",
    "fish": "fish",
    "haemulidae": "Haemulidae",
    "lutjanidae": "Lutjanidae",
    "muraenidae": "Muraenidae",
    "scaridae": "Scaridae",
    "serranidae": "Serranidae",
}

SKIP_CLASSES = {
    "urchin",
    "giant_clam",
    "sea_cucumber",
    "crown_of_thorns",
    "lobster",
}


def _extract_fishinv_raw(dest: Path) -> Path:
    cache = ROOT / "data" / "downloads" / "fishinv.zip"
    if not cache.exists():
        raise FileNotFoundError(f"Missing {cache} — run: python scripts/download_public_dataset.py --dataset fishinv")
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    with zipfile.ZipFile(cache, "r") as zf:
        zf.extractall(dest)
    root = find_yolo_root(dest)
    normalize_valid_split(root)
    return root


def _load_names(root: Path) -> dict[int, str]:
    cfg = yaml.safe_load((root / "data.yaml").read_text(encoding="utf-8")) or {}
    raw = cfg.get("names", [])
    if isinstance(raw, dict):
        return {int(k): str(v) for k, v in raw.items()}
    return {i: str(n) for i, n in enumerate(raw)}


def remap_to_families(src: Path, out: Path) -> Path:
    names = _load_names(src)
    family_names = sorted({FISHINV_TO_FAMILY.get(n, n) for n in names.values() if n not in SKIP_CLASSES})
    if "fish" in family_names:
        family_names.remove("fish")
        family_names = ["fish"] + family_names
    family_to_id = {n: i for i, n in enumerate(family_names)}
    src_to_family = {}
    for cls_id, name in names.items():
        if name in SKIP_CLASSES:
            continue
        src_to_family[cls_id] = family_to_id[FISHINV_TO_FAMILY.get(name, name)]

    if out.exists():
        shutil.rmtree(out)
    stats: Counter[str] = Counter()

    for split in ("train", "val", "valid", "test"):
        src_img = src / split / "images"
        src_lbl = src / split / "labels"
        if not src_img.exists():
            continue
        dst_split = "val" if split == "valid" else split
        dst_img = out / dst_split / "images"
        dst_lbl = out / dst_split / "labels"
        dst_img.mkdir(parents=True, exist_ok=True)
        dst_lbl.mkdir(parents=True, exist_ok=True)

        for img in src_img.iterdir():
            if img.suffix.lower() not in {".jpg", ".jpeg", ".png", ".bmp", ".webp"}:
                continue
            lbl = src_lbl / f"{img.stem}.txt"
            if not lbl.exists():
                continue
            lines = []
            for line in lbl.read_text(encoding="utf-8").strip().splitlines():
                parts = line.split()
                if len(parts) < 5:
                    continue
                src_id = int(parts[0])
                if src_id not in src_to_family:
                    continue
                fam_id = src_to_family[src_id]
                fam_name = family_names[fam_id]
                parts[0] = str(fam_id)
                lines.append(" ".join(parts))
                stats[fam_name] += 1
            if not lines:
                continue
            shutil.copy2(img, dst_img / img.name)
            (dst_lbl / f"{img.stem}.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")

    (out / "data.yaml").write_text(
        yaml.dump(
            {
                "path": out.resolve().as_posix(),
                "train": "train/images",
                "val": "val/images" if (out / "val" / "images").exists() else "valid/images",
                "names": family_names,
                "nc": len(family_names),
            },
            default_flow_style=False,
        ),
        encoding="utf-8",
    )
    write_config(out, single_class=False, family_config=True)
    print(f"Family dataset: {out} ({len(family_names)} classes)")
    for name, count in stats.most_common():
        print(f"  {name}: {count} instances")
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Build family-level YOLO dataset from FishInv")
    parser.add_argument("--out", type=Path, default=ROOT / "data" / "public" / "fish_families")
    parser.add_argument("--src", type=Path, default=None, help="Existing multi-class YOLO root (optional)")
    args = parser.parse_args()

    src = args.src
    if src is None:
        raw = ROOT / "data" / "public" / "_fishinv_raw"
        src = _extract_fishinv_raw(raw)
    remap_to_families(src, args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
