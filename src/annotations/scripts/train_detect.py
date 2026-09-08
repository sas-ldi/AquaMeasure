#!/usr/bin/env python3
"""Train YOLO fish detector (public or custom export)."""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def resolve_data_yaml(path: Path) -> Path:
    path = Path(path)
    if not path.is_absolute():
        path = ROOT / path
    if not path.exists():
        raise FileNotFoundError(f"Data config not found: {path}")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="Train YOLO fish detector")
    parser.add_argument("--data", type=Path, default=ROOT / "configs" / "data_public.yaml")
    parser.add_argument("--model", default="yolo11n.pt")
    parser.add_argument("--epochs", type=int, default=None)
    parser.add_argument("--imgsz", type=int, default=None)
    parser.add_argument("--batch", type=int, default=None)
    parser.add_argument("--device", default=None)
    parser.add_argument("--name", default=None)
    parser.add_argument("--profile", choices=["default", "bootstrap", "finetune", "family_finetune"], default="default",
                        help="Preset from train_detect.yaml (bootstrap=public, finetune=custom DB export)")
    parser.add_argument("--config", type=Path, default=ROOT / "configs" / "train_detect.yaml")
    args = parser.parse_args()

    cfg = {}
    if args.config.exists():
        cfg = yaml.safe_load(args.config.read_text(encoding="utf-8")) or {}

    profile_cfg = {}
    if args.profile != "default" and args.profile in cfg:
        profile_cfg = cfg.get(args.profile, {}) or {}

    data_yaml = resolve_data_yaml(args.data)
    if args.profile == "bootstrap" and args.data == ROOT / "configs" / "data_public.yaml":
        pass  # keep public
    elif args.profile == "finetune" and args.data == ROOT / "configs" / "data_public.yaml":
        custom = ROOT / "configs" / "data_custom.yaml"
        if custom.exists():
            data_yaml = resolve_data_yaml(custom)
    elif args.profile == "family_finetune" and args.data == ROOT / "configs" / "data_public.yaml":
        family = ROOT / "configs" / "data_family.yaml"
        if family.exists():
            data_yaml = resolve_data_yaml(family)

    device = args.device if args.device is not None else profile_cfg.get("device", cfg.get("device", 0))
    try:
        import torch
        if str(device) not in ("cpu", "mps") and not torch.cuda.is_available():
            device = "cpu"
            print("CUDA unavailable — training on CPU")
    except ImportError:
        device = "cpu"

    train_cfg = {
        "data": str(data_yaml),
        "model": args.model or profile_cfg.get("model", cfg.get("model", "yolo11n.pt")),
        "epochs": args.epochs or profile_cfg.get("epochs", cfg.get("epochs", 50)),
        "imgsz": args.imgsz or profile_cfg.get("imgsz", cfg.get("imgsz", 640)),
        "batch": args.batch or profile_cfg.get("batch", cfg.get("batch", 16)),
        "patience": profile_cfg.get("patience", cfg.get("patience", 20)),
        "device": device,
        "workers": profile_cfg.get("workers", cfg.get("workers", 4)),
        "amp": profile_cfg.get("amp", cfg.get("amp", True)),
        "cache": profile_cfg.get("cache", cfg.get("cache", False)),
        "project": str(ROOT / profile_cfg.get("project", cfg.get("project", "runs/detect"))),
        "name": args.name or profile_cfg.get("name", cfg.get("name", "fish_train")),
    }

    print(f"Training profile: {args.profile}")
    print(f"Dataset: {data_yaml}")
    print(f"Model: {train_cfg['model']} | epochs={train_cfg['epochs']} | batch={train_cfg['batch']} | device={device}")

    from ultralytics import YOLO

    model = YOLO(train_cfg["model"])
    results = model.train(**train_cfg)

    best = Path(results.save_dir) / "weights" / "best.pt"
    models_dir = ROOT / "models"
    models_dir.mkdir(exist_ok=True)
    if best.exists():
        if "custom" in str(data_yaml):
            tag = "custom"
        elif "family" in str(data_yaml) or args.profile == "family_finetune":
            tag = "family"
        else:
            tag = "public"
        dest = models_dir / f"fish_detect_{tag}.pt"
        shutil.copy2(best, dest)
        print(f"Best weights copied to {dest}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
