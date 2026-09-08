import QtQuick
import QtQuick.Controls
import AquaMeasure

Item {
    id: root
    property string label: ""
    property bool active: false
    signal clicked()

    implicitHeight: 44
    implicitWidth: parent ? parent.width : 200

    Rectangle {
        id: bg
        anchors.fill: parent
        radius: Theme.radiusSm
        color: root.active ? Theme.surfaceActive : (mouse.containsMouse ? Theme.surfaceHover : "transparent")
        Behavior on color { ColorAnimation { duration: Theme.motionFast } }

        Rectangle {
            anchors.left: parent.left
            anchors.verticalCenter: parent.verticalCenter
            width: 3
            height: parent.height * 0.55
            radius: 2
            color: Theme.accent
            opacity: root.active ? 1 : 0
            Behavior on opacity { NumberAnimation { duration: Theme.motionBase } }
        }

        AppLabel {
            anchors.left: parent.left
            anchors.leftMargin: Theme.spaceMd + 6
            anchors.verticalCenter: parent.verticalCenter
            text: root.label
            font.pixelSize: Theme.fontBody
            font.weight: root.active ? Font.DemiBold : Font.Normal
            color: root.active ? Theme.text : (mouse.containsMouse ? Theme.text : Theme.textMuted)
            Behavior on color { ColorAnimation { duration: Theme.motionFast } }
        }
    }

    MouseArea {
        id: mouse
        anchors.fill: parent
        hoverEnabled: true
        cursorShape: Qt.PointingHandCursor
        onClicked: root.clicked()
    }
}
