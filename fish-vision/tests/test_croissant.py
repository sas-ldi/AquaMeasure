"""Descripteur Croissant (MLCommons) des exports scellés — phase 5.

Ce que ces tests tiennent :

1. **Le JSON est valide et complet** — contexte JSON-LD, `@type`, `conformsTo`,
   nom, version, date, licence.
2. **Il est cohérent avec le manifeste** — mêmes nom/version/date, et surtout
   une `distribution[]` qui reprend exactement `files[]` (mêmes chemins, mêmes
   empreintes). Un descripteur qui annonce d'autres fichiers, ou d'autres
   condensés, que le manifeste du même dossier ne vaut rien.
3. **Il entre dans le scellement** — `croissant.json` figure dans `files[]` avec
   sa propre empreinte, donc dans `content_sha256`. Un export n'a pas deux
   vérités : le descripteur n'est pas une pièce rapportée après coup.
4. **Il ne ment pas sur ce qu'il ne décrit pas** — pour les formats tabulaires
   (AVA), il renvoie au `datapackage.json` livré à côté plutôt que de
   redécrire les colonnes une seconde fois.
"""

from __future__ import annotations

import json
import unittest

# `PhotoFixture` monte huit photos, deux familles et un poisson NA : le jeu
# d'essai du noyau d'export. Le réutiliser garantit que Croissant est testé
# sur un export réellement scellé, pas sur une maquette.
from tests.test_export_core import PhotoFixture  # noqa: E402

from src.annodb import croissant, datapackage  # noqa: E402
from src.annodb.export_core import sha256_file  # noqa: E402


class DescriptorShapeTest(unittest.TestCase):
    """Forme du descripteur, sans toucher à la base."""

    def _manifest(self, fmt: str = "coco") -> dict:
        return {
            "schema": "fishvision/export-manifest/1",
            "dataset_name": "test",
            "dataset_version": "1.0.0",
            "created_at": "2026-08-18T09:00:00",
            "git_commit": "abc1234",
            "format": fmt,
            "taxonomy_rank": "family",
            "source": {"media_count": 3, "image_count": 8, "annotation_count": 24},
            "coordinate_frame": {"image_space": "stereo_rectified_left"},
            "split": {"split_by": "media", "seed": 42},
        }

    def _files(self) -> list[dict]:
        return [
            {"path": "instances_fish.json", "sha256": "a" * 64, "bytes": 120},
            {"path": "images/train/x.jpg", "sha256": "b" * 64, "bytes": 4096},
        ]

    def test_json_ld_valide_et_champs_requis(self):
        payload = croissant.build_croissant(self._manifest(), self._files())
        # Sérialisable et relisable — c'est le minimum d'un fichier livré.
        reloaded = json.loads(json.dumps(payload, ensure_ascii=False))
        self.assertEqual(reloaded["@type"], "sc:Dataset")
        self.assertEqual(reloaded["conformsTo"], croissant.CONFORMS_TO)
        self.assertEqual(reloaded["@context"]["cr"], "http://mlcommons.org/croissant/")
        self.assertEqual(reloaded["@context"]["sc"], "https://schema.org/")
        for key in ("name", "description", "license", "version", "dateCreated",
                    "distribution"):
            self.assertIn(key, reloaded, key)
        self.assertTrue(reloaded["description"].strip())

    def test_nom_version_et_date_viennent_du_manifeste(self):
        payload = croissant.build_croissant(self._manifest(), self._files())
        self.assertEqual(payload["version"], "1.0.0")
        self.assertEqual(payload["dateCreated"], "2026-08-18T09:00:00")
        self.assertIn("test", payload["name"])
        self.assertIn("1.0.0", payload["name"])

    def test_licence_reprend_le_todo_client_de_la_phase_4a(self):
        """Même placeholder que le Data Package : pas de licence inventée."""
        payload = croissant.build_croissant(self._manifest(), self._files())
        self.assertIn("À définir", payload["license"])
        self.assertIn("TODO", payload["license"])
        self.assertIn(datapackage.LICENSE_TODO[0]["name"], payload["license"])

    def test_distribution_reprend_les_empreintes_fournies(self):
        payload = croissant.build_croissant(self._manifest(), self._files())
        by_id = {entry["@id"]: entry for entry in payload["distribution"]}
        self.assertEqual(set(by_id), {"instances_fish.json", "images/train/x.jpg"})
        self.assertEqual(by_id["instances_fish.json"]["sha256"], "a" * 64)
        self.assertEqual(by_id["instances_fish.json"]["encodingFormat"], "application/json")
        self.assertEqual(by_id["images/train/x.jpg"]["encodingFormat"], "image/jpeg")
        self.assertEqual(by_id["images/train/x.jpg"]["contentSize"], "4096 B")

    def test_croissant_ne_se_decrit_pas_lui_meme(self):
        """Il ne peut pas porter son propre condensé — le manifeste le fait."""
        files = self._files() + [
            {"path": croissant.CROISSANT_NAME, "sha256": "c" * 64, "bytes": 10},
        ]
        payload = croissant.build_croissant(self._manifest(), files)
        self.assertNotIn(
            croissant.CROISSANT_NAME,
            {entry["@id"] for entry in payload["distribution"]},
        )

    def test_recordsets_coco_decrivent_les_trois_tables(self):
        payload = croissant.build_croissant(self._manifest("coco"), self._files())
        records = {r["@id"]: r for r in payload["recordSet"]}
        self.assertEqual(set(records), {"images", "annotations", "categories"})
        types = {
            f["name"]: f["dataType"] for f in records["annotations"]["field"]
        }
        self.assertEqual(types["bbox"], "cr:BoundingBox")
        self.assertEqual(types["ignore"], "sc:Integer")
        self.assertEqual(types["area"], "sc:Float")
        # Chaque champ dit d'où il vient : sans `source`, un recordSet Croissant
        # est décoratif.
        for record in payload["recordSet"]:
            for field in record["field"]:
                self.assertEqual(
                    field["source"]["fileObject"]["@id"], croissant.COCO_FISH_FILE,
                )
                self.assertTrue(field["source"]["extract"]["jsonPath"])
                self.assertTrue(field["description"].strip())

    def test_yolo_renvoie_vers_le_coco_du_meme_export(self):
        payload = croissant.build_croissant(self._manifest("yolo"), self._files())
        self.assertIn("PERTE", payload["description"])
        # Les recordSet décrivent les fichiers COCO, qui font foi.
        self.assertEqual(
            {r["@id"] for r in payload["recordSet"]},
            {"images", "annotations", "categories"},
        )
        sources = {
            field["source"]["fileObject"]["@id"]
            for record in payload["recordSet"] for field in record["field"]
        }
        self.assertEqual(sources, {croissant.COCO_FISH_FILE})

    def test_suivi_decrit_le_pivot_coco_vid(self):
        for fmt in ("coco_vid", "mot"):
            with self.subTest(fmt=fmt):
                payload = croissant.build_croissant(self._manifest(fmt), self._files())
                self.assertEqual(
                    {r["@id"] for r in payload["recordSet"]},
                    {"videos", "images", "tracks", "annotations"},
                )
                sources = {
                    field["source"]["fileObject"]["@id"]
                    for record in payload["recordSet"] for field in record["field"]
                }
                self.assertEqual(sources, {croissant.COCO_VID_FILE})

    def test_csv_renvoie_vers_le_datapackage_plutot_que_de_redecrire(self):
        payload = croissant.build_croissant(self._manifest("ava"), self._files())
        self.assertNotIn("recordSet", payload)
        self.assertIn("datapackage.json", payload["description"])
        self.assertIn("Frictionless", payload["description"])

    def test_aucun_horodatage_spontane(self):
        """Un fichier haché ne contient jamais de `datetime.now()`."""
        manifest = self._manifest()
        manifest["created_at"] = ""
        payload = croissant.build_croissant(manifest, self._files())
        self.assertEqual(payload["dateCreated"], "")


class CroissantInSealedExportTest(PhotoFixture):
    """Sur un vrai export scellé : cohérence avec le manifeste, et scellement."""

    def test_croissant_ecrit_a_la_racine_de_l_export(self):
        result = self.export("croissant")
        path = result.output_dir / croissant.CROISSANT_NAME
        self.assertTrue(path.is_file(), path)
        payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(payload["@type"], "sc:Dataset")
        self.assertEqual(payload["conformsTo"], croissant.CONFORMS_TO)

    def test_manifeste_et_croissant_disent_la_meme_chose(self):
        result = self.export("coherence")
        manifest = result.manifest
        payload = croissant.load_croissant(result.output_dir)

        self.assertEqual(payload["version"], manifest["dataset_version"])
        self.assertEqual(payload["dateCreated"], manifest["created_at"])
        self.assertIn(manifest["dataset_name"], payload["name"])
        self.assertIn(manifest["git_commit"] or "", payload.get("citeAs", ""))

        # distribution[] == files[] moins croissant.json lui-même.
        declared = {e["@id"]: e.get("sha256") for e in payload["distribution"]}
        expected = {
            row["path"]: row["sha256"]
            for row in manifest["files"]
            if row["path"] != croissant.CROISSANT_NAME
        }
        self.assertEqual(declared, expected)

    def test_croissant_est_scelle_avec_le_reste(self):
        """Il figure dans `files[]` avec sa vraie empreinte, donc dans le condensé."""
        result = self.export("scelle")
        rows = {row["path"]: row for row in result.manifest["files"]}
        self.assertIn(croissant.CROISSANT_NAME, rows)
        path = result.output_dir / croissant.CROISSANT_NAME
        self.assertEqual(rows[croissant.CROISSANT_NAME]["sha256"], sha256_file(path))
        self.assertEqual(rows[croissant.CROISSANT_NAME]["bytes"], path.stat().st_size)
        self.assertEqual(
            result.manifest["croissant"]["file"], croissant.CROISSANT_NAME,
        )

    def test_deux_exports_identiques_donnent_le_meme_croissant(self):
        """Le descripteur ne casse pas la reproductibilité de la phase 2.

        Deux exports de la même base **vivante** n'ont pas le même
        `content_sha256` — l'export y écrit sa propre ligne `export_runs`, donc
        `source.db_snapshot_sha256` change (contrat documenté en phase 2). Ce
        qui doit rester identique au bit près, c'est le contenu de
        `croissant.json` : rien d'horodaté ni d'aléatoire ne s'y glisse.
        """
        first = self.export("repro_a")
        second = self.export("repro_b")
        self.assertEqual(
            (first.output_dir / croissant.CROISSANT_NAME).read_bytes(),
            (second.output_dir / croissant.CROISSANT_NAME).read_bytes(),
        )
        self.assertEqual(
            sha256_file(first.output_dir / croissant.CROISSANT_NAME),
            sha256_file(second.output_dir / croissant.CROISSANT_NAME),
        )

    def test_les_fichiers_annonces_existent_vraiment(self):
        result = self.export("existence")
        payload = croissant.load_croissant(result.output_dir)
        self.assertTrue(payload["distribution"])
        for entry in payload["distribution"]:
            target = result.output_dir / entry["contentUrl"]
            self.assertTrue(target.is_file(), entry["contentUrl"])
            self.assertEqual(sha256_file(target), entry["sha256"])

    def test_le_pivot_coco_annonce_est_bien_livre(self):
        """Les recordSet interrogent un fichier réellement présent."""
        result = self.export("pivot")
        payload = croissant.load_croissant(result.output_dir)
        sources = {
            field["source"]["fileObject"]["@id"]
            for record in payload["recordSet"] for field in record["field"]
        }
        declared = {entry["@id"] for entry in payload["distribution"]}
        for source in sources:
            self.assertIn(source, declared, source)
            self.assertTrue((result.output_dir / source).is_file(), source)


class CroissantMediaTypeTest(unittest.TestCase):
    def test_extensions_connues_et_inconnues(self):
        self.assertEqual(croissant.media_type_for("a/b.json"), "application/json")
        self.assertEqual(croissant.media_type_for("a/b.CSV"), "text/csv")
        self.assertEqual(croissant.media_type_for("mot/x/seqinfo.ini"), "text/plain")
        self.assertEqual(croissant.media_type_for("data.yaml"), "application/yaml")
        self.assertEqual(
            croissant.media_type_for("x.inconnu"), "application/octet-stream",
        )


if __name__ == "__main__":
    unittest.main()
