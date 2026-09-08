import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import AquaMeasure

Rectangle {
    id: root
    objectName: "compactRegistryPanel"
    color: Theme.panel
    border.color: Theme.border2
    radius: Theme.radiusMd
    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 12
        spacing: 10
        RowLayout {
            Layout.fillWidth: true
            Layout.preferredHeight: 32
            WorkspaceIcon { name: "list" }
            AppLabel { Layout.fillWidth: true; text: qsTr("Registre"); font.weight: Font.DemiBold }
            AppLabel { text: Data.registryCount; color: Theme.accentText; font.family: Theme.monoFamily }
        }
        AppLabel {
            Layout.fillWidth: true
            Layout.minimumWidth: 0
            text: Sessions.hasActiveSession ? Sessions.activeSessionName : qsTr("Vidéos en cours")
            font.pixelSize: Theme.fzXs
            color: Theme.textMuted
            wrapMode: Text.NoWrap
            elide: Text.ElideRight
        }
        ListView {
            id: registryList
            objectName: "compactRegistryList"
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            spacing: 6
            model: Data.registry
            ScrollBar.vertical: ScrollBar { }
            Connections {
                target: Data
                function onSelectedObservationChanged() {
                    if (Data.selectedRegistryRow >= 0)
                        registryList.positionViewAtIndex(Data.selectedRegistryRow, ListView.Contain)
                }
            }
            delegate: Rectangle {
                width: registryList.width
                height: 76
                radius: Theme.radiusSm
                color: Data.selectedAnnId === annId ? Theme.accentSoft : Theme.elevated
                border.color: Data.selectedAnnId === annId ? Theme.accent : Theme.border2
                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 10
                    spacing: 4
                    RowLayout {
                        Layout.fillWidth: true
                        AppLabel {
                            Layout.fillWidth: true
                            Layout.minimumWidth: 0
                            text: species !== "-" && species !== "NA" ? species : (genus !== "-" ? genus : qsTr("Poisson"))
                            font.pixelSize: Theme.fzSm
                            font.weight: Font.DemiBold
                            wrapMode: Text.NoWrap
                            elide: Text.ElideRight
                        }
                        AppLabel {
                            text: measurementMm > 0 ? qsTr("%1 mm").arg(measurementMm.toFixed(1)) : ""
                            font.pixelSize: Theme.fzXs
                            color: Theme.accentText
                            wrapMode: Text.NoWrap
                        }
                    }
                    AppLabel {
                        Layout.fillWidth: true
                        Layout.minimumWidth: 0
                        text: qsTr("Image %1").arg(frameIndex) + (trackId.length > 0 ? qsTr(" · piste liée") : "")
                        color: Theme.textMuted
                        font.pixelSize: Theme.fzXs
                        wrapMode: Text.NoWrap
                        elide: Text.ElideRight
                    }
                    AppLabel {
                        Layout.fillWidth: true
                        Layout.minimumWidth: 0
                        text: eventSummary.replace(/\n/g, " · ") || qsTr("Aucun événement")
                        color: eventSummary.length > 0 ? Theme.accentText : Theme.textDim
                        font.pixelSize: Theme.fzXs
                        wrapMode: Text.NoWrap
                        elide: Text.ElideRight
                    }
                }
                MouseArea {
                    anchors.fill: parent
                    cursorShape: Qt.PointingHandCursor
                    enabled: !Fish.trackFollowActive
                    hoverEnabled: true
                    onClicked: Data.focusRegistryRow(index)
                    ToolTip.visible: containsMouse
                    ToolTip.delay: 700
                    ToolTip.text: species + (eventSummary.length > 0 ? "\n" + eventSummary : "")
                }
            }
            AppLabel {
                anchors.centerIn: parent
                width: parent.width
                visible: Data.registryCount === 0
                text: qsTr("Les poissons enregistrés apparaîtront ici.")
                horizontalAlignment: Text.AlignHCenter
                color: Theme.textDim
                wrapMode: Text.WordWrap
            }
        }
        GhostButton {
            objectName: "refreshRegistryButton"
            Layout.fillWidth: true
            small: true
            text: qsTr("Actualiser")
            requires: Data.dbAvailable
            onClicked: Data.refreshRegistry()
        }
        PrimaryButton {
            objectName: "openDetailedRegistryButton"
            Layout.fillWidth: true
            text: qsTr("Ouvrir les données")
            tooltipText: qsTr("Registre détaillé, filtres et exports dans Données & IA.")
            onClicked: App.currentPage = 5
        }
    }
}
