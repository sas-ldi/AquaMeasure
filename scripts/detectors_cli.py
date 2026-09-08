#!/usr/bin/env python3
"""Outil console du catalogue de detecteurs.

    python scripts/detectors_cli.py list
    python scripts/detectors_cli.py install cfd-yolov12x
    python scripts/detectors_cli.py use cfd-yolov12x
    python scripts/detectors_cli.py test chemin/image.jpg --conf 0.3
    python scripts/detectors_cli.py update-catalog https://exemple/catalog.json
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import fish_detectors as fd  # noqa: E402


def cmd_list(_args: argparse.Namespace) -> int:
    reg = fd.registry()
    print("Backends disponibles")
    for backend in reg.backend_report():
        mark = "ok" if backend["installed"] else "-- "
        missing = ", ".join(backend["missingRequirements"])
        suffix = f"  (manque : {missing})" if missing else ""
        print(f"  [{mark}] {backend['id']:<12} {backend['label']}{suffix}")

    errors = reg.plugin_errors()
    if errors:
        print("\nPlugins en erreur")
        for err in errors:
            print(f"  ! {err}")

    print(f"\nModeles  (actif : {reg.active_id or 'aucun'})")
    for entry in reg.entries():
        mark = "ok" if entry["usable"] else ("dl" if entry["downloadable"] else "--")
        star = "*" if entry["id"] == reg.active_id else " "
        print(f" {star}[{mark}] {entry['id']:<28} {entry['backend']:<12}"
              f" {entry['sizeLabel']:>8}  {entry['reason']}")
    return 0


def cmd_install(args: argparse.Namespace) -> int:
    reg = fd.registry()
    last = [-1]

    def progress(done: int, total: int) -> None:
        pct = int(done * 100 / total) if total else 0
        if pct != last[0] and pct % 5 == 0:
            last[0] = pct
            print(f"\r  {pct:3d}%  {done // (1024 * 1024)} Mo", end="", flush=True)

    print(f"Installation de {args.detector_id}…")
    path = reg.download(args.detector_id, progress=progress)
    print(f"\n  -> {path}")
    reg.reload()
    return 0


def cmd_use(args: argparse.Namespace) -> int:
    reg = fd.registry()
    if not reg.set_active(args.detector_id):
        print(f"Modele inconnu ou deja actif : {args.detector_id}")
        return 1
    print(f"Modele actif : {reg.active_id}")
    return 0


def cmd_test(args: argparse.Namespace) -> int:
    import time

    import cv2

    image = cv2.imread(args.image)
    if image is None:
        print(f"Image illisible : {args.image}")
        return 1

    reg = fd.registry()
    targets = args.detector or [reg.active_id]
    for detector_id in targets:
        status = reg.status(detector_id)
        if status is None:
            print(f"{detector_id:<28} modele inconnu")
            continue
        if not status.usable:
            print(f"{detector_id:<28} indisponible — {status.reason()}")
            continue
        start = time.perf_counter()
        try:
            boxes = reg.detect(image, conf=args.conf, detector_id=detector_id)
        except Exception as exc:
            print(f"{detector_id:<28} erreur — {exc}")
            continue
        elapsed = (time.perf_counter() - start) * 1000
        best = f" meilleure conf {boxes[0]['conf']:.2f}" if boxes else ""
        print(f"{detector_id:<28} {len(boxes):3d} poisson(s)  {elapsed:7.0f} ms{best}")
    return 0


def cmd_update_catalog(args: argparse.Namespace) -> int:
    count, message = fd.registry().update_catalog(args.url)
    print(message)
    return 0 if count else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="Lister backends et modeles").set_defaults(func=cmd_list)

    p_install = sub.add_parser("install", help="Telecharger les poids d'un modele")
    p_install.add_argument("detector_id")
    p_install.set_defaults(func=cmd_install)

    p_use = sub.add_parser("use", help="Definir le modele actif")
    p_use.add_argument("detector_id")
    p_use.set_defaults(func=cmd_use)

    p_test = sub.add_parser("test", help="Comparer des modeles sur une image")
    p_test.add_argument("image")
    p_test.add_argument("--conf", type=float, default=None)
    p_test.add_argument("--detector", action="append", help="Repetable")
    p_test.set_defaults(func=cmd_test)

    p_update = sub.add_parser("update-catalog", help="Recuperer un catalogue distant")
    p_update.add_argument("url", nargs="?", default=None)
    p_update.set_defaults(func=cmd_update_catalog)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
