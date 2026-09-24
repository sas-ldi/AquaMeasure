"""Vérifie un premier usage compilé : catalogue complet, aucune donnée d'essai."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import traceback


def _check_video_playback(root: Path) -> str:
    """Le lecteur QtMultimedia doit ouvrir un MP4 (plugins FFmpeg livrés)."""
    import cv2
    import numpy as np
    from PySide6.QtCore import QEventLoop, QTimer, QUrl
    from PySide6.QtMultimedia import QMediaPlayer, QVideoSink

    real = os.environ.get("AQUAMEASURE_TEST_VIDEO")
    video = Path(real) if real else root / "lecture.mp4"
    writer = None if real else cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*"mp4v"), 25, (320, 240))
    for index in range(25 if writer else 0):
        writer.write(np.full((240, 320, 3), index * 8, np.uint8))
    if writer:
        writer.release()
    player = QMediaPlayer()
    sink = QVideoSink()
    player.setVideoOutput(sink)
    loop = QEventLoop()
    player.mediaStatusChanged.connect(lambda *_: loop.quit())
    player.errorOccurred.connect(lambda *_: loop.quit())
    QTimer.singleShot(15000, loop.quit)
    player.setSource(QUrl.fromLocalFile(str(video)))
    ok = (QMediaPlayer.MediaStatus.LoadedMedia, QMediaPlayer.MediaStatus.BufferedMedia)
    for _ in range(10):
        if player.mediaStatus() in ok or player.error() != QMediaPlayer.Error.NoError:
            break
        loop.exec()
    status, duration, error = player.mediaStatus(), player.duration(), player.errorString()
    player.stop()
    player.setSource(QUrl())
    assert status in ok, f"Lecteur vidéo inopérant : {status} {error}"
    assert duration > 0, "Durée vidéo nulle"
    return f"{status.name} {video.name} {duration} ms"


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
            report["video_playback"] = _check_video_playback(root)
            report["ok"] = True
        except Exception:
            report["error"] = traceback.format_exc()
        finally:
            if engine is not None:
                engine.dispose()
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return 0 if report["ok"] else 1
