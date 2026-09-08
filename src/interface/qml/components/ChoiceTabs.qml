import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import AquaMeasure

RowLayout {
    id: root
    property var options: []
    property int currentIndex: 0
    signal chosen(int index)
    spacing: 4
    Repeater {
        model: root.options
        delegate: Button {
            required property int index
            required property string modelData
            Layout.fillWidth: true
            Layout.preferredWidth: 0
            implicitHeight: 34
            text: modelData
            Accessible.role: Accessible.PageTab
            Accessible.name: text
            background: Rectangle {
                radius: Theme.radiusSm
                color: root.currentIndex === index ? Theme.accentSoft : Theme.panel
                border.color: root.currentIndex === index ? Theme.accent : Theme.border2
                opacity: root.enabled ? 1 : 0.55
            }
            contentItem: Text {
                text: parent.text
                color: root.currentIndex === index ? Theme.accentText : Theme.textMuted
                font.family: Theme.fontFamily
                font.pixelSize: Theme.fzXs
                font.weight: root.currentIndex === index ? Font.DemiBold : Font.Normal
                horizontalAlignment: Text.AlignHCenter
                verticalAlignment: Text.AlignVCenter
                elide: Text.ElideRight
            }
            onClicked: { root.currentIndex = index; root.chosen(index) }
        }
    }
}
