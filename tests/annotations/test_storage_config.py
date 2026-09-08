"""Racine des données configurable — phase 6.

Ce qui doit être vrai :

- **sans configuration, rien ne bouge** : les chemins sont exactement ceux
  d'avant (c'est la condition pour que les 259 tests existants restent verts) ;
- la priorité est `FISH_VISION_DB` > configuration > défaut ;
- la racine choisie porte réellement les quatre emplacements ;
- la copie d'une racine copie tout et **ne touche pas** à l'origine ;
- la trace de sauvegarde est écrite et relue.
"""

from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str((Path(__file__).resolve().parents[2] / "src" / "annotations")))

from annotations.helpers import TempDirCase, set_storage_config_env  # noqa: E402


class StorageConfigDefaultsTest(TempDirCase):
    """Sans fichier de configuration : le comportement historique, au bit près."""

    def setUp(self) -> None:
        super().setUp()
        from src.annodb import storage_config

        self.sc = storage_config
        # TempDirCase a déjà dérouté la configuration vers un fichier qui
        # n'existe pas : on est donc dans le cas « aucune configuration ».
        self.assertFalse(self.sc.config_path().is_file())

    def test_aucune_configuration_aucun_deplacement(self):
        repo = Path(__file__).resolve().parents[2] / "src"
        self.assertIsNone(self.sc.configured_data_root())
        self.assertTrue(self.sc.is_default_root())
        self.assertEqual(self.sc.data_root(), repo)
        self.assertEqual(self.sc.camera_params_dir(), repo / "camera_parameters")
        self.assertEqual(self.sc.exports_dir(), repo / "data" / "exports")
        self.assertEqual(self.sc.media_dir(), repo / "data" / "media")
        self.assertEqual(
            self.sc.default_db_path(),
            repo / "annotations" / "data" / "fish_annotations.db",
        )

    def test_get_db_path_sans_configuration_est_le_defaut(self):
        from src.annodb import connection

        previous = os.environ.pop("FISH_VISION_DB", None)
        if previous is not None:
            self.addCleanup(os.environ.__setitem__, "FISH_VISION_DB", previous)
        self.assertEqual(connection.get_db_path(), self.sc.default_db_path())

    def test_config_illisible_ne_casse_rien(self):
        self.sc.config_path().parent.mkdir(parents=True, exist_ok=True)
        self.sc.config_path().write_text("{ ceci n'est pas du JSON", encoding="utf-8")
        self.sc.invalidate_cache()
        self.assertIsNone(self.sc.configured_data_root())
        self.assertEqual(self.sc.data_root(), self.sc.default_data_root())


class StorageConfigRootTest(TempDirCase):
    """Avec une racine configurée : tous les emplacements la suivent."""

    def setUp(self) -> None:
        super().setUp()
        from src.annodb import storage_config

        self.sc = storage_config
        self.root = self.tmp_path / "racine_choisie"
        self.root.mkdir()
        self.sc.set_data_root(self.root)

    def test_la_racine_porte_les_quatre_emplacements(self):
        self.assertEqual(self.sc.data_root(), self.root)
        self.assertFalse(self.sc.is_default_root())
        self.assertEqual(self.sc.configured_db_path(), self.root / "fish_annotations.db")
        self.assertEqual(self.sc.camera_params_dir(), self.root / "camera_parameters")
        self.assertEqual(self.sc.exports_dir(), self.root / "data" / "exports")
        self.assertEqual(self.sc.media_dir(), self.root / "data" / "media")

    def test_env_prioritaire_sur_la_configuration(self):
        from src.annodb import connection

        forced = self.tmp_path / "forcee.db"
        os.environ["FISH_VISION_DB"] = str(forced)
        # TempDbCase n'est pas utilisée ici : on restaure nous-mêmes.
        self.addCleanup(os.environ.pop, "FISH_VISION_DB", None)
        self.assertEqual(connection.get_db_path(), forced)
        self.assertEqual(self.sc.annotations_db_path(), forced)

    def test_configuration_prioritaire_sur_le_defaut(self):
        from src.annodb import connection

        previous = os.environ.pop("FISH_VISION_DB", None)
        if previous is not None:
            self.addCleanup(os.environ.__setitem__, "FISH_VISION_DB", previous)
        self.assertEqual(connection.get_db_path(), self.root / "fish_annotations.db")

    def test_camera_params_candidates_voit_la_racine(self):
        from src.annodb import rectify

        set_camera_env_absent(self)
        candidates = rectify.camera_params_candidates()
        self.assertIn(self.root / "camera_parameters", candidates)
        # Avant le répertoire courant : un choix explicite prime sur un
        # accident de lancement.
        self.assertLess(
            candidates.index(self.root / "camera_parameters"),
            candidates.index(Path.cwd() / "camera_parameters"),
        )

    def test_le_fichier_de_configuration_est_du_json_lisible(self):
        payload = json.loads(self.sc.config_path().read_text(encoding="utf-8"))
        self.assertEqual(payload["data_root"], str(self.root))

    def test_retour_au_defaut(self):
        self.sc.set_data_root(None)
        self.assertTrue(self.sc.is_default_root())
        self.assertEqual(self.sc.data_root(), self.sc.default_data_root())


def set_camera_env_absent(case: unittest.TestCase) -> None:
    from src.annodb.rectify import ENV_CAMERA_PARAMS

    previous = os.environ.pop(ENV_CAMERA_PARAMS, None)
    if previous is not None:
        case.addCleanup(os.environ.__setitem__, ENV_CAMERA_PARAMS, previous)


class StorageValidationTest(TempDirCase):
    def setUp(self) -> None:
        super().setUp()
        from src.annodb import storage_config

        self.sc = storage_config

    def test_racine_creee_si_absente(self):
        target = self.tmp_path / "pas_encore" / "la"
        validated = self.sc.validate_root(target)
        self.assertTrue(validated.is_dir())

    def test_racine_actuelle_refusee(self):
        root = self.tmp_path / "actuelle"
        root.mkdir()
        self.sc.set_data_root(root)
        with self.assertRaises(self.sc.StorageRootError):
            self.sc.validate_root(root)

    def test_racine_imbriquee_refusee(self):
        root = self.tmp_path / "actuelle"
        (root / "dedans").mkdir(parents=True)
        self.sc.set_data_root(root)
        with self.assertRaises(self.sc.StorageRootError):
            self.sc.validate_root(root / "dedans")


class StorageCopyTest(TempDirCase):
    """La copie de racine copie tout, et l'origine reste intacte."""

    def setUp(self) -> None:
        super().setUp()
        from src.annodb import storage_config

        self.sc = storage_config
        self.origin = self.tmp_path / "origine"
        (self.origin / "camera_parameters" / "profiles" / "fast_v2").mkdir(parents=True)
        (self.origin / "data" / "exports" / "jeu_v1").mkdir(parents=True)
        (self.origin / "data" / "media").mkdir(parents=True)
        (self.origin / "fish_annotations.db").write_bytes(b"SQLite format 3\x00base")
        (self.origin / "camera_parameters" / "mtx1.npy").write_bytes(b"npy-1")
        (self.origin / "camera_parameters" / "profiles" / "fast_v2" / "R.npy").write_bytes(b"npy-2")
        (self.origin / "data" / "exports" / "jeu_v1" / "manifest.json").write_text(
            '{"ok": true}', encoding="utf-8"
        )
        (self.origin / "data" / "media" / "vignette.png").write_bytes(b"png")
        self.sc.set_data_root(self.origin)

    def test_copie_complete_et_origine_intacte(self):
        avant = sorted(
            p.relative_to(self.origin).as_posix()
            for p in self.origin.rglob("*") if p.is_file()
        )
        destination = self.tmp_path / "destination"
        seen: list[tuple[int, int]] = []
        report = self.sc.copy_data_tree(
            destination, progress=lambda _n, i, t: seen.append((i, t))
        )

        self.assertEqual(report["copied"], report["file_count"])
        self.assertEqual(report["errors"], [])
        self.assertEqual(report["file_count"], len(avant))
        # La progression a bien été rapportée pour chaque fichier.
        self.assertEqual(len(seen), report["file_count"])

        apres = sorted(
            p.relative_to(destination).as_posix()
            for p in destination.rglob("*") if p.is_file()
        )
        self.assertEqual(apres, avant)
        self.assertEqual(
            (destination / "fish_annotations.db").read_bytes(),
            b"SQLite format 3\x00base",
        )

        # L'origine n'est JAMAIS supprimée : c'est la règle du produit.
        self.assertEqual(
            sorted(
                p.relative_to(self.origin).as_posix()
                for p in self.origin.rglob("*") if p.is_file()
            ),
            avant,
        )

    def test_le_journal_wal_est_copie_avec_la_base(self):
        (self.origin / "fish_annotations.db-wal").write_bytes(b"wal")
        destination = self.tmp_path / "avec_wal"
        self.sc.copy_data_tree(destination)
        self.assertTrue((destination / "fish_annotations.db-wal").is_file())


class BackupTraceTest(TempDirCase):
    def setUp(self) -> None:
        super().setUp()
        from src.annodb import storage_config

        self.sc = storage_config

    def test_aucune_sauvegarde_au_depart(self):
        self.assertEqual(self.sc.last_backup(), {"at": "", "path": ""})
        self.assertIsNone(self.sc.backup_age_days())

    def test_trace_ecrite_et_relue(self):
        out = self.tmp_path / "sauvegarde" / "fish_annotations_20260818.db"
        self.sc.record_backup(out, when="2026-08-18T10:00:00")
        info = self.sc.last_backup()
        self.assertEqual(info["at"], "2026-08-18T10:00:00")
        self.assertEqual(info["path"], str(out))
        self.assertIsNotNone(self.sc.backup_age_days())

    def test_la_trace_ne_perd_pas_la_racine(self):
        root = self.tmp_path / "racine"
        root.mkdir()
        self.sc.set_data_root(root)
        self.sc.record_backup(self.tmp_path / "copie.db")
        self.assertEqual(self.sc.data_root(), root)


class HumanSizeTest(unittest.TestCase):
    def test_lisible_en_francais(self):
        from src.annodb.storage_config import human_size

        self.assertEqual(human_size(0), "0 o")
        self.assertEqual(human_size(512), "512 o")
        self.assertEqual(human_size(1536), "1,5 ko")
        self.assertEqual(human_size(None), "-")


class ConfigLocationTest(TempDirCase):
    def test_le_fichier_ne_depend_pas_du_repertoire_courant(self):
        """Le piège réparé : la configuration vit dans le profil utilisateur."""
        from src.annodb import storage_config

        set_storage_config_env(self, None)
        expected_parent = storage_config.config_dir()
        self.assertEqual(storage_config.config_path().parent, expected_parent)
        self.assertEqual(
            storage_config.config_path().name, storage_config.CONFIG_FILE_NAME
        )
        # Ni le dépôt ni le cwd n'apparaissent dans ce chemin.
        repo = Path(__file__).resolve().parents[2] / "src"
        self.assertFalse(
            str(storage_config.config_path()).startswith(str(repo)),
            "la configuration ne doit pas vivre dans le dépôt",
        )


if __name__ == "__main__":
    unittest.main()
