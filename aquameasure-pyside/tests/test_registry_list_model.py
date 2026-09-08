from __future__ import annotations

import sys
import unittest
from pathlib import Path

from PySide6.QtCore import QCoreApplication
from PySide6.QtTest import QSignalSpy


APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from src.models.registry_list_model import RegistryListModel  # noqa: E402


class RegistryListModelTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def test_na_roles_follow_identification_status_with_legacy_fallback(self):
        rows = [
            {"source": "manual", "identification_status": "unidentifiable"},
            {"source": "model", "identification_status": "unidentifiable"},
            {"source": "model", "identification_status": "unreviewed"},
            {"source": "validated", "identification_status": "unreviewed"},
            {"source": "validated"},
        ]
        model = RegistryListModel()
        model.set_rows(rows)
        rank_roles = (
            model.FamilyRole, model.GenusRole, model.SpeciesRole,
        )

        for row_index in (0, 1):
            for role in rank_roles:
                self.assertEqual(model.data(model.index(row_index), role), "NA")
        for role in rank_roles:
            self.assertEqual(model.data(model.index(2), role), "-")
            # Le statut explicite prime sur l'ancienne surcharge de ``source``.
            self.assertEqual(model.data(model.index(3), role), "-")
            # Sans taxon réel, même une source legacy ne devient pas une
            # identification scientifique ni un choix NA inventé.
            self.assertEqual(model.data(model.index(4), role), "-")

    def test_update_by_ann_id_emits_data_changed_without_model_reset(self):
        model = RegistryListModel()
        model.set_rows([
            {"ann_id": "ann-a", "measurement_mm": 10.0},
            {"ann_id": "ann-b", "measurement_mm": 20.0},
        ])
        resets = QSignalSpy(model.modelReset)
        changes = QSignalSpy(model.dataChanged)

        self.assertTrue(model.update_row_by_ann_id(
            "ann-b", {"ann_id": "ann-b", "measurement_mm": 42.0},
        ))

        self.assertEqual(resets.count(), 0)
        self.assertEqual(changes.count(), 1)
        self.assertEqual(changes.at(0)[0].row(), 1)
        self.assertEqual(model.data(model.index(1), model.MeasurementRole), 42.0)
        self.assertEqual(model.row_at(0)["ann_id"], "ann-a")
        self.assertFalse(model.update_row_by_ann_id(
            "absente", {"ann_id": "absente", "measurement_mm": 1.0},
        ))
        self.assertEqual(resets.count(), 0)

    def test_event_summary_distinguishes_counts_flags_and_tracking(self):
        model = RegistryListModel()
        model.set_rows([
            {"track_id": "t1", "track_events": [
                {"key": "bite", "label": "Bouchée", "count": 3},
                {"key": "flee", "label": "Fuite", "count": 2},
            ], "behaviors": [{"key": "bite", "label": "Bouchée"}]},
            {"track_id": "t2"}, {},
        ])
        self.assertEqual(model.data(model.index(0), model.EventSummaryRole),
                         "Bouchée × 3\nFuite × 2\nBouchée × 1 (fiche)\nSuivi × 1")
        self.assertEqual(model.data(model.index(1), model.EventSummaryRole), "Suivi × 1")
        self.assertEqual(model.data(model.index(2), model.EventSummaryRole), "")

    def test_behavior_roles_expose_symbols_labels_and_color(self):
        """Le registre doit dire l'événement, pas seulement le taxon.

        Les comportements ponctuels sont déjà enregistrés sur la ligne : sans
        ces rôles, la colonne « Év. » du panneau resterait vide alors que la
        base sait que le poisson est marqué.
        """
        grazing = {"key": "grazing", "label": "Broutage", "symbol": "🌿",
                   "color": "#22c55e"}
        fleeing = {"key": "fleeing", "label": "Fuite", "symbol": "➤",
                   "color": "#f59e0b"}
        model = RegistryListModel()
        model.set_rows([
            {"ann_id": "a", "behaviors": [grazing, fleeing]},
            {"ann_id": "b", "behaviors": []},
            {"ann_id": "c"},
            {"ann_id": "d", "behaviors": [{"key": "autre"}]},
        ])

        self.assertEqual(
            model.data(model.index(0), model.BehaviorSymbolsRole), "🌿➤")
        self.assertEqual(
            model.data(model.index(0), model.BehaviorLabelsRole),
            "Broutage · Fuite")
        self.assertEqual(
            model.data(model.index(0), model.BehaviorColorRole), "#22c55e")

        # Sans comportement la colonne reste vide et ne teinte rien : sinon
        # toutes les lignes porteraient un pictogramme par defaut.
        for row_index in (1, 2):
            self.assertEqual(
                model.data(model.index(row_index), model.BehaviorSymbolsRole), "")
            self.assertEqual(
                model.data(model.index(row_index), model.BehaviorColorRole), "")

        # Un type sans pictogramme ni couleur reste visible.
        self.assertEqual(
            model.data(model.index(3), model.BehaviorSymbolsRole), "●")
        self.assertEqual(
            model.data(model.index(3), model.BehaviorLabelsRole), "autre")
        self.assertEqual(
            model.data(model.index(3), model.BehaviorColorRole), "#f59e0b")

        names = set(model.roleNames().values())
        self.assertIn(b"behaviorSymbols", names)
        self.assertIn(b"behaviorColor", names)


if __name__ == "__main__":
    unittest.main()
