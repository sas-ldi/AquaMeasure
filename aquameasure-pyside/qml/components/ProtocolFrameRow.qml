import QtQuick
import QtQuick.Layouts
import AquaMeasure

Rectangle {
    id: root

    property string label: ""
    property string frameText: ""
    property string note: ""
    property bool copyEnabled: true

    signal copyRequested(string frameText)

    Layout.fillWidth: true
    implicitHeight: row.implicitHeight + Theme.s3 * 2
    radius: Theme.radiusSm
    color: Theme.bgElevated
    border.color: Theme.border
    border.width: 1

    RowLayout {
        id: row
        anchors.fill: parent
        anchors.margins: Theme.s3
        spacing: Theme.s3

        ColumnLayout {
            Layout.fillWidth: true
            spacing: Theme.s1

            Text {
                text: root.label
                font.family: Theme.fontFamily
                font.pixelSize: Theme.fzSm
                font.weight: Font.DemiBold
                color: Theme.text
            }

            Text {
                text: root.frameText
                font.family: Theme.monoFamily
                font.pixelSize: Theme.fzSm
                color: Theme.accentText
            }

            Text {
                visible: root.note.length > 0
                text: root.note
                font.family: Theme.fontFamily
                font.pixelSize: Theme.fzXs
                color: Theme.textDim
                wrapMode: Text.WordWrap
                Layout.fillWidth: true
            }
        }

        GhostButton {
            visible: root.copyEnabled
            text: qsTr("Copier")
            small: true
            onClicked: root.copyRequested(root.frameText)
        }
    }
}
