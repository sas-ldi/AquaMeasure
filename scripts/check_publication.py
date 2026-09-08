"""Vérifie le contenu du dépôt public, sans installer l'application."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
BLOCKED_SUFFIXES = {
    ".exe", ".dll", ".msi", ".pyd", ".pyc", ".pt", ".pth", ".onnx",
    ".safetensors", ".db", ".sqlite", ".sqlite3", ".zip", ".7z", ".dmg",
    ".pkg", ".mov", ".mp4", ".avi", ".log",
}
BLOCKED_PREFIXES = (
    "build/", "dist/", "release/", "LIVRAISON_CLIENT/", "DEMO_CLIENT/",
    ".venv/", "fish-vision/.venv/", "camera_parameters/", "data/",
    "Calibrate_videos/", "D2/", "J2/", "synched/", ".codex/", ".cursor/",
)
DOCS = (
    "README.md", "CONTRIBUTING.md", "THIRD_PARTY_NOTICES.md",
    "aquameasure-pyside/docs/LISEZ_MOI.md",
    "aquameasure-pyside/docs/MANUEL_UTILISATEUR.md",
    "aquameasure-pyside/docs/DETECTEURS.md",
    "aquameasure-pyside/docs/GUIDE_DEVELOPPEUR.md",
)
TOKEN_PATTERN = re.compile(
    r"(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,}|"
    r"hf_[A-Za-z0-9]{25,}|AKIA[0-9A-Z]{16}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)"
)


def main() -> int:
    names = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).decode("utf-8").split("\0")
    names = [name for name in names if name]
    errors: list[str] = []
    if not names:
        errors.append("Aucun fichier suivi par Git.")
    total = 0
    for name in names:
        path = ROOT / name
        if path.suffix.lower() in BLOCKED_SUFFIXES or name.startswith(BLOCKED_PREFIXES):
            errors.append(f"Fichier local ou binaire : {name}")
        if path.name.startswith(".env") and path.name != ".env.example":
            errors.append(f"Configuration privée : {name}")
        if name.startswith("fish-vision/data/") and path.name != ".gitkeep":
            errors.append(f"Données de travail : {name}")
        if name.startswith("fish-vision/models/") and path.name not in {".gitkeep", "fishial_labels.json"}:
            errors.append(f"Poids ou bibliothèque locale : {name}")
        if not path.is_file():
            errors.append(f"Fichier absent : {name}")
            continue
        total += path.stat().st_size
        if path.stat().st_size > 50_000_000:
            errors.append(f"Fichier trop volumineux : {name}")
        try:
            content = path.read_text(encoding="utf-8")
        except (UnicodeError, OSError):
            continue
        if TOKEN_PATTERN.search(content):
            errors.append(f"Identifiant d’accès potentiel : {name}")

    link_count = 0
    for name in DOCS:
        doc = ROOT / name
        content = doc.read_text(encoding="utf-8")
        content = re.sub(r"```.*?```", "", content, flags=re.S)
        targets = re.findall(r"\]\(([^)]+)\)", content)
        targets += re.findall(r"(?:src|href)=\"([^\"]+)\"", content)
        for target in targets:
            target = target.strip().strip("<>")
            parts = urlsplit(target)
            if parts.scheme or parts.netloc or not parts.path:
                continue
            link_count += 1
            destination = (doc.parent / unquote(parts.path)).resolve()
            if not destination.is_relative_to(ROOT) or not destination.exists():
                errors.append(f"Lien absent dans {name} : {target}")
        if "\u2014" in content:
            errors.append(f"Tiret cadratin dans {name}")
    for error in errors:
        print(error)
    print(f"{len(names)} fichiers, {total / 1_000_000:.1f} Mo, {link_count} liens locaux vérifiés.")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
