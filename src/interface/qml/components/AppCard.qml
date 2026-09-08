import QtQuick
import QtQuick.Layouts
import AquaMeasure

Rectangle {
    id: card
    default property alias content: contentColumn.data
    property string title: ""
    property string subtitle: ""
    property string iconName: "settings"
    property bool wide: false
    // Explication (i) affichée à côté du titre de la carte.
    property string info: ""

    clip: true
    color: Theme.elevated
    radius: Theme.radius
    border.color: Theme.border2
    border.width: 1

    implicitWidth: card.wide ? -1 : Math.min(Theme.contentMaxWidth, 640)
    implicitHeight: card.wide ? -1 : contentColumn.implicitHeight + Theme.spaceLg * 2
    Layout.fillWidth: card.wide
    Layout.fillHeight: card.wide
    Layout.maximumWidth: card.wide ? -1 : Theme.contentMaxWidth

    ColumnLayout {
        id: contentColumn
        anchors.fill: parent
        anchors.margins: Theme.spaceLg
        spacing: Theme.spaceMd

        ColumnLayout {
            visible: card.title !== "" || card.info !== ""
            spacing: Theme.s2
            Layout.fillWidth: true

            RowLayout {
                Layout.fillWidth: true
                spacing: Theme.s2

                WorkspaceIcon { name: card.iconName; visible: card.title.length > 0 }

                Text {
                    text: card.title
                    font.family:    Theme.fontFamily
                    font.pixelSize: Theme.fzLg
                    font.weight:    Font.DemiBold
                    color: Theme.text
                    Layout.fillWidth: true
                    Layout.minimumWidth: 0
                    elide: Text.ElideRight
                }

                InfoDot { text: card.info }
            }
            Text {
                visible: card.subtitle !== ""
                text:    card.subtitle
                font.family:    Theme.fontFamily
                font.pixelSize: Theme.fzSm
                color: Theme.textMuted
                Layout.fillWidth: true
                wrapMode: Text.WordWrap
            }
        }
    }
}
