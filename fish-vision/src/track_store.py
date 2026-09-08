"""Persist tracking results into annotation database."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from .annodb.projects import get_or_create_project, register_media
from .annodb.tracks import add_track_sample, get_or_create_track


class TrackStore:
    def __init__(
        self,
        session: Session,
        project_name: str = "inference",
        *,
        media_id: Optional[str] = None,
    ):
        self.session = session
        self.project_name = project_name
        self._media_id: Optional[str] = media_id
        self._track_map: Dict[int, str] = {}

    def register_video(self, video_path: str, *, copy_into_store: bool = False) -> str:
        proj = get_or_create_project(self.session, self.project_name)
        media = register_media(
            self.session,
            project_id=proj.id,
            file_path=Path(video_path),
            copy_into_store=copy_into_store,
        )
        self._media_id = media.id
        return media.id

    @property
    def media_id(self) -> Optional[str]:
        return self._media_id

    def add_frame(
        self,
        frame_index: int,
        tracks: List[Tuple[int, float, float, Tuple[float, float, float, float]]],
    ) -> None:
        """
        tracks: list of (external_id, cx_norm, cy_norm, (x1,y1,x2,y2) pixels)
        """
        if not self._media_id:
            raise RuntimeError("Call register_video first")
        for ext_id, cx, cy, bbox in tracks:
            db_track = get_or_create_track(
                self.session,
                media_id=self._media_id,
                external_track_id=ext_id,
                source="bytetrack",
            )
            self._track_map[ext_id] = db_track.id
            add_track_sample(
                self.session,
                track_id=db_track.id,
                frame_index=frame_index,
                cx=cx,
                cy=cy,
                bbox=bbox,
            )

    def get_db_track_id(self, external_id: int) -> Optional[str]:
        return self._track_map.get(external_id)
