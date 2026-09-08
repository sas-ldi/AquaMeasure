import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import AquaMeasure

// Une ligne de la section « Détail » de la page Paramètres : un emplacement,
// son chemin réel, ce qu'on en sait (taille, date, nombre), un (i) qui
// explique ce qu'il contient et ce qu'on perd s'il disparaît, et un bouton
// pour l'ouvrir dans l'explorateur de fichiers.
Rectangle {
    id: root

    property string label: ""
    property string path: ""
    property string info: ""
    // Ligne de faits : « 12,4 Mo · modifié le 18/08/2026 à 14:02 ».
    property string details: ""
    property bool warn: false

    Layout.fillWidth: true
    implicitHeight: layout.implicitHeight + Theme.s3 * 2
    radius: Theme.radiusSm
    color: Theme.panel
    border.color: root.warn ? Theme.warn : Theme.border
    border.width: 1

    RowLayout {
        id: layout
        anchors.fill: parent
        anchors.margins: Theme.s3
        spacing: Theme.s3

        ColumnLayout {
            Layout.fillWidth: true
            spacing: Theme.s1

            RowLayout {
                Layout.fillWidth: true
                spacing: Theme.s2

                AppLabel {
                    text: root.label
                    font.weight: Font.DemiBold
                }
                InfoDot {
                    diameter: 14
                    text: root.info
                }
                Item { Layout.fillWidth: true }
                AppLabel {
                    text: root.details
                    color: root.warn ? Theme.warn : Theme.textMuted
                    font.pixelSize: Theme.fzXs
                }
            }

            // Le chemin réel, en entier et sélectionnable : c'est la réponse à
            // « où c'est enregistré ? », on ne la tronque pas.
            TextEdit {
                Layout.fillWidth: true
                text: root.path
                readOnly: true
                selectByMouse: true
                wrapMode: Text.WrapAnywhere
                color: Theme.textDim
                font.family: Theme.monoFamily
                font.pixelSize: Theme.fzXs
            }
        }

        GhostButton {
            small: true
            text: qsTr("Ouvrir le dossier")
            requires: root.path.length > 0
            disabledReason: qsTr("Aucun emplacement à ouvrir.")
            tooltipText: qsTr("Ouvre cet emplacement dans l'explorateur de fichiers de Windows.")
            onClicked: Storage.openPath(root.path)
        }
    }
}
