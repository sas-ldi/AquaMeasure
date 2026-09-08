"""Export de suivi (phase 3) : COCO-VID pivot, MOTChallenge dérivé.

Ce qui est vérifié ici n'est pas cosmétique : un `gt.txt` dont les numéros de
frame ne sont pas documentés donne des durées fausses ; un dérivé MOT relu
depuis la base plutôt que depuis le pivot finit par décrire d'autres boîtes que
lui ; un média coupé entre deux splits fait fuiter la réponse.
"""

from __future__ import annotations

import configparser
import json
import unittest
import uuid
from datetime import datetime
from pathlib import Path

from tests.helpers import (
    TempDbCase,
    set_camera_params_env,
    write_fake_calibration,
)

from src.annodb import export_tracking
from src.annodb.connection import init_db, session_scope
from src.annodb.export_core import (
    ExportIntegrityError,
    export_dataset,
)
from src.annodb.models import (
    MediaAsset,
    Project,
    SpatialAnnotation,
    TaxonNode,
    Track,
    TrackSample,
    TemporalEvent,
    EventType,
)

WIDTH, HEIGHT = 64, 48
FPS = 10.0
STAMP = datetime(2026, 8, 18, 9, 0, 0)


def _write_video(path: Path, frames: int = 12):
    import cv2
    import numpy as np

    path.parent.mkdir(parents=True, exist_ok=True)
    writer = cv2.VideoWriter(
        str(path), cv2.VideoWriter_fourcc(*"MJPG"), FPS, (WIDTH, HEIGHT),
    )
    for i in range(frames):
        frame = np.zeros((HEIGHT, WIDTH, 3), dtype=np.uint8)
        frame[:, :, 0] = (i * 21) % 255
        writer.write(frame)
    writer.release()
    return path


def _bbox(x1: float, y1: float, x2: float, y2: float) -> str:
    return json.dumps({"x_min": x1, "y_min": y1, "x_max": x2, "y_max": y2})


class TrackingFixture(TempDbCase):
    """Deux vidéos, quatre pistes, des trous de suivi assumés.

    - `vid-a` : piste 1 sur les frames 0, 2, 4 (origines auto / interpolated /
      keyframe), piste 2 sur 0 et 8 — un trou de six frames, exactement le cas
      que l'export ne doit pas combler.
    - `vid-b` : piste 3 sur 0 et 1, piste 4 sur une seule frame (le motif des
      246 pistes d'une frame de la base réelle).
    """

    def setUp(self):
        super().setUp()
        init_db(self.db_path, seed=False)
        cam = self.tmp_path / "camera_parameters"
        write_fake_calibration(cam, width=WIDTH, height=HEIGHT)
        set_camera_params_env(self, cam)

        self.video_a = _write_video(self.tmp_path / "videos" / "a.avi")
        self.video_b = _write_video(self.tmp_path / "videos" / "b.avi")

        with session_scope(self.db_path) as session:
            project = Project(id=str(uuid.uuid4()), name="suivi")
            session.add(project)
            session.flush()
            self.project_id = project.id
            session.add_all([
                TaxonNode(id="fam-a", rank="family", scientific_name="Acanthuridae"),
                TaxonNode(id="sp-a", parent_id="fam-a", rank="species",
                          scientific_name="Acanthurus nigrofuscus"),
            ])
            session.add_all([
                MediaAsset(id="vid-a", project_id=project.id, media_type="video",
                           rel_path=str(self.video_a), width=WIDTH, height=HEIGHT,
                           fps=FPS, frame_count=12),
                MediaAsset(id="vid-b", project_id=project.id, media_type="video",
                           rel_path=str(self.video_b), width=WIDTH, height=HEIGHT,
                           fps=FPS, frame_count=12),
            ])
            session.flush()
            session.add_all([
                Track(id="trk-1", media_id="vid-a", external_track_id=1,
                      source="bytetrack", taxon_node_id="sp-a",
                      first_frame=0, last_frame=4),
                Track(id="trk-2", media_id="vid-a", external_track_id=2,
                      source="bytetrack", taxon_node_id="sp-a",
                      first_frame=0, last_frame=8),
                Track(id="trk-3", media_id="vid-b", external_track_id=1,
                      source="bytetrack", taxon_node_id="sp-a",
                      first_frame=0, last_frame=1),
                Track(id="trk-4", media_id="vid-b", external_track_id=2,
                      source="bytetrack", taxon_node_id="sp-a",
                      first_frame=5, last_frame=5),
            ])
            session.flush()
            rows = [
                ("trk-1", 0, 8, 6, 24, 22, "auto"),
                ("trk-1", 2, 10, 8, 26, 24, "interpolated"),
                ("trk-1", 4, 12, 10, 28, 26, "keyframe"),
                ("trk-2", 0, 30, 20, 46, 36, "auto"),
                ("trk-2", 8, 34, 24, 50, 40, "auto"),
                ("trk-3", 0, 4, 4, 20, 20, "auto"),
                ("trk-3", 1, 5, 5, 21, 21, "auto"),
                ("trk-4", 5, 40, 30, 56, 44, "auto"),
            ]
            for track_id, frame, x1, y1, x2, y2, origin in rows:
                session.add(TrackSample(
                    track_id=track_id, frame_index=frame,
                    cx=(x1 + x2) / 2 / WIDTH, cy=(y1 + y2) / 2 / HEIGHT,
                    bbox_json=_bbox(x1, y1, x2, y2), origin=origin,
                ))

    def export(self, name: str, **kwargs):
        options = {
            "split_by": "media",
            "taxonomy_rank": "fish",
            "fmt": "coco_vid",
            "created_at": STAMP,
            "dataset_name": "suivi",
        }
        options.update(kwargs)
        with session_scope(self.db_path) as session:
            return export_dataset(session, self.tmp_path / name, **options)

    @staticmethod
    def coco_vid(result) -> dict:
        return json.loads(
            (result.output_dir / export_tracking.COCO_VID_NAME).read_text(encoding="utf-8")
        )


class CocoVidStructureTest(TrackingFixture):
    def test_structure_videos_images_annotations(self):
        result = self.export("pivot")
        payload = self.coco_vid(result)

        for key in ("info", "categories", "videos", "images", "tracks", "annotations"):
            self.assertIn(key, payload, f"clé COCO-VID manquante : {key}")

        self.assertEqual(len(payload["videos"]), 2)
        for video in payload["videos"]:
            for key in ("id", "name", "width", "height", "fps", "exported_frames"):
                self.assertIn(key, video)
            self.assertEqual(video["width"], WIDTH)
            self.assertEqual(video["height"], HEIGHT)
            self.assertEqual(video["fps"], FPS)

        video_ids = {video["id"] for video in payload["videos"]}
        for image in payload["images"]:
            self.assertIn(image["video_id"], video_ids)
            # `frame_id` = index absolu du fichier source, convention du dépôt.
            self.assertEqual(image["frame_id"], image["frame_index"])

        # 8 échantillons en base, 8 annotations livrées.
        self.assertEqual(len(payload["annotations"]), 8)
        for annotation in payload["annotations"]:
            self.assertIsNotNone(annotation["instance_id"])
            self.assertIn("track_db_id", annotation["attributes"])
            self.assertIn("origin", annotation["attributes"])

    def test_instance_id_est_l_identite_de_piste(self):
        """Deux frames d'une même piste portent le **même** `instance_id`."""
        payload = self.coco_vid(self.export("instances"))
        by_track = {}
        for annotation in payload["annotations"]:
            by_track.setdefault(
                annotation["attributes"]["track_db_id"], set()
            ).add(annotation["instance_id"])
        self.assertEqual(
            {track: len(ids) for track, ids in by_track.items()},
            {"trk-1": 1, "trk-2": 1, "trk-3": 1, "trk-4": 1},
        )
        # Et deux pistes distinctes ne partagent jamais un identifiant.
        flat = [next(iter(ids)) for ids in by_track.values()]
        self.assertEqual(len(set(flat)), len(flat))

        declared = {track["track_db_id"]: track["id"] for track in payload["tracks"]}
        self.assertEqual(declared, {t: next(iter(i)) for t, i in by_track.items()})

    def test_categories_viennent_de_la_class_map_du_noyau(self):
        payload = self.coco_vid(self.export("classes"))
        self.assertEqual(
            [c["name"] for c in payload["categories"]], ["fish"],
        )
        self.assertTrue(all(a["category_id"] == 1 for a in payload["annotations"]))
        self.assertTrue(all(a["ignore"] == 0 for a in payload["annotations"]))

    def test_bbox_en_pixels_de_l_espace_declare(self):
        result = self.export("geom")
        payload = self.coco_vid(result)
        images = {img["id"]: img for img in payload["images"]}
        for annotation in payload["annotations"]:
            x, y, w, h = annotation["bbox"]
            image = images[annotation["image_id"]]
            self.assertGreaterEqual(x, 0.0)
            self.assertGreaterEqual(y, 0.0)
            self.assertLessEqual(x + w, image["width"] + 1e-6)
            self.assertLessEqual(y + h, image["height"] + 1e-6)
            self.assertGreater(w, 1.0, "bbox en pixels attendue, pas normalisée")
        space = result.manifest["coordinate_frame"]["image_space"]
        self.assertEqual(space, "stereo_rectified_left")

    def test_decimation_frame_stride_applicable(self):
        # 7 images distinctes : vid-a sur 0, 2, 4, 8 ; vid-b sur 0, 1, 5.
        complet = self.export("complet")
        self.assertEqual(complet.manifest["source"]["image_count"], 7)
        decime = self.export("decime", frame_stride=4)
        self.assertEqual(decime.manifest["source"]["frame_stride"], 4)
        frames = sorted(
            img["frame_id"] for img in self.coco_vid(decime)["images"]
        )
        self.assertEqual(frames, [0, 0, 4, 8])

    def test_frame_sans_image_materialisable_est_exclue_avec_sa_raison(self):
        """Une vidéo disparue ne fait pas disparaître ses pistes en silence."""
        Path(self.video_b).unlink()
        result = self.export("media_absent")
        raisons = {row["reason"] for row in result.exclusions}
        self.assertIn("fichier_media_introuvable", raisons)
        self.assertEqual(len(self.coco_vid(result)["videos"]), 1)

    def test_couverture_declaree_au_manifeste(self):
        """Le suivi est plein de trous : le manifeste doit le dire."""
        manifest = self.export("couverture").manifest
        coverage = manifest["coverage"]
        # vid-a : frames 0,2,4,8 sur une étendue de 9 ; vid-b : 0,1,5 sur 6.
        self.assertEqual(coverage["exported_frames"], 7)
        self.assertEqual(coverage["covered_span"], 15)
        per_video = {row["name"]: row for row in coverage["per_video"]}
        self.assertEqual(len(per_video), 2)
        for row in per_video.values():
            self.assertLess(row["coverage_ratio"], 1.0)


class SplitByGroupTest(TrackingFixture):
    def test_un_media_entier_d_un_seul_cote_du_decoupage(self):
        result = self.export("split")
        payload = self.coco_vid(result)
        splits_of_media = {}
        for image in payload["images"]:
            splits_of_media.setdefault(image["media_id"], set()).add(image["split"])
        for media_id, splits in splits_of_media.items():
            self.assertEqual(
                len(splits), 1, f"{media_id} est présent dans {sorted(splits)}",
            )
        splits = json.loads(
            (result.output_dir / "splits.json").read_text(encoding="utf-8")
        )
        self.assertEqual(splits["split_by"], "media")
        listed = [g for names in splits["group_ids"].values() for g in names]
        self.assertEqual(len(listed), len(set(listed)))

    def test_split_par_session_garde_la_paire_ensemble(self):
        from src.annodb.models import CaptureSession

        with session_scope(self.db_path) as session:
            session.add(CaptureSession(
                id="sess", name="Paire", site="Récif Nord",
                session_date=datetime(2026, 5, 1), status="done",
                left_media_id="vid-a", right_media_id="vid-b", frame_offset=0,
            ))
        result = self.export("session", split_by="session")
        payload = self.coco_vid(result)
        self.assertEqual(
            len({image["split"] for image in payload["images"]}), 1,
            "les deux caméras d'une session doivent rester du même côté",
        )

    def test_format_inconnu_refuse(self):
        with self.assertRaises(ValueError):
            self.export("inconnu", fmt="mot_challenge")


class MotDerivationTest(TrackingFixture):
    def test_mot_decrit_les_memes_boites_que_coco_vid(self):
        result = self.export("mot", fmt="mot")
        payload = self.coco_vid(result)
        images = {img["id"]: img for img in payload["images"]}

        attendu = {}
        for annotation in payload["annotations"]:
            image = images[annotation["image_id"]]
            attendu.setdefault(image["media_id"], []).append((
                image["frame_id"], annotation["instance_id"],
                tuple(round(v, 2) for v in annotation["bbox"]),
            ))

        videos = {video["media_id"]: video for video in payload["videos"]}
        for media_id, rows in attendu.items():
            seq = videos[media_id]["name"]
            seq_dir = result.output_dir / "mot" / seq
            mapping = self._frames_map(seq_dir)
            lines = [
                line for line in
                (seq_dir / "gt" / "gt.txt").read_text(encoding="utf-8").splitlines()
                if line
            ]
            self.assertEqual(len(lines), len(rows))
            lus = set()
            for line in lines:
                parts = line.split(",")
                frame_mot = int(parts[0])
                lus.add((
                    mapping[frame_mot], int(parts[1]),
                    (round(float(parts[2]), 2), round(float(parts[3]), 2),
                     round(float(parts[4]), 2), round(float(parts[5]), 2)),
                ))
            self.assertEqual(lus, set(rows))

    @staticmethod
    def _frames_map(seq_dir: Path) -> dict:
        rows = (seq_dir / export_tracking.FRAMES_MAP_NAME).read_text(
            encoding="utf-8"
        ).splitlines()
        assert rows[0].startswith("frame_mot,frame_absolue"), rows[0]
        return {
            int(line.split(",")[0]): int(line.split(",")[1])
            for line in rows[1:] if line
        }

    def test_frames_mot_sont_base_1_contigues_et_documentees(self):
        result = self.export("mot_frames", fmt="mot")
        payload = self.coco_vid(result)
        videos = {video["media_id"]: video for video in payload["videos"]}
        seq_dir = result.output_dir / "mot" / videos["vid-a"]["name"]
        mapping = self._frames_map(seq_dir)
        # vid-a couvre les frames absolues 0, 2, 4, 8 → MOT 1, 2, 3, 4.
        self.assertEqual(sorted(mapping), [1, 2, 3, 4])
        self.assertEqual([mapping[i] for i in sorted(mapping)], [0, 2, 4, 8])
        for line in (seq_dir / "gt" / "gt.txt").read_text(encoding="utf-8").splitlines():
            if line:
                self.assertGreaterEqual(int(line.split(",")[0]), 1)

    def test_seqinfo_reprend_fps_taille_et_couverture(self):
        result = self.export("seqinfo", fmt="mot")
        payload = self.coco_vid(result)
        videos = {video["media_id"]: video for video in payload["videos"]}
        seq = videos["vid-a"]["name"]
        path = result.output_dir / "mot" / seq / "seqinfo.ini"
        parser = configparser.ConfigParser(comment_prefixes=(";", "#"))
        with path.open(encoding="utf-8") as handle:
            parser.read_file(handle)
        section = parser["Sequence"]
        self.assertEqual(section["name"], seq)
        self.assertEqual(section["imDir"], "img1")
        self.assertEqual(float(section["frameRate"]), FPS)
        self.assertEqual(int(section["seqLength"]), 4)
        self.assertEqual(int(section["imWidth"]), WIDTH)
        self.assertEqual(int(section["imHeight"]), HEIGHT)
        texte = path.read_text(encoding="utf-8")
        self.assertIn("couverture", texte)
        self.assertIn(export_tracking.FRAMES_MAP_NAME, texte)

    def test_visibility_suit_l_origine_de_l_echantillon(self):
        result = self.export("visibilite", fmt="mot")
        payload = self.coco_vid(result)
        videos = {video["media_id"]: video for video in payload["videos"]}
        images = {img["id"]: img for img in payload["images"]}
        origine_par_cle = {
            (images[a["image_id"]]["media_id"], images[a["image_id"]]["frame_id"],
             a["instance_id"]): a["attributes"]["origin"]
            for a in payload["annotations"]
        }
        seq_dir = result.output_dir / "mot" / videos["vid-a"]["name"]
        mapping = self._frames_map(seq_dir)
        vues = {}
        for line in (seq_dir / "gt" / "gt.txt").read_text(encoding="utf-8").splitlines():
            if not line:
                continue
            parts = line.split(",")
            cle = ("vid-a", mapping[int(parts[0])], int(parts[1]))
            vues[origine_par_cle[cle]] = float(parts[8])
        self.assertEqual(vues["auto"], 0.7)
        self.assertEqual(vues["interpolated"], 0.9)
        self.assertEqual(vues["keyframe"], 1.0)
        table = result.manifest["mot"]["visibility_by_origin"]
        self.assertEqual(table, {"keyframe": 1.0, "interpolated": 0.9, "auto": 0.7})

    def test_images_de_la_sequence_sont_liees_et_le_manifeste_les_compte(self):
        result = self.export("images_mot", fmt="mot")
        payload = self.coco_vid(result)
        videos = {video["media_id"]: video for video in payload["videos"]}
        seq_dir = result.output_dir / "mot" / videos["vid-a"]["name"]
        jpgs = sorted(p.name for p in (seq_dir / "img1").glob("*.jpg"))
        self.assertEqual(jpgs, ["000001.jpg", "000002.jpg", "000003.jpg", "000004.jpg"])
        info = result.manifest["mot"]
        self.assertEqual(info["sequence_count"], 2)
        self.assertEqual(info["gt_line_count"], 8)
        self.assertEqual(info["frame_base"], 1)

    def test_seqmaps_listent_les_sequences_par_split(self):
        result = self.export("seqmaps", fmt="mot")
        maps = result.output_dir / "mot" / "seqmaps"
        noms = set()
        for path in maps.glob("*.txt"):
            lignes = path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(lignes[0], "name")
            noms.update(lignes[1:])
        self.assertEqual(len(noms), 2)


class TrackingRunTest(TrackingFixture):
    def test_export_run_enregistre_le_format_de_suivi(self):
        from sqlalchemy import select

        from src.annodb.models import ExportRun

        result = self.export("run", fmt="mot")
        with session_scope(self.db_path) as session:
            row = session.scalar(select(ExportRun).where(ExportRun.id == result.run.id))
            self.assertEqual(row.format, "mot")
            self.assertEqual(row.status, "completed")
            self.assertEqual(row.image_count, 7)
            self.assertEqual(row.annotation_count, 8)
            self.assertTrue(row.manifest_sha256)
            self.assertEqual(row.split_strategy, "group_by_media")

    def test_manifeste_scelle_et_reproductible(self):
        premier = self.export("repro_1", fmt="mot")
        second = self.export("repro_2", fmt="mot")
        fichiers = {
            row["path"]: row["sha256"] for row in premier.manifest["files"]
        }
        rejoue = {row["path"]: row["sha256"] for row in second.manifest["files"]}
        self.assertEqual(fichiers, rejoue)

    def test_base_sans_piste_produit_un_export_vide_mais_valide(self):
        with session_scope(self.db_path) as session:
            session.query(TrackSample).delete()
            session.query(Track).delete()
        result = self.export("vide")
        payload = self.coco_vid(result)
        self.assertEqual(payload["videos"], [])
        self.assertEqual(payload["annotations"], [])
        self.assertEqual(result.manifest["source"]["track_count"], 0)


class TrackingClassPlanTest(TrackingFixture):
    def test_identified_track_is_classified_and_unreviewed_track_is_ignored(self):
        with session_scope(self.db_path) as session:
            session.get(Track, "trk-1").identification_status = "identified"
            session.get(Track, "trk-2").identification_status = "unreviewed"
            session.add_all([
                SpatialAnnotation(
                    id="ann-track-identified", media_id="vid-a", frame_index=0,
                    frame_ref="absolute", geom_type="bbox",
                    geometry_json=_bbox(8, 6, 24, 22),
                    track_id="trk-1", taxon_node_id="sp-a", source="model",
                    identification_status="identified",
                ),
                SpatialAnnotation(
                    id="ann-track-unreviewed", media_id="vid-a", frame_index=0,
                    frame_ref="absolute", geom_type="bbox",
                    geometry_json=_bbox(30, 20, 46, 36),
                    track_id="trk-2", taxon_node_id="sp-a", source="model",
                    identification_status="unreviewed",
                ),
            ])

        payload = self.coco_vid(self.export(
            "track_authority", taxonomy_rank="family",
            min_instances=1, min_media=1,
        ))
        categories = {
            row["id"]: row["name"] for row in payload["categories"]
        }
        by_track = {}
        for annotation in payload["annotations"]:
            by_track.setdefault(
                annotation["attributes"]["track_db_id"], set(),
            ).add((
                categories[annotation["category_id"]],
                annotation["ignore"],
                annotation["attributes"]["identified"],
            ))

        self.assertEqual(by_track["trk-1"], {("Acanthuridae", 0, True)})
        self.assertEqual(by_track["trk-2"], {("unidentified", 1, False)})

    def test_legacy_track_authority_matrix_matches_stats_with_or_without_sample(self):
        from src.annodb.export_core import build_class_plan_for_tracks
        from src.annodb.session_stats import _track_taxon_map

        cases = (
            ("trk-1", "vid-a", True, True),
            ("trk-2", "vid-a", False, True),
            ("trk-3", "vid-b", True, False),
            ("trk-4", "vid-b", False, False),
        )
        with session_scope(self.db_path) as session:
            session.add(TaxonNode(
                id="sp-b", parent_id="fam-a", rank="species",
                scientific_name="Acanthurus triostegus",
            ))
            for track_id, media_id, conflict, with_sample in cases:
                track = session.get(Track, track_id)
                track.source = "manual"
                track.taxon_node_id = "sp-a"
                track.identification_status = None
                if not with_sample:
                    session.query(TrackSample).filter_by(track_id=track_id).delete()
                taxa = ("sp-a", "sp-b") if conflict else ("sp-b",)
                for index, taxon_id in enumerate(taxa):
                    session.add(SpatialAnnotation(
                        id=f"ann-authority-{track_id}-{index}",
                        media_id=media_id,
                        frame_index=index,
                        frame_ref="absolute",
                        geom_type="bbox",
                        geometry_json=_bbox(8, 6, 24, 22),
                        track_id=track_id,
                        taxon_node_id=taxon_id,
                        source="manual",
                        identification_status="identified",
                    ))
            session.flush()
            tracks = session.query(Track).order_by(Track.id).all()
            plan = build_class_plan_for_tracks(
                session, tracks, "species", min_instances=1, min_media=1,
            )
            stats_taxa = {
                **_track_taxon_map(session, "vid-a"),
                **_track_taxon_map(session, "vid-b"),
            }

        expected_taxa = {
            track_id: None if conflict else "sp-b"
            for track_id, _media_id, conflict, _with_sample in cases
        }
        expected_classes = {
            track_id: None if conflict else "Acanthurus triostegus"
            for track_id, _media_id, conflict, _with_sample in cases
        }
        self.assertEqual(stats_taxa, expected_taxa)
        self.assertEqual(plan.class_of_annotation, expected_classes)

        payload = self.coco_vid(self.export(
            "legacy_authority_matrix", taxonomy_rank="species",
            min_instances=1, min_media=1,
        ))
        categories = {row["id"]: row["name"] for row in payload["categories"]}
        by_track = {}
        for annotation in payload["annotations"]:
            by_track.setdefault(annotation["attributes"]["track_db_id"], set()).add((
                categories[annotation["category_id"]],
                annotation["ignore"],
                annotation["iscrowd"],
                annotation["attributes"]["identified"],
            ))
        self.assertEqual(by_track["trk-1"], {("unidentified", 1, 1, False)})
        self.assertEqual(
            by_track["trk-2"], {("Acanthurus triostegus", 0, 0, True)},
        )
        self.assertNotIn("trk-3", by_track)
        self.assertNotIn("trk-4", by_track)

        payload_tracks = {
            row["track_db_id"]: row for row in payload["tracks"]
        }
        self.assertEqual(payload_tracks["trk-1"]["taxon_node_id"], None)
        self.assertEqual(payload_tracks["trk-2"]["taxon_node_id"], "sp-b")
        self.assertEqual(
            categories[payload_tracks["trk-1"]["category_id"]], "unidentified",
        )
        self.assertEqual(
            categories[payload_tracks["trk-2"]["category_id"]],
            "Acanthurus triostegus",
        )
        # Sans TrackSample, aucune piste n'est publiée : la résolution reste
        # néanmoins couverte ci-dessus par les stats et le plan de classes.
        self.assertNotIn("trk-3", payload_tracks)
        self.assertNotIn("trk-4", payload_tracks)

    def test_piste_sans_taxon_sort_en_ignore_et_force_train(self):
        """Le rang famille sur des pistes sans taxon : ignore + groupe en train."""
        with session_scope(self.db_path) as session:
            for track in session.query(Track).all():
                track.taxon_node_id = None
        result = self.export("ignore", taxonomy_rank="family", min_instances=1,
                             min_media=1)
        payload = self.coco_vid(result)
        self.assertTrue(all(a["ignore"] == 1 for a in payload["annotations"]))
        self.assertIn(
            "unidentified", [c["name"] for c in payload["categories"]],
        )
        composition = result.manifest["split"]["composition"]
        self.assertEqual(composition["val"]["annotations"], 0)
        self.assertEqual(composition["test"]["annotations"], 0)
        self.assertEqual(
            result.manifest["split"]["na_rule"]["tracks_concerned"], 4,
        )

    def test_regle_na_verifiee_sur_la_composition_livree(self):
        from src.annodb.export_core import assert_val_test_fully_identified

        with self.assertRaises(ExportIntegrityError):
            assert_val_test_fully_identified(
                {"val": {"ignored_annotations": 2}, "test": {"ignored_annotations": 0}},
                context="suivi",
            )


class TrackActionsExportTest(TrackingFixture):
    def setUp(self):
        super().setUp()
        with session_scope(self.db_path) as db:
            db.add_all([
                EventType(id="ray-type", key="ray", label="Passage de raie", scope="instant",
                          is_active=False, color="#33aaff", symbol="◆"),
            ])
            for event_id, track_id, key, start, end in (
                ("bite-2", "trk-1", "bite", 2, 2),
                ("bite-3", "trk-1", "bite", 3, 3),
                ("graze-0-4", "trk-1", "grazing", 0, 4),
                ("ray-2", "trk-2", "ray", 2, 2),
                ("other-video", "trk-3", "bite", 1, 1),
            ):
                db.add(TemporalEvent(id=event_id, track_id=track_id, event_type=key,
                    frame_start=start, frame_end=end, frame_ref="absolute", source="manual", author="Test"))

    def test_points_durees_et_types_sont_lies_a_la_bonne_piste(self):
        result = self.export("actions", media_ids=["vid-a"], fmt="mot")
        payload = self.coco_vid(result)
        events = {row["id"]: row for row in payload["events"]}
        self.assertEqual(set(events), {"bite-2", "bite-3", "graze-0-4", "ray-2"})
        tracks = {row["track_db_id"]: row for row in payload["tracks"]}
        self.assertEqual(events["bite-2"]["track_id"], tracks["trk-1"]["id"])
        self.assertEqual(events["ray-2"]["track_id"], tracks["trk-2"]["id"])
        self.assertEqual(events["bite-2"]["frame_index"], 2)
        self.assertEqual(events["bite-2"]["start_time_s"], 0.2)
        self.assertEqual((events["graze-0-4"]["frame_start"], events["graze-0-4"]["frame_end"]), (0, 4))
        self.assertEqual(events["graze-0-4"]["scope"], "interval")
        kinds = {row["key"]: row for row in payload["event_types"]}
        self.assertEqual(kinds["ray"]["label"], "Passage de raie")
        self.assertEqual(kinds["ray"]["color"], "#33aaff")
        self.assertFalse(kinds["ray"]["is_active"])
        frame_by_id = {image["id"]: image["frame_id"] for image in payload["images"]}
        for box in payload["annotations"]:
            expected = []
            if box["attributes"]["track_db_id"] == "trk-1":
                expected.append("graze-0-4")
                if frame_by_id[box["image_id"]] == 2:
                    expected.append("bite-2")
            self.assertEqual(set(box["attributes"]["event_ids"]), set(expected))
        # La frame 3 n'a pas d'échantillon de piste : image autonome, aucune fausse boîte.
        self.assertIsNone(events["bite-3"]["start_image_id"])
        self.assertTrue((result.output_dir / events["bite-3"]["start_image_file_name"]).is_file())
        self.assertEqual(len(payload["annotations"]), 5)
        self.assertEqual(len(payload["images"]), 4)
        lines = [json.loads(line) for line in (result.output_dir / export_tracking.TRACK_EVENTS_NAME).read_text(encoding="utf-8").splitlines()]
        self.assertEqual(lines, payload["events"])
        self.assertEqual(result.report["events"]["count"], 4)
        self.assertEqual(result.report["events"]["additional_image_count"], 1)
        self.assertEqual(result.report["events"]["missing_images"], [])

    def test_frame_exacte_conservee_avec_decimation_et_offset_historique(self):
        from src.annodb import sessions
        from tests.helpers import write_sync_frames
        with session_scope(self.db_path) as db:
            capture = sessions.create_session(db, name="Ancienne", site="Récif", session_date="2026-08-20")
            sessions.attach_media_pair(db, capture.id, left_media_id="vid-a", frame_offset=3)
            db.add(TemporalEvent(id="legacy", track_id="trk-1", event_type="bite",
                frame_start=0, frame_end=0, frame_ref="timeline_legacy", source="manual"))
        write_sync_frames(self.tmp_path / "camera_parameters", 500, 500)
        result = self.export("stride-actions", frame_stride=4, media_ids=["vid-a"])
        payload = self.coco_vid(result)
        events = {row["id"]: row for row in payload["events"]}
        self.assertEqual(events["legacy"]["frame_index"], 3)
        self.assertEqual(events["legacy"]["start_time_s"], 0.3)
        self.assertIsNone(events["bite-2"]["start_image_id"])
        self.assertTrue((result.output_dir / events["bite-2"]["start_image_file_name"]).is_file())
        self.assertEqual(sorted(row["frame_id"] for row in payload["images"]), [0, 4, 8])
        self.assertEqual(result.report["events"]["additional_image_count"], 2)
        # Les images du paquet restent lisibles lorsque la vidéo source disparaît.
        self.video_a.unlink()
        import cv2
        for event in payload["events"]:
            image = cv2.imread(str(result.output_dir / event["start_image_file_name"]))
            self.assertIsNotNone(image)
            # Chaque frame source porte un niveau de bleu distinct.
            self.assertAlmostEqual(int(image[HEIGHT // 2, WIDTH // 2, 0]), event["frame_start"] * 21, delta=5)

    def test_sources_conservees_et_piste_absente_signalee(self):
        with session_scope(self.db_path) as db:
            db.add(Track(id="empty-track", media_id="vid-a", external_track_id=99,
                         source="manual", first_frame=0, last_frame=1))
            db.flush()
            db.add_all([
                TemporalEvent(id="automatic", track_id="trk-1", event_type="grazing",
                              frame_start=0, frame_end=4, frame_ref="absolute",
                              source="heuristic", confidence=0.6),
                TemporalEvent(id="no-samples", track_id="empty-track", event_type="bite",
                              frame_start=1, frame_end=1, frame_ref="absolute", source="manual"),
            ])
        result = self.export("sources-actions", media_ids=["vid-a"])
        payload = self.coco_vid(result)
        events = {row["id"]: row for row in payload["events"]}
        self.assertEqual(events["automatic"]["source"], "heuristic")
        self.assertEqual(events["automatic"]["confidence"], 0.6)
        self.assertEqual(events["bite-2"]["source"], "manual")
        self.assertEqual(events["bite-2"]["author"], "Test")
        self.assertNotIn("no-samples", events)
        self.assertEqual(payload["excluded_events"][0]["reason"], "piste_absente_de_l_export")
        self.assertEqual(result.report["events"]["by_source"], {"heuristic": 1, "manual": 4})
        for track in payload["tracks"]:
            self.assertEqual(set(track["event_ids"]), {
                event["id"] for event in events.values() if event["track_id"] == track["id"]
            })

    def test_action_hors_video_reste_declaree_sans_image_trompeuse(self):
        with session_scope(self.db_path) as db:
            db.add(TemporalEvent(id="invalid", track_id="trk-1", event_type="bite",
                frame_start=-1, frame_end=-1, frame_ref="absolute", source="manual"))
        result = self.export("invalid-event", media_ids=["vid-a"])
        event = next(row for row in self.coco_vid(result)["events"] if row["id"] == "invalid")
        self.assertEqual(event["frame_index"], -1)
        self.assertIsNone(event["start_image_file_name"])
        self.assertEqual(result.report["events"]["missing_images"], [
            {"media_id": "vid-a", "frame_index": -1, "reason": "hors_video"},
        ])


class InstanceIdHelperTest(unittest.TestCase):
    def test_instance_ids_sont_deterministes(self):
        class _T:
            def __init__(self, id_, media, ext):
                self.id, self.media_id, self.external_track_id = id_, media, ext

        pistes = [
            _T("z", "m2", 1), _T("a", "m1", 2), _T("b", "m1", 1),
        ]
        premier = export_tracking.instance_ids(pistes)
        second = export_tracking.instance_ids(list(reversed(pistes)))
        self.assertEqual(premier, second)
        self.assertEqual(premier, {"b": 1, "a": 2, "z": 3})


class SequenceNameTest(unittest.TestCase):
    def test_nom_de_sequence_sur_media_homonymes(self):
        class _M:
            def __init__(self, rel):
                self.rel_path = rel

        premier = export_tracking.sequence_name(_M("a/droite.MP4"), "abcdef123456")
        second = export_tracking.sequence_name(_M("b/droite.MP4"), "999999999999")
        self.assertNotEqual(premier, second)
        self.assertTrue(premier.startswith("droite_"))
        self.assertNotIn("/", premier)


if __name__ == "__main__":
    unittest.main()
