import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import AquaMeasure

Item {
    id: root

    AppCard {
        width: root.width
        height: root.height
        wide: true
        title: qsTr("Registre poissons")
        iconName: "list"
        info: qsTr("Une ligne par poisson annoté : l'image où il apparaît, son identification (famille / genre / espèce) et sa longueur en millimètres si elle a été mesurée. « NA » signifie « rang non identifiable » - c'est une réponse valable, conservée telle quelle à l'export. Cette page relit ; l'annotation se fait sur la page Mesure, devant la vidéo.")
        // L'ancien sous-titre annonçait « clic : retour à la frame » alors que
        // la page ne montre aucune vidéo : le clic ne produisait rien de visible.
        subtitle: qsTr("%1 observation(s) sur cette session - %2 en base · sélectionnez une ligne pour l'éditer, double-clic pour l'ouvrir dans Mesure")
            .arg(Data.registryCount).arg(Data.registryTotalCount)

        ColumnLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: Theme.s3

            SectionSurface {
                Layout.maximumWidth: Theme.contentMaxWidth
                title: qsTr("Bilan")
                iconName: "chart"
                Flow {
                    Layout.fillWidth: true
                    Layout.maximumWidth: Theme.contentMaxWidth
                    spacing: Theme.s2

                    Repeater {
                        model: [
                            {
                                label: qsTr("MaxN"),
                                value: Data.sessionMaxVisibleFish >= 0
                                    ? String(Data.sessionMaxVisibleFish)
                                    : qsTr("-"),
                                muted: Data.sessionMaxVisibleFish <= 0
                            },
                            {
                                label: qsTr("Durées"),
                                value: String(Data.sessionGrazingCount),
                                muted: Data.sessionGrazingCount <= 0
                            },
                            {
                                label: qsTr("Points isolés"),
                                value: Data.sessionFlagCount > 0
                                    ? qsTr("%1 sur %2 obs.")
                                        .arg(Data.sessionFlagCount)
                                        .arg(Data.sessionFlaggedCount)
                                    : "0",
                                muted: Data.sessionFlagCount <= 0
                            },
                            {
                                label: qsTr("Suivi"),
                                value: !Data.sessionHasTracking
                                    ? qsTr("non")
                                    : (Data.sessionTrackedCount > 0
                                        ? qsTr("oui · %1 obs. liée(s)")
                                            .arg(Data.sessionTrackedCount)
                                        : qsTr("oui · aucune obs. liée")),
                                muted: !Data.sessionHasTracking
                            }
                        ]
                        delegate: Rectangle {
                            implicitWidth: chipRow.implicitWidth + Theme.s3 * 2
                            implicitHeight: chipRow.implicitHeight + Theme.s2
                            radius: Theme.radiusSm
                            color: Theme.panel
                            border.color: Theme.border

                            RowLayout {
                                id: chipRow
                                anchors.centerIn: parent
                                spacing: Theme.s2

                                AppLabel {
                                    text: modelData.label
                                    color: Theme.textDim
                                    font.pixelSize: Theme.fzXs
                                }
                                AppLabel {
                                    text: modelData.value
                                    color: modelData.muted ? Theme.textDim : Theme.accentText
                                    font.pixelSize: Theme.fzXs
                                    font.family: Theme.monoFamily
                                    font.weight: Font.DemiBold
                                }
                            }
                        }
                    }

                    InfoDot {
                        text: qsTr("MaxN : maximum des comptages validés sur une image. Durées : comportements enregistrés entre un début et une fin. Points isolés : événements ponctuels portés par une observation, sans piste. Suivi : présence de pistes et nombre d'observations qui leur sont liées. Les points sur piste apparaissent dans la colonne Événements.")
                    }
                }

                AppLabel {
                    Layout.fillWidth: true
                    Layout.maximumWidth: Theme.contentMaxWidth
                    visible: Data.sessionFlagSummary.length > 0
                    text: qsTr("Points isolés : %1").arg(Data.sessionFlagSummary)
                    color: Theme.textDim
                    font.pixelSize: Theme.fzXs
                    elide: Text.ElideRight
                }
            }

            SectionSurface {
                Layout.maximumWidth: Theme.contentMaxWidth
                title: qsTr("Identification")
                iconName: "fish"

                ColumnLayout {
                    id: taxonEdit
                    Layout.fillWidth: true
                    spacing: Theme.s2
                    AppLabel {
                        Layout.fillWidth: true
                        visible: Data.selectedAnnId.length === 0
                        text: qsTr("Sélectionnez un poisson dans le registre.")
                        color: Theme.textDim
                        font.pixelSize: Theme.fzXs
                    }

                    GridLayout {
                        Layout.fillWidth: true
                        enabled: Data.selectedAnnId.length > 0
                        columns: 3
                        columnSpacing: Theme.s2
                        rowSpacing: Theme.s2

                        TaxonSearchField {
                            Layout.fillWidth: true
                            label: qsTr("Famille")
                            fieldText: Data.editFamily
                            options: Data.familyOptions
                            pinnedOption: Data.naLabel
                            onTextEdited: function(text) { Data.setFamilyFilter(text) }
                        }
                        TaxonSearchField {
                            Layout.fillWidth: true
                            label: qsTr("Genre")
                            fieldText: Data.editGenus
                            options: Data.genusOptions
                            pinnedOption: Data.naLabel
                            onTextEdited: function(text) { Data.setGenusFilter(text) }
                        }
                        TaxonSearchField {
                            Layout.fillWidth: true
                            label: qsTr("Espèce")
                            fieldText: Data.editSpecies
                            options: Data.speciesOptions
                            pinnedOption: Data.naLabel
                            onTextEdited: function(text) { Data.setSpeciesFilter(text) }
                        }
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        GhostButton {
                            text: qsTr("Enregistrer")
                            requires: Data.selectedAnnId.length > 0
                            disabledReason: qsTr("Sélectionnez d'abord une ligne du registre ci-dessous.")
                            tooltipText: qsTr("Enregistre Famille / Genre / Espèce sur l'observation sélectionnée.")
                            onClicked: Data.saveSelectedObservation()
                        }
                        GhostButton {
                            text: qsTr("Supprimer")
                            requires: Data.selectedAnnId.length > 0
                            disabledReason: qsTr("Sélectionnez d'abord une ligne du registre ci-dessous.")
                            tooltipText: qsTr("Retire définitivement cette observation de la base.")
                            onClicked: Data.deleteSelectedObservation()
                        }
                        GhostButton {
                            text: qsTr("Voir dans la vidéo")
                            requires: Data.selectedRegistryRow >= 0
                            disabledReason: qsTr("Sélectionnez d'abord une ligne du registre ci-dessous.")
                            tooltipText: qsTr("Bascule sur la page Mesure, à la frame du poisson.")
                            onClicked: {
                                App.currentPage = 4
                                Data.focusSelectedObservation()
                            }
                        }
                        Item { Layout.fillWidth: true }

                        AppLabel {
                            Layout.minimumWidth: 0
                            Layout.maximumWidth: 160
                            elide: Text.ElideRight
                            visible: Data.selectedAnnId.length > 0 && Data.selectedFrameIndex >= 0
                            text: qsTr("%1 · frame %2").arg(Data.selectedLabel).arg(Data.selectedFrameIndex)
                            color: Theme.accentText
                            font.pixelSize: Theme.fzXs
                            font.family: Theme.monoFamily
                        }
                    }
                }
            }

            Rectangle {
                Layout.fillWidth: true
                // Le fond s'arrête où s'arrête la ligne : un bandeau qui
                // continue seul sur 900 px de vide laissait croire à des
                // colonnes cachées plus loin.
                Layout.maximumWidth: Theme.contentMaxWidth
                height: 32
                color: Theme.panel
                DataTableRow {
                    anchors.fill: parent
                    tableWidth: registryList.width
                    isHeader: true
                    colFrame: qsTr("Frame")
                    colEvent: qsTr("Événements")
                    colFamily: qsTr("Famille")
                    colGenus: qsTr("Genre")
                    colSpecies: qsTr("Espèce")
                    colMm: qsTr("Taille (mm)")
                }
            }

            ListView {
                id: registryList
                Layout.fillWidth: true
                Layout.maximumWidth: Theme.contentMaxWidth
                Layout.fillHeight: true
                clip: true
                model: Data.registry
                delegate: Rectangle {
                    width: registryList.width
                    height: Math.max(34, registryRow.implicitHeight)
                    color: Data.selectedAnnId === annId
                        ? Theme.accentSoft
                        : (index % 2 ? Theme.bgElevated : "transparent")

                    DataTableRow {
                        anchors.fill: parent
                        tableWidth: registryList.width
                        colFrame: String(frameIndex)
                        id: registryRow
                        colEvent: eventSummary
                        colEventColor: behaviorColor
                        colFamily: family
                        colGenus: genus
                        colSpecies: species
                        colMm: measurementMm > 0 ? measurementMm.toFixed(1) : "-"
                    }

                    MouseArea {
                        anchors.fill: parent
                        cursorShape: Qt.PointingHandCursor
                        onClicked: Data.focusRegistryRow(index)
                        onDoubleClicked: {
                            App.currentPage = 4
                            Data.focusObservationById(annId)
                        }
                    }
                }
            }
        }
    }
}
