"""Journal fichier pour les sessions de calibration."""

from __future__ import annotations

import shutil
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from src.util import paths

_MAX_KEEP = 30


class CalibFileLogger:
    """Ecrit un fichier horodate par session + copie calib_latest.log."""

    def __init__(self, logs_dir: Path | None = None) -> None:
        self._dir = logs_dir or paths.calib_logs_dir()
        self._lock = threading.Lock()
        self._file: Path | None = None
        self._handle = None

    @property
    def log_path(self) -> Path | None:
        return self._file

    def is_active(self) -> bool:
        return self._handle is not None

    def start_session(self, meta: Mapping[str, Any]) -> Path:
        self.end_session("interrompu", "Nouvelle session demarree.")
        self._dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        self._file = self._dir / f"calib_{ts}.log"
        self._handle = open(self._file, "w", encoding="utf-8", newline="\n")
        started = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        lines = [
            f"=== AquaMeasure - calibration {started} ===",
            "",
        ]
        for key, value in meta.items():
            if value is None or value == "":
                continue
            lines.append(f"{key}: {value}")
        lines.append("")
        lines.append("--- journal ---")
        lines.append("")
        self._write("\n".join(lines) + "\n")
        self._prune_old()
        return self._file

    def append(self, line: str) -> None:
        if not line or self._handle is None:
            return
        ts = datetime.now().strftime("%H:%M:%S")
        self._write(f"[{ts}] {line}\n")

    def end_session(self, status: str, summary: str = "") -> Path | None:
        if self._handle is None:
            return None
        ended = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        footer = [f"", f"--- Fin ({status}) - {ended} ---"]
        if summary:
            footer.append(summary)
        footer.append("")
        self._write("\n".join(footer) + "\n")
        path = self._file
        self._handle.close()
        self._handle = None
        if path is not None and path.is_file():
            latest = self._dir / "calib_latest.log"
            shutil.copy2(path, latest)
        return path

    def _write(self, text: str) -> None:
        with self._lock:
            if self._handle is not None:
                self._handle.write(text)
                self._handle.flush()

    def _prune_old(self) -> None:
        dated = sorted(
            (p for p in self._dir.glob("calib_*.log") if p.name != "calib_latest.log"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for old in dated[_MAX_KEEP:]:
            try:
                old.unlink(missing_ok=True)
            except OSError:
                pass
