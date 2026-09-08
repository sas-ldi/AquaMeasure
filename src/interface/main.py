#!/usr/bin/env python3
"""Point d'entree AquaMeasure PySide6 + QML."""

from __future__ import annotations

import os
import sys
from pathlib import Path

# L'application utilise son runtime testé ; une inférence n'installe rien.
os.environ["YOLO_AUTOINSTALL"] = "false"


def _setup_paths() -> Path:
    if getattr(sys, "frozen", False):
        # Une distribution PyInstaller se compose de deux emplacements :
        # le dossier portable, a cote de l'exe, pour les donnees durables ;
        # et le bundle (_MEIPASS), pour les ressources internes.
        repo = Path(sys.executable).resolve().parent
        bundle = Path(getattr(sys, "_MEIPASS", repo)).resolve()
        app_candidates = (
            bundle / "interface",
            repo / "interface",
            bundle,
        )
        here = next(
            (candidate for candidate in app_candidates if (candidate / "qml").is_dir()),
            bundle / "interface",
        )
        fv_candidates = (repo / "annotations", bundle / "annotations")
        fv_roots = [candidate for candidate in fv_candidates if candidate.is_dir()]
    else:
        here = Path(__file__).resolve().parent
        repo = here.parent
        fv_roots = [repo / "annotations"]

    # Namespace 'src' partagé : interface/src + annotations/src (annodb).
    # Sans cela, fish_annotate.is_available() échoue après import des contrôleurs PySide.
    import types

    if "src" not in sys.modules:
        src_pkg = types.ModuleType("src")
        src_pkg.__path__ = [
            str(path)
            for path in (
                here / "src",
                *(fv_root / "src" for fv_root in fv_roots),
            )
            if path.is_dir()
        ]
        sys.modules["src"] = src_pkg

    if str(here) not in sys.path:
        sys.path.insert(0, str(here))
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))
    for fv_root in fv_roots:
        if str(fv_root) not in sys.path:
            sys.path.insert(0, str(fv_root))
    os.chdir(repo)
    return here


def _sync_tree(src: Path, dst: Path) -> None:
    """Recopie src -> dst en supprimant les fichiers dont la source a disparu.

    Sans la purge, un composant retiré de qml/ survivait indéfiniment dans le
    module généré : le projet compilait grâce à un fichier fantôme absent de la
    source (cas vécu avec PrimaryButton.qml).
    """
    import shutil

    if not src.is_dir():
        return
    dst.mkdir(parents=True, exist_ok=True)
    for item in src.iterdir():
        s, d = item, dst / item.name
        if item.is_dir():
            _sync_tree(s, d)
        else:
            shutil.copy2(s, d)

    for item in dst.iterdir():
        if item.name == "qmldir":
            continue
        counterpart = src / item.name
        if counterpart.exists():
            continue
        if item.is_dir():
            shutil.rmtree(item, ignore_errors=True)
        else:
            item.unlink(missing_ok=True)


def _prepare_qml_module(app_dir: Path) -> Path:
    qml_root = app_dir / "qml"
    module_dir = qml_root / "AquaMeasure"
    for sub in ("components", "pages", "style"):
        src = qml_root / sub
        dst = module_dir / sub
        _sync_tree(src, dst)
    from tools.gen_qmldir import write_qmldir

    write_qmldir(module_dir)
    return qml_root


def _pick_font_family(candidates: list[str], fallback: str) -> str:
    from PySide6.QtGui import QFontDatabase

    installed = set(QFontDatabase.families())
    for name in candidates:
        if name in installed:
            return name
    return fallback


def _set_application_icon(app, app_dir: Path) -> None:
    """Même logo IRD pour les fenêtres Qt et leur groupe dans la barre Windows."""
    from PySide6.QtGui import QIcon

    if sys.platform == "win32":
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("IRD.AquaMeasure")
    app.setWindowIcon(QIcon(str(app_dir / "resources" / "aquameasure.ico")))


def _run_cpu_tracking_self_test(output_path: Path) -> int:
    """Exerce le vrai modèle + ByteTrack depuis la distribution compilée.

    Ce point d'entrée est réservé au contrôle de livraison. Il écrit toujours
    un rapport JSON, y compris si un import ou une inférence échoue, car
    l'exécutable Windows n'a volontairement pas de console.
    """
    import json
    import time
    import traceback

    started = time.perf_counter()
    report: dict[str, object] = {"ok": False}
    try:
        import numpy as np
        import torch
        import ultralytics
        from ultralytics import YOLO

        import fish_detectors as fd

        registry = fd.registry()
        detector_id = "aquameasure-public"
        status = registry.status(detector_id)
        if status is None or not status.usable:
            reason = status.reason() if status is not None else "modèle absent"
            raise RuntimeError(f"{detector_id} indisponible : {reason}")
        weights = registry.weights_path(detector_id)
        if weights is None:
            raise RuntimeError("poids .pt de tracking introuvables")

        model = YOLO(str(weights))
        frame = np.zeros((320, 320, 3), dtype=np.uint8)
        results = model.track(
            frame,
            persist=True,
            tracker="bytetrack.yaml",
            conf=0.25,
            imgsz=320,
            verbose=False,
            device="cpu",
        )
        device = str(next(model.model.parameters()).device)
        cuda_build = torch.version.cuda
        cuda_available = bool(torch.cuda.is_available())
        if cuda_build is not None or cuda_available or device != "cpu":
            raise RuntimeError(
                f"runtime non CPU : build={cuda_build}, disponible={cuda_available}, device={device}"
            )

        report.update({
            "ok": True,
            "torch_version": torch.__version__,
            "torch_cuda_build": cuda_build,
            "torch_cuda_available": cuda_available,
            "ultralytics_version": ultralytics.__version__,
            "tracker": "bytetrack.yaml",
            "model_id": detector_id,
            "model_weights": str(weights),
            "device": device,
            "result_count": len(results or ()),
        })
    except Exception as exc:
        report.update({
            "error": str(exc),
            "traceback": traceback.format_exc(),
        })
    report["elapsed_seconds"] = round(time.perf_counter() - started, 3)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return 0 if report["ok"] else 1


def main() -> int:
    app_dir = _setup_paths()
    if "--self-test-clean-install" in sys.argv:
        from tools.release_install_test import run

        index = sys.argv.index("--self-test-clean-install")
        return run(Path(sys.argv[index + 1]).resolve(), app_dir)
    if "--self-test-models" in sys.argv:
        from tools.release_models_test import run
        index = sys.argv.index("--self-test-models")
        output = Path(sys.argv[index + 1]).resolve()
        return run(output)
    if "--self-test-cpu-tracking" in sys.argv:
        index = sys.argv.index("--self-test-cpu-tracking")
        output = (
            Path(sys.argv[index + 1]).expanduser().resolve()
            if index + 1 < len(sys.argv)
            else Path.cwd() / "tracking-self-test.json"
        )
        return _run_cpu_tracking_self_test(output)
    qml_root = _prepare_qml_module(app_dir)

    from PySide6.QtCore import QUrl
    from PySide6.QtWidgets import QApplication
    from PySide6.QtQml import QQmlApplicationEngine
    from PySide6.QtQuickControls2 import QQuickStyle

    from src.controllers.app_controller import AppController
    from src.imaging.frame_image_provider import FrameImageProvider

    QApplication.setOrganizationName("IRD")
    QApplication.setOrganizationDomain("ird.fr")
    QApplication.setApplicationName("AquaMeasure")

    app = QApplication(sys.argv)
    _set_application_icon(app, app_dir)
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

    logo = app_dir / "resources" / "logo_ird.png"
    ctx.setContextProperty("AppLogoUrl", QUrl.fromLocalFile(str(logo.resolve())))
    ctx.setContextProperty(
        "AppSansFont",
        _pick_font_family(["IBM Plex Sans", "Segoe UI", "Arial"], "Segoe UI"),
    )
    ctx.setContextProperty(
        "AppMonoFont",
        _pick_font_family(["IBM Plex Mono", "Consolas", "Courier New"], "Consolas"),
    )

    main_qml = qml_root / "Main.qml"
    engine.load(QUrl.fromLocalFile(str(main_qml.resolve())))
    if not engine.rootObjects():
        print("Echec chargement QML - verifiez la console Qt.")
        return 1

    print("AquaMeasure demarre - fenetre ouverte.", flush=True)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
