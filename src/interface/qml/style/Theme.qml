pragma Singleton

import QtQuick

// AquaMeasure - tokens concept E ("Workspace montage", thème sombre pro).
// IBM Plex Sans / IBM Plex Mono. Accent bleu #2563eb.
// Aliases de compatibilité conservés pour les composants existants.
QtObject {

    // ── Surfaces ──────────────────────────────────────────────────────────
    readonly property color bg:       "#0b1015"   // fond canevas
    readonly property color panel:    "#11181f"   // barre de menu, onglets, toolbars
    readonly property color panel2:   "#131c25"   // pistes timeline, champs
    readonly property color elevated: "#161f28"   // menus déroulants, popups
    readonly property color video:    "#0e141b"   // intérieur aperçus vidéo

    // Aliases compat (anciens noms utilisés dans les composants existants)
    readonly property color bgElevated:   "#11181f"   // → panel
    readonly property color surface:      "#131c25"   // → panel2
    readonly property color surfaceHover: "#192435"
    readonly property color surfaceActive:"#1e2d42"

    // ── Bordures ──────────────────────────────────────────────────────────
    readonly property color border:      "#1a232c"
    readonly property color border2:     "#243240"
    readonly property color borderFocus: "#2d4a6e"    // compat

    // ── Texte ─────────────────────────────────────────────────────────────
    readonly property color text:      "#e6edf3"
    readonly property color textMuted: "#9fb2c0"
    readonly property color textDim:   "#7e909e"

    // ── État désactivé ────────────────────────────────────────────────────
    // textDim servait aussi d'état « grisé » : trop proche de textMuted
    // (#7e909e vs #9fb2c0), un bouton inactif était indiscernable d'un actif.
    readonly property color textDisabled:    "#4a5763"
    readonly property color surfaceDisabled: "#0e151c"
    readonly property color borderDisabled:  "#18202a"

    // ── Accent bleu ───────────────────────────────────────────────────────
    readonly property color accent:      "#2563eb"
    readonly property color accentHover: "#3b73ef"
    readonly property color accentSoft:  "#16233b"
    readonly property color accentText:  "#8db8ff"   // texte accentué sur fond sombre

    // Aliases compat
    readonly property color accentBlue:     "#2563eb"
    readonly property color accentBlueSoft: "#8db8ff"   // → accentText

    // ── Statuts ───────────────────────────────────────────────────────────
    readonly property color ok:        "#4ade80"
    readonly property color okSoft:    "#10301f"
    readonly property color warn:      "#fbbf24"
    readonly property color warnSoft:  "#33280a"
    readonly property color danger:    "#f87171"
    readonly property color dangerSoft:"#3a1414"

    // Aliases compat
    readonly property color success:    "#4ade80"    // → ok
    readonly property color successBg:  "#10301f"   // → okSoft
    readonly property color warning:    "#fbbf24"   // → warn
    readonly property color warningBg:  "#33280a"   // → warnSoft
    readonly property color error:      "#f87171"   // → danger
    readonly property color errorBg:    "#3a1414"   // → dangerSoft
    readonly property color pending:    "#64748B"
    readonly property color pendingBg:  "#1A2030"

    // ── Marqueurs timeline ────────────────────────────────────────────────
    readonly property color markIn:   "#4ade80"   // vert
    readonly property color markOut:  "#f87171"   // rouge
    readonly property color markPin:  "#fbbf24"   // ambre
    readonly property color playhead: "#ffffff"

    // ── Rayons ────────────────────────────────────────────────────────────
    readonly property int radiusSm: 6
    readonly property int radius:   9
    readonly property int radiusLg: 12
    readonly property int radiusMd: 9     // compat → radius
    readonly property int radiusFull: 999 // compat

    // ── Espacements (grille 4 px) ─────────────────────────────────────────
    readonly property int s1: 4
    readonly property int s2: 8
    readonly property int s3: 12
    readonly property int s4: 16
    readonly property int s5: 20
    readonly property int s6: 24

    // Aliases compat
    readonly property int spaceXs:  4    // → s1
    readonly property int spaceSm:  8    // → s2
    readonly property int spaceMd:  16   // → s4
    readonly property int spaceLg:  24   // → s6
    readonly property int spaceXl:  32
    readonly property int space2xl: 48

    // ── Typographie ──────────────────────────────────────────────────────
    readonly property string fontFamily:        typeof AppSansFont !== "undefined" ? AppSansFont : "Segoe UI"
    readonly property string monoFamily:        typeof AppMonoFont !== "undefined" ? AppMonoFont : "Consolas"
    readonly property string fontFamilyDisplay: typeof AppSansFont !== "undefined" ? AppSansFont : "Segoe UI"
    readonly property string canvasFontStack: '"' + fontFamily + '", "Segoe UI", sans-serif'

    readonly property int fzXs:   11
    readonly property int fzSm:   12
    readonly property int fzBase: 13
    readonly property int fzMd:   14
    readonly property int fzLg:   16
    readonly property int fzXl:   20

    // Aliases compat (anciens noms de taille)
    readonly property int fontCaption:  11   // → fzXs
    readonly property int fontBody:     14   // → fzMd
    readonly property int fontSubtitle: 16   // → fzLg
    readonly property int fontTitle:    20   // → fzXl
    readonly property int fontHero:     32

    // ── Dimensions chrome ────────────────────────────────────────────────
    readonly property int menuBarH:    38
    readonly property int tabBarH:     50
    readonly property int ribbonH:   44
    readonly property int controlH:    32
    readonly property int headerHeight: 50   // compat → tabBarH
    readonly property int sidebarWidth: 260
    readonly property int sidePanelWidth: 260

    // ── Mise en page (évite l'étirement plein écran) ─────────────────────
    readonly property int contentMaxWidth:   860
    readonly property int consoleMaxWidth:  1180
    readonly property real consoleWidthRatio: 0.88
    readonly property int buttonMaxWidth:    320
    // Plancher d'un bouton dans un layout serre. L'ancien plancher valait la
    // largeur du libelle (jusqu'a 320 px) : deux boutons cote a cote imposaient
    // alors 472 px au volet de droite, qui n'en fait que 380 - 300 replie, et
    // tout son contenu partait sous le bord. Le libelle s'elide desormais, le
    // plancher n'a donc plus a le contenir en entier.
    readonly property int buttonMinWidth:    88
    readonly property int panelInnerMaxWidth: 640
    readonly property int formLabelWidth:    132
    readonly property int previewWidth:      255
    readonly property int metricValueWidth:  72

    // ── Animations ───────────────────────────────────────────────────────
    readonly property int motionFast: 120
    readonly property int motionBase: 200
    readonly property int motionSlow: 320
    readonly property var easeOut: Easing.OutCubic
    readonly property var easeIn:  Easing.InCubic
}
