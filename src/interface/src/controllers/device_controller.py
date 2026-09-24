from __future__ import annotations

from PySide6.QtCore import Property, QObject, QTimer, Signal, Slot
from PySide6.QtGui import QGuiApplication

from src.device import arduino_protocol as ap
from src.device.arduino_serial import ArduinoSerial


class DeviceController(QObject):
    """Dialogue série avec la carte Arduino qui pilote les deux caméras."""

    connectedChanged = Signal()
    portNameChanged = Signal()
    baudRateChanged = Signal()
    recordDurationMinChanged = Signal()
    pauseDurationMinChanged = Signal()
    lastErrorChanged = Signal()
    availablePortsChanged = Signal()
    serialLogChanged = Signal()
    boardRespondingChanged = Signal()
    sequenceTextChanged = Signal()
    portDetailTextChanged = Signal()
    cameraPowerChanged = Signal()
    sequenceSyncedChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._serial = ArduinoSerial()
        self._connected = False
        self._port = ""
        self._baud = ap.DEFAULT_BAUD
        self._record_min = ap.DEFAULT_RECORD_MIN
        self._pause_min = ap.DEFAULT_PAUSE_MIN
        self._last_error = ""
        self._ports: list[str] = []
        self._port_devices: list[str] = []
        self._port_details: dict[str, str] = {}
        self._port_detail_text = ""
        self._log = ""
        self._board_responding = False
        self._sequence_text = ""
        # Alimentation présumée des caméras : la carte n'accuse pas réception
        # de « cam1 on », on ne mémorise donc que la dernière commande envoyée.
        self._cam_power: dict[int, str] = {1: "", 2: ""}
        # True quand les durées affichées viennent d'une lecture « seq ».
        self._sequence_synced = False
        # Durées envoyées par « Envoyer la séquence », comparées à la relecture.
        self._sent_sequence: tuple[int, int] | None = None

        # Lecture non bloquante des lignes renvoyées par la carte.
        self._rx_timer = QTimer(self)
        self._rx_timer.setInterval(150)
        self._rx_timer.timeout.connect(self._drain_serial)

        # Après un « seq », on regroupe les lignes reçues dans la fenêtre.
        self._seq_capture: list[str] | None = None
        self._seq_timer = QTimer(self)
        self._seq_timer.setSingleShot(True)
        self._seq_timer.setInterval(900)
        self._seq_timer.timeout.connect(self._finish_sequence_capture)

        # Envoi espacé des commandes multi-lignes (« rec » puis « veille »).
        self._tx_queue: list[tuple[str, str]] = []
        self._tx_timer = QTimer(self)
        self._tx_timer.setSingleShot(True)
        self._tx_timer.setInterval(250)
        self._tx_timer.timeout.connect(self._pump_queue)

    # -- Properties --

    @Property(bool, constant=True)
    def serialAvailable(self):
        try:
            import serial  # noqa: F401
            return True
        except ImportError:
            return False

    @Property(bool, notify=connectedChanged)
    def connected(self):
        return self._connected

    @Property(str, notify=portNameChanged)
    def portName(self):
        return self._port

    @portName.setter
    def portName(self, v: str):
        resolved = self._resolve_port_label(v)
        if self._port != resolved:
            self._port = resolved
            self._update_port_detail_text()
            self.portNameChanged.emit()

    @Property(str, notify=portDetailTextChanged)
    def portDetailText(self):
        return self._port_detail_text

    @Property(int, notify=baudRateChanged)
    def baudRate(self):
        return self._baud

    @baudRate.setter
    def baudRate(self, v: int):
        if self._baud != v:
            self._baud = max(9600, min(921600, int(v)))
            self.baudRateChanged.emit()

    @Property(int, notify=recordDurationMinChanged)
    def recordDurationMin(self):
        return self._record_min

    @recordDurationMin.setter
    def recordDurationMin(self, v: int):
        v = max(1, min(24 * 60, int(v)))
        if self._record_min != v:
            self._record_min = v
            # Saisie utilisateur : la valeur ne reflète plus la carte tant que
            # « Envoyer la séquence » (qui relit) n'a pas été fait.
            self._set_sequence_synced(False)
            self.recordDurationMinChanged.emit()

    @Property(int, notify=pauseDurationMinChanged)
    def pauseDurationMin(self):
        return self._pause_min

    @pauseDurationMin.setter
    def pauseDurationMin(self, v: int):
        v = max(1, min(24 * 60, int(v)))
        if self._pause_min != v:
            self._pause_min = v
            self._set_sequence_synced(False)
            self.pauseDurationMinChanged.emit()

    @Property(str, notify=lastErrorChanged)
    def lastError(self):
        return self._last_error

    @Property(list, notify=availablePortsChanged)
    def availablePorts(self):
        return self._ports

    @Property(str, notify=serialLogChanged)
    def serialLog(self):
        return self._log

    @Property(bool, notify=boardRespondingChanged)
    def boardResponding(self):
        return self._board_responding

    @Property(str, notify=sequenceTextChanged)
    def sequenceText(self):
        return self._sequence_text

    @Property(bool, notify=sequenceSyncedChanged)
    def sequenceSynced(self):
        """True si les durées affichées ont été relues dans la carte."""
        return self._sequence_synced

    @Property(str, notify=cameraPowerChanged)
    def cam1Power(self):
        """« on », « off », ou « » si aucune commande n'a encore été envoyée."""
        return self._cam_power[1]

    @Property(str, notify=cameraPowerChanged)
    def cam2Power(self):
        return self._cam_power[2]

    @staticmethod
    def _group_list(include_params: bool):
        groups = []
        for title in ap.GROUP_ORDER:
            cmds = [
                c for c in ap.commands_in_group(title)
                if include_params or not c.param
            ]
            if not cmds:
                continue
            groups.append({
                "title": title,
                "commands": [
                    {"key": c.key, "label": c.label, "text": c.display, "note": c.note}
                    for c in cmds
                ],
            })
        return groups

    @Property(list, constant=True)
    def commandGroups(self):
        """Commandes cliquables - les commandes à paramètre sont exclues."""
        return self._group_list(include_params=False)

    @Property(list, constant=True)
    def commandReference(self):
        """Menu complet de la carte, pour le guide."""
        return self._group_list(include_params=True)

    @Property(int, constant=True)
    def defaultBaud(self):
        return ap.DEFAULT_BAUD

    # -- Helpers --

    def _set_error(self, msg: str) -> None:
        self._last_error = msg
        self.lastErrorChanged.emit()

    def _clear_error(self) -> None:
        if self._last_error:
            self._last_error = ""
            self.lastErrorChanged.emit()

    def _append_log(self, line: str) -> None:
        self._log += f"\n{line}"
        if len(self._log) > 12000:
            self._log = self._log[-10000:]
        self.serialLogChanged.emit()

    def _update_port_detail_text(self) -> None:
        text = self._port_details.get(self._port, "")
        if self._port_detail_text != text:
            self._port_detail_text = text
            self.portDetailTextChanged.emit()

    def _set_board_responding(self, value: bool) -> None:
        if self._board_responding != value:
            self._board_responding = value
            self.boardRespondingChanged.emit()

    @staticmethod
    def _port_kind(hwid: str) -> str:
        up = (hwid or "").upper()
        if "BTHENUM" in up or "BLUETOOTH" in up:
            return "Bluetooth"
        if "ACPI" in up or "PNP0501" in up:
            return "Port intégré (ACPI)"
        if any(k in up for k in ("CH340", "CH910", "CP210", "FTDI", "USB")):
            return "USB série"
        return ""

    @classmethod
    def _format_port_entry(cls, p) -> tuple[str, str, str]:
        """Retourne (libellé combo, device COMx, texte détail complet)."""
        device = p.device
        desc = (p.description or "").strip()
        manufacturer = (p.manufacturer or "").strip()
        product = (getattr(p, "product", None) or "").strip()
        serial = (p.serial_number or "").strip()
        kind = cls._port_kind(p.hwid or "")

        summary_parts: list[str] = []
        if desc:
            summary_parts.append(desc)
        if manufacturer and manufacturer.lower() not in (desc or "").lower():
            summary_parts.append(manufacturer)
        if kind:
            summary_parts.append(kind)
        if p.vid is not None and p.pid is not None:
            summary_parts.append(f"USB {p.vid:04X}:{p.pid:04X}")

        label = f"{device} - {' · '.join(summary_parts)}" if summary_parts else device

        detail_lines = [f"Port série : {device}"]
        if desc:
            detail_lines.append(f"Description Windows : {desc}")
        if manufacturer:
            detail_lines.append(f"Fabricant : {manufacturer}")
        if product:
            detail_lines.append(f"Produit : {product}")
        if kind:
            detail_lines.append(f"Type : {kind}")
        if p.vid is not None and p.pid is not None:
            detail_lines.append(f"Identifiant USB : VID {p.vid:04X} · PID {p.pid:04X}")
        if serial:
            detail_lines.append(f"Numéro de série : {serial}")
        if p.hwid:
            detail_lines.append(f"HWID : {p.hwid}")

        return label, device, "\n".join(detail_lines)

    def _resolve_port_label(self, name: str) -> str:
        if not name:
            return ""
        for label, device in zip(self._ports, self._port_devices, strict=False):
            if name in (label, device):
                return label
        return name

    def _device_from_label(self, label: str) -> str:
        for lbl, dev in zip(self._ports, self._port_devices, strict=False):
            if label == lbl:
                return dev
        if label.startswith("COM") or label.startswith("/dev/"):
            return label.split(" ", 1)[0]
        return label

    def _guess_board_port(self) -> str:
        keywords = ("arduino", "usb serial", "ch340", "ch910", "cp210", "ftdi", "uart", "wch")
        for label in self._ports:
            low = label.lower()
            if any(k in low for k in keywords):
                return label
        return ""

    # -- Envoi / réception --

    def _send(self, label: str, text: str) -> bool:
        self._append_log(f"> {label} : {text}<LF>")
        if not self._serial.is_open:
            self._append_log("[!] Port fermé")
            return False
        try:
            self._serial.send_line(text)
            return True
        except (OSError, RuntimeError) as exc:
            self._set_error(str(exc))
            self._append_log(f"[!] Erreur série : {exc}")
            return False

    def _drain_serial(self) -> None:
        try:
            lines = self._serial.read_lines()
        except OSError as exc:
            self._set_error(str(exc))
            self._append_log(f"[!] Erreur lecture série : {exc}")
            self._rx_timer.stop()
            return
        if not lines:
            return
        self._set_board_responding(True)
        for line in lines:
            self._append_log(f"< {line}")
            if self._seq_capture is not None:
                self._seq_capture.append(line)

    def _finish_sequence_capture(self) -> None:
        lines = self._seq_capture or []
        self._seq_capture = None
        text = "\n".join(lines)
        if not lines:
            text = ""
            self._append_log("[!] Aucune réponse de la carte à « seq »")
        if self._sequence_text != text:
            self._sequence_text = text
            self.sequenceTextChanged.emit()
        self._apply_sequence_reply(text)

    def _apply_sequence_reply(self, text: str) -> None:
        """Aligne les durées affichées sur ce que la carte a réellement en mémoire.

        La réponse à « seq » était collectée puis seulement affichée : les
        champs Enregistrement / Veille pouvaient donc contredire la carte sans
        que rien ne le signale.
        """
        rec, pause = ap.parse_sequence_reply(text)
        sent, self._sent_sequence = self._sent_sequence, None
        if rec is None and pause is None:
            if text:
                self._append_log(
                    "[!] Durées illisibles dans la réponse « seq » - "
                    "champs laissés inchangés"
                )
            self._set_sequence_synced(False)
            return

        changed: list[str] = []
        if rec is not None and rec != self._record_min:
            self._record_min = rec
            self.recordDurationMinChanged.emit()
            changed.append(f"enregistrement {rec} min")
        if pause is not None and pause != self._pause_min:
            self._pause_min = pause
            self.pauseDurationMinChanged.emit()
            changed.append(f"veille {pause} min")

        if changed:
            self._append_log("Séquence relue dans la carte : " + ", ".join(changed))
        self._set_sequence_synced(True)
        # Les champs affichent la mémoire de la carte : si elle diffère de ce
        # qui vient d'être envoyé, la carte a ignoré la commande. Sans ce
        # message, l'application semblait « remettre 50 » toute seule.
        if sent is not None:
            refused = []
            if rec is not None and rec != sent[0]:
                refused.append(f"enregistrement {sent[0]} min (la carte garde {rec} min)")
            if pause is not None and pause != sent[1]:
                refused.append(f"veille {sent[1]} min (la carte garde {pause} min)")
            if refused:
                message = ("La carte n'a pas appliqué : " + " ; ".join(refused)
                           + ". Réponse de la carte : " + " | ".join(text.splitlines()))
                self._append_log("[!] " + message)
                self._set_error(message)

    def _set_sequence_synced(self, value: bool) -> None:
        if self._sequence_synced != value:
            self._sequence_synced = value
            self.sequenceSyncedChanged.emit()

    def _note_camera_command(self, key: str) -> None:
        """Mémorise l'alimentation demandée par « cam1 on » / « cam2 off »."""
        if not key.startswith("cam"):
            return
        cam_id, _, state = key.partition("_")
        try:
            index = int(cam_id[3:])
        except ValueError:
            return
        if index not in self._cam_power or state not in ("on", "off"):
            return
        if self._cam_power[index] != state:
            self._cam_power[index] = state
            self.cameraPowerChanged.emit()

    # -- Slots --

    @Slot(str)
    def copyToClipboard(self, text: str):
        QGuiApplication.clipboard().setText(text)

    @Slot()
    def clearLog(self):
        self._log = ""
        self.serialLogChanged.emit()

    @Slot()
    def refreshPorts(self):
        self._ports = []
        self._port_devices = []
        self._port_details = {}
        if not self.serialAvailable:
            self._update_port_detail_text()
            self.availablePortsChanged.emit()
            return
        try:
            from serial.tools import list_ports

            entries: list[tuple[str, str, str]] = []
            for p in list_ports.comports():
                label, device, detail = self._format_port_entry(p)
                entries.append((label, device, detail))
            entries.sort(key=lambda e: e[1])
            self._port_devices = [e[1] for e in entries]
            self._ports = [e[0] for e in entries]
            self._port_details = {e[0]: e[2] for e in entries}

            if not self._port and self._ports:
                auto = self._guess_board_port()
                self._port = auto if auto else self._ports[0]
                self.portNameChanged.emit()
            self._update_port_detail_text()
        except OSError as exc:
            self._set_error(str(exc))
        self.availablePortsChanged.emit()

    @Slot(result=bool)
    def connectPort(self) -> bool:
        self._clear_error()
        if not self.serialAvailable:
            self._set_error("pyserial absent - pip install pyserial")
            return False
        device = self._device_from_label(self._port)
        if not device:
            self._set_error("Sélectionnez un port COM")
            return False
        try:
            self._serial.open(device, self._baud)
        except OSError as exc:
            self._set_error(f"Connexion impossible : {exc}")
            self._connected = False
            self.connectedChanged.emit()
            return False
        self._connected = True
        self.connectedChanged.emit()
        self._set_board_responding(False)
        self._rx_timer.start()
        self._append_log(f"Connecté sur {device} @ {self._baud}")
        self._append_log(
            f"Attente du boot carte ({ap.BOOT_DELAY_S:.0f} s - reset DTR à l'ouverture)…"
        )
        return True

    @Slot()
    def disconnectPort(self):
        self._rx_timer.stop()
        self._seq_timer.stop()
        self._tx_timer.stop()
        self._tx_queue.clear()
        self._seq_capture = None
        self._serial.close()
        if self._connected:
            self._connected = False
            self.connectedChanged.emit()
        self._set_board_responding(False)
        # Carte débranchée : on ne sait plus ni l'alimentation des caméras ni
        # si les durées affichées correspondent encore à sa mémoire.
        self._set_sequence_synced(False)
        if any(self._cam_power.values()):
            self._cam_power = {1: "", 2: ""}
            self.cameraPowerChanged.emit()
        self._append_log("Déconnecté")

    @Slot(result=bool)
    def reconnectPort(self) -> bool:
        self._append_log("Reconnexion…")
        port_label = self._port
        baud = self._baud
        self.disconnectPort()
        self.refreshPorts()
        if port_label:
            self._port = port_label
            self._update_port_detail_text()
            self.portNameChanged.emit()
        self._baud = baud
        self.baudRateChanged.emit()
        return self.connectPort()

    @Slot(str)
    def sendCommand(self, key: str):
        try:
            cmd = ap.command(key)
        except KeyError:
            self._append_log(f"[!] Commande inconnue : {key}")
            return
        if key == "seq":
            self.requestSequence()
            return
        if self._send(cmd.label, cmd.text):
            self._note_camera_command(key)

    @Slot(str)
    def sendRaw(self, text: str):
        text = text.strip()
        if not text:
            return
        if not self._send("brut", text):
            return
        # Une commande caméra tapée à la main doit mettre à jour l'état affiché.
        known = ap.command_by_text(text)
        if known is not None:
            self._note_camera_command(known.key)

    @Slot()
    def requestSequence(self):
        """Envoie « seq » et collecte la réponse de la carte."""
        self._clear_error()
        self._seq_capture = []
        if not self._send("Demande de séquence enregistrée", ap.command("seq").text):
            self._seq_capture = None
            return
        self._seq_timer.start()

    @Slot()
    def applySequence(self):
        """Règle la séquence rec/veille de la carte, puis relit sa config."""
        self._clear_error()
        if not self._connected:
            self._set_error("Connectez la carte avant d'envoyer la séquence")
            return
        lines = ap.sequence_commands(self._record_min, self._pause_min)
        if not lines:
            self._append_log("[!] Aucune commande de séquence définie")
            return
        self._append_log(
            f"Séquence : {self._record_min} min d'enregistrement / "
            f"{self._pause_min} min de veille"
        )
        self._sent_sequence = (self._record_min, self._pause_min)
        self._tx_queue = [("Séquence", text) for text in lines]
        self._pump_queue()

    def _pump_queue(self) -> None:
        """Vide la file d'envoi une ligne à la fois, puis relit la config."""
        if not self._tx_queue:
            self.requestSequence()
            return
        label, text = self._tx_queue.pop(0)
        if not self._send(label, text):
            self._tx_queue.clear()
            return
        self._tx_timer.start()
