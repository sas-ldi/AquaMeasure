import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import AquaMeasure

ColumnLayout {
    id: root
    property int bodyHeight: 140
    property bool expanded: Sync.step >= 2 && Sync.curvesAvailable

    readonly property int headerHeight: headerRow.implicitHeight
    readonly property int expandedLayoutHeight: headerHeight + Theme.s3 + bodyHeight

    spacing: 0
    Layout.fillWidth: true
    Layout.topMargin: Theme.s2
    Layout.preferredHeight: expanded ? expandedLayoutHeight : headerHeight
    Layout.maximumHeight: expanded ? expandedLayoutHeight : headerHeight
    visible: Sync.curvesAvailable || Sync.step >= 2

    RowLayout {
        id: headerRow
        Layout.fillWidth: true
        AppLabel {
            text: qsTr("Courbes luminosité")
            color: Theme.textMuted
            font.pixelSize: Theme.fzSm
            font.weight: Font.DemiBold
        }
        Item { Layout.fillWidth: true }
        GhostButton {
            text: root.expanded ? qsTr("Réduire") : qsTr("Afficher")
            small: true
            onClicked: root.expanded = !root.expanded
        }
    }

    Rectangle {
        Layout.fillWidth: true
        Layout.preferredHeight: bodyHeight
        visible: root.expanded
        color: Theme.panel2
        radius: Theme.radiusSm
        border.color: Theme.border
        clip: true

        Image {
            anchors.fill: parent
            anchors.margins: Theme.s2
            fillMode: Image.PreserveAspectFit
            source: Sync.curvesAvailable
                ? "image://frames/sync_curves?" + Sync.curvesTick
                : ""
            smooth: true
        }

        AppLabel {
            anchors.centerIn: parent
            visible: !Sync.curvesAvailable
            muted: true
            text: qsTr("Lancez la détection flash pour générer les courbes")
        }
    }
}
