"""Ajoute la documentation à la distribution et crée une archive ZIP64."""
from pathlib import Path
import shutil
import sys
import zipfile

root = Path(__file__).resolve().parents[1]
release = Path(sys.argv[1]).resolve()
docs = release / "Documentation"
docs.mkdir(exist_ok=True)
manuals = ("AquaMeasure_Manuel_Utilisateur.docx",
           "AquaMeasure_Guide_Extension_Modeles_Detection.docx")
# Retirer uniquement les anciennes sorties connues lors d'un repackaging.
for name in ("Guide_Complet", "Guide_Developpeur", "Guide_Technique",
             "Note_Base_De_Donnees", "Note_Calibration_Stereo", "Note_Detection_IA",
             "Note_Exports_Datasets", "Note_Mesure_Stereo"):
    (docs / f"AquaMeasure_{name}.docx").unlink(missing_ok=True)
for name in manuals:
    source = root / "aquameasure-pyside/docs/word" / name
    shutil.copy2(source, docs / source.name)
with zipfile.ZipFile(release.with_suffix(".zip"), "w", zipfile.ZIP_DEFLATED,
                     compresslevel=4, allowZip64=True) as archive:
    for path in sorted(release.rglob("*")):
        if path.is_file():
            archive.write(path, path.relative_to(release.parent))
print(release.with_suffix(".zip"))
