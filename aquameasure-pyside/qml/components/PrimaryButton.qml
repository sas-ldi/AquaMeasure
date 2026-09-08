import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import AquaMeasure

// Bouton plein bleu (action principale d'une section).
//
// Ce fichier avait disparu de qml/components/ : il ne survivait plus que dans
// le module généré qml/AquaMeasure/ (que main.py recopie sans jamais nettoyer),
// alors que ContextSidePanel, CalibResultsPanel et FrameAbundanceBar l'utilisent.
//
// `requires` : voir GhostButton - grise le bouton tout en le laissant expliquer
// pourquoi l'action est indisponible.
Rectangle {
    id: root
    property string text:    ""
    property bool   small:   false

    property bool   requires: true
    property string disabledReason: ""
    property string tooltipText: ""

    // `attention` : une modification attend d'etre enregistree. Le bouton
    // prend un liseré ambre qui respire doucement, pour qu'on sache d'un
    // coup d'oeil que ce qui est a l'ecran n'est pas encore memorise.
    property bool   attention: false

    readonly property bool actionable: root.enabled && root.requires
    readonly property bool _alerting: root.attention && root.actionable

    signal clicked()

    Layout.maximumWidth: Theme.buttonMaxWidth

    // Plancher de largeur : le bouton ne doit jamais disparaitre, mais il n'a
    // plus a contenir tout son libelle - celui-ci s'elide (voir GhostButton).
    Layout.minimumWidth: Math.min(lbl.implicitWidth + Theme.s3, Theme.buttonMinWidth)

    implicitHeight: root.small ? 28 : Theme.controlH
    implicitWidth:  lbl.implicitWidth + (root.small ? Theme.s5 * 2 : Theme.s6 * 2)
    radius: Theme.radiusSm

    color: {
        if (!root.actionable) return Theme.surfaceDisabled
        if (ma.pressed)       return Theme.accentHover
        if (ma.containsMouse) return Theme.accentHover
        return Theme.accent
    }
    border.width: root._alerting ? 2 : (root.actionable ? 0 : 1)
    border.color: root._alerting ? Theme.warn : Theme.borderDisabled
    Behavior on color { ColorAnimation { duration: Theme.motionFast } }

    SequentialAnimation on opacity {
        running: root._alerting
        loops: Animation.Infinite
        alwaysRunToEnd: true
        NumberAnimation { to: 0.72; duration: 700; easing.type: Theme.easeOut }
        NumberAnimation { to: 1.0;  duration: 700; easing.type: Theme.easeOut }
        onRunningChanged: if (!running) root.opacity = 1
    }

    Text {
        id: lbl
        anchors.centerIn: parent
        // Voir GhostButton : le libelle reste dans son fond, quitte a s'elider.
        width: Math.min(implicitWidth, Math.max(0, root.width - Theme.s2))
        horizontalAlignment: Text.AlignHCenter
        elide: Text.ElideRight
        text: root.text
        font.family: Theme.fontFamily
        font.pixelSize: root.small ? Theme.fzSm : Theme.fzBase
        font.weight: Font.DemiBold
        color: root.actionable ? "#ffffff" : Theme.textDisabled
    }

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
