import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import AquaMeasure

Popup {
    id: root

    modal: true
    focus: true
    closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
    padding: 0

    readonly property real modalW: Math.min(parent ? parent.width * 0.88 : 960, 1280)
    readonly property real modalH: Math.min(parent ? parent.height * 0.88 : 760, 920)

    width: modalW
    height: modalH
    x: parent ? (parent.width - width) / 2 : 40
    y: parent ? (parent.height - height) / 2 : 40

    background: Rectangle {
        color: Theme.bg
        radius: Theme.radiusMd
        border.color: Theme.border
        border.width: 1
    }

    Overlay.modal: Rectangle {
        color: Qt.rgba(0, 0, 0, 0.55)
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 0

        Rectangle {
            id: titleBar
            Layout.fillWidth: true
            Layout.preferredHeight: 40
            color: Theme.panel

            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: Theme.s3
                anchors.rightMargin: Theme.s2

                AppLabel {
                    text: qsTr("Registre session")
                    font.weight: Font.DemiBold
                    Layout.fillWidth: true
                }

                GhostButton {
                    text: "×"
                    onClicked: root.close()
                }
            }

            MouseArea {
                anchors.fill: parent
                anchors.rightMargin: 48
                cursorShape: Qt.SizeAllCursor
                property point origin
                property point start
                onPressed: function(mouse) {
                    origin = Qt.point(root.x, root.y)
                    start = Qt.point(mouse.x, mouse.y)
                }
                onPositionChanged: function(mouse) {
                    if (pressed) {
                        root.x = origin.x + mouse.x - start.x
                        root.y = origin.y + mouse.y - start.y
                    }
                }
            }
        }

        MeasureRegistryPanel {
            Layout.fillWidth: true
            Layout.fillHeight: true
            wide: true
            compact: false
            expanded: true
            showHeader: false
        }
    }
}
