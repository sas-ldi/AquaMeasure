"""Catalogue des jeux publics : la liste vient du script, pas d'une saisie libre.

Le champ « Dataset » etait un texte libre alors que le script n'accepte que
sept noms exacts (argparse `choices`). Une faute de frappe faisait echouer le
telechargement, et rien dans l'application ne disait ou trouver la liste.
"""

from __future__ import annotations

import ast
import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from src.controllers.pro_tools_controller import ProToolsController  # noqa: E402


class ProHubDatasetsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.pro = ProToolsController()

    def _script_choices(self) -> set[str]:
        """Noms acceptes par download_public_dataset.py, lus a la source."""
        script = (
            self.pro._fv_root() / "scripts" / "download_public_dataset.py"
        )
        tree = ast.parse(script.read_text(encoding="utf-8"))
        names: set[str] = set()
        for node in tree.body:
            if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
                if node.targets[0].id in ("DATASETS", "ZENODO", "DIRECT_ZIPS"):
                    names |= set(ast.literal_eval(node.value))
        return names

    def test_le_catalogue_n_est_pas_vide(self):
        self.assertGreaterEqual(len(self.pro.datasetOptions), 5)

    def test_chaque_entree_a_un_identifiant_et_un_libelle(self):
        for opt in self.pro.datasetOptions:
            self.assertTrue(opt["id"], "identifiant vide")
            self.assertIn(opt["id"], opt["label"], "le libelle doit citer l'id")
            self.assertGreater(len(opt["label"]), len(opt["id"]))

    def test_le_catalogue_colle_au_script(self):
        proposed = {o["id"] for o in self.pro.datasetOptions} - {"synthetic"}
        self.assertEqual(
            proposed, self._script_choices(),
            "la liste proposee doit etre exactement celle que le script accepte",
        )

    def test_synthetic_est_propose(self):
        ids = [o["id"] for o in self.pro.datasetOptions]
        self.assertIn("synthetic", ids)

    def test_le_defaut_est_dans_la_liste(self):
        ids = [o["id"] for o in self.pro.datasetOptions]
        self.assertIn(self.pro.datasetName, ids)
        self.assertEqual(ids[self.pro.datasetIndex], self.pro.datasetName)

    def test_choisir_par_index_change_le_dataset(self):
        options = self.pro.datasetOptions
        target = next(
            i for i, o in enumerate(options) if o["id"] != self.pro.datasetName
        )
        self.pro.selectDatasetAt(target)
        self.assertEqual(self.pro.datasetName, options[target]["id"])
        self.assertEqual(self.pro.datasetIndex, target)

    def test_un_index_hors_liste_ne_change_rien(self):
        before = self.pro.datasetName
        self.pro.selectDatasetAt(999)
        self.pro.selectDatasetAt(-1)
        self.assertEqual(self.pro.datasetName, before)


if __name__ == "__main__":
    unittest.main()
