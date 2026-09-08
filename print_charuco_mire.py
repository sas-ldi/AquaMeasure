#!/usr/bin/env python3
"""CLI — mire ChArUco imprimable (PNG). Voir charuco_board_export.py."""
from __future__ import annotations

import argparse
import os
import sys

from charuco_board_export import (
    DEFAULT_MARKER_RATIO,
    DICT_BY_NAME,
    write_charuco_board_png,
)

ROOT = os.path.dirname(os.path.abspath(__file__))


def main() -> int:
    ap = argparse.ArgumentParser(description="Mire ChArUco imprimable (PNG)")
    ap.add_argument("--cols", type=int, default=5)
    ap.add_argument("--rows", type=int, default=7)
    ap.add_argument("--square-mm", type=float, required=True)
    ap.add_argument("--marker-mm", type=float, default=None)
    ap.add_argument("--marker-ratio", type=float, default=DEFAULT_MARKER_RATIO)
    ap.add_argument("--dpi", type=int, default=300)
    ap.add_argument("--page-w-mm", type=float, default=None)
    ap.add_argument("--page-h-mm", type=float, default=None)
    ap.add_argument("--dict", default="auto")
    ap.add_argument("--out", default=os.path.join(ROOT, "charuco_board.png"))
    args = ap.parse_args()

    marker_mm = args.marker_mm if args.marker_mm is not None else args.square_mm * args.marker_ratio
    dict_name = args.dict if args.dict != "auto" else "DICT_5X5_50"
    auto_dict = args.dict == "auto"
    out_dir = os.path.dirname(os.path.abspath(args.out)) or "."
    filename = os.path.basename(args.out)

    try:
        path, dict_used = write_charuco_board_png(
            out_dir,
            cols=args.cols,
            rows=args.rows,
            square_mm=args.square_mm,
            marker_mm=marker_mm,
            page_w_mm=args.page_w_mm or args.cols * args.square_mm,
            page_h_mm=args.page_h_mm or args.rows * args.square_mm,
            dpi=args.dpi,
            dict_name=dict_name,
            auto_dict=auto_dict,
            filename=filename,
        )
    except (ValueError, OSError) as exc:
        print(f"[ERREUR] {exc}", file=sys.stderr)
        return 1

    board_w = args.cols * args.square_mm
    board_h = args.rows * args.square_mm
    print(f"Grille : {args.cols}x{args.rows}  case={args.square_mm} mm  marker={marker_mm:.2f} mm  {dict_used}")
    print(f"Mire utile : {board_w:.0f}x{board_h:.0f} mm")
    print(f"Fichier : {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
