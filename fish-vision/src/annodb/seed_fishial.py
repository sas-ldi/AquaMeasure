"""Import Fishial labels.json + especes supplementaires dans taxon_nodes."""

from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import TaxonNode
from .taxonomy import add_taxon, get_node_by_name, resolve_to_rank

FISHIAL_MISC_FAMILY_ID = "taxon-fam-fishial-misc"
TAXON_ROOT_FISH_ID = "taxon-root-fish"

# Especes IA absentes de certaines versions du bundle Fishial.
SUPPLEMENTAL_SPECIES: tuple[str, ...] = (
    "Chaetodon lunula",
    "Atypichthys strigatus",
    "Anguilla australis",
    "Pteroplatytrygon violacea",
)

# Genres recifaux courants -> familles deja presentes dans seed_taxonomy.sql
GENUS_FAMILY_HINTS: dict[str, str] = {
    "Chromis": "taxon-fam-pomacentridae",
    "Abudefduf": "taxon-fam-pomacentridae",
    "Dascyllus": "taxon-fam-pomacentridae",
    "Amphiprion": "taxon-fam-pomacentridae",
    "Chaetodon": "taxon-fam-chaetodontidae",
    "Heniochus": "taxon-fam-chaetodontidae",
    "Scarus": "taxon-fam-scaridae",
    "Sparisoma": "taxon-fam-scaridae",
    "Labroides": "taxon-fam-labridae",
    "Thalassoma": "taxon-fam-labridae",
    "Halichoeres": "taxon-fam-labridae",
    "Balistoides": "taxon-fam-balistidae",
    "Balistes": "taxon-fam-balistidae",
    "Caranx": "taxon-fam-carangidae",
    "Seriola": "taxon-fam-carangidae",
    "Lutjanus": "taxon-fam-lutjanidae",
    "Scomber": "taxon-fam-scombridae",
    "Sarda": "taxon-fam-scombridae",
    "Epinephelus": "taxon-fam-serranidae",
    "Cephalopholis": "taxon-fam-serranidae",
    "Zanclus": "taxon-fam-zanclidae",
    "Pomacanthus": "taxon-fam-pomacanthidae",
    "Pygoplites": "taxon-fam-pomacanthidae",
    "Acanthurus": "taxon-fam-acanthuridae",
    "Ctenochaetus": "taxon-fam-acanthuridae",
    "Zebrasoma": "taxon-fam-acanthuridae",
    "Naso": "taxon-fam-acanthuridae",
    "Paracanthurus": "taxon-fam-acanthuridae",
    "Pomatomus": "taxon-fam-carangidae",
    "Labrus": "taxon-fam-labridae",
    "Clepticus": "taxon-fam-labridae",
    "Trachinotus": "taxon-fam-carangidae",
    "Sciaenops": "taxon-fam-sciaenidae-fishial",
    "Cyprinus": "taxon-fam-cyprinidae-fishial",
    "Atypichthys": "taxon-fam-fishial-misc",
    "Pteroplatytrygon": "taxon-fam-madagascar-dasyatidae",
}


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s[:80] or str(uuid.uuid4())[:8]


def normalize_scientific_name(raw: str) -> str:
    """Normalise un nom latin (Genus epithet, hybrides avec X)."""
    raw = (raw or "").strip()
    if not raw:
        return ""
    out: list[str] = []
    cap_next = True
    for token in raw.split():
        if token.lower() == "x":
            out.append("X")
            cap_next = True
        elif cap_next:
            out.append(token[0].upper() + token[1:].lower())
            cap_next = False
        else:
            out.append(token.lower())
    return " ".join(out)


def _find_species_ci(session: Session, sci_name: str) -> Optional[TaxonNode]:
    from sqlalchemy import func

    return session.scalar(
        select(TaxonNode).where(
            TaxonNode.rank == "species",
            func.lower(TaxonNode.scientific_name) == sci_name.lower(),
        )
    )


def genus_from_species(sci_name: str) -> str:
    parts = sci_name.split()
    return parts[0] if parts else sci_name


def _labels_path() -> Optional[Path]:
    root = Path(__file__).resolve().parents[2]
    for rel in ("models/fishial_labels.json",):
        p = root / rel
        if p.is_file():
            return p
    p = root / "models" / "fishial_labels.json"
    return p if p.is_file() else None


def fishial_species_count(session: Session) -> int:
    return int(
        session.scalar(
            select(func.count())
            .select_from(TaxonNode)
            .where(
                TaxonNode.rank == "species",
                TaxonNode.id.like("taxon-sp-fishial-%"),
            )
        )
        or 0
    )


def load_fishial_labels(path: Path | None = None) -> dict[str, str]:
    """Charge labels.json Fishial (index -> nom scientifique)."""
    p = path or _labels_path()
    if p is None or not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): normalize_scientific_name(str(v)) for k, v in data.items() if str(v).strip()}


def ensure_misc_family(session: Session) -> str:
    if get_node_by_name(session, "Autres (Fishial)") is None:
        add_taxon(
            session,
            node_id=FISHIAL_MISC_FAMILY_ID,
            rank="family",
            scientific_name="Autres (Fishial)",
            common_name="Especes non classees",
            parent_id=TAXON_ROOT_FISH_ID,
            is_provisional=True,
        )
    return FISHIAL_MISC_FAMILY_ID


def _ensure_extra_families(session: Session) -> None:
    """Familles pour genres hors seed Madagascar."""
    extras = (
        ("taxon-fam-sciaenidae-fishial", "Sciaenidae", "Sciaens"),
        ("taxon-fam-cyprinidae-fishial", "Cyprinidae", "Carpes"),
    )
    for fam_id, sci, common in extras:
        if session.get(TaxonNode, fam_id) is None:
            add_taxon(
                session,
                node_id=fam_id,
                rank="family",
                scientific_name=sci,
                common_name=common,
                parent_id=TAXON_ROOT_FISH_ID,
                is_provisional=True,
            )


def _resolve_family_id(
    session: Session,
    genus_name: str,
    family_name: Optional[str] = None,
) -> str:
    ensure_misc_family(session)
    _ensure_extra_families(session)

    if family_name:
        fam = get_node_by_name(session, family_name)
        if fam and fam.rank == "family":
            return fam.id
        node = add_taxon(
            session,
            rank="family",
            scientific_name=family_name,
            parent_id=TAXON_ROOT_FISH_ID,
            is_provisional=True,
            node_id=f"taxon-fam-user-{_slug(family_name)}",
        )
        return node.id

    hint = GENUS_FAMILY_HINTS.get(genus_name)
    if hint and session.get(TaxonNode, hint):
        return hint

    existing_genus = get_node_by_name(session, genus_name)
    if existing_genus and existing_genus.rank == "genus":
        fam_id = resolve_to_rank(session, existing_genus.id, "family")
        if fam_id:
            return fam_id

    return FISHIAL_MISC_FAMILY_ID


def _ensure_genus(
    session: Session,
    genus_name: str,
    family_id: str,
) -> str:
    existing = get_node_by_name(session, genus_name)
    if existing and existing.rank == "genus":
        return existing.id
    node = add_taxon(
        session,
        rank="genus",
        scientific_name=genus_name,
        parent_id=family_id,
        is_provisional=family_id == FISHIAL_MISC_FAMILY_ID,
        node_id=f"taxon-gen-user-{_slug(genus_name)}",
    )
    return node.id


def ensure_binomial_species(
    session: Session,
    sci_name: str,
    *,
    genus_name: Optional[str] = None,
    family_name: Optional[str] = None,
    source: str = "fishial",
) -> str:
    """Cree ou retourne l'id d'une espece (nom scientifique complet)."""
    sci_name = normalize_scientific_name(sci_name)
    if not sci_name:
        raise ValueError("Nom d'espece vide")

    parts = sci_name.split()
    if len(parts) < 2:
        raise ValueError(f"Binome invalide : {sci_name!r}")

    genus_name = genus_name or genus_from_species(sci_name)

    existing = _find_species_ci(session, sci_name)
    if existing:
        return existing.id

    prefix = "fishial" if source == "fishial" else "user"
    node_id = f"taxon-sp-{prefix}-{_slug(sci_name)}"
    by_id = session.get(TaxonNode, node_id)
    if by_id and by_id.rank == "species":
        return by_id.id

    family_id = _resolve_family_id(session, genus_name, family_name)
    genus_id = _ensure_genus(session, genus_name, family_id)
    node = add_taxon(
        session,
        rank="species",
        scientific_name=sci_name,
        parent_id=genus_id,
        node_id=node_id,
        # Un binôme créé par la saisie utilisateur reste identifiable comme
        # classe locale, même après validation de l'observation. La ligne
        # TaxonNode est la source durable ; le drapeau de l'annotation décrit
        # seulement son propre cycle de validation.
        is_provisional=source == "user",
    )
    return node.id


def ensure_family_name(session: Session, family_name: str) -> str:
    family_name = family_name.strip()
    existing = get_node_by_name(session, family_name)
    if existing and existing.rank == "family":
        return existing.id
    node = add_taxon(
        session,
        rank="family",
        scientific_name=family_name,
        parent_id=TAXON_ROOT_FISH_ID,
        is_provisional=True,
        node_id=f"taxon-fam-user-{_slug(family_name)}",
    )
    return node.id


def ensure_genus_name(
    session: Session,
    genus_name: str,
    family_name: Optional[str] = None,
    family_id: Optional[str] = None,
) -> str:
    genus_name = genus_name.strip()
    existing = get_node_by_name(session, genus_name)
    if existing and existing.rank == "genus":
        return existing.id
    if not family_id:
        family_id = _resolve_family_id(session, genus_name, family_name)
    return _ensure_genus(session, genus_name, family_id)


def sync_supplemental_species(session: Session) -> int:
    added = 0
    for sci in SUPPLEMENTAL_SPECIES:
        if get_node_by_name(session, sci) is None:
            ensure_binomial_species(session, sci, source="supplemental")
            added += 1
    return added


def ensure_fishial_taxonomy(session: Session, labels_path: Path | None = None) -> int:
    """Synchronise le référentiel Madagascar puis les espèces Fishial."""
    from .seed_madagascar import sync_madagascar_taxonomy

    added = sync_madagascar_taxonomy(session)
    added += sync_supplemental_species(session)

    path = labels_path or _labels_path()
    labels = load_fishial_labels(path) if path else {}
    if not labels:
        return added

    ensure_misc_family(session)
    _ensure_extra_families(session)

    for _key, sci_name in labels.items():
        if len(sci_name.split()) < 2:
            continue
        if _find_species_ci(session, sci_name):
            continue
        ensure_binomial_species(
            session,
            sci_name,
            genus_name=genus_from_species(sci_name),
            source="fishial",
        )
        added += 1

    session.flush()
    return added
