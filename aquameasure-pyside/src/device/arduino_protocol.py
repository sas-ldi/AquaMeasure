"""Protocole série de la carte Arduino AquaMeasure.

La carte pilote les deux caméras simultanément : alimentation via power bank,
simulation des boutons Power / WiFi, et flash de synchronisation stéréo.
Les commandes sont du texte ASCII terminé par LF (``\\n``).

Menu de configuration affiché par la carte au démarrage (source : fabricant,
message du 2026-08-17) - fait référence pour les commandes supportées ::

    --- MENU CONFIGURATION ---
      rec xx             : modifier durée d'enregistrement à xx min
      veille xx          : modifier durée de veille à xx min
      seq                : afficher configuration actuelle de la séquence
      cam1 on / cam1 off : activer/désactiver l'alimentation de la caméra 1
      cam2 on / cam2 off : activer/désactiver l'alimentation de la caméra 2
      wifi bp            : envoyer commande SIMULATE_WIFI_BUTTON aux caméras
      power bp           : envoyer commande SIMULATE_POWER_BUTTON aux caméras
"""

from __future__ import annotations

import re
from dataclasses import dataclass

LINE_END = "\n"
DEFAULT_BAUD = 115200
READ_TIMEOUT_S = 1.0
# Ouvrir le port bascule DTR : la carte redémarre, laissez-la booter.
BOOT_DELAY_S = 2.0


@dataclass(frozen=True)
class Command:
    key: str
    label: str
    text: str
    note: str = ""
    group: str = ""
    # Commande à paramètre (« rec xx ») : documentée dans le guide, mais pas
    # cliquable - elle passe par le formulaire de séquence.
    param: bool = False

    @property
    def display(self) -> str:
        """Trame telle qu'affichée dans les guides : ``cam1 on<LF>``."""
        return f"{self.text}<LF>"

    def encode(self) -> bytes:
        return encode_line(self.text)


GROUP_SEQUENCE = "Séquence"
GROUP_POWER = "Alimentation caméras"
GROUP_BUTTONS = "Boutons caméras"
GROUP_FLASH = "Flash (hors menu firmware)"

GROUP_ORDER: tuple[str, ...] = (
    GROUP_SEQUENCE,
    GROUP_POWER,
    GROUP_BUTTONS,
    GROUP_FLASH,
)

COMMANDS: tuple[Command, ...] = (
    Command(
        "rec",
        "Durée d'enregistrement",
        "rec xx",
        "xx en minutes - envoyée par « Envoyer la séquence »",
        GROUP_SEQUENCE,
        param=True,
    ),
    Command(
        "veille",
        "Durée de veille",
        "veille xx",
        "xx en minutes - envoyée par « Envoyer la séquence »",
        GROUP_SEQUENCE,
        param=True,
    ),
    Command(
        "seq",
        "Configuration actuelle de la séquence",
        "seq",
        "La carte affiche la séquence qu'elle a en mémoire",
        GROUP_SEQUENCE,
    ),
    Command(
        "cam1_on",
        "Activation alimentation caméra 1",
        "cam1 on",
        "Power bank caméra 1",
        GROUP_POWER,
    ),
    Command(
        "cam1_off",
        "Désactivation alimentation caméra 1",
        "cam1 off",
        "Power bank caméra 1",
        GROUP_POWER,
    ),
    Command(
        "cam2_on",
        "Activation alimentation caméra 2",
        "cam2 on",
        "Power bank caméra 2",
        GROUP_POWER,
    ),
    Command(
        "cam2_off",
        "Désactivation alimentation caméra 2",
        "cam2 off",
        "Power bank caméra 2",
        GROUP_POWER,
    ),
    Command(
        "power_bp",
        "Simulation bouton Power des caméras",
        "power bp",
        "Envoie SIMULATE_POWER_BUTTON aux caméras",
        GROUP_BUTTONS,
    ),
    Command(
        "wifi_bp",
        "Simulation bouton WiFi des caméras",
        "wifi bp",
        "Envoie SIMULATE_WIFI_BUTTON aux caméras",
        GROUP_BUTTONS,
    ),
    # Absentes du menu de configuration communiqué le 2026-08-17. Conservées
    # car le flash est piloté hors menu, mais non confirmées par le fabricant.
    Command(
        "stop_flash",
        "Couper le flash pendant la config",
        "stop flash",
        "Hors menu de configuration - non confirmée par le fabricant",
        GROUP_FLASH,
    ),
    Command(
        "start_flash",
        "Relancer le flash après la config",
        "start flash",
        "Hors menu de configuration - non confirmée par le fabricant",
        GROUP_FLASH,
    ),
    Command(
        "flash",
        "Flash unique",
        "flash",
        "Hors menu de configuration - non confirmée par le fabricant",
        GROUP_FLASH,
    ),
)

_BY_KEY = {c.key: c for c in COMMANDS}


def encode_line(text: str) -> bytes:
    return (text.strip() + LINE_END).encode("ascii", errors="replace")


def command(key: str) -> Command:
    return _BY_KEY[key]


def commands_in_group(group: str) -> tuple[Command, ...]:
    return tuple(c for c in COMMANDS if c.group == group)


def command_by_text(text: str) -> Command | None:
    """Commande correspondant à une trame saisie à la main, None si inconnue.

    Insensible à la casse et aux espaces multiples (« CAM1  ON » → cam1_on).
    Les commandes à paramètre sont exclues : « rec 10 » ne correspond pas au
    gabarit « rec xx ».
    """
    normalized = " ".join((text or "").split()).lower()
    if not normalized:
        return None
    for cmd in COMMANDS:
        if not cmd.param and cmd.text.lower() == normalized:
            return cmd
    return None


# -- Configuration de la séquence rec / veille --
# La carte règle la séquence en deux commandes distinctes, en minutes.
SEQUENCE_TEMPLATES: tuple[str, ...] = ("rec {rec_min}", "veille {pause_min}")

DEFAULT_RECORD_MIN = 10
DEFAULT_PAUSE_MIN = 50


def sequence_commands(record_min: int, pause_min: int) -> list[str]:
    """Commandes de réglage de la séquence, dans l'ordre d'envoi."""
    return [
        tpl.format(rec_min=record_min, pause_min=pause_min)
        for tpl in SEQUENCE_TEMPLATES
    ]


# -- Lecture de la réponse à « seq » --
# Le format exact de la réponse n'est pas spécifié par le fabricant : on lit
# donc le premier nombre suivant le mot-clé de chaque durée, sur sa ligne.
# Volontairement tolérant (« rec : 10 min », « Enregistrement = 10 »), et
# silencieux si rien ne correspond - mieux vaut ne rien changer que deviner.
_REC_RE = re.compile(r"\b(?:rec|enregistrement|record)\b[^0-9\n]{0,24}(\d{1,4})", re.I)
_PAUSE_RE = re.compile(r"\b(?:veille|pause|sleep|standby)\b[^0-9\n]{0,24}(\d{1,4})", re.I)

# Le menu de configuration se termine par la liste des commandes : ces lignes
# ne doivent pas être confondues avec une réponse à « seq ».
MENU_MARKER = "MENU CONFIGURATION"

_MIN_DURATION = 1
_MAX_DURATION = 24 * 60


def _first_duration(pattern: re.Pattern[str], text: str) -> int | None:
    for match in pattern.finditer(text):
        value = int(match.group(1))
        if _MIN_DURATION <= value <= _MAX_DURATION:
            return value
    return None


def parse_sequence_reply(text: str) -> tuple[int | None, int | None]:
    """(rec_min, veille_min) lus dans la réponse à « seq », None si absent.

    Les lignes du menu de configuration sont ignorées : elles décrivent la
    syntaxe (« rec xx ») et ne portent aucune valeur courante.
    """
    kept: list[str] = []
    for line in (text or "").splitlines():
        if MENU_MARKER.lower() in line.lower():
            kept.clear()  # tout ce qui précède appartenait au menu
            continue
        stripped = line.strip()
        # « rec xx : modifier durée… » - ligne de syntaxe, pas une valeur.
        is_syntax_line = bool(re.match(r"^\w+\s+xx\b", stripped, re.I)) or (
            " : " in stripped and "xx" in stripped.lower()
        )
        if is_syntax_line:
            continue
        kept.append(line)
    body = "\n".join(kept)
    return _first_duration(_REC_RE, body), _first_duration(_PAUSE_RE, body)
