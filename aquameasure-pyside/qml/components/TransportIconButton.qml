import QtQuick
import QtQuick.Controls
import AquaMeasure

// Bouton carré 32×32 avec icône vectorielle de transport.
Rectangle {
    id: root

    property int glyphKind: TransportGlyph.Play
    property bool primary: false
    property string tooltipText: ""

    signal clicked()

    implicitWidth: 32
    implicitHeight: 32
    radius: Theme.radiusSm
    opacity: enabled ? 1 : 0.45

    color: {
        if (!enabled)
            return Theme.panel2
        if (ma.pressed)
            return primary ? Theme.accentHover : Theme.surfaceActive
        if (ma.containsMouse)
            return primary ? Theme.accentHover : Theme.surfaceHover
        return primary ? Theme.accent : Theme.panel2
    }
    border.color: primary ? Theme.accent : Theme.border
    border.width: 1

    TransportGlyph {
        anchors.centerIn: parent
        width: 13
        height: 13
        kind: root.glyphKind
        glyphColor: {
            if (!root.enabled)
                return Theme.textDim
            if (root.primary)
                return "#ffffff"
            return Theme.textMuted
        }
    }

    MouseArea {
        id: ma
        anchors.fill: parent
        hoverEnabled: true
        enabled: root.enabled
        cursorShape: enabled ? Qt.PointingHandCursor : Qt.ArrowCursor
        onClicked: if (root.enabled)
            root.clicked()
    }

    ToolTip.visible: ma.containsMouse && root.tooltipText.length > 0
    ToolTip.text: root.tooltipText
}
