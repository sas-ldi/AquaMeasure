#!/usr/bin/env python3
"""Audit d'un dataset YOLO : effectifs par classe, images par classe, split.

Deux défauts corrigés en phase 2 :

- l'audit cherchait les labels dans `<racine>/<split>/labels`, alors que
  l'export les écrit dans `<racine>/labels/<split>` : il annonçait donc
  0 instance sur tout export maison ;
- appelé sans argument (c'est ce que fait le bouton « Vérifier les
  annotations »), il auditait le dataset **public** téléchargé, jamais
  l'export qu'on venait de produire. Sans chemin, il vise maintenant le
  dernier export enregistré dans `export_runs`.

Quand un `manifest.json` est présent, il est lu et affiché : version du
dataset, split, seuils de classe, exclusions — l'audit doit dire ce que
l'export a décidé, pas seulement compter des lignes.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ROOT.parent
sys.path.insert(0, str(ROOT))

SPLITS = ("train", "val", "valid", "test")


def load_names(root: Path) -> tuple[dict[int, str], Path]:
    """Noms de classes depuis `data.yaml` (celui du dérivé YOLO en priorité)."""
    for candidate in (root / "yolo" / "data.yaml", root / "data.yaml"):
        if candidate.is_file():
            return _names_of(candidate), candidate.parent
    for candidate in sorted(root.rglob("data.yaml")):
        return _names_of(candidate), candidate.parent
    raise FileNotFoundError(f"Aucun data.yaml sous {root}")


def _names_of(data_yaml: Path) -> dict[int, str]:
    cfg = yaml.safe_load(data_yaml.read_text(encoding="utf-8")) or {}
    raw = cfg.get("names", {})
    if isinstance(raw, list):
        return {i: n for i, n in enumerate(raw)}
    return {int(k): str(v) for k, v in raw.items()}


def label_dirs(root: Path) -> list[tuple[str, Path]]:
    """Répertoires de labels réellement présents, quelle que soit la mise en forme.

    `labels/<split>` est la structure produite par l'export ; `<split>/labels`
    reste rencontrée dans les datasets publics téléchargés.
    """
    found: list[tuple[str, Path]] = []
    for split in SPLITS:
        for candidate in (root / "labels" / split, root / split / "labels"):
            if candidate.is_dir():
                found.append((split, candidate))
    if not found and (root / "labels").is_dir():
        found.append(("labels", root / "labels"))
    return found


def audit_yolo(root: Path) -> dict:
    names, yolo_root = load_names(root)
    instances: Counter[int] = Counter()
    images_with: defaultdict[int, set[str]] = defaultdict(set)
    per_split: Counter[str] = Counter()
    empty_labels = 0

    dirs = label_dirs(yolo_root) or label_dirs(root)
    for split, lbl_dir in dirs:
        for txt in sorted(lbl_dir.glob("*.txt")):
            content = txt.read_text(encoding="utf-8").strip()
            per_split[split] += 1
            if not content:
                empty_labels += 1
                continue
            for line in content.splitlines():
                parts = line.split()
                if len(parts) < 5:
                    continue
                cls_id = int(parts[0])
                instances[cls_id] += 1
                images_with[cls_id].add(f"{split}/{txt.stem}")

    rows = []
    for cls_id in sorted(names.keys()):
        rows.append({
            "id": cls_id,
            "name": names[cls_id],
            "instances": instances.get(cls_id, 0),
            "images": len(images_with.get(cls_id, set())),
        })
    unknown = sorted(set(instances) - set(names))
    return {
        "root": str(root.resolve()),
        "yolo_root": str(yolo_root.resolve()),
        "classes": rows,
        "nc": len(names),
        "labels_per_split": dict(sorted(per_split.items())),
        "empty_label_files": empty_labels,
        "unknown_class_ids": unknown,
        "label_dirs": [str(d) for _s, d in dirs],
    }


def read_manifest(root: Path) -> dict | None:
    path = Path(root) / "manifest.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def print_manifest(manifest: dict) -> None:
    print(
        f"Dataset  : {manifest.get('dataset_name')} "
        f"v{manifest.get('dataset_version')} "
        f"({manifest.get('format')}, rang {manifest.get('taxonomy_rank')})"
    )
    print(f"Produit  : {manifest.get('created_at')} · commit {(manifest.get('git_commit') or '?')[:8]}")
    print(f"Empreinte: {manifest.get('content_sha256', '')[:16]}")
    split = manifest.get("split") or {}
    composition = split.get("composition") or {}
    print(
        f"Split    : {split.get('strategy')} (graine {split.get('seed')}) — "
        + ", ".join(
            f"{name} {row.get('images', 0)} img/{row.get('groups', 0)} grp"
            for name, row in composition.items()
        )
    )
    na = split.get("na_rule") or {}
    if na.get("groups_forced_to_train"):
        print(
            f"Règle NA : {na['groups_forced_to_train']} groupe(s) forcé(s) en "
            f"train ({na.get('frames_concerned', 0)} frame(s) non identifiées)"
        )
    stats = manifest.get("class_stats") or {}
    print(
        f"Classes  : {stats.get('count', 0)} au-dessus des seuils "
        f"(min {stats.get('min_instances')} instances / {stats.get('min_media')} médias) — "
        f"{stats.get('ignored_instances', 0)} instance(s) en ignore"
    )
    for row in (stats.get("fallbacks") or [])[:10]:
        cible = row.get("to") or "ignore"
        print(f"           repli {row.get('from')} → {cible} ({row.get('instances')} instances)")
    exclusions = manifest.get("exclusions") or {}
    if exclusions.get("count"):
        print(f"Exclues  : {exclusions.get('summary')}")
    print("")


def resolve_last_export(explicit_db: Path | None = None) -> Path | None:
    """Dossier du dernier export réussi, d'après `export_runs`."""
    try:
        from src.annodb.connection import get_db_path, session_scope
        from src.annodb.export_core import latest_export_run
    except ImportError as exc:  # annotations non installé
        print(f"[!] Base d'annotations indisponible ({exc})")
        return None
    db_path = explicit_db or get_db_path()
    if not Path(db_path).exists():
        return None
    with session_scope(db_path) as session:
        run = latest_export_run(session)
        if run is None:
            return None
        print(
            f"Dernier export : {run.format} · rang {run.taxonomy_rank} · "
            f"{run.created_at:%Y-%m-%d %H:%M}"
        )
        return Path(run.output_path)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audit d'un dataset YOLO (défaut : le dernier export produit)",
    )
    parser.add_argument("dataset", type=Path, nargs="?", default=None,
                        help="Dossier du dataset ; par défaut le dernier export")
    parser.add_argument("--db", type=Path, default=None)
    parser.add_argument("--highlight", default="Acanthuridae", help="Famille à mettre en avant")
    parser.add_argument("--min-images", type=int, default=20, help="Alerte sous ce nombre d'images")
    args = parser.parse_args()

    root = args.dataset
    if root is None:
        root = resolve_last_export(args.db)
        if root is None:
            print("Aucun export enregistré : lancez d'abord « Export YOLO » "
                  "(ou passez un dossier en argument).")
            fallback = REPO_ROOT / "data" / "public" / "fish_families"
            if not fallback.exists():
                return 1
            print(f"Repli sur le dataset public : {fallback}")
            root = fallback
    if not root.is_absolute():
        root = REPO_ROOT / root
    if not root.exists():
        print(f"Dataset introuvable : {root}")
        return 1

    manifest = read_manifest(root)
    if manifest:
        print_manifest(manifest)

    try:
        report = audit_yolo(root)
    except FileNotFoundError as exc:
        print(f"[!] {exc}")
        return 1

    print(f"Dataset: {report['root']}")
    if report["yolo_root"] != report["root"]:
        print(f"Labels : {report['yolo_root']}")
    print(f"Classes: {report['nc']}")
    if report["labels_per_split"]:
        print("Labels par split: " + ", ".join(
            f"{k}={v}" for k, v in report["labels_per_split"].items()
        ))
    if report["empty_label_files"]:
        print(f"Fichiers de label vides (images de fond): {report['empty_label_files']}")
    print("")
    print(f"{'ID':>3}  {'Instances':>10}  {'Images':>8}  Nom")
    print("-" * 60)
    for row in report["classes"]:
        flag = "  <--" if row["name"] == args.highlight else ""
        warn = "  [LOW]" if row["images"] < args.min_images else ""
        print(f"{row['id']:3d}  {row['instances']:10d}  {row['images']:8d}  {row['name']}{flag}{warn}")

    if report["unknown_class_ids"]:
        print(f"\n[!] Identifiants de classe absents de data.yaml : {report['unknown_class_ids']}")

    highlight = next((r for r in report["classes"] if r["name"] == args.highlight), None)
    if highlight:
        print(f"\n{args.highlight}: {highlight['images']} images, {highlight['instances']} instances")
    elif report["nc"] == 1:
        print("\nMono-classe detecte : c'est le rang 'fish'. Exportez au rang famille "
              "pour un dataset taxonomique.")
    elif report["nc"] == 0:
        print("\nAucune classe : au rang demande, aucun taxon ne franchit les seuils "
              "d'effectif. Tout est sorti en ignore.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
