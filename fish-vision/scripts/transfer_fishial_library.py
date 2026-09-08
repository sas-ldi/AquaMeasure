#!/usr/bin/env python3
"""Exporter ou importer les références Fishial locales dans un ZIP."""

import argparse
import json
from pathlib import Path
import sys

FV = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(FV))
sys.path.insert(0, str(FV.parent))

from src.annodb.fishial_transfer import export_library, import_library
from src.annodb.storage_config import exports_dir


def main():
    # Le contrôleur QProcess lit le résultat et les erreurs en UTF-8.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("export", "import"))
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--db", type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    if args.action == "import" and args.archive is None:
        parser.error("--archive requis pour un import")
    try:
        result = export_library(args.out or exports_dir(), db_path=args.db) if args.action == "export" else import_library(args.archive, db_path=args.db)
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
