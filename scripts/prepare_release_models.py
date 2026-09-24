"""Prépare les poids publics et leur inventaire pour l'installeur.

Deux éditions : « complète » embarque tous les détecteurs du catalogue ;
« légère » (--edition legere) ne garde que la suite Fishial (détection,
classification, segmentation) et les petits détecteurs, pour les postes à
faible débit. Les autres s'ajoutent ensuite depuis l'onglet Modèles IA.

Les poids Fishial extraits doivent déjà se trouver dans src/annotations/models.
Les autres poids sont téléchargés aux URL du catalogue et contrôlés par SHA-256.
SAM 3 conserve l'authentification personnelle Hugging Face au premier usage.
"""
from __future__ import annotations

import hashlib
import io
import argparse
import json
from pathlib import Path
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
MODELS = ROOT / "src/annotations/models"
YOLOV5_COMMIT = "35b48237aef6d71ca9de2c5dea345d7536eb7fa7"
# Liste de livraison explicite : un modèle personnel ou un export Fishial
# déposé dans models/ ne doit jamais entrer dans un installateur client.
BUNDLED_FILES = (
    "fish_detect_family.onnx", "fish_detect_family.pt",
    "fish_detect_public.onnx", "fish_detect_public.pt",
    "detector_v26_n3/model.pt", "fishial_labels.json",
    "fishial_classification_v0.10.2/model.pt",
    "fishial_classification_v0.10.2/inference.py",
    "fishial_classification_v0.10.2/info.json",
    "fishial_classification_v0.10.2/requirements.txt",
    "fishial_segmentator_fpn_res18/model.ts",
    "fishial_segmentator_fpn_res18/inference.py",
    "fishial_segmentator_fpn_res18/info.json",
)

# Édition légère : seuls détecteurs du catalogue gardés en plus de
# BUNDLED_FILES. Choix du 2026-09-24 : les sept autres (MegaFish m, MBARI x2,
# RF-DETR x3, YOLOv12x) pèsent environ 880 Mo et doublonnent Fishial.
LIGHT_CATALOG_IDS = ("megafishdetector-s",)
EDITIONS = {"complete": "complète", "legere": "légère"}


def catalog_files(entries, edition: str) -> list[str]:
    """Poids du catalogue embarqués pour une édition donnée."""
    return [entry["filename"] for entry in entries
            if entry.get("download_url") and not entry.get("options", {}).get("archive")
            and (edition == "complete" or entry["id"] in LIGHT_CATALOG_IDS)]


def edition_of(manifest: dict) -> str:
    return "legere" if "légère" in manifest.get("edition", "") else "complete"


def sha256(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--edition", choices=sorted(EDITIONS), default="complete")
    edition = parser.parse_args().edition
    entries = json.loads((ROOT / "src/fish_detectors/catalog.json").read_text(encoding="utf-8"))["detectors"]
    wanted = set(catalog_files(entries, edition))
    for entry in entries:
        if entry.get("filename") not in wanted:
            continue
        target = MODELS / entry["filename"]
        if not target.is_file():
            print("Téléchargement : " + entry["id"], flush=True)
            part = target.with_suffix(target.suffix + ".part")
            urllib.request.urlretrieve(entry["download_url"], part)
            if sha256(part) != entry["sha256"]:
                raise RuntimeError("SHA-256 incorrect : " + entry["id"])
            part.replace(target)
        if sha256(target) != entry["sha256"]:
            raise RuntimeError("SHA-256 incorrect : " + entry["id"])

    vendor = ROOT / "src/vendor/yolov5"
    if not (vendor / "hubconf.py").is_file():
        print("Code YOLOv5 : " + YOLOV5_COMMIT, flush=True)
        url = f"https://codeload.github.com/ultralytics/yolov5/zip/{YOLOV5_COMMIT}"
        with urllib.request.urlopen(url, timeout=120) as response:
            archive = zipfile.ZipFile(io.BytesIO(response.read()))
        for member in archive.infolist():
            relative = Path(*Path(member.filename).parts[1:])
            if member.is_dir() or not relative.parts or ".." in relative.parts:
                continue
            dest = vendor / relative
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(archive.read(member))

    required = set(BUNDLED_FILES)
    required.update(wanted)
    files = []
    for name in sorted(required):
        path = (MODELS / name).resolve()
        path.relative_to(MODELS.resolve())
        if not path.is_file():
            raise FileNotFoundError(path)
        files.append({"path": path.relative_to(ROOT / "src").as_posix(), "size": path.stat().st_size,
                      "sha256": sha256(path)})
    manifest = {"edition": f"2026.09.24 {EDITIONS[edition]} CPU", "files": files,
                "yolov5_commit": YOLOV5_COMMIT,
                "sam3": "Moteur Transformers inclus ; poids soumis à accès Hugging Face personnel."}
    (ROOT / "packaging/models-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Manifeste : {len(files)} fichiers, {sum(f['size'] for f in files) / 1e9:.2f} Go", flush=True)


if __name__ == "__main__":
    main()
