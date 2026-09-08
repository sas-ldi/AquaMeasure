#!/usr/bin/env python3
"""Train YOLOX on DB export (MIT) — alternative to Ultralytics AGPL fine-tune.

Trois défauts corrigés dans la vague « correctifs phase 2 » :

- `--project` comparait des **noms** de projets à des identifiants UUID (le
  même bug que dans `retrain_from_db.py`, corrigé là-bas seulement) : le filtre
  vidait l'export sans un mot. On passe par `projects.resolve_project_ids`.
- `_write_voc_layout` aplatissait `images/<split>/x.jpg` dans `voc/images/`
  alors que les `file_name` du COCO valent `train/x.jpg` : YOLOX ne retrouvait
  aucune image. L'arborescence des splits est conservée.
- le dossier `voc/` était écrit **dans** l'export, dont `manifest.json` scelle
  le contenu fichier par fichier : le dataset livré ne correspondait plus à son
  propre manifeste. Il est écrit dans un dossier frère.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.annodb.connection import get_db_path, init_db, session_scope
from src.annodb.export_core import SPLIT_BY_VALUES, TAXONOMY_RANKS, export_dataset
from src.annodb.projects import resolve_project_ids
from src.annodb.storage_config import exports_dir

REPO_ROOT = ROOT.parent


def _yolox_available() -> bool:
    try:
        import yolox  # noqa: F401
        return True
    except ImportError:
        return False


def voc_dir_for(export_dir: Path) -> Path:
    """Dossier de travail YOLOX, **hors** de l'export scellé.

    `manifest.json` liste chaque fichier de l'export avec son empreinte :
    y ajouter `voc/` après coup rendait le dossier non conforme à son propre
    manifeste (fichiers non listés), donc invérifiable. Le dossier de travail
    est donc un frère, nommé d'après l'export dont il dérive.
    """
    export_dir = Path(export_dir)
    return export_dir.parent / f"{export_dir.name}_voc"


def _write_voc_layout(export_dir: Path, ann_file: Path, voc_root: Path) -> Path:
    """Copie les images COCO + le JSON d'instances pour les outils YOLOX.

    L'arborescence des splits est **conservée** : les `file_name` du COCO
    valent `train/<media>_<frame>.jpg`, un aplatissement dans `voc/images/`
    rendait chaque chemin faux et l'entraînement démarrait sur zéro image.
    """
    export_dir, voc_root = Path(export_dir), Path(voc_root)
    images_src = export_dir / "images"
    if not ann_file.is_file():
        raise FileNotFoundError(f"Export COCO introuvable : {ann_file}")
    voc_images = voc_root / "images"
    voc_images.mkdir(parents=True, exist_ok=True)
    if images_src.is_dir():
        for img in sorted(images_src.rglob("*.jpg")):
            dest = voc_images / img.relative_to(images_src)
            dest.parent.mkdir(parents=True, exist_ok=True)
            if not dest.exists():
                shutil.copy2(img, dest)
    shutil.copy2(ann_file, voc_root / "instances_train.json")
    shutil.copy2(ann_file, voc_root / "instances_val.json")

    # Garde-fou : chaque `file_name` du COCO doit exister sous voc/images.
    payload = json.loads(Path(ann_file).read_text(encoding="utf-8"))
    manquantes = [
        image["file_name"] for image in payload.get("images", [])
        if not (voc_images / image["file_name"]).is_file()
    ]
    if manquantes:
        raise FileNotFoundError(
            f"{len(manquantes)} image(s) du COCO absentes de {voc_images} — "
            f"exemple : {manquantes[0]}"
        )
    return voc_root


def main() -> int:
    parser = argparse.ArgumentParser(description="Export DB + train YOLOX detector")
    parser.add_argument("--rank", choices=list(TAXONOMY_RANKS), default="fish")
    parser.add_argument("--split-by", choices=list(SPLIT_BY_VALUES), required=True,
                        help="Grain du split : media | session | site (obligatoire)")
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--epochs", type=int, default=50)
    parser.add_argument("--batch", type=int, default=8)
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--model", default="yolox-s", help="YOLOX model name (yolox-s, yolox-m, …)")
    parser.add_argument("--skip-train", action="store_true")
    parser.add_argument("--project", action="append", dest="projects",
                        help="Filtre par id ou nom de projet (répétable)")
    args = parser.parse_args()

    export_root = args.out or exports_dir()

    db_path = get_db_path()
    if not db_path.exists():
        init_db(db_path)

    # Un seul export : le dossier produit est à la fois COCO (pivot) et YOLO
    # (dérivé). Deux exports séparés divergeaient sur le split.
    with session_scope(db_path) as session:
        # Noms ou identifiants : comparer un nom à un UUID vidait l'export.
        project_ids = resolve_project_ids(session, args.projects)
        result = export_dataset(
            session, export_root, split_by=args.split_by, taxonomy_rank=args.rank,
            fmt="yolo", project_ids=project_ids, dataset_name=f"yolox_{args.rank}",
        )
        export_dir = result.output_dir
        annotation_count = result.report["annotation_count"]
        coco_json = export_dir / f"instances_{args.rank}.json"

    summary = {
        "annotations": annotation_count,
        "export_dir": str(export_dir),
        "data_yaml": str(export_dir / "yolo" / "data.yaml"),
        "coco_json": str(coco_json),
        "content_sha256": result.manifest["content_sha256"],
    }
    print(json.dumps(summary, indent=2))

    if annotation_count == 0:
        print("Aucune annotation exportable.")
        return 1

    if args.skip_train:
        return 0

    if not _yolox_available():
        print(
            "YOLOX non installé. Export terminé.\n"
            "  pip install yolox\n"
            "Puis entraînez avec le COCO export :\n"
            f"  python -m yolox.tools.train -f exps/default/{args.model}.py "
            f"-d 1 -b {args.batch} --fp16 -o "
            f"-c {coco_json}"
        )
        return 0

    # Hors de l'export : son manifeste scelle la liste exacte de ses fichiers.
    voc_dir = voc_dir_for(export_dir)
    _write_voc_layout(export_dir, coco_json, voc_dir)
    print(f"Dossier de travail YOLOX (hors export scellé) : {voc_dir}")
    exp_name = args.model.replace("-", "_")
    cmd = [
        sys.executable, "-m", "yolox.tools.train",
        "-f", f"exps/default/{exp_name}.py",
        "-d", "1",
        "-b", str(args.batch),
        "--fp16",
        "-o",
        "--max_epoch", str(args.epochs),
    ]
    print("Lancement YOLOX :", " ".join(cmd))
    import os
    env = os.environ.copy()
    env["YOLOX_DATADIR"] = str(voc_dir)
    proc = subprocess.run(cmd, cwd=str(ROOT), env=env)
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
