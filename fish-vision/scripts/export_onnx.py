#!/usr/bin/env python3
"""Export trained YOLO model to ONNX for C++/Qt integration (phase 2)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description="Export YOLO to ONNX")
    parser.add_argument("--model", type=Path, default=ROOT / "models" / "fish_detect_public.pt")
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--opset", type=int, default=12)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    if not args.model.exists():
        print(f"Model not found: {args.model}")
        return 1

    from ultralytics import YOLO

    model = YOLO(str(args.model))
    export_path = model.export(
        format="onnx",
        imgsz=args.imgsz,
        opset=args.opset,
        dynamic=False,
        simplify=True,
    )

    export_path = Path(export_path)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        export_path.replace(args.out)
        export_path = args.out

    print(f"ONNX exported: {export_path}")
    print("Declarer ce fichier dans le catalogue avec le backend 'onnx'.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
