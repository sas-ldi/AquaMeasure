#!/usr/bin/env python3
"""Recette hors ecran du chantier « bouchees » : captures a l'appui.

La recette client demande l'application lancee. Sur un poste sans les poids ni
la base reelle (worktree de developpement), ce script en fait la preuve
autrement : il monte une base isolee, une vraie paire de videos de synthese et
une piste suivie, puis charge le MEME `Main.qml` et les MEMES controleurs que
l'application, hors ecran, et capture chaque etape.

Ce qui est prouve, image par image :

  01-reglages-type-bouchee   le type « Bouchee » cree depuis les reglages,
                             portee ponctuelle, avec sa lettre de raccourci ;
  02-piste-selectionnee      la piste choisie depuis le panneau de droite, la
                             cible cliquable sur l'image, la plage sur la barre ;
  03-trois-marqueurs         trois bouchees posees (clavier, clic, bouton),
                             visibles sur la barre et comptees dans le panneau ;
  04-marqueur-retire         un marqueur retire, le compte suit.

Usage :
    python aquameasure-pyside/tools/capture_peck_markers.py [--out DOSSIER]

La base reelle n'est jamais touchee : FISH_VISION_DB, FISH_VISION_SETTINGS et
AQUAMEASURE_STORAGE_CONFIG pointent tous vers un dossier temporaire.
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

DEFAULT_OUT = HERE / "output" / "bouchees"
WIN_W, WIN_H = 1600, 980
SETTLE_MS = 1200
FRAME_COUNT = 240
FRAME_W, FRAME_H = 640, 480


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out", type=Path, default=DEFAULT_OUT,
        help=f"dossier des captures (defaut : {DEFAULT_OUT})",
    )
    parser.add_argument(
        "--onscreen", action="store_true",
        help="affiche la fenetre au lieu de la plate-forme Qt offscreen",
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
    """Copie le module QML dans un dossier temporaire importable."""
    source = APP_DIR / "qml"
    qml_root = work / "qml"
    module = qml_root / "AquaMeasure"
    qml_root.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source / "Main.qml", qml_root / "Main.qml")
    for subdir in ("components", "pages", "style"):
        shutil.copytree(source / subdir, module / subdir)

    from tools.gen_qmldir import write_qmldir

    write_qmldir(module)
    return qml_root


def _write_demo_video(path: Path, *, shift: int) -> None:
    """Sequence de synthese : un fond de substrat et un poisson qui derive."""
    import cv2
    import numpy as np

    writer = cv2.VideoWriter(
        str(path), cv2.VideoWriter_fourcc(*"MJPG"), 25.0, (FRAME_W, FRAME_H),
    )
    if not writer.isOpened():
        raise RuntimeError(f"Ecriture video impossible : {path}")
    rng = np.random.default_rng(7)
    substrate = (rng.integers(30, 70, size=(FRAME_H, FRAME_W, 3))).astype("uint8")
    substrate[:, :, 0] = np.clip(substrate[:, :, 0] + 60, 0, 255)
    for index in range(FRAME_COUNT):
        frame = substrate.copy()
        x = 120 + int(index * 1.4) + shift
        y = 200 + int(40 * np.sin(index / 12.0))
        cv2.ellipse(frame, (x + 60, y + 40), (58, 26), 0, 0, 360, (60, 170, 230), -1)
        cv2.circle(frame, (x + 100, y + 32), 5, (20, 20, 20), -1)
        writer.write(frame)
    writer.release()


def _seed(media_left: Path) -> tuple[str, str, dict]:
    """Media, piste suivie et type « Bouchee » dans la base isolee."""
    import fish_annotate as fa

    from src.annodb.annotators import create_annotator
    from src.annodb.connection import session_scope
    from src.annodb.tracks import (
        add_track_sample,
        get_or_create_track,
        refresh_track_bounds,
    )

    media_id = fa.resolve_media_id(str(media_left), create=True)
    if not media_id:
        raise RuntimeError("Media non enregistre dans la base isolee")

    with session_scope() as session:
        create_annotator(session, display_name="Thomas Lamy")
        track = get_or_create_track(
            session, media_id=media_id, external_track_id=12,
        )
        track_id = track.id
        for index in range(40, 200):
            x = 120 + index * 1.4
            y = 200 + 40 * __import__("math").sin(index / 12.0)
            add_track_sample(
                session, track_id=track_id, frame_index=index,
                cx=x + 60, cy=y + 40,
                bbox=(x, y, x + 120, y + 80),
            )
        refresh_track_bounds(session, track_id)

    # « Broutage » porte un raccourci « B » depuis toujours, sans qu'aucune
    # touche ne lui soit branchee. La lettre revient a la bouchee, qui elle est
    # posee au clavier ; deux lignes annoncant la meme touche seraient un doute
    # de plus a l'ecran.
    grazing = next(
        (row for row in fa.list_event_types(active_only=False)
         if row["key"] == "grazing"), None,
    )
    if grazing is not None:
        fa.set_behavior_shortcut(grazing["id"], "")
    behavior = fa.create_behavior_type("Bouchée", "point", "●")
    behavior = fa.set_behavior_shortcut(behavior["id"], "B")
    return media_id, track_id, behavior


def main() -> int:
    args = _arguments()
    if not args.onscreen:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        os.environ.setdefault("QT_QUICK_BACKEND", "software")
        os.environ.setdefault("QSG_RHI_BACKEND", "software")

    out_dir = args.out.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    with ExitStack() as stack:
        data_root = Path(stack.enter_context(tempfile.TemporaryDirectory(
            prefix="aquameasure-peck-data-", ignore_cleanup_errors=True,
        )))
        qml_work = Path(stack.enter_context(tempfile.TemporaryDirectory(
            prefix="aquameasure-peck-qml-", ignore_cleanup_errors=True,
        )))
        _isolate_data(data_root)
        print(f"Base isolee : {data_root}", flush=True)

        app_dir = am._setup_paths()
        qml_root = _prepare_isolated_qml(qml_work)

        left = data_root / "recif_gauche.avi"
        right = data_root / "recif_droite.avi"
        _write_demo_video(left, shift=0)
        _write_demo_video(right, shift=-18)
        media_id, track_id, behavior = _seed(left)
        print(f"Piste {track_id} · type {behavior['key']} "
              f"(raccourci {behavior['shortcut']})", flush=True)

        from PySide6.QtCore import QTimer, QUrl
        from PySide6.QtGui import QFont, QFontDatabase
        from PySide6.QtQml import QQmlApplicationEngine
        from PySide6.QtQuickControls2 import QQuickStyle
        from PySide6.QtWidgets import QApplication

        from src.controllers.app_controller import AppController
        from src.imaging.frame_image_provider import FrameImageProvider

        QApplication.setOrganizationName("IRD")
        QApplication.setApplicationName("AquaMeasure")
        app = QApplication(sys.argv[:1])
        QQuickStyle.setStyle("Basic")

        if os.name == "nt":
            fonts = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
            for name in ("segoeui.ttf", "segoeuib.ttf", "consola.ttf"):
                if (fonts / name).is_file():
                    QFontDatabase.addApplicationFont(str(fonts / name))
        families = set(QFontDatabase.families())
        sans = next((n for n in ("Segoe UI", "Arial", "DejaVu Sans")
                     if n in families), "")
        mono = next((n for n in ("Consolas", "Courier New", "DejaVu Sans Mono")
                     if n in families), "")
        if sans:
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
            ("Storage", ctrl.storage()),
            ("AppLogoUrl", QUrl.fromLocalFile(
                str((app_dir / "resources" / "logo_ird.png").resolve()))),
            ("AppSansFont", sans or "Segoe UI"),
            ("AppMonoFont", mono or "Consolas"),
        ):
            ctx.setContextProperty(name, value)

        engine.load(QUrl.fromLocalFile(str((qml_root / "Main.qml").resolve())))
        roots = engine.rootObjects()
        if not roots:
            print("Echec du chargement QML")
            return 1
        win = roots[0]
        win.setProperty("visibility", 2)
        win.setWidth(WIN_W)
        win.setHeight(WIN_H)

        measure = ctrl.measure()
        data = ctrl.data()
        pecks = ctrl.pecks()
        service = measure._service  # noqa: SLF001

        def arm_measure_page() -> None:
            """Etat de la page Mesure sans calibration ni synchro sur disque.

            `Measure.refresh` demande des parametres camera absents ici ; on
            pousse donc directement ce que le service aurait calcule, la video
            reelle restant affichee par le lecteur QML.
            """
            measure._left = str(left)  # noqa: SLF001
            measure._right = str(right)  # noqa: SLF001
            measure._frame_count = FRAME_COUNT  # noqa: SLF001
            measure._frame_index = 60  # noqa: SLF001
            service._total_frames = FRAME_COUNT  # noqa: SLF001
            service._left_start = 0  # noqa: SLF001
            service._width = FRAME_W  # noqa: SLF001
            service._height = FRAME_H  # noqa: SLF001
            service._left_fps = 25.0  # noqa: SLF001
            measure.leftVideoChanged.emit()
            measure.rightVideoChanged.emit()
            measure.videoMetaChanged.emit()
            measure.frameCountChanged.emit()
            measure.frameIndexChanged.emit()
            data._media_id = media_id  # noqa: SLF001
            data.mediaIdChanged.emit()
            pecks.refresh()

        def scroll_to_behaviors() -> None:
            """La carte « Comportements » est en bas des Preferences."""
            from PySide6.QtCore import QObject

            scroll = win.findChild(QObject, "settingsScroll")
            field = win.findChild(QObject, "behaviorNameField")
            if scroll is None or field is None:
                print("  (carte Comportements introuvable)", flush=True)
                return
            flick = scroll.property("contentItem")
            content = flick.property("contentItem")
            target = field.mapToItem(content, 0, 0).y() - 120
            flick.setProperty(
                "contentY",
                max(0.0, min(target,
                             flick.property("contentHeight")
                             - flick.property("height"))),
            )

        def select_track() -> None:
            arm_measure_page()
            pecks.selectTrack(track_id)
            measure.frameIndex = 70
            print(f"  piste : {pecks.selectedTrackLabel} · "
                  f"plage f{pecks.selectedFirstFrame}-f{pecks.selectedLastFrame}",
                  flush=True)

        def mark_three() -> None:
            for frame in (72, 88, 104):
                measure.frameIndex = frame
                ok = pecks.markAtCurrentFrame()
                print(f"  marqueur f{frame} : {ok} — {pecks.statusText}", flush=True)
            measure.frameIndex = 104

        def remove_one() -> None:
            pecks.removeMarker(pecks.markers[1]["eventId"])
            print(f"  apres retrait : {pecks.markerCount} marqueur(s) "
                  f"{[m['frameAbs'] for m in pecks.markers]}", flush=True)

        shots = [
            (8, "01-reglages-type-bouchee", scroll_to_behaviors),
            (4, "02-piste-selectionnee", select_track),
            (4, "03-trois-marqueurs", mark_three),
            (4, "04-marqueur-retire", remove_one),
        ]

        state = {"i": 0, "failed": False}

        def grab(name: str) -> None:
            img = win.grabWindow()
            path = out_dir / f"{name}.png"
            if img.isNull() or not img.save(str(path)):
                state["failed"] = True
                print(f"  [!] Capture impossible : {path.name}", flush=True)
                return
            print(f"  -> {path}  ({img.width()}x{img.height()})", flush=True)

        def step() -> None:
            i = state["i"]
            if i >= len(shots):
                print("Captures terminees.", flush=True)
                app.quit()
                return
            page, name, action = shots[i]
            print(f"[{i + 1}/{len(shots)}] page {page} -> {name}", flush=True)
            ctrl.currentPage = page
            if action is not None:
                action()
            state["i"] = i + 1
            QTimer.singleShot(SETTLE_MS, lambda: (grab(name), step()))

        QTimer.singleShot(1200, step)
        code = app.exec()

        points = __import__("fish_annotate").list_behavior_points(str(left))
        print("Relecture en base :", json.dumps(
            [{"frame": row["frame_abs"], "type": row["event_type"],
              "piste": row["external_track_id"], "auteur": row["author"]}
             for row in points], ensure_ascii=False,
        ), flush=True)

        try:
            from src.annodb import connection

            if connection._engine is not None:  # noqa: SLF001
                connection._engine.dispose()  # noqa: SLF001
                connection._engine = None  # noqa: SLF001
                connection._SessionLocal = None  # noqa: SLF001
        except Exception as exc:
            print(f"[!] Fermeture de la base : {exc}", flush=True)
        return 1 if state["failed"] else code


if __name__ == "__main__":
    raise SystemExit(main())
