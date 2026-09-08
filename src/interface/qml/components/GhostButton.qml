import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import AquaMeasure

// Bouton contour transparent (toolbar).
//
// Deux façons de neutraliser le bouton :
//   • enabled: false        → grisé, non survolable (Qt désactive les enfants).
//   • requires: <condition> → grisé mais SURVOLABLE et CLIQUABLE : le clic
//     affiche `disabledReason` au lieu d'agir. À préférer dès qu'un utilisateur
//     peut légitimement se demander pourquoi le bouton ne répond pas.
Rectangle {
    id: root
    property string text:    ""
    property bool   small:   false
    property bool   active:  false
    property bool   fill:    false

    // Condition métier. false = action indisponible, mais le bouton reste
    // interactif pour pouvoir expliquer.
    property bool   requires: true
    property string disabledReason: ""
    property string tooltipText: ""

    readonly property bool actionable: root.enabled && root.requires

    signal clicked()

    Layout.fillWidth: fill
    Layout.maximumWidth: fill ? -1 : Theme.buttonMaxWidth

    // Plancher de largeur. En mode `fill` le bouton ne declare aucune largeur
    // preferee (implicitWidth 0) : place dans un layout avec un voisin qui en
    // declare une, Qt repartit l'espace au prorata des largeurs preferees et
    // reduit celui-ci a 0 px. Le bouton disparait alors completement.
    // Le plancher ne vaut plus la largeur du libelle : celui-ci s'elide, et
    // deux boutons a libelle long imposaient sinon plus de 400 px au volet.
    Layout.minimumWidth: Math.min(lbl.implicitWidth + Theme.s3, Theme.buttonMinWidth)

    implicitHeight: root.small ? 26 : Theme.controlH
    implicitWidth: root.fill ? 0 : (lbl.implicitWidth + (root.small ? Theme.s3 * 2 : Theme.s4 * 2))
    radius: Theme.radiusSm

    color: {
        if (!root.actionable) return Theme.surfaceDisabled
        if (root.active)      return Theme.accentSoft
        if (ma.pressed)       return Theme.surfaceActive
        if (ma.containsMouse) return Theme.surfaceHover
        return "transparent"
    }

    border.color: {
        if (!root.actionable) return Theme.borderDisabled
        return root.active ? Theme.accent : Theme.border
    }
    border.width: 1

    Behavior on color { ColorAnimation { duration: Theme.motionFast } }

    Text {
        id: lbl
        anchors.centerIn: parent
        // Le libelle ne peut plus depasser son propre fond. Sans cette borne,
        // un bouton reduit en dessous de sa largeur naturelle laissait son
        // texte centre deborder de chaque cote, puis se faire rogner par le
        // volet : c'est ce que le client voyait dans le panneau de droite.
        width: Math.min(implicitWidth, Math.max(0, root.width - Theme.s2))
        horizontalAlignment: Text.AlignHCenter
        elide: Text.ElideRight
        text: root.text
        font.family: Theme.fontFamily
        font.pixelSize: root.small ? Theme.fzSm : Theme.fzBase
        color: {
            if (!root.actionable) return Theme.textDisabled
            if (root.active)      return Theme.accentText
            return Theme.textMuted
        }
    }

    // Affichage forcé de l'infobulle après un clic sur un bouton indisponible.
    property bool _tipPinned: false
    Timer {
        id: tipTimer
        interval: 3200
        onTriggered: root._tipPinned = false
    }

    MouseArea {
        id: ma
        anchors.fill: parent
        hoverEnabled: true
        cursorShape: root.actionable ? Qt.PointingHandCursor : Qt.ArrowCursor
        onClicked: {
            if (root.actionable) {
                root.clicked()
            } else if (root.disabledReason.length > 0) {
                // Clic sur un bouton indisponible : on explique au lieu d'ignorer.
                root._tipPinned = true
                tipTimer.restart()
            }
        }
    }

    ToolTip {
        delay: root._tipPinned ? 0 : 400
        visible: (ma.containsMouse || root._tipPinned) && text.length > 0
        text: root.actionable
            ? root.tooltipText
            : (root.disabledReason.length > 0 ? root.disabledReason : root.tooltipText)
    }
}
