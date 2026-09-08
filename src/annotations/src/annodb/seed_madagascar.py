"""Synchronisation du référentiel taxonomique des poissons de Madagascar.

Le fichier embarqué ne constitue pas un modèle d'IA : il alimente uniquement
les choix famille, genre et espèce proposés par l'interface. La synchronisation
est idempotente et conserve les noms vernaculaires déjà renseignés localement.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import TaxonNode

TAXON_ROOT_FISH_ID = "taxon-root-fish"
REFERENCE_PATH = Path(__file__).resolve().parents[2] / "db" / "madagascar_taxonomy.json"


def _clean(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    return slug[:100] or "taxon"


def load_madagascar_taxonomy(path: Path | None = None) -> list[dict[str, str | None]]:
    """Charge et valide les rangs nécessaires aux menus taxonomiques."""
    source = path or REFERENCE_PATH
    if not source.is_file():
        return []

    payload = json.loads(source.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1 or not isinstance(payload.get("records"), list):
        raise ValueError(f"Référentiel taxonomique invalide : {source}")

    records: list[dict[str, str | None]] = []
    seen_species: set[str] = set()
    genus_families: dict[str, str] = {}
    for raw in payload["records"]:
        if not isinstance(raw, dict):
            raise ValueError(f"Entrée taxonomique invalide : {raw!r}")
        family = _clean(raw.get("family"))
        genus = _clean(raw.get("genus"))
        scientific_name = _clean(raw.get("scientific_name"))
        common_name = _clean(raw.get("common_name")) or None
        if not family or not genus or not scientific_name:
            raise ValueError(f"Entrée taxonomique incomplète : {raw!r}")
        if scientific_name.split()[0].casefold() != genus.casefold():
            raise ValueError(
                f"Genre incohérent pour {scientific_name!r} : {genus!r}"
            )

        species_key = scientific_name.casefold()
        if species_key in seen_species:
            raise ValueError(f"Espèce dupliquée : {scientific_name}")
        seen_species.add(species_key)

        genus_key = genus.casefold()
        previous_family = genus_families.setdefault(genus_key, family)
        if previous_family.casefold() != family.casefold():
            raise ValueError(
                f"Familles contradictoires pour le genre {genus!r} : "
                f"{previous_family!r} et {family!r}"
            )
        records.append(
            {
                "family": family,
                "genus": genus,
                "scientific_name": scientific_name,
                "common_name": common_name,
            }
        )
    return records


def _new_id(prefix: str, scientific_name: str, used_ids: set[str]) -> str:
    base = f"taxon-{prefix}-madagascar-{_slug(scientific_name)}"
    candidate = base
    suffix = 2
    while candidate in used_ids:
        candidate = f"{base}-{suffix}"
        suffix += 1
    used_ids.add(candidate)
    return candidate


def _update_canonical_node(
    node: TaxonNode,
    *,
    parent_id: str,
    common_name: str | None = None,
) -> None:
    if node.parent_id != parent_id:
        node.parent_id = parent_id
    if node.is_provisional:
        node.is_provisional = False
    # Un nom choisi ou corrigé dans la base locale reste prioritaire.
    if common_name and not _clean(node.common_name):
        node.common_name = common_name


def sync_madagascar_taxonomy(
    session: Session,
    path: Path | None = None,
) -> int:
    """Ajoute ou consolide le référentiel ; retourne le nombre de nœuds créés."""
    records = load_madagascar_taxonomy(path)
    if not records:
        return 0

    nodes = list(session.scalars(select(TaxonNode)))
    by_rank_name = {
        (node.rank, node.scientific_name.casefold()): node
        for node in nodes
    }
    used_ids = {node.id for node in nodes}
    added = 0

    root = session.get(TaxonNode, TAXON_ROOT_FISH_ID)
    if root is None:
        root = TaxonNode(
            id=TAXON_ROOT_FISH_ID,
            parent_id=None,
            rank="provisional",
            scientific_name="Actinopterygii",
            common_name="Poisson",
            is_provisional=False,
        )
        session.add(root)
        by_rank_name[(root.rank, root.scientific_name.casefold())] = root
        used_ids.add(root.id)
        added += 1
        session.flush()

    family_names = sorted({str(record["family"]) for record in records}, key=str.casefold)
    families: dict[str, TaxonNode] = {}
    for family_name in family_names:
        key = ("family", family_name.casefold())
        family = by_rank_name.get(key)
        if family is None:
            family = TaxonNode(
                id=_new_id("fam", family_name, used_ids),
                parent_id=root.id,
                rank="family",
                scientific_name=family_name,
                is_provisional=False,
            )
            session.add(family)
            by_rank_name[key] = family
            added += 1
        else:
            _update_canonical_node(family, parent_id=root.id)
        families[family_name.casefold()] = family
    session.flush()

    genus_to_family = {
        str(record["genus"]): str(record["family"])
        for record in records
    }
    genera: dict[str, TaxonNode] = {}
    for genus_name in sorted(genus_to_family, key=str.casefold):
        family = families[genus_to_family[genus_name].casefold()]
        key = ("genus", genus_name.casefold())
        genus = by_rank_name.get(key)
        if genus is None:
            genus = TaxonNode(
                id=_new_id("gen", genus_name, used_ids),
                parent_id=family.id,
                rank="genus",
                scientific_name=genus_name,
                is_provisional=False,
            )
            session.add(genus)
            by_rank_name[key] = genus
            added += 1
        else:
            _update_canonical_node(genus, parent_id=family.id)
        genera[genus_name.casefold()] = genus
    session.flush()

    for record in records:
        scientific_name = str(record["scientific_name"])
        genus = genera[str(record["genus"]).casefold()]
        key = ("species", scientific_name.casefold())
        species = by_rank_name.get(key)
        if species is None:
            species = TaxonNode(
                id=_new_id("sp", scientific_name, used_ids),
                parent_id=genus.id,
                rank="species",
                scientific_name=scientific_name,
                common_name=record["common_name"],
                is_provisional=False,
            )
            session.add(species)
            by_rank_name[key] = species
            added += 1
        else:
            _update_canonical_node(
                species,
                parent_id=genus.id,
                common_name=record["common_name"],
            )
    session.flush()
    return added


__all__ = [
    "REFERENCE_PATH",
    "load_madagascar_taxonomy",
    "sync_madagascar_taxonomy",
]
