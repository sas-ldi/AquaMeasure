"""Port série vers la carte Arduino - lignes ASCII terminées par LF."""

from __future__ import annotations

from src.device.arduino_protocol import READ_TIMEOUT_S, encode_line


class ArduinoSerial:
    def __init__(self) -> None:
        self._ser = None
        self._rx = ""

    @property
    def is_open(self) -> bool:
        return self._ser is not None and self._ser.is_open

    def open(self, port: str, baud: int) -> None:
        import serial

        self.close()
        self._ser = serial.Serial(
            port=port,
            baudrate=baud,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=READ_TIMEOUT_S,
            write_timeout=READ_TIMEOUT_S,
        )
        self._rx = ""
        self._ser.reset_input_buffer()

    def close(self) -> None:
        if self._ser is not None:
            try:
                if self._ser.is_open:
                    self._ser.close()
            except OSError:
                pass
            self._ser = None
        self._rx = ""

    def send_line(self, text: str) -> None:
        if not self.is_open:
            raise RuntimeError("Port série fermé")
        self._ser.write(encode_line(text))
        self._ser.flush()

    def read_lines(self) -> list[str]:
        """Lignes complètes reçues depuis le dernier appel (non bloquant)."""
        if not self.is_open:
            return []
        waiting = self._ser.in_waiting
        if waiting <= 0:
            return []
        chunk = self._ser.read(waiting)
        if not chunk:
            return []
        self._rx += chunk.decode("utf-8", errors="replace")
        *lines, self._rx = self._rx.split("\n")
        # Garde-fou : une carte muette sur le LF ne doit pas gonfler le tampon.
        if len(self._rx) > 4096:
            lines.append(self._rx)
            self._rx = ""
        return [line.rstrip("\r") for line in lines if line.strip()]
