"""Nettoyage de la phase 7 : ce qui a été retiré doit le rester.

Une suppression sans garde-fou se défait toute seule : quelqu'un recrée le
fichier, un composant QML réapparaît dans une copie générée, un second bouton
« Sauvegarder » repousse dans un autre contrôleur. Ces tests sont des
**assertions d'absence** — bon marché, et le seul moyen de savoir que le
ménage tient.

Ils vérifient aussi la **substance** de la fusion de sauvegarde : la seule
implémentation restante rabat le journal WAL avant de copier la base, sans quoi
la « sauvegarde » d'une base en mode WAL est en retard sur les annotations
qu'elle prétend protéger.
"""

from __future__ import annotations

import sqlite3
import unittest
from pathlib import Path

from tests.helpers import TempDirCase  # noqa: E402

from src.annodb.export_core import wal_checkpoint  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
PYSIDE = REPO_ROOT / "aquameasure-pyside"
QML_SRC = PYSIDE / "qml"
QML_MIRROR = QML_SRC / "AquaMeasure"

# Extensions dans lesquelles une référence pendante compte vraiment. Les notes
# de conception (docs/refonte-donnees, .cursor/plans) parlent au passé de ce
# qui a été retiré : c'est leur rôle, elles sont exclues.
_CODE_SUFFIXES = {".py", ".qml", ".bat", ".spec", ".qrc"}
# `tests` est exclu : ce fichier-ci cite forcément tout ce qu'il traque.
_EXCLUDED_DIRS = {
    ".git", "__pycache__", ".venv", "docs", ".cursor", "node_modules", "tests",
}


def _code_files() -> list[Path]:
    out: list[Path] = []
    for path in REPO_ROOT.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in _CODE_SUFFIXES:
            continue
        if any(part in _EXCLUDED_DIRS for part in path.relative_to(REPO_ROOT).parts):
            continue
        out.append(path)
    return out


def _grep(needle: str) -> list[str]:
    hits: list[str] = []
    for path in _code_files():
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if needle in text:
            hits.append(str(path.relative_to(REPO_ROOT)))
    return sorted(hits)


class AncienneApplicationTest(unittest.TestCase):
    """`fish_registry` / `fish_database` supprimés — `aquameasure.py` conservé."""

    def test_les_deux_modules_pyqt6_ont_disparu(self):
        for name in ("fish_registry.py", "fish_database.py"):
            self.assertFalse((REPO_ROOT / name).exists(), name)

    def test_plus_aucun_code_ne_les_importe(self):
        for needle in ("fish_registry", "fish_database",
                       "FishRegistryTable", "DatabaseTab"):
            with self.subTest(needle=needle):
                # `aquameasure.py` en parle encore, mais en commentaire.
                hits = [
                    path for path in _grep(needle)
                    if not self._only_in_comments(REPO_ROOT / path, needle)
                ]
                self.assertEqual(hits, [], f"{needle} encore référencé")

    @staticmethod
    def _only_in_comments(path: Path, needle: str) -> bool:
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            if needle in line and not line.lstrip().startswith("#"):
                return False
        return True

    def test_aquameasure_reste_car_l_application_l_importe(self):
        """Le supprimer casserait calibration, synchronisation et mesure."""
        self.assertTrue((REPO_ROOT / "aquameasure.py").is_file())
        attendus = {
            "src/backend/calib_service.py": "from aquameasure import",
            "src/backend/sync_service.py": "from aquameasure import",
            "src/backend/measure_service.py": "from aquameasure import",
            "src/controllers/sync_controller.py": "from aquameasure import",
        }
        for rel, needle in attendus.items():
            with self.subTest(rel=rel):
                text = (PYSIDE / rel).read_text(encoding="utf-8")
                self.assertIn(needle, text)


class CheminEcritureConcurrentTest(unittest.TestCase):
    """`fish_track` n'écrit plus en base ; `track_store` sert au CLI."""

    def test_video_tracker_n_a_plus_de_persistance(self):
        source = (REPO_ROOT / "fish_track.py").read_text(encoding="utf-8")
        for interdit in ("_persist_frame", "TrackStore", "session_scope",
                         "persist_db", "close_db"):
            with self.subTest(interdit=interdit):
                self.assertNotIn(f"def {interdit}", source)
                self.assertNotIn(f"{interdit}(", source)

    def test_le_seul_ecrivain_de_pistes_est_le_worker_de_l_application(self):
        worker = (PYSIDE / "src/backend/tracking_worker.py").read_text(encoding="utf-8")
        self.assertIn("add_track_samples_bulk", worker)
        self.assertIn("_flush_pending", worker)

    def test_track_store_reste_pour_le_script_d_inference(self):
        store = REPO_ROOT / "fish-vision" / "src" / "track_store.py"
        self.assertTrue(store.is_file())
        script = REPO_ROOT / "fish-vision" / "scripts" / "infer_track_count.py"
        self.assertIn("from src.track_store import TrackStore",
                      script.read_text(encoding="utf-8"))


class ComposantsQmlMortsTest(unittest.TestCase):
    MORTS = (
        "RibbonContextBar", "RibbonInfoStrip", "RibbonGroup",
        "RibbonSplitButton", "LogDrawer",
        # Fichiers du miroir sans source, purgés lors des vagues précédentes.
        "FlashDetectRangeBar", "SidebarNav",
    )

    def test_ni_source_ni_miroir(self):
        for name in self.MORTS:
            with self.subTest(name=name):
                self.assertFalse(
                    (QML_SRC / "components" / f"{name}.qml").exists(), f"source {name}",
                )
                self.assertFalse(
                    (QML_MIRROR / "components" / f"{name}.qml").exists(),
                    f"miroir {name}",
                )

    def test_aucune_reference_qml_ni_qmldir(self):
        qmldir = (QML_MIRROR / "qmldir").read_text(encoding="utf-8")
        for name in self.MORTS:
            with self.subTest(name=name):
                self.assertNotIn(name, qmldir)
                self.assertEqual(_grep(name), [], f"{name} encore référencé")

    def test_ribbon_button_reste_il_est_utilise(self):
        """La purge s'arrête là où commence le code vivant."""
        self.assertTrue((QML_SRC / "components" / "RibbonButton.qml").is_file())
        transport = (QML_SRC / "components" / "VideoTransportBar.qml").read_text(
            encoding="utf-8",
        )
        self.assertIn("RibbonButton", transport)


class StubExportAndRetrainTest(unittest.TestCase):
    def test_le_slot_a_disparu_et_personne_ne_l_appelle(self):
        controller = (PYSIDE / "src/controllers/data_controller.py").read_text(
            encoding="utf-8",
        )
        self.assertNotIn("def exportAndRetrain", controller)
        for path in QML_SRC.rglob("*.qml"):
            self.assertNotIn(
                "exportAndRetrain", path.read_text(encoding="utf-8"), str(path),
            )


class SauvegardeFusionneeTest(TempDirCase):
    """Une seule implémentation — et c'est la bonne."""

    def test_plus_qu_une_implementation(self):
        db_explorer = (PYSIDE / "src/controllers/db_explorer_controller.py").read_text(
            encoding="utf-8",
        )
        self.assertNotIn("def backupDatabase", db_explorer)
        self.assertNotIn("def _record_backup", db_explorer)
        storage = (PYSIDE / "src/controllers/storage_controller.py").read_text(
            encoding="utf-8",
        )
        self.assertIn("def backupNow", storage)

    def test_les_deux_boutons_appellent_le_meme_slot(self):
        for root in (QML_SRC, QML_MIRROR):
            exports = (root / "pages/data/DataExportTab.qml").read_text(encoding="utf-8")
            settings = (root / "pages/SettingsPage.qml").read_text(encoding="utf-8")
            with self.subTest(root=root.name):
                self.assertIn("Storage.backupNow()", exports)
                self.assertIn("Storage.backupNow()", settings)
                self.assertNotIn("DbExplorer.backupDatabase", exports)

    def test_la_sauvegarde_rabat_le_journal_wal(self):
        """Sans checkpoint, copier le seul `.db` perd les derniers commits."""
        db = self.tmp_path / "base.db"
        conn = sqlite3.connect(db)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("CREATE TABLE obs (id INTEGER PRIMARY KEY, note TEXT)")
            conn.execute("INSERT INTO obs (note) VALUES ('ancienne')")
            conn.commit()
            # Un premier checkpoint pose la table et la 1re ligne dans le `.db` :
            # on isole ainsi ce que le journal retient de la SUITE.
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            conn.commit()

            conn.execute("INSERT INTO obs (note) VALUES ('annotation recente')")
            conn.commit()

            # Copie naïve : la nouvelle ligne vit encore dans `base.db-wal`.
            naive = self.tmp_path / "naive.db"
            naive.write_bytes(db.read_bytes())
            self.assertEqual(self._count(naive), 1)

            # Ce que fait `StorageController.backupNow` avant de copier.
            self.assertTrue(wal_checkpoint(db))
            checkpointed = self.tmp_path / "checkpoint.db"
            checkpointed.write_bytes(db.read_bytes())
            self.assertEqual(self._count(checkpointed), 2)
        finally:
            # Windows : un handle SQLite ouvert empêche le nettoyage du dossier.
            conn.close()

    def test_le_slot_appelle_bien_le_checkpoint(self):
        storage = (PYSIDE / "src/controllers/storage_controller.py").read_text(
            encoding="utf-8",
        )
        self.assertIn("self._checkpoint_wal(db)", storage)
        self.assertIn("from src.annodb.export_core import wal_checkpoint", storage)

    @staticmethod
    def _count(path: Path) -> int:
        conn = sqlite3.connect(path)
        try:
            return conn.execute("SELECT COUNT(*) FROM obs").fetchone()[0]
        finally:
            conn.close()


class ColonnesDepreciesTest(unittest.TestCase):
    """Colonnes non reconstruites, mais plus jamais lues comme si elles vivaient."""

    def test_aucune_lecture_de_is_grazing_sur_l_annotation(self):
        crops = (REPO_ROOT / "fish-vision/scripts/export_crops.py").read_text(
            encoding="utf-8",
        )
        self.assertNotIn('"is_grazing": ann.is_grazing', crops)

    def test_add_spatial_annotation_n_accepte_plus_is_grazing(self):
        import inspect

        from src.annodb.spatial import add_spatial_annotation

        params = inspect.signature(add_spatial_annotation).parameters
        self.assertNotIn("is_grazing", params)

    def test_les_colonnes_restent_en_base_et_sont_documentees(self):
        """Décision superviseur : ne PAS reconstruire les tables."""
        from src.annodb.models import SpatialAnnotation

        for column in ("is_grazing", "cvat_task_id", "cvat_shape_id"):
            self.assertIn(column, SpatialAnnotation.__table__.columns, column)
        models = (REPO_ROOT / "fish-vision/src/annodb/models.py").read_text(
            encoding="utf-8",
        )
        self.assertIn("DÉPRÉCIÉES", models)


if __name__ == "__main__":
    unittest.main()
