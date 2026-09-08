import QtQuick
import QtQuick.Controls
import AquaMeasure

// Bouton icône carré 32×32.
// `requires` / `disabledReason` : voir GhostButton.
Rectangle {
    id: root
    property string glyph:   ""
    property bool   primary: false

    property bool   requires: true
    property string disabledReason: ""
    property string tooltipText: ""

    readonly property bool actionable: root.enabled && root.requires

    signal clicked()

    implicitWidth:  Theme.controlH
    implicitHeight: Theme.controlH
    radius: Theme.radiusSm

    color: {
        if (!root.actionable) return Theme.surfaceDisabled
        if (ma.pressed)    return root.primary ? Theme.accentHover : Theme.surfaceActive
        if (ma.containsMouse) return root.primary ? Theme.accent    : Theme.surfaceHover
        return root.primary ? Theme.accent : Theme.panel2
    }

    border.color: !root.actionable
        ? Theme.borderDisabled
        : (root.primary ? "transparent" : Theme.border)
    border.width: 1

    Behavior on color { ColorAnimation { duration: Theme.motionFast } }

    Text {
        anchors.centerIn: parent
        text: root.glyph
        font.pixelSize: Theme.fzBase
        color: root.actionable
            ? (root.primary ? Theme.accentText : Theme.textMuted)
            : Theme.textDisabled
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
