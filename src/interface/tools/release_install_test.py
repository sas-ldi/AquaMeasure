"""Vérifie un premier usage compilé : catalogue complet, aucune donnée d'essai."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import traceback


def run(output: Path, app_dir: Path) -> int:
    report = {"ok": False}
    engine = None
    with tempfile.TemporaryDirectory(prefix="aquameasure-first-run-") as folder:
        root = Path(folder)
        config = root / "storage.json"
        config.write_text(json.dumps({"data_root": str(root)}), encoding="utf-8")
        os.environ.update(AQUAMEASURE_STORAGE_CONFIG=str(config),
                          FISH_VISION_DB=str(root / "fresh.db"),
                          FISH_VISION_SETTINGS=str(root / "settings.json"),
                          HF_HOME=str(root / "hf"), HF_HUB_OFFLINE="1")
        try:
            from PySide6.QtCore import QStandardPaths
            from PySide6.QtWidgets import QApplication
            import main
            from src.controllers.app_controller import AppController
            from src.annodb.connection import get_engine

            QStandardPaths.setTestModeEnabled(True)
            QApplication.setOrganizationName("AquaMeasureReleaseTest")
            QApplication.setApplicationName("AquaMeasureReleaseTest")
            app = QApplication.instance() or QApplication(sys.argv)
            main._set_application_icon(app, app_dir)
            assert not app.windowIcon().isNull(), "Icône IRD absente"
            report["icon_sizes"] = [[s.width(), s.height()] for s in app.windowIcon().availableSizes()]
            controller = AppController()
            controller.currentPage = 4  # ouverture normale de la page Mesure
            if getattr(sys, "frozen", False):
                report["database_next_to_executable"] = (app_dir.parent / "annotations/data/fish_annotations.db").exists()
                assert not report["database_next_to_executable"], "Base créée dans le dossier de l'exécutable"
            database = root / "fresh.db"
            assert database.is_file(), "Base vierge non créée"
            engine = get_engine(database)
            connection = sqlite3.connect(database)
            try:
                report["taxonomy"] = dict(connection.execute("SELECT rank, COUNT(*) FROM taxon_nodes GROUP BY rank"))
                names = {row[0] for row in connection.execute("SELECT scientific_name FROM taxon_nodes WHERE rank='species'")}
                assert len(names) == 2438, f"Catalogue incomplet : {len(names)} espèces"
                assert {"Anguilla australis", "Pteroplatytrygon violacea", "Chromis chromis"} <= names
                assert not any("demofish" in name.lower() for name in names)
                tables = ("sessions", "media_assets", "spatial_annotations", "tracks", "track_samples",
                          "temporal_events", "taxon_reference_embeddings", "frame_abundance", "calibrations")
                report["working_data"] = {name: connection.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
                                          for name in tables}
                assert not any(report["working_data"].values()), "Données de démonstration présentes"
            finally:
                connection.close()
            report["ok"] = True
        except Exception:
            report["error"] = traceback.format_exc()
        finally:
            if engine is not None:
                engine.dispose()
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return 0 if report["ok"] else 1
