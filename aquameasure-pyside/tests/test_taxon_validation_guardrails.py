"""Garde-fous de l'identification : saisie non enregistrée et cohérence NA.

Deux pertes de données vécues sur le terrain, toutes deux silencieuses :

  • « Enregistrer le poisson » ne validait aucun taxon : il créait une ligne,
    sélectionnait celle-ci, puis remplaçait la saisie en cours par la
    proposition du modèle. Un annotateur qui tapait « NA » puis cliquait
    voyait apparaître une espèce qu'il n'avait jamais choisie.

  • « Genre = NA » avec une espèce binomiale écrivait `genus_is_na = True`
    ET un `genus_id` redéduit du binôme par `ensure_taxon_from_ui`. Le NA
    n'était jamais réaffiché, et aucun export ne pouvait l'interpréter.
"""

from __future__ import annotations

import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from src.controllers.data_controller import _na_conflict  # noqa: E402


class NaConflictTest(unittest.TestCase):
    """La règle est purement syntaxique : ni Qt ni base de données."""

    def test_na_genus_with_a_binomial_species_is_refused(self):
        message = _na_conflict(
            [False, True, False],
            ["Carangidae", "", "Trachinotus goodei"],
        )
        self.assertIn("Genre", message)
        self.assertIn("Trachinotus goodei", message)

    def test_na_family_with_a_genus_is_refused(self):
        self.assertTrue(
            _na_conflict([True, False, False], ["", "Trachinotus", ""])
        )

    def test_an_empty_parent_rank_stays_allowed(self):
        # Vide veut dire « la base le déduira » ; NA est une affirmation
        # d'annotateur. Seule la seconde entre en conflit avec un rang fin.
        self.assertEqual(
            _na_conflict([False, False, False], ["", "", "Trachinotus goodei"]),
            "",
        )

    def test_na_on_the_finest_rank_stays_allowed(self):
        self.assertEqual(
            _na_conflict(
                [False, False, True], ["Carangidae", "Trachinotus", ""]
            ),
            "",
        )

    def test_all_ranks_na_stays_allowed(self):
        # C'est le chemin « non identifiable » : il doit rester ouvert.
        self.assertEqual(_na_conflict([True, True, True], ["", "", ""]), "")


class UnsavedIdentificationLockTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls._tmp = tempfile.TemporaryDirectory(
            prefix="taxon_guardrails_", ignore_cleanup_errors=True
        )
        tmp = Path(cls._tmp.name)
        cls._env = {
            "FISH_VISION_DB": os.environ.get("FISH_VISION_DB"),
            "AQUAMEASURE_STORAGE_CONFIG": os.environ.get(
                "AQUAMEASURE_STORAGE_CONFIG"
            ),
        }
        # La vraie base n'est jamais touchée par les tests.
        os.environ["FISH_VISION_DB"] = str(tmp / "annotations.db")
        os.environ["AQUAMEASURE_STORAGE_CONFIG"] = str(tmp / "storage.json")

        from src.controllers.app_controller import AppController

        cls.controller = AppController()

    @classmethod
    def tearDownClass(cls):
        for key, value in cls._env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        cls._tmp.cleanup()

    def _armed_data(self):
        """Un cadre détecté et sélectionné, aucune saisie en attente."""
        data = self.controller.data()
        fish = self.controller.fish()
        measure = self.controller.measure()
        measure._frame_count = 10
        measure._playing = False
        measure._left = str(Path(self._tmp.name) / "gauche.mp4")
        fish._last_boxes = [{
            "x1": 10.0, "y1": 20.0, "x2": 80.0, "y2": 100.0,
            "track_id": -1, "cls_name": "fish", "conf": 0.9,
        }]
        fish._manual_boxes = []
        fish._publish_overlay()
        data._db_ok = True
        data._busy = False
        data._selected_ann_id = ""
        data._edit_family = ""
        data._edit_genus = ""
        data._edit_species = ""
        data._edit_measurement_mm = 0.0
        data._measurement_pending_ann_ids.clear()
        data._snapshot_row_state()
        data.selectBoxIndex(0)
        return data

    def test_adding_a_row_is_refused_while_an_identification_is_unsaved(self):
        data = self._armed_data()
        data._selected_ann_id = "ann-en-cours"
        data.setGenusFilter("NA")
        self.assertTrue(data.registryRowDirty)

        with patch.object(data, "_db_add_observation") as add:
            data.addObservationFromSelectedBox()

        add.assert_not_called()
        self.assertIn("non enregistrée", data.statusText)

    def test_discarding_edits_restores_the_saved_taxon_and_unlocks_adding(self):
        data = self._armed_data()
        data._selected_ann_id = "ann-en-cours"
        data._edit_family = "Carangidae"
        data._edit_genus = "Trachinotus"
        data._edit_species = "Trachinotus goodei"
        data._snapshot_row_state()

        data.setGenusFilter("NA")
        self.assertTrue(data.registryRowDirty)

        data.discardRowEdits()

        self.assertEqual(data.editGenus, "Trachinotus")
        self.assertFalse(data.registryRowDirty)

        # Le verrou est bien levé : l'ajout repart pour de bon.
        started = threading.Event()

        def _fake_add(payload):
            started.set()
            return {
                "out": {"ann_id": "ann-nouvelle"},
                "manual": True,
                "species": "?",
                "measurement_target": None,
            }

        # La triangulation stéréo n'a rien à voir avec le verrou testé ici :
        # sans vraie vidéo ni calibration, elle échouerait avant le worker.
        with patch.object(
            data, "_enrich_observation_raw",
            side_effect=lambda raw, frame, manual: dict(raw),
        ), patch.object(data, "_db_add_observation", side_effect=_fake_add):
            data.addObservationFromSelectedBox()
            self.assertTrue(
                started.wait(5),
                f"l'ajout n'a jamais été lancé - statut : {data.statusText!r}",
            )

        data._busy = False


if __name__ == "__main__":
    unittest.main()
