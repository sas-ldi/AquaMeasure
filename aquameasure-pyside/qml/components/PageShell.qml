import QtQuick
import QtQuick.Layouts
import AquaMeasure

// Colonne centrée, largeur plafonnée (pages formulaire / calibration).
Item {
    id: root
    anchors.fill: parent

    readonly property real laneWidth: Math.min(Math.max(width, 1), Theme.contentMaxWidth)

    default property alias content: lane.data

    ColumnLayout {
        id: lane
        width: root.laneWidth
        height: parent.height
        anchors.horizontalCenter: parent.horizontalCenter
        spacing: 0
    }
}
