"""Installation assistée de SAM 3 : prérequis réels et dépôt du jeton.

Ce que le test protège
----------------------
Le backend exigeait « Python 3.12+ » et le paquet `sam3` de Meta. C'était une
exigence inventée : le dépôt amont déclare `requires-python = ">=3.8"` et
`transformers` implémente SAM 3 nativement dès Python 3.10. La régression à
éviter est le retour de cette contrainte, qui imposerait à tort de migrer tout
l'environnement.

Reste l'accès contrôlé au dépôt `facebook/sam3`, que rien ne doit contourner.
Le logiciel se contente de déposer le jeton là où `huggingface_hub` le relira,
ce que faisait `hf auth login` dans un terminal.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

APP_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = APP_ROOT.parent
for path in (APP_ROOT, REPO_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from fish_detectors.backends import sam3 as sam3_backend  # noqa: E402
from fish_detectors.registry import DetectorStatus  # noqa: E402
from fish_detectors.spec import DetectorSpec  # noqa: E402


def _sans_jeton_en_environnement():
    """Neutralise les variables de jeton, sans toucher au reste."""
    return patch.dict(
        os.environ,
        {name: "" for name in sam3_backend._TOKEN_ENV_VARS},
        clear=False,
    )


class Sam3PrerequisitesTest(unittest.TestCase):
    def test_python_minimum_est_310_pas_312(self):
        self.assertEqual(sam3_backend.MIN_PYTHON, (3, 10))

    def test_environnement_courant_satisfait_la_version_de_python(self):
        """Régression : l'app tourne en 3.10, SAM 3 ne doit plus la refuser."""
        self.assertTrue(
            sam3_backend._python_ok(),
            "SAM 3 declare l'interpreteur courant trop ancien alors que "
            "transformers tourne des Python 3.10",
        )
        self.assertNotIn(
            "python>=3.12",
            sam3_backend.Sam3Backend.missing_requirements(),
        )

    def test_transformers_suffit_sans_le_paquet_de_meta(self):
        with patch.object(sam3_backend, "_has", lambda mod: mod != "sam3"):
            with _sans_jeton_en_environnement():
                with patch.object(sam3_backend, "hf_token_present", lambda: True):
                    self.assertEqual(sam3_backend.Sam3Backend.missing_requirements(), [])

    def test_le_paquet_de_meta_suffit_sans_transformers(self):
        with patch.object(sam3_backend, "_has", lambda mod: mod != "transformers"):
            with patch.object(sam3_backend, "hf_token_present", lambda: True):
                self.assertEqual(sam3_backend.Sam3Backend.missing_requirements(), [])

    def test_transformers_reclame_quand_aucune_voie_n_est_disponible(self):
        with patch.object(
            sam3_backend, "_has", lambda mod: mod not in ("transformers", "sam3"),
        ):
            with patch.object(sam3_backend, "hf_token_present", lambda: True):
                self.assertEqual(
                    sam3_backend.Sam3Backend.missing_requirements(), ["transformers"],
                )


class Sam3TokenTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_hf_home_deroute_le_fichier_de_jeton(self):
        with patch.dict(os.environ, {"HF_HOME": str(self.home / "ailleurs")}):
            self.assertEqual(
                sam3_backend.token_path(), self.home / "ailleurs" / "token",
            )

    def test_chemin_par_defaut_dans_le_cache_huggingface(self):
        with patch.dict(os.environ, {"HF_HOME": ""}):
            with patch.object(sam3_backend.Path, "home", staticmethod(lambda: self.home)):
                self.assertEqual(
                    sam3_backend.token_path(),
                    self.home / ".cache" / "huggingface" / "token",
                )

    def test_jeton_vide_ne_compte_pas(self):
        cible = self.home / "token"
        cible.write_text("   \n", encoding="utf-8")
        with patch.dict(os.environ, {"HF_HOME": str(self.home)}):
            with _sans_jeton_en_environnement():
                with patch.object(sam3_backend.Path, "home", staticmethod(lambda: self.home)):
                    self.assertFalse(sam3_backend.hf_token_present())

    def test_jeton_depose_est_reconnu(self):
        cible = self.home / "token"
        cible.write_text("hf_exemple", encoding="utf-8")
        with patch.dict(os.environ, {"HF_HOME": str(self.home)}):
            with _sans_jeton_en_environnement():
                self.assertTrue(sam3_backend.hf_token_present())


class Sam3ReasonTest(unittest.TestCase):
    """Message affiché au catalogue : le premier obstacle, pas la liste."""

    @staticmethod
    def _reason(missing):
        spec = DetectorSpec(id="sam3-fish", label="SAM 3", backend="sam3")
        return DetectorStatus(spec, None, list(missing), known_backend=True).reason()

    def test_version_de_python_lue_depuis_l_exigence(self):
        """Le message ne doit plus coder « 3.12 » en dur."""
        self.assertIn("3.10", self._reason(["python>=3.10", "transformers"]))

    def test_paquet_avant_acces_hugging_face(self):
        reason = self._reason(["transformers", "huggingface-login"])
        self.assertIn("pip install transformers", reason)
        self.assertNotIn("huggingface-login", reason)

    def test_acces_hugging_face_quand_il_ne_reste_que_lui(self):
        reason = self._reason(["huggingface-login"])
        self.assertIn("Hugging Face", reason)
        self.assertIn("facebook/sam3", reason)


if __name__ == "__main__":
    unittest.main()
