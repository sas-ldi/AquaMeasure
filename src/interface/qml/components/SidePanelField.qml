import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import AquaMeasure

ColumnLayout {
    id: root

    property string label: ""
    property string value: ""
    property string subtitle: ""
    property string placeholder: qsTr("Non sélectionné")
    property var menuItems: []
    property bool fieldEnabled: true
    // Explication (i) affichée à côté du libellé du champ.
    property string info: ""

    signal activated()
    signal menuTriggered(string actionId)

    spacing: Theme.s1
    Layout.fillWidth: true

    RowLayout {
        visible: root.label.length > 0 || root.info.length > 0
        Layout.fillWidth: true
        spacing: Theme.s1

        AppLabel {
            Layout.fillWidth: true
            text: root.label
            font.pixelSize: Theme.fzXs
            color: Theme.textDim
        }

        InfoDot {
            diameter: 14
            text: root.info
        }
    }

    Rectangle {
        id: fieldBox
        Layout.fillWidth: true
        implicitHeight: Theme.controlH + Theme.s1
        radius: Theme.radiusSm
        color: root.fieldEnabled ? Theme.panel2 : Theme.panel
        border.color: bodyMa.containsMouse && root.fieldEnabled ? Theme.border2 : Theme.border
        border.width: 1
        clip: true

        RowLayout {
            anchors.fill: parent
            anchors.leftMargin: Theme.s3
            anchors.rightMargin: Theme.s2
            spacing: 0

            Text {
                Layout.fillWidth: true
                text: root.value.length > 0 ? root.value : root.placeholder
                elide: Text.ElideMiddle
                font.family: Theme.fontFamily
                font.pixelSize: Theme.fzSm
                color: root.value.length > 0
                    ? (root.fieldEnabled ? Theme.text : Theme.textDim)
                    : Theme.textDim
            }

            Item {
                visible: root.menuItems.length > 0
                Layout.preferredWidth: 28
                Layout.fillHeight: true

                Text {
                    anchors.centerIn: parent
                    text: "▾"
                    font.pixelSize: 9
                    color: menuMa.containsMouse && root.fieldEnabled ? Theme.text : Theme.textDim
                }

                MouseArea {
                    id: menuMa
                    anchors.fill: parent
                    hoverEnabled: true
                    enabled: root.fieldEnabled && root.menuItems.length > 0
                    cursorShape: enabled ? Qt.PointingHandCursor : Qt.ArrowCursor
                    onClicked: root.openFieldMenu()
                }
            }
        }

        MouseArea {
            id: bodyMa
            anchors.fill: parent
            anchors.rightMargin: root.menuItems.length > 0 ? 28 : 0
            hoverEnabled: true
            enabled: root.fieldEnabled
            cursorShape: enabled ? Qt.PointingHandCursor : Qt.ArrowCursor
            onClicked: root.activated()
        }
    }

    AppLabel {
        visible: root.subtitle.length > 0
        Layout.fillWidth: true
        text: root.subtitle
        font.pixelSize: Theme.fzXs
        color: root.subtitle.indexOf("Sync") >= 0 ? Theme.ok : Theme.textDim
        wrapMode: Text.WordWrap
    }

    function openFieldMenu() {
        if (root.menuItems.length === 0)
            return
        const pos = fieldBox.mapToItem(fieldPopup.parent, 0, fieldBox.height)
        fieldPopup.x = pos.x
        fieldPopup.y = pos.y + Theme.s1
        fieldPopup.width = Math.max(220, fieldBox.width)
        fieldPopup.open()
    }

    Popup {
        id: fieldPopup
        parent: Overlay.overlay
        modal: true
        focus: true
        padding: Theme.s2
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside

        background: Rectangle {
            color: Theme.elevated
            radius: Theme.radiusSm
            border.color: Theme.border
            border.width: 1
        }

        contentItem: ColumnLayout {
            spacing: Theme.s1
            width: fieldPopup.width - fieldPopup.padding * 2

            Repeater {
                model: root.menuItems

                Rectangle {
                    Layout.fillWidth: true
                    implicitHeight: 36
                    radius: Theme.radiusSm
                    color: itemMa.containsMouse ? Theme.accentSoft : "transparent"
                    opacity: modelData.enabled === false ? 0.45 : 1

                    Text {
                        anchors.fill: parent
                        anchors.leftMargin: Theme.s3
                        anchors.rightMargin: Theme.s3
                        text: modelData.text
                        verticalAlignment: Text.AlignVCenter
                        font.family: Theme.fontFamily
                        font.pixelSize: Theme.fzSm
                        color: Theme.text
                        elide: Text.ElideRight
                    }

                    MouseArea {
                        id: itemMa
                        anchors.fill: parent
                        hoverEnabled: true
                        enabled: modelData.enabled !== false
                        cursorShape: Qt.PointingHandCursor
                        onClicked: {
                            root.menuTriggered(modelData.id)
                            fieldPopup.close()
                        }
                    }
                }
            }
        }
    }
}
