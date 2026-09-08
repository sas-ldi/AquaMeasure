import QtQuick
import QtQuick.Layouts
import AquaMeasure

Rectangle {
    id: card
    property string title: ""
    property string subtitle: ""
    property string iconName: "settings"
    property string status: "pending" // ok | warn | error | pending

    readonly property color statusColor: {
        switch (status) {
        case "ok": return Theme.success
        case "warn": return Theme.warning
        case "error": return Theme.error
        default: return Theme.pending
        }
    }

    color: Theme.elevated
    radius: Theme.radiusMd
    border.color: Theme.border2
    border.width: 1
    implicitWidth: 220
    implicitHeight: 116

    Rectangle {
        anchors.left: parent.left
        anchors.top: parent.top
        anchors.bottom: parent.bottom
        width: 4
        radius: Theme.radiusSm
        color: card.statusColor
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: Theme.spaceLg
        anchors.leftMargin: Theme.spaceLg + 10
        spacing: Theme.spaceMd

        RowLayout {
            spacing: Theme.spaceSm
            Layout.fillWidth: true
            WorkspaceIcon { name: card.iconName }
            Rectangle {
                width: 8
                height: 8
                radius: 4
                color: card.statusColor
            }
            AppLabel {
                Layout.fillWidth: true
                text: card.title
                font.pixelSize: Theme.fontCaption
                font.weight: Font.Medium
                color: Theme.textMuted
            }
        }

        AppLabel {
            text: card.subtitle
            Layout.minimumWidth: 0
            elide: Text.ElideRight
            font.pixelSize: Theme.fontTitle
            font.weight: Font.DemiBold
            color: Theme.text
            Layout.fillWidth: true
        }
    }
}
