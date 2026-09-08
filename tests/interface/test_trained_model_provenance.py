"""Savoir quel modele maison on utilise : rang et date d'entrainement.

Un re-entrainement recopie best.pt vers models/fish_detect_<tag>.pt en
ecrasant le precedent : meme nom, poids differents, et rien a l'ecran ne
disait a quel rang ni quand il avait ete entraine.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

APP_ROOT = Path(__file__).resolve().parents[2] / "src" / "interface"
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from src.controllers.detector_controller import DetectorController  # noqa: E402
from src.controllers.pro_tools_controller import ProToolsController  # noqa: E402


class SignTrainedWeightsTest(unittest.TestCase):
    """Le Hub Pro signe les poids qu'il vient de produire."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.fv = Path(self.tmp.name) / "src/annotations"
        self.models = self.fv / "models"
        self.models.mkdir(parents=True)
        self.pro = ProToolsController()
        self.pro._fv_root = lambda: self.fv

    def tearDown(self):
        self.tmp.cleanup()

    def _weights(self, name: str, content: str = "x") -> Path:
        f = self.models / name
        f.write_text(content, encoding="utf-8")
        return f

    def test_seuls_les_poids_reecrits_sont_signes(self):
        untouched = self._weights("fish_detect_public.pt")
        retrained = self._weights("fish_detect_family.pt")
        before = self.pro._weights_snapshot()

        time.sleep(1.1)  # mtime a la seconde
        retrained.write_text("nouveau", encoding="utf-8")
        self.pro._sign_trained_weights(before, "family")

        self.assertTrue(retrained.with_suffix(".meta.json").is_file())
        self.assertFalse(
            untouched.with_suffix(".meta.json").exists(),
            "un fichier que le job n'a pas touche ne doit pas etre signe",
        )

    def test_le_descripteur_porte_rang_epochs_et_date(self):
        f = self._weights("fish_detect_custom.pt")
        self.pro._epochs = 120
        self.pro._sign_trained_weights({}, "species")
        meta = json.loads(f.with_suffix(".meta.json").read_text(encoding="utf-8"))
        self.assertEqual(meta["rank"], "species")
        self.assertEqual(meta["epochs"], 120)
        self.assertRegex(meta["trained_at"], r"^\d{4}-\d{2}-\d{2}T")

    def test_sans_dossier_models_rien_n_explose(self):
        self.pro._fv_root = lambda: Path(self.tmp.name) / "absent"
        self.assertEqual(self.pro._weights_snapshot(), {})
        self.pro._sign_trained_weights({}, "family")  # ne doit pas lever


class ModelProvenanceTest(unittest.TestCase):
    """Le controleur de detecteurs publie cette provenance."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _row(self, name: str, installed: bool = True) -> dict:
        path = self.dir / name
        path.write_text("w", encoding="utf-8")
        return {"id": "m", "weightsPath": str(path), "installed": installed}

    def test_un_modele_maison_expose_sa_date(self):
        row = DetectorController._with_training_info(self._row("fish_detect_family.pt"))
        self.assertRegex(row["trainedAt"], r"^\d{2}/\d{2}/\d{4}")
        self.assertIn(row["trainedAt"], row["trainingLabel"])

    def test_le_descripteur_ajoute_le_rang_et_les_epochs(self):
        path = self.dir / "fish_detect_custom.pt"
        path.write_text("w", encoding="utf-8")
        path.with_suffix(".meta.json").write_text(
            json.dumps({"rank": "genus", "epochs": 80}), encoding="utf-8"
        )
        row = DetectorController._with_training_info(
            {"id": "m", "weightsPath": str(path), "installed": True}
        )
        self.assertEqual(row["trainedRank"], "genus")
        self.assertIn("rang genus", row["trainingLabel"])
        self.assertIn("80 epochs", row["trainingLabel"])

    def test_un_modele_tiers_n_est_pas_date(self):
        row = DetectorController._with_training_info(self._row("megafishdetector.pt"))
        self.assertEqual(row["trainingLabel"], "")
        self.assertEqual(row["trainedAt"], "")

    def test_un_modele_non_installe_n_est_pas_date(self):
        row = DetectorController._with_training_info(
            self._row("fish_detect_family.pt", installed=False)
        )
        self.assertEqual(row["trainingLabel"], "")

    def test_un_descripteur_illisible_laisse_la_date(self):
        path = self.dir / "fish_detect_family.pt"
        path.write_text("w", encoding="utf-8")
        path.with_suffix(".meta.json").write_text("{pas du json", encoding="utf-8")
        row = DetectorController._with_training_info(
            {"id": "m", "weightsPath": str(path), "installed": True}
        )
        self.assertEqual(row["trainedRank"], "")
        self.assertRegex(row["trainingLabel"], r"^\d{2}/\d{2}/\d{4}")


class CustomModelInCatalogTest(unittest.TestCase):
    """Le modele « rang libre » doit exister dans le catalogue.

    retrain_from_db.py ecrit fish_detect_custom.pt des que le rang n'est pas
    « family ». Sans entree correspondante, ce modele etait produit puis
    introuvable dans l'application.
    """

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_le_catalogue_reference_les_poids_custom(self):
        controller = DetectorController()
        # weightsPath reste vide tant que le fichier n'existe pas : c'est le
        # catalogue lui-meme qu'il faut interroger.
        declared = {
            Path(rel).name
            for spec in controller._registry.specs()
            for rel in (spec.bundled_paths or ())
        }
        self.assertIn(
            "fish_detect_custom.pt", declared,
            "aucune entree du catalogue ne pointe vers fish_detect_custom.pt : "
            "un re-entrainement hors rang famille produirait un modele "
            "inatteignable",
        )

    def test_les_sorties_d_entrainement_sont_couvertes(self):
        """Tout fichier que les scripts ecrivent doit etre atteignable.

        Le modele « familles » a ete retire du catalogue et
        `retrain_from_db.py` n'ecrit plus qu'une seule sortie,
        `fish_detect_custom.pt`, quel que soit le rang demande. Restent donc
        deux noms a couvrir : le generique de secours et le modele maison.
        """
        controller = DetectorController()
        declared = {
            Path(rel).name
            for spec in controller._registry.specs()
            for rel in (spec.bundled_paths or ())
        }
        for name in ("fish_detect_public.pt", "fish_detect_custom.pt"):
            self.assertIn(name, declared, f"{name} n'est reference nulle part")
        self.assertNotIn(
            "fish_detect_family.pt", declared,
            "le modele « familles » a ete retire du catalogue : plus aucune "
            "entree ne doit y renvoyer",
        )


if __name__ == "__main__":
    unittest.main()
