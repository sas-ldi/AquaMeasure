from __future__ import annotations

from PySide6.QtCore import QAbstractListModel, QModelIndex, Qt


class SessionsListModel(QAbstractListModel):
    SessionDateRole = Qt.UserRole + 1
    SiteRole = Qt.UserRole + 2
    VideoNameRole = Qt.UserRole + 3
    DurationRole = Qt.UserRole + 4
    GrazingCountRole = Qt.UserRole + 5
    GrazingFreqRole = Qt.UserRole + 6
    MaxFishRole = Qt.UserRole + 7
    SpeciesCountRole = Qt.UserRole + 8
    MediaIdRole = Qt.UserRole + 9

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: list[dict] = []

    def rowCount(self, parent=QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(self._rows)

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):
        if not index.isValid() or index.row() >= len(self._rows):
            return None
        row = self._rows[index.row()]
        if role == self.SessionDateRole:
            return row.get("session_date") or "-"
        if role == self.SiteRole:
            return row.get("site") or "-"
        if role == self.VideoNameRole:
            return row.get("video_name") or "-"
        if role == self.DurationRole:
            d = row.get("duration_s")
            return float(d) if d is not None else 0.0
        if role == self.GrazingCountRole:
            return int(row.get("grazing_count", 0) or 0)
        if role == self.GrazingFreqRole:
            f = row.get("grazing_freq_per_min")
            return float(f) if f is not None else -1.0
        if role == self.MaxFishRole:
            m = row.get("max_fish_on_screen")
            return int(m) if m is not None else -1
        if role == self.SpeciesCountRole:
            return int(row.get("species_count", 0) or 0)
        if role == self.MediaIdRole:
            return row.get("media_id", "") or ""
        return None

    def roleNames(self):
        return {
            self.SessionDateRole: b"sessionDate",
            self.SiteRole: b"site",
            self.VideoNameRole: b"videoName",
            self.DurationRole: b"durationS",
            self.GrazingCountRole: b"grazingCount",
            self.GrazingFreqRole: b"grazingFreq",
            self.MaxFishRole: b"maxFish",
            self.SpeciesCountRole: b"speciesCount",
            self.MediaIdRole: b"mediaId",
        }

    def set_rows(self, rows: list[dict]) -> None:
        self.beginResetModel()
        self._rows = list(rows)
        self.endResetModel()

    def row_at(self, row: int) -> dict | None:
        if 0 <= row < len(self._rows):
            return self._rows[row]
        return None
