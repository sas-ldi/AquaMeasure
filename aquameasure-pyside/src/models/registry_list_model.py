from __future__ import annotations

from PySide6.QtCore import QAbstractListModel, QModelIndex, Qt


class RegistryListModel(QAbstractListModel):
    FrameRole = Qt.UserRole + 1
    FamilyRole = Qt.UserRole + 2
    GenusRole = Qt.UserRole + 3
    SpeciesRole = Qt.UserRole + 4
    MeasurementRole = Qt.UserRole + 5
    AnnIdRole = Qt.UserRole + 6
    TrackIdRole = Qt.UserRole + 7
    ConfidenceRole = Qt.UserRole + 8
    MediaIdRole = Qt.UserRole + 9
    MediaNameRole = Qt.UserRole + 10
    # Comportements ponctuels posés sur l'observation : le registre affichait
    # le taxon et la longueur, jamais l'événement, alors qu'il est enregistré
    # sur la même ligne en base.
    BehaviorSymbolsRole = Qt.UserRole + 11
    BehaviorLabelsRole = Qt.UserRole + 12
    BehaviorColorRole = Qt.UserRole + 13

    EventSummaryRole = Qt.UserRole + 14

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: list[dict] = []

    def rowCount(self, parent=QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(self._rows)

    @staticmethod
    def _rank_text(row: dict, key: str) -> str:
        """« NA » sur une ligne validee, « - » tant qu'elle ne l'est pas.

        Le rang vide d'une observation validee est un choix de l'observateur
        (non identifiable a ce niveau), pas un champ oublie.
        """
        value = (row.get(key) or "").strip()
        if value:
            return value
        if row.get(f"{key}_is_na") is True:
            return "NA"
        if row.get("identification_status") == "unidentifiable":
            return "NA"
        return "-"

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):
        if not index.isValid() or index.row() >= len(self._rows):
            return None
        row = self._rows[index.row()]
        if role == self.FrameRole:
            # Index absolu du fichier source quand il est calculable : c'est la
            # convention affichee partout ailleurs (statut, lecteur, export).
            abs_frame = row.get("frame_index_abs")
            if abs_frame is not None:
                return abs_frame
            return row.get("frame_index", 0)
        if role == self.FamilyRole:
            return self._rank_text(row, "family")
        if role == self.GenusRole:
            return self._rank_text(row, "genus")
        if role == self.SpeciesRole:
            return self._rank_text(row, "species")
        if role == self.MeasurementRole:
            mm = row.get("measurement_mm")
            return float(mm) if mm is not None else 0.0
        if role == self.AnnIdRole:
            return row.get("ann_id", "")
        if role == self.TrackIdRole:
            tid = row.get("track_id")
            return str(tid) if tid is not None else ""
        if role == self.ConfidenceRole:
            c = row.get("confidence")
            return float(c) if c is not None else 0.0
        if role == self.MediaIdRole:
            return row.get("media_id", "") or ""
        if role == self.MediaNameRole:
            return row.get("media_name", "") or ""
        if role == self.EventSummaryRole:
            return self._event_summary(row)
        if role == self.BehaviorSymbolsRole:
            return "".join(self._behavior_symbols(row))
        if role == self.BehaviorLabelsRole:
            return " · ".join(
                str(flag.get("label") or flag.get("key") or "?")
                for flag in (row.get("behaviors") or [])
            )
        if role == self.BehaviorColorRole:
            # La couleur du premier flag suffit a teinter la colonne : le
            # detail rang par rang se lit sur la fiche de la ligne selectionnee.
            for flag in row.get("behaviors") or []:
                return str(flag.get("color") or "") or "#f59e0b"
            return ""
        return None

    @staticmethod
    def _event_summary(row: dict) -> str:
        # Les étiquettes de fiche restent distinctes des occurrences datées :
        # une étiquette Bouchée ne doit pas gonfler le compteur de la piste.
        parts = [
            f"{event.get('label') or event.get('key') or 'Événement'} × {event['count']}"
            for event in row.get("track_events") or []
        ]
        parts.extend(
            f"{flag.get('label') or flag.get('key') or 'Événement'} × 1 (fiche)"
            for flag in row.get("behaviors") or []
        )
        if row.get("track_id"):
            parts.append("Suivi × 1")
        return "\n".join(parts)

    @staticmethod
    def _behavior_symbols(row: dict) -> list[str]:
        return [
            str(flag.get("symbol") or "") or "●"
            for flag in (row.get("behaviors") or [])
        ]

    def roleNames(self):
        return {
            self.FrameRole: b"frameIndex",
            self.FamilyRole: b"family",
            self.GenusRole: b"genus",
            self.SpeciesRole: b"species",
            self.MeasurementRole: b"measurementMm",
            self.AnnIdRole: b"annId",
            self.TrackIdRole: b"trackId",
            self.ConfidenceRole: b"confidence",
            self.MediaIdRole: b"mediaId",
            self.MediaNameRole: b"mediaName",
            self.EventSummaryRole: b"eventSummary",
            self.BehaviorSymbolsRole: b"behaviorSymbols",
            self.BehaviorLabelsRole: b"behaviorLabels",
            self.BehaviorColorRole: b"behaviorColor",
        }

    def set_rows(self, rows: list[dict]) -> None:
        self.beginResetModel()
        self._rows = list(rows)
        self.endResetModel()

    def update_row_by_ann_id(self, ann_id: str, row: dict) -> bool:
        """Remplace une seule ligne et notifie uniquement ses delegates."""
        target = str(ann_id)
        for position, current in enumerate(self._rows):
            if str(current.get("ann_id") or "") != target:
                continue
            self._rows[position] = dict(row)
            index = self.index(position, 0)
            self.dataChanged.emit(index, index, list(self.roleNames()))
            return True
        return False

    def row_at(self, row: int) -> dict | None:
        if 0 <= row < len(self._rows):
            return self._rows[row]
        return None
