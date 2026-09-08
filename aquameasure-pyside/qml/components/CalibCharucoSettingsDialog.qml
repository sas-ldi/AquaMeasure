import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import AquaMeasure

Popup {
    id: dlg
    parent: Overlay.overlay
    modal: true
    focus: true
    closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
    padding: Theme.spaceLg
    width: Math.min(460, Overlay.overlay ? Overlay.overlay.width - Theme.spaceXl * 2 : 460)

    onAboutToShow: {
        const o = Overlay.overlay
        if (!o)
            return
        x = Math.round(Math.max(0, (o.width - width) / 2))
        y = Math.round(Math.max(0, (o.height - implicitHeight) / 2))
    }

    property bool blocked: Calib.busy

    background: Rectangle {
        radius: Theme.radiusMd
        color: Theme.surface
        border.color: Theme.border
        border.width: 1
    }

    contentItem: ColumnLayout {
        width: parent.width
        spacing: Theme.spaceMd

        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.spaceSm
            AppLabel {
                Layout.fillWidth: true
                text: qsTr("Paramètres ChArUco")
                font.pixelSize: Theme.fontTitle
                font.weight: Font.DemiBold
            }
            AppButton {
                text: "✕"
                implicitWidth: 36
                implicitHeight: 32
                enabled: !blocked
                onClicked: dlg.close()
            }
        }

        AppLabel {
            Layout.fillWidth: true
            muted: true
            wrapMode: Text.WordWrap
            font.pixelSize: Theme.fontCaption
            text: qsTr("Cible physique de la mire ChArUco. Identique à celle imprimée et visible dans les vidéos.")
        }

        GridLayout {
            Layout.fillWidth: true
            columns: 2
            columnSpacing: Theme.spaceLg
            rowSpacing: Theme.spaceMd

            AppLabel {
                text: qsTr("Colonnes (X)")
                Layout.fillWidth: true
                verticalAlignment: Text.AlignVCenter
            }
            AppSpinBox {
                Layout.preferredWidth: 148
                Layout.alignment: Qt.AlignRight
                from: 3
                to: 40
                value: Settings.charucoSquaresX
                enabled: !blocked
                onValueModified: Settings.charucoSquaresX = value
            }

            AppLabel {
                text: qsTr("Lignes (Y)")
                Layout.fillWidth: true
                verticalAlignment: Text.AlignVCenter
            }
            AppSpinBox {
                Layout.preferredWidth: 148
                Layout.alignment: Qt.AlignRight
                from: 3
                to: 40
                value: Settings.charucoSquaresY
                enabled: !blocked
                onValueModified: Settings.charucoSquaresY = value
            }

            AppLabel {
                text: qsTr("Case (mm)")
                Layout.fillWidth: true
                verticalAlignment: Text.AlignVCenter
            }
            AppSpinBox {
                Layout.preferredWidth: 148
                Layout.alignment: Qt.AlignRight
                from: 1
                to: 10000
                value: Math.round(Settings.charucoSquareLengthMm * 10)
                enabled: !blocked
                textFromValue: (v, locale) => (v / 10).toFixed(1)
                valueFromText: (text, locale) => Math.round(parseFloat(text) * 10)
                onValueModified: Settings.charucoSquareLengthMm = value / 10
            }

            AppLabel {
                text: qsTr("Marqueur (mm)")
                Layout.fillWidth: true
                verticalAlignment: Text.AlignVCenter
            }
            AppSpinBox {
                Layout.preferredWidth: 148
                Layout.alignment: Qt.AlignRight
                from: 1
                to: 10000
                value: Math.round(Settings.charucoMarkerLengthMm * 10)
                enabled: !blocked
                textFromValue: (v, locale) => (v / 10).toFixed(1)
                valueFromText: (text, locale) => Math.round(parseFloat(text) * 10)
                onValueModified: Settings.charucoMarkerLengthMm = value / 10
            }

            AppLabel {
                text: qsTr("Dictionnaire ArUco")
                Layout.fillWidth: true
                verticalAlignment: Text.AlignVCenter
            }
            AppComboBox {
                Layout.preferredWidth: 148
                Layout.alignment: Qt.AlignRight
                Layout.fillWidth: false
                model: Settings.arucoDictNames
                currentIndex: Math.max(0, model.indexOf(Settings.charucoDictName))
                enabled: !blocked
                onActivated: Settings.charucoDictName = model[currentIndex]
            }
        }

        AppLabel {
            Layout.fillWidth: true
            muted: true
            wrapMode: Text.WordWrap
            font.pixelSize: Theme.fontCaption
            visible: Settings.charucoMarkerLengthMm >= Settings.charucoSquareLengthMm
            text: qsTr("Le marqueur doit être plus petit que la case.")
            color: Theme.warning
        }

        RowLayout {
            Layout.fillWidth: true
            Layout.topMargin: Theme.spaceSm
            spacing: Theme.spaceSm

            AppButton {
                text: qsTr("Défauts")
                fill: true
                enabled: !blocked
                onClicked: Settings.resetCharucoToDefaults()
            }
            AppButton {
                text: qsTr("Fermer")
                primary: true
                fill: true
                enabled: !blocked
                onClicked: dlg.close()
            }
        }
    }
}
