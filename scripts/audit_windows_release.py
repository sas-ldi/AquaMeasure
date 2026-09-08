"""Contrôle du paquet vierge avant fabrication de l'installateur client."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from prepare_release_models import BUNDLED_FILES


def audit(folder: Path) -> dict:
    root = folder.resolve()
    manifest = json.loads((root / "models-manifest.json").read_text(encoding="utf-8"))
    catalog = json.loads((Path(__file__).resolve().parents[1] / "fish_detectors/catalog.json").read_text(encoding="utf-8"))
    expected = {"fish-vision/models/" + name for name in BUNDLED_FILES}
    expected.update("fish-vision/models/" + row["filename"] for row in catalog["detectors"]
                    if row.get("download_url") and not row.get("options", {}).get("archive"))
    listed = {row["path"] for row in manifest["files"]}
    assert listed == expected, f"Manifeste inattendu : {listed ^ expected}"
    actual = {p.relative_to(root).as_posix() for p in (root / "fish-vision/models").rglob("*")
              if p.is_file() and p.name != ".gitkeep" and "__pycache__" not in p.parts}
    assert actual == expected, f"Fichiers de modèles inattendus : {actual ^ expected}"
    for row in manifest["files"]:
        path = (root / row["path"]).resolve()
        path.relative_to(root)
        assert path.stat().st_size == row["size"], f"Taille : {row['path']}"
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        assert digest.hexdigest() == row["sha256"], f"Empreinte : {row['path']}"
    forbidden = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        if (path.suffix.lower() in {".db", ".sqlite", ".sqlite3", ".mp4", ".mov", ".avi", ".mkv"}
                or path.name in {"storage.json", "detectors.local.json", "detectors.state.json"}
                or (relative.startswith(("camera_parameters/", "data/", "fish-vision/data/"))
                    and path.name != ".gitkeep")):
            forbidden.append(relative)
    assert not forbidden, f"Données de travail dans le paquet : {forbidden}"
    for name in ("AquaMeasure_Manuel_Utilisateur", "AquaMeasure_Guide_Extension_Modeles_Detection"):
        assert (root / "Documentation" / (name + ".docx")).is_file(), f"Manuel absent : {name}"
    assert (root / "aquameasure-pyside/resources/aquameasure.ico").is_file()
    return {"ok": True, "model_files": len(expected), "model_bytes": sum(row["size"] for row in manifest["files"]),
            "working_data_files": forbidden, "edition": manifest["edition"]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", type=Path)
    args = parser.parse_args()
    print(json.dumps(audit(args.folder), indent=2, ensure_ascii=False))
