from __future__ import annotations

import json

from PySide6.QtCore import Property, QObject, Signal, Slot

from src.util import paths

# Préréglages calibration - seule source de vérité pour le comportement OpenCV / scan.
CALIB_QUALITY_PRESETS: dict[str, dict] = {
    "fast": {
        "label": "Rapide",
        "max_views": 50,
        "max_pairs": 50,
        "scan_stride": 8,
        "dense_window": 12,
        "dense_stride": 3,
        "camera_scale": 1.0,
    },
    "standard": {
        "label": "Standard",
        "max_views": 80,
        "max_pairs": 120,
        "scan_stride": 6,
        "dense_window": 18,
        "dense_stride": 1,
        "camera_scale": 1.0,
    },
    "precise": {
        "label": "Précis",
        "max_views": 120,
        "max_pairs": 160,
        "scan_stride": 4,
        "dense_window": 28,
        "dense_stride": 1,
        "camera_scale": 1.0,
    },
}

# Préréglages densité du scan v2 (saut sans mire + rafale après mire).
SCAN_DENSITY_PRESETS: dict[str, dict[str, int]] = {
    "sparse": {"scan_stride": 8, "dense_window": 12, "dense_stride": 3},
    "balanced": {"scan_stride": 5, "dense_window": 20, "dense_stride": 1},
    "detailed": {"scan_stride": 3, "dense_window": 35, "dense_stride": 1},
}

_CALIB_SETTINGS_FILE = "calib_scan_settings.json"
_UI_SETTINGS_FILE = "ui_settings.json"

# Saut rapide de la barre de transport (page Mesure). Deux quantites sont
# retenues, une par unite : basculer de « images » a « secondes » ne doit pas
# faire perdre le reglage precedent.
TRANSPORT_JUMP_UNITS = ("frames", "seconds")
DEFAULT_JUMP_FRAMES = 30
DEFAULT_JUMP_SECONDS = 5.0
MAX_JUMP_FRAMES = 9999
MAX_JUMP_SECONDS = 600.0


# Mires imprimees du projet (docs/mires_test). Colonnes = cases dans la
# largeur du fichier imprime : inversees (7x20, 7x5), OpenCV ne detecte aucun coin.
CHARUCO_PRESETS: dict[str, dict] = {
    "a3": {"label": "Petite mire A3 (5×7)", "sq": (5, 7), "square_mm": 49.5,
           "marker_mm": 37.0, "dict": "DICT_5X5_100"},
    "long": {"label": "Grande mire 84 cm (20×7)", "sq": (20, 7), "square_mm": 39.0,
             "marker_mm": 29.0, "dict": "DICT_5X5_100"},
}


class SettingsController(QObject):
    proModeChanged = Signal()
    charucoSettingsChanged = Signal()
    calibSettingsChanged = Signal()
    transportJumpChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pro_mode = False
        self._sq_x = 5
        self._sq_y = 7
        self._square_mm = 49.5
        self._marker_mm = 37.0
        self._dict_name = "DICT_5X5_100"
        self._min_corners = 6
        self._baseline_mm = 800.0
        self._calib_quality_preset = "fast"
        self._apply_preset_values(CALIB_QUALITY_PRESETS["fast"])
        self._transport_jump_unit = "frames"
        self._transport_jump_frames = DEFAULT_JUMP_FRAMES
        self._transport_jump_seconds = DEFAULT_JUMP_SECONDS
        self._load_calib_scan_settings()
        self._load_ui_settings()
        # La mire etait oubliee a chaque redemarrage.
        self.charucoSettingsChanged.connect(self._save_calib_scan_settings)

    def _apply_preset_values(self, preset: dict) -> None:
        self._max_views = int(preset["max_views"])
        self._max_pairs = int(preset["max_pairs"])
        self._calib_stride = int(preset["scan_stride"])
        self._calib_dense = int(preset["dense_window"])
        self._calib_dense_stride = int(preset.get("dense_stride", 1))
        self._calib_camera_scale = float(preset["camera_scale"])

    def _mark_custom_preset(self) -> None:
        if self._calib_quality_preset != "custom":
            self._calib_quality_preset = "custom"
            self.calibSettingsChanged.emit()

    def _save_calib_scan_settings(self) -> None:
        paths.ensure_camera_params_dir()
        data = {
            "preset_id": self._calib_quality_preset,
            "max_views": self._max_views,
            "max_pairs": self._max_pairs,
            "scan_stride": self._calib_stride,
            "dense_window": self._calib_dense,
            "dense_stride": self._calib_dense_stride,
            "camera_scale": self._calib_camera_scale,
            "min_corners": self._min_corners,
            "baseline_mm": self._baseline_mm,
            "charuco": {"sq": [self._sq_x, self._sq_y], "square_mm": self._square_mm,
                        "marker_mm": self._marker_mm, "dict": self._dict_name},
        }
        try:
            paths.cam_param(_CALIB_SETTINGS_FILE).write_text(
                json.dumps(data, indent=2) + "\n", encoding="utf-8"
            )
        except OSError:
            pass

    def _load_calib_scan_settings(self) -> None:
        path = paths.cam_param(_CALIB_SETTINGS_FILE)
        if not path.is_file():
            self._save_calib_scan_settings()
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        board = data.get("charuco")
        if isinstance(board, dict):
            try:
                self._sq_x, self._sq_y = (int(v) for v in board["sq"])
                self._square_mm = float(board["square_mm"])
                self._marker_mm = float(board["marker_mm"])
                self._dict_name = str(board["dict"])
            except (KeyError, TypeError, ValueError):
                pass
        preset_id = data.get("preset_id", "fast")
        if preset_id in CALIB_QUALITY_PRESETS:
            self._calib_quality_preset = preset_id
            self._apply_preset_values(CALIB_QUALITY_PRESETS[preset_id])
        else:
            self._calib_quality_preset = "custom"
            self._max_views = int(data.get("max_views", self._max_views))
            self._max_pairs = int(data.get("max_pairs", self._max_pairs))
            self._calib_stride = int(data.get("scan_stride", self._calib_stride))
            self._calib_dense = int(data.get("dense_window", self._calib_dense))
            self._calib_dense_stride = int(
                data.get("dense_stride", getattr(self, "_calib_dense_stride", 1))
            )
            self._calib_camera_scale = float(
                data.get("camera_scale", self._calib_camera_scale)
            )
        self._min_corners = int(data.get("min_corners", self._min_corners))
        self._baseline_mm = float(data.get("baseline_mm", self._baseline_mm))
        self.calibSettingsChanged.emit()

    # -- saut rapide de la barre de transport -----------------------------
    #
    # Reglage d'interface, pas de calibration : il vit dans son propre fichier
    # a cote des autres reglages hors git, et se recharge au demarrage suivant.

    def _save_ui_settings(self) -> None:
        paths.ensure_camera_params_dir()
        data = {
            "transport_jump_unit": self._transport_jump_unit,
            "transport_jump_frames": self._transport_jump_frames,
            "transport_jump_seconds": self._transport_jump_seconds,
        }
        try:
            paths.cam_param(_UI_SETTINGS_FILE).write_text(
                json.dumps(data, indent=2) + "\n", encoding="utf-8"
            )
        except OSError:
            pass

    def _load_ui_settings(self) -> None:
        path = paths.cam_param(_UI_SETTINGS_FILE)
        if not path.is_file():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(data, dict):
            return
        unit = str(data.get("transport_jump_unit", self._transport_jump_unit))
        if unit in TRANSPORT_JUMP_UNITS:
            self._transport_jump_unit = unit
        try:
            self._transport_jump_frames = self._clamp_jump_frames(
                data.get("transport_jump_frames", self._transport_jump_frames)
            )
            self._transport_jump_seconds = self._clamp_jump_seconds(
                data.get("transport_jump_seconds", self._transport_jump_seconds)
            )
        except (TypeError, ValueError):
            pass
        self.transportJumpChanged.emit()

    @staticmethod
    def _clamp_jump_frames(value) -> int:
        return max(1, min(MAX_JUMP_FRAMES, int(value)))

    @staticmethod
    def _clamp_jump_seconds(value) -> float:
        return round(max(0.1, min(MAX_JUMP_SECONDS, float(value))), 2)

    @Property(str, notify=transportJumpChanged)
    def transportJumpUnit(self):
        return self._transport_jump_unit

    @transportJumpUnit.setter
    def transportJumpUnit(self, value: str):
        unit = str(value)
        if unit not in TRANSPORT_JUMP_UNITS or unit == self._transport_jump_unit:
            return
        self._transport_jump_unit = unit
        self._save_ui_settings()
        self.transportJumpChanged.emit()

    @Property(int, notify=transportJumpChanged)
    def transportJumpFrames(self):
        return self._transport_jump_frames

    @transportJumpFrames.setter
    def transportJumpFrames(self, value: int):
        try:
            frames = self._clamp_jump_frames(value)
        except (TypeError, ValueError):
            return
        if frames == self._transport_jump_frames:
            return
        self._transport_jump_frames = frames
        self._save_ui_settings()
        self.transportJumpChanged.emit()

    @Property(float, notify=transportJumpChanged)
    def transportJumpSeconds(self):
        return self._transport_jump_seconds

    @transportJumpSeconds.setter
    def transportJumpSeconds(self, value: float):
        try:
            seconds = self._clamp_jump_seconds(value)
        except (TypeError, ValueError):
            return
        if abs(seconds - self._transport_jump_seconds) < 1e-9:
            return
        self._transport_jump_seconds = seconds
        self._save_ui_settings()
        self.transportJumpChanged.emit()

    @Slot(str, float)
    def setTransportJump(self, unit: str, amount: float):
        """Pose unite et quantite d'un coup - un seul enregistrement disque."""
        changed = False
        text = str(unit)
        if text in TRANSPORT_JUMP_UNITS and text != self._transport_jump_unit:
            self._transport_jump_unit = text
            changed = True
        try:
            if self._transport_jump_unit == "seconds":
                value = self._clamp_jump_seconds(amount)
                if abs(value - self._transport_jump_seconds) >= 1e-9:
                    self._transport_jump_seconds = value
                    changed = True
            else:
                value = self._clamp_jump_frames(amount)
                if value != self._transport_jump_frames:
                    self._transport_jump_frames = value
                    changed = True
        except (TypeError, ValueError):
            pass
        if changed:
            self._save_ui_settings()
            self.transportJumpChanged.emit()

    @Slot(float, result=int)
    def transportJumpStep(self, fps: float) -> int:
        """Taille du saut en images, pour la cadence donnee.

        En secondes, une cadence absente ou absurde retomberait sur 0 image :
        le bouton ne ferait alors rien. On se rabat sur 30 img/s, la valeur par
        defaut du lecteur, et on garantit au moins une image de deplacement.
        """
        if self._transport_jump_unit == "seconds":
            rate = float(fps) if fps and fps > 0 else 30.0
            return max(1, int(round(self._transport_jump_seconds * rate)))
        return max(1, self._transport_jump_frames)

    @Property(str, notify=transportJumpChanged)
    def transportJumpLabel(self):
        """Libelle court affiche sur le bouton et dans son infobulle."""
        if self._transport_jump_unit == "seconds":
            seconds = self._transport_jump_seconds
            text = f"{seconds:g}"
            return f"{text} s"
        return f"{self._transport_jump_frames} img"

    @Property(bool, notify=proModeChanged)
    def proMode(self):
        return self._pro_mode

    @proMode.setter
    def proMode(self, v: bool):
        if self._pro_mode != v:
            self._pro_mode = v
            self.proModeChanged.emit()

    @Property(int, notify=charucoSettingsChanged)
    def charucoSquaresX(self):
        return self._sq_x

    @charucoSquaresX.setter
    def charucoSquaresX(self, v: int):
        self._sq_x = v
        self.charucoSettingsChanged.emit()

    @Property(int, notify=charucoSettingsChanged)
    def charucoSquaresY(self):
        return self._sq_y

    @charucoSquaresY.setter
    def charucoSquaresY(self, v: int):
        self._sq_y = v
        self.charucoSettingsChanged.emit()

    @Property(float, notify=charucoSettingsChanged)
    def charucoSquareLengthMm(self):
        return self._square_mm

    @charucoSquareLengthMm.setter
    def charucoSquareLengthMm(self, v: float):
        self._square_mm = v
        self.charucoSettingsChanged.emit()

    @Property(float, notify=charucoSettingsChanged)
    def charucoMarkerLengthMm(self):
        return self._marker_mm

    @charucoMarkerLengthMm.setter
    def charucoMarkerLengthMm(self, v: float):
        self._marker_mm = v
        self.charucoSettingsChanged.emit()

    @Property(str, notify=charucoSettingsChanged)
    def charucoDictName(self):
        return self._dict_name

    @charucoDictName.setter
    def charucoDictName(self, v: str):
        self._dict_name = v
        self.charucoSettingsChanged.emit()

    @Property(str, notify=calibSettingsChanged)
    def calibQualityPreset(self):
        return self._calib_quality_preset

    @Property(str, notify=calibSettingsChanged)
    def calibQualityPresetLabel(self):
        if self._calib_quality_preset == "custom":
            return "Personnalisé"
        preset = CALIB_QUALITY_PRESETS.get(self._calib_quality_preset)
        return preset["label"] if preset else self._calib_quality_preset

    @Property(int, notify=calibSettingsChanged)
    def maxCalibViews(self):
        return self._max_views

    @maxCalibViews.setter
    def maxCalibViews(self, v: int):
        v = max(10, min(300, int(v)))
        if self._max_views != v:
            self._max_views = v
            self._mark_custom_preset()
            self._save_calib_scan_settings()
            self.calibSettingsChanged.emit()

    @Property(int, notify=calibSettingsChanged)
    def maxStereoPairs(self):
        return self._max_pairs

    @maxStereoPairs.setter
    def maxStereoPairs(self, v: int):
        v = max(10, min(500, int(v)))
        if self._max_pairs != v:
            self._max_pairs = v
            self._mark_custom_preset()
            self._save_calib_scan_settings()
            self.calibSettingsChanged.emit()

    @Property(float, notify=calibSettingsChanged)
    def calibCameraScale(self):
        return self._calib_camera_scale

    @Property(int, notify=calibSettingsChanged)
    def charucoMinCorners(self):
        return self._min_corners

    @charucoMinCorners.setter
    def charucoMinCorners(self, v: int):
        v = max(4, min(30, int(v)))
        if self._min_corners != v:
            self._min_corners = v
            self._mark_custom_preset()
            self._save_calib_scan_settings()
            self.calibSettingsChanged.emit()

    @Property(float, notify=calibSettingsChanged)
    def stereoNominalBaselineMm(self):
        return self._baseline_mm

    @stereoNominalBaselineMm.setter
    def stereoNominalBaselineMm(self, v: float):
        v = max(300.0, min(1500.0, float(v)))
        if self._baseline_mm != v:
            self._baseline_mm = v
            self._mark_custom_preset()
            self._save_calib_scan_settings()
            self.calibSettingsChanged.emit()

    @Property(int, notify=calibSettingsChanged)
    def calibScanStride(self):
        return self._calib_stride

    @calibScanStride.setter
    def calibScanStride(self, v: int):
        v = max(3, min(15, int(v)))
        if self._calib_stride != v:
            self._calib_stride = v
            self._mark_custom_preset()
            self._save_calib_scan_settings()
            self.calibSettingsChanged.emit()

    @Property(int, notify=calibSettingsChanged)
    def calibDenseWindow(self):
        return self._calib_dense

    @calibDenseWindow.setter
    def calibDenseWindow(self, v: int):
        v = max(10, min(60, int(v)))
        if self._calib_dense != v:
            self._calib_dense = v
            self._mark_custom_preset()
            self._save_calib_scan_settings()
            self.calibSettingsChanged.emit()

    @Property(int, notify=calibSettingsChanged)
    def calibDenseStride(self):
        return self._calib_dense_stride

    @calibDenseStride.setter
    def calibDenseStride(self, v: int):
        v = max(1, min(10, int(v)))
        if self._calib_dense_stride != v:
            self._calib_dense_stride = v
            self._mark_custom_preset()
            self._save_calib_scan_settings()
            self.calibSettingsChanged.emit()

    @Property(list, constant=True)
    def arucoDictNames(self):
        return [
            "DICT_4X4_50",
            "DICT_4X4_100",
            "DICT_5X5_50",
            "DICT_5X5_100",
            "DICT_6X6_50",
            "DICT_6X6_100",
            "DICT_7X7_50",
            "DICT_7X7_100",
        ]

    @Property(str, notify=charucoSettingsChanged)
    def charucoSummary(self):
        return (
            f"{self._sq_x}×{self._sq_y} · case {self._square_mm:.1f} mm · "
            f"marqueur {self._marker_mm:.1f} mm · {self._dict_name}"
        )

    @Property(str, notify=calibSettingsChanged)
    def calibSettingsSummary(self):
        res = (
            "pleine"
            if self._calib_camera_scale >= 0.999
            else f"×{self._calib_camera_scale:.2f}"
        )
        return (
            f"{self.calibQualityPresetLabel} · "
            f"{self._max_views} vues · {self._max_pairs} paires · "
            f"rés. OpenCV {res} · saut {self._calib_stride} · "
            f"rafale {self._calib_dense} · pas rafale 1/{self._calib_dense_stride}"
        )

    @Property(str, notify=charucoSettingsChanged)
    def charucoPresetId(self):
        """Mire du projet qui correspond aux reglages, ou "" (personnalisee)."""
        for pid, p in CHARUCO_PRESETS.items():
            if ((self._sq_x, self._sq_y) == p["sq"]
                    and abs(self._square_mm - p["square_mm"]) < 0.01
                    and abs(self._marker_mm - p["marker_mm"]) < 0.01
                    and self._dict_name == p["dict"]):
                return pid
        return ""

    @Property(list, constant=True)
    def charucoPresets(self):
        return [{"id": pid, "label": p["label"]} for pid, p in CHARUCO_PRESETS.items()]

    @Property(bool, notify=charucoSettingsChanged)
    def charucoUsingDefaults(self):
        return self.charucoPresetId == "a3"

    @Slot(str)
    def applyCharucoPreset(self, preset_id: str):
        p = CHARUCO_PRESETS.get(preset_id)
        if p is None:
            return
        self._sq_x, self._sq_y = p["sq"]
        self._square_mm, self._marker_mm = p["square_mm"], p["marker_mm"]
        self._dict_name = p["dict"]
        self.charucoSettingsChanged.emit()

    @Slot()
    def resetCharucoToDefaults(self):
        self.applyCharucoPreset("a3")

    @Slot()
    def resetCalibScanToDefaults(self):
        self.applyCalibQualityPreset("fast")

    @Slot(str)
    def applyCalibQualityPreset(self, preset_id: str):
        preset = CALIB_QUALITY_PRESETS.get(preset_id)
        if preset is None:
            return
        self._calib_quality_preset = preset_id
        self._apply_preset_values(preset)
        self._save_calib_scan_settings()
        self.calibSettingsChanged.emit()

    @Slot(str)
    def applyScanDensityPreset(self, preset_id: str):
        """Préréglage rapide : sparse | balanced | detailed."""
        preset = SCAN_DENSITY_PRESETS.get(preset_id)
        if preset is None:
            return
        self.calibScanStride = preset["scan_stride"]
        self.calibDenseWindow = preset["dense_window"]
        self.calibDenseStride = preset.get("dense_stride", 1)

    @Slot(int)
    def adjustScanDensity(self, direction: int):
        """direction < 0 = moins de détections ; > 0 = plus de détections."""
        step = 1 if direction > 0 else -1 if direction < 0 else 0
        if step == 0:
            return
        if step < 0:
            self.calibScanStride = min(15, self._calib_stride + 1)
            self.calibDenseWindow = max(10, self._calib_dense - 3)
            self.calibDenseStride = min(10, self._calib_dense_stride + 1)
        else:
            self.calibScanStride = max(3, self._calib_stride - 1)
            self.calibDenseWindow = min(60, self._calib_dense + 3)
            self.calibDenseStride = max(1, self._calib_dense_stride - 1)

    @Property(str, notify=calibSettingsChanged)
    def scanDensitySummary(self):
        rafale = (
            "chaque image"
            if self._calib_dense_stride <= 1
            else f"1 image / {self._calib_dense_stride}"
        )
        return (
            f"Saut sans mire : {self._calib_stride} image(s) · "
            f"Rafale : {self._calib_dense} pas ({rafale})"
        )

    @Slot(int, result=str)
    def calibrationDurationHint(self, scan_span: int) -> str:
        span = max(0, int(scan_span))
        if span <= 0:
            return (
                "Chargez les videos dans Sync et enregistrez In/Out "
                "pour estimer la duree."
            )
        intr = self._max_views
        pairs = self._max_pairs
        scale = max(0.25, min(1.0, self._calib_camera_scale))
        view_factor = 1.0 / max(scale * scale, 0.25)
        opencv_lo = max(1, int((intr * 2 + pairs) * view_factor) // 60)
        opencv_hi = max(opencv_lo + 1, int((intr * 3 + pairs * 2) * view_factor) // 30)

        stride = max(1, self._calib_stride)
        probed = max(1, (span + stride - 1) // stride)
        scan_lo = max(1, probed // 1500)
        scan_hi = max(scan_lo + 1, probed // 900 + 1)
        if probed < 1500:
            scan_lo, scan_hi = 1, max(2, probed // 600 + 1)
        trim_warn = (
            " · Rognez la video dans Sync (plage > 5 min)." if span > 9000 else ""
        )
        return (
            f"Préréglage {self.calibQualityPresetLabel} · "
            f"scan pas {stride} : ~{scan_lo}–{scan_hi} min "
            f"({probed} instants / {span} frames trim){trim_warn} · "
            f"OpenCV {intr} vues/cam, {pairs} paires · "
            f"~{opencv_lo}–{opencv_hi} min (G+D+stéréo)"
        )
