#!/usr/bin/env python3
"""Export du dataset puis fine-tune YOLO sur **ce même export**.

Deux bugs corrigés en phase 2 :

- `--project` comparait des **noms** de projets à des identifiants UUID
  (`project_ids=args.projects`), si bien qu'un filtre par nom vidait l'export.
  Les noms sont désormais résolus en identifiants, et un nom inconnu est
  signalé au lieu de filtrer en silence.
- l'entraînement partait de `data/exports/yolo_<rang>_mixed`, un dossier
  **différent** de celui que produit le bouton « Export YOLO » : on
  réentraînait donc sur un dataset qui n'était pas celui qu'on venait
  d'exporter. On s'appuie maintenant sur la ligne `export_runs` du dernier
  export (celui qu'on vient de faire, ou le dernier enregistré avec
  `--skip-export`).
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ROOT.parent
sys.path.insert(0, str(ROOT))

from src.annodb.connection import get_db_path, init_db, session_scope
from src.annodb.export_core import (
    FORMAT_YOLO,
    SPLIT_BY_VALUES,
    TAXONOMY_RANKS,
    export_dataset,
    latest_export_run,
)

# La résolution nom → identifiant vit désormais dans `src.annodb.projects` :
# `train_yolox.py` portait encore le même bug (noms comparés à des UUID), il
# fallait une seule implémentation testée pour les deux scripts.
from src.annodb.projects import resolve_project_ids  # noqa: F401 (ré-export)
from src.annodb.storage_config import exports_dir


def data_yaml_of(export_dir: Path) -> Path:
    """`data.yaml` du dérivé YOLO d'un export produit par le noyau."""
    candidate = Path(export_dir) / "yolo" / "data.yaml"
    if candidate.is_file():
        return candidate
    for found in Path(export_dir).rglob("data.yaml"):
        return found
    raise FileNotFoundError(f"data.yaml introuvable sous {export_dir}")


def write_data_custom(data_yaml: Path, rank: str) -> Path:
    """Reflète l'export courant dans `configs/data_custom.yaml`.

    `train_detect.py --profile finetune` lit ce fichier : sans mise à jour, il
    entraînerait sur un export périmé. Il pointe donc toujours vers le dossier
    réellement produit ici.
    """
    cfg = yaml.safe_load(Path(data_yaml).read_text(encoding="utf-8")) or {}
    names = cfg.get("names", {0: "fish"})
    if isinstance(names, dict):
        name_lines = "\n".join(
            f"  {k}: {v}" for k, v in sorted(names.items(), key=lambda x: int(x[0]))
        )
    else:
        name_lines = "\n".join(f"  {i}: {n}" for i, n in enumerate(names))
    custom = ROOT / "configs" / "data_custom.yaml"
    custom.parent.mkdir(parents=True, exist_ok=True)
    custom.write_text(
        f"# Auto-genere par retrain_from_db.py (rank={rank})\n"
        f"path: {Path(data_yaml).parent.resolve().as_posix()}\n"
        f"train: images/train\n"
        f"val: images/val\n"
        f"test: images/test\n\n"
        f"names:\n{name_lines}\n",
        encoding="utf-8",
    )
    return custom


def main() -> int:
    parser = argparse.ArgumentParser(description="Export DB + fine-tune YOLO")
    parser.add_argument("--rank", choices=list(TAXONOMY_RANKS), default="family")
    parser.add_argument("--split-by", choices=list(SPLIT_BY_VALUES), default="media",
                        help="Grain du split de l'export produit ici")
    parser.add_argument("--out", type=Path, default=None,
                        help="Racine des exports (défaut <dépôt>/data/exports)")
    parser.add_argument("--epochs", type=int, default=None)
    # Point de depart du fine-tuning. Le modele « familles » a ete retire du
    # catalogue : partir de lui reviendrait a propager un decoupage de classes
    # abandonne. Le generique mono-classe est la base neutre.
    parser.add_argument("--model", type=Path, default=ROOT / "models" / "fish_detect_public.pt")
    parser.add_argument("--skip-train", action="store_true")
    parser.add_argument("--skip-export", action="store_true",
                        help="Réutiliser le dernier export YOLO enregistré")
    parser.add_argument("--project", action="append", dest="projects")
    args = parser.parse_args()

    export_root = args.out or exports_dir()
    db_path = get_db_path()
    if not db_path.exists():
        init_db(db_path)

    annotation_count = 0
    with session_scope(db_path) as session:
        if args.skip_export:
            run = latest_export_run(session, fmt=FORMAT_YOLO)
            if run is None:
                print("Aucun export YOLO enregistré : relancez sans --skip-export.")
                return 1
            export_dir = Path(run.output_path)
            annotation_count = run.annotation_count or 0
            print(f"Dernier export YOLO : {export_dir}")
        else:
            project_ids = resolve_project_ids(session, args.projects)
            result = export_dataset(
                session, export_root, split_by=args.split_by,
                taxonomy_rank=args.rank, fmt=FORMAT_YOLO, project_ids=project_ids,
                dataset_name=f"retrain_{args.rank}",
            )
            export_dir = result.output_dir
            annotation_count = result.report["annotation_count"]
            print(f"Export : {annotation_count} annotations -> {export_dir}")

    if annotation_count == 0:
        print("Aucune annotation exportable. Annotez dans Mesure ou importez un dataset.")
        return 1

    data_yaml = data_yaml_of(export_dir)
    print(f"Config d'entraînement : {data_yaml}")
    print(f"Miroir pour train_detect.py : {write_data_custom(data_yaml, args.rank)}")

    if args.skip_train:
        return 0

    model = args.model
    if not model.exists():
        fallback = ROOT / "models" / "fish_detect_public.pt"
        if fallback.exists():
            model = fallback
            print(f"Modele de depart absent, fallback: {model}")
        else:
            print(f"Model not found: {args.model}")
            return 1

    from ultralytics import YOLO

    train_cfg_path = ROOT / "configs" / "train_detect.yaml"
    profile = yaml.safe_load(train_cfg_path.read_text(encoding="utf-8")).get("finetune", {})
    epochs = args.epochs or profile.get("epochs", 100)
    batch = profile.get("batch", 16)
    imgsz = profile.get("imgsz", 640)

    print(f"Fine-tuning {model.name} | rank={args.rank} | epochs={epochs}")
    yolo = YOLO(str(model))
    results = yolo.train(
        data=str(data_yaml),
        epochs=epochs,
        imgsz=imgsz,
        batch=batch,
        device=profile.get("device", 0),
        project=str(ROOT / "runs" / "detect"),
        name=f"fish_retrain_{args.rank}",
    )

    best = Path(results.save_dir) / "weights" / "best.pt"
    if best.exists():
        # Sortie unique, quel que soit le rang. Le rang « famille » ecrivait
        # autrefois fish_detect_family.pt ; cette entree a ete retiree du
        # catalogue, et un modele produit ici ne doit jamais atterrir sous un
        # nom que l'application ne sait plus proposer. Le rang reste lisible
        # dans le descripteur .meta.json ecrit a cote.
        dest = ROOT / "models" / "fish_detect_custom.pt"
        shutil.copy2(best, dest)
        print(f"Best weights -> {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
