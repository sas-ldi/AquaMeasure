import QtQuick
import QtQuick.Layouts
import AquaMeasure

// Carte repliable - en-tête cliquable + contenu masquable.
Rectangle {
    id: root
    default property alias content: body.data
    property string title: ""
    property string iconName: "settings"
    property string subtitle: ""
    property bool expanded: true
    property bool fillWidth: false
    property int minLayoutWidth: 0

    clip: true
    color: Theme.elevated
    radius: Theme.radius
    border.color: Theme.border2
    border.width: 1

    Layout.fillWidth: root.fillWidth
    Layout.minimumWidth: root.minLayoutWidth > 0 ? root.minLayoutWidth : implicitWidth

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: Theme.spaceMd
        spacing: Theme.s2

        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: headerRow.implicitHeight + Theme.s2
            color: "transparent"

            RowLayout {
                id: headerRow
                anchors.fill: parent
                spacing: Theme.s2

                WorkspaceIcon { name: root.iconName; visible: root.title.length > 0 }

                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 2
                    visible: root.title !== ""
                    AppLabel {
                        text: root.title
                        font.pixelSize: Theme.fzBase
                        font.weight: Font.DemiBold
                    }
                    AppLabel {
                        visible: root.subtitle !== ""
                        text: root.subtitle
                        color: Theme.textMuted
                        font.pixelSize: Theme.fzXs
                        elide: Text.ElideRight
                        Layout.fillWidth: true
                    }
                }

                AppLabel {
                    text: root.expanded ? "▾" : "▸"
                    color: Theme.textMuted
                    font.pixelSize: Theme.fzSm
                }
            }

            MouseArea {
                anchors.fill: parent
                cursorShape: Qt.PointingHandCursor
                onClicked: root.expanded = !root.expanded
            }
        }

        ColumnLayout {
            id: body
            Layout.fillWidth: true
            visible: root.expanded
            spacing: Theme.s2
        }
    }
}
