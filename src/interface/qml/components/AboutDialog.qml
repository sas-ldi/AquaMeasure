import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import AquaMeasure

Dialog {
    id: dlg
    title: qsTr("À propos")
    modal: true
    anchors.centerIn: parent
    width: Math.min(420, parent ? parent.width - 48 : 420)

    background: Rectangle {
        color: Theme.elevated
        radius: Theme.radiusMd
        border.color: Theme.border
        border.width: 1
    }

    contentItem: ColumnLayout {
        spacing: Theme.s3
        AppLabel {
            text: qsTr("AquaMeasure")
            font.pixelSize: Theme.fzLg
            font.weight: Font.DemiBold
        }
        AppLabel {
            text: qsTr("Mesure stéréo poisson - IRD / LDI")
            color: Theme.textMuted
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }
        AppLabel {
            text: qsTr("PySide6 + QML · OpenCV · YOLO · Fishial")
            color: Theme.textDim
            font.pixelSize: Theme.fzSm
        }
    }

    footer: DialogButtonBox {
        standardButtons: DialogButtonBox.Ok
        onAccepted: dlg.close()
    }
}
