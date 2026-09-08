"""Un ZIP réutilisable sur un autre poste, avec fusion et sans faux poissons."""

from __future__ import annotations

import copy
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch
import zipfile

import numpy as np
from sqlalchemy import select

from annotations.helpers import FV_ROOT, TempDbCase, write_test_image

sys.path.insert(0, str(FV_ROOT.parent))
import fishial_gallery as gallery
from src.annodb import fishial_transfer as transfer
from src.annodb.connection import init_db, session_scope
from src.annodb.models import CaptureSession, MediaAsset, Project, SpatialAnnotation, TaxonNode, TaxonReferenceEmbedding


SIGNATURE = {"model_sha256": "test-model", "inference_sha256": "test-inference",
             "input_size": [154, 434], "embedding_api": "fishial_embed_crop_v1"}


class FishialTransferTest(TempDbCase):
    def setUp(self):
        super().setUp()
        self.addCleanup(gallery.invalidate_cache)
        self.signature = patch.object(transfer, "model_signature", return_value=SIGNATURE)
        self.signature.start()
        self.addCleanup(self.signature.stop)
        self.settings = self.tmp_path / "settings.json"
        self.settings.write_text('{"fishial_min_refs": 1}', encoding="utf-8")
        self.env = patch.dict(os.environ, {"FISH_VISION_SETTINGS": str(self.settings)})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.storage_config_path.write_text(json.dumps({"data_root": str(self.tmp_path/"storage")}),encoding="utf-8")
        from src.annodb import storage_config
        storage_config.invalidate_cache()
        init_db(self.db_path, seed=False)
        self.image = self.tmp_path/"source.jpg"
        write_test_image(self.image, width=64, height=48)
        with session_scope(self.db_path) as db:
            db.add(Project(id="source-project",name="madagascar_measure"))
            # Le catalogue livré utilise un ancêtre de rang « provisional ».
            db.add(TaxonNode(id="root",rank="provisional",scientific_name="Actinopterygii"))
            db.flush()
            db.add(TaxonNode(id="family",parent_id="root",rank="family",scientific_name="Acanthuridae"))
            db.flush()
            db.add(TaxonNode(id="genus",parent_id="family",rank="genus",scientific_name="Acanthurus"))
            db.flush()
            db.add_all([
                TaxonNode(id="known",parent_id="genus",rank="species",scientific_name="Acanthurus lineatus"),
                TaxonNode(id="new",rank="species",scientific_name="Novafish exemplaris",common_name="Poisson démo",is_provisional=True),
            ])
            db.flush()
            for index in range(2):
                db.add(MediaAsset(id=f"video-{index}",project_id="source-project",media_type="image",
                                  rel_path=str(self.image)+str(index),width=64,height=48))
                db.flush()
                db.add(CaptureSession(id=f"session-{index}",name=f"Sortie {index}",site="Bassin",
                                      session_date=datetime(2026,9,8),left_media_id=f"video-{index}"))
            db.flush()
            for index in range(6):
                taxon = "known" if index < 5 else "new"
                ann = SpatialAnnotation(id=f"ann-{index}",media_id=f"video-{index%2}",
                    frame_index=index,geom_type="bbox",geometry_json='{"x_min":0,"y_min":0,"x_max":64,"y_max":48}',
                    taxon_node_id=taxon,identification_status="identified",reviewed_by="test",source="manual",
                    crop_path=str(self.image))
                db.add(ann)
                db.flush()
                db.add(TaxonReferenceEmbedding(id=f"ref-{index}",taxon_node_id=taxon,
                    spatial_annotation_id=ann.id,media_id=ann.media_id,frame_index=index,
                    embedding=np.array([1,index/10,0.5,0],dtype=np.float32).tobytes(),source="validated"))
        self.destination = self.tmp_path/"destination.db"
        init_db(self.destination,seed=False)

    def export(self, db_path=None):
        result = transfer.export_library(self.tmp_path/"exports",db_path=db_path or self.db_path)
        self.assertEqual(result["reference_count"],6)
        return Path(result["archive_path"])

    def import_to(self, archive, db_path=None):
        return transfer.import_library(archive,db_path=db_path or self.destination,image_root=self.tmp_path/"imported-images")

    def rewrite(self, archive, mutate):
        with zipfile.ZipFile(archive) as z:
            files={name:z.read(name) for name in z.namelist()}
        manifest=json.loads(files["library.json"])
        mutate(manifest,files)
        files["library.json"]=json.dumps(manifest).encode()
        result=self.tmp_path/"modified.zip"
        with zipfile.ZipFile(result,"w") as z:
            for name,data in files.items():
                z.writestr(name,data)
        return result

    def assert_no_imported_rows(self):
        with session_scope(self.destination) as db:
            self.assertEqual(db.query(TaxonReferenceEmbedding).count(),0)
            self.assertEqual(db.query(MediaAsset).count(),0)
            self.assertEqual(db.query(SpatialAnnotation).count(),0)

    def test_all_sessions_export_and_classification_preserved_without_videos(self):
        archive=self.export()
        with zipfile.ZipFile(archive) as z:
            manifest=json.loads(z.read("library.json"))
            self.assertEqual(len(manifest["references"]),6)
            self.assertNotIn(str(self.tmp_path),z.read("library.json").decode())
        with patch.dict(os.environ,{"FISH_VISION_DB":str(self.db_path)}):
            before=gallery.rebuild_centroids()
        result=self.import_to(archive)
        self.assertEqual(result["added"],6)
        with session_scope(self.destination) as db:
            self.assertEqual(db.query(SpatialAnnotation).count(),0)
            self.assertEqual(db.query(CaptureSession).count(),0)
            self.assertEqual(db.get(TaxonNode,"new").common_name,"Poisson démo")
            for ref in db.scalars(select(TaxonReferenceEmbedding)):
                self.assertTrue(transfer.reference_crop_path(db,ref).is_file())
        with patch.dict(os.environ,{"FISH_VISION_DB":str(self.destination)}):
            after=gallery.rebuild_centroids()
            import fish_db_stats
            rows=fish_db_stats.list_species_summary()
            names={row["scientific_name"]:row for row in rows}
            self.assertEqual(names["Acanthurus lineatus"]["gallery_ref_count"],5)
            self.assertEqual(names["Acanthurus lineatus"]["annotation_count"],0)
            image_export=gallery.export_local_library(self.tmp_path/"photos-export")
            self.assertEqual(image_export["crop_count"],6)
        self.assertEqual(set(before),set(after))
        for taxon in before:
            np.testing.assert_array_equal(before[taxon],after[taxon])

    def test_reimport_and_round_trip_do_not_duplicate(self):
        archive=self.export()
        self.import_to(archive)
        repeated=self.import_to(archive)
        self.assertEqual((repeated["added"],repeated["existing"],repeated["total_references"]),(0,6,6))
        returned=self.export(self.destination)
        result=self.import_to(returned,self.db_path)
        self.assertEqual((result["added"],result["existing"]),(0,6))

    def test_merges_scientific_name_and_preserves_destination_reference(self):
        with session_scope(self.destination) as db:
            db.add(Project(id="existing-project",name="existing"))
            db.add(TaxonNode(id="destination-known",rank="species",scientific_name="Acanthurus lineatus",common_name="Nom local"))
            db.flush()
            db.add(MediaAsset(id="existing-image",project_id="existing-project",media_type="image",rel_path="old.jpg"))
            db.flush()
            db.add(TaxonReferenceEmbedding(id="destination-ref",taxon_node_id="destination-known",
                media_id="existing-image",embedding=np.array([0,1,1,0],dtype=np.float32).tobytes(),source="manual"))
        result=self.import_to(self.export())
        self.assertEqual(result["total_references"],7)
        with session_scope(self.destination) as db:
            self.assertEqual(db.get(TaxonReferenceEmbedding,"ref-0").taxon_node_id,"destination-known")
            self.assertEqual(db.get(TaxonNode,"destination-known").common_name,"Nom local")
            self.assertIsNotNone(db.get(TaxonReferenceEmbedding,"destination-ref"))

    def test_missing_crops_do_not_lose_vectors(self):
        self.image.unlink()
        archive=self.export()
        result=self.import_to(archive)
        self.assertEqual((result["added"],result["images_added"]),(6,0))
        repeated=self.export(self.destination)
        with zipfile.ZipFile(repeated) as z:
            self.assertEqual(json.loads(z.read("library.json"))["references_without_image"],6)

    def test_model_mismatch_fails_before_database_writes(self):
        archive=self.export()
        with patch.object(transfer,"model_signature",return_value={"different":"model"}):
            with self.assertRaisesRegex(ValueError,"Modèle Fishial différent"):
                self.import_to(archive)
        self.assert_no_imported_rows()

    def test_reimport_can_restore_previously_missing_images(self):
        with_images=self.export()
        self.image.unlink()
        without_images=self.export()
        self.import_to(without_images)
        result=self.import_to(with_images)
        self.assertEqual((result["added"],result["existing"]),(0,6))
        self.assertGreater(result["images_added"],0)
        with session_scope(self.destination) as db:
            for ref in db.scalars(select(TaxonReferenceEmbedding)):
                self.assertTrue(transfer.reference_crop_path(db,ref).is_file())

    def test_export_keeps_vectors_when_a_local_photo_is_corrupt(self):
        self.image.write_bytes(b"image corrompue")
        result=self.import_to(self.export())
        self.assertEqual((result["added"],result["images_added"]),(6,0))

    def test_vector_dimension_mismatch_preserves_existing_bank(self):
        with session_scope(self.destination) as db:
            db.add(Project(id="p",name="p")); db.add(TaxonNode(id="sp",rank="species",scientific_name="Other fish")); db.flush()
            db.add(MediaAsset(id="m",project_id="p",media_type="image",rel_path="m.jpg")); db.flush()
            db.add(TaxonReferenceEmbedding(id="old",taxon_node_id="sp",media_id="m",source="manual",embedding=np.ones(8,dtype=np.float32).tobytes()))
        with self.assertRaisesRegex(ValueError,"dimension"):
            self.import_to(self.export())
        with session_scope(self.destination) as db:
            self.assertEqual(db.query(TaxonReferenceEmbedding).count(),1)
            self.assertIsNotNone(db.get(TaxonReferenceEmbedding,"old"))

    def test_conflicting_reference_is_not_overwritten(self):
        archive=self.export()
        self.import_to(archive)
        with session_scope(self.destination) as db:
            ref=db.get(TaxonReferenceEmbedding,"ref-0")
            ref.embedding=np.ones(4,dtype=np.float32).tobytes()
        result=self.import_to(archive)
        self.assertEqual((result["added"],result["existing"],result["conflicts"]),(0,5,1))
        with session_scope(self.destination) as db:
            self.assertEqual(db.get(TaxonReferenceEmbedding,"ref-0").embedding,np.ones(4,dtype=np.float32).tobytes())

    def test_invalid_archives_are_atomic(self):
        archive=self.export()
        def bad_vector(manifest,files):
            row=manifest["references"][-1]
            data=np.full(4,np.nan,dtype=np.float32).tobytes()
            files[row["vector_file"]]=data
            row["vector_sha256"]=hashlib.sha256(data).hexdigest()
        def bad_image(manifest,files):
            image=manifest["references"][-1]["image"]
            files[image["file"]]=b"not-an-image"
            image["sha256"]=hashlib.sha256(b"not-an-image").hexdigest()
        def cycle(manifest,files):
            for n in manifest["taxa"]:
                if n["id"]=="family": n["parent_id"]="known"
        mutations=[bad_vector,bad_image,cycle,
                   lambda m,f:f.update({"../outside.txt":b"bad"}),
                   lambda m,f:m["references"].append(copy.deepcopy(m["references"][0]))]
        for mutate in mutations:
            with self.subTest(case=mutate.__name__):
                with self.assertRaises(ValueError):
                    self.import_to(self.rewrite(archive,mutate))
                self.assert_no_imported_rows()

    def test_imported_references_count_toward_incremental_threshold(self):
        self.import_to(self.export())
        with patch.dict(os.environ,{"FISH_VISION_DB":str(self.destination)}):
            import fish_db_stats
            result=fish_db_stats.species_promotion_eligibility("known",min_refs=5)
            self.assertTrue(result["eligible"],result)
