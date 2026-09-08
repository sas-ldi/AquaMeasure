#!/usr/bin/env python3
"""Recette hors ecran du volet de droite : le poisson et sa chaine.

Le client a signale deux fois des debordements dans ce volet, qui fait 380 px
par defaut et 300 px replie. Ce script rend le VRAI panneau, avec les VRAIS
controleurs et une base isolee, aux deux largeurs et dans deux etats, puis
capture chaque image et verifie par le calcul qu'aucun element ne sort du
cadre.

Quatre images :

  380-poisson-complet   taxon, taille, piste et bouchees : les quatre jalons
                        du bandeau allumes, le bloc de suivi renseigne, les
                        bouchees calees sur la piste du poisson ;
  380-poisson-nu        le meme volet sur un poisson mesure sans piste : deux
                        jalons eteints, et le bloc des bouchees qui dit
                        comment obtenir une piste ;
  300-poisson-complet   le meme etat complet a la largeur repliee minimale ;
  300-poisson-nu        idem pour le poisson nu.

Usage :
    python aquameasure-pyside/tools/capture_fish_chain_panel.py [--out DOSSIER]

La base reelle n'est jamais touchee : FISH_VISION_DB, FISH_VISION_SETTINGS et
AQUAMEASURE_STORAGE_CONFIG pointent vers un dossier temporaire.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from contextlib import ExitStack
from pathlib import Path

HERE = Path(__file__).resolve().parent
APP_DIR = HERE.parent
REPO_ROOT = APP_DIR.parent
for candidate in (APP_DIR, REPO_ROOT):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

import main as am  # noqa: E402

DEFAULT_OUT = REPO_ROOT / "docs" / "captures" / "volet-poisson"
PANEL_HEIGHT = 860
FRAME_COUNT = 60
FRAME_W, FRAME_H = 320, 240
# Les deux largeurs du volet : SplitView.preferredWidth et minimumWidth dans
# MeasurePage.qml. Toute la contrainte d'affichage tient dans ces deux nombres.
WIDTHS = (380, 300)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out", type=Path, default=DEFAULT_OUT,
        help=f"dossier des captures (defaut : {DEFAULT_OUT})",
    )
    return parser.parse_args()


def _isolate_data(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    config = root / "storage.json"
    config.write_text(
        json.dumps({"data_root": str(root)}, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    os.environ["AQUAMEASURE_STORAGE_CONFIG"] = str(config)
    os.environ["FISH_VISION_DB"] = str(root / "fish_annotations.db")
    os.environ["FISH_VISION_SETTINGS"] = str(root / "app_settings.json")


def _prepare_isolated_qml(work: Path) -> Path:
    source = APP_DIR / "qml"
    qml_root = work / "qml"
    module = qml_root / "AquaMeasure"
    qml_root.mkdir(parents=True, exist_ok=True)
    for subdir in ("components", "pages", "style"):
        shutil.copytree(source / subdir, module / subdir)

    from tools.gen_qmldir import write_qmldir

    write_qmldir(module)
    return qml_root


def _write_demo_video(path: Path) -> None:
    import cv2
    import numpy as np

    writer = cv2.VideoWriter(
        str(path), cv2.VideoWriter_fourcc(*"MJPG"), 25.0, (FRAME_W, FRAME_H),
    )
    if not writer.isOpened():
        raise RuntimeError(f"Ecriture video impossible : {path}")
    for index in range(FRAME_COUNT):
        frame = np.zeros((FRAME_H, FRAME_W, 3), dtype=np.uint8)
        frame[:, :, 0] = (index * 4) % 255
        writer.write(frame)
    writer.release()


def _seed(media: Path) -> dict:
    """Un poisson complet, un poisson nu, une piste et trois bouchees."""
    import fish_annotate as fa

    from src.annodb.annotators import create_annotator
    from src.annodb.connection import session_scope
    from src.annodb.tracks import (
        add_track_sample,
        get_or_create_track,
        refresh_track_bounds,
    )

    media_id = fa.resolve_media_id(str(media), create=True)
    if not media_id:
        raise RuntimeError("Media non enregistre dans la base isolee")
    with session_scope() as session:
        create_annotator(session, display_name="Thomas Lamy")
        track = get_or_create_track(
            session, media_id=media_id, external_track_id=7,
        )
        track_id = track.id
        for frame in range(10, 41):
            add_track_sample(
                session, track_id=track_id, frame_index=frame,
                cx=160.0, cy=120.0, bbox=(100.0, 80.0, 220.0, 160.0),
            )
        refresh_track_bounds(session, track_id)

    complet = fa.add_observation(
        str(media), 12, {"x1": 100.0, "y1": 80.0, "x2": 220.0, "y2": 160.0},
        source="manual", measurement_mm=154.2, track_id=track_id,
        frame_ref="absolute",
    )
    nu = fa.add_observation(
        str(media), 20, {"x1": 40.0, "y1": 40.0, "x2": 110.0, "y2": 95.0},
        source="manual", measurement_mm=98.7, frame_ref="absolute",
    )
    behavior = fa.create_behavior_type("Bouchée", "point", "●")
    behavior = fa.set_behavior_shortcut(behavior["id"], "B")
    for frame in (14, 18, 25):
        fa.add_behavior_point(
            track_id, frame, event_type=behavior["key"], frame_ref="absolute",
        )
    return {
        "media_id": media_id,
        "track_id": track_id,
        "complet": complet["ann_id"],
        "nu": nu["ann_id"],
        "behavior": behavior,
    }


def _outside(panel) -> list[tuple[str, float, float]]:
    """Elements peints dont un bord sort du panneau - la preuve chiffree."""
    out: list[tuple[str, float, float]] = []

    def paints(item) -> bool:
        # Un QQuickItem nu sans enfant ne peint rien : la ListView en garde un
        # en reserve pour recycler ses delegues.
        return item.metaObject().className() != "QQuickItem" or bool(
            item.childItems()
        )

    def walk(item, offset_x: float, path: str) -> None:
        for child in item.childItems():
            if not child.isVisible() or child.width() <= 0:
                continue
            left = offset_x + child.x()
            here = "%s/%s" % (
                path, child.objectName() or child.metaObject().className(),
            )
            if (left < -1.0 or left + child.width() > panel.width() + 1.0) \
                    and paints(child):
                out.append((here, round(left, 1),
                            round(left + child.width(), 1)))
            walk(child, left, here)

    walk(panel, 0.0, "")
    return out


def main() -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("QT_QUICK_BACKEND", "software")
    os.environ.setdefault("QSG_RHI_BACKEND", "software")

    args = _arguments()
    out_dir = args.out.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    with ExitStack() as stack:
        data_root = Path(stack.enter_context(tempfile.TemporaryDirectory(
            prefix="aquameasure-chain-data-", ignore_cleanup_errors=True,
        )))
        qml_work = Path(stack.enter_context(tempfile.TemporaryDirectory(
            prefix="aquameasure-chain-qml-", ignore_cleanup_errors=True,
        )))
        _isolate_data(data_root)
        am._setup_paths()
        qml_root = _prepare_isolated_qml(qml_work)

        media = data_root / "recif_gauche.avi"
        _write_demo_video(media)
        seeded = _seed(media)

        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QFont, QFontDatabase
        from PySide6.QtQml import QQmlApplicationEngine, QQmlComponent
        from PySide6.QtQuick import QQuickItem
        from PySide6.QtQuickControls2 import QQuickStyle
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QApplication

        from src.controllers.app_controller import AppController
        from src.imaging.frame_image_provider import FrameImageProvider

        app = QApplication(sys.argv[:1])
        QQuickStyle.setStyle("Basic")
        if os.name == "nt":
            fonts = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
            for name in ("segoeui.ttf", "segoeuib.ttf", "consola.ttf"):
                if (fonts / name).is_file():
                    QFontDatabase.addApplicationFont(str(fonts / name))
        families = set(QFontDatabase.families())
        sans = next((n for n in ("Segoe UI", "Arial", "DejaVu Sans")
                     if n in families), "Segoe UI")
        mono = next((n for n in ("Consolas", "Courier New", "DejaVu Sans Mono")
                     if n in families), "Consolas")
        app.setFont(QFont(sans))

        ctrl = AppController()
        images = FrameImageProvider()
        ctrl.sync().set_image_provider(images)
        ctrl.calibration().set_image_provider(images)
        ctrl.measure().set_image_provider(images)

        engine = QQmlApplicationEngine()
        engine.addImportPath(str(qml_root))
        engine.addImageProvider("frames", images)
        ctx = engine.rootContext()
        for name, value in (
            ("App", ctrl), ("Device", ctrl.device()), ("Sync", ctrl.sync()),
            ("Calib", ctrl.calibration()), ("Measure", ctrl.measure()),
            ("Settings", ctrl.settings()), ("Fish", ctrl.fish()),
            ("Detectors", ctrl.detectors()), ("Data", ctrl.data()),
            ("DbExplorer", ctrl.dbExplorer()), ("Sessions", ctrl.sessions()),
            ("Annotator", ctrl.annotator()), ("ProTools", ctrl.proTools()),
            ("Tracks", ctrl.tracks()), ("Pecks", ctrl.pecks()),
            ("Storage", ctrl.storage()), ("AppLogoUrl", QUrl()),
            ("AppSansFont", sans), ("AppMonoFont", mono),
        ):
            ctx.setContextProperty(name, value)

        measure = ctrl.measure()
        data = ctrl.data()
        pecks = ctrl.pecks()
        service = measure._service  # noqa: SLF001
        measure._left = str(media)  # noqa: SLF001
        measure._frame_count = FRAME_COUNT  # noqa: SLF001
        measure._frame_index = 14  # noqa: SLF001
        service._total_frames = FRAME_COUNT  # noqa: SLF001
        service._left_start = 0  # noqa: SLF001
        service._width = FRAME_W  # noqa: SLF001
        service._height = FRAME_H  # noqa: SLF001
        measure.leftVideoChanged.emit()
        measure.videoMetaChanged.emit()
        measure.frameCountChanged.emit()
        measure.frameIndexChanged.emit()
        data.loadEventTypes()
        data.refreshRegistry()
        pecks.refresh()

        def select(ann_id: str) -> None:
            for row in range(data.registry.rowCount()):
                if data.registry.row_at(row).get("ann_id") == ann_id:
                    data.focusRegistryRow(row)
                    return
            raise RuntimeError(f"Observation absente du registre : {ann_id}")

        # Le poisson complet porte aussi son taxon : c'est le geste reel,
        # « Valider l'identification » sur la ligne selectionnee.
        select(seeded["complet"])
        data.saveSelectedRow("Acanthuridae", "", "Ctenochaetus striatus")

        failures = 0
        for width in WIDTHS:
            component = QQmlComponent(engine)
            component.setData(("""
                import QtQuick
                import QtQuick.Window
                import AquaMeasure
                Window { width: %d; height: %d; color: "#0b1015"
                    MeasureRegistryPanel { anchors.fill: parent; compact: true }
                }
            """ % (width, PANEL_HEIGHT)).encode("utf-8"),
                QUrl("inline:capture.qml"))
            while component.isLoading():
                QTest.qWait(20)
            window = component.create()
            if window is None:
                print([str(error) for error in component.errors()])
                return 1
            window.show()
            QTest.qWait(300)
            panel = window.findChild(QQuickItem, "measureRegistryPanel")

            for label, ann_id, scrolled in (
                ("poisson-complet", seeded["complet"], False),
                ("poisson-complet-bas", seeded["complet"], True),
                ("poisson-nu", seeded["nu"], False),
            ):
                select(ann_id)
                QTest.qWait(300)
                flick = window.findChild(QQuickItem, "registryBodyFlick")
                if flick is not None:
                    # Le volet défile : la seconde image montre le bas, là où
                    # se posent les bouchées.
                    flick.setProperty(
                        "contentY",
                        max(0.0, float(flick.property("contentHeight"))
                            - flick.height()) if scrolled else 0.0,
                    )
                    QTest.qWait(200)
                name = f"{width}-{label}"
                debordements = _outside(panel)
                image = window.grabWindow()
                path = out_dir / f"{name}.png"
                if image.isNull() or not image.save(str(path)):
                    print(f"[!] Capture impossible : {path}")
                    failures += 1
                    continue
                print(f"-> {path}  ({image.width()}x{image.height()})")
                print(f"   bandeau : {data.selectedTrackNumber} · "
                      f"{data.editMeasurementMm} mm · "
                      f"piste {data.selectedTrackDbId or '-'}")
                flick = window.findChild(QQuickItem, "registryBodyFlick")
                peck = window.findChild(QQuickItem, "trackPeckPanel")
                if flick is not None and peck is not None:
                    content = float(flick.property("contentHeight"))
                    bottom = peck.mapToItem(flick.childItems()[0], 0.0,
                                            peck.height()).y()
                    print(f"   contenu {round(content, 1)} px pour "
                          f"{round(flick.height(), 1)} px de volet · bas du "
                          f"bloc « Bouchées » à {round(bottom, 1)} px "
                          f"({'atteignable' if bottom <= content + 1 else 'HORS CONTENU'})")
                if debordements:
                    failures += 1
                    print(f"   [!] {len(debordements)} element(s) hors cadre :")
                    for row in debordements:
                        print(f"       {row}")
                else:
                    print("   aucun element hors du cadre")
            window.close()

        try:
            from src.annodb import connection

            if connection._engine is not None:  # noqa: SLF001
                connection._engine.dispose()  # noqa: SLF001
                connection._engine = None  # noqa: SLF001
                connection._SessionLocal = None  # noqa: SLF001
        except Exception as exc:
            print(f"[!] Fermeture de la base : {exc}")
        return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
