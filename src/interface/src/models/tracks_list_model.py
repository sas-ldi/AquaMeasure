from __future__ import annotations

from PySide6.QtCore import QAbstractListModel, QModelIndex, Qt


class TracksListModel(QAbstractListModel):
    """Les pistes du media courant, telles que `tracks.track_overview` les rend.

    Les drapeaux sont exposes separement du score : « suspicion 85 » ne dit rien
    a l'operateur, « piste d'une seule frame » lui dit quoi verifier.
    """

    TrackIdRole = Qt.UserRole + 1
    ExternalIdRole = Qt.UserRole + 2
    FirstFrameRole = Qt.UserRole + 3
    LastFrameRole = Qt.UserRole + 4
    SpanRole = Qt.UserRole + 5
    SampleCountRole = Qt.UserRole + 6
    CoverageRole = Qt.UserRole + 7
    TaxonRole = Qt.UserRole + 8
    SingleFrameRole = Qt.UserRole + 9
    HasGapsRole = Qt.UserRole + 10
    VeryLongRole = Qt.UserRole + 11
    SuspicionRole = Qt.UserRole + 12
    MaxGapRole = Qt.UserRole + 13
    EventCountRole = Qt.UserRole + 14
    AnnotationCountRole = Qt.UserRole + 15
    FlagsTextRole = Qt.UserRole + 16

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: list[dict] = []

    def rowCount(self, parent=QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(self._rows)

    @staticmethod
    def _flags_text(row: dict) -> str:
        flags = []
        if row.get("single_frame"):
            flags.append("1 frame")
        if row.get("very_long"):
            flags.append("tres longue")
        if row.get("has_gaps"):
            flags.append(f"trou {int(row.get('max_gap') or 0)} img")
        return " · ".join(flags)

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):
        if not index.isValid() or index.row() >= len(self._rows):
            return None
        row = self._rows[index.row()]
        if role == self.TrackIdRole:
            return row.get("track_id", "")
        if role == self.ExternalIdRole:
            return int(row.get("external_track_id") or 0)
        if role == self.FirstFrameRole:
            return int(row.get("first_frame") or 0)
        if role == self.LastFrameRole:
            return int(row.get("last_frame") or 0)
        if role == self.SpanRole:
            return int(row.get("span") or 0)
        if role == self.SampleCountRole:
            return int(row.get("sample_count") or 0)
        if role == self.CoverageRole:
            return float(row.get("coverage") or 0.0)
        if role == self.TaxonRole:
            return row.get("taxon") or ""
        if role == self.SingleFrameRole:
            return bool(row.get("single_frame"))
        if role == self.HasGapsRole:
            return bool(row.get("has_gaps"))
        if role == self.VeryLongRole:
            return bool(row.get("very_long"))
        if role == self.SuspicionRole:
            return float(row.get("suspicion") or 0.0)
        if role == self.MaxGapRole:
            return int(row.get("max_gap") or 0)
        if role == self.EventCountRole:
            return int(row.get("event_count") or 0)
        if role == self.AnnotationCountRole:
            return int(row.get("annotation_count") or 0)
        if role == self.FlagsTextRole:
            return self._flags_text(row)
        return None

    def roleNames(self):
        return {
            self.TrackIdRole: b"trackId",
            self.ExternalIdRole: b"externalId",
            self.FirstFrameRole: b"firstFrame",
            self.LastFrameRole: b"lastFrame",
            self.SpanRole: b"span",
            self.SampleCountRole: b"sampleCount",
            self.CoverageRole: b"coverage",
            self.TaxonRole: b"taxon",
            self.SingleFrameRole: b"singleFrame",
            self.HasGapsRole: b"hasGaps",
            self.VeryLongRole: b"veryLong",
            self.SuspicionRole: b"suspicion",
            self.MaxGapRole: b"maxGap",
            self.EventCountRole: b"eventCount",
            self.AnnotationCountRole: b"annotationCount",
            self.FlagsTextRole: b"flagsText",
        }

    def set_rows(self, rows: list[dict]) -> None:
        self.beginResetModel()
        self._rows = list(rows)
        self.endResetModel()

    def row_at(self, row: int) -> dict | None:
        if 0 <= row < len(self._rows):
            return self._rows[row]
        return None

    def index_of(self, track_id: str) -> int:
        for i, row in enumerate(self._rows):
            if row.get("track_id") == track_id:
                return i
        return -1
