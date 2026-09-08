"""Noyau d'export (phase 2) : split par groupe, NA, classes, manifeste, YOLO.

Les règles testées ici ne sont pas des détails de format : un groupe partagé
entre deux splits fait fuiter la réponse et gonfle les métriques de 20 à 40
points ; un poisson non identifié dans le test rend l'évaluation fausse ; un
export non reproductible n'est pas un jeu de données scientifique.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import unittest
import uuid
from datetime import datetime
from pathlib import Path
from unittest import mock

from tests.helpers import (
    TempDbCase,
    dispose_engine,
    set_camera_params_env,
    write_fake_calibration,
    write_test_image,
)

from src.annodb import export_core
from src.annodb.connection import init_db, session_scope
from src.annodb.export_core import (
    ExportIntegrityError,
    ExportRun,
    apply_frame_stride,
    assert_no_group_leak,
    assert_val_test_fully_identified,
    assert_written_layout_matches_plan,
    clamp_box_to_image,
    export_dataset,
    latest_export_run,
    load_replay_splits,
    sha256_file,
    suggest_split_by,
    wal_checkpoint,
)
from src.annodb.models import CaptureSession, MediaAsset, Project, SpatialAnnotation, TaxonNode
from src.annodb.spatial import make_bbox_geometry

WIDTH, HEIGHT = 64, 48
STAMP = datetime(2026, 8, 17, 9, 0, 0)

FAMILY_A = "Acanthuridae"
FAMILY_B = "Lutjanidae"


def _geometry() -> str:
    return json.dumps(make_bbox_geometry(
        8, 6, 40, 30, space="stereo_rectified_left",
        ref_width=WIDTH, ref_height=HEIGHT,
    ))


def _write_video(path: Path, frames: int = 12):
    """Petite vidéo lisible par OpenCV (MJPG) : indispensable au test de stride."""
    import cv2
    import numpy as np

    path.parent.mkdir(parents=True, exist_ok=True)
    writer = cv2.VideoWriter(
        str(path), cv2.VideoWriter_fourcc(*"MJPG"), 10, (WIDTH, HEIGHT),
    )
    for i in range(frames):
        frame = np.zeros((HEIGHT, WIDTH, 3), dtype=np.uint8)
        frame[:, :, 0] = (i * 17) % 255
        frame[:, :, 1] = 90
        writer.write(frame)
    writer.release()
    return path


class PhotoFixture(TempDbCase):
    """Huit photos, deux familles, une frame non identifiée.

    - `m00`..`m04` : famille A, trois espèces d'un même genre (15 instances)
    - `m05`, `m06` : famille B (6 instances, 2 médias) — rare, sans genre
    - `m07` : famille A (2 instances) + un poisson NA (option « non identifié »)
    """

    ANNOTATIONS_PER_MEDIA = 3

    def setUp(self):
        super().setUp()
        init_db(self.db_path, seed=False)
        cam = self.tmp_path / "camera_parameters"
        cam.mkdir(parents=True, exist_ok=True)
        set_camera_params_env(self, cam)

        geom = _geometry()
        self.media_ids = [f"m{i:02d}" for i in range(8)]
        with session_scope(self.db_path) as session:
            project = Project(id=str(uuid.uuid4()), name="photos")
            session.add(project)
            session.flush()
            self.project_id = project.id

            nodes = [
                TaxonNode(id="fam-a", rank="family", scientific_name=FAMILY_A),
                TaxonNode(id="gen-a", parent_id="fam-a", rank="genus",
                          scientific_name="Acanthurus"),
                TaxonNode(id="sp-a", parent_id="gen-a", rank="species",
                          scientific_name="Acanthurus nigrofuscus"),
                TaxonNode(id="sp-a2", parent_id="gen-a", rank="species",
                          scientific_name="Acanthurus chirurgus"),
                TaxonNode(id="sp-a3", parent_id="gen-a", rank="species",
                          scientific_name="Acanthurus lineatus"),
                TaxonNode(id="fam-b", rank="family", scientific_name=FAMILY_B),
                # Espèce rattachée directement à sa famille : aucun genre
                # intermédiaire, le repli doit sauter le rang manquant.
                TaxonNode(id="sp-b", parent_id="fam-b", rank="species",
                          scientific_name="Lutjanus argentiventris"),
                TaxonNode(id="taxon-fish-generic", rank="provisional",
                          scientific_name="fish", is_provisional=True),
            ]
            session.add_all(nodes)
            session.flush()

            for index, media_id in enumerate(self.media_ids):
                image = self.tmp_path / "photos" / f"{media_id}.jpg"
                write_test_image(image, WIDTH, HEIGHT)
                session.add(MediaAsset(
                    id=media_id, project_id=project.id, media_type="image",
                    rel_path=str(image), width=WIDTH, height=HEIGHT,
                ))
                session.flush()
                for k in range(self.ANNOTATIONS_PER_MEDIA):
                    if index in (5, 6):
                        taxon = "sp-b"
                    elif index == 7 and k == 0:
                        taxon = "taxon-fish-generic"  # NA
                    else:
                        # Trois espèces du même genre, toutes trop rares pour
                        # tenir seules : c'est le cas d'usage du repli.
                        taxon = ("sp-a2", "sp-a", "sp-a3")[k]
                    session.add(SpatialAnnotation(
                        id=f"ann-{media_id}-{k}", media_id=media_id, frame_index=0,
                        frame_ref="absolute", geom_type="bbox", geometry_json=geom,
                        taxon_node_id=taxon, source="validated",
                    ))

    def export(self, name: str = "run", **kwargs):
        options = {
            "split_by": "media",
            "taxonomy_rank": "family",
            "min_instances": 5,
            "min_media": 2,
            "created_at": STAMP,
            "dataset_name": "test",
        }
        options.update(kwargs)
        with session_scope(self.db_path) as session:
            return export_dataset(session, self.tmp_path / name, **options)


class SplitIntegrityTest(PhotoFixture):
    def test_groupe_partage_leve_une_erreur_bloquante(self):
        """L'assertion anti-fuite doit nommer les groupes fautifs."""
        with self.assertRaises(ExportIntegrityError) as ctx:
            assert_no_group_leak(
                [("media:m01", "train"), ("media:m01", "val"), ("media:m02", "test")],
                context="test",
            )
        message = str(ctx.exception)
        self.assertIn("media:m01", message)
        self.assertIn("train", message)
        self.assertIn("val", message)
        self.assertNotIn("media:m02", message)

    def test_splits_json_incoherent_refuse_au_rejeu(self):
        """Un découpage rejoué qui fait fuiter un groupe est refusé net."""
        result = self.export("initial")
        splits_path = result.output_dir / "splits.json"
        payload = json.loads(splits_path.read_text(encoding="utf-8"))
        # On force la fuite : un même groupe dans deux splits.
        leaked = sorted(payload["groups"])[0]
        payload["group_ids"]["train"] = [leaked]
        payload["group_ids"]["val"] = [leaked]
        corrupted = self.tmp_path / "splits_corrompu.json"
        corrupted.write_text(json.dumps(payload), encoding="utf-8")

        with self.assertRaises(ExportIntegrityError) as ctx:
            self.export("rejeu_casse", replay_splits=corrupted)
        self.assertIn(leaked, str(ctx.exception))

    def test_rejeu_impossible_avec_un_autre_grain(self):
        result = self.export("initial")
        splits_path = result.output_dir / "splits.json"
        with self.assertRaises(ExportIntegrityError) as ctx:
            self.export("autre_grain", split_by="site", replay_splits=splits_path)
        self.assertIn("split_by", str(ctx.exception))

    def test_aucun_groupe_dans_deux_splits(self):
        result = self.export()
        splits = json.loads((result.output_dir / "splits.json").read_text(encoding="utf-8"))
        seen: dict[str, str] = {}
        for split, groups in splits["group_ids"].items():
            for group in groups:
                self.assertNotIn(group, seen, f"{group} déjà dans {seen.get(group)}")
                seen[group] = split

    def test_split_par_session_garde_la_paire_entiere(self):
        """Deux médias d'une même session ne peuvent pas être séparés.

        `strict_grouping=None` : six des huit photos de ce jeu d'essai n'ont
        aucune session, ce qui dépasse le seuil de refus. On lève le refus pour
        vérifier la règle de regroupement elle-même — et on contrôle au passage
        que les six replis sont bien comptés dans le manifeste.
        """
        with session_scope(self.db_path) as session:
            session.add(CaptureSession(
                id="sess-1", name="Paire", site="Récif Nord",
                session_date=datetime(2026, 5, 1), status="done",
                left_media_id="m00", right_media_id="m05",
            ))
        result = self.export(
            "par_session", split_by="session", min_instances=1, min_media=1,
            strict_grouping=None,
        )
        splits = json.loads((result.output_dir / "splits.json").read_text(encoding="utf-8"))
        placement = {
            group: split
            for split, groups in splits["group_ids"].items() for group in groups
        }
        self.assertIn("session:sess-1", placement)
        images = json.loads(
            (result.output_dir / "instances_fish.json").read_text(encoding="utf-8")
        )["images"]
        by_media = {img["media_id"]: img["split"] for img in images}
        self.assertEqual(by_media["m00"], by_media["m05"])

        degraded = result.manifest["split"]["degraded_groups"]
        self.assertEqual(degraded["count"], 6)
        self.assertEqual(degraded["media_total"], 8)
        self.assertEqual(degraded["requested_split_by"], "session")
        self.assertNotIn("m00", degraded["media"])
        self.assertNotIn("m05", degraded["media"])


class NaRuleTest(PhotoFixture):
    def test_na_jamais_en_val_ni_en_test(self):
        result = self.export("na")
        data = json.loads(
            (result.output_dir / "instances_family.json").read_text(encoding="utf-8")
        )
        split_of_image = {img["id"]: img["split"] for img in data["images"]}
        for annotation in data["annotations"]:
            if annotation["ignore"] == 1:
                self.assertEqual(
                    split_of_image[annotation["image_id"]], "train",
                    "un poisson non identifié est sorti hors de train",
                )
        # Et la règle est déclarée dans le manifeste, pas seulement appliquée.
        na_rule = result.manifest["split"]["na_rule"]
        self.assertGreaterEqual(na_rule["groups_forced_to_train"], 1)
        self.assertIn("media:m07", result.manifest["split"]["forced_train_groups"])

    def test_val_et_test_integralement_identifies(self):
        result = self.export("na2")
        composition = result.manifest["split"]["composition"]
        self.assertEqual(composition["val"]["ignored_annotations"], 0)
        self.assertEqual(composition["test"]["ignored_annotations"], 0)

    def test_rejeu_ne_peut_pas_placer_un_groupe_na_en_val(self):
        """La règle NA prime sur le rejeu — sinon la fuite revient par la porte.

        Défaut prouvé par la revue : le rejeu affectait les groupes **avant** la
        règle NA, et la boucle NA passait son tour sur tout groupe déjà affecté.
        Un `splits.json` d'hier plaçant en val un groupe devenu NA y restait, et
        le manifeste annonçait `groups_forced_to_train = 0`.
        """
        reference = self.export("na_ref")
        payload = json.loads(
            (reference.output_dir / "splits.json").read_text(encoding="utf-8")
        )
        groups = dict(payload["groups"])
        self.assertEqual(groups.get("media:m07"), "train", "m07 est le groupe NA")
        groups["media:m07"] = "val"  # le découpage rejoué contamine le val
        replay = self.tmp_path / "splits_na_en_val.json"
        replay.write_text(
            json.dumps({"split_by": "media", "seed": 1, "groups": groups}),
            encoding="utf-8",
        )

        result = self.export("na_rejeu", replay_splits=replay)

        splits = json.loads(
            (result.output_dir / "splits.json").read_text(encoding="utf-8")
        )
        self.assertEqual(splits["groups"]["media:m07"], "train")
        na_rule = result.manifest["split"]["na_rule"]
        self.assertGreaterEqual(na_rule["groups_forced_to_train"], 1)
        self.assertEqual(na_rule["groups_moved_from_replay"], 1)
        override = na_rule["replay_overrides"][0]
        self.assertEqual(override["group"], "media:m07")
        self.assertEqual(override["replayed_split"], "val")
        self.assertEqual(override["applied_split"], "train")
        # Et le résultat livré tient la promesse : aucun ignore hors de train.
        composition = result.manifest["split"]["composition"]
        self.assertEqual(composition["val"]["ignored_annotations"], 0)
        self.assertEqual(composition["test"]["ignored_annotations"], 0)

    def test_ignore_en_val_arrete_l_export(self):
        """Assertion finale : un `ignore` livré en val/test est bloquant.

        On sabote le plan de split *après* son calcul pour fabriquer le cas que
        la règle NA est censée rendre impossible : l'export doit s'arrêter net
        plutôt que livrer un jeu d'évaluation contaminé.
        """
        vrai_plan = export_core.plan_splits

        def plan_sabote(*args, **kwargs):
            plan = vrai_plan(*args, **kwargs)
            plan.split_of_group["media:m07"] = "val"
            plan.forced_train_groups = [
                g for g in plan.forced_train_groups if g != "media:m07"
            ]
            return plan

        with mock.patch.object(export_core, "plan_splits", plan_sabote):
            with self.assertRaises(ExportIntegrityError) as ctx:
                self.export("na_sabote")
        message = str(ctx.exception)
        self.assertIn("val", message)
        self.assertIn("Règle NA", message)

    def test_assertion_val_test_est_verifiable_isolement(self):
        """Le verrou lit la composition livrée, pas l'intention du plan."""
        propre = {
            "train": {"ignored_annotations": 3},
            "val": {"ignored_annotations": 0},
            "test": {"ignored_annotations": 0},
        }
        assert_val_test_fully_identified(propre)  # ne lève pas
        sale = dict(propre, test={"ignored_annotations": 1})
        with self.assertRaises(ExportIntegrityError) as ctx:
            assert_val_test_fully_identified(sale, context="rang family")
        self.assertIn("test", str(ctx.exception))
        self.assertIn("rang family", str(ctx.exception))


class ClassPlanTest(PhotoFixture):
    def test_classes_construites_depuis_les_donnees(self):
        """Jamais les 780 classes du rang : seulement ce qui existe vraiment."""
        result = self.export("classes")
        stats = result.manifest["class_stats"]
        self.assertEqual(stats["names"], [FAMILY_A, FAMILY_B])
        self.assertEqual(stats["stats"][FAMILY_A]["instances"], 17)
        self.assertEqual(stats["stats"][FAMILY_A]["media"], 6)
        self.assertEqual(stats["stats"][FAMILY_B]["instances"], 6)
        self.assertEqual(stats["stats"][FAMILY_B]["media"], 2)

    def test_repli_des_classes_sous_le_seuil(self):
        """Une espèce trop rare remonte au genre, puis à la famille."""
        result = self.export(
            "repli", taxonomy_rank="species", min_instances=10, min_media=2,
        )
        stats = result.manifest["class_stats"]
        # Les trois espèces d'Acanthurus sont sous le seuil : elles se replient
        # sur le genre, qui totalise alors 17 instances et passe. Lutjanus n'a
        # pas de genre dans l'arbre — le repli saute le rang manquant et atterrit
        # sur la famille, encore sous le seuil (6 < 10) → ignore.
        self.assertEqual(stats["names"], ["Acanthurus"])
        self.assertEqual(stats["stats"]["Acanthurus"]["instances"], 17)
        outcomes = {(row["from"], row["to"]) for row in stats["fallbacks"]}
        self.assertIn(("Acanthurus nigrofuscus", "Acanthurus"), outcomes)
        self.assertIn(("Acanthurus chirurgus", "Acanthurus"), outcomes)
        self.assertIn(("Lutjanus argentiventris", FAMILY_B), outcomes)
        self.assertIn((FAMILY_B, None), outcomes)
        self.assertEqual(stats["ignored_instances"], 7)  # 6 Lutjanidae + 1 NA

    def test_seuils_par_defaut_ne_gardent_rien_ici(self):
        """Seuils par défaut (30 instances, 2 médias) : tout part en ignore."""
        result = self.export("seuils", min_instances=30, min_media=2)
        self.assertEqual(result.manifest["class_stats"]["names"], [])
        data = json.loads(
            (result.output_dir / "instances_family.json").read_text(encoding="utf-8")
        )
        self.assertTrue(all(a["ignore"] == 1 for a in data["annotations"]))
        # Aucune image ne peut alors entrer dans val ou test.
        composition = result.manifest["split"]["composition"]
        self.assertEqual(composition["val"]["images"], 0)
        self.assertEqual(composition["test"]["images"], 0)


class YoloDerivationTest(PhotoFixture):
    def test_yolo_est_coherent_avec_le_coco_du_meme_export(self):
        result = self.export("yolo", fmt="yolo")
        root = result.output_dir
        coco = json.loads((root / "instances_family.json").read_text(encoding="utf-8"))
        names = result.manifest["class_stats"]["names"]

        images = {img["id"]: img for img in coco["images"]}
        ignored_images = {
            a["image_id"] for a in coco["annotations"] if a["ignore"] == 1
        }
        expected = {
            Path(img["file_name"]).stem for img in coco["images"]
            if img["id"] not in ignored_images
        }
        written = {p.stem for p in (root / "yolo" / "images").rglob("*.jpg")}
        self.assertEqual(written, expected)
        self.assertTrue(ignored_images, "le test n'a plus de cas ignore")

        for image_id, image in images.items():
            stem = Path(image["file_name"]).stem
            label = root / "yolo" / "labels" / image["split"] / f"{stem}.txt"
            if image_id in ignored_images:
                self.assertFalse(label.exists(), f"{stem} aurait dû être écarté")
                continue
            lines = [l for l in label.read_text(encoding="utf-8").splitlines() if l]
            boxes = [a for a in coco["annotations"] if a["image_id"] == image_id]
            self.assertEqual(len(lines), len(boxes))
            for line, box in zip(lines, boxes):
                parts = line.split()
                category = next(
                    c["name"] for c in coco["categories"] if c["id"] == box["category_id"]
                )
                self.assertEqual(names[int(parts[0])], category)
                x, y, w, h = box["bbox"]
                self.assertAlmostEqual(float(parts[1]), (x + w / 2) / image["width"], places=5)
                self.assertAlmostEqual(float(parts[2]), (y + h / 2) / image["height"], places=5)
                self.assertAlmostEqual(float(parts[3]), w / image["width"], places=5)
                self.assertAlmostEqual(float(parts[4]), h / image["height"], places=5)

    def test_data_yaml_reprend_la_class_map_reelle(self):
        import yaml

        result = self.export("yaml", fmt="yolo")
        cfg = yaml.safe_load(
            (result.output_dir / "yolo" / "data.yaml").read_text(encoding="utf-8")
        )
        self.assertEqual(
            [cfg["names"][i] for i in sorted(cfg["names"])],
            result.manifest["class_stats"]["names"],
        )
        self.assertEqual(cfg["train"], "images/train")
        # Pas de chemin absolu : le dataset reste déplaçable et l'export
        # reproductible au bit près.
        self.assertNotIn("path", cfg)


class ManifestTest(PhotoFixture):
    REQUIRED = (
        "schema", "dataset_name", "dataset_version", "created_at", "git_commit",
        "format", "taxonomy_rank", "source", "coordinate_frame", "split",
        "class_stats", "exclusions", "files", "content_sha256",
    )

    def test_manifeste_complet(self):
        result = self.export("manifeste", fmt="yolo")
        manifest = json.loads(
            (result.output_dir / "manifest.json").read_text(encoding="utf-8")
        )
        for field in self.REQUIRED:
            self.assertIn(field, manifest)
        self.assertEqual(manifest["dataset_version"], "1.0.0")
        self.assertTrue(manifest["source"]["db_snapshot_sha256"])
        self.assertEqual(manifest["source"]["filter"], {"project_ids": None})
        self.assertEqual(manifest["coordinate_frame"]["frame_index_convention"], "absolute")
        self.assertIn("image_space", manifest["coordinate_frame"])
        self.assertEqual(manifest["split"]["split_by"], "media")
        self.assertEqual(manifest["split"]["seed"], 42)

        # Chaque fichier écrit porte son empreinte et sa taille.
        on_disk = {
            p.relative_to(result.output_dir).as_posix()
            for p in result.output_dir.rglob("*") if p.is_file()
        } - {"manifest.json"}
        self.assertEqual({row["path"] for row in manifest["files"]}, on_disk)
        for row in manifest["files"]:
            self.assertEqual(len(row["sha256"]), 64)
            self.assertGreater(row["bytes"], 0)

    def test_empreinte_de_base_prise_apres_checkpoint_wal(self):
        """L'empreinte scellée doit décrire la base, journal WAL compris.

        Le moteur SQLAlchemy garde sa connexion ouverte : les écritures de la
        fixture sont commitées mais **pas encore rabattues** dans le `.db`.
        Sans checkpoint, le manifeste scellait donc un état antérieur.
        """
        avant = sha256_file(self.db_path)
        result = self.export("wal")
        source = result.manifest["source"]
        self.assertIs(source["db_snapshot_wal_checkpointed"], True)
        self.assertEqual(len(source["db_snapshot_sha256"]), 64)
        self.assertNotEqual(
            source["db_snapshot_sha256"], avant,
            "l'empreinte doit inclure les commits restés dans le journal WAL",
        )

    def test_version_hors_charte_refusee(self):
        """Nom et version forment le dossier : pas de séparateur de chemin."""
        for bad in ("../ailleurs", "1.0.0/x", ""):
            with self.assertRaises(ValueError):
                self.export("mauvais", dataset_version=bad)

    def test_nom_de_dossier_porte_version_date_et_empreinte(self):
        result = self.export("nommage")
        name = result.output_dir.name
        self.assertTrue(name.startswith("test_v1.0.0_20260817_"), name)
        self.assertTrue(name.endswith(result.manifest["content_sha256"][:8]))

    def test_export_run_complet(self):
        result = self.export("run")
        run_id = result.run.id
        with session_scope(self.db_path) as session:
            row = session.get(ExportRun, run_id)
            self.assertEqual(row.status, "completed")
            self.assertEqual(row.split_strategy, "group_by_media")
            self.assertEqual(row.dataset_name, "test")
            self.assertEqual(row.dataset_version, "1.0.0")
            self.assertTrue(row.db_snapshot_sha256)
            self.assertEqual(Path(row.output_path), result.output_dir)
            self.assertEqual(latest_export_run(session).id, run_id)
            # L'empreinte enregistrée doit être celle du fichier réellement là.
            self.assertEqual(
                row.manifest_sha256, sha256_file(result.output_dir / "manifest.json"),
            )

    def test_export_echoue_laisse_un_run_failed_et_rien_d_autre(self):
        root = self.tmp_path / "echec"
        with self.assertRaises(ExportIntegrityError):
            self.export("echec", split_by="site", replay_splits=self._bad_replay())
        with session_scope(self.db_path) as session:
            statuses = [row.status for row in session.query(ExportRun).all()]
        self.assertIn("failed", statuses)
        # Aucun dossier laissé derrière : ni export, ni staging.
        self.assertEqual(sorted(p.name for p in root.iterdir()) if root.exists() else [], [])

    def _bad_replay(self) -> Path:
        path = self.tmp_path / "replay_incompatible.json"
        path.write_text(
            json.dumps({"split_by": "media", "seed": 1, "groups": {"media:m00": "train"}}),
            encoding="utf-8",
        )
        return path


class DegradedGroupingTest(PhotoFixture):
    """Le grain demandé doit être tenu, ou dit — jamais dégradé en silence."""

    def _set_sites(self, media_ids, site: str) -> None:
        with session_scope(self.db_path) as session:
            for media_id in media_ids:
                session.get(MediaAsset, media_id).site = site

    def test_grain_site_sans_aucune_metadonnee_est_refuse(self):
        """100 % de replis : l'export s'arrête au lieu d'annoncer group_by_site."""
        with self.assertRaises(ExportIntegrityError) as ctx:
            self.export("site_vide", split_by="site")
        message = str(ctx.exception)
        self.assertIn("site", message)
        self.assertIn("strict_grouping", message)

    def test_replis_comptes_et_listes_dans_le_manifeste(self):
        result = self.export("site_libre", split_by="site", strict_grouping=None)
        degraded = result.manifest["split"]["degraded_groups"]
        self.assertEqual(degraded["count"], 8)
        self.assertEqual(degraded["media_total"], 8)
        self.assertEqual(degraded["ratio"], 1.0)
        self.assertEqual(degraded["requested_split_by"], "site")
        self.assertEqual(degraded["fallback_split_by"], "media")
        self.assertEqual(sorted(degraded["media"]), sorted(self.media_ids))
        self.assertEqual(
            sorted(degraded["groups"]), [f"media:{m}" for m in sorted(self.media_ids)],
        )
        # Le rapport le dit aussi : l'écran n'est pas la seule trace.
        self.assertEqual(
            result.report["split"]["degraded_groups"]["count"], 8,
        )

    def test_sous_le_seuil_l_export_passe_et_compte_les_replis(self):
        self._set_sites(["m00", "m01", "m02", "m03", "m04"], "Récif Nord")
        result = self.export("site_partiel", split_by="site")
        degraded = result.manifest["split"]["degraded_groups"]
        self.assertEqual(degraded["count"], 3)
        self.assertEqual(degraded["ratio"], 0.375)
        self.assertEqual(sorted(degraded["media"]), ["m05", "m06", "m07"])
        groupes = json.loads(
            (result.output_dir / "splits.json").read_text(encoding="utf-8")
        )["groups"]
        self.assertIn("site:Récif Nord", groupes)

    def test_seuil_parametrable(self):
        """`strict_grouping` est un seuil, pas un interrupteur."""
        self._set_sites(["m00", "m01", "m02", "m03", "m04"], "Récif Nord")
        # 37,5 % de replis : refusé à 30 %, accepté à 50 %.
        with self.assertRaises(ExportIntegrityError):
            self.export("seuil_bas", split_by="site", strict_grouping=0.3)
        result = self.export("seuil_haut", split_by="site", strict_grouping=0.5)
        self.assertEqual(result.manifest["split"]["degraded_groups"]["threshold"], 0.5)


class BboxClampTest(PhotoFixture):
    """Une boîte qui déborde de l'image est bornée — et le dit."""

    def _add_out_of_bounds(self) -> None:
        geom = json.dumps(make_bbox_geometry(
            -10, -5, WIDTH + 26, HEIGHT + 22, space="stereo_rectified_left",
            ref_width=WIDTH, ref_height=HEIGHT,
        ))
        with session_scope(self.db_path) as session:
            session.add(SpatialAnnotation(
                id="ann-debordante", media_id="m00", frame_index=0,
                frame_ref="absolute", geom_type="bbox", geometry_json=geom,
                taxon_node_id="sp-a", source="validated",
            ))

    def test_boite_hors_image_bornee_et_comptee(self):
        self._add_out_of_bounds()
        result = self.export("clamp", taxonomy_rank="fish", fmt="yolo")

        self.assertEqual(result.report["bbox_clamped"]["count"], 1)
        self.assertEqual(result.manifest["source"]["bbox_clamped_count"], 1)

        coco = json.loads(
            (result.output_dir / "instances_fish.json").read_text(encoding="utf-8")
        )
        images = {img["id"]: img for img in coco["images"]}
        cible = next(
            a for a in coco["annotations"]
            if a["attributes"]["spatial_annotation_id"] == "ann-debordante"
        )
        image = images[cible["image_id"]]
        x, y, w, h = cible["bbox"]
        self.assertEqual([x, y, w, h], [0.0, 0.0, float(image["width"]), float(image["height"])])
        self.assertEqual(cible["area"], image["width"] * image["height"])
        # Aucune boîte, clampée ou non, ne sort de l'image.
        for annotation in coco["annotations"]:
            bx, by, bw, bh = annotation["bbox"]
            img = images[annotation["image_id"]]
            self.assertGreaterEqual(bx, 0.0)
            self.assertGreaterEqual(by, 0.0)
            self.assertLessEqual(bx + bw, img["width"] + 1e-6)
            self.assertLessEqual(by + bh, img["height"] + 1e-6)

        # Le dérivé YOLO décrit la même boîte, normalisée dans [0, 1].
        for label in (result.output_dir / "yolo" / "labels").rglob("*.txt"):
            for line in label.read_text(encoding="utf-8").splitlines():
                if not line:
                    continue
                values = [float(v) for v in line.split()[1:]]
                for value in values:
                    self.assertGreaterEqual(value, 0.0)
                    self.assertLessEqual(value, 1.0)

    def test_bornage_est_verifiable_isolement(self):
        (x, y, w, h), clamped = clamp_box_to_image(0.5, 0.5, 2.0, 2.0, 64, 48)
        self.assertTrue(clamped)
        self.assertEqual((x, y, w, h), (0.0, 0.0, 64.0, 48.0))
        (_, _, _, _), untouched = clamp_box_to_image(0.5, 0.5, 0.5, 0.5, 64, 48)
        self.assertFalse(untouched)

    def test_boite_entierement_hors_image_est_exclue_avec_sa_raison(self):
        geom = json.dumps(make_bbox_geometry(
            WIDTH + 10, HEIGHT + 10, WIDTH + 30, HEIGHT + 30,
            space="stereo_rectified_left", ref_width=WIDTH, ref_height=HEIGHT,
        ))
        with session_scope(self.db_path) as session:
            session.add(SpatialAnnotation(
                id="ann-ailleurs", media_id="m00", frame_index=0,
                frame_ref="absolute", geom_type="bbox", geometry_json=geom,
                taxon_node_id="sp-a", source="validated",
            ))
        result = self.export("hors_image", taxonomy_rank="fish")
        exclues = {
            row["annotation_id"]: row["reason"] for row in result.exclusions
        }
        self.assertIn("ann-ailleurs", exclues)
        self.assertEqual(exclues["ann-ailleurs"], "geometrie_illisible")


class WrittenLayoutTest(unittest.TestCase):
    """La 2e vérification anti-fuite doit porter sur le disque, pas sur le plan."""

    def _plan(self):
        from src.annodb.export_core import SplitPlan

        plan = SplitPlan(split_by="media", seed=1, ratios={"train": 1.0, "val": 0.0, "test": 0.0})
        plan.group_of_frame = {"a": "media:a", "b": "media:b"}
        plan.split_of_group = {"media:a": "train", "media:b": "val"}
        return plan

    def _staging(self, layout: dict) -> Path:
        import tempfile

        tmp = tempfile.TemporaryDirectory(prefix="layout_")
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        for rel in layout:
            path = root / "images" / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"x")
        return root

    def test_image_ecrite_dans_le_mauvais_split_est_refusee(self):
        plan = self._plan()
        images = [
            {"file_name": "train/a.jpg"}, {"file_name": "val/b.jpg"},
        ]
        # Sur disque, b.jpg est en train alors que le plan dit val.
        staging = self._staging({"train/a.jpg": 1, "train/b.jpg": 1})
        with self.assertRaises(ExportIntegrityError) as ctx:
            assert_written_layout_matches_plan(staging, images, plan)
        self.assertIn("b.jpg", str(ctx.exception))

    def test_layout_conforme_passe(self):
        plan = self._plan()
        images = [{"file_name": "train/a.jpg"}, {"file_name": "val/b.jpg"}]
        staging = self._staging({"train/a.jpg": 1, "val/b.jpg": 1})
        assert_written_layout_matches_plan(staging, images, plan)

    def test_fichier_etranger_est_refuse(self):
        plan = self._plan()
        images = [{"file_name": "train/a.jpg"}, {"file_name": "val/b.jpg"}]
        staging = self._staging({"train/a.jpg": 1, "val/b.jpg": 1, "train/intrus.jpg": 1})
        with self.assertRaises(ExportIntegrityError) as ctx:
            assert_written_layout_matches_plan(staging, images, plan)
        self.assertIn("intrus.jpg", str(ctx.exception))


class WalSnapshotTest(TempDbCase):
    """`db_snapshot_sha256` doit décrire la base, journal WAL compris."""

    def test_le_db_ignore_les_commits_restes_dans_le_wal(self):
        """Preuve de la revue, reproduite : sans checkpoint, l'empreinte ment."""
        conn = sqlite3.connect(str(self.db_path))
        self.addCleanup(conn.close)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE essai (id INTEGER PRIMARY KEY, valeur TEXT)")
        conn.commit()
        avant = sha256_file(self.db_path)

        conn.executemany(
            "INSERT INTO essai (valeur) VALUES (?)",
            [(f"valeur-{i}",) for i in range(300)],
        )
        conn.commit()  # commité, donc visible en base… mais dans le journal WAL

        self.assertTrue(Path(f"{self.db_path}-wal").is_file())
        sans_checkpoint = sha256_file(self.db_path)
        self.assertEqual(
            sans_checkpoint, avant,
            "le fichier .db n'a pas bougé alors que 300 lignes sont commitées",
        )

        self.assertTrue(wal_checkpoint(self.db_path))
        apres = sha256_file(self.db_path)
        self.assertNotEqual(
            apres, sans_checkpoint,
            "après checkpoint, l'empreinte doit refléter les commits",
        )
        # Et la base est intacte.
        self.assertEqual(
            conn.execute("SELECT COUNT(*) FROM essai").fetchone()[0], 300,
        )

    def test_checkpoint_impossible_ne_fait_pas_echouer_l_export(self):
        """Base illisible : on log, on renvoie False, on n'explose pas."""
        self.assertFalse(wal_checkpoint(self.tmp_path / "sous-dossier-absent" / "x.db"))


class ReplaySourceTest(PhotoFixture):
    """Le fichier rejoué entre par son nom et son empreinte, jamais son chemin."""

    def test_deux_rejeux_depuis_deux_emplacements_donnent_le_meme_manifeste(self):
        reference = self.export("ref", seed=1)
        splits_path = reference.output_dir / "splits.json"

        # Même fichier, deux emplacements — comme deux postes de travail.
        emplacements = []
        for nom in ("poste_a", "poste_b"):
            dossier = self.tmp_path / nom / "quelque" / "part"
            dossier.mkdir(parents=True, exist_ok=True)
            cible = dossier / "splits.json"
            shutil.copy2(splits_path, cible)
            emplacements.append(cible)

        dispose_engine()
        manifests = []
        for index, replay in enumerate(emplacements):
            db_copy = self.tmp_path / f"copie_{index}.db"
            shutil.copy2(self.db_path, db_copy)
            dispose_engine()
            with session_scope(db_copy) as session:
                result = export_dataset(
                    session, self.tmp_path / f"rejeu_{index}", split_by="media",
                    taxonomy_rank="family", min_instances=5, min_media=2,
                    created_at=STAMP, dataset_name="test", seed=999,
                    replay_splits=replay,
                )
            manifests.append(
                (result.output_dir / "manifest.json").read_bytes()
            )
            self.assertEqual(
                result.manifest["split"]["replayed_from"],
                {"file_name": "splits.json", "sha256": sha256_file(replay)},
            )

        self.assertEqual(
            manifests[0], manifests[1],
            "deux rejeux du même découpage doivent produire le même manifeste",
        )


class ReplayCoherenceTest(PhotoFixture):
    """`groups` et `group_ids` sont deux vues du même découpage."""

    def test_vues_contradictoires_refusees(self):
        reference = self.export("coherence")
        payload = json.loads(
            (reference.output_dir / "splits.json").read_text(encoding="utf-8")
        )
        cible = payload["group_ids"]["train"][0]
        payload["groups"][cible] = "test"  # contredit group_ids
        chemin = self.tmp_path / "splits_contradictoire.json"
        chemin.write_text(json.dumps(payload), encoding="utf-8")

        with self.assertRaises(ExportIntegrityError) as ctx:
            load_replay_splits(chemin)
        message = str(ctx.exception)
        self.assertIn(cible, message)
        self.assertIn("contredisent", message)

    def test_vues_coherentes_acceptees(self):
        reference = self.export("coherence_ok")
        chemin = reference.output_dir / "splits.json"
        charge = load_replay_splits(chemin)
        self.assertEqual(charge["split_by"], "media")
        self.assertEqual(charge["source"]["file_name"], "splits.json")
        self.assertEqual(charge["source"]["sha256"], sha256_file(chemin))


class FailureAfterRenameTest(PhotoFixture):
    """Un échec après le renommage ne laisse jamais un dossier au nom valide."""

    def _dossiers(self, root: Path) -> list[str]:
        return sorted(p.name for p in root.iterdir()) if root.exists() else []

    def test_exception_apres_rename_ne_laisse_rien(self):
        root = self.tmp_path / "echec_rename"
        with mock.patch.object(
            export_core, "_disk_bytes", side_effect=RuntimeError("panne après rename"),
        ):
            with self.assertRaises(RuntimeError) as ctx:
                self.export("echec_rename")
        self.assertEqual(str(ctx.exception), "panne après rename")
        self.assertEqual(self._dossiers(root), [], "dossier résiduel après échec")
        with session_scope(self.db_path) as session:
            statuses = [row.status for row in session.query(ExportRun).all()]
        self.assertEqual(statuses, ["failed"])

    def test_commit_d_echec_ne_masque_pas_l_exception_d_origine(self):
        """La trace `failed` est un service rendu, pas une raison de perdre la cause."""
        from sqlalchemy.orm import Session as OrmSession

        vrai_commit = OrmSession.commit
        etat = {"panne": False}

        def commit_capricieux(self_session, *args, **kwargs):
            if etat["panne"]:
                raise sqlite3.OperationalError("base verrouillée")
            return vrai_commit(self_session, *args, **kwargs)

        def disk_bytes_en_panne(_root):
            etat["panne"] = True
            raise RuntimeError("panne après rename")

        root = self.tmp_path / "echec_commit"
        with mock.patch.object(OrmSession, "commit", commit_capricieux), \
                mock.patch.object(export_core, "_disk_bytes", disk_bytes_en_panne):
            with self.assertRaises(RuntimeError) as ctx:
                self.export("echec_commit")
        self.assertEqual(str(ctx.exception), "panne après rename")
        self.assertEqual(self._dossiers(root), [])


class SuggestSplitByTest(TempDbCase):
    """Défaut d'interface : `media` sépare les deux caméras d'une session."""

    def setUp(self):
        super().setUp()
        init_db(self.db_path, seed=False)

    def test_sans_session_le_defaut_reste_media(self):
        with session_scope(self.db_path) as session:
            self.assertEqual(suggest_split_by(session), "media")

    def test_des_qu_une_session_existe_le_defaut_devient_session(self):
        with session_scope(self.db_path) as session:
            session.add(CaptureSession(
                id="sess-defaut", name="Sortie", site="Récif Nord",
                session_date=datetime(2026, 5, 1), status="planned",
            ))
        with session_scope(self.db_path) as session:
            self.assertEqual(suggest_split_by(session), "session")


class YoloNamesGapTest(PhotoFixture):
    """`data.yaml` doit rendre compte des effectifs réellement livrés."""

    def test_classe_sans_instance_livree_est_signalee(self):
        geom = _geometry()
        with session_scope(self.db_path) as session:
            for media_id in ("m05", "m06"):
                session.add(SpatialAnnotation(
                    id=f"ann-na-{media_id}", media_id=media_id, frame_index=0,
                    frame_ref="absolute", geom_type="bbox", geometry_json=geom,
                    taxon_node_id="taxon-fish-generic", source="validated",
                ))

        result = self.export("names_gap", fmt="yolo")
        yolo = result.manifest["yolo"]
        self.assertIn(FAMILY_B, result.manifest["class_stats"]["names"])
        self.assertEqual(yolo["names_without_instances"], [FAMILY_B])
        self.assertEqual(yolo["instances_per_class"][FAMILY_B], 0)
        self.assertGreater(yolo["instances_per_class"][FAMILY_A], 0)

        # L'écart est écrit dans le YAML lui-même, pas seulement en base.
        texte = (result.output_dir / "yolo" / "data.yaml").read_text(encoding="utf-8")
        self.assertIn("sans aucune instance livree", texte)
        self.assertIn(FAMILY_B, texte)

        # Décision documentée : les index restent alignés sur le COCO du rang.
        import yaml

        cfg = yaml.safe_load(texte)
        self.assertEqual(
            [cfg["names"][i] for i in sorted(cfg["names"])],
            result.manifest["class_stats"]["names"],
        )


class ExportRunProvenanceWithoutCalibrationTest(PhotoFixture):
    """Sans calibration, les colonnes de provenance restent vides — jamais inventées."""

    def test_sans_calibration_les_champs_restent_vides(self):
        result = self.export("sans_calib")
        with session_scope(self.db_path) as session:
            row = session.get(ExportRun, result.run.id)
            self.assertEqual(row.image_space, "raw")
            self.assertIsNone(row.calibration_profile)
            self.assertIsNone(row.calibration_sha256)


class ReproducibilityTest(PhotoFixture):
    def test_deux_exports_identiques_donnent_les_memes_sha256(self):
        """Même base, même graine, même version, même date → même dataset."""
        dispose_engine()
        db_a = self.tmp_path / "copie_a.db"
        db_b = self.tmp_path / "copie_b.db"
        shutil.copy2(self.db_path, db_a)
        shutil.copy2(self.db_path, db_b)

        def run(db_path: Path, root: Path):
            with session_scope(db_path) as session:
                return export_dataset(
                    session, root, split_by="media", taxonomy_rank="family",
                    fmt="yolo", min_instances=5, min_media=2, created_at=STAMP,
                    dataset_name="test", seed=42,
                )

        first = run(db_a, self.tmp_path / "export_a")
        dispose_engine()
        second = run(db_b, self.tmp_path / "export_b")

        self.assertEqual(first.output_dir.name, second.output_dir.name)
        self.assertEqual(first.content_sha256, second.content_sha256)
        self.assertEqual(
            {row["path"]: row["sha256"] for row in first.manifest["files"]},
            {row["path"]: row["sha256"] for row in second.manifest["files"]},
        )
        self.assertEqual(
            (first.output_dir / "manifest.json").read_bytes(),
            (second.output_dir / "manifest.json").read_bytes(),
        )
        self.assertEqual(first.run.manifest_sha256, second.run.manifest_sha256)

    def test_rejeu_de_splits_json_fige_le_decoupage(self):
        reference = self.export("v1", seed=1)
        splits_path = reference.output_dir / "splits.json"
        # Graine différente : sans rejeu, le découpage bougerait.
        replayed = self.export("v2", seed=999, replay_splits=splits_path)

        before = json.loads(splits_path.read_text(encoding="utf-8"))["groups"]
        after = json.loads(
            (replayed.output_dir / "splits.json").read_text(encoding="utf-8")
        )["groups"]
        self.assertEqual(before, after)
        # Le fichier rejoué est identifié par son NOM et son empreinte, jamais
        # par son chemin : un chemin absolu dans un fichier haché ferait diverger
        # deux exports pourtant identiques d'un poste à l'autre.
        self.assertEqual(
            replayed.manifest["split"]["replayed_from"],
            {"file_name": "splits.json", "sha256": sha256_file(splits_path)},
        )
        manifest_text = (replayed.output_dir / "manifest.json").read_text(encoding="utf-8")
        self.assertNotIn(str(splits_path.resolve()), manifest_text)
        self.assertNotIn(str(self.tmp_path), manifest_text)
        splits_text = (replayed.output_dir / "splits.json").read_text(encoding="utf-8")
        self.assertNotIn(str(self.tmp_path), splits_text)


class VideoFixture(TempDbCase):
    """Une paire de vidéos en session, six frames annotées sur la gauche."""

    def setUp(self):
        super().setUp()
        init_db(self.db_path, seed=False)
        cam = self.tmp_path / "camera_parameters"
        write_fake_calibration(cam, width=WIDTH, height=HEIGHT)
        set_camera_params_env(self, cam)

        self.left = _write_video(self.tmp_path / "videos" / "gauche.avi")
        self.right = _write_video(self.tmp_path / "videos" / "droite.avi")
        geom = _geometry()
        with session_scope(self.db_path) as session:
            project = Project(id=str(uuid.uuid4()), name="videos")
            session.add(project)
            session.flush()
            session.add_all([
                TaxonNode(id="fam-a", rank="family", scientific_name=FAMILY_A),
                TaxonNode(id="sp-a", parent_id="fam-a", rank="species",
                          scientific_name="Acanthurus nigrofuscus"),
            ])
            session.add_all([
                MediaAsset(id="vid-l", project_id=project.id, media_type="video",
                           rel_path=str(self.left), width=WIDTH, height=HEIGHT),
                MediaAsset(id="vid-r", project_id=project.id, media_type="video",
                           rel_path=str(self.right), width=WIDTH, height=HEIGHT),
            ])
            session.flush()
            session.add(CaptureSession(
                id="sess-video", name="Sortie du 1er mai", site="Récif Sud",
                session_date=datetime(2026, 5, 1), status="done",
                left_media_id="vid-l", right_media_id="vid-r", frame_offset=0,
            ))
            for index in range(6):
                session.add(SpatialAnnotation(
                    id=f"ann-v{index}", media_id="vid-l", frame_index=index,
                    frame_ref="absolute", geom_type="bbox", geometry_json=geom,
                    taxon_node_id="sp-a", source="validated",
                ))

    def export(self, name: str, **kwargs):
        options = {
            "split_by": "media",
            "taxonomy_rank": "family",
            "min_instances": 1,
            "min_media": 1,
            "created_at": STAMP,
            "dataset_name": "video",
        }
        options.update(kwargs)
        with session_scope(self.db_path) as session:
            return export_dataset(session, self.tmp_path / name, **options)


class FrameStrideTest(VideoFixture):
    def test_stride_decime_les_frames_voisines(self):
        complete = self.export("plein")
        self.assertEqual(complete.manifest["source"]["image_count"], 6)

        decimated = self.export("stride", frame_stride=3)
        self.assertEqual(decimated.manifest["source"]["frame_stride"], 3)
        self.assertEqual(decimated.manifest["source"]["frames_dropped_by_stride"], 4)
        images = json.loads(
            (decimated.output_dir / "instances_fish.json").read_text(encoding="utf-8")
        )["images"]
        self.assertEqual(sorted(img["frame_index"] for img in images), [0, 3])

    def test_stride_decime_sur_l_index_absolu_pas_sur_le_rang(self):
        """Deux frames voisines dans le temps ne doivent pas passer ensemble.

        Décimer sur le **rang d'apparition** gardait une frame annotée sur N :
        les index 3 et 4 (un dixième de seconde d'écart, le même poisson)
        pouvaient sortir tous les deux, tandis que deux frames éloignées
        tombaient. Sur `frame_index % stride`, l'écart minimal entre deux frames
        gardées est bien de N images.
        """
        def frame(index: int):
            return type("F", (), {
                "media_id": "vid", "frame_index": index, "media_type": "video",
                "key": f"vid_{index}", "annotations": [],
            })()

        # Rangs 0,1,2,3 → un stride sur le rang garderait les index 0 et 6.
        kept, dropped = apply_frame_stride([frame(i) for i in (0, 3, 4, 6)], 3)
        self.assertEqual([f.frame_index for f in kept], [0, 3, 6])
        self.assertEqual([row["frame_index"] for row in dropped], [4])

        kept, _ = apply_frame_stride([frame(i) for i in (100, 101, 102, 103)], 2)
        self.assertEqual([f.frame_index for f in kept], [100, 102])

    def test_stride_ne_touche_pas_aux_photos(self):
        frames = [
            type("F", (), {"media_id": "photo", "frame_index": 0, "media_type": "image",
                           "key": "photo", "annotations": []})(),
            type("F", (), {"media_id": "photo2", "frame_index": 0, "media_type": "image",
                           "key": "photo2", "annotations": []})(),
        ]
        kept, dropped = apply_frame_stride(frames, 5)
        self.assertEqual(len(kept), 2)
        self.assertEqual(dropped, [])


class SessionStatusTest(VideoFixture):
    def test_export_pose_le_statut_exported(self):
        with session_scope(self.db_path) as session:
            self.assertEqual(session.get(CaptureSession, "sess-video").status, "done")

        result = self.export("statut")

        with session_scope(self.db_path) as session:
            self.assertEqual(session.get(CaptureSession, "sess-video").status, "exported")
        self.assertEqual(
            [row["session_id"] for row in result.manifest["sessions_marked_exported"]],
            ["sess-video"],
        )

    def test_coordinate_frame_porte_la_calibration(self):
        result = self.export("calib")
        frame = result.manifest["coordinate_frame"]
        self.assertEqual(frame["image_space"], "stereo_rectified_left")
        self.assertTrue(frame["calibration"]["calibration_sha256"])
        self.assertEqual(frame["calibration"]["profile_name"], "classic")


class ExportRunProvenanceTest(VideoFixture):
    """Colonnes de traçabilité d'`export_runs`, remplies par le noyau lui-même.

    (Ce cas testait `export_media.export_run_provenance`, une aide morte depuis
    le noyau de la phase 2 : le même contrat est vérifié ici sur la ligne
    réellement écrite en base — et sur les champs que l'aide laissait NULL.)
    """

    def test_provenance_depuis_le_recapitulatif_d_espace_image(self):
        result = self.export("provenance")
        with session_scope(self.db_path) as session:
            row = session.get(ExportRun, result.run.id)
            self.assertEqual(row.image_space, "stereo_rectified_left")
            self.assertEqual(row.calibration_profile, "classic")
            self.assertEqual(len(row.calibration_sha256), 64)
            self.assertEqual(row.image_count, 6)
            self.assertEqual(row.split_strategy, "group_by_media")
            self.assertEqual(row.split_seed, 42)
            self.assertEqual(row.status, "completed")
            # Ce que l'aide morte laissait NULL, le noyau le connaît vraiment.
            self.assertEqual(len(row.manifest_sha256), 64)
            self.assertEqual(len(row.db_snapshot_sha256), 64)


if __name__ == "__main__":
    unittest.main()
