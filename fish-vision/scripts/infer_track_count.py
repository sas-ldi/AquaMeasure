#!/usr/bin/env python3
"""YOLO detection + ByteTrack tracking + line counting."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List, Tuple

import cv2

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.annodb.connection import get_db_path, init_db, session_scope
from src.counter import LineCounter, ZoneCounter, parse_line_arg
from src.track_store import TrackStore


def extract_tracks_from_result(result, img_w: int, img_h: int) -> List[Tuple[int, float, float, Tuple[float, float, float, float]]]:
    out = []
    if result.boxes is None or len(result.boxes) == 0:
        return out
    boxes = result.boxes.xyxy.cpu().numpy()
    ids = result.boxes.id
    if ids is None:
        return out
    ids = ids.cpu().numpy().astype(int)
    for i, box in enumerate(boxes):
        x1, y1, x2, y2 = box
        cx = (x1 + x2) / 2 / img_w
        cy = (y1 + y2) / 2 / img_h
        out.append((int(ids[i]), cx, cy, (float(x1), float(y1), float(x2), float(y2))))
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Fish detection + tracking + counting")
    parser.add_argument("--model", type=Path, default=ROOT / "models" / "fish_detect_public.pt")
    parser.add_argument("--source", required=True, help="Video path or camera index")
    parser.add_argument("--line", nargs=4, type=float, metavar=("X1", "Y1", "X2", "Y2"))
    parser.add_argument("--zone", nargs="+", type=float, help="Polygon x1 y1 x2 y2 ... normalized")
    parser.add_argument("--tracker", default="bytetrack.yaml")
    parser.add_argument("--conf", type=float, default=0.25)
    parser.add_argument("--output", type=Path, default=None, help="Annotated output video")
    parser.add_argument("--json", type=Path, default=None, help="Results JSON path")
    parser.add_argument("--show", action="store_true")
    parser.add_argument("--save-db", action="store_true", help="Persist tracks to annotation DB")
    parser.add_argument("--project", default="inference", help="DB project name for --save-db")
    parser.add_argument("--max-frames", type=int, default=None)
    args = parser.parse_args()

    model_path = args.model
    if not model_path.exists():
        fallback = ROOT / "models" / "yolo11n.pt"
        if not fallback.exists():
            print("No model found. Train first: python scripts/train_detect.py")
            return 1
        model_path = "yolo11n.pt"

    from ultralytics import YOLO

    model = YOLO(str(model_path))
    source = int(args.source) if args.source.isdigit() else args.source

    line_counter = parse_line_arg(args.line) if args.line else None
    zone_counter = None
    if args.zone and len(args.zone) >= 6:
        pts = list(zip(args.zone[0::2], args.zone[1::2]))
        zone_counter = ZoneCounter(pts, normalized=True)

    cap = cv2.VideoCapture(source)
    if not cap.isOpened():
        print(f"Cannot open source: {source}")
        return 1

    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    writer = None
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        writer = cv2.VideoWriter(str(args.output), fourcc, fps, (w, h))

    trajectories: Dict[int, List[dict]] = {}
    frame_idx = 0
    db_path = get_db_path()

    if args.save_db:
        init_db(db_path)

    with session_scope(db_path) if args.save_db else _nullcontext() as session:
        store = TrackStore(session, args.project) if args.save_db else None
        if store and isinstance(source, str):
            store.register_video(source)

        while cap.isOpened():
            ok, frame = cap.read()
            if not ok:
                break
            if args.max_frames and frame_idx >= args.max_frames:
                break

            results = model.track(
                frame,
                persist=True,
                tracker=args.tracker,
                conf=args.conf,
                verbose=False,
            )
            result = results[0]
            tracks = extract_tracks_from_result(result, w, h)

            if store:
                store.add_frame(frame_idx, tracks)

            for ext_id, cx, cy, bbox in tracks:
                trajectories.setdefault(ext_id, []).append({"frame": frame_idx, "cx": cx, "cy": cy})
                if line_counter:
                    line_counter.update(ext_id, cx, cy, w, h)
                if zone_counter:
                    zone_counter.update(ext_id, cx, cy, w, h)

            annotated = result.plot()
            count = line_counter.total if line_counter else (zone_counter.total if zone_counter else len(trajectories))
            cv2.putText(
                annotated,
                f"Count: {count}",
                (20, 40),
                cv2.FONT_HERSHEY_SIMPLEX,
                1.0,
                (0, 255, 0),
                2,
            )
            if line_counter:
                lx1 = int(line_counter.x1 * w)
                ly1 = int(line_counter.y1 * h)
                lx2 = int(line_counter.x2 * w)
                ly2 = int(line_counter.y2 * h)
                cv2.line(annotated, (lx1, ly1), (lx2, ly2), (0, 0, 255), 2)

            if writer:
                writer.write(annotated)
            if args.show:
                cv2.imshow("fish-vision", annotated)
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break

            frame_idx += 1

    cap.release()
    if writer:
        writer.release()
    cv2.destroyAllWindows()

    summary = {
        "frames": frame_idx,
        "unique_tracks": len(trajectories),
        "line_count": line_counter.total if line_counter else None,
        "zone_count": zone_counter.total if zone_counter else None,
        "trajectories": {str(k): v for k, v in trajectories.items()},
    }

    traj_path = args.json or (ROOT / "data" / "last_inference.json")
    traj_path.parent.mkdir(parents=True, exist_ok=True)
    traj_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k != "trajectories"}, indent=2))
    print(f"Full trajectories: {traj_path}")
    return 0


class _nullcontext:
    def __enter__(self):
        return None

    def __exit__(self, *args):
        return False


if __name__ == "__main__":
    raise SystemExit(main())
