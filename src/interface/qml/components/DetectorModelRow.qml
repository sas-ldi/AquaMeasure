import QtQuick
import QtQuick.Layouts
import AquaMeasure

// Une ligne du gestionnaire de modeles de detection.
Rectangle {
    id: root

    property var entry: ({})
    property bool active: false

    readonly property bool usable: entry.usable === true
    readonly property bool downloading: Detectors.downloadingId === entry.id

    Layout.fillWidth: true
    implicitHeight: body.implicitHeight + Theme.s3 * 2
    radius: Theme.radiusSm
    color: root.active ? Theme.accentSoft : (hover.hovered ? Theme.surfaceHover : Theme.panel2)
    border.width: 1
    border.color: root.active ? Theme.accent : Theme.border

    Behavior on color { ColorAnimation { duration: Theme.motionFast } }

    HoverHandler { id: hover }

    ColumnLayout {
        id: body
        anchors.fill: parent
        anchors.margins: Theme.s3
        spacing: Theme.s2

        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.s2

            Rectangle {
                Layout.alignment: Qt.AlignVCenter
                Layout.preferredWidth: 8
                Layout.preferredHeight: 8
                radius: 4
                color: root.usable ? Theme.ok : (entry.downloadable ? Theme.warn : Theme.textDim)
            }

            AppLabel {
                Layout.fillWidth: true
                text: entry.label || ""
                font.pixelSize: Theme.fzBase
                font.weight: Font.DemiBold
                color: root.usable ? Theme.text : Theme.textMuted
                elide: Text.ElideRight
            }

            Rectangle {
                visible: root.active
                implicitWidth: activeLabel.implicitWidth + Theme.s3
                implicitHeight: 20
                radius: Theme.radiusSm
                color: Theme.accent
                AppLabel {
                    id: activeLabel
                    anchors.centerIn: parent
                    text: qsTr("actif")
                    font.pixelSize: Theme.fzXs
                    color: "#ffffff"
                }
            }
        }

        AppLabel {
            Layout.fillWidth: true
            text: [entry.origin, entry.backendLabel, entry.classesLabel, entry.sizeLabel]
                .filter(function(v) { return v && v.length > 0 }).join("  ·  ")
            font.pixelSize: Theme.fzXs
            font.family: Theme.monoFamily
            color: Theme.textDim
            elide: Text.ElideRight
        }

        AppLabel {
            Layout.fillWidth: true
            visible: (entry.description || "").length > 0
            text: entry.description || ""
            font.pixelSize: Theme.fzXs
            color: Theme.textMuted
            wrapMode: Text.WordWrap
        }

        AppLabel {
            Layout.fillWidth: true
            visible: !root.usable && !root.downloading
            text: entry.reason || ""
            font.pixelSize: Theme.fzXs
            color: entry.downloadable && !entry.installed ? Theme.warn : Theme.danger
            wrapMode: Text.WordWrap
        }

        // Progression du telechargement en cours
        ColumnLayout {
            Layout.fillWidth: true
            visible: root.downloading
            spacing: Theme.s1

            Rectangle {
                Layout.fillWidth: true
                implicitHeight: 4
                radius: 2
                color: Theme.surface
                Rectangle {
                    width: parent.width * Detectors.downloadProgress
                    height: parent.height
                    radius: 2
                    color: Theme.accent
                }
            }
            AppLabel {
                text: qsTr("%1 %").arg(Math.round(Detectors.downloadProgress * 100))
                font.pixelSize: Theme.fzXs
                font.family: Theme.monoFamily
                color: Theme.accentText
            }
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.s2

            GhostButton {
                small: true
                text: qsTr("Utiliser")
                enabled: root.usable && !root.active
                onClicked: Detectors.setActive(entry.id)
            }

            GhostButton {
                small: true
                visible: entry.downloadable === true && entry.installed !== true
                text: root.downloading ? qsTr("Annuler") : qsTr("Installer")
                enabled: root.downloading || !Detectors.busy
                onClicked: {
                    if (root.downloading)
                        Detectors.cancelDownload()
                    else
                        Detectors.download(entry.id)
                }
            }

            GhostButton {
                small: true
                visible: (entry.homepage || "").length > 0
                text: qsTr("Source")
                onClicked: Detectors.openHomepage(entry.id)
            }

            Item { Layout.fillWidth: true }

            GhostButton {
                small: true
                visible: entry.source === "user"
                text: qsTr("Retirer")
                onClicked: Detectors.removeModel(entry.id)
            }
        }
    }
}
