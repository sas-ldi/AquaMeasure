"""Ajoute les guides classés à la distribution et crée une archive ZIP64."""
from pathlib import Path
import sys
import zipfile

from copy_documentation import copy_guides

release = Path(sys.argv[1]).resolve()
copy_guides(release / "Documentation")
with zipfile.ZipFile(release.with_suffix(".zip"), "w", zipfile.ZIP_DEFLATED,
                     compresslevel=4, allowZip64=True) as archive:
    for path in sorted(release.rglob("*")):
        if path.is_file():
            archive.write(path, path.relative_to(release.parent))
print(release.with_suffix(".zip"))
