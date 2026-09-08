import QtQuick
import QtQuick.Controls
import AquaMeasure

TextArea {
    id: control
    leftPadding: Theme.spaceMd
    rightPadding: Theme.spaceMd
    topPadding: Theme.s2
    bottomPadding: Theme.s2
    color: Theme.text
    selectionColor: Theme.accent
    selectedTextColor: "#FFFFFF"
    font.family: Theme.fontFamily
    font.pixelSize: Theme.fontBody
    placeholderTextColor: Theme.textDim
    wrapMode: TextArea.Wrap

    palette {
        base: Theme.bgElevated
        window: Theme.bgElevated
        text: Theme.text
        placeholderText: Theme.textDim
    }

    background: Rectangle {
        implicitHeight: 64
        radius: Theme.radiusSm
        color: Theme.bgElevated
        border.width: control.activeFocus ? 2 : 1
        border.color: control.activeFocus ? Theme.accentBlueSoft : Theme.border
    }
}
