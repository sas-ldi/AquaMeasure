# -*- mode: python ; coding: utf-8 -*-
r"""PyInstaller | AquaMeasure PySide6, distribution Windows portable.

Usage (depuis la racine du dépôt) :
    .venv\Scripts\python.exe -m PyInstaller --clean --noconfirm packaging/aquameasure-windows.spec

Le paquet embarque les poids publics du catalogue, Fishial, les moteurs
Ultralytics, YOLOv5 et RF-DETR, ainsi que le tracking ByteTrack sur CPU.
Le moteur SAM 3 est inclus ; ses poids nécessitent l'accès personnel Hugging Face.
CUDA et l'entraînement local ne sont pas inclus.
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata


root = Path(SPECPATH).parent
code_root = root / "src"
app_dir = root / "src/interface"
fv_dir = root / "src/annotations"


def tree(source: Path, destination: str, *, suffixes: tuple[str, ...] | None = None):
    """Convertit un petit arbre source en tuples ``datas`` PyInstaller."""
    items = []
    if not source.is_dir():
        return items
    for path in source.rglob("*"):
        if not path.is_file() or "__pycache__" in path.parts or "__MACOSX" in path.parts:
            continue
        if suffixes is not None and path.suffix.lower() not in suffixes:
            continue
        relative_parent = path.relative_to(source).parent
        target = Path(destination) / relative_parent
        items.append((str(path), str(target)))
    return items


datas = []
datas += tree(app_dir / "qml", "interface/qml")
datas += tree(app_dir / "resources", "interface/resources")
datas += tree(app_dir / "src", "interface/src", suffixes=(".py",))
for tool in ("__init__.py", "gen_qmldir.py", "release_models_test.py", "release_install_test.py"):
    path = app_dir / "tools" / tool
    if path.is_file():
        datas.append((str(path), "interface/tools"))
datas += tree(fv_dir / "src", "annotations/src", suffixes=(".py",))
datas += tree(fv_dir / "scripts", "annotations/scripts", suffixes=(".py",))
datas += tree(fv_dir / "configs", "annotations/configs")
datas += tree(fv_dir / "db", "annotations/db")

# Base neuve au premier démarrage ; aucune donnée de travail n'est embarquée.
datas.append((str(fv_dir / "data" / ".gitkeep"), "annotations/data"))

# Détection ONNX et tracking ByteTrack sur CPU. Les deux variantes de poids
# sont petites ; les .pt servent au suivi Ultralytics, les .onnx à la détection
# courante plus légère. Aucun runtime CUDA n'est embarqué.
for model_name in (
    "fish_detect_family.onnx",
    "fish_detect_public.onnx",
    "fish_detect_family.pt",
    "fish_detect_public.pt",
    "fishial_labels.json",
):
    model = fv_dir / "models" / model_name
    if model.is_file():
        datas.append((str(model), "annotations/models"))

# La distribution complète fournit les modèles du manifeste, pas les archives
# de téléchargement ni les données utilisateur.
import json
manifest = root / "packaging" / "models-manifest.json"
if not manifest.is_file():
    raise RuntimeError("Lancer scripts/prepare_release_models.py avant la compilation")
for entry in json.loads(manifest.read_text(encoding="utf-8"))["files"]:
    source = code_root / entry["path"]
    if not source.is_file():
        raise RuntimeError(f"Ressource de livraison absente : {source}")
    datas.append((str(source), str(Path(entry["path"]).parent)))
datas.append((str(manifest), "."))
datas += tree(code_root / "vendor" / "yolov5", "vendor/yolov5")

for name in (
    "app_paths.py",
    "aquameasure.py",
    "stereo_utils.py",
    "fish_annotate.py",
    "fish_db_stats.py",
    "fish_detect.py",
    "fish_track.py",
    "fish_sparse_measure.py",
    "fishial_classify.py",
    "fishial_gallery.py",
    "charuco_board_export.py",
):
    path = code_root / name
    if path.is_file():
        datas.append((str(path), "."))

datas += tree(code_root / "fish_detectors", "fish_detectors", suffixes=(".py", ".json"))
datas += tree(code_root / "plugins", "plugins", suffixes=(".py",))
for name in ("README.md", "LICENSE", "THIRD_PARTY_NOTICES.md"):
    datas.append((str(root / name), "."))
datas += tree(root / "LICENSES", "LICENSES")

hiddenimports = [
    "cv2",
    "numpy",
    "scipy.linalg",
    "serial",
    "sqlalchemy",
    "sqlalchemy.dialects.sqlite",
    "yaml",
    "onnxruntime",
    "onnxruntime.capi._pybind_state",
    "torch",
    "torchvision",
    "lap",
    "ultralytics",
    "ultralytics.trackers.byte_tracker",
    "ultralytics.trackers.track",
    "ultralytics.trackers.utils.kalman_filter",
    "ultralytics.trackers.utils.matching",
    "fish_detectors.backends.onnx_yolo",
    "fish_detectors.backends.ultralytics_yolo",
    "fish_detectors.backends.rfdetr_detr",
    "fish_detectors.backends.yolov5_hub",
    "fish_detectors.backends.sam3",
]
hiddenimports += collect_submodules("sqlalchemy")
hiddenimports += ["albumentations", "albumentations.pytorch", "rfdetr", "pandas", "seaborn",
                  "transformers.models.sam3.modeling_sam3", "transformers.models.sam3.processing_sam3",
                  "transformers.models.sam3.image_processing_sam3", "transformers.models.clip.tokenization_clip"]
hiddenimports += collect_submodules("rfdetr", filter=lambda name: ".training" not in name)
for package in ("rfdetr", "transformers", "huggingface_hub", "albumentations", "supervision"):
    # RF-DETR compile certaines fonctions TorchScript à l'import et doit
    # pouvoir relire leurs sources même dans l'application empaquetée.
    datas += collect_data_files(package, include_py_files=(package == "rfdetr"))
for package in ("rfdetr", "transformers", "huggingface-hub", "supervision"):
    datas += copy_metadata(package)

a = Analysis(
    [str(app_dir / "main.py")],
    pathex=[str(app_dir), str(code_root), str(fv_dir), str(fv_dir / "src")],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "PyQt6",
        "torchaudio",
        "sam3",
        "IPython",
        "notebook",
        "onnx",
        "onnxslim",
    ],
    noarchive=False,
)

# L'environnement de travail Codex ajoute Poppler au PATH. PyInstaller y
# trouvait alors ``icuuc.dll`` 78 et l'embarquait à la racine ; Qt 6 attend la
# DLL proxy ICU fournie par Windows, avec une ABI différente. Le symptôme était
# un ``DLL load failed`` dès l'import de QtCore. Ces deux DLL appartiennent à
# Poppler, pas à AquaMeasure, et doivent rester hors de la distribution.
foreign_icu = {"icuuc.dll", "icudt78.dll"}
a.binaries = [
    entry for entry in a.binaries
    if Path(entry[0]).name.lower() not in foreign_icu
]
a.datas = [
    entry for entry in a.datas
    if Path(entry[0]).name.lower() not in foreign_icu
]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="AquaMeasure",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon=str(app_dir / "resources" / "aquameasure.ico"),
    version=str(root / "packaging" / "windows-version.txt"),
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    contents_directory=".",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="AquaMeasure-Windows-portable",
)
