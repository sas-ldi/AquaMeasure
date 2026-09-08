import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import AquaMeasure

// Pastille « i » cerclée : explique au survol à quoi sert une information ou
// une fonction, en français et sans jargon.
//
//   InfoDot { text: qsTr("Le flash sert de repère commun aux deux caméras…") }
//
// Survol → infobulle multi-ligne (largeur plafonnée à `maxTipWidth`).
// Clic → l'infobulle reste affichée quelques secondes : indispensable sur
// écran tactile, où il n'y a pas de survol. Même idiome « épinglé » que
// GhostButton / ToolButtonAM.
//
// La pastille se masque toute seule quand `text` est vide : on peut donc
// écrire `info: condition ? qsTr("…") : ""` sans gérer la visibilité.
Item {
    id: root

    property string text: ""
    property int diameter: 16
    property int maxTipWidth: 320

    readonly property bool showing: hover.hovered || root._pinned
    readonly property Item _tipOverlay: Overlay.overlay
    readonly property var _tipPlacement: {
        // `showing`, x et y forcent un nouveau calcul a chaque ouverture et
        // apres un deplacement direct de la pastille dans son conteneur.
        const positionRevision = (root.showing ? 1 : 0) + root.x + root.y
        return root._calculateTipPlacement(tip.width, tip.height,
                                           positionRevision)
    }

    implicitWidth:  root.diameter
    implicitHeight: root.diameter
    visible: root.text.length > 0
    Layout.alignment: Qt.AlignVCenter

    property bool _pinned: false

    function _calculateTipPlacement(tipWidth, tipHeight, positionRevision) {
        const gap = Theme.spaceXs
        const margin = Theme.spaceSm
        const fallback = {
            x: root.width + gap,
            y: Math.round((root.height - tipHeight) / 2),
            side: "right"
        }
        const overlay = root._tipOverlay
        if (!root.showing || !overlay || tipWidth <= 0 || tipHeight <= 0
                || !Number.isFinite(positionRevision))
            return fallback

        const anchor = root.mapToItem(overlay, 0, 0)
        const candidates = [
            { side: "right",
              score: overlay.width - margin - anchor.x - root.width
                     - gap - tipWidth },
            { side: "left",
              score: anchor.x - margin - gap - tipWidth },
            { side: "bottom",
              score: overlay.height - margin - anchor.y - root.height
                     - gap - tipHeight },
            { side: "top",
              score: anchor.y - margin - gap - tipHeight }
        ]

        // Le meilleur cote est celui qui laisse le plus d'espace libre une
        // fois l'infobulle posee. Un score negatif choisit le moindre debord.
        let best = candidates[0]
        for (let i = 1; i < candidates.length; ++i) {
            if (candidates[i].score > best.score)
                best = candidates[i]
        }

        let localX = Math.round((root.width - tipWidth) / 2)
        let localY = Math.round((root.height - tipHeight) / 2)
        if (best.side === "right")
            localX = root.width + gap
        else if (best.side === "left")
            localX = -tipWidth - gap
        else if (best.side === "bottom")
            localY = root.height + gap
        else
            localY = -tipHeight - gap

        // Le centrage sur l'autre axe ne doit jamais faire sortir la bulle de
        // l'application, notamment pour une pastille proche d'un coin.
        const maxGlobalX = Math.max(margin,
                                    overlay.width - margin - tipWidth)
        const maxGlobalY = Math.max(margin,
                                    overlay.height - margin - tipHeight)
        const globalX = Math.max(margin,
                                 Math.min(anchor.x + localX, maxGlobalX))
        const globalY = Math.max(margin,
                                 Math.min(anchor.y + localY, maxGlobalY))
        return {
            x: Math.round(globalX - anchor.x),
            y: Math.round(globalY - anchor.y),
            side: best.side
        }
    }

    Timer {
        id: pinTimer
        interval: 8000
        onTriggered: root._pinned = false
    }

    Rectangle {
        anchors.fill: parent
        radius: width / 2
        color: root.showing ? Theme.accentSoft : "transparent"
        border.color: root.showing ? Theme.accent : Theme.border2
        border.width: 1

        Behavior on color { ColorAnimation { duration: Theme.motionFast } }

        Text {
            anchors.centerIn: parent
            text: "i"
            font.family: Theme.fontFamily
            font.pixelSize: Math.round(root.diameter * 0.7)
            font.weight: Font.DemiBold
            font.italic: true
            color: root.showing ? Theme.accentText : Theme.textDim
        }
    }

    HoverHandler {
        id: hover
        cursorShape: Qt.PointingHandCursor
    }

    // ReleaseWithinBounds : le geste est capté ici, il ne déclenche donc pas
    // l'action de l'élément qui se trouve dessous (repli d'une section, etc.).
    TapHandler {
        gesturePolicy: TapHandler.ReleaseWithinBounds
        onTapped: {
            root._pinned = !root._pinned
            if (root._pinned)
                pinTimer.restart()
            else
                pinTimer.stop()
        }
    }

    // Largeur mesurée sur le texte non replié : plafonnée à maxTipWidth, elle
    // évite l'infobulle d'une seule ligne interminable comme la bulle étroite
    // et très haute qu'on obtient en fixant une largeur constante.
    TextMetrics {
        id: tipMetrics
        font.family: Theme.fontFamily
        font.pixelSize: Theme.fzSm
        text: root.text
    }

    ToolTip {
        id: tip
        // Le parent explicite evite un placement a l'origine de la page dans
        // les StackView, Loader et panneaux defilants.
        parent: root
        x: root._tipPlacement.x
        y: root._tipPlacement.y
        margins: Theme.spaceSm
        text: root.text
        visible: root.showing && root.text.length > 0
        delay: root._pinned ? 0 : 220
        font.family: Theme.fontFamily
        font.pixelSize: Theme.fzSm
        width: Math.min(root.maxTipWidth,
                        tipMetrics.width + tip.leftPadding + tip.rightPadding)
    }
}
