import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import AquaMeasure

// Tout ce qui concerne la journée de travail tient sur une seule page : le
// registre à gauche, le bilan et son unique export à droite.
Item {
    id: root

    SplitView {
        anchors.fill: parent
        orientation: Qt.Horizontal

        DataRegistryTab {
            SplitView.fillWidth: true
            SplitView.minimumWidth: 560
        }

        DataExportTab {
            SplitView.preferredWidth: Math.min(430, root.width * 0.38)
            SplitView.minimumWidth: 350
            SplitView.maximumWidth: 500
        }
    }
}
