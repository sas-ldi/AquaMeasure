import QtQuick
import QtQuick.Layouts
import AquaMeasure

// Ligne tableau registre poissons - en-tête ou donnée, largeurs stables.
Item {
    id: root
    property real tableWidth: 800
    property bool isHeader: false
    property bool compact: false
    property string colFrame: ""
    property string colFamily: ""
    property string colGenus: ""
    property string colSpecies: ""
    property string colMm: ""
    // Noms et décomptes, une entrée par ligne.
    property string colEvent: ""
    property string colEventColor: ""

    // Sur un écran large la ligne s'étirait sur toute la fenêtre : les trois
    // colonnes de taxon devenaient énormes et la colonne « mm », collée au
    // bord droit, sortait du champ de lecture. La ligne est donc bornée à la
    // largeur de contenu du reste de l'application, le vide restant à droite.
    property real maxRowWidth: Theme.contentMaxWidth
    readonly property real rowWidth: Math.min(tableWidth, root.maxRowWidth)

    readonly property real wFrame: root.compact ? 48 : 56
    // Assez large pour « Taille (mm) » : la colonne ne disait pas son unité,
    // et une longueur sans unité ne veut rien dire sur une fiche scientifique.
    readonly property real wTaille: 80
    // Garder les événements lisibles même quand le volet est étroit.
    readonly property real wTaxonMin: 0
    readonly property real wEventFull: root.compact ? 116 : 150
    readonly property bool showEvent: true
    readonly property real wEvent: Math.min(wEventFull,
        Math.max(88, (rowWidth - wFrame - wTaille - contentGaps) * 0.45))

    // La largeur utile exclut les deux marges de la ligne et ses espacements :
    // un par intervalle entre colonnes affichées. La marge de droite manquait,
    // et la dernière colonne se retrouvait collée au bord du volet.
    readonly property real contentGaps:
        Theme.s2 * 2 + Theme.s1 * (showEvent ? 5 : 4)
    readonly property real wTaxon: Math.max(
        wTaxonMin, (rowWidth - wFrame - wEvent - wTaille - contentGaps) / 3)

    implicitHeight: Math.max(32, eventText.implicitHeight + 10)
    width: tableWidth

    RowLayout {
        anchors.left: parent.left
        anchors.top: parent.top
        anchors.bottom: parent.bottom
        anchors.leftMargin: Theme.s2
        width: root.rowWidth - Theme.s2 * 2
        spacing: Theme.s1

        AppLabel {
            Layout.preferredWidth: root.wFrame
            text: root.colFrame
            color: root.isHeader ? Theme.textDim : Theme.text
            font.pixelSize: Theme.fzSm
            font.family: root.isHeader ? Theme.fontFamily : Theme.monoFamily
            elide: Text.ElideRight
        }
        AppLabel {
            id: eventText
            objectName: "registryEventText"
            visible: root.showEvent
            Layout.preferredWidth: root.wEvent
            Layout.maximumWidth: root.wEvent
            Layout.minimumWidth: root.wEvent
            text: root.colEvent
            textFormat: Text.PlainText
            color: root.isHeader
                ? Theme.textDim
                : (root.colEventColor.length > 0 ? root.colEventColor : Theme.text)
            font.pixelSize: Theme.fzSm
            wrapMode: Text.Wrap
            elide: Text.ElideNone
        }
        AppLabel {
            Layout.preferredWidth: root.wTaxon
            text: root.colFamily
            color: root.isHeader ? Theme.textDim : Theme.text
            font.pixelSize: Theme.fzSm
            wrapMode: Text.NoWrap
            elide: Text.ElideRight
        }
        AppLabel {
            Layout.preferredWidth: root.wTaxon
            text: root.colGenus
            color: root.isHeader ? Theme.textDim : Theme.text
            font.pixelSize: Theme.fzSm
            wrapMode: Text.NoWrap
            elide: Text.ElideRight
        }
        AppLabel {
            Layout.preferredWidth: root.wTaxon
            text: root.colSpecies
            color: root.isHeader ? Theme.textDim : Theme.text
            font.pixelSize: Theme.fzSm
            wrapMode: Text.NoWrap
            elide: Text.ElideRight
        }
        AppLabel {
            Layout.preferredWidth: root.wTaille
            text: root.colMm
            color: root.isHeader ? Theme.accentText : (root.colMm === "-" ? Theme.textDim : Theme.accent)
            font.pixelSize: Theme.fzSm
            font.family: Theme.monoFamily
            font.weight: root.isHeader || root.colMm === "-" ? Font.Normal : Font.DemiBold
            wrapMode: Text.NoWrap
            elide: Text.ElideRight
            horizontalAlignment: Text.AlignRight
        }
    }
}
