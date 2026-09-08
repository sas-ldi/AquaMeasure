"""Référentiel embarqué pour les menus famille → genre → espèce."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import func, select

from annotations.helpers import TempDbCase

from src.annodb.connection import init_db, session_scope
from src.annodb.models import TaxonNode
from src.annodb.seed_madagascar import (
    load_madagascar_taxonomy,
    sync_madagascar_taxonomy,
)

REPO_ROOT = Path(__file__).resolve().parents[2] / "src"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


class MadagascarReferenceTest(TempDbCase):
    def test_referentiel_embarque_est_complet_et_coherent(self):
        records = load_madagascar_taxonomy()

        self.assertEqual(len(records), 1812)
        self.assertEqual(len({row["family"] for row in records}), 249)
        self.assertEqual(len({row["genus"] for row in records}), 849)
        self.assertEqual(
            len({str(row["scientific_name"]).casefold() for row in records}),
            len(records),
        )
        self.assertEqual(
            sum(bool(row["common_name"]) for row in records),
            1800,
        )

    def test_sync_est_idempotente_et_preserve_un_nom_local(self):
        init_db(self.db_path, seed=True)
        with session_scope(self.db_path) as session:
            acanthurus = session.scalar(
                select(TaxonNode).where(
                    TaxonNode.rank == "genus",
                    TaxonNode.scientific_name == "Acanthurus",
                )
            )
            self.assertIsNotNone(acanthurus)
            session.add(
                TaxonNode(
                    id="taxon-sp-local-acanthurus-blochii",
                    parent_id=acanthurus.id,
                    rank="species",
                    scientific_name="Acanthurus blochii",
                    common_name="Mon chirurgien local",
                    is_provisional=True,
                )
            )

        with session_scope(self.db_path) as session:
            added_first = sync_madagascar_taxonomy(session)
            self.assertGreater(added_first, 0)

        with session_scope(self.db_path) as session:
            added_second = sync_madagascar_taxonomy(session)
            self.assertEqual(added_second, 0)

            local = session.get(TaxonNode, "taxon-sp-local-acanthurus-blochii")
            self.assertIsNotNone(local)
            self.assertEqual(local.common_name, "Mon chirurgien local")
            self.assertFalse(local.is_provisional)

            genus = session.get(TaxonNode, local.parent_id)
            family = session.get(TaxonNode, genus.parent_id)
            self.assertEqual(genus.scientific_name, "Acanthurus")
            self.assertEqual(family.scientific_name, "Acanthuridae")
            self.assertEqual(family.id, "taxon-fam-acanthuridae")

            shark = session.scalar(
                select(TaxonNode).where(
                    TaxonNode.rank == "species",
                    TaxonNode.scientific_name == "Heptranchias perlo",
                )
            )
            self.assertIsNotNone(shark)
            self.assertEqual(shark.common_name, "requin perlon")
            shark_genus = session.get(TaxonNode, shark.parent_id)
            shark_family = session.get(TaxonNode, shark_genus.parent_id)
            self.assertEqual(shark_genus.scientific_name, "Heptranchias")
            self.assertEqual(shark_family.scientific_name, "Hexanchidae")

            reference_species = session.scalar(
                select(func.count())
                .select_from(TaxonNode)
                .where(TaxonNode.rank == "species")
            )
            self.assertGreaterEqual(reference_species, 1812)

    def test_chargement_application_fonctionne_sans_labels_fishial(self):
        import fish_annotate as fa

        init_db(self.db_path, seed=True)
        with patch("urllib.request.urlretrieve", side_effect=OSError("hors ligne")):
            added = fa.ensure_fishial_taxonomy_if_needed()

        self.assertGreater(added, 0)
        index = fa.build_taxon_index()
        family_names = {node["scientific_name"] for node in index["families"]}
        self.assertIn("Hexanchidae", family_names)
        self.assertIn("Acanthuridae", family_names)
