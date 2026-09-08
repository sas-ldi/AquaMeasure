from __future__ import annotations

from PySide6.QtCore import QAbstractListModel, QModelIndex, Qt


class SpeciesSummaryModel(QAbstractListModel):
    ScientificNameRole = Qt.UserRole + 1
    CommonNameRole = Qt.UserRole + 2
    CropCountRole = Qt.UserRole + 3
    SessionCountRole = Qt.UserRole + 4
    InFishialRole = Qt.UserRole + 5
    GalleryRefRole = Qt.UserRole + 6
    IsProvisionalRole = Qt.UserRole + 7
    TaxonIdRole = Qt.UserRole + 8

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
        if role == self.ScientificNameRole:
            return row.get("scientific_name", "") or "-"
        if role == self.CommonNameRole:
            return row.get("common_name", "") or "-"
        if role == self.CropCountRole:
            return int(row.get("crop_count", 0) or 0)
        if role == self.SessionCountRole:
            return int(row.get("session_count", 0) or 0)
        if role == self.InFishialRole:
            return bool(row.get("in_fishial_catalog", False))
        if role == self.GalleryRefRole:
            return int(row.get("gallery_ref_count", 0) or 0)
        if role == self.IsProvisionalRole:
            return bool(row.get("is_provisional", False))
        if role == self.TaxonIdRole:
            return row.get("taxon_node_id", "") or ""
        return None

    def roleNames(self):
        return {
            self.ScientificNameRole: b"scientificName",
            self.CommonNameRole: b"commonName",
            self.CropCountRole: b"cropCount",
            self.SessionCountRole: b"sessionCount",
            self.InFishialRole: b"inFishial",
            self.GalleryRefRole: b"galleryRefCount",
            self.IsProvisionalRole: b"isProvisional",
            self.TaxonIdRole: b"taxonId",
        }

    def set_rows(self, rows: list[dict]) -> None:
        self.beginResetModel()
        self._rows = list(rows)
        self.endResetModel()

    def row_at(self, row: int) -> dict | None:
        if 0 <= row < len(self._rows):
            return self._rows[row]
        return None
