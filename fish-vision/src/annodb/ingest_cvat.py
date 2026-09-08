"""Import CVAT exports into the unified annotation database."""

from __future__ import annotations

import json
import shutil
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import CvatLabelMap, Project
from .projects import get_or_create_project, register_media
from .spatial import add_spatial_annotation, status_for_taxon
from .taxonomy import get_node, get_node_by_name
from .tracks import get_or_create_track


def normalized_bbox_in_frame(
    cx: float, cy: float, bw: float, bh: float,
) -> Tuple[Tuple[float, float, float, float], bool]:
    """Ramène une boîte YOLO normalisée dans le cadre. Retour : `(boîte, corrigée)`.

    Les fichiers de labels YOLO publics (Roboflow, CVAT) écrivent `cx`, `cy`,
    `w` et `h` arrondis à six décimales, **indépendamment les uns des autres**.
    Une boîte collée au bord de l'image ressort alors à `cx − w/2 = −0,0000005` :
    hors du cadre, d'un demi-millionième. C'est l'origine - vérifiée sur la base
    réelle par `scripts/audit_bbox_bounds.py` - des 146 « boîtes hors cadre »
    signalées à l'export : **toutes** venaient de l'import public, aucune de la
    saisie manuelle ni du tracker.

    On corrige donc à l'entrée : les coins sont ramenés dans [0, 1] et le
    centre/taille recalculés depuis eux. Rien n'est perdu - l'écart corrigé est
    de l'ordre du dix-millième de pixel - et la base cesse de contenir des
    boîtes qui n'ont jamais eu de sens.
    """
    x1, y1 = cx - bw / 2.0, cy - bh / 2.0
    x2, y2 = cx + bw / 2.0, cy + bh / 2.0
    fx1, fy1 = min(max(x1, 0.0), 1.0), min(max(y1, 0.0), 1.0)
    fx2, fy2 = min(max(x2, 0.0), 1.0), min(max(y2, 0.0), 1.0)
    if (fx1, fy1, fx2, fy2) == (x1, y1, x2, y2):
        # Rien à corriger : on rend les valeurs d'origine plutôt que le
        # résultat d'un aller-retour coins → centre, qui décalerait chaque
        # boîte saine du dernier bit de la mantisse.
        return (cx, cy, bw, bh), False
    return (
        (fx1 + fx2) / 2.0, (fy1 + fy2) / 2.0, fx2 - fx1, fy2 - fy1,
    ), True


def load_label_mapping(path: Optional[Path]) -> Dict[str, str]:
    """Load cvat_label -> taxon_node_id from YAML."""
    if not path or not Path(path).exists():
        return {}
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not data:
        return {}
    mapping = data.get("mapping", data)
    return {str(k): str(v) for k, v in mapping.items()}


def sync_label_map_table(session: Session, mapping: Dict[str, str], project_id: Optional[str]) -> None:
    for cvat_label, taxon_id in mapping.items():
        if not get_node(session, taxon_id):
            continue
        existing = session.scalar(
            select(CvatLabelMap).where(
                CvatLabelMap.cvat_label == cvat_label,
                CvatLabelMap.project_id == project_id,
            )
        )
        if existing:
            existing.taxon_node_id = taxon_id
        else:
            session.add(
                CvatLabelMap(
                    cvat_label=cvat_label,
                    taxon_node_id=taxon_id,
                    project_id=project_id,
                )
            )
    session.flush()


def resolve_taxon_for_label(
    session: Session,
    label: str,
    mapping: Dict[str, str],
    project_id: str,
) -> Optional[str]:
    if label in mapping:
        return mapping[label]
    row = session.scalar(
        select(CvatLabelMap).where(
            CvatLabelMap.cvat_label == label,
            CvatLabelMap.project_id.in_([project_id, None]),
        )
    )
    if row:
        return row.taxon_node_id
    node = get_node_by_name(session, label)
    return node.id if node else None


def _extract_zip(zip_path: Path) -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="cvat_import_"))
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(tmp)
    return tmp


def ingest_yolo_folder(
    session: Session,
    folder: Path,
    *,
    project_name: str,
    mapping: Dict[str, str],
    author: str = "import",
) -> Tuple[int, int]:
    """Import a local YOLO dataset folder (images + labels + data.yaml)."""
    return ingest_yolo_zip(
        session,
        Path(folder),
        project_name=project_name,
        mapping=mapping,
        author=author,
    )


def ingest_yolo_zip(
    session: Session,
    extract_dir: Path,
    *,
    project_name: str,
    mapping: Dict[str, str],
    author: str = "cvat",
) -> Tuple[int, int]:
    """Import YOLO 1.1 export (images + labels + optional data.yaml)."""
    proj = get_or_create_project(session, project_name)
    sync_label_map_table(session, mapping, proj.id)

    data_yaml = None
    for candidate in extract_dir.rglob("data.yaml"):
        data_yaml = candidate
        break

    names: Dict[int, str] = {}
    if data_yaml:
        cfg = yaml.safe_load(data_yaml.read_text(encoding="utf-8"))
        raw_names = cfg.get("names", {})
        if isinstance(raw_names, dict):
            names = {int(k): v for k, v in raw_names.items()}
        elif isinstance(raw_names, list):
            names = {i: n for i, n in enumerate(raw_names)}

    image_dirs = list(extract_dir.rglob("images"))
    if not image_dirs:
        image_dirs = [extract_dir]

    media_count = ann_count = 0
    clamped_count = 0
    seen_images: set = set()

    for img_dir in image_dirs:
        if not img_dir.is_dir():
            continue
        for img_path in img_dir.rglob("*"):
            if img_path.suffix.lower() not in {".jpg", ".jpeg", ".png", ".bmp", ".webp"}:
                continue
            rel_label_dirs = ["labels", "../labels"]
            label_path = None
            for rel in rel_label_dirs:
                candidate = (img_path.parent / rel / f"{img_path.stem}.txt").resolve()
                if candidate.exists():
                    label_path = candidate
                    break
            if label_path is None:
                for lp in extract_dir.rglob(f"labels/**/{img_path.stem}.txt"):
                    label_path = lp
                    break

            media = register_media(session, project_id=proj.id, file_path=img_path, copy_into_store=True)
            if media.id not in seen_images:
                media_count += 1
                seen_images.add(media.id)

            if not label_path or not label_path.exists():
                continue

            w = media.width or 640
            h = media.height or 480
            for line in label_path.read_text(encoding="utf-8").strip().splitlines():
                parts = line.strip().split()
                if len(parts) < 5:
                    continue
                cls_id = int(parts[0])
                cx, cy, bw, bh = map(float, parts[1:5])
                # Cause amont des « boîtes hors cadre » : les labels publics
                # arrondissent centre et taille séparément. On borne à l'entrée
                # plutôt que de laisser la base porter des boîtes impossibles.
                (cx, cy, bw, bh), clamped = normalized_bbox_in_frame(cx, cy, bw, bh)
                if clamped:
                    clamped_count += 1
                if bw <= 0 or bh <= 0:
                    # Boîte entièrement hors de l'image : rien à importer.
                    continue
                label_name = names.get(cls_id, "fish")
                taxon_id = resolve_taxon_for_label(session, label_name, mapping, proj.id)
                add_spatial_annotation(
                    session,
                    media_id=media.id,
                    geom_type="bbox",
                    geometry={"cx": cx, "cy": cy, "w": bw, "h": bh, "normalized": True},
                    taxon_node_id=taxon_id,
                    author=author,
                    source="cvat",
                    # Même règle que le backfill de la phase 1 : un import qui
                    # porte un taxon fin vaut détermination, le nœud générique
                    # non - il ne dit rien de plus que « il y a un poisson ».
                    identification_status=status_for_taxon(taxon_id),
                    reviewed_by=(author if status_for_taxon(taxon_id) == "identified" else None),
                )
                ann_count += 1

    if clamped_count:
        print(
            f"[i] {clamped_count} boîte(s) du dataset source dépassaient du "
            "cadre (arrondi des labels) et ont été ramenées dans [0, 1] à "
            "l'import."
        )
    session.flush()
    return media_count, ann_count


def ingest_cvat_xml(
    session: Session,
    xml_path: Path,
    *,
    project_name: str,
    mapping: Dict[str, str],
    media_dir: Optional[Path] = None,
    author: str = "cvat",
) -> Tuple[int, int]:
    """Import CVAT for images 1.1 XML (supports tracks)."""
    proj = get_or_create_project(session, project_name)
    sync_label_map_table(session, mapping, proj.id)

    tree = ET.parse(xml_path)
    root = tree.getroot()

    label_names: Dict[str, str] = {}
    for label in root.findall(".//label"):
        name_el = label.find("name")
        if name_el is not None and name_el.text:
            label_names[name_el.text] = name_el.text

    media_count = ann_count = 0
    image_nodes = root.findall(".//image")
    for img_el in image_nodes:
        img_name = img_el.get("name", "unknown.jpg")
        frame_idx = int(img_el.get("frame", img_el.get("id", 0)))
        w = int(img_el.get("width", 640))
        h = int(img_el.get("height", 480))

        if media_dir:
            img_path = media_dir / img_name
            if not img_path.exists():
                for found in media_dir.rglob(Path(img_name).name):
                    img_path = found
                    break
        else:
            img_path = Path(img_name)

        if img_path.exists():
            media = register_media(session, project_id=proj.id, file_path=img_path, copy_into_store=True)
            media_count += 1
        else:
            from .models import MediaAsset
            import uuid
            media = MediaAsset(
                id=str(uuid.uuid4()),
                project_id=proj.id,
                media_type="image",
                rel_path=img_name,
                width=w,
                height=h,
            )
            session.add(media)
            session.flush()

        for box in img_el.findall("box"):
            label = box.get("label", "fish")
            taxon_id = resolve_taxon_for_label(session, label, mapping, proj.id)
            xtl, ytl, xbr, ybr = (
                float(box.get("xtl", 0)),
                float(box.get("ytl", 0)),
                float(box.get("xbr", 0)),
                float(box.get("ybr", 0)),
            )
            track_id_attr = box.get("track_id")
            db_track_id = None
            if track_id_attr is not None:
                tr = get_or_create_track(
                    session,
                    media_id=media.id,
                    external_track_id=int(track_id_attr),
                    source="cvat",
                    taxon_node_id=taxon_id,
                )
                db_track_id = tr.id
            add_spatial_annotation(
                session,
                media_id=media.id,
                frame_index=frame_idx,
                geom_type="bbox",
                geometry={"x_min": xtl, "y_min": ytl, "x_max": xbr, "y_max": ybr},
                taxon_node_id=taxon_id,
                track_id=db_track_id,
                author=author,
                source="cvat",
                identification_status=status_for_taxon(taxon_id),
                reviewed_by=(author if status_for_taxon(taxon_id) == "identified" else None),
                cvat_shape_id=int(box.get("id", 0)) if box.get("id") else None,
            )
            ann_count += 1

    session.flush()
    return media_count, ann_count


def ingest_cvat_export(
    session: Session,
    export_path: Path,
    *,
    project_name: str,
    mapping: Optional[Dict[str, str]] = None,
    mapping_file: Optional[Path] = None,
    author: str = "cvat",
) -> Tuple[int, int]:
    """Auto-detect YOLO zip or CVAT XML export."""
    mapping = mapping or load_label_mapping(mapping_file)
    export_path = Path(export_path)

    if export_path.suffix.lower() == ".zip":
        extract_dir = _extract_zip(export_path)
        try:
            if list(extract_dir.rglob("data.yaml")) or list(extract_dir.rglob("labels")):
                return ingest_yolo_zip(session, extract_dir, project_name=project_name, mapping=mapping, author=author)
            xml_files = list(extract_dir.rglob("annotations.xml")) + list(extract_dir.rglob("*.xml"))
            if xml_files:
                return ingest_cvat_xml(
                    session,
                    xml_files[0],
                    project_name=project_name,
                    mapping=mapping,
                    media_dir=extract_dir,
                    author=author,
                )
            return ingest_yolo_zip(session, extract_dir, project_name=project_name, mapping=mapping, author=author)
        finally:
            shutil.rmtree(extract_dir, ignore_errors=True)

    if export_path.suffix.lower() == ".xml":
        return ingest_cvat_xml(
            session,
            export_path,
            project_name=project_name,
            mapping=mapping,
            media_dir=export_path.parent,
            author=author,
        )

    raise ValueError(f"Unsupported export format: {export_path}")
