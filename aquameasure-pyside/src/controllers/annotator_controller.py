"""Identite de l'annotateur - « qui annote ? ».

`author` valait `'operator'` en dur sur toutes les lignes de la base : on ne
savait pas qui avait identifie un poisson, ni qui crediter dans un jeu de
donnees publie. Ce controleur porte l'identite courante, demandee **une fois**
au premier lancement puis memorisee (table `annotators` + reglage du dernier
utilise), et changeable a tout moment depuis la page Sessions.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import Property, QObject, Signal, Slot

from src.util import paths
from src.util.log_model import LogModel


class AnnotatorController(QObject):
    annotatorsChanged = Signal()
    currentChanged = Signal()
    statusTextChanged = Signal()
    # Meme idiome que DetectorController.openManagerRequested : la page demande,
    # Main.qml ouvre - aucune page n'a besoin de connaitre la fenetre.
    openDialogRequested = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._logs = LogModel(self)
        self._rows: list[dict] = []
        self._suggested_names: list[str] = []
        self._current: dict = {}
        self._status = ""
        self._db_ok = self._check_db()
        self.refresh()

    # ── utilitaires ────────────────────────────────────────────────────

    def _ensure_fv(self) -> None:
        root = paths.app_root()
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        fv = Path(root) / "fish-vision"
        if fv.is_dir() and str(fv) not in sys.path:
            sys.path.insert(0, str(fv))

    def _check_db(self) -> bool:
        """Base utilisable - **creee si besoin**.

        Se contenter de `is_available()` faisait echouer le tout premier
        lancement : le fichier n'existe pas encore, le controleur se croyait
        sans base, et la question « Qui annote ? » n'etait jamais posee - soit
        exactement le cas ou elle est indispensable.
        """
        try:
            self._ensure_fv()
            import fish_annotate as fa

            return fa.ensure_database()
        except Exception:
            return False

    def _set_status(self, msg: str) -> None:
        self._status = msg
        self.statusTextChanged.emit()

    # ── proprietes exposees au QML ─────────────────────────────────────

    @Property(QObject, constant=True)
    def logs(self):
        return self._logs

    @Property(str, notify=statusTextChanged)
    def statusText(self):
        return self._status

    @Property(bool, notify=currentChanged)
    def needsIdentity(self):
        """Vrai au tout premier lancement : personne ne s'est encore identifie.

        C'est ce qui declenche le dialogue « Qui annote ? ». Une base sans
        annotateur ne doit pas laisser ecrire des lignes anonymes.
        """
        return self._db_ok and not self._current

    @Property(str, notify=currentChanged)
    def currentId(self):
        return str(self._current.get("annotator_id", ""))

    @Property(str, notify=currentChanged)
    def currentName(self):
        return str(self._current.get("display_name", ""))

    @Property(str, notify=currentChanged)
    def currentOrcid(self):
        return str(self._current.get("orcid", ""))

    @Property(list, notify=annotatorsChanged)
    def names(self):
        """Noms affiches, dans l'ordre du selecteur."""
        return [str(row.get("display_name", "")) for row in self._rows]

    @Property(list, notify=annotatorsChanged)
    def suggestedNames(self):
        """Noms de la table et des sessions historiques, pour la saisie."""
        return self._suggested_names

    @Property(int, notify=currentChanged)
    def currentIndex(self):
        wanted = self.currentId
        for i, row in enumerate(self._rows):
            if str(row.get("annotator_id", "")) == wanted:
                return i
        return -1

    @Property(int, notify=annotatorsChanged)
    def count(self):
        return len(self._rows)

    @Property(bool, constant=True)
    def dbAvailable(self):
        return self._db_ok

    # ── actions ────────────────────────────────────────────────────────

    @Slot()
    def requestDialog(self):
        """Demande l'ouverture du dialogue « Qui annote ? ».

        Annulable des qu'une identite existe deja : on change d'annotateur, on
        ne repart pas de zero.
        """
        self.openDialogRequested.emit(bool(self._rows))

    @Slot()
    def refresh(self):
        if not self._db_ok:
            self._db_ok = self._check_db()
        if not self._db_ok:
            self._rows = []
            self._suggested_names = []
            self._current = {}
            self.annotatorsChanged.emit()
            self.currentChanged.emit()
            return
        try:
            import fish_annotate as fa

            self._ensure_fv()
            self._rows = fa.list_annotators()
            self._suggested_names = fa.list_known_annotator_names()
            self._current = fa.current_annotator() or {}
            self.annotatorsChanged.emit()
            self.currentChanged.emit()
        except Exception as exc:
            self._logs.append(f"[!] Annotateurs : {exc}")
            self._set_status(str(exc))

    @Slot(str, result=str)
    def orcidForName(self, display_name: str) -> str:
        """ORCID d'une identité existante, avec comparaison souple du nom."""
        key = " ".join(str(display_name or "").split()).casefold()
        for row in self._rows:
            row_key = " ".join(str(row.get("display_name", "")).split()).casefold()
            if row_key == key:
                return str(row.get("orcid", ""))
        return ""

    @Slot(str, str, result=bool)
    def createAnnotator(self, display_name: str, orcid: str = "") -> bool:
        """Cree une identite et la retient comme annotateur courant."""
        if not self._db_ok:
            self._set_status("Base d'annotations indisponible")
            return False
        try:
            import fish_annotate as fa

            self._ensure_fv()
            row = fa.create_annotator(display_name, orcid)
            self.refresh()
            self._set_status(f"Annotateur : {row.get('display_name', '')}")
            self._logs.append(self._status)
            return True
        except ValueError as exc:
            # Nom vide ou ORCID mal forme : message tel quel, il est explicite.
            self._set_status(str(exc))
            return False
        except Exception as exc:
            self._set_status(str(exc))
            self._logs.append(f"[!] Annotateur : {exc}")
            return False

    @Slot(int)
    def selectAnnotatorAt(self, index: int):
        if not (0 <= index < len(self._rows)):
            return
        self.selectAnnotatorById(str(self._rows[index].get("annotator_id", "")))

    @Slot(str)
    def selectAnnotatorById(self, annotator_id: str):
        if not annotator_id or not self._db_ok:
            return
        try:
            import fish_annotate as fa

            self._ensure_fv()
            fa.set_current_annotator(annotator_id)
            self.refresh()
            self._set_status(f"Annotateur : {self.currentName}")
        except Exception as exc:
            self._set_status(str(exc))
            self._logs.append(f"[!] Annotateur : {exc}")
