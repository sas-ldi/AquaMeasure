from __future__ import annotations

from PySide6.QtCore import QAbstractListModel, QModelIndex, Qt


class GrazingListModel(QAbstractListModel):
    EventIdRole = Qt.UserRole + 1
    TrackIdRole = Qt.UserRole + 2
    FrameStartRole = Qt.UserRole + 3
    FrameEndRole = Qt.UserRole + 4
    EventLabelRole = Qt.UserRole + 5
    EventSymbolRole = Qt.UserRole + 6
    EventColorRole = Qt.UserRole + 7

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
        if role == self.EventIdRole:
            return row.get("event_id", "")
        if role == self.TrackIdRole:
            tid = row.get("external_track_id")
            return str(tid) if tid is not None else "?"
        if role == self.FrameStartRole:
            # Index absolu du fichier source des qu'il est calculable.
            value = row.get("frame_start_abs")
            return value if value is not None else row.get("frame_start", 0)
        if role == self.FrameEndRole:
            value = row.get("frame_end_abs")
            return value if value is not None else row.get("frame_end", 0)
        if role == self.EventLabelRole:
            # Le catalogue event_types n'est plus limite a la broute : la
            # liste doit dire de quel comportement il s'agit.
            return row.get("event_label") or row.get("event_type") or "Broutage"
        if role == self.EventSymbolRole:
            return row.get("event_symbol") or "●"
        if role == self.EventColorRole:
            return row.get("event_color") or "#f59e0b"
        return None

    def roleNames(self):
        return {
            self.EventIdRole: b"eventId",
            self.TrackIdRole: b"trackId",
            self.FrameStartRole: b"frameStart",
            self.FrameEndRole: b"frameEnd",
            self.EventLabelRole: b"eventLabel",
            self.EventSymbolRole: b"eventSymbol",
            self.EventColorRole: b"eventColor",
        }

    def set_rows(self, rows: list[dict]) -> None:
        self.beginResetModel()
        self._rows = list(rows)
        self.endResetModel()

    def rows(self) -> list[dict]:
        """Lignes brutes : la fiche d'une observation doit pouvoir retrouver
        les intervalles de sa propre piste, que le modele n'expose pas role
        par role."""
        return list(self._rows)

    def event_id_at(self, row: int) -> str:
        if 0 <= row < len(self._rows):
            return self._rows[row].get("event_id", "")
        return ""
