#!/usr/bin/env python3
"""Chargement hors ecran de chaque page QML — detecte les erreurs de binding.

Usage : python interface/tools/qml_smoke.py
Sortie : liste des warnings QML par page, code retour 1 si au moins un warning.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import main as app_main  # noqa: E402

WARNINGS: list[str] = []


def main() -> int:
    app_dir = app_main._setup_paths()
    qml_root = app_main._prepare_qml_module(app_dir)

    from PySide6.QtCore import QTimer, QUrl, qInstallMessageHandler
    from PySide6.QtQml import QQmlApplicationEngine
    from PySide6.QtQuickControls2 import QQuickStyle
    from PySide6.QtWidgets import QApplication

    from src.controllers.app_controller import AppController
    from src.imaging.frame_image_provider import FrameImageProvider

    def on_message(mode, context, message):
        text = str(message)
        if "qrc:" in text or ".qml" in text:
            WARNINGS.append(text)
        print(text, flush=True)

    qInstallMessageHandler(on_message)

    app = QApplication(sys.argv)
    QQuickStyle.setStyle("Basic")

    app_ctrl = AppController()
    images = FrameImageProvider()
    app_ctrl.sync().set_image_provider(images)
    app_ctrl.calibration().set_image_provider(images)
    app_ctrl.measure().set_image_provider(images)

    engine = QQmlApplicationEngine()
    engine.addImportPath(str(qml_root))
    engine.addImageProvider("frames", images)

    ctx = engine.rootContext()
    ctx.setContextProperty("App", app_ctrl)
    ctx.setContextProperty("Device", app_ctrl.device())
    ctx.setContextProperty("Sync", app_ctrl.sync())
    ctx.setContextProperty("Calib", app_ctrl.calibration())
    ctx.setContextProperty("Measure", app_ctrl.measure())
    ctx.setContextProperty("Settings", app_ctrl.settings())
    ctx.setContextProperty("Fish", app_ctrl.fish())
    ctx.setContextProperty("Detectors", app_ctrl.detectors())
    ctx.setContextProperty("Data", app_ctrl.data())
    ctx.setContextProperty("DbExplorer", app_ctrl.dbExplorer())
    ctx.setContextProperty("Sessions", app_ctrl.sessions())
    ctx.setContextProperty("Annotator", app_ctrl.annotator())
    ctx.setContextProperty("ProTools", app_ctrl.proTools())
    ctx.setContextProperty("Tracks", app_ctrl.tracks())
    ctx.setContextProperty("Pecks", app_ctrl.pecks())
    ctx.setContextProperty("Storage", app_ctrl.storage())
    ctx.setContextProperty("AppLogoUrl", QUrl())
    ctx.setContextProperty("AppSansFont", "Segoe UI")
    ctx.setContextProperty("AppMonoFont", "Consolas")

    engine.load(QUrl.fromLocalFile(str((qml_root / "Main.qml").resolve())))
    roots = engine.rootObjects()
    if not roots:
        print("[!] Main.qml non charge")
        return 1
    window = roots[0]

    def exercise_registry():
        data = app_ctrl.data()
        rows = data.registry.rowCount()
        print(f"--- registre : {rows} ligne(s) ---", flush=True)
        if rows <= 0:
            return
        data.focusRegistryRow(0)
        print(
            f"    selection : {data.selectedLabel!r} frame={data.selectedFrameIndex} "
            f"row={data.selectedRegistryRow} boxIndex={data.selectedBoxIndex}",
            flush=True,
        )
        print(f"    focusBox  : {app_ctrl.fish().focusBox}", flush=True)
        print(f"    Measure.frameIndex = {app_ctrl.measure().frameIndex}", flush=True)

    def report_match():
        data = app_ctrl.data()
        fish = app_ctrl.fish()
        print(
            f"    apres detection : {fish.lastBoxCount} bbox IA · "
            f"selectedFishIndex={fish.selectedFishIndex} · "
            f"Data.selectedBoxIndex={data.selectedBoxIndex} · "
            f"focus encore actif={fish.focusBox.get('valid')}",
            flush=True,
        )
        target, idx = fish._measure_target_box()
        print(f"      cible mesure : idx={idx} bbox={target}", flush=True)

    def open_detector_manager():
        window.openDetectorManager()
        detectors = app_ctrl.detectors()
        print(
            f"    actif : {detectors.activeId} ({detectors.activeBackend}) · "
            f"{detectors.installedCount}/{len(detectors.models)} modele(s) utilisable(s)",
            flush=True,
        )

    def report_sessions():
        sessions = app_ctrl.sessions()
        print(
            f"    sessions : {sessions.sessionCount} · {sessions.statusText!r}",
            flush=True,
        )

    def exercise_tracks():
        """Onglet « Pistes » : le panneau doit se peupler sans warning."""
        data = app_ctrl.data()
        tracks = app_ctrl.tracks()
        data.subTab = 2
        tracks.refresh()
        print(
            f"    pistes : {tracks.count} · {tracks.suspiciousCount} a verifier "
            f"({tracks.singleFrameCount} d'une frame, {tracks.veryLongCount} "
            f"tres longues, {tracks.gapCount} avec trous) · "
            f"{tracks.sampleCount} position(s)",
            flush=True,
        )
        print(f"    statut    : {tracks.statusText!r}", flush=True)
        if tracks.count > 0:
            tracks.selectRow(0)
            row = tracks.tracks.row_at(0)
            print(
                f"    1re piste : {tracks.selectedLabel} · "
                f"{row['sample_count']} position(s) · span {row['span']} · "
                f"suspicion {row['suspicion']} · drapeaux "
                f"1frame={row['single_frame']} longue={row['very_long']} "
                f"trous={row['has_gaps']}",
                flush=True,
            )
        data.subTab = 3

    def report_coco_without_pro():
        """COCO reste une action terrain ; les autres outils gardent leur garde Pro."""
        from PySide6.QtCore import QObject

        app_ctrl.settings().proMode = False
        app_ctrl.data().subTab = 3
        button = window.findChild(QObject, "cocoExportButton")
        if button is None:
            raise RuntimeError("Bouton Export COCO introuvable")
        actionable = bool(button.property("actionable"))
        print(
            f"    proMode={app_ctrl.settings().proMode} · "
            f"Export COCO actionable={actionable}",
            flush=True,
        )
        if not actionable:
            raise RuntimeError("Export COCO doit être utilisable hors Mode Pro")

    def report_measure_export_guide():
        expected = "Données & IA > Exports > Export COCO"
        source = (qml_root / "components" / "MeasureRegistryPanel.qml").read_text(
            encoding="utf-8",
        )
        if expected not in source:
            raise RuntimeError(f"Guide Mesure incorrect : {expected!r} absent")
        print(f"    guide Mesure : {expected}", flush=True)

    def report_storage():
        """Page Parametres : les chemins affiches sont ceux reellement utilises."""
        storage = app_ctrl.storage()
        print(f"    racine      : {storage.dataRoot} (defaut={storage.isDefaultRoot})", flush=True)
        print(f"    config      : {storage.configPath} (existe={storage.configExists})", flush=True)
        print(f"    base        : {storage.dbPath} (existe={storage.dbExists})", flush=True)
        print(f"    calibrations: {storage.calibrationsPath}", flush=True)
        print(f"    exports     : {storage.exportsPath}", flush=True)
        print(f"    medias      : {storage.mediaPath}", flush=True)
        print(f"    sauvegarde  : {storage.backupSummary!r} (retard={storage.backupOverdue})", flush=True)

    def report_storage_sizes():
        storage = app_ctrl.storage()
        print(
            f"    tailles : base={storage.dbSize} calib={storage.calibrationsSize} "
            f"exports={storage.exportsSize} medias={storage.mediaSize}",
            flush=True,
        )
        print(
            f"    exports traces : {storage.exportCount} · dernier {storage.lastExport!r} "
            f"· profils calib {storage.calibrationProfileCount}",
            flush=True,
        )

    steps = iter([
        ("page 4", lambda: setattr(app_ctrl, "currentPage", 4)),
        ("page 5", lambda: setattr(app_ctrl, "currentPage", 5)),
        ("onglet Pistes", exercise_tracks),
        ("export COCO hors Mode Pro", report_coco_without_pro),
        ("guide export depuis Mesure", report_measure_export_guide),
        ("page 7 (sessions)", lambda: setattr(app_ctrl, "currentPage", 7)),
        ("etat sessions", report_sessions),
        ("retour page 4", lambda: setattr(app_ctrl, "currentPage", 4)),
        ("focus registre", exercise_registry),
        ("resultat detection", report_match),
        ("gestionnaire de modeles", open_detector_manager),
        ("page 8 (parametres)", lambda: setattr(app_ctrl, "currentPage", 8)),
        ("chemins affiches", report_storage),
        ("tailles calculees", report_storage_sizes),
    ])

    def next_step():
        try:
            title, action = next(steps)
        except StopIteration:
            app.quit()
            return
        print(f"--- {title} ---", flush=True)
        action()
        QTimer.singleShot(12000 if title == "focus registre" else 2500, next_step)

    QTimer.singleShot(800, next_step)
    app.exec()

    if WARNINGS:
        print(f"\n{len(WARNINGS)} warning(s) QML")
        return 1
    print("\nAucun warning QML")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
