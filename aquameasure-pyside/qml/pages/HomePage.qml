import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import AquaMeasure

ColumnLayout {
    spacing: Theme.spaceXl

    RowLayout {
        spacing: Theme.spaceMd
        Layout.fillWidth: true

        Rectangle {
            Layout.preferredWidth: 5
            Layout.preferredHeight: 56
            radius: 3
            color: Theme.accent
        }

        ColumnLayout {
            spacing: Theme.spaceXs
            AppLabel {
                hero: true
                text: qsTr("Mesure stéréo")
                color: Theme.text
            }
            AppLabel {
                muted: true
                text: qsTr("Poisson · mire ChArUco · prise de vues synchronisée")
                color: Theme.textMuted
            }
        }
    }

    Rectangle {
        Layout.fillWidth: true
        implicitHeight: hintCol.implicitHeight + Theme.spaceLg * 2
        radius: Theme.radiusMd
        color: Theme.surface
        border.color: Theme.border

        RowLayout {
            id: hintCol
            anchors.fill: parent
            anchors.margins: Theme.spaceLg
            spacing: Theme.spaceMd

            Rectangle {
                Layout.preferredWidth: 40
                Layout.preferredHeight: 40
                radius: Theme.radiusSm
                color: Theme.accentBlueSoft
                opacity: 0.15
                Rectangle {
                    anchors.centerIn: parent
                    width: 8
                    height: 8
                    radius: 4
                    color: Theme.accentBlueSoft
                }
            }

            AppLabel {
                Layout.fillWidth: true
                muted: true
                text: App.continueHint
                font.pixelSize: Theme.fontBody
            }
        }
    }

    RowLayout {
        spacing: Theme.s2

        AppLabel {
            text: qsTr("État du projet")
            font.pixelSize: Theme.fontCaption
            font.weight: Font.Medium
            color: Theme.textMuted
        }

        InfoDot {
            diameter: 14
            text: qsTr("L'ordre de travail est toujours le même : synchroniser les deux vidéos, calibrer la paire de caméras, puis annoter sur la page Mesure - l'IA propose des poissons, vous les identifiez, vous les mesurez et/ou vous les suivez. Chaque étape a besoin de la précédente ; les pastilles vertes ci-dessous indiquent celles qui sont faites.")
        }
    }

    RowLayout {
        spacing: Theme.spaceMd
        Layout.maximumWidth: Theme.contentMaxWidth

        AppStatusCard {
            title: qsTr("Machine")
            iconName: "device"
            subtitle: Device.connected ? qsTr("Connectée") : qsTr("Non connectée")
            status: Device.connected ? "ok" : "pending"
        }
        AppStatusCard {
            title: qsTr("Synchronisation")
            iconName: "sync"
            subtitle: App.syncOk ? qsTr("Vidéos alignées") : qsTr("À faire")
            status: App.syncOk ? "ok" : "pending"
        }
        AppStatusCard {
            title: qsTr("Calibration")
            iconName: "calibrate"
            subtitle: App.calibOk
                ? qsTr("RMSE %1 px").arg(App.stereoRmse.toFixed(1))
                : qsTr("À faire")
            status: App.calibOk
                ? (App.stereoRmse < 1.0 ? "ok" : "warn")
                : "pending"
        }
    }

    Item { Layout.fillHeight: true }

    RowLayout {
        spacing: Theme.spaceMd
        AppButton {
            text: qsTr("Continuer le workflow")
            primary: true
            implicitWidth: 240
            onClicked: App.goToContinue()
        }
        AppButton {
            text: qsTr("Machine")
            onClicked: App.currentPage = 1
        }
    }
}
