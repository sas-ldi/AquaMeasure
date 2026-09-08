from __future__ import annotations

from PySide6.QtCore import (
    QAbstractListModel,
    Property,
    QCoreApplication,
    QMetaObject,
    QModelIndex,
    Qt,
    QThread,
    Signal,
    Slot,
    Q_ARG,
)


class LogModel(QAbstractListModel):
    MessageRole = Qt.UserRole + 1
    fullTextChanged = Signal()
    countChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._lines: list[str] = []

    @Property(str, notify=fullTextChanged)
    def fullText(self) -> str:
        return "\n".join(self._lines)

    @Property(int, notify=countChanged)
    def count(self) -> int:
        """Nombre de lignes, exposé comme propriété notifiante.

        rowCount() est une méthode : appelée depuis un binding QML elle est
        évaluée une seule fois et ne se réévalue jamais. La console restait donc
        bloquée sur « Aucun message » même remplie de lignes.
        """
        return len(self._lines)

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        if parent.isValid():
            return 0
        return len(self._lines)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid() or index.row() >= len(self._lines):
            return None
        if role in (Qt.DisplayRole, self.MessageRole):
            return self._lines[index.row()]
        return None

    def roleNames(self):  # noqa: N802
        return {self.MessageRole: b"message", Qt.DisplayRole: b"display"}

    def _on_main_thread(self) -> bool:
        app = QCoreApplication.instance()
        if app is None:
            return True
        return QThread.currentThread() == app.thread()

    def _push_line(self, line: str) -> None:
        self.beginInsertRows(QModelIndex(), len(self._lines), len(self._lines))
        self._lines.append(line)
        self.endInsertRows()
        self.countChanged.emit()
        self.fullTextChanged.emit()

    @Slot(str)
    def _appendLine(self, line: str) -> None:
        self._push_line(line)

    @Slot(str)
    def append(self, line: str) -> None:
        if not line:
            return
        parts = line.split("\n")
        if not self._on_main_thread():
            for part in parts:
                if part:
                    QMetaObject.invokeMethod(
                        self,
                        "_appendLine",
                        Qt.ConnectionType.QueuedConnection,
                        Q_ARG(str, part),
                    )
            return
        for part in parts:
            if part:
                self._push_line(part)

    @Slot()
    def clear(self) -> None:
        if not self._lines:
            return
        if not self._on_main_thread():
            QMetaObject.invokeMethod(
                self,
                "clear",
                Qt.ConnectionType.QueuedConnection,
            )
            return
        self.beginResetModel()
        self._lines.clear()
        self.endResetModel()
        self.countChanged.emit()
        self.fullTextChanged.emit()


class CalibLogModel(LogModel):
    """Console calibration + miroir vers fichier si session active."""

    def __init__(self, file_logger, parent=None):
        super().__init__(parent)
        self._file_logger = file_logger

    def _push_line(self, line: str) -> None:
        super()._push_line(line)
        if self._file_logger is not None:
            self._file_logger.append(line)
