#!/usr/bin/env python3
"""Annote les captures d'écran AquaMeasure pour la documentation.

Produit, à partir des captures brutes de ``docs/images/`` :
  - une version annotée, repères numérotés sur les zones clés ;
  - une planche de zooms agrandis sur les détails importants ;
  - ``docs/images/annotated/legendes.md``, les légendes prêtes à citer.

Les repères ne sont plus des coordonnées écrites à la main. Chaque repère
désigne un élément par son **libellé exact**, et sa position est lue dans
``docs/images/geometry.json``, relevé par ``capture_screenshots.py`` dans la
fenêtre au moment même de la capture. Un bouton déplacé, agrandi ou renommé ne
produit donc plus un cadre qui encadre le vide : il produit soit un cadre juste,
soit un avertissement explicite.

Usage :
    python aquameasure-pyside/docs/capture_screenshots.py     (d'abord)
    python aquameasure-pyside/docs/annotate_screenshots.py

Dépendance : pip install pillow
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

DOCS = Path(__file__).resolve().parent
SRC = DOCS / "images"
OUT = SRC / "annotated"
GEOMETRY = SRC / "geometry.json"

MARK = (255, 176, 32)        # ambre : repères
TEXT_ON_MARK = (26, 26, 26)
ZOOM_BG = (12, 22, 36)
ZOOM_FRAME = (43, 217, 198)  # turquoise : planches de zoom
LABEL_FG = (232, 240, 247)

FONT_BOLD = "C:/Windows/Fonts/segoeuib.ttf"
FONT_REG = "C:/Windows/Fonts/segoeui.ttf"

R = 19          # rayon de la pastille numérotée
STROKE = 4      # épaisseur du cadre
PAD = 6         # marge autour de l'élément désigné


def font(size: int, bold: bool = True) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(FONT_BOLD if bold else FONT_REG, size)
    except OSError:
        return ImageFont.load_default()


@dataclass
class Mark:
    """Repère : l'élément visé par son libellé, et ce qu'on en dit.

    ``kind`` lève une ambiguïté quand plusieurs éléments portent le même texte
    (le libellé d'un bouton et le bouton lui-même, par exemple). ``span``
    étend le cadre jusqu'à un second élément, pour désigner une rangée entière.
    """
    label: str
    caption: str
    kind: str = ""
    span: str = ""
    zoom: bool = False
    anchor: str = "tl"


@dataclass
class Shot:
    name: str
    title: str
    marks: list[Mark] = field(default_factory=list)
    zoom_scale: float = 1.8


# ─────────────────────────────────────────────────────────────────────────────
#  Ce qui est désigné sur chaque capture
# ─────────────────────────────────────────────────────────────────────────────

SHOTS: list[Shot] = [
    Shot("01-accueil", "Écran d'accueil", [
        Mark("Fichier", "La barre de menus : Fichier, Édition, Affichage, Outils, Aide",
             span="Aide"),
        Mark("Machine · déconnectée", "L'état de la machine de prise de vues, toujours visible",
             zoom=True, anchor="tr"),
        Mark("Connectez la machine de prise de vues",
             "Ce que l'application vous conseille de faire maintenant"),
        Mark("État du projet", "Les trois voyants : machine, synchronisation, calibration",
             span="À faire"),
        Mark("Continuer le workflow",
             "Vous emmène directement à la première étape qui reste à faire", zoom=True),
    ]),

    Shot("02-machine", "Page Machine", [
        Mark("Port série", "L'état du port, de la carte et de la séquence", span="Séquence"),
        Mark("Port", "Le port de la carte, puis le débit", span="Baud", zoom=True),
        Mark("Connecter", "Se connecter, se reconnecter, se déconnecter, tester",
             span="Tester la carte"),
        Mark("Enregistrement (min)", "La séquence enregistrement puis veille, jouée par la carte",
             span="Veille (min)"),
        Mark("Envoyer la séquence", "Écrire la séquence dans la carte, puis la relire",
             span="Lire la séquence", zoom=True),
    ]),

    # Les repères ne visent que des libellés stables. Un nom de fichier de
    # démonstration ou un numéro d'image dépendent de la paire de vidéos
    # trouvée sur le poste : ils faisaient disparaître le repère en silence
    # dès que la capture tournait ailleurs.
    Shot("03-sync-videos", "Synchronisation, choix des vidéos", [
        Mark("Vidéos", "Les cinq sections du panneau, dans l'ordre de travail",
             kind="AppLabel", span="Prochaine étape"),
        Mark("Caméra gauche", "Les deux vidéos de la paire", span="Caméra droite",
             zoom=True),
        Mark("CAM G", "L'aperçu de la caméra gauche", anchor="tl"),
        Mark("Flash manuel", "Désigner soi-même l'image du flash sur cette caméra",
             zoom=True),
        Mark("→ Calibration", "Passer à l'étape suivante une fois le décalage figé"),
    ]),

    Shot("03b-sync-flash", "Synchronisation, détection du flash", [
        Mark("Détecter flash", "Cherche le flash dans les deux vidéos et en déduit le décalage",
             zoom=True),
        Mark("Cadre de détection", "Limite la recherche à une zone que vous dessinez"),
        Mark("✕ ROI", "Efface la zone dessinée"),
    ]),

    Shot("03c-sync-manuelle", "Synchronisation manuelle", [
        Mark("Appliquer sync (frames courantes)",
             "Fige le décalage à partir des deux images affichées", zoom=True),
    ]),

    Shot("03d-sync-trim", "Découpe des vidéos", [
        Mark("Enregistrer In / Out",
             "Mémorise les bornes utiles : tout le reste sera ignoré", zoom=True),
    ]),

    Shot("04-calibration-videos", "Calibration, vidéos de la mire", [
        Mark("Importer les vidéos", "Reprend les vidéos de l'étape Synchronisation",
             zoom=True),
        Mark("Caméra gauche", "Les deux vidéos où la mire est filmée",
             span="Caméra droite"),
        Mark("Erreur de la calibration (RMSE)",
             "Le verdict, une fois le calcul terminé", span="Focale G / D"),
        Mark("Passer à la mesure →", "Disponible seulement si une calibration est enregistrée",
             zoom=True),
    ]),

    Shot("04b-calibration-reglages", "Calibration, réglages et lancement", [
        Mark("Mire ChArUco", "La géométrie de la planche imprimée", zoom=True),
        Mark("Paramètres calibration", "La finesse du balayage des vidéos"),
        Mark("Lancer la calibration", "Démarre le calcul", zoom=True),
        Mark("Annuler", "Interrompt un calcul en cours"),
    ]),

    Shot("05-mesure-session", "Mesure, section Session", [
        Mark("Lieu (obligatoire)", "Lieu et date sont obligatoires : sans eux, rien n'est publiable",
             span="2026-08-20 08:30:00"),
        Mark("Enregistrer session", "Enregistre la paire, fige le décalage, trace la calibration",
             zoom=True),
        Mark("Détection & suivi", "Ce menu n'apparaît que sur la page Mesure", anchor="tl"),
        Mark("Cliquez le point A sur l'image gauche rectifiee",
             "La consigne de l'étape de mesure en cours"),
        # Le clic droit ouvre la fiche du poisson et « Enregistrer le poisson »
        # l'écrit : ce bouton porte les deux libellés selon l'état. Sans fiche
        # ouverte, la capture montre le premier.
        Mark("Ouvrir la fiche de ce cadre",
             "Ouvre la fiche du cadre choisi, puis l'enregistre", zoom=True),
        Mark("Mesurer",
             "Mesure la longueur du poisson encadré et la porte sur sa fiche",
             zoom=True),
        Mark("Valider comptage",
             "Valide le comptage de l'image affichée, d'où sort le MaxN"),
    ]),

    Shot("05b-mesure-videos", "Mesure, section Vidéos", [
        Mark("Caméra gauche", "La paire annotée, reprise de la Synchronisation",
             span="Caméra droite"),
        Mark("Calibration utilisée", "Quelle calibration sert réellement à mesurer", zoom=True),
    ]),

    Shot("05c-mesure-detection", "Mesure, section Détection IA", [
        Mark("Modèle", "Le modèle de détection actif", zoom=True),
        Mark("Gérer les modèles…", "Installer, comparer ou changer de modèle"),
        Mark("Détection poisson (IA)", "Active ou coupe les propositions de l'IA"),
        Mark("Détecter à chaque pause", "Relance la détection à chaque arrêt de la lecture"),
        Mark("Confiance min (%)", "Bas : l'IA propose beaucoup, dont des erreurs", zoom=True),
        Mark("Détecter sur cette image", "Lance l'IA sur l'image affichée"),
    ]),

    # Le cycle « Début / Fin et analyser » a été retiré : il écrivait un
    # intervalle de broutage pour produire une piste. Reste le suivi In/Out,
    # qui ne produit que la piste et la rattache à la fiche du poisson.
    Shot("05d-mesure-comportement", "Mesure, section Comportement et suivi", [
        Mark("Comportement", "Le type d'événement à noter", kind="AppLabel"),
        Mark("Début (In)", "Marque l'image où commence le suivi", zoom=True),
        Mark("Fin (Out)",
             "Marque la dernière image, suit le poisson et rattache la piste à sa fiche",
             zoom=True),
    ]),

    Shot("05e-mesure-affichage", "Mesure, section Affichage et mesure", [
        Mark("Lignes de contrôle (épipolaires)",
             "Vérifie qu'un même point est à la même hauteur dans les deux images", zoom=True),
        Mark("Effacer points", "Recommence la mesure au premier point"),
    ]),

    # Le sous-onglet « Session » porte les deux moitiés du travail de
    # relecture : le registre à gauche, le bilan et l'export à droite. Les deux
    # captures ci-dessous montrent donc le même écran, annoté d'un côté puis de
    # l'autre.
    # Un repère ne peut viser qu'un élément instancié en dur : `findChildren`
    # ne voit pas les délégués créés par un Repeater ou une ListView. Les
    # compteurs de session et les lignes du tableau en font partie.
    Shot("06-registre", "Données et IA, onglet Session (registre)", [
        Mark("Registre poissons", "Le registre de la sortie, une ligne par poisson"),
        Mark("Valider taxon", "Les trois actions sur la ligne sélectionnée",
             span="Voir dans la vidéo", zoom=True),
        Mark("Édition taxon", "L'identification, rang par rang. NA est une décision, pas un vide"),
        Mark("Frame", "L'en-tête du tableau : image, événement, taxon, taille",
             span="Taille (mm)", zoom=True),
    ]),

    Shot("06c-exports", "Données et IA, onglet Session (résumé et export)", [
        Mark("Résumé et export", "Le bilan de la sortie, puis son export"),
        Mark("Format d'export", "Le format du paquet à produire"),
        Mark("Exporter en COCO", "Lance l'export", zoom=True),
        Mark("Réglages avancés", "La version du dataset se règle ici", zoom=True),
        Mark("Ouvrir Fishial", "Les images Fishial partent de l'autre onglet"),
    ]),

    Shot("06c2-exports-galerie", "Données et IA, onglet Fishial", [
        Mark("Bibliothèque Fishial locale",
             "Les espèces dont l'application garde des vignettes"),
        Mark("Actualiser", "Relit la bibliothèque", zoom=True),
        Mark("Exporter les images Fishial",
             "Sort la bibliothèque entière dans un dossier", zoom=True),
    ]),

    Shot("07-hub-pro", "Hub Pro", [
        Mark("Téléchargement", "Récupérer un jeu de données public", kind="QQuickText"),
        Mark("Import", "Réimporter un export CVAT dans la base", kind="QQuickText"),
        Mark("Entraînement", "Relancer un entraînement du détecteur", kind="QQuickText"),
        Mark("Audit", "Recompter un dataset déjà produit", kind="QQuickText"),
    ]),

    Shot("08-sessions", "Page Sessions", [
        Mark("Annotateur", "Qui annote. Cette identité signe chaque validation",
             span="Changer…", zoom=True),
        Mark("Nouvelle session", "Prépare une sortie : lieu, date, notes", zoom=True),
        Mark("Où sont les fichiers", "Où sont réellement les vidéos de cette session"),
        Mark("Ouvrir cette session", "Charge la paire et le registre, puis ouvre Mesure"),
        Mark("Attacher la paire chargée",
             "Rattache les vidéos chargées et fige le décalage de synchronisation", zoom=True),
    ]),

    Shot("09-parametres", "Préférences", [
        Mark("Modifier…", "Choisir une autre racine pour vos données", zoom=True),
        Mark("Base d'annotations", "Les quatre emplacements réels, et leur taille"),
    ]),

    Shot("09b-parametres-sauvegarde", "Préférences, sauvegarde et comportements", [
        Mark("Sauvegarder maintenant",
             "La sauvegarde est manuelle : personne ne la fait à votre place", zoom=True),
        Mark("Nom du comportement", "Créer un type de comportement", zoom=True),
    ]),

    Shot("10-annotateur", "Dialogue « Qui annote ? »", [
        Mark("Nom affiché", "Le nom enregistré avec chaque identification", zoom=True),
        Mark("ORCID", "Facultatif, mais il lève toute ambiguïté lors d'une publication"),
        Mark("C'est moi", "Valide l'identité", zoom=True),
    ]),

    Shot("11-modeles-detection", "Gestionnaire de modèles", [
        Mark("Catalogue", "Trois onglets : catalogue, comparaison, ajout", span="Étendre",
             zoom=True),
        Mark("Actualiser", "Relit le catalogue"),
    ]),

    Shot("12-mire-charuco", "Réglages de la mire ChArUco", [
        Mark("Colonnes (X)", "La géométrie de la planche imprimée", span="Lignes (Y)",
             zoom=True),
        Mark("Case (mm)", "La taille de case est la seule source de l'échelle en millimètres",
             span="Marqueur (mm)", zoom=True),
        Mark("Dictionnaire ArUco", "Doit correspondre à la planche réellement imprimée"),
    ]),

    Shot("13-reglages-calibration", "Réglages de la calibration", [
        Mark("Saut sans mire", "La finesse du balayage des vidéos", span="Pas en rafale (1/N)"),
        Mark("Max vues / caméra", "Combien d'images alimentent le calcul",
             span="Max paires stéréo", zoom=True),
    ]),
]


# ─────────────────────────────────────────────────────────────────────────────
#  Résolution des repères
# ─────────────────────────────────────────────────────────────────────────────

def _best_match(items: list[dict], label: str, kind: str) -> dict | None:
    """Élément portant ce libellé, le plus grand d'abord.

    Un bouton contient un texte : les deux portent le même libellé. Le plus
    grand des deux est le contrôle, c'est celui qu'il faut encadrer.
    """
    hits = [e for e in items if e["text"] == label]
    if kind:
        hits = [e for e in hits if e["kind"] == kind]
    if not hits:
        return None
    return max(hits, key=lambda e: e["w"] * e["h"])


def resolve(shot: Shot, items: list[dict], size: tuple[int, int]) -> list[tuple[Mark, tuple]]:
    """Boîte de chaque repère, en pixels, dans l'ordre de la capture."""
    width, height = size
    boxes: list[tuple[Mark, tuple]] = []
    for mark in shot.marks:
        found = _best_match(items, mark.label, mark.kind)
        if found is None:
            print(f"  [!] {shot.name} : libellé introuvable « {mark.label} »")
            continue
        x, y, w, h = found["x"], found["y"], found["w"], found["h"]
        if mark.span:
            other = _best_match(items, mark.span, "")
            if other is None:
                print(f"  [!] {shot.name} : extension introuvable « {mark.span} »")
            else:
                x2 = max(x + w, other["x"] + other["w"])
                y2 = max(y + h, other["y"] + other["h"])
                x, y = min(x, other["x"]), min(y, other["y"])
                w, h = x2 - x, y2 - y
        box = (x - PAD, y - PAD, w + 2 * PAD, h + 2 * PAD)
        if box[1] + box[3] > height or box[0] + box[2] > width:
            print(f"  [!] {shot.name} : « {mark.label} » hors de l'image (contenu défilé)")
            continue
        boxes.append((mark, box))
    return boxes


# ─────────────────────────────────────────────────────────────────────────────
#  Dessin
# ─────────────────────────────────────────────────────────────────────────────

def draw_marker(img: Image.Image, number: int, box: tuple, anchor: str) -> None:
    x, y, w, h = box
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    # Halo sombre sous le trait ambre : lisible sur fond clair comme sur fond sombre.
    od.rounded_rectangle([x - 2, y - 2, x + w + 2, y + h + 2], radius=8,
                         outline=(0, 0, 0, 130), width=STROKE + 4)
    od.rounded_rectangle([x, y, x + w, y + h], radius=6,
                         outline=MARK + (255,), width=STROKE)
    img.alpha_composite(overlay)

    cx = x if anchor in ("tl", "bl") else x + w
    cy = y if anchor in ("tl", "tr") else y + h
    cx = max(R, min(img.width - R, cx))
    cy = max(R, min(img.height - R, cy))
    d = ImageDraw.Draw(img)
    d.ellipse([cx - R, cy - R, cx + R, cy + R], fill=(0, 0, 0, 150))
    d.ellipse([cx - R + 3, cy - R + 3, cx + R - 3, cy + R - 3], fill=MARK)
    f = font(20)
    text = str(number)
    bbox = d.textbbox((0, 0), text, font=f)
    d.text((cx - (bbox[2] - bbox[0]) / 2, cy - (bbox[3] - bbox[1]) / 2 - 2),
           text, font=f, fill=TEXT_ON_MARK)


def build_zoom_sheet(base: Image.Image, shot: Shot,
                     boxes: list[tuple[Mark, tuple]]) -> Image.Image | None:
    zooms = [(i + 1, m, b) for i, (m, b) in enumerate(boxes) if m.zoom]
    if not zooms:
        return None

    scale = shot.zoom_scale
    pad, gap, cap_h = 18, 16, 34
    tiles = []
    for number, mark, (x, y, w, h) in zooms:
        crop = base.crop((max(0, x), max(0, y),
                          min(base.width, x + w), min(base.height, y + h)))
        crop = crop.resize((int(crop.width * scale), int(crop.height * scale)),
                           Image.LANCZOS)
        tiles.append((number, mark, crop))

    total_w = pad * 2 + sum(t.width for _, _, t in tiles) + gap * (len(tiles) - 1)
    total_h = pad * 2 + cap_h + max(t.height for _, _, t in tiles)

    sheet = Image.new("RGBA", (total_w, total_h), ZOOM_BG + (255,))
    d = ImageDraw.Draw(sheet)
    fx = pad
    for number, mark, tile in tiles:
        ty = pad + cap_h
        sheet.paste(tile, (fx, ty))
        d.rounded_rectangle([fx - 2, ty - 2, fx + tile.width + 2, ty + tile.height + 2],
                            radius=6, outline=ZOOM_FRAME, width=3)
        cx, cy = fx + R + 2, pad + cap_h // 2 - 2
        d.ellipse([cx - R, cy - R, cx + R, cy + R], fill=MARK)
        f = font(20)
        bb = d.textbbox((0, 0), str(number), font=f)
        d.text((cx - (bb[2] - bb[0]) / 2, cy - (bb[3] - bb[1]) / 2 - 2),
               str(number), font=f, fill=TEXT_ON_MARK)
        d.text((cx + R + 10, cy - 10), mark.caption[:60], font=font(16), fill=LABEL_FG)
        fx += tile.width + gap
    return sheet


def main() -> int:
    if not GEOMETRY.is_file():
        print(f"[!] {GEOMETRY.name} absent : lancez d'abord capture_screenshots.py")
        return 1
    geometry = json.loads(GEOMETRY.read_text(encoding="utf-8"))

    OUT.mkdir(parents=True, exist_ok=True)
    legend_lines = ["# Légendes des captures annotées", "",
                    "Généré par `annotate_screenshots.py`. À citer tel quel dans le manuel.",
                    ""]
    missing = 0

    for shot in SHOTS:
        src = SRC / f"{shot.name}.png"
        if not src.is_file():
            src = SRC / f"{shot.name}.jpg"
        if not src.is_file():
            print(f"[!] Capture absente : {src.name}")
            missing += 1
            continue
        items = geometry.get(shot.name)
        if not items:
            print(f"[!] Aucun repère relevé pour {shot.name}")
            missing += 1
            continue

        base = Image.open(src).convert("RGBA")
        boxes = resolve(shot, items, base.size)
        if not boxes:
            print(f"[!] {shot.name} : aucun repère résolu")
            missing += 1
            continue

        annotated = base.copy()
        for index, (mark, box) in enumerate(boxes, start=1):
            draw_marker(annotated, index, box, mark.anchor)
        out_a = OUT / f"{shot.name}-annote.png"
        annotated.convert("RGB").save(out_a, optimize=True)

        sheet = build_zoom_sheet(base, shot, boxes)
        if sheet is not None:
            sheet.convert("RGB").save(OUT / f"{shot.name}-zoom.png", optimize=True)

        legend_lines.append(f"## {shot.title} (`{shot.name}`)")
        legend_lines.append("")
        legend_lines.append("Repères : " + " ; ".join(
            f"**{i}** {m.caption}" for i, (m, _) in enumerate(boxes, start=1)) + ".")
        legend_lines.append("")
        print(f"  -> {out_a.name}  ({len(boxes)} repère(s))")

    (OUT / "legendes.md").write_text("\n".join(legend_lines) + "\n", encoding="utf-8")
    print(f"Captures annotées dans {OUT}")
    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
