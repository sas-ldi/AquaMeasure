import QtQuick
import AquaMeasure

// Bouton icône compact (annulation / fermeture).
Rectangle {
    id: root
    property string glyph: "✕"

    signal clicked()

    implicitWidth:  Theme.controlH
    implicitHeight: Theme.controlH
    radius: Theme.radiusSm

    color: {
        if (!root.enabled) return Theme.panel2
        if (ma.pressed)    return Theme.dangerSoft
        if (ma.containsMouse) return Theme.dangerSoft
        return Theme.panel2
    }

    border.color: {
        if (!root.enabled) return Theme.border
        if (ma.containsMouse || ma.pressed) return Theme.danger
        return Theme.border2
    }
    border.width: 1

    Behavior on color { ColorAnimation { duration: Theme.motionFast } }
    Behavior on border.color { ColorAnimation { duration: Theme.motionFast } }

    Text {
        anchors.centerIn: parent
        text: root.glyph
        font.family: Theme.fontFamily
        font.pixelSize: Theme.fzMd
        font.weight: Font.Medium
        color: root.enabled ? Theme.danger : Theme.textDim
    }

    MouseArea {
        id: ma
        anchors.fill: parent
        hoverEnabled: true
        cursorShape: root.enabled ? Qt.PointingHandCursor : Qt.ArrowCursor
        onClicked: if (root.enabled) root.clicked()
    }

    // Exposé pour ToolTip parent (CalibProgressPanel)
    property alias hoverActive: ma.containsMouse
}
