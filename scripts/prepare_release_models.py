"""Prépare les poids publics et leur inventaire pour l'installeur complet.

Les poids Fishial extraits doivent déjà se trouver dans fish-vision/models.
Les autres poids sont téléchargés aux URL du catalogue et contrôlés par SHA-256.
SAM 3 conserve l'authentification personnelle Hugging Face au premier usage.
"""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
MODELS = ROOT / "fish-vision/models"
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


def sha256(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    entries = json.loads((ROOT / "fish_detectors/catalog.json").read_text(encoding="utf-8"))["detectors"]
    for entry in entries:
        if not entry.get("download_url") or entry.get("options", {}).get("archive"):
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

    vendor = ROOT / "vendor/yolov5"
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
    required.update(entry["filename"] for entry in entries
                    if entry.get("download_url") and not entry.get("options", {}).get("archive"))
    files = []
    for name in sorted(required):
        path = (MODELS / name).resolve()
        path.relative_to(MODELS.resolve())
        if not path.is_file():
            raise FileNotFoundError(path)
        files.append({"path": path.relative_to(ROOT).as_posix(), "size": path.stat().st_size,
                      "sha256": sha256(path)})
    manifest = {"edition": "2026.09.08 complète CPU", "files": files,
                "yolov5_commit": YOLOV5_COMMIT,
                "sam3": "Moteur Transformers inclus ; poids soumis à accès Hugging Face personnel."}
    (ROOT / "packaging/models-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Manifeste : {len(files)} fichiers, {sum(f['size'] for f in files) / 1e9:.2f} Go", flush=True)


if __name__ == "__main__":
    main()
