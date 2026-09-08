import QtQuick
import QtQuick.Controls
import AquaMeasure

TextField {
    id: control
    implicitHeight: 40
    leftPadding: Theme.spaceMd
    rightPadding: Theme.spaceMd
    topPadding: 0
    bottomPadding: 0
    verticalAlignment: Text.AlignVCenter
    color: Theme.text
    selectionColor: Theme.accent
    selectedTextColor: "#FFFFFF"
    font.family: Theme.fontFamily
    font.pixelSize: Theme.fontBody
    placeholderTextColor: Theme.textDim

    palette {
        base: Theme.bgElevated
        window: Theme.bgElevated
        text: Theme.text
        placeholderText: Theme.textDim
    }

    background: Rectangle {
        implicitHeight: 40
        radius: Theme.radiusSm
        color: Theme.bgElevated
        border.width: control.activeFocus ? 2 : 1
        border.color: control.activeFocus ? Theme.accentBlueSoft : Theme.border
        Behavior on border.color { ColorAnimation { duration: Theme.motionFast } }
    }
}
