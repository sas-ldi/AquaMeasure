"""État persistant du dernier export lancé depuis l'onglet Données."""

from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication


APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from src.controllers.pro_tools_controller import ProToolsController  # noqa: E402


class ProToolsExportStateTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def _wait_for_process(self, controller: ProToolsController, timeout: float = 5.0):
        deadline = time.monotonic() + timeout
        while controller.busy and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.01)
        self.app.processEvents()
        self.assertFalse(controller.busy, "Le QProcess est resté verrouillé")
        self.assertIsNone(controller._process)

    def _run_script(self, source: str, *, python_exe: str | None = None, kind: str = "technical", on_import=None):
        temporary = tempfile.TemporaryDirectory(prefix="pro_export_state_")
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        scripts = root / "scripts"
        scripts.mkdir()
        (scripts / "dummy.py").write_text(source, encoding="utf-8")
        controller = ProToolsController()
        if on_import:
            controller.fishialLibraryImported.connect(on_import)
        with (
            patch.object(controller, "_fv_root", return_value=root),
            patch.object(controller, "_python_exe", return_value=python_exe or sys.executable),
        ):
            controller._run_script("dummy.py", [], is_export=True, export_kind=kind)
            self._wait_for_process(controller)
        return controller

    def test_process_success_exposes_code_and_existing_output_directory(self):
        controller = ProToolsController()
        temporary = tempfile.TemporaryDirectory(prefix="real_export_")
        self.addCleanup(temporary.cleanup)
        controller._set_last_export("running")
        controller._record_export_result(
            0,
            "journal avant JSON\n"
            + json.dumps({"format": "coco", "output_path": temporary.name}),
        )

        self.assertEqual(controller.lastExport["status"], "success")
        self.assertEqual(controller.lastExport["exitCode"], 0)
        self.assertEqual(controller.lastExport["directory"], temporary.name)
        self.assertEqual(controller.lastExport["error"], "")

    def test_process_failure_exposes_nonzero_code(self):
        controller = ProToolsController()
        controller._set_last_export("running")
        controller._record_export_result(7, "export impossible")

        self.assertEqual(controller.lastExport["status"], "error")
        self.assertEqual(controller.lastExport["exitCode"], 7)
        self.assertEqual(controller.lastExport["directory"], "")

    def test_failed_to_start_releases_busy_and_exposes_error(self):
        controller = self._run_script(
            "print('jamais exécuté')",
            python_exe=str(Path(tempfile.gettempdir()) / "python-inexistant.exe"),
        )

        self.assertEqual(controller.lastExport["status"], "error")
        self.assertTrue(controller.lastExport["error"])

    def test_zero_without_json_is_an_error(self):
        controller = self._run_script("print('terminé sans manifeste')")

        self.assertEqual(controller.lastExport["status"], "error")
        self.assertEqual(controller.lastExport["exitCode"], 0)
        self.assertIn("aucun dossier", controller.lastExport["error"])

    def test_zero_with_missing_directory_is_an_error(self):
        missing = Path(tempfile.gettempdir()) / "export-coco-dossier-absent-cycle-3"
        source = (
            "import json\n"
            f"print(json.dumps({{'output_path': {str(missing)!r}}}))\n"
        )
        controller = self._run_script(source)

        self.assertEqual(controller.lastExport["status"], "error")
        self.assertEqual(controller.lastExport["directory"], str(missing))
        self.assertIn("introuvable", controller.lastExport["error"])

    def test_zero_with_real_directory_is_success(self):
        output = tempfile.TemporaryDirectory(prefix="export-coco-real_")
        self.addCleanup(output.cleanup)
        source = (
            "import json\n"
            f"print(json.dumps({{'output_path': {output.name!r}}}))\n"
        )
        controller = self._run_script(source)

        self.assertEqual(controller.lastExport["status"], "success")
        self.assertEqual(controller.lastExport["directory"], output.name)

    def test_session_export_uses_the_explicit_selected_format(self):
        controller = ProToolsController()
        output = tempfile.TemporaryDirectory(prefix="session-format_")
        self.addCleanup(output.cleanup)
        controller.sessionExportFormat = "tracking"

        with (
            patch.object(controller, "_export_root", return_value=Path(output.name)),
            patch.object(controller, "_load_session_preview", return_value={"missing_media": []}),
            patch.object(controller, "_run_script") as run,
        ):
            controller.exportSession("session-1")

        args = run.call_args.args[1]
        self.assertEqual(args[args.index("--format") + 1], "tracking")
        self.assertEqual(controller.sessionExportFormat, "tracking")

    def test_csv_does_not_require_the_source_video_to_still_exist(self):
        controller = ProToolsController()
        output = tempfile.TemporaryDirectory(prefix="session-csv_")
        self.addCleanup(output.cleanup)
        controller.sessionExportFormat = "csv"
        preview = {"missing_media": [{"media_id": "missing", "name": "old.mp4"}]}

        with (
            patch.object(controller, "_export_root", return_value=Path(output.name)),
            patch.object(controller, "_load_session_preview", return_value=preview),
            patch.object(controller, "_run_script") as run,
        ):
            controller.exportSession("session-1")

        self.assertEqual(controller.sessionExportState, "idle")
        run.assert_called_once()

    def test_unknown_session_format_falls_back_to_coco(self):
        controller = ProToolsController()
        controller.sessionExportFormat = "invented"
        self.assertEqual(controller.sessionExportFormat, "coco")

    def test_fishial_import_refresh_signal_and_user_summary_after_process(self):
        output = tempfile.TemporaryDirectory(prefix="fishial_import_")
        self.addCleanup(output.cleanup)
        notifications = []
        summary = "Fishial local importe : 5 nouvelles references."
        payload = {"output_path": output.name, "message": summary, "added": 5}
        controller = self._run_script(
            "import json\nprint(json.dumps(" + repr(payload) + "))\n",
            kind="fishial_local_import", on_import=lambda: notifications.append(True),
        )
        self.assertEqual(notifications, [True])
        self.assertEqual(controller.fishialTransferSummary, summary)
        self.assertEqual(controller.lastExport["kind"], "fishial_local_import")

    def test_failed_fishial_import_does_not_claim_a_refresh(self):
        notifications = []
        controller = self._run_script(
            "raise SystemExit(2)", kind="fishial_local_import",
            on_import=lambda: notifications.append(True),
        )
        self.assertEqual(notifications, [])
        self.assertEqual(controller.lastExport["status"], "error")

    def test_fishial_picker_cancellation_does_not_start_import(self):
        controller = ProToolsController()
        with patch("src.controllers.pro_tools_controller.QFileDialog.getOpenFileName", return_value=("", "")), patch.object(controller, "_run_script") as run:
            controller.importFishialLocal()
        run.assert_not_called()

    def test_selected_fishial_zip_is_passed_as_one_argument(self):
        controller = ProToolsController()
        archive = "C:/Donnees client/Fishial local.zip"
        with patch("src.controllers.pro_tools_controller.QFileDialog.getOpenFileName", return_value=(archive, "")), patch.object(controller, "_run_script") as run:
            controller.importFishialLocal()
        self.assertEqual(run.call_args.args[1], ["import", "--archive", archive])


if __name__ == "__main__":
    unittest.main()
