"""Genere qml/AquaMeasure/qmldir pour le module QML."""

from __future__ import annotations

from pathlib import Path


def write_qmldir(module_dir: Path) -> None:
    lines = ["module AquaMeasure", ""]
    theme = module_dir / "style" / "Theme.qml"
    if theme.is_file():
        lines.append("singleton Theme 1.0 style/Theme.qml")
    for sub in ("components", "pages"):
        folder = module_dir / sub
        if not folder.is_dir():
            continue
        for qml in sorted(folder.rglob("*.qml")):
            name = qml.stem
            if name == "Theme":
                continue
            rel = qml.relative_to(module_dir).as_posix()
            lines.append(f"{name} 1.0 {rel}")
    (module_dir / "qmldir").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    mod = root / "qml" / "AquaMeasure"
    write_qmldir(mod)
    print(f"qmldir ecrit : {mod / 'qmldir'}")
