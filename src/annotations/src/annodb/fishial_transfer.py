"""Transfert de la bibliothèque Fishial locale, sans observations artificielles."""

from __future__ import annotations

from collections import Counter
from datetime import datetime
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import tempfile
import uuid
import zipfile

import numpy as np
from sqlalchemy import select

from .connection import session_scope
from .embeddings import normalize_embedding
from .models import MediaAsset, Project, SpatialAnnotation, TaxonNode, TaxonReferenceEmbedding
from .storage_config import media_dir

FORMAT = "aquameasure.fishial-local.v1"
IMPORT_PROJECT = "fishial_local_imports"
MAX_REFERENCES = 100_000
MAX_IMAGE_BYTES = 32 * 1024 * 1024
MAX_MANIFEST_BYTES = 64 * 1024 * 1024
MAX_ARCHIVE_BYTES = 8 * 1024**3
MAX_DIMENSION = 16_384


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _file_sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_image(data: bytes) -> None:
    from PIL import Image

    try:
        with Image.open(io.BytesIO(data)) as im:
            im.verify()
    except Exception as exc:
        raise ValueError("Cliché Fishial illisible.") from exc


def model_signature() -> dict:
    """Même extracteur sur les deux postes ; les poids ne voyagent pas dans le ZIP."""
    from fishial_classify import default_model_dir, _read_input_size

    folder = default_model_dir()
    weights = folder / "model.pt"
    inference = folder / "inference.py"
    if not weights.is_file() or not inference.is_file():
        raise ValueError("Installez le modèle Fishial sur ce poste avant le transfert de la bibliothèque.")
    return {
        "model_sha256": _file_sha(weights),
        "inference_sha256": _file_sha(inference),
        "input_size": list(_read_input_size(folder)),
        "embedding_api": "fishial_embed_crop_v1",
    }


def reference_crop_path(session, reference) -> Path | None:
    if reference.spatial_annotation_id:
        ann = session.get(SpatialAnnotation, reference.spatial_annotation_id)
        path = Path(ann.crop_path or "") if ann else None
    else:
        media = session.get(MediaAsset, reference.media_id)
        try:
            imported = json.loads(media.notes or "{}").get("fishial_local_reference") if media else False
        except (ValueError, AttributeError):
            imported = False
        path = Path(media.rel_path) if imported else None
    return path if path is not None and path.is_file() else None


def _taxa(session, taxon_ids: set[str]) -> list[dict]:
    rows = {}
    for taxon_id in sorted(taxon_ids):
        seen = set()
        node = session.get(TaxonNode, taxon_id)
        while node is not None:
            if node.id in seen:
                raise ValueError("Hiérarchie taxonomique circulaire : " + node.scientific_name)
            seen.add(node.id)
            rows[node.id] = {
                "id": node.id, "parent_id": node.parent_id, "rank": node.rank,
                "scientific_name": node.scientific_name,
                "common_name": node.common_name, "is_provisional": bool(node.is_provisional),
            }
            node = session.get(TaxonNode, node.parent_id) if node.parent_id else None
    return list(rows.values())


def export_library(output_root: Path, *, db_path: Path | None = None) -> dict:
    from fishial_gallery import active_reference_vectors
    from .app_settings import get_setting

    signature = model_signature()
    output_root = Path(output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    name = f"fishial_local_{stamp}_{uuid.uuid4().hex[:6]}"
    target = output_root / name
    with tempfile.TemporaryDirectory(prefix=".fishial-export-", dir=output_root) as temp:
        package = Path(temp)
        archive = package / "Fishial_local.zip"
        with session_scope(db_path) as session:
            candidates = active_reference_vectors(session)
            candidates = [(ref, vec) for ref, vec in candidates
                          if (node := session.get(TaxonNode, ref.taxon_node_id)) is not None
                          and node.rank == "species"]
            if not candidates:
                raise ValueError("Aucune référence Fishial à transférer. Cliquez d’abord sur Ajouter toutes les nouvelles images.")
            if len(candidates) > MAX_REFERENCES:
                raise ValueError("La bibliothèque dépasse la capacité de ce format de transfert.")
            rows, written_images = [], set()
            missing_images = 0
            with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as z:
                for index, (ref, vector) in enumerate(candidates):
                    raw = vector.astype("<f4").tobytes()
                    vector_path = f"references/{index:06d}.f32"
                    z.writestr(vector_path, raw)
                    crop = reference_crop_path(session, ref)
                    image = None
                    data = None
                    try:
                        if crop is not None and crop.stat().st_size <= MAX_IMAGE_BYTES:
                            data = crop.read_bytes()
                            _validate_image(data)
                    except (OSError, ValueError):
                        data = None
                    if data is not None:
                        image_hash = _sha(data)
                        image = {"file": f"images/{image_hash}.jpg", "sha256": image_hash}
                        if image["file"] not in written_images:
                            z.writestr(image["file"], data)
                            written_images.add(image["file"])
                    else:
                        missing_images += 1
                    rows.append({
                        "id": ref.id, "taxon_id": ref.taxon_node_id,
                        "vector_file": vector_path, "vector_sha256": _sha(raw),
                        "dimension": int(vector.size), "image": image,
                    })
                taxa = _taxa(session, {row["taxon_id"] for row in rows})
                manifest = {
                    "format": FORMAT, "created_at": datetime.now().isoformat(timespec="seconds"),
                    "model": signature, "taxa": taxa, "references": rows,
                    "reference_count": len(rows), "species_count": len({r["taxon_id"] for r in rows}),
                    "references_without_image": missing_images,
                    "activation_min_refs": int(get_setting("fishial_min_refs", 5)),
                }
                z.writestr("library.json", json.dumps(manifest, ensure_ascii=False, indent=2))
                z.writestr("LISEZ-MOI.txt", (
                    "AquaMeasure - Fishial local\n\n"
                    "Sur le poste destinataire : Données & IA > Fishial > Importer un Fishial local.\n"
                    "Le ZIP contient les références déjà ajoutées à Fishial, toutes sessions confondues,\n"
                    "leurs espèces et les clichés encore disponibles. Les vidéos ne sont pas nécessaires.\n"
                    "Les références existantes sont conservées. Réimporter le même ZIP ne les double pas.\n"
                    "Le même modèle Fishial doit être installé sur les deux postes.\n"
                    "Les poids officiels et les observations du registre ne sont pas inclus.\n"
                    "Le seuil d’activation du poste destinataire reste réglable dans Réglages avancés.\n"
                ).encode("utf-8"))
        target.mkdir()
        archive.replace(target / archive.name)
    message = f"Fishial local exporté : {len(rows)} référence(s), {manifest['species_count']} espèce(s). Copiez Fishial_local.zip sur l’autre PC."
    if missing_images:
        message += f" {missing_images} référence(s) sans cliché disponible ; leurs vecteurs sont conservés."
    return {"output_path": str(target.resolve()), "archive_path": str((target / "Fishial_local.zip").resolve()),
            "reference_count": len(rows), "species_count": manifest["species_count"],
            "references_without_image": missing_images, "message": message}


def _text(value, field: str, *, maximum: int = 500) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum or "\x00" in value:
        raise ValueError("Champ invalide dans la bibliothèque : " + field)
    return value


def _read_member(z: zipfile.ZipFile, name: str, limit: int) -> bytes:
    _text(name, "fichier")
    try:
        info = z.getinfo(name)
    except KeyError as exc:
        raise ValueError("Fichier manquant dans la bibliothèque : " + name) from exc
    if info.file_size > limit:
        raise ValueError("Fichier trop volumineux dans la bibliothèque : " + name)
    return z.read(info)


def _load_package(z: zipfile.ZipFile) -> tuple[dict, list[dict]]:
    infos = z.infolist()
    names = [entry.filename for entry in infos]
    if len(names) != len(set(names)) or len(names) > MAX_REFERENCES * 3 + 10:
        raise ValueError("Archive Fishial invalide : fichiers répétés ou trop nombreux.")
    if sum(entry.file_size for entry in infos) > MAX_ARCHIVE_BYTES:
        raise ValueError("Archive Fishial trop volumineuse.")
    for name in names:
        p = PurePosixPath(name)
        if p.is_absolute() or ".." in p.parts or "\\" in name or ":" in name:
            raise ValueError("Chemin invalide dans l’archive Fishial.")
    try:
        manifest = json.loads(_read_member(z, "library.json", MAX_MANIFEST_BYTES))
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise ValueError("Manifeste Fishial illisible.") from exc
    if not isinstance(manifest, dict) or manifest.get("format") != FORMAT:
        raise ValueError("Ce fichier n’est pas un export de Fishial local AquaMeasure.")
    if manifest.get("model") != model_signature():
        raise ValueError("Modèle Fishial différent : installez la même version que sur le poste d’origine avant d’importer ce ZIP.")
    taxa = manifest.get("taxa")
    references = manifest.get("references")
    if not isinstance(taxa, list) or not isinstance(references, list) or not 0 < len(references) <= MAX_REFERENCES:
        raise ValueError("La bibliothèque ne contient pas de références valides.")
    nodes = {}
    for node in taxa:
        if not isinstance(node, dict):
            raise ValueError("Taxonomie invalide.")
        ident = _text(node.get("id"), "taxon.id")
        if ident in nodes:
            raise ValueError("Identifiant taxonomique répété.")
        if node.get("rank") not in ("kingdom", "phylum", "class", "order", "family", "genus", "species", "provisional"):
            raise ValueError("Rang taxonomique non pris en charge.")
        _text(node.get("scientific_name"), "nom scientifique")
        if node.get("common_name") is not None and not isinstance(node["common_name"], str):
            raise ValueError("Nom commun invalide.")
        nodes[ident] = node
    for node in nodes.values():
        seen = {node["id"]}
        parent = node.get("parent_id")
        while parent:
            if not isinstance(parent, str) or parent not in nodes or parent in seen:
                raise ValueError("Hiérarchie taxonomique invalide.")
            seen.add(parent)
            if len(seen) > 16:
                raise ValueError("Hiérarchie taxonomique trop profonde.")
            parent = nodes[parent].get("parent_id")
    rows, ids, dimensions = [], set(), set()
    vector_bytes = 0
    for ref in references:
        if not isinstance(ref, dict):
            raise ValueError("Référence invalide.")
        ident = _text(ref.get("id"), "référence.id")
        if ident in ids:
            raise ValueError("Référence répétée dans le ZIP.")
        ids.add(ident)
        taxon = nodes.get(ref.get("taxon_id"))
        if not taxon or taxon["rank"] != "species":
            raise ValueError("Espèce manquante pour une référence.")
        dimension = ref.get("dimension")
        if not isinstance(dimension, int) or not 0 < dimension <= MAX_DIMENSION:
            raise ValueError("Dimension de vecteur invalide.")
        raw = _read_member(z, ref.get("vector_file"), MAX_DIMENSION * 4)
        vector_bytes += len(raw)
        if vector_bytes > 256 * 1024 * 1024:
            raise ValueError("Les vecteurs dépassent la capacité de ce format de transfert.")
        vec = normalize_embedding(np.frombuffer(raw, dtype="<f4"), expected_dimension=dimension) if len(raw) % 4 == 0 else None
        if vec is None or _sha(raw) != ref.get("vector_sha256"):
            raise ValueError("Vecteur Fishial invalide ou endommagé.")
        dimensions.add(dimension)
        image = ref.get("image")
        if image is not None:
            if not isinstance(image, dict):
                raise ValueError("Cliché Fishial invalide.")
            data = _read_member(z, image.get("file"), MAX_IMAGE_BYTES)
            if _sha(data) != image.get("sha256"):
                raise ValueError("Cliché Fishial endommagé.")
            _validate_image(data)
        rows.append({**ref, "embedding": vec.astype(np.float32).tobytes()})
    if len(dimensions) != 1:
        raise ValueError("Le ZIP mélange des dimensions de vecteurs différentes.")
    return manifest, rows


def _merge_taxa(session, nodes: list[dict]) -> dict[str, str]:
    existing = session.scalars(select(TaxonNode)).all()
    by_name = {(n.rank, n.scientific_name.strip().casefold()): n for n in existing}
    by_id = {n.id: n for n in existing}
    remaining = {n["id"]: n for n in nodes}
    mapped = {}
    while remaining:
        for key, node in list(remaining.items()):
            parent = node.get("parent_id")
            if parent and parent not in mapped:
                continue
            name_key = (node["rank"], node["scientific_name"].strip().casefold())
            match = by_name.get(name_key)
            if match is None:
                ident = key if key not in by_id else str(uuid.uuid4())
                match = TaxonNode(id=ident, parent_id=mapped.get(parent), rank=node["rank"],
                                  scientific_name=node["scientific_name"].strip(),
                                  common_name=node.get("common_name"),
                                  is_provisional=bool(node.get("is_provisional")))
                session.add(match)
                session.flush()
                by_name[name_key] = match
                by_id[ident] = match
            mapped[key] = match.id
            del remaining[key]
    return mapped


def _import_media(session, project, ref, z, root, created_images):
    root.mkdir(parents=True, exist_ok=True)
    image = ref.get("image")
    path = root / (image["sha256"] + ".jpg" if image else ref["vector_sha256"] + ".sans-image")
    copied = 0
    if image is not None:
        if path.exists():
            if _file_sha(path) != image["sha256"]:
                raise ValueError("Un cliché local porte le même nom avec un contenu différent.")
        else:
            with path.open("xb") as file:
                file.write(_read_member(z, image["file"], MAX_IMAGE_BYTES))
            created_images.append(path)
            copied = 1
    media = session.scalar(select(MediaAsset).where(
        MediaAsset.project_id == project.id, MediaAsset.rel_path == str(path.resolve())))
    if media is None:
        media = MediaAsset(id=str(uuid.uuid4()), project_id=project.id,
                           media_type="image", rel_path=str(path.resolve()),
                           notes=json.dumps({"fishial_local_reference": True,
                                             "reference_id": ref["id"]}))
        session.add(media)
        session.flush()
    return media, copied


def import_library(archive: Path, *, db_path: Path | None = None, image_root: Path | None = None) -> dict:
    from fishial_gallery import active_reference_vectors, invalidate_cache

    archive = Path(archive)
    root = Path(image_root) if image_root is not None else media_dir() / "fishial_imports"
    created_images = []
    try:
        with zipfile.ZipFile(archive) as z:
            manifest, rows = _load_package(z)  # Tout valider avant la première écriture en base.
            with session_scope(db_path) as session:
                current = active_reference_vectors(session)
                if current and current[0][1].size != rows[0]["dimension"]:
                    raise ValueError("Les références présentes ont une autre dimension : l’import est annulé.")
                mapping = _merge_taxa(session, manifest["taxa"])
                project = session.scalar(select(Project).where(Project.name == IMPORT_PROJECT))
                if project is None:
                    project = Project(id=str(uuid.uuid4()), name=IMPORT_PROJECT,
                                      description="Clichés de référence Fishial transférés entre postes")
                    session.add(project)
                    session.flush()
                added, existing, conflicts, images_added = 0, 0, 0, 0
                for ref in rows:
                    taxon_id = mapping[ref["taxon_id"]]
                    old = session.get(TaxonReferenceEmbedding, ref["id"])
                    if old is not None:
                        if old.taxon_node_id == taxon_id and old.embedding == ref["embedding"]:
                            existing += 1
                            old_media = session.get(MediaAsset, old.media_id)
                            if (ref.get("image") and not old.spatial_annotation_id
                                    and old_media is not None and old_media.project_id == project.id
                                    and reference_crop_path(session, old) is None):
                                media, copied = _import_media(session, project, ref, z, root, created_images)
                                old.media_id = media.id
                                images_added += copied
                        else:
                            conflicts += 1  # Une référence locale corrigée reste prioritaire.
                        continue
                    media, copied = _import_media(session, project, ref, z, root, created_images)
                    images_added += copied
                    session.add(TaxonReferenceEmbedding(id=ref["id"], taxon_node_id=taxon_id,
                        spatial_annotation_id=None, media_id=media.id, frame_index=0,
                        embedding=ref["embedding"], source="validated"))
                    added += 1
                session.flush()
                counts = Counter(ref.taxon_node_id for ref, _ in active_reference_vectors(session))
    except Exception:
        for path in created_images:
            path.unlink(missing_ok=True)
        raise
    root.mkdir(parents=True, exist_ok=True)
    invalidate_cache()
    message = f"Fishial local importé : {added} nouvelle(s) référence(s), {existing} déjà présente(s)."
    if conflicts:
        message += f" {conflicts} référence(s) différente(s) ignorée(s) pour conserver les corrections de ce poste."
    return {"output_path": str(root.resolve()), "added": added, "existing": existing,
            "conflicts": conflicts, "images_added": images_added,
            "total_references": sum(counts.values()), "species_count": len(counts),
            "message": message}
