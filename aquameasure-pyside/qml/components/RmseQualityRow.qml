import QtQuick
import QtQuick.Layouts
import AquaMeasure

// Ligne métrique : libellé | pastille qualité | valeur (colonnes fixes, pas de chevauchement).
GridLayout {
    id: root
    property string title: ""
    property real rmse: -1
    property bool computing: false

    readonly property int tier: rmse >= 0 ? Calib.rmseQualityTier(rmse) : 0
    readonly property string tierLabel: Calib.rmseQualityLabel(rmse)
    readonly property color valueColor: Calib.rmseQualityColor(rmse)

    columns: 3
    columnSpacing: Theme.s3
    rowSpacing: 0
    Layout.fillWidth: true

    AppLabel {
        text: title
        color: Theme.textMuted
        font.pixelSize: Theme.fontCaption
        elide: Text.ElideRight
        Layout.fillWidth: true
        Layout.preferredWidth: Theme.formLabelWidth
        Layout.maximumWidth: Theme.formLabelWidth
        Layout.column: 0
        Layout.row: 0
    }

    Rectangle {
        Layout.column: 1
        Layout.row: 0
        Layout.alignment: Qt.AlignLeft | Qt.AlignVCenter
        Layout.preferredHeight: 24
        implicitWidth: pillText.implicitWidth + Theme.s3 * 2
        width: implicitWidth
        radius: Theme.radiusFull
        color: computing ? Theme.accentSoft : Calib.rmseQualityBg(rmse)
        border.color: computing ? Theme.accent : valueColor
        border.width: 1
        opacity: (rmse >= 0 || computing) ? 1 : 0.35

        Text {
            id: pillText
            anchors.centerIn: parent
            text: computing ? qsTr("Calcul…") : (rmse >= 0 ? tierLabel : qsTr("En attente"))
            color: computing ? Theme.accentText : valueColor
            font.family: Theme.fontFamily
            font.pixelSize: Theme.fzXs
            font.weight: Font.DemiBold
        }

        SequentialAnimation on opacity {
            running: root.computing
            loops: Animation.Infinite
            NumberAnimation { to: 0.55; duration: Theme.motionBase; easing.type: Easing.InOutSine }
            NumberAnimation { to: 1.0; duration: Theme.motionBase; easing.type: Easing.InOutSine }
        }
    }

    Text {
        Layout.column: 2
        Layout.row: 0
        Layout.alignment: Qt.AlignRight | Qt.AlignVCenter
        Layout.preferredWidth: Theme.metricValueWidth
        Layout.minimumWidth: Theme.metricValueWidth
        horizontalAlignment: Text.AlignRight
        text: rmse >= 0 ? qsTr("%1 px").arg(rmse.toFixed(3)) : (computing ? "…" : "-")
        color: computing ? Theme.accentText : valueColor
        font.family: Theme.monoFamily
        font.pixelSize: Theme.fzSm
        font.weight: Font.DemiBold
    }
}
