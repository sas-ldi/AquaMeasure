#!/usr/bin/env python3
"""Capture les écrans réels d'AquaMeasure pour la documentation.

Le script charge le même ``Main.qml`` et les mêmes contrôleurs que
l'application. Par défaut, il travaille hors écran avec :

- une base SQLite et des réglages temporaires, sans toucher aux données réelles ;
- un module QML temporaire, sans régénérer ``qml/AquaMeasure`` dans le dépôt ;
- quelques données de démonstration pour rendre Sessions et Pistes lisibles.

Usage normal (recommandé) :
    python docs/capture_screenshots.py

Options utiles :
    --data-root DOSSIER   conserve la base isolée dans ce dossier ;
    --live-data           capture les données configurées sur le poste ;
    --onscreen            affiche la fenêtre pendant la capture.

Les captures PNG et JPEG sont écrites dans ``docs/images/``. Une capture sans vidéo montre
volontairement les états vides et les boutons indisponibles de l'interface.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from contextlib import ExitStack
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
APP_DIR = HERE.parent / "src" / "interface"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import main as am  # noqa: E402  (main.py de src/interface)

OUT_DIR = HERE / "images"
WIN_W, WIN_H = 1600, 980
SETTLE_MS = 2400  # temps laissé au rendu / chargement de chaque page
TUNE_MS = 700     # temps laissé aux replis et dépliages avant le déclenchement

DEMO_MEDIA_ID = "docs-media-left"
DEMO_SESSION_ID = "docs-session-active"

# Paires de vidéos utilisables pour la capture, de la plus parlante à la plus
# neutre. Sans vidéo, les pages Synchronisation, Calibration et Mesure ne
# montrent qu'un cadre noir : le manuel n'a alors rien à désigner.
DEMO_VIDEO_CANDIDATES = (
    ("video/left/gauche.MP4", "video/right/droite.MP4"),
    ("Calibrate_videos/Left.mov", "Calibrate_videos/Right.mov"),
)


def _demo_video_pair(explicit: tuple[Path, Path] | None) -> tuple[Path, Path] | None:
    """Première paire de vidéos disponible, ou ``None``.

    Les candidats sont cherchés dans le dépôt puis dans son dossier parent :
    les vidéos de terrain pèsent trop lourd pour être versionnées, mais elles
    sont posées à côté du dépôt sur les postes de travail.
    """
    if explicit is not None:
        left, right = explicit
        if not left.is_file() or not right.is_file():
            raise SystemExit(f"Vidéos introuvables : {left} / {right}")
        return left, right

    repo = APP_DIR.parent
    # Les candidats priment sur l'emplacement : une vidéo de terrain posée à
    # côté du dépôt vaut mieux qu'une vidéo de mire présente dedans.
    for left_rel, right_rel in DEMO_VIDEO_CANDIDATES:
        for root in (repo, repo.parent):
            left, right = root / left_rel, root / right_rel
            if left.is_file() and right.is_file():
                return left.resolve(), right.resolve()
    return None


def _write_video_list(data_root: Path, pair: tuple[Path, Path]) -> None:
    """Dépose la paire là où ``Sync.reloadSavedVideos()`` ira la chercher."""
    cam_dir = data_root / "camera_parameters"
    cam_dir.mkdir(parents=True, exist_ok=True)
    (cam_dir / "videos.txt").write_text(
        f"{pair[0]}\n{pair[1]}\n", encoding="utf-8"
    )


def _find_qml_objects(win, marker_property: str) -> list:
    """Objets QML exposant ``marker_property`` — repli quand l'objectName manque.

    Les panneaux repliables de l'application ne portent pas tous un nom : les
    reconnaître par une propriété qui leur est propre évite d'ajouter des
    ``objectName`` au code applicatif pour les seuls besoins de la capture.
    """
    from PySide6.QtCore import QObject

    found = []
    for obj in win.findChildren(QObject):
        meta = obj.metaObject()
        if meta.indexOfProperty(marker_property) >= 0:
            found.append(obj)
    return found


def _collect_geometry(win) -> list[dict]:
    """Position à l'écran de chaque élément porteur de texte.

    Les repères de ``annotate_screenshots.py`` étaient des coordonnées écrites
    à la main : au premier bouton déplacé, ils encadraient le vide. Les relever
    ici, dans la fenêtre qui vient d'être capturée, les rend exacts par
    construction et fait suivre l'annotation à chaque régénération.
    """
    from PySide6.QtCore import QPointF
    from PySide6.QtQuick import QQuickItem

    found: list[dict] = []
    for item in win.findChildren(QQuickItem):
        if not item.isVisible():
            continue
        width, height = item.width(), item.height()
        if width < 8 or height < 8:
            continue
        if item.metaObject().indexOfProperty("text") < 0:
            continue
        text = item.property("text")
        if not isinstance(text, str):
            continue
        text = text.strip()
        if not text or len(text) > 90:
            continue
        top_left = item.mapToScene(QPointF(0.0, 0.0))
        # Le nom de classe QML (« GhostButton_QMLTYPE_42 ») dit s'il s'agit
        # d'un contrôle ou du simple libellé posé à l'intérieur.
        kind = item.metaObject().className().split("_QMLTYPE_")[0]
        found.append({
            "text": text,
            "kind": kind,
            "x": round(top_left.x()),
            "y": round(top_left.y()),
            "w": round(width),
            "h": round(height),
        })
    return found


def _scroll_page(win, offset: float) -> None:
    """Fait défiler la zone défilable la plus haute de la page.

    Les pages Préférences et Exports sont plus hautes que la fenêtre : sans
    défilement, leur bas ne serait jamais capturé et le manuel ne pourrait pas
    désigner les boutons qui s'y trouvent.
    """
    best = None
    for item in _find_qml_objects(win, "contentY"):
        if not item.property("visible"):
            continue
        height = item.property("height") or 0
        content = item.property("contentHeight") or 0
        if content <= height:
            continue
        if best is None or height > (best.property("height") or 0):
            best = item
    if best is None:
        print("  [!] Aucune zone défilable trouvée", flush=True)
        return
    reachable = (best.property("contentHeight") or 0) - (best.property("height") or 0)
    best.setProperty("contentY", max(0.0, min(float(offset), float(reachable))))


def _close_popups(win) -> None:
    """Referme les dialogues ouverts — « opened » n'existe que sur un Popup."""
    from PySide6.QtCore import QMetaObject

    for obj in _find_qml_objects(win, "opened"):
        if obj.property("opened"):
            QMetaObject.invokeMethod(obj, "close")


def _collapse_consoles(win) -> None:
    """Replie les consoles de journal : elles mangent la moitié de la page."""
    for panel in _find_qml_objects(win, "logModel"):
        panel.setProperty("expanded", False)


def _show_only_sections(win, titles: tuple[str, ...]) -> None:
    """Déplie les sections citées et replie les autres.

    Une section dépliée à la fois : c'est ce qui permet au manuel de montrer un
    panneau entier sans que la capture soit tronquée. Un titre absent de la
    fenêtre est signalé plutôt qu'ignoré, sans quoi un libellé renommé
    produirait en silence une capture entièrement repliée.
    """
    wanted = set(titles)
    seen = set()
    # « collapsible » identifie SidePanelSection, le composant des panneaux
    # latéraux ; CollapsibleCard, lui, porte « subtitle ».
    for card in _find_qml_objects(win, "collapsible") + _find_qml_objects(win, "subtitle"):
        title = card.property("title")
        if isinstance(title, str) and title:
            seen.add(title)
            card.setProperty("expanded", title in wanted)
    missing = wanted - seen
    if missing:
        print(f"  [!] Section(s) introuvable(s) : {', '.join(sorted(missing))}",
              flush=True)


def _load_capture_fonts(app) -> tuple[str, str]:
    """Rend les polices disponibles même avec le greffon Qt ``offscreen``.

    Sous Windows, ce greffon peut exposer une base de polices vide. Qt dessine
    alors chaque glyphe sous forme de carré. Enregistrer explicitement les
    fichiers Segoe UI et Consolas garde une capture réellement hors écran tout
    en produisant le même rendu typographique que l'application normale.
    """
    from PySide6.QtGui import QFont, QFontDatabase

    if os.name == "nt":
        windows_dir = Path(os.environ.get("WINDIR", r"C:\Windows"))
        font_dir = windows_dir / "Fonts"
        for filename in (
            "segoeui.ttf",
            "segoeuib.ttf",
            "segoeuil.ttf",
            "seguisym.ttf",
            "seguiemj.ttf",
            "consola.ttf",
            "consolab.ttf",
        ):
            path = font_dir / filename
            if path.is_file():
                QFontDatabase.addApplicationFont(str(path))

    families = set(QFontDatabase.families())
    sans = next(
        (name for name in ("Segoe UI", "Arial", "DejaVu Sans") if name in families),
        "",
    )
    mono = next(
        (name for name in ("Consolas", "Courier New", "DejaVu Sans Mono") if name in families),
        "",
    )
    if not sans or not mono:
        raise RuntimeError(
            "Qt ne voit pas de police utilisable pour la capture "
            f"(familles détectées : {len(families)})."
        )

    app.setFont(QFont(sans))
    print(f"Polices de capture : {sans} / {mono}", flush=True)
    return sans, mono


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frame", type=int, default=10330)
    parser.add_argument("--calibration-dir", type=Path)
    parser.add_argument("--source-db", type=Path,
                        help="copie une base existante dans la racine isolée")
    parser.add_argument("--session-id", default=DEMO_SESSION_ID)
    parser.add_argument("--calibration-videos", nargs=2, type=Path)
    parser.add_argument("--only-measure", action="store_true")
    parser.add_argument("--only", nargs="+", help="noms de captures à refaire, dans l'ordre du parcours")
    parser.add_argument("--width", type=int, default=WIN_W)
    parser.add_argument("--height", type=int, default=WIN_H)
    parser.add_argument("--output-dir", type=Path, default=OUT_DIR)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--data-root",
        type=Path,
        help="racine isolée persistante à utiliser pour la capture",
    )
    mode.add_argument(
        "--live-data",
        action="store_true",
        help="utilise les données configurées sur ce poste (déconseillé)",
    )
    parser.add_argument(
        "--onscreen",
        action="store_true",
        help="affiche la fenêtre au lieu d'utiliser la plate-forme Qt offscreen",
    )
    parser.add_argument(
        "--videos",
        nargs=2,
        type=Path,
        metavar=("GAUCHE", "DROITE"),
        help="paire de vidéos à charger pour la capture (sinon : détection auto)",
    )
    parser.add_argument(
        "--no-videos",
        action="store_true",
        help="capture les pages vidéo à vide, sans charger de paire",
    )
    return parser.parse_args()


def _configure_isolated_data(root: Path) -> None:
    """Déroute base, réglages et racine écrite vers ``root``."""
    root = root.resolve()
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
    """Copie le module QML source dans un dossier temporaire importable."""
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


def _seed_demo_database(
    db_path: Path, video_pair: tuple[Path, Path] | None = None
) -> None:
    """Crée une identité, deux sessions et quatre pistes de démonstration.

    Quand une paire de vidéos est fournie, la session de démonstration pointe
    dessus : la page Sessions montre alors des fichiers réellement accessibles
    au lieu d'un avertissement « introuvable sur ce disque ».
    """
    from sqlalchemy import select

    from src.annodb.annotators import create_annotator
    from src.annodb.connection import init_db, session_scope
    from src.annodb.models import (
        Annotator,
        CaptureSession,
        MediaAsset,
        Project,
        SpatialAnnotation,
        TaxonNode,
        Track,
        TrackSample,
    )

    init_db(db_path)
    try:
        # Sans la taxonomie complète, les listes Famille / Genre / Espèce des
        # captures seraient réduites aux quelques rangs créés par `init_db`.
        import fish_annotate as fa

        fa.ensure_fishial_taxonomy_if_needed()
    except Exception as exc:  # la capture reste possible sans référentiel
        print(f"[!] Taxonomie Fishial non chargée : {exc}", flush=True)

    left_path = str(video_pair[0]) if video_pair else "D:/campagne_2026/recif_nord_gauche.mp4"
    right_path = str(video_pair[1]) if video_pair else "D:/campagne_2026/recif_nord_droite.mp4"
    box = json.dumps(
        {"x_min": 420, "y_min": 250, "x_max": 610, "y_max": 390},
        ensure_ascii=False,
    )
    with session_scope(db_path) as session:
        if session.scalar(select(Annotator).limit(1)) is None:
            # Une seule identité suffit : current_annotator() la choisit sans
            # écrire de réglage global, ce qui garde la capture hermétique.
            create_annotator(session, display_name="Camille Martin")

        # Le registre ne lit qu'un seul projet, celui de `fish_annotate`. Un
        # média rangé sous le projet « default » créé par `init_db` resterait
        # invisible dans Registre, Explorateur et Exports.
        import fish_annotate as fa

        project = session.scalar(
            select(Project).where(Project.name == fa.REGISTRY_PROJECT)
        )
        if project is None:
            project = Project(id="docs-project", name=fa.REGISTRY_PROJECT)
            session.add(project)
            session.flush()

        left = session.get(MediaAsset, DEMO_MEDIA_ID)
        if left is None:
            left = MediaAsset(
                id=DEMO_MEDIA_ID,
                project_id=project.id,
                media_type="video",
                rel_path=left_path,
                width=1920,
                height=1080,
                fps=30.0,
                frame_count=9000,
                site="Récif Nord",
                session_title="Transect du matin",
                session_date=datetime(2026, 8, 20, 8, 30),
            )
            session.add(left)
        right = session.get(MediaAsset, "docs-media-right")
        if right is None:
            right = MediaAsset(
                id="docs-media-right",
                project_id=project.id,
                media_type="video",
                rel_path=right_path,
                width=1920,
                height=1080,
                fps=30.0,
                frame_count=9000,
                site="Récif Nord",
                session_title="Transect du matin",
                session_date=datetime(2026, 8, 20, 8, 30),
            )
            session.add(right)
        session.flush()

        if session.get(CaptureSession, "docs-session-planned") is None:
            session.add(CaptureSession(
                id="docs-session-planned",
                name="Passe de Toliara — après-midi",
                site="Passe de Toliara",
                session_date=datetime(2026, 8, 22, 14, 0),
                operator="Camille Martin",
                status="planned",
                notes="Météo à confirmer",
            ))
        if session.get(CaptureSession, "docs-session-active") is None:
            session.add(CaptureSession(
                id="docs-session-active",
                name="Transect du matin",
                site="Récif Nord",
                session_date=datetime(2026, 8, 20, 8, 30),
                operator="Camille Martin",
                status="active",
                left_media_id=left.id,
                right_media_id=right.id,
                frame_offset=7,
                calibration_profile="fast_v2",
                calibration_sha256="5e2a3417documentation",
                notes="Visibilité correcte",
            ))

        taxon = session.scalar(
            select(TaxonNode).where(TaxonNode.rank == "species").limit(1)
        )
        species = list(session.scalars(
            select(TaxonNode)
            .where(TaxonNode.rank == "species")
            .order_by(TaxonNode.scientific_name)
            .limit(4)
        ))
        tracks = [
            ("docs-track-1", 9012, [120], None),
            ("docs-track-2", 9018, [300, 302, 304, 2105], None),
            ("docs-track-3", 9027, list(range(520, 561, 2)), taxon.id if taxon else None),
            ("docs-track-4", 9031, [700, 702, 704, 900, 902], None),
        ]
        for track_id, external_id, frames, taxon_id in tracks:
            if session.get(Track, track_id) is not None:
                continue
            session.add(Track(
                id=track_id,
                media_id=left.id,
                external_track_id=external_id,
                taxon_node_id=taxon_id,
                first_frame=min(frames),
                last_frame=max(frames),
                source="bytetrack",
            ))
            session.flush()
            for frame in frames:
                session.add(TrackSample(
                    track_id=track_id,
                    frame_index=frame,
                    cx=515.0,
                    cy=320.0,
                    bbox_json=box,
                    origin="auto",
                ))

        # Quelques observations : sans elles, le Registre, l'Explorateur et les
        # Exports se capturent vides et n'illustrent aucun des gestes decrits.
        # La derniere reste sans taxon : c'est l'etat « Non relu » du manuel.
        demo_obs = [
            ("docs-obs-1", 512, 214.0, 0),
            ("docs-obs-2", 833, 187.5, 1),
            ("docs-obs-3", 1204, 305.0, 2),
            ("docs-obs-4", 1610, 168.0, 3),
            ("docs-obs-5", 2042, None, 0),
            ("docs-obs-6", 2455, None, None),
        ]
        for ann_id, frame, length_mm, taxon_rank in demo_obs:
            if session.get(SpatialAnnotation, ann_id) is not None:
                continue
            node = None
            if taxon_rank is not None and taxon_rank < len(species):
                node = species[taxon_rank]
            session.add(SpatialAnnotation(
                id=ann_id,
                media_id=left.id,
                frame_index=frame,
                frame_ref="absolute",
                geom_type="bbox",
                geometry_json=box,
                taxon_node_id=node.id if node is not None else None,
                measurement_mm=length_mm,
                confidence=0.86,
                author="Camille Martin",
                source="manual" if node is not None else "model",
            ))


def _dispose_isolated_database() -> None:
    """Ferme le pool SQLite pour que le dossier temporaire soit supprimable."""
    try:
        from src.annodb import connection

        if connection._engine is not None:  # noqa: SLF001
            connection._engine.dispose()  # noqa: SLF001
            connection._engine = None  # noqa: SLF001
            connection._SessionLocal = None  # noqa: SLF001
    except Exception as exc:  # nettoyage best-effort à la fermeture
        print(f"[!] Fermeture de la base temporaire : {exc}", flush=True)


def main() -> int:
    global OUT_DIR, DEMO_MEDIA_ID, DEMO_SESSION_ID
    args = _arguments()
    OUT_DIR = args.output_dir.resolve()
    if args.source_db and args.live_data:
        raise SystemExit("--source-db exige une copie isolée, sans --live-data")
    DEMO_SESSION_ID = args.session_id

    if not args.onscreen:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        os.environ.setdefault("QT_QUICK_BACKEND", "software")
        os.environ.setdefault("QSG_RHI_BACKEND", "software")

    with ExitStack() as stack:
        if args.live_data:
            data_root = None
            print("[!] Capture avec les données actives du poste", flush=True)
        elif args.data_root is not None:
            data_root = args.data_root.resolve()
            _configure_isolated_data(data_root)
            print(f"Base isolée persistante : {data_root}", flush=True)
        else:
            data_root = Path(stack.enter_context(
                tempfile.TemporaryDirectory(
                    prefix="aquameasure-docs-data-", ignore_cleanup_errors=True
                )
            ))
            _configure_isolated_data(data_root)
            print(f"Base isolée temporaire : {data_root}", flush=True)

        qml_work = Path(stack.enter_context(
            tempfile.TemporaryDirectory(
                prefix="aquameasure-docs-qml-", ignore_cleanup_errors=True
            )
        ))

        if args.calibration_dir and data_root is not None:
            # Les coefficients suffisent ; les images de vérification peuvent
            # peser plusieurs Go et ne servent pas aux captures de l'interface.
            shutil.copytree(args.calibration_dir, data_root / "camera_parameters",
                            dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns("verify", "*.png", "*.jpg", "*.mp4", "*.avi"))
        app_dir = am._setup_paths()
        qml_root = _prepare_isolated_qml(qml_work)

        video_pair = None
        if not args.no_videos:
            explicit = tuple(args.videos) if args.videos else None
            video_pair = _demo_video_pair(explicit)
            if video_pair is None:
                print("[!] Aucune paire de vidéos trouvée : pages vidéo vides",
                      flush=True)
            elif data_root is not None:
                _write_video_list(data_root, video_pair)
                print(f"Vidéos de capture : {video_pair[0].name} / "
                      f"{video_pair[1].name}", flush=True)

        if data_root is not None and args.source_db:
            import sqlite3
            source_db = args.source_db.resolve(strict=True)
            target_db = data_root / "fish_annotations.db"
            if target_db.resolve() == source_db:
                raise SystemExit("La base de capture doit différer de la source")
            with sqlite3.connect(source_db.as_uri() + "?mode=ro", uri=True) as src:
                with sqlite3.connect(target_db) as dst:
                    src.backup(dst)
                    row = dst.execute("SELECT left_media_id FROM sessions WHERE id=?",
                                      (DEMO_SESSION_ID,)).fetchone()
                    if not row:
                        raise SystemExit("Session absente de la copie")
                    DEMO_MEDIA_ID = row[0]
            print("Session copiée pour les captures ; la source reste en lecture seule.", flush=True)
        elif data_root is not None:
            _seed_demo_database(data_root / "fish_annotations.db", video_pair)

        from PySide6.QtCore import QTimer, QUrl
        from PySide6.QtQml import QQmlApplicationEngine
        from PySide6.QtQuickControls2 import QQuickStyle
        from PySide6.QtWidgets import QApplication

        from src.controllers.app_controller import AppController
        from src.imaging.frame_image_provider import FrameImageProvider

        QApplication.setOrganizationName("IRD")
        QApplication.setOrganizationDomain("ird.fr")
        QApplication.setApplicationName("AquaMeasure")

        app = QApplication(sys.argv[:1])
        QQuickStyle.setStyle("Basic")
        sans_font, mono_font = _load_capture_fonts(app)

        app_ctrl = AppController()
        images = FrameImageProvider()
        app_ctrl.sync().set_image_provider(images)
        app_ctrl.calibration().set_image_provider(images)
        app_ctrl.measure().set_image_provider(images)

        # La page Pistes a besoin d'un média courant. Cette affectation ne sert
        # qu'à la base de démonstration et ne modifie aucun média réel.
        if data_root is not None:
            app_ctrl.data()._media_id = DEMO_MEDIA_ID  # noqa: SLF001
            app_ctrl.data().mediaIdChanged.emit()

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

        logo = app_dir / "resources" / "logo_ird.png"
        ctx.setContextProperty("AppLogoUrl", QUrl.fromLocalFile(str(logo.resolve())))
        ctx.setContextProperty(
            "AppSansFont",
            sans_font,
        )
        ctx.setContextProperty(
            "AppMonoFont",
            mono_font,
        )

        engine.load(QUrl.fromLocalFile(str((qml_root / "Main.qml").resolve())))
        roots = engine.rootObjects()
        if not roots:
            print("Échec du chargement QML")
            return 1

        win = roots[0]
        # Taille fixe : captures homogènes d'une machine à l'autre. La fenêtre
        # reste rendue même avec QT_QPA_PLATFORM=offscreen.
        win.setProperty("visibility", 2)  # Windowed
        win.setWidth(args.width)
        win.setHeight(args.height)
        win.setX(40)
        win.setY(40)
        if args.onscreen:
            # Éviter qu'une infobulle apparaisse sous la position de souris
            # laissée par l'utilisateur pendant une capture automatique.
            from PySide6.QtCore import QPoint
            from PySide6.QtGui import QCursor
            QCursor.setPos(win.mapToGlobal(QPoint(20, 20)))

        OUT_DIR.mkdir(parents=True, exist_ok=True)

        def show_sessions() -> None:
            app_ctrl.sessions().refresh()
            for row in range(app_ctrl.sessions().sessionCount):
                app_ctrl.sessions().selectSession(row)
                if app_ctrl.sessions().selectedId == DEMO_SESSION_ID:
                    break

        # Le tout début d'une vidéo sous-marine est souvent noir : les aperçus
        # sont capturés plus loin, là où la scène est réellement lisible.
        DEMO_FRAME = args.frame
        demo_context = {"reference_track": None, "repairs": []}

        def load_sync_videos() -> None:
            app_ctrl.sync().reloadSavedVideos()
            app_ctrl.sync().seekLeft(DEMO_FRAME)
            app_ctrl.sync().seekRight(DEMO_FRAME)

        def load_calib_videos() -> None:
            if args.calibration_videos and data_root is not None:
                pair = tuple(path.resolve(strict=True) for path in args.calibration_videos)
                _write_video_list(data_root, pair)
                app_ctrl.sync().reloadSavedVideos()
            app_ctrl.calibration().loadVideosFromSync()

        def load_measure_videos() -> None:
            if video_pair and data_root is not None:
                _write_video_list(data_root, video_pair)
                load_sync_videos()
            app_ctrl.measure().loadVideosFromSync()
            app_ctrl.measure().seekFromLeftAbsFrame(DEMO_FRAME)
            if data_root is not None:
                import fish_annotate as fa
                from src.annodb.connection import session_scope
                from src.annodb.models import CaptureSession
                left_id = fa.resolve_media_id(app_ctrl.measure().leftVideo, create=True)
                right_id = fa.resolve_media_id(app_ctrl.measure().rightVideo, create=True)
                with session_scope() as session:
                    capture = session.get(CaptureSession, DEMO_SESSION_ID)
                    capture.left_media_id, capture.right_media_id = left_id, right_id
                app_ctrl.data().loadSessionMetadata()
                sessions = app_ctrl.sessions()
                sessions.refresh()
                for row in range(sessions.sessionCount):
                    sessions.selectSession(row)
                    if sessions.selectedId == DEMO_SESSION_ID:
                        break

        def select_demo_fish() -> None:
            fish = app_ctrl.fish()
            app_ctrl.data()._set_session_id(DEMO_SESSION_ID)
            if not fish.lastBoxCount:
                raise RuntimeError("Aucun poisson détecté pour la capture de fiche")
            boxes = [fish.lastBoxAtIndex(i) for i in range(fish.lastBoxCount)]
            index = max(range(len(boxes)), key=lambda i: (boxes[i]["x2"]-boxes[i]["x1"]) * (boxes[i]["y2"]-boxes[i]["y1"]))
            if args.source_db:
                # Retrouver un vrai poisson déjà enregistré à cette image,
                # plutôt que la plus grande boîte (parfois le tuyau du bassin).
                import sqlite3
                from src.controllers.fish_controller import _box_iou
                with sqlite3.connect(args.source_db.resolve().as_uri() + "?mode=ro", uri=True) as source:
                    row = source.execute(
                        "SELECT geometry_json, track_id, id FROM spatial_annotations "
                        "WHERE media_id=? AND frame_index=? ORDER BY created_at LIMIT 1",
                        (DEMO_MEDIA_ID, DEMO_FRAME)).fetchone()
                if not row:
                    raise RuntimeError("Choisir --frame sur une observation suivie pour cadrer le tutoriel")
                demo_context["reference_track"] = row[1]
                demo_context["reference_observation"] = row[2]
                coords = fish._geometry_bbox_pixels(json.loads(row[0]))
                if coords is None:
                    raise RuntimeError("Le cadre enregistré ne peut pas être affiché")
                target = dict(zip(("x1", "y1", "x2", "y2"), coords))
                if not demo_context["reference_track"]:
                    with sqlite3.connect(args.source_db.resolve().as_uri() + "?mode=ro", uri=True) as source:
                        candidates = source.execute(
                            "SELECT s.track_id, s.bbox_json FROM track_samples s "
                            "JOIN tracks t ON t.id=s.track_id WHERE t.media_id=? AND s.frame_index=?",
                            (DEMO_MEDIA_ID, DEMO_FRAME)).fetchall()
                    matches = []
                    for track_id, bbox_json in candidates:
                        old = json.loads(bbox_json)
                        box = {key: old.get(key, old.get(alias)) for key, alias in
                               (("x1", "x_min"), ("y1", "y_min"), ("x2", "x_max"), ("y2", "y_max"))}
                        if all(value is not None for value in box.values()):
                            matches.append((_box_iou(box, target), track_id))
                    if matches and max(matches)[0] > 0.5:
                        demo_context["reference_track"] = max(matches)[1]
                index = max(range(len(boxes)), key=lambda i: _box_iou(boxes[i], target))
                if _box_iou(boxes[index], target) < 0.35:
                    raise RuntimeError("Le poisson du tutoriel n'est pas localisé par la détection")
            app_ctrl.data().selectBoxExplicit(index)
            if args.source_db:
                # Ouvrir la fiche existante avec sa longueur déjà enregistrée.
                # Une nouvelle mesure automatique pourrait échouer sur cette
                # image ; la documentation ne doit pas fabriquer de résultat.
                if not app_ctrl.data().focusObservationById(demo_context["reference_observation"]):
                    raise RuntimeError("La fiche de référence n'est pas dans le registre")
            box = boxes[index]
            def zoom(item):
                if item.metaObject().indexOfProperty("viewZoom") >= 0 and item.property("isLeft"):
                    from PySide6.QtCore import QMetaObject
                    item.setProperty("viewZoom", 2.5)
                    scale = item.property("_viewScale")
                    item.setProperty("panX", (item.property("frameWidth") / 2 - (box["x1"] + box["x2"]) / 2) * scale)
                    item.setProperty("panY", (item.property("frameHeight") / 2 - (box["y1"] + box["y2"]) / 2) * scale)
                    QMetaObject.invokeMethod(item, "clampPan")
                for child in item.childItems():
                    zoom(child)
            zoom(win.contentItem())
            if not args.source_db:
                fish.measureSelectedBoxLength()

        def sub_tab(index: int):
            def apply() -> None:
                # La page Mesure a redéfini le média courant en chargeant la
                # paire : le rétablir avant de relire la base, sans quoi les
                # sous-onglets se capturent vides.
                if data_root is not None:
                    show_sessions()
                    app_ctrl.data()._media_id = DEMO_MEDIA_ID  # noqa: SLF001
                    app_ctrl.data().mediaIdChanged.emit()
                app_ctrl.data().subTab = index
                app_ctrl.data().refreshAll()
            return apply

        def save_demo_fish():
            data = app_ctrl.data()
            data.saveFish(data.editFamily, data.editGenus, data.editSpecies)
            # Option réelle de l'application : masquer les propositions IA
            # pour que les vues de suivi montrent clairement le seul poisson.
            app_ctrl.fish().fishIaEnabled = False

        def choose_follow_mode(mode, span=0):
            from PySide6.QtCore import Q_ARG, QMetaObject, QObject
            block = win.findChild(QObject, "trackFollowBlock")
            QMetaObject.invokeMethod(block.findChild(QObject, "followModeTabs"), "chosen", Q_ARG(int, mode))
            QMetaObject.invokeMethod(block.findChild(QObject, "behaviorSpanTabs"), "chosen", Q_ARG(int, span))

        def show_point_only():
            choose_follow_mode(0)
            pecks = app_ctrl.pecks()
            pecks.loadPointTypes()
            key = next(row["key"] for row in pecks.pointTypes if row["label"] == "Bouchée")
            pecks.setTypeKey(key)

        def show_trajectory_start():
            choose_follow_mode(1)
            app_ctrl.fish().autoOnPause = False

        def mark_follow_start():
            app_ctrl.fish().beginTrackFollow()
            if not app_ctrl.fish().trackFollowStartMarked:
                raise RuntimeError("In non mémorisé sur le poisson")

        def start_demo_follow():
            from PySide6.QtTest import QTest
            fish = app_ctrl.fish()
            fish.autoOnPause = False
            if not fish.trackFollowStartMarked:
                fish.beginTrackFollow()
            app_ctrl.measure().seekFromLeftAbsFrame(DEMO_FRAME + 2)
            QTest.qWait(1000)
            fish.finishTrackFollow()

        def mark_demo_track():
            from PySide6.QtTest import QTest
            import fish_annotate as fa
            pecks = app_ctrl.pecks()
            track = app_ctrl.data().selectedTrackDbId
            if not track:
                raise RuntimeError("Le suivi de démonstration n'a pas produit de piste")
            behavior = next((row for row in fa.list_point_event_types()
                             if row["label"] == "Bouchée"), None)
            if behavior is None:
                behavior = fa.create_behavior_type("Bouchée", "point", "●")
            pecks.refresh()
            pecks.loadPointTypes()
            pecks.setTypeKey(behavior["key"])
            pecks.selectTrack(track)
            for frame in (DEMO_FRAME, DEMO_FRAME + 1, DEMO_FRAME + 2):
                app_ctrl.measure().seekFromLeftAbsFrame(frame)
                QTest.qWait(600)
                if not pecks.markAtCurrentFrame():
                    raise RuntimeError(pecks.statusText)

        def mark_demo_duration():
            fish = app_ctrl.fish()
            choose_follow_mode(0, 1)
            app_ctrl.measure().seekFromLeftAbsFrame(DEMO_FRAME)
            if not fish.beginBehaviorFollow("grazing"):
                raise RuntimeError(fish.statusText)
            app_ctrl.measure().seekFromLeftAbsFrame(DEMO_FRAME + 2)
            fish.finishTrackFollow()
            if fish.grazingWorkflowState != "success":
                raise RuntimeError(fish.grazingWorkflowMessage)

        def panel(*titles: str):
            """Replie les consoles et n'ouvre que les sections citées."""
            def apply() -> None:
                _close_popups(win)
                _collapse_consoles(win)
                _show_only_sections(win, titles)
                if app_ctrl.currentPage == 4 and any(t in ("Session", "Vidéos", "Détection IA", "Affichage") for t in titles):
                    from PySide6.QtCore import QObject, QMetaObject
                    drawer = win.findChild(QObject, "measurementSettingsDrawer")
                    if drawer is not None:
                        QMetaObject.invokeMethod(drawer, "open")
            return apply

        def show_track_events() -> None:
            panel("Suivi et annotations")()
            from PySide6.QtCore import QObject
            for flick in win.findChildren(QObject, "fishWorkflowFlick"):
                if flick.property("visible"):
                    flick.setProperty("contentY", max(0.0,
                        flick.property("contentHeight") - flick.property("height")))

        def tidy() -> None:
            _close_popups(win)
            _collapse_consoles(win)

        def show_export():
            from PySide6.QtCore import QObject, QPointF
            from PySide6.QtQuick import QQuickItem
            # Le résumé peut contenir beaucoup d'espèces. Descendre uniquement
            # sa colonne jusqu'au bloc Export, sans déplacer le registre.
            view = win.findChild(QObject, "dataExportScrollView")
            item = view.property("contentItem") if view is not None else None
            selector = win.findChild(QQuickItem, "sessionExportFormatSelector")
            if item is None or selector is None:
                raise RuntimeError("Le panneau d'export n'est pas disponible")
            y = selector.mapToItem(item, QPointF(0, 0)).y()
            maximum = max(0.0, item.property("contentHeight") - item.property("height"))
            item.setProperty("contentY", min(maximum, max(0.0, item.property("contentY") + y - 70)))
            print(f"  Export : défilement {item.property('contentY'):.0f}/{maximum:.0f}", flush=True)

        def detector_extend():
            dialog("openDetectorManager")()
            for popup in _find_qml_objects(win, "tab"):
                if popup.metaObject().className().startswith("DetectorManagerDialog"):
                    popup.setProperty("tab", 2)

        def show_behavior_settings():
            # Se fonder sur le titre réellement rendu pour viser la carte.
            from PySide6.QtCore import QPointF
            from PySide6.QtQuick import QQuickItem
            for item in win.findChildren(QQuickItem):
                if item.isVisible() and item.property("text") == "Comportements":
                    parent = item.parentItem()
                    while parent is not None:
                        if parent.metaObject().indexOfProperty("contentY") >= 0:
                            y = item.mapToItem(parent, QPointF(0, 0)).y()
                            parent.setProperty("contentY", max(0, parent.property("contentY") + y - 20))
                            return
                        parent = parent.parentItem()

        def dialog(method: str, *args):
            """Ouvre un dialogue déclaré comme fonction de ``Main.qml``."""
            from PySide6.QtCore import Q_ARG, QMetaObject, Qt

            def apply() -> None:
                _close_popups(win)
                packed = [Q_ARG("QVariant", value) for value in args]
                QMetaObject.invokeMethod(
                    win, method, Qt.ConnectionType.DirectConnection, *packed
                )
            return apply

        # (page, nom de fichier, action avant l'affichage, réglage après l'affichage)
        shots: list[tuple[int, str, object, object]] = [
            (0, "01-accueil", None, None),
            (1, "02-machine", None, tidy),

            (2, "03-sync-videos", load_sync_videos, panel("Vidéos")),
            (2, "03b-sync-flash", None, panel("Détection flash (auto)")),
            (2, "03c-sync-manuelle", None, panel("Synchronisation manuelle")),
            (2, "03d-sync-trim", None, panel("Découpe vidéo (trim)")),

            (3, "04-calibration-videos", load_calib_videos,
             panel("Vidéos de la mire")),
            (3, "04b-calibration-reglages", None, panel("Calibration")),

            (4, "05-mesure-session", load_measure_videos, panel("Session")),
            (4, "05b-mesure-videos", None, panel("Vidéos")),
            (4, "05c-mesure-detection", None, panel("Détection IA")),
            (4, "05f-poisson-selectionne", select_demo_fish, tidy),
            (4, "05g-poisson-enregistre", save_demo_fish, tidy),
            (4, "05k-point-ponctuel", show_point_only, show_track_events),
            (4, "05l-trajectoire-depart", show_trajectory_start, tidy),
            (4, "05m-trajectoire-in", mark_follow_start, tidy),
            (4, "05h-suivi-piste", start_demo_follow, panel("Suivi et annotations")),
            (4, "05i-bouchees-piste", mark_demo_track, show_track_events),
            (4, "05j-broutage-et-bouchees", mark_demo_duration, show_track_events),
            # Commandes et événements enregistrés dans le panneau du poisson.
            (4, "05d-mesure-comportement", None, panel("Suivi et annotations")),
            (4, "05e-mesure-affichage", None, panel("Affichage")),

            # « Données & IA » ne compte plus que deux sous-onglets : Session
            # (registre à gauche, résumé et export à droite) et Fishial. Les
            # onglets Explorateur, Pistes et Exports ont disparu, avec les
            # captures qui les montraient.
            (5, "06-registre", sub_tab(0), None),
            (5, "06c-exports", None, show_export),
            (5, "06c2-exports-galerie", sub_tab(1), None),

            (6, "07-hub-pro", lambda: setattr(app_ctrl.settings(), "proMode", True),
             tidy),
            (7, "08-sessions", show_sessions, None),
            (8, "09-parametres", lambda: app_ctrl.storage().refresh(), None),
            (8, "09b-parametres-sauvegarde", None, lambda: _scroll_page(win, 620)),
            (8, "09c-types-comportements", None, show_behavior_settings),

            # Dialogues, capturés en dernier : chacun referme le précédent.
            (7, "10-annotateur", None, dialog("openAnnotatorDialog", True)),
            (4, "11-modeles-detection", None, dialog("openDetectorManager")),
            (4, "11b-ajout-modeles", None, detector_extend),
            (3, "12-mire-charuco", load_calib_videos, dialog("openCharucoSettingsDialog")),
            (3, "13-reglages-calibration", None,
             dialog("openCalibScanSettingsDialog")),
        ]

        state = {"i": 0, "failed": False}
        if data_root is None:
            # Ces vues créent volontairement une observation et des événements.
            # Elles sont réservées à la base isolée de démonstration.
            shots = [shot for shot in shots if shot[1] not in {
                "05f-poisson-selectionne", "05g-poisson-enregistre",
                "05h-suivi-piste", "05i-bouchees-piste",
                "05j-broutage-et-bouchees", "05k-point-ponctuel",
                "05l-trajectoire-depart", "05m-trajectoire-in",
            }]
        if args.only_measure:
            shots = [shot for shot in shots if shot[0] == 4]
        if args.only:
            unknown = set(args.only) - {shot[1] for shot in shots}
            if unknown:
                raise RuntimeError("Captures inconnues : " + ", ".join(sorted(unknown)))
            shots = [shot for shot in shots if shot[1] in args.only]

        geometry: dict[str, list[dict]] = {}

        def grab(name: str) -> None:
            img = win.grabWindow()
            # Les captures natives Windows des lecteurs sont livrées en JPEG.
            extension = ".jpg" if name.startswith("03") else ".png"
            path = OUT_DIR / f"{name}{extension}"
            if img.isNull() or not img.save(str(path)):
                state["failed"] = True
                print(f"  [!] Capture impossible : {path.name}", flush=True)
                return
            geometry[name] = _collect_geometry(win)
            from PySide6.QtCore import QEventLoop, QSize
            from PySide6.QtQuick import QQuickItem
            item = None
            follow_detail = name in {"05k-point-ponctuel", "05l-trajectoire-depart",
                                     "05m-trajectoire-in", "05h-suivi-piste",
                                     "05i-bouchees-piste", "05j-broutage-et-bouchees"}
            if follow_detail:
                item = win.findChild(QQuickItem, "trackFollowBlock")
                if item is not None and item.isVisible():
                    # Inclure la carte qui porte le titre et le fond : un
                    # Item nu donne du texte clair sur un PNG transparent.
                    while item.parentItem() is not None and item.property("title") != "Suivi et annotations":
                        item = item.parentItem()
            elif name == "11b-ajout-modeles":
                item = next((candidate for candidate in win.findChildren(QQuickItem)
                             if candidate.isVisible() and candidate.property("title") == "Ajouter des poids depuis le disque"), None)
            elif name in {"11-modeles-detection", "12-mire-charuco", "13-reglages-calibration"}:
                item = next((candidate for candidate in win.findChildren(QQuickItem)
                             if "PopupItem" in candidate.metaObject().className()
                             and candidate.isVisible() and candidate.width() > 200), None)
            elif name == "09c-types-comportements":
                item = next((candidate for candidate in win.findChildren(QQuickItem)
                             if candidate.isVisible() and candidate.property("title") == "Comportements"), None)
            elif name == "06c2-exports-galerie":
                item = next((candidate for candidate in win.findChildren(QQuickItem)
                             if candidate.isVisible() and candidate.property("title") == "Bibliothèque Fishial locale"), None)
            detail_viewport = None
            if item is not None and name == "06c2-exports-galerie":
                from PySide6.QtQml import QQmlExpression, qmlContext
                from PySide6.QtTest import QTest
                # Le plein écran conserve toute la liste visible. Le gros plan
                # du manuel montre les premières lignes dans un viewport court,
                # pour garder les boutons lisibles sur la page Word.
                listing = win.findChild(QQuickItem, "fishialLibraryList")
                if listing is not None:
                    context = qmlContext(listing)
                    detail_viewport = (context, listing, listing.height())
                    expression = QQmlExpression(context, listing,
                        "Layout.preferredHeight = itemAtIndex(0) ? "
                        "Math.min(contentHeight, itemAtIndex(0).height * 3 + spacing * 2) : 80")
                    expression.evaluate()
                    if expression.hasError():
                        raise RuntimeError(expression.error().toString())
                    QTest.qWait(80)
            if item is not None and item.isVisible():
                result = item.grabToImage(QSize(round(item.width() * 2), round(item.height() * 2)))
                loop = QEventLoop()
                result.ready.connect(loop.quit)
                QTimer.singleShot(5000, loop.quit)
                loop.exec()
                if result.image().isNull() or not result.saveToFile(str(OUT_DIR / (name + "-detail.png"))):
                    raise RuntimeError("Capture détaillée impossible : " + name)
            if detail_viewport is not None:
                context, listing, height = detail_viewport
                QQmlExpression(context, listing, f"Layout.preferredHeight = {height}").evaluate()
            print(f"  -> {path.name}  ({img.width()}x{img.height()}) · "
                  f"{len(geometry[name])} élément(s) repérés", flush=True)

        def resume_from_reference() -> bool:
            """Reproduit le recadrage humain avec une position déjà en base.

            La copie isolée reçoit le vrai geste addManualBox ; les positions
            de référence ne sont jamais injectées dans le résultat du tracker.
            """
            fish = app_ctrl.fish()
            if not fish.assistWaiting or not demo_context["reference_track"]:
                return False
            import sqlite3
            frame = fish.followLostFrame
            if frame in demo_context["repairs"]:
                raise RuntimeError(f"Le suivi reste perdu à l'image {frame} après correction")
            with sqlite3.connect(args.source_db.resolve().as_uri() + "?mode=ro", uri=True) as source:
                row = source.execute(
                    "SELECT bbox_json FROM track_samples WHERE track_id=? AND frame_index=?",
                    (demo_context["reference_track"], frame)).fetchone()
            if row is None:
                raise RuntimeError(f"Aucun cadre de référence à l'image {frame}")
            box = json.loads(row[0])
            coords = tuple(box.get(key, box.get(alias)) for key, alias in
                           (("x1", "x_min"), ("y1", "y_min"), ("x2", "x_max"), ("y2", "y_max")))
            if any(value is None for value in coords):
                raise RuntimeError("Cadre de référence non reconnu")
            demo_context["repairs"].append(frame)
            fish.addManualBox(*coords)
            print(f"  Reprise du suivi par recadrage à l'image {frame}", flush=True)
            return True

        def step() -> None:
            if app_ctrl.fish().busy or app_ctrl.data().busy:
                QTimer.singleShot(500, step)
                return
            i = state["i"]
            if i >= len(shots):
                geo_path = OUT_DIR / "geometry.json"
                geo_path.write_text(
                    json.dumps(geometry, ensure_ascii=False, indent=1) + "\n",
                    encoding="utf-8",
                )
                print(f"Repères écrits dans {geo_path.name}", flush=True)
                print("Captures terminées.", flush=True)
                print(f"Bilan : {len(geometry)}/{len(shots)} captures, échec={state['failed']}", flush=True)
                (OUT_DIR / "capture-report.json").write_text(json.dumps({
                    "captured_at": datetime.now().isoformat(timespec="seconds"),
                    "captures": list(geometry), "failed": state["failed"],
                    "session_id": DEMO_SESSION_ID, "reference_frame": DEMO_FRAME,
                    "manual_tracking_repairs": demo_context["repairs"],
                    "isolated": data_root is not None,
                }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                app.quit()
                return
            page, name, action, tune = shots[i]
            print(f"[{i + 1}/{len(shots)}] page {page} -> {name}", flush=True)
            if action is not None:
                try:
                    action()
                except Exception as exc:
                    state["failed"] = True
                    print(f"  [!] Action échouée : {exc}", flush=True)
            app_ctrl.currentPage = page
            state["i"] = i + 1

            def settle() -> None:
                # Les réglages d'affichage (sections, consoles) attendent que la
                # page soit construite : ses Loader ne peuplent l'arbre d'objets
                # qu'une fois la page devenue courante.
                if tune is not None:
                    try:
                        tune()
                    except Exception as exc:
                        state["failed"] = True
                        print(f"  [!] Réglage échoué : {exc}", flush=True)
                def ready():
                    if app_ctrl.fish().busy or app_ctrl.data().busy:
                        QTimer.singleShot(500, ready)
                        return
                    if name == "05h-suivi-piste":
                        try:
                            if resume_from_reference():
                                QTimer.singleShot(1000, ready)
                                return
                            if not app_ctrl.data().selectedTrackDbId:
                                raise RuntimeError("Le suivi n'a pas créé de piste liée à la fiche")
                        except Exception as exc:
                            print(f"  [!] Suivi non validé : {exc}", flush=True)
                            state["failed"] = True
                            app.quit()
                            return
                    grab(name)
                    step()
                QTimer.singleShot(TUNE_MS, ready)

            QTimer.singleShot(SETTLE_MS, settle)

        QTimer.singleShot(1500, step)
        exit_code = app.exec()
        if data_root is not None:
            _dispose_isolated_database()
        if state["failed"]:
            return 1
        return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
