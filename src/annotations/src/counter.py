"""Line and zone crossing counters for fish tracking."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional, Set, Tuple


def _side(px: float, py: float, x1: float, y1: float, x2: float, y2: float) -> float:
    return (x2 - x1) * (py - y1) - (y2 - y1) * (px - x1)


@dataclass
class LineCounter:
    """Count unique track IDs crossing a line segment."""

    x1: float
    y1: float
    x2: float
    y2: float
    normalized: bool = True
    counted_ids: Set[int] = field(default_factory=set)
    _prev_side: Dict[int, float] = field(default_factory=dict)
    total: int = 0

    def _to_abs(self, cx: float, cy: float, w: int, h: int) -> Tuple[float, float]:
        if self.normalized:
            return cx * w, cy * h
        return cx, cy

    def update(self, track_id: int, cx: float, cy: float, img_w: int, img_h: int) -> bool:
        if track_id < 0:
            return False
        px, py = self._to_abs(cx, cy, img_w, img_h)
        lx1, ly1 = self.x1, self.y1
        lx2, ly2 = self.x2, self.y2
        if self.normalized:
            lx1, ly1, lx2, ly2 = lx1 * img_w, ly1 * img_h, lx2 * img_w, ly2 * img_h

        side = _side(px, py, lx1, ly1, lx2, ly2)
        prev = self._prev_side.get(track_id)
        self._prev_side[track_id] = side

        if track_id in self.counted_ids or prev is None:
            return False
        if prev == 0 or side == 0:
            return False
        if prev * side < 0:
            self.counted_ids.add(track_id)
            self.total += 1
            return True
        return False


@dataclass
class ZoneCounter:
    """Count track IDs entering a polygon zone once."""

    polygon: list  # [(x,y)] normalized 0-1 or absolute
    normalized: bool = True
    entered_ids: Set[int] = field(default_factory=set)
    _inside: Dict[int, bool] = field(default_factory=dict)
    total: int = 0

    def _point_in_poly(self, px: float, py: float) -> bool:
        n = len(self.polygon)
        inside = False
        j = n - 1
        for i in range(n):
            xi, yi = self.polygon[i]
            xj, yj = self.polygon[j]
            if ((yi > py) != (yj > py)) and (px < (xj - xi) * (py - yi) / (yj - yi + 1e-9) + xi):
                inside = not inside
            j = i
        return inside

    def update(self, track_id: int, cx: float, cy: float, img_w: int, img_h: int) -> bool:
        if track_id < 0:
            return False
        px, py = (cx * img_w, cy * img_h) if self.normalized else (cx, cy)
        poly = (
            [(x * img_w, y * img_h) for x, y in self.polygon]
            if self.normalized
            else self.polygon
        )
        inside = self._point_in_poly(px, py)
        was_inside = self._inside.get(track_id, False)
        self._inside[track_id] = inside
        if inside and not was_inside and track_id not in self.entered_ids:
            self.entered_ids.add(track_id)
            self.total += 1
            return True
        return False


def parse_line_arg(values: list) -> LineCounter:
    if len(values) != 4:
        raise ValueError("Line requires 4 values: x1 y1 x2 y2 (normalized 0-1)")
    x1, y1, x2, y2 = map(float, values)
    return LineCounter(x1, y1, x2, y2, normalized=True)
