from __future__ import annotations

from PySide6.QtCore import QAbstractListModel, QModelIndex, Qt


class SessionRowsModel(QAbstractListModel):
    """Sessions de terrain - une ligne = une sortie avec plusieurs prises.

    À distinguer de `SessionsListModel`, qui liste des **médias** pour
    l'explorateur de la page Données.
    """

    SessionIdRole = Qt.UserRole + 1
    NameRole = Qt.UserRole + 2
    SiteRole = Qt.UserRole + 3
    SessionDateRole = Qt.UserRole + 4
    StatusRole = Qt.UserRole + 5
    StatusLabelRole = Qt.UserRole + 6
    ObservationCountRole = Qt.UserRole + 7
    MeasurementCountRole = Qt.UserRole + 8
    EventCountRole = Qt.UserRole + 9
    HasPairRole = Qt.UserRole + 10
    FilesReadyRole = Qt.UserRole + 11
    VideosLabelRole = Qt.UserRole + 12
    PairCountRole = Qt.UserRole + 13
    MediaCountRole = Qt.UserRole + 14

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: list[dict] = []

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        if parent.isValid():
            return 0
        return len(self._rows)

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):
        if not index.isValid() or index.row() >= len(self._rows):
            return None
        row = self._rows[index.row()]
        if role == self.SessionIdRole:
            return row.get("session_id", "")
        if role == self.NameRole:
            return row.get("name") or "(sans nom)"
        if role == self.SiteRole:
            return row.get("site") or "-"
        if role == self.SessionDateRole:
            return (row.get("session_date") or "")[:10] or "-"
        if role == self.StatusRole:
            return row.get("status", "planned")
        if role == self.StatusLabelRole:
            return row.get("status_label", "")
        if role == self.ObservationCountRole:
            return int(row.get("observation_count", 0) or 0)
        if role == self.MeasurementCountRole:
            return int(row.get("measurement_count", 0) or 0)
        if role == self.EventCountRole:
            return int(row.get("event_count", 0) or 0)
        if role == self.HasPairRole:
            return bool(row.get("has_pair"))
        if role == self.FilesReadyRole:
            # Les fichiers sont-ils encore là ? Une session reste consultable
            # sans ses vidéos, mais on ne peut plus revoir les images.
            return bool(row.get("left_available")) and bool(row.get("right_available"))
        if role == self.VideosLabelRole:
            return self._videos_label(row)
        if role == self.PairCountRole:
            return int(row.get("pair_count", 0) or 0)
        if role == self.MediaCountRole:
            return int(row.get("media_count", 0) or 0)
        return None

    @staticmethod
    def _videos_label(row: dict) -> str:
        pair_count = int(row.get("pair_count", 0) or 0)
        if pair_count > 1:
            missing = sum(
                int(not bool(side.get("available")))
                for pair in (row.get("pairs") or [])
                for side in (pair.get("left") or {}, pair.get("right") or {})
                if side.get("media_id")
            )
            suffix = f" · {missing} fichier(s) manquant(s)" if missing else ""
            return f"{pair_count} paires vidéo{suffix}"
        left = (row.get("left") or {}).get("name", "")
        right = (row.get("right") or {}).get("name", "")
        if not left and not right:
            return "Aucune vidéo pour l'instant"
        if left and right:
            return f"{left}  ·  {right}"
        return left or right

    def roleNames(self):  # noqa: N802
        return {
            self.SessionIdRole: b"sessionId",
            self.NameRole: b"name",
            self.SiteRole: b"site",
            self.SessionDateRole: b"sessionDate",
            self.StatusRole: b"status",
            self.StatusLabelRole: b"statusLabel",
            self.ObservationCountRole: b"observationCount",
            self.MeasurementCountRole: b"measurementCount",
            self.EventCountRole: b"eventCount",
            self.HasPairRole: b"hasPair",
            self.FilesReadyRole: b"filesReady",
            self.VideosLabelRole: b"videosLabel",
            self.PairCountRole: b"pairCount",
            self.MediaCountRole: b"mediaCount",
        }

    def set_rows(self, rows: list[dict]) -> None:
        self.beginResetModel()
        self._rows = list(rows)
        self.endResetModel()

    def row_at(self, row: int) -> dict | None:
        if 0 <= row < len(self._rows):
            return self._rows[row]
        return None

    def index_of(self, session_id: str) -> int:
        for i, row in enumerate(self._rows):
            if row.get("session_id") == session_id:
                return i
        return -1
