#!/usr/bin/env python3
"""Initialize fish-vision annotation database."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.annodb.connection import get_db_path, init_db, session_scope
from src.annodb.taxonomy import taxonomy_tree_dict


def main() -> int:
    parser = argparse.ArgumentParser(description="Initialize fish-vision annotation DB")
    parser.add_argument("--db", type=Path, default=None, help="Database file path")
    parser.add_argument("--no-seed", action="store_true", help="Skip taxonomy seed")
    args = parser.parse_args()

    path = init_db(args.db, seed=not args.no_seed)
    print(f"Database ready: {path}")

    with session_scope(path) as session:
        tree = taxonomy_tree_dict(session)
        print(f"Taxonomy nodes: {len(tree)} root branches")
        for branch in tree:
            print(f"  - {branch['scientific_name']} ({branch['rank']})")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
