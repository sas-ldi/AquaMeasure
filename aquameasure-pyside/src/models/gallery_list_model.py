from __future__ import annotations

from PySide6.QtCore import QAbstractListModel, QModelIndex, Qt


class GalleryListModel(QAbstractListModel):
    NameRole = Qt.UserRole + 1
    RankRole = Qt.UserRole + 2
    CropCountRole = Qt.UserRole + 3
    RefCountRole = Qt.UserRole + 4
    TaxonIdRole = Qt.UserRole + 5
    InCatalogRole = Qt.UserRole + 6
    PromotionEligibleRole = Qt.UserRole + 7
    PromotionReasonRole = Qt.UserRole + 8
    PromotionMinRefsRole = Qt.UserRole + 9
    AnnotationCountRole = Qt.UserRole + 10
    PendingCountRole = Qt.UserRole + 11

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
        if role == self.NameRole:
            return row.get("scientific_name", "") or "-"
        if role == self.RankRole:
            return row.get("rank", "") or ""
        if role == self.CropCountRole:
            return int(row.get("crop_count", 0) or 0)
        if role == self.RefCountRole:
            return int(row.get("gallery_ref_count", 0) or 0)
        if role == self.TaxonIdRole:
            return row.get("taxon_node_id", "") or ""
        if role == self.InCatalogRole:
            return bool(row.get("in_fishial_catalog", False))
        if role == self.PromotionEligibleRole:
            return bool(row.get("promotion_eligible", False))
        if role == self.PromotionReasonRole:
            return row.get("promotion_reason", "") or ""
        if role == self.PromotionMinRefsRole:
            return int(row.get("promotion_min_refs", 0) or 0)
        if role == self.AnnotationCountRole:
            return int(row.get("annotation_count", row.get("crop_count", 0)) or 0)
        if role == self.PendingCountRole:
            return self._pending_count(row)
        return None

    def roleNames(self):
        return {
            self.NameRole: b"scientificName",
            self.RankRole: b"rank",
            self.CropCountRole: b"cropCount",
            self.RefCountRole: b"refCount",
            self.TaxonIdRole: b"taxonId",
            self.InCatalogRole: b"inCatalog",
            self.PromotionEligibleRole: b"promotionEligible",
            self.PromotionReasonRole: b"promotionReason",
            self.PromotionMinRefsRole: b"promotionMinRefs",
            self.AnnotationCountRole: b"annotationCount",
            self.PendingCountRole: b"pendingCount",
        }

    @staticmethod
    def _pending_count(row: dict) -> int:
        return max(0, int(row.get("pending_reference_count", 0) or 0))

    def pending_totals(self) -> tuple[int, int]:
        counts = [self._pending_count(row) for row in self._rows
                  if row.get("promotion_eligible") and self._pending_count(row) > 0]
        return sum(counts), len(counts)

    def set_rows(self, rows: list[dict]) -> None:
        self.beginResetModel()
        self._rows = [
            row for row in rows
            if row.get("rank", "species") == "species"
        ]
        self.endResetModel()

    def taxon_id_at(self, row: int) -> str:
        if 0 <= row < len(self._rows):
            return str(self._rows[row].get("taxon_node_id", "") or "")
        return ""
