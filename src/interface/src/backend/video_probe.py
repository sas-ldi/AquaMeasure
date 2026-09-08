from __future__ import annotations

from dataclasses import dataclass

import cv2


@dataclass
class VideoMeta:
    valid: bool = False
    frame_count: int = 0
    fps: float = 30.0
    width: int = 0
    height: int = 0


def probe_video(path: str) -> VideoMeta:
    if not path:
        return VideoMeta()
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        cap = cv2.VideoCapture(path, cv2.CAP_FFMPEG)
    if not cap.isOpened():
        return VideoMeta()
    fc = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 30.0)
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    cap.release()
    return VideoMeta(valid=True, frame_count=max(0, fc), fps=fps or 30.0, width=w, height=h)
