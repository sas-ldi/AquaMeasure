import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import AquaMeasure

Item {
    id: root

    ColumnLayout {
        width: root.width
        height: root.height
        spacing: Theme.spaceMd

        AppCard {
            Layout.fillWidth: true
            title: qsTr("Filtres")
            info: qsTr("Restreint ce qui est affiché dessous - et ce que reprendra « Copie JSON filtrée » dans l'onglet Exports. Les critères ne s'appliquent qu'après un clic sur « Appliquer les filtres ». Laissez vide pour tout voir.")
            subtitle: DbExplorer.statusText

            GridLayout {
                columns: 4
                columnSpacing: Theme.spaceMd
                rowSpacing: Theme.s2
                Layout.fillWidth: true

                AppLabel { text: qsTr("Du"); color: Theme.textMuted }
                AppTextField {
                    Layout.fillWidth: true
                    placeholderText: "YYYY-MM-DD"
                    text: DbExplorer.filterDateFrom
                    onTextEdited: DbExplorer.filterDateFrom = text
                    font.family: Theme.monoFamily
                }
                AppLabel { text: qsTr("Au"); color: Theme.textMuted }
                AppTextField {
                    Layout.fillWidth: true
                    placeholderText: "YYYY-MM-DD"
                    text: DbExplorer.filterDateTo
                    onTextEdited: DbExplorer.filterDateTo = text
                    font.family: Theme.monoFamily
                }
                // Choisir dans une liste déroulante ne rafraîchissait rien tant
                // qu'on n'avait pas cliqué le bouton : on applique directement.
                AppLabel { text: qsTr("Lieu"); color: Theme.textMuted }
                AppComboBox {
                    Layout.fillWidth: true
                    Layout.minimumWidth: 120
                    model: DbExplorer.siteOptions
                    currentIndex: Math.max(0, model.indexOf(DbExplorer.filterSite.length ? DbExplorer.filterSite : "Tous"))
                    onActivated: function(i) {
                        DbExplorer.filterSite = model[i]
                        DbExplorer.refresh()
                    }
                }
                AppLabel { text: qsTr("Espèce"); color: Theme.textMuted }
                AppComboBox {
                    Layout.fillWidth: true
                    Layout.minimumWidth: 160
                    model: DbExplorer.speciesOptions
                    currentIndex: DbExplorer.filterSpeciesIndex
                    onActivated: function(i) {
                        DbExplorer.filterSpeciesIndex = i
                        DbExplorer.refresh()
                    }
                }
                Item { Layout.columnSpan: 2 }
                GhostButton {
                    text: qsTr("Appliquer les filtres")
                    tooltipText: qsTr("Relit la base avec les critères ci-dessus (les filtres ne s'appliquent pas automatiquement).")
                    onClicked: DbExplorer.refresh()
                }
            }
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.s2
            Repeater {
                model: [qsTr("Sessions"), qsTr("Espèces"), qsTr("Observations")]
                delegate: Rectangle {
                    Layout.preferredHeight: 32
                    Layout.preferredWidth: tabTxt.implicitWidth + Theme.s4 * 2
                    radius: Theme.radiusSm
                    color: DbExplorer.viewTab === index ? Theme.accentSoft : Theme.panel2
                    border.color: DbExplorer.viewTab === index ? Theme.accent : Theme.border
                    AppLabel {
                        id: tabTxt
                        anchors.centerIn: parent
                        text: modelData
                        font.pixelSize: Theme.fzSm
                        color: DbExplorer.viewTab === index ? Theme.accentText : Theme.textMuted
                    }
                    MouseArea {
                        anchors.fill: parent
                        onClicked: DbExplorer.viewTab = index
                    }
                }
            }
            Item { Layout.fillWidth: true }
            AppLabel {
                text: qsTr("%1 / %2 / %3").arg(DbExplorer.sessionCount)
                    .arg(DbExplorer.speciesCount).arg(DbExplorer.observationCount)
                color: Theme.textDim
                font.pixelSize: Theme.fzXs
                font.family: Theme.monoFamily
            }
        }

        SplitView {
            Layout.fillWidth: true
            Layout.fillHeight: true
            orientation: Qt.Vertical

            Item {
                SplitView.preferredHeight: parent.height * 0.55
                SplitView.minimumHeight: 160

                StackLayout {
                    anchors.fill: parent
                    currentIndex: DbExplorer.viewTab

                    ColumnLayout {
                        spacing: 0
                        Rectangle {
                            Layout.fillWidth: true
                            height: 32
                            color: Theme.panel
                            DataExplorerTableRow {
                                anchors.fill: parent
                                tableWidth: sessionsList.width
                                isHeader: true
                                labels: [qsTr("Date"), qsTr("Lieu"), qsTr("Vidéo"), qsTr("Durée"),
                                    qsTr("Broutes"), qsTr("Freq"), qsTr("Max"), qsTr("Esp.")]
                            }
                        }
                        ListView {
                            id: sessionsList
                            Layout.fillWidth: true
                            Layout.fillHeight: true
                            clip: true
                            model: DbExplorer.sessions
                            delegate: Rectangle {
                                width: sessionsList.width
                                height: 32
                                color: index % 2 ? Theme.bgElevated : "transparent"
                                DataExplorerTableRow {
                                    anchors.fill: parent
                                    tableWidth: sessionsList.width
                                    labels: [
                                        sessionDate, site, videoName,
                                        durationS > 0 ? durationS.toFixed(0) + "s" : "-",
                                        String(grazingCount),
                                        grazingFreq >= 0 ? grazingFreq.toFixed(1) : "-",
                                        maxFish >= 0 ? String(maxFish) : "-",
                                        String(speciesCount)
                                    ]
                                }
                                MouseArea {
                                    anchors.fill: parent
                                    cursorShape: Qt.PointingHandCursor
                                    onClicked: DbExplorer.selectSession(index)
                                    onDoubleClicked: {
                                        DbExplorer.selectSession(index)
                                        DbExplorer.openSelectedInMeasure()
                                    }
                                }
                            }
                        }
                    }

                    ColumnLayout {
                        spacing: 0
                        Rectangle {
                            Layout.fillWidth: true
                            height: 32
                            color: Theme.panel
                            DataExplorerTableRow {
                                anchors.fill: parent
                                tableWidth: speciesList.width
                                isHeader: true
                                labels: [qsTr("Espèce"), qsTr("Commun"), qsTr("Crops"), qsTr("Sessions"),
                                    qsTr("Fishial"), qsTr("Galerie"), qsTr("Prov.")]
                            }
                        }
                        ListView {
                            id: speciesList
                            Layout.fillWidth: true
                            Layout.fillHeight: true
                            clip: true
                            model: DbExplorer.species
                            delegate: Rectangle {
                                width: speciesList.width
                                height: 32
                                color: index % 2 ? Theme.bgElevated : "transparent"
                                DataExplorerTableRow {
                                    anchors.fill: parent
                                    tableWidth: speciesList.width
                                    labels: [
                                        scientificName, commonName, String(cropCount),
                                        String(sessionCount), inFishial ? "Oui" : "Non",
                                        String(galleryRefCount), isProvisional ? "Oui" : "Non"
                                    ]
                                }
                                MouseArea {
                                    anchors.fill: parent
                                    onClicked: DbExplorer.selectSpecies(index)
                                }
                            }
                        }
                    }

                    ColumnLayout {
                        spacing: 0
                        Rectangle {
                            Layout.fillWidth: true
                            height: 32
                            color: Theme.panel
                            DataExplorerTableRow {
                                anchors.fill: parent
                                tableWidth: obsList.width
                                isHeader: true
                                labels: [qsTr("Espèce"), qsTr("Vidéo"), qsTr("Frame"),
                                    qsTr("BBox"), qsTr("Mesure"), qsTr("Position 3D")]
                            }
                        }
                        ListView {
                            id: obsList
                            Layout.fillWidth: true
                            Layout.fillHeight: true
                            clip: true
                            model: DbExplorer.observations
                            delegate: Rectangle {
                                width: obsList.width
                                height: 32
                                color: index % 2 ? Theme.bgElevated : "transparent"
                                DataExplorerTableRow {
                                    anchors.fill: parent
                                    tableWidth: obsList.width
                                    labels: [
                                        speciesLabel, mediaName, String(frameIndex),
                                        bboxText, measureText, positionText
                                    ]
                                }
                                MouseArea {
                                    anchors.fill: parent
                                    cursorShape: Qt.PointingHandCursor
                                    onClicked: DbExplorer.selectObservation(index)
                                    onDoubleClicked: {
                                        DbExplorer.selectObservation(index)
                                        DbExplorer.openSelectedInMeasure()
                                    }
                                }
                            }
                        }
                    }
                }
            }

            Item {
                SplitView.fillHeight: true
                SplitView.minimumHeight: 140

                JsonDetailPanel {
                    id: detailPanel
                    anchors.fill: parent
                    jsonText: DbExplorer.detailJson
                    editable: DbExplorer.editable
                    selectionType: DbExplorer.selectionType
                    onApplyRequested: DbExplorer.applyMetadataEdits(detailPanel.draftText)
                    onOpenInMeasureRequested: DbExplorer.openSelectedInMeasure()
                    onExportCsvRequested: DbExplorer.exportSelectedSessionCsv()
                    onPromoteSpeciesRequested: DbExplorer.promoteSelectedSpecies()
                }
            }
        }
    }
}
