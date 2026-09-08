"""Exporte les sources commitées, sans historique Git ni binaires de livraison."""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import subprocess
import zipfile


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    paths = subprocess.check_output(["git", "ls-tree", "-rz", "--name-only", "HEAD"], cwd=repo).decode("utf-8").split("\0")
    forbidden = [p for p in paths if Path(p).suffix.lower() in {".exe", ".dll", ".msi", ".pt", ".pth", ".onnx", ".db", ".sqlite", ".sqlite3"}]
    if forbidden:
        raise RuntimeError(f"Fichiers compilés ou locaux encore suivis : {forbidden}")
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise FileExistsError(output)
    subprocess.run(["git", "archive", "--format=zip", "--prefix=AquaMeasure-sources/", f"--output={output}", "HEAD"], cwd=repo, check=True)
    with zipfile.ZipFile(output) as archive:
        bad = archive.testzip()
        if bad:
            raise RuntimeError(f"Archive corrompue : {bad}")
        print(f"{len(archive.namelist())} entrées ; aucun historique Git exporté.")
    print(output)
    print("SHA256 " + hashlib.sha256(output.read_bytes()).hexdigest())


if __name__ == "__main__":
    main()
