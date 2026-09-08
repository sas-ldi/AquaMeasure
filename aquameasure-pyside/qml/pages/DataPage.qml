import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import AquaMeasure

Item {
    id: root

    ColumnLayout {
        anchors.fill: parent
        spacing: Theme.s2

        DataSubTabBar {
            id: subNav
            Layout.fillWidth: true
            currentIndex: Data.subTab
            onTabClicked: function(i) { Data.subTab = i }
        }

        StackLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            currentIndex: Data.subTab

            SessionDataTab { Layout.fillWidth: true; Layout.fillHeight: true }
            FishialLibraryTab { Layout.fillWidth: true; Layout.fillHeight: true }
        }

        RowLayout {
            Layout.fillWidth: true
            readonly property bool hasMessage: Data.statusText.length > 0
                || (Data.subTab === 0 && ProTools.lastExport.kind === "session"
                    && ProTools.lastExport.status !== "idle")
                || (Data.subTab === 1 && Data.fishialProjectState !== "idle")
                || (Data.subTab === 1 && ProTools.lastExport.kind === "fishial"
                    && ProTools.lastExport.status !== "idle")
            Layout.preferredHeight: hasMessage ? 22 : 0
            spacing: Theme.s2
            visible: hasMessage

            AppLabel {
                id: statusLine
                objectName: "dataPageStatusLine"
                Layout.fillWidth: true
                text: {
                    const dataMsg = Data.statusText
                    if (Data.subTab === 1
                            && Data.fishialProjectState !== "idle"
                            && dataMsg.length > 0)
                        return dataMsg
                    if (Data.subTab === 0
                            && ProTools.lastExport.kind === "session"
                            && ProTools.lastExport.status !== "idle") {
                        const state = ProTools.lastExport
                        if (state.status === "running")
                            return qsTr("Export en cours…")
                        if (state.status === "success")
                            return qsTr("Export terminé (code %1) · %2").arg(state.exitCode).arg(state.directory)
                        return state.error.length > 0
                            ? qsTr("Échec de l'export (code %1) · %2").arg(state.exitCode).arg(state.error)
                            : qsTr("Échec de l'export (code %1)").arg(state.exitCode)
                    }
                    if (Data.subTab === 1
                            && ProTools.lastExport.kind === "fishial"
                            && ProTools.lastExport.status !== "idle") {
                        const state = ProTools.lastExport
                        if (state.status === "running")
                            return qsTr("Export Fishial en cours…")
                        if (state.status === "success")
                            return qsTr("Images Fishial exportées · %1").arg(state.directory)
                        return state.error
                    }
                    return dataMsg
                }
                color: Theme.textDim
                font.pixelSize: Theme.fzXs
                font.family: Theme.monoFamily
                elide: Text.ElideRight
            }
        }
    }

    Connections {
        target: App
        function onCurrentPageChanged() {
            if (App.currentPage === 5) {
                // Repli sur la paire synchronisée, comme la page Mesure :
                // Calib peut porter d'autres vidéos (celles de la mire).
                if (Measure.frameCount <= 0)
                    Measure.refresh(Sync.leftVideo, Sync.rightVideo)
                Data.refreshAll()
                Data.loadSessionMetadata()
            }
        }
    }
}
