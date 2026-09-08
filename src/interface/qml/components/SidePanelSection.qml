import QtQuick
import QtQuick.Layouts
import AquaMeasure

// La carte couvre le titre et le contenu, même pour une section repliable.
Rectangle {
    id: root
    property string title: ""
    property string iconName: "settings"
    property bool collapsible: true
    property bool expanded: false
    property string info: ""
    property bool attention: false
    readonly property bool _hasHeader: title.length > 0
    readonly property bool _open: !_hasHeader || !collapsible || expanded
    default property alias content: body.data

    Layout.fillWidth: true
    implicitHeight: cardColumn.implicitHeight
    implicitWidth: 220
    radius: Theme.radiusMd
    color: Theme.elevated
    border.color: attention ? Theme.warn : Theme.border2
    onAttentionChanged: { if (attention) expanded = true }

    ColumnLayout {
        id: cardColumn
        width: parent.width
        spacing: 0
        Item {
            visible: root._hasHeader
            Layout.fillWidth: true
            implicitHeight: 42
            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: 12
                anchors.rightMargin: 12
                spacing: 8
                WorkspaceIcon { name: root.iconName; tint: root.attention ? Theme.warn : Theme.accentText }
                AppLabel {
                    Layout.fillWidth: true
                    Layout.minimumWidth: 0
                    text: root.title
                    font.pixelSize: Theme.fzSm
                    font.weight: Font.DemiBold
                    color: root.attention ? Theme.warn : Theme.text
                    elide: Text.ElideRight
                }
                Text {
                    visible: root.collapsible
                    text: "›"
                    rotation: root._open ? 90 : 0
                    color: Theme.textMuted
                    font.pixelSize: 18
                }
                InfoDot { text: root.info }
            }
            MouseArea {
                anchors.fill: parent
                anchors.rightMargin: root.info.length > 0 ? 36 : 0
                enabled: root.collapsible
                cursorShape: Qt.PointingHandCursor
                onClicked: root.expanded = !root.expanded
            }
        }
        ColumnLayout {
            id: body
            Layout.fillWidth: true
            Layout.minimumWidth: 0
            Layout.leftMargin: 12
            Layout.rightMargin: 12
            Layout.topMargin: root._hasHeader ? 0 : 12
            Layout.bottomMargin: 12
            spacing: Theme.s2
            visible: root._open
        }
    }
}
