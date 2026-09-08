"""Copie les guides courants dans une livraison, avec leur classement."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]


def copy_guides(destination: Path, pdf_root: Path | None = None) -> int:
    guides = json.loads((ROOT / "docs/documents.json").read_text(encoding="utf-8"))
    destination.mkdir(parents=True, exist_ok=True)
    lines = ["DOCUMENTATION AQUAMEASURE", "", "Huit guides classés par thème, en Word et en PDF.", ""]
    previous_category = None
    for guide in guides:
        category = guide["category"]
        if category != previous_category:
            lines += [category, ""]
            previous_category = category
        lines += [guide["label"] + " : " + guide["purpose"],
                  "  Word/" + category + "/" + guide["stem"] + ".docx",
                  "  PDF/" + category + "/" + guide["stem"] + ".pdf", ""]
        for source_dir, target_dir, extension in (("word", "Word", ".docx"), ("pdf", "PDF", ".pdf")):
            base = pdf_root if source_dir == "pdf" and pdf_root else ROOT / "docs" / source_dir
            source = base / category / (guide["stem"] + extension)
            if not source.is_file():
                raise FileNotFoundError(source)
            target = destination / target_dir / category / source.name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    (destination / "LIRE-MOI.txt").write_text("\n".join(lines), encoding="utf-8-sig")
    return len(guides)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--pdf-root", type=Path)
    args = parser.parse_args()
    print(f"{copy_guides(args.destination, args.pdf_root)} guides copiés dans {args.destination}")
