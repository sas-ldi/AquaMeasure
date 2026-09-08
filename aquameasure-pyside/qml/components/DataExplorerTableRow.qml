import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import AquaMeasure

// Ligne tableau explorateur - colonnes égales basées sur tableWidth.
Item {
    id: root
    property real tableWidth: 800
    property bool isHeader: false
    property var labels: []

    implicitHeight: 32
    width: tableWidth

    RowLayout {
        anchors.fill: parent
        anchors.leftMargin: Theme.s2
        spacing: Theme.s2

        Repeater {
            model: root.labels
            delegate: AppLabel {
                Layout.fillWidth: true
                Layout.preferredWidth: 1
                Layout.minimumWidth: 48
                text: modelData
                color: root.isHeader ? Theme.textDim : Theme.text
                font.pixelSize: Theme.fzXs
                font.family: (!root.isHeader && (index === 3 || index === 4 || index === 5))
                    ? Theme.monoFamily : Theme.fontFamily
                elide: Text.ElideRight
            }
        }
    }
}
