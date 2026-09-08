"""Lance les tests avec les chemins du dépôt et des données isolées."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
CORE = (
    "test_madagascar_taxonomy", "test_sessions", "test_session_csv_tables",
    "test_session_export_workflow", "test_export_tracking",
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", choices=("data", "interface"), default="data")
    parser.add_argument("--pattern", default="test_*.py")
    parser.add_argument("--core", action="store_true", help="Tests de données utilisés en intégration continue")
    args = parser.parse_args()
    if args.core and args.suite != "data":
        parser.error("--core concerne la suite data")
    env = os.environ.copy()
    paths = [ROOT / "tests", ROOT / "src"]
    env["PYTHONPATH"] = os.pathsep.join(map(str, paths))
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    env["YOLO_AUTOINSTALL"] = "false"
    env["PYTHONUTF8"] = "1"
    command = [sys.executable, "-m", "unittest"]
    if args.core:
        command += ["annotations." + name for name in CORE]
    else:
        suite = "annotations" if args.suite == "data" else "interface"
        command += ["discover", "-s", str(ROOT / "tests" / suite), "-p", args.pattern]
        if args.suite == "data":
            command += ["-t", str(ROOT / "tests")]
    with tempfile.TemporaryDirectory(prefix="aquameasure_tests_") as tmp:
        env["AQUAMEASURE_STORAGE_CONFIG"] = str(Path(tmp) / "storage.json")
        env["FISH_VISION_DB"] = str(Path(tmp) / "annotations.db")
        return subprocess.run(command, cwd=ROOT, env=env).returncode


if __name__ == "__main__":
    raise SystemExit(main())
