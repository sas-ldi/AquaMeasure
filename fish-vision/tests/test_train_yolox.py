"""`train_yolox.py` — filtre projet, layout VOC, dossier de travail.

Trois défauts prouvés par la revue adversariale de la phase 2 : un filtre par
nom comparé à des UUID (l'export sortait vide), un layout VOC aplati qui rendait
faux tous les `file_name` du COCO, et un dossier `voc/` écrit **dans** l'export
scellé par son manifeste.
"""

from __future__ import annotations

import importlib.util
import json
import unittest
import uuid
from pathlib import Path

from tests.helpers import TempDbCase, TempDirCase

from src.annodb.connection import init_db, session_scope
from src.annodb.models import Project
from src.annodb.projects import resolve_project_ids

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def _load_train_yolox():
    """Charge le script comme un module (`scripts/` n'est pas un paquet)."""
    spec = importlib.util.spec_from_file_location(
        "train_yolox_sous_test", SCRIPTS / "train_yolox.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ResolveProjectIdsTest(TempDbCase):
    """Un nom de projet doit devenir un identifiant, pas filtrer dans le vide."""

    def setUp(self):
        super().setUp()
        init_db(self.db_path, seed=False)
        self.project_id = str(uuid.uuid4())
        with session_scope(self.db_path) as session:
            session.add(Project(id=self.project_id, name="Sortie mai"))

    def test_nom_resolu_en_identifiant(self):
        with session_scope(self.db_path) as session:
            self.assertEqual(
                resolve_project_ids(session, ["Sortie mai"]), [self.project_id],
            )

    def test_identifiant_accepte_tel_quel(self):
        with session_scope(self.db_path) as session:
            self.assertEqual(
                resolve_project_ids(session, [self.project_id]), [self.project_id],
            )

    def test_sans_filtre_renvoie_none(self):
        with session_scope(self.db_path) as session:
            self.assertIsNone(resolve_project_ids(session, None))
            self.assertIsNone(resolve_project_ids(session, []))

    def test_nom_inconnu_ne_filtre_pas_tout(self):
        """Une liste vide filtrerait TOUT : on préfère ne pas filtrer et le dire."""
        with session_scope(self.db_path) as session:
            self.assertIsNone(resolve_project_ids(session, ["projet fantôme"]))
            self.assertEqual(
                resolve_project_ids(session, ["projet fantôme", "Sortie mai"]),
                [self.project_id],
            )

    def test_le_script_utilise_bien_le_resolveur(self):
        """Le bug était de passer `args.projects` (des noms) tel quel."""
        module = _load_train_yolox()
        self.assertIs(module.resolve_project_ids, resolve_project_ids)
        source = (SCRIPTS / "train_yolox.py").read_text(encoding="utf-8")
        self.assertIn("project_ids = resolve_project_ids(session, args.projects)", source)
        self.assertNotIn("project_ids=args.projects", source)


class VocLayoutTest(TempDirCase):
    """Le layout VOC doit rester compatible avec les `file_name` du COCO."""

    def _fake_export(self) -> tuple[Path, Path]:
        export_dir = self.tmp_path / "exports" / "yolox_fish_v1.0.0_20260817_abcdef12"
        images = {"train": ["m0_10.jpg", "m0_20.jpg"], "val": ["m1_5.jpg"]}
        coco_images = []
        for index, (split, names) in enumerate(images.items()):
            for name in names:
                path = export_dir / "images" / split / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"jpeg")
                coco_images.append({
                    "id": len(coco_images) + 1,
                    "file_name": f"{split}/{name}",
                    "width": 64, "height": 48,
                })
        ann = export_dir / "instances_fish.json"
        ann.write_text(json.dumps({
            "images": coco_images, "annotations": [], "categories": [],
        }), encoding="utf-8")
        (export_dir / "manifest.json").write_text("{}", encoding="utf-8")
        return export_dir, ann

    def test_arborescence_des_splits_conservee(self):
        module = _load_train_yolox()
        export_dir, ann = self._fake_export()
        voc = module.voc_dir_for(export_dir)
        module._write_voc_layout(export_dir, ann, voc)

        payload = json.loads(ann.read_text(encoding="utf-8"))
        for image in payload["images"]:
            cible = voc / "images" / image["file_name"]
            self.assertTrue(cible.is_file(), f"{image['file_name']} introuvable")
        self.assertTrue((voc / "images" / "train" / "m0_10.jpg").is_file())
        self.assertTrue((voc / "images" / "val" / "m1_5.jpg").is_file())
        # Et surtout pas la version aplatie qui cassait les chemins.
        self.assertFalse((voc / "images" / "m0_10.jpg").exists())
        self.assertTrue((voc / "instances_train.json").is_file())
        self.assertTrue((voc / "instances_val.json").is_file())

    def test_image_manquante_est_signalee(self):
        module = _load_train_yolox()
        export_dir, ann = self._fake_export()
        (export_dir / "images" / "val" / "m1_5.jpg").unlink()
        with self.assertRaises(FileNotFoundError) as ctx:
            module._write_voc_layout(export_dir, ann, module.voc_dir_for(export_dir))
        self.assertIn("val/m1_5.jpg", str(ctx.exception))

    def test_voc_ecrit_hors_du_dossier_scelle(self):
        """`manifest.json` liste les fichiers de l'export : rien ne s'y ajoute."""
        module = _load_train_yolox()
        export_dir, ann = self._fake_export()
        voc = module.voc_dir_for(export_dir)
        self.assertEqual(voc.parent, export_dir.parent)
        self.assertNotIn(export_dir, voc.parents)

        avant = sorted(p.relative_to(export_dir).as_posix()
                       for p in export_dir.rglob("*") if p.is_file())
        module._write_voc_layout(export_dir, ann, voc)
        apres = sorted(p.relative_to(export_dir).as_posix()
                       for p in export_dir.rglob("*") if p.is_file())
        self.assertEqual(avant, apres, "l'export scellé a été modifié")


if __name__ == "__main__":
    unittest.main()
