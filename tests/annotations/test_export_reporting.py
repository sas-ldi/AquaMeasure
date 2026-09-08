"""Exports COCO/YOLO : NA en ignore, aucune annotation perdue, `export_runs` tracé.

Depuis la phase 2, les deux exports passent par le noyau (`export_core`) :
`split_by` est obligatoire, le dossier produit est versionné (son nom est dans
`run.output_path`) et les seuils de classe sont explicites — la fixture ne
compte que deux instances par famille, on abaisse donc les seuils pour tester
le comportement NA et non le filtrage par effectif.
"""

from __future__ import annotations

import json
import unittest
import uuid
from pathlib import Path

from annotations.helpers import TempDbCase, set_camera_params_env, write_test_image

from src.annodb.connection import init_db, session_scope
from src.annodb.export_coco import UNIDENTIFIED_CATEGORY_NAME, export_coco
from src.annodb.export_media import REASON_FICHIER_INTROUVABLE
from src.annodb.export_yolo import export_yolo
from src.annodb.models import (
    ExportRun,
    MediaAsset,
    Project,
    SpatialAnnotation,
    TaxonNode,
)
from src.annodb.spatial import make_bbox_geometry

WIDTH, HEIGHT = 64, 48
FAMILY = "Acanthuridae"

# Fixture minuscule : sans cet abaissement, les seuils par défaut (30
# instances, 2 médias) enverraient toutes les classes en ignore et on ne
# testerait plus le NA.
SMALL = {"min_instances": 1, "min_media": 1}


class ExportFixture(TempDbCase):
    """Trois photos : identifiée, mixte identifiée + NA, et fichier disparu."""

    def setUp(self):
        super().setUp()
        init_db(self.db_path, seed=False)
        # Pas de calibration : l'export doit sortir en 'raw' explicite.
        cam = self.tmp_path / "camera_parameters"
        cam.mkdir(parents=True, exist_ok=True)
        set_camera_params_env(self, cam)

        self.img_a = self.tmp_path / "a.jpg"
        self.img_b = self.tmp_path / "b.jpg"
        write_test_image(self.img_a, WIDTH, HEIGHT)
        write_test_image(self.img_b, WIDTH, HEIGHT)
        self.media_a = str(uuid.uuid4())
        self.media_b = str(uuid.uuid4())
        self.media_c = str(uuid.uuid4())

        geom = json.dumps(make_bbox_geometry(
            8, 6, 40, 30, space="stereo_rectified_left",
            ref_width=WIDTH, ref_height=HEIGHT,
        ))
        with session_scope(self.db_path) as session:
            proj = Project(id=str(uuid.uuid4()), name="test")
            session.add(proj)
            session.flush()
            family = TaxonNode(
                id="fam-acanthuridae", rank="family", scientific_name=FAMILY,
            )
            genus = TaxonNode(
                id="gen-acanthurus", parent_id=family.id, rank="genus",
                scientific_name="Acanthurus",
            )
            species = TaxonNode(
                id="sp-acanthurus-nigrofuscus", parent_id=genus.id, rank="species",
                scientific_name="Acanthurus nigrofuscus",
            )
            # Racine technique « poisson générique » : c'est le taxon posé par
            # l'option NA, il n'a pas d'ancêtre de rang famille.
            generic = TaxonNode(
                id="taxon-fish-generic", rank="provisional", scientific_name="fish",
                is_provisional=True,
            )
            session.add_all([family, genus, species, generic])
            session.flush()

            for mid, path in (
                (self.media_a, self.img_a),
                (self.media_b, self.img_b),
                (self.media_c, self.tmp_path / "jamais_ecrit.jpg"),
            ):
                session.add(MediaAsset(
                    id=mid, project_id=proj.id, media_type="image",
                    rel_path=str(path), width=WIDTH, height=HEIGHT,
                ))
            session.flush()

            session.add_all([
                SpatialAnnotation(
                    id="ann-a1", media_id=self.media_a, frame_index=0,
                    frame_ref="absolute", geom_type="bbox", geometry_json=geom,
                    taxon_node_id=species.id, source="validated",
                ),
                SpatialAnnotation(
                    id="ann-b1", media_id=self.media_b, frame_index=0,
                    frame_ref="absolute", geom_type="bbox", geometry_json=geom,
                    taxon_node_id=species.id, source="validated",
                ),
                # NA « sans taxon du tout »
                SpatialAnnotation(
                    id="ann-b2", media_id=self.media_b, frame_index=0,
                    frame_ref="absolute", geom_type="bbox", geometry_json=geom,
                    taxon_node_id=None, source="validated",
                ),
                # NA « rattaché au poisson générique » (option NA de l'UI)
                SpatialAnnotation(
                    id="ann-b3", media_id=self.media_b, frame_index=0,
                    frame_ref="absolute", geom_type="bbox", geometry_json=geom,
                    taxon_node_id=generic.id, source="validated",
                ),
                # Média dont le fichier a disparu : perte à signaler.
                SpatialAnnotation(
                    id="ann-c1", media_id=self.media_c, frame_index=0,
                    frame_ref="absolute", geom_type="bbox", geometry_json=geom,
                    taxon_node_id=species.id, source="validated",
                ),
            ])


class CocoNaTest(ExportFixture):
    def test_unreviewed_taxa_never_become_scientific_classes(self):
        with session_scope(self.db_path) as session:
            template = session.get(SpatialAnnotation, "ann-a1")
            for ann_id, source in (
                ("ann-model-unreviewed", "model"),
                ("ann-manual-unreviewed", "manual"),
            ):
                session.add(SpatialAnnotation(
                    id=ann_id,
                    media_id=self.media_a,
                    frame_index=0,
                    frame_ref="absolute",
                    geom_type="bbox",
                    geometry_json=template.geometry_json,
                    taxon_node_id=template.taxon_node_id,
                    source=source,
                    identification_status="unreviewed",
                ))

        with session_scope(self.db_path) as session:
            run = export_coco(
                session, self.tmp_path / "unreviewed", split_by="media",
                taxonomy_rank="family", **SMALL,
            )
            out = Path(run.output_path)

        data = json.loads((out / "instances_family.json").read_text(encoding="utf-8"))
        by_ann = {
            ann["attributes"]["spatial_annotation_id"]: ann
            for ann in data["annotations"]
        }
        categories = {row["id"]: row["name"] for row in data["categories"]}
        for ann_id in ("ann-model-unreviewed", "ann-manual-unreviewed"):
            exported = by_ann[ann_id]
            self.assertEqual(exported["ignore"], 1)
            self.assertEqual(
                categories[exported["category_id"]], UNIDENTIFIED_CATEGORY_NAME,
            )
            self.assertFalse(exported["attributes"]["identified"])
            self.assertEqual(
                exported["attributes"]["identification_status"], "unreviewed",
            )

    def test_na_sort_en_ignore_et_non_supprime(self):
        with session_scope(self.db_path) as session:
            run = export_coco(
                session, self.tmp_path / "coco", split_by="media",
                taxonomy_rank="family", **SMALL,
            )
            report = run.report
            out = Path(run.output_path)

        data = json.loads((out / "instances_family.json").read_text(encoding="utf-8"))
        by_ann = {
            a["attributes"]["spatial_annotation_id"]: a for a in data["annotations"]
        }
        # Les quatre annotations des médias présents sont sorties : aucune
        # suppression silencieuse d'un poisson non identifié.
        self.assertEqual(set(by_ann), {"ann-a1", "ann-b1", "ann-b2", "ann-b3"})

        cat_by_id = {c["id"]: c["name"] for c in data["categories"]}
        for na_id in ("ann-b2", "ann-b3"):
            entry = by_ann[na_id]
            self.assertEqual(entry["ignore"], 1, na_id)
            self.assertEqual(entry["iscrowd"], 1, na_id)
            self.assertEqual(cat_by_id[entry["category_id"]], UNIDENTIFIED_CATEGORY_NAME)
            self.assertFalse(entry["attributes"]["identified"])

        identified = by_ann["ann-a1"]
        self.assertEqual(identified["ignore"], 0)
        self.assertEqual(identified["iscrowd"], 0)
        self.assertEqual(cat_by_id[identified["category_id"]], FAMILY)
        self.assertEqual(report["ignored_annotation_count"], 2)

    def test_deux_jeux_de_categories_memes_images_memes_ids(self):
        """`instances_fish.json` et `instances_<rang>.json` doivent coïncider."""
        with session_scope(self.db_path) as session:
            run = export_coco(
                session, self.tmp_path / "coco", split_by="media",
                taxonomy_rank="family", **SMALL,
            )
            out = Path(run.output_path)

        fish = json.loads((out / "instances_fish.json").read_text(encoding="utf-8"))
        family = json.loads((out / "instances_family.json").read_text(encoding="utf-8"))

        self.assertEqual(fish["images"], family["images"])
        self.assertEqual(
            [a["id"] for a in fish["annotations"]],
            [a["id"] for a in family["annotations"]],
        )
        self.assertEqual(
            [a["image_id"] for a in fish["annotations"]],
            [a["image_id"] for a in family["annotations"]],
        )
        self.assertEqual(
            [a["bbox"] for a in fish["annotations"]],
            [a["bbox"] for a in family["annotations"]],
        )
        # Une seule classe, tout inclus, zéro ignore.
        self.assertEqual([c["name"] for c in fish["categories"]], ["fish"])
        self.assertTrue(all(a["ignore"] == 0 for a in fish["annotations"]))
        self.assertEqual(sum(a["ignore"] for a in family["annotations"]), 2)

    def test_annotation_exclue_est_rapportee(self):
        with session_scope(self.db_path) as session:
            run = export_coco(
                session, self.tmp_path / "coco", split_by="media",
                taxonomy_rank="family", **SMALL,
            )
            exclusions = run.exclusions
            report = run.report
            out = Path(run.output_path)

        ids = {row["annotation_id"] for row in exclusions}
        self.assertEqual(ids, {"ann-c1"})
        self.assertEqual(exclusions[0]["reason"], REASON_FICHIER_INTROUVABLE)

        # Retour de fonction, manifeste sur disque et JSON COCO doivent dire
        # la même chose.
        self.assertEqual(report["exclusions"]["count"], 1)
        self.assertIn("1 annotations exclues", report["exclusions"]["summary"])

        on_disk = json.loads((out / "export_report.json").read_text(encoding="utf-8"))
        self.assertEqual(on_disk["exclusions"]["rows"][0]["annotation_id"], "ann-c1")

        data = json.loads((out / "instances_family.json").read_text(encoding="utf-8"))
        self.assertEqual(
            [r["annotation_id"] for r in data["excluded_annotations"]], ["ann-c1"],
        )

    def test_espace_image_declare_dans_les_metadonnees(self):
        with session_scope(self.db_path) as session:
            run = export_coco(
                session, self.tmp_path / "coco", split_by="media",
                taxonomy_rank="family", **SMALL,
            )
            out = Path(run.output_path)

        data = json.loads((out / "instances_family.json").read_text(encoding="utf-8"))
        self.assertEqual(data["info"]["image_space"], "raw")
        self.assertEqual(data["info"]["frame_index_convention"], "absolute")
        for image in data["images"]:
            self.assertEqual(image["image_space"], "raw")
            self.assertEqual(image["frame_ref"], "absolute")

    def test_rang_fish_garde_tout_sans_ignore(self):
        """Détecteur mono-classe : un poisson non identifié reste un poisson."""
        with session_scope(self.db_path) as session:
            run = export_coco(
                session, self.tmp_path / "coco_fish", split_by="media",
                taxonomy_rank="fish", **SMALL,
            )
            report = run.report
            out = Path(run.output_path)

        data = json.loads((out / "instances_fish.json").read_text(encoding="utf-8"))
        self.assertEqual(len(data["annotations"]), 4)
        self.assertTrue(all(a["ignore"] == 0 for a in data["annotations"]))
        self.assertEqual(report["ignored_annotation_count"], 0)
        # Au rang fish, il n'y a pas de second fichier à produire.
        self.assertFalse((out / "instances_family.json").exists())


class YoloNaTest(ExportFixture):
    def test_frame_avec_poisson_non_identifie_ecartee_et_declaree(self):
        with session_scope(self.db_path) as session:
            run = export_yolo(
                session, self.tmp_path / "yolo", split_by="media",
                taxonomy_rank="family", **SMALL,
            )
            report = run.report
            exclusions = run.exclusions
            out = Path(run.output_path)

        dropped = report["unidentified_frames"]
        self.assertEqual(dropped["count"], 1)
        self.assertEqual(dropped["frames"][0]["media_id"], self.media_b)
        self.assertEqual(
            sorted(dropped["frames"][0]["unidentified_annotation_ids"]),
            ["ann-b2", "ann-b3"],
        )

        # Ni image ni label pour la frame écartée, dans le dérivé YOLO.
        written = {p.name for p in (out / "yolo" / "images").rglob("*.jpg")}
        self.assertIn(f"{self.media_a}.jpg", written)
        self.assertNotIn(f"{self.media_b}.jpg", written)
        labels = {p.stem for p in (out / "yolo" / "labels").rglob("*.txt")}
        self.assertEqual(labels, {self.media_a})

        # Le pivot COCO, lui, garde la frame avec son ignore : rien n'est perdu.
        coco = json.loads((out / "instances_family.json").read_text(encoding="utf-8"))
        self.assertEqual(len(coco["images"]), 2)

        # Les trois annotations de la frame écartée sont listées, plus le
        # média disparu : rien ne sort du périmètre sans raison.
        excluded = {row["annotation_id"]: row["reason"] for row in exclusions}
        self.assertEqual(
            set(excluded), {"ann-b1", "ann-b2", "ann-b3", "ann-c1"},
        )
        self.assertEqual(excluded["ann-c1"], REASON_FICHIER_INTROUVABLE)
        self.assertEqual(report["annotation_count"], 1)

    def test_metadonnees_annotations_portent_espace_et_exclusions(self):
        with session_scope(self.db_path) as session:
            run = export_yolo(
                session, self.tmp_path / "yolo", split_by="media",
                taxonomy_rank="family", **SMALL,
            )
            out = Path(run.output_path)

        meta = json.loads((out / "annotations_meta.json").read_text(encoding="utf-8"))
        self.assertEqual(meta["coordinate_frame"]["image_space"], "raw")
        row = next(r for r in meta["annotations"] if r["annotation_id"] == "ann-a1")
        self.assertEqual(row["image_space"], "raw")
        self.assertEqual(row["class"], FAMILY)
        self.assertTrue(meta["excluded_annotations"])

    def test_rang_fish_ne_perd_aucune_frame(self):
        with session_scope(self.db_path) as session:
            run = export_yolo(
                session, self.tmp_path / "yolo_fish", split_by="media",
                taxonomy_rank="fish", **SMALL,
            )
            report = run.report

        self.assertEqual(report["unidentified_frames"]["count"], 0)
        self.assertEqual(report["annotation_count"], 4)
        # Seul le média au fichier disparu manque à l'appel, et il est listé.
        self.assertEqual(
            [r["annotation_id"] for r in report["exclusions"]["rows"]], ["ann-c1"],
        )

    def test_export_run_yolo_trace_espace_split_et_poids(self):
        """`export_runs` doit porter tout ce que le noyau sait (phase 2)."""
        with session_scope(self.db_path) as session:
            run = export_yolo(
                session, self.tmp_path / "yolo_run", split_by="media",
                taxonomy_rank="fish", seed=7, **SMALL,
            )
            run_id = run.id
        with session_scope(self.db_path) as session:
            row = session.get(ExportRun, run_id)
            self.assertEqual(row.format, "yolo")
            # Pas de calibration dans ce test : l'espace est 'raw' et le dit.
            self.assertEqual(row.image_space, "raw")
            self.assertIsNone(row.calibration_profile)
            self.assertEqual(row.image_count, 2)
            self.assertEqual(row.split_strategy, "group_by_media")
            self.assertEqual(row.split_seed, 7)
            self.assertEqual(row.status, "completed")
            self.assertGreater(row.total_bytes or 0, 0)
            # Champs du noyau d'export : désormais renseignés, plus jamais vides.
            self.assertTrue(row.manifest_sha256)
            self.assertTrue(row.db_snapshot_sha256)
            self.assertEqual(row.dataset_version, "1.0.0")
            self.assertTrue(Path(row.output_path).is_dir())

    def test_export_run_coco_trace_espace_et_poids(self):
        with session_scope(self.db_path) as session:
            run = export_coco(
                session, self.tmp_path / "coco_run", split_by="media",
                taxonomy_rank="family", **SMALL,
            )
            run_id = run.id
        with session_scope(self.db_path) as session:
            row = session.get(ExportRun, run_id)
            self.assertEqual(row.format, "coco")
            self.assertEqual(row.image_space, "raw")
            self.assertEqual(row.image_count, 2)
            self.assertEqual(row.status, "completed")
            self.assertGreater(row.total_bytes or 0, 0)


if __name__ == "__main__":
    unittest.main()
