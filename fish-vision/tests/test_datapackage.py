"""Descripteurs Frictionless autour des CSV — phase 4a.

L'exigence tenue ici est la seule qui compte : **les colonnes déclarées sont
exactement celles réellement écrites**. Un descripteur qui décrit un fichier
qu'il n'a jamais lu est pire qu'un CSV nu — il ment avec autorité.

Les tests écrivent donc les vrais CSV (timeline, événements, abondance) depuis
une vraie base jetable, relisent leur première ligne, et la comparent au
descripteur produit à côté.
"""

from __future__ import annotations

import csv
import json
import sys
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.helpers import TempDbCase  # noqa: E402

from src.annodb import datapackage  # noqa: E402
from src.annodb.connection import init_db, session_scope  # noqa: E402
from src.annodb.events import add_temporal_event  # noqa: E402
from src.annodb.export_abundance_csv import (  # noqa: E402
    ABUNDANCE_CSV_FIELDS,
    export_abundance_csv,
)
from src.annodb.export_session_csv import export_session_timeline_csv  # noqa: E402
from src.annodb.models import (  # noqa: E402
    FrameAbundance,
    MediaAsset,
    Project,
    Track,
)
from src.annodb.session_stats import TIMELINE_CSV_FIELDS  # noqa: E402
from src.annodb.tracks import add_track_sample  # noqa: E402


def _read_header(path: Path) -> list[str]:
    """En-tête réellement écrit — encodage compris (BOM `utf-8-sig`)."""
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return next(csv.reader(handle))


class DescriptorShapeTest(unittest.TestCase):
    """Forme du descripteur, indépendamment de toute base."""

    def test_toutes_les_colonnes_sont_decrites(self):
        for kind in datapackage.RESOURCE_SPECS:
            with self.subTest(kind=kind):
                for field in datapackage.RESOURCE_SPECS[kind]["fields"]:
                    self.assertTrue(field["name"], kind)
                    self.assertIn(
                        field["type"],
                        {"string", "integer", "number", "boolean", "datetime", "date"},
                        f"{kind}.{field['name']}",
                    )
                    self.assertTrue(
                        len(field["description"]) > 10,
                        f"{kind}.{field['name']} sans description utile",
                    )

    def test_aucun_nom_de_colonne_en_double(self):
        for kind in datapackage.RESOURCE_SPECS:
            names = datapackage.field_names(kind)
            self.assertEqual(len(names), len(set(names)), kind)

    def test_les_unites_sont_declarees_ou_l_on_en_attend(self):
        units = {
            f["name"]: f.get("unit")
            for f in datapackage.RESOURCE_SPECS["timeline"]["fields"]
        }
        self.assertEqual(units["measurement_mm"], "mm")
        self.assertEqual(units["position_z_mm"], "mm")
        self.assertEqual(units["time_offset_s"], "s")
        self.assertEqual(units["frame_index"], "images")
        self.assertEqual(units["bbox_x1"], "px")

    def test_licence_presente_avec_son_todo(self):
        payload = datapackage.build_datapackage(
            [("timeline", "x.csv")], name="x", title="X"
        )
        self.assertTrue(payload["licenses"])
        titre = payload["licenses"][0]["title"]
        self.assertIn("À définir", titre)
        self.assertIn("TODO", titre)

    def test_cles_primaires_seulement_la_ou_elles_sont_garanties(self):
        # Garanties par le schéma de la base.
        self.assertEqual(
            datapackage.RESOURCE_SPECS["abundance"]["primaryKey"],
            ["media_id", "frame_index"],
        )
        self.assertEqual(
            datapackage.RESOURCE_SPECS["grazing"]["primaryKey"], ["event_id"]
        )
        # La chronologie n'en a pas : plusieurs poissons peuvent être annotés
        # sur la même image sans piste. En déclarer une serait un mensonge.
        self.assertIsNone(datapackage.RESOURCE_SPECS["timeline"]["primaryKey"])
        resource = datapackage.build_resource("timeline", "t.csv")
        self.assertNotIn("primaryKey", resource["schema"])

    def test_na_n_est_pas_une_valeur_manquante(self):
        """« NA » est une détermination revendiquée, pas un trou."""
        resource = datapackage.build_resource("timeline", "t.csv")
        self.assertEqual(resource["schema"]["missingValues"], [""])

    def test_nom_du_descripteur_resiste_aux_points_dans_le_nom(self):
        target = datapackage.descriptor_path_for_csv(
            Path("/x/20260818_site_Runcam6_0004 (1).MP4_timeline.csv")
        )
        self.assertEqual(
            target.name, "20260818_site_Runcam6_0004 (1).MP4_timeline.datapackage.json"
        )

    def test_created_n_est_jamais_invente(self):
        """Un descripteur scellé entre dans un condensé : pas d'horodatage spontané."""
        payload = datapackage.build_datapackage(
            [("timeline", "x.csv")], name="x", title="X"
        )
        self.assertNotIn("created", payload)
        dated = datapackage.build_datapackage(
            [("timeline", "x.csv")], name="x", title="X", created="2026-08-18T10:00:00"
        )
        self.assertEqual(dated["created"], "2026-08-18T10:00:00")


class DescriptorMatchesRealCsvTest(TempDbCase):
    """Le contrat : en-tête réel du CSV == colonnes déclarées."""

    def setUp(self):
        super().setUp()
        init_db(self.db_path, seed=False)
        self.media_id = str(uuid.uuid4())
        with session_scope(self.db_path) as db:
            proj = Project(id=str(uuid.uuid4()), name="test")
            db.add(proj)
            db.flush()
            db.add(MediaAsset(
                id=self.media_id, project_id=proj.id, media_type="video",
                rel_path="clip.mp4", width=1920, height=1080, fps=30.0,
                frame_count=900, site="Récif Nord", session_title="Sortie 1",
            ))
            db.flush()
            self.track_id = str(uuid.uuid4())
            db.add(Track(
                id=self.track_id, media_id=self.media_id, external_track_id=7,
                source="bytetrack",
            ))
            db.flush()
            for frame in (10, 20, 60):
                add_track_sample(
                    db, track_id=self.track_id, frame_index=frame,
                    cx=0.5, cy=0.5, bbox=(10.0, 20.0, 30.0, 40.0),
                )
            add_temporal_event(
                db, track_id=self.track_id, frame_start=10, frame_end=20,
            )
            for frame, ai, manual in ((10, 3, None), (20, 4, 5)):
                db.add(FrameAbundance(
                    media_id=self.media_id, frame_index=frame, ai_count=ai,
                    manual_count=manual, validated=manual is not None,
                    frame_ref="absolute",
                ))

    # ── 1 · chronologie ───────────────────────────────────────────────

    def test_timeline_entete_reelle_egale_champs_declares(self):
        out = self.tmp_path / "chronologie.csv"
        result = export_session_timeline_csv(self.media_id, out, db_path=self.db_path)
        self.assertGreater(result["row_count"], 0)

        header = _read_header(out)
        self.assertEqual(header, TIMELINE_CSV_FIELDS)
        self.assertEqual(header, datapackage.field_names("timeline"))

        descriptor = Path(result["datapackage"])
        self.assertTrue(descriptor.is_file())
        payload = json.loads(descriptor.read_text(encoding="utf-8"))
        resource = payload["resources"][0]
        self.assertEqual([f["name"] for f in resource["schema"]["fields"]], header)
        self.assertEqual(resource["path"], out.name)
        self.assertEqual(resource["encoding"], "utf-8-sig")
        self.assertEqual(resource["format"], "csv")

    def test_timeline_declare_les_colonnes_des_phases_0bis_et_3(self):
        for column in ("frame_ref", "geometry_space", "event_type"):
            self.assertIn(column, datapackage.field_names("timeline"), column)

    # ── 2 · événements ────────────────────────────────────────────────

    def test_grazing_entete_reelle_egale_champs_declares(self):
        from scripts.export_grazing_dataset import (
            GRAZING_CSV_FIELDS,
            export_grazing_events,
        )

        out = self.tmp_path / "grazing_events.csv"
        count = export_grazing_events(self.media_id, out, db_path=self.db_path)
        self.assertEqual(count, 1)

        header = _read_header(out)
        self.assertEqual(header, GRAZING_CSV_FIELDS)
        self.assertEqual(header, datapackage.field_names("grazing"))

        descriptor = datapackage.descriptor_path_for_csv(out)
        self.assertTrue(descriptor.is_file())
        payload = json.loads(descriptor.read_text(encoding="utf-8"))
        self.assertEqual(
            [f["name"] for f in payload["resources"][0]["schema"]["fields"]], header
        )
        self.assertEqual(
            payload["resources"][0]["schema"]["primaryKey"], ["event_id"]
        )

    # ── 3 · abondance ─────────────────────────────────────────────────

    def test_abondance_entete_reelle_egale_champs_declares(self):
        out = self.tmp_path / "abondance.csv"
        result = export_abundance_csv(self.media_id, out, db_path=self.db_path)
        self.assertEqual(result["row_count"], 2)

        header = _read_header(out)
        self.assertEqual(header, ABUNDANCE_CSV_FIELDS)
        self.assertEqual(header, datapackage.field_names("abundance"))

        payload = json.loads(
            Path(result["datapackage"]).read_text(encoding="utf-8")
        )
        self.assertEqual(
            [f["name"] for f in payload["resources"][0]["schema"]["fields"]], header
        )

    def test_abondance_dit_quel_comptage_fait_foi(self):
        out = self.tmp_path / "abondance.csv"
        export_abundance_csv(self.media_id, out, db_path=self.db_path)
        with out.open("r", encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        par_frame = {int(r["frame_index"]): r for r in rows}
        self.assertEqual(par_frame[10]["count_used"], "3")
        self.assertEqual(par_frame[10]["count_source"], "ai")
        self.assertEqual(par_frame[10]["manual_count"], "")
        # Le comptage de l'opérateur prime sur celui de l'IA.
        self.assertEqual(par_frame[20]["count_used"], "5")
        self.assertEqual(par_frame[20]["count_source"], "manual")
        self.assertEqual(par_frame[20]["validated"], "1")

    def test_abondance_trace_son_export_run(self):
        from src.annodb.models import ExportRun

        out = self.tmp_path / "abondance.csv"
        export_abundance_csv(self.media_id, out, db_path=self.db_path)
        with session_scope(self.db_path) as db:
            formats = [r.format for r in db.query(ExportRun).all()]
        self.assertIn("csv_abundance", formats)

    # ── 4 · JSON valide ───────────────────────────────────────────────

    def test_les_descripteurs_sont_du_json_utf8_valide(self):
        out = self.tmp_path / "chronologie.csv"
        result = export_session_timeline_csv(self.media_id, out, db_path=self.db_path)
        raw = Path(result["datapackage"]).read_text(encoding="utf-8")
        payload = json.loads(raw)
        self.assertEqual(payload["profile"], datapackage.PROFILE)
        self.assertIn("é", raw, "les descriptions doivent rester en français lisible")
        self.assertNotIn("\\u00e9", raw, "pas d'échappement Unicode inutile")


class BehaviorDatapackageTest(unittest.TestCase):
    """Le descripteur de l'export scellé décrit les deux tables AVA."""

    def test_colonnes_ava_conformes_aux_constantes_de_l_exporteur(self):
        from src.annodb.export_behavior import ACTIONS_COLUMNS, AVA_COLUMNS

        self.assertEqual(datapackage.field_names("ava_events"), list(AVA_COLUMNS))
        self.assertEqual(datapackage.field_names("ava_actions"), list(ACTIONS_COLUMNS))

    def test_encodage_declare_conforme_a_l_ecriture(self):
        """`write_ava` écrit en UTF-8 **sans** BOM : le descripteur le dit."""
        self.assertEqual(
            datapackage.RESOURCE_SPECS["ava_events"]["encoding"], "utf-8"
        )
        self.assertEqual(
            datapackage.RESOURCE_SPECS["ava_actions"]["encoding"], "utf-8"
        )


if __name__ == "__main__":
    unittest.main()
