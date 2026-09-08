from __future__ import annotations

from PySide6.QtCore import QAbstractListModel, QModelIndex, Qt


class ObservationsListModel(QAbstractListModel):
    SpeciesLabelRole = Qt.UserRole + 1
    MediaNameRole = Qt.UserRole + 2
    FrameIndexRole = Qt.UserRole + 3
    BboxTextRole = Qt.UserRole + 4
    MeasureTextRole = Qt.UserRole + 5
    PositionTextRole = Qt.UserRole + 6
    AnnIdRole = Qt.UserRole + 7
    MediaIdRole = Qt.UserRole + 8

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
        if role == self.SpeciesLabelRole:
            return row.get("species_label", "?")
        if role == self.MediaNameRole:
            return row.get("media_name", "-")
        if role == self.FrameIndexRole:
            # Index absolu du fichier source des qu'il est calculable.
            abs_frame = row.get("frame_index_abs")
            if abs_frame is None:
                abs_frame = row.get("frame_index", 0)
            return int(abs_frame or 0)
        if role == self.BboxTextRole:
            return row.get("bbox_text", "-")
        if role == self.MeasureTextRole:
            return row.get("measure_text", "-")
        if role == self.PositionTextRole:
            return row.get("position_text", "-")
        if role == self.AnnIdRole:
            return row.get("ann_id", "") or ""
        if role == self.MediaIdRole:
            return row.get("media_id", "") or ""
        return None

    def roleNames(self):
        return {
            self.SpeciesLabelRole: b"speciesLabel",
            self.MediaNameRole: b"mediaName",
            self.FrameIndexRole: b"frameIndex",
            self.BboxTextRole: b"bboxText",
            self.MeasureTextRole: b"measureText",
            self.PositionTextRole: b"positionText",
            self.AnnIdRole: b"annId",
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
