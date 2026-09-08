import QtQuick
import QtQuick.Controls
import AquaMeasure

// Bouton compact pour le ruban contextuel.
Rectangle {
    id: root
    property string text: ""
    property bool primary: false
    property bool toggle: false
    property bool checked: false
    property bool small: true
    property string infoText: ""

    signal clicked()
    signal infoHover(string text)

    implicitHeight: Theme.controlH
    implicitWidth: lbl.implicitWidth + Theme.s4 * 2
    radius: Theme.radiusSm
    // `enabled: true` était forcé ici, ce qui neutralisait toute condition
    // posée par l'appelant tant qu'elle n'était pas évaluée après coup.

    color: {
        if (!root.enabled) return Theme.surfaceDisabled
        if (root.toggle && root.checked) return Theme.accentSoft
        if (ma.pressed) return root.primary ? Theme.accentHover : Theme.surfaceActive
        if (ma.containsMouse) return root.primary ? Theme.accentHover : Theme.surfaceHover
        return root.primary ? Theme.accent : Theme.panel2
    }
    border.color: !root.enabled
        ? Theme.borderDisabled
        : (root.toggle && root.checked ? Theme.accent : Theme.border)
    border.width: 1

    Text {
        id: lbl
        anchors.centerIn: parent
        text: root.text
        font.family: Theme.fontFamily
        font.pixelSize: root.small ? Theme.fzSm : Theme.fzBase
        color: {
            if (!root.enabled) return Theme.textDisabled
            if (root.toggle && root.checked) return Theme.accentText
            if (root.primary) return "#ffffff"
            return Theme.textMuted
        }
    }

    MouseArea {
        id: ma
        anchors.fill: parent
        hoverEnabled: true
        cursorShape: root.enabled ? Qt.PointingHandCursor : Qt.ArrowCursor
        onClicked: root.clicked()
        onContainsMouseChanged: {
            if (containsMouse && root.infoText.length > 0)
                root.infoHover(root.infoText)
        }
    }
}
