import QtQuick
import QtQuick.Layouts
import AquaMeasure

// Sous-groupe compact : une surface, un titre facultatif, une seule marge.
Rectangle {
    id: root
    default property alias content: contents.data
    property string title: ""
    property string iconName: "settings"
    property int padding: 10
    Layout.fillWidth: true
    Layout.minimumWidth: 0
    implicitHeight: contents.implicitHeight + padding * 2
    implicitWidth: 0
    color: Theme.panel2
    radius: Theme.radiusSm
    border.color: Theme.border2
    ColumnLayout {
        id: contents
        width: parent.width - root.padding * 2
        x: root.padding
        y: root.padding
        spacing: Theme.s2
        RowLayout {
            Layout.fillWidth: true
            visible: root.title.length > 0
            WorkspaceIcon { name: root.iconName; implicitWidth: 16; implicitHeight: 16 }
            AppLabel {
                Layout.fillWidth: true
                Layout.minimumWidth: 0
                text: root.title
                font.pixelSize: Theme.fzSm
                font.weight: Font.DemiBold
                elide: Text.ElideRight
            }
        }
    }
}
