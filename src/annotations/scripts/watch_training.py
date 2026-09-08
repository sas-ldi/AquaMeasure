#!/usr/bin/env python3
"""Affiche la progression YOLO en direct depuis results.csv (style terminal Ultralytics)."""

from __future__ import annotations

import argparse
import csv
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read_epochs(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def format_row(row: dict[str, str], total_epochs: int | None) -> str:
    epoch = int(float(row["epoch"]))
    total = f"/{total_epochs}" if total_epochs else ""
    p = float(row.get("metrics/precision(B)", 0))
    r = float(row.get("metrics/recall(B)", 0))
    m50 = float(row.get("metrics/mAP50(B)", 0))
    m95 = float(row.get("metrics/mAP50-95(B)", 0))
    box = float(row.get("train/box_loss", 0))
    cls = float(row.get("train/cls_loss", 0))
    dfl = float(row.get("train/dfl_loss", 0))
    elapsed = float(row.get("time", 0))
    mins = int(elapsed // 60)
    secs = int(elapsed % 60)
    return (
        f"Epoch {epoch:>2}{total}  |  "
        f"box {box:.3f}  cls {cls:.3f}  dfl {dfl:.3f}  |  "
        f"P {p:.3f}  R {r:.3f}  mAP50 {m50:.3f}  mAP50-95 {m95:.3f}  |  "
        f"{mins:02d}:{secs:02d}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Suivi live entrainement YOLO")
    parser.add_argument(
        "results",
        nargs="?",
        type=Path,
        default=ROOT / "runs" / "detect" / "fish_bootstrap-2" / "results.csv",
    )
    parser.add_argument("--epochs", type=int, default=50, help="Total epochs prevu")
    parser.add_argument("--interval", type=float, default=2.0, help="Poll interval (s)")
    args = parser.parse_args()

    path = args.results if args.results.is_absolute() else ROOT / args.results
    print(f"Watching: {path}")
    print("Ctrl+C pour quitter (n'arrete pas l'entrainement)\n")

    seen = 0
    try:
        while True:
            rows = read_epochs(path)
            if len(rows) > seen:
                for row in rows[seen:]:
                    print(format_row(row, args.epochs))
                seen = len(rows)
                if args.epochs and seen >= args.epochs:
                    print("\nEntrainement termine (toutes les epochs dans results.csv).")
                    best_m50 = max(float(r["metrics/mAP50(B)"]) for r in rows)
                    print(f"Meilleur mAP50 vu: {best_m50:.3f}")
                    print(f"Graphiques: {path.parent / 'results.png'} (genere a la fin par Ultralytics)")
                    return 0
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nArret du suivi (entrainement continue en arriere-plan).")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
