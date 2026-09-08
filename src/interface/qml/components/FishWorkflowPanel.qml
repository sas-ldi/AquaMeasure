import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import AquaMeasure

Item {
    id: root
    objectName: "fishWorkflowPanel"
    readonly property bool hasFish: Data.draftActive || Data.selectedAnnId.length > 0
    readonly property bool selectedBoxAvailable: Data.selectedBoxIndex >= 0
        && Data.selectedBoxIndex < Fish.lastBoxCount
    function syncTaxonFields() {
        familyField.syncFromModel()
        genusField.syncFromModel()
        speciesField.syncFromModel()
    }
    Connections {
        target: Data
        function onSelectedAnnIdChanged() { Qt.callLater(root.syncTaxonFields) }
        function onSelectedBoxIndexChanged() { Qt.callLater(root.syncTaxonFields) }
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 10
        RowLayout {
            Layout.fillWidth: true
            Layout.preferredHeight: 32
            WorkspaceIcon { name: "fish" }
            AppLabel { Layout.fillWidth: true; text: qsTr("Poisson actif"); font.weight: Font.DemiBold }
            AppLabel {
                text: Data.selectedAnnId.length > 0 ? qsTr("Enregistré") : qsTr("Nouvelle fiche")
                visible: root.hasFish
                color: Data.selectedAnnId.length > 0 ? Theme.ok : Theme.textDim
                font.pixelSize: Theme.fzXs
            }
        }
        Flickable {
            id: workflowFlick
            objectName: "fishWorkflowFlick"
            Layout.fillWidth: true
            Layout.fillHeight: true
            contentWidth: width
            contentHeight: cards.implicitHeight
            clip: true
            boundsBehavior: Flickable.StopAtBounds
            ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }
            ColumnLayout {
                id: cards
                width: parent.width - 8
                spacing: 10

                AppLabel {
                    Layout.fillWidth: true
                    Layout.minimumWidth: 0
                    visible: !root.hasFish
                    text: Measure.frameCount > 0
                        ? qsTr("Clic droit sur un poisson, ou tracez son cadre.")
                        : qsTr("Chargez vos vidéos dans Réglages.")
                    color: Theme.textMuted
                    font.pixelSize: Theme.fzSm
                    wrapMode: Text.WordWrap
                }

                SidePanelSection {
                    title: qsTr("Identification")
                    iconName: "fish"
                    collapsible: false
                    info: qsTr("Vérifiez l'espèce proposée. Choisissez NA si l'identification est impossible. Enregistrer crée la fiche ou met à jour le même poisson ; son nom reste lié à sa piste.")
                    RowLayout {
                        Layout.fillWidth: true
                        visible: Fish.lastBoxCount > 1
                        AppComboBox {
                            objectName: "boxToAddSelector"
                            enabled: !Fish.trackFollowActive
                            Layout.fillWidth: true
                            implicitHeight: 30
                            model: {
                                const labels = []
                                for (let i = 0; i < Fish.lastBoxCount; ++i)
                                    labels.push(qsTr("Cadre %1").arg(i + 1))
                                return labels
                            }
                            currentIndex: root.selectedBoxAvailable ? Data.selectedBoxIndex : -1
                            placeholderText: qsTr("Choisir un poisson…")
                            onActivated: function(i) { Data.selectBoxExplicit(i); root.syncTaxonFields() }
                        }
                    }
                    GridLayout {
                        Layout.fillWidth: true
                        columns: 2
                        columnSpacing: 8
                        rowSpacing: 8
                        enabled: root.hasFish && !Fish.trackFollowActive
                        TaxonSearchField {
                            id: familyField
                            objectName: "familyTaxonField"
                            Layout.fillWidth: true
                            Layout.minimumWidth: 0
                            label: qsTr("Famille")
                            fieldText: Data.editFamily
                            options: Data.familyOptions
                            pinnedOption: Data.naLabel
                            onTextEdited: function(text) { Data.setFamilyFilter(text) }
                        }
                        TaxonSearchField {
                            id: genusField
                            objectName: "genusTaxonField"
                            Layout.fillWidth: true
                            Layout.minimumWidth: 0
                            label: qsTr("Genre")
                            fieldText: Data.editGenus
                            options: Data.genusOptions
                            pinnedOption: Data.naLabel
                            onTextEdited: function(text) { Data.setGenusFilter(text) }
                        }
                        TaxonSearchField {
                            id: speciesField
                            objectName: "speciesTaxonField"
                            Layout.fillWidth: true
                            Layout.minimumWidth: 0
                            Layout.columnSpan: 2
                            label: qsTr("Espèce")
                            fieldText: Data.editSpecies
                            options: Data.speciesOptions
                            pinnedOption: Data.naLabel
                            onTextEdited: function(text) { Data.setSpeciesFilter(text) }
                        }
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        visible: Data.selectedAnnId.length > 0
                        AppLabel {
                            Layout.fillWidth: true
                            Layout.minimumWidth: 0
                            text: qsTr("Image %1").arg(Data.selectedFrameIndex)
                            color: Theme.textDim
                            font.pixelSize: Theme.fzXs
                            elide: Text.ElideRight
                        }
                        GhostButton {
                            small: true
                            text: qsTr("Revoir")
                            onClicked: Data.focusSelectedObservation()
                        }
                    }
                }

                SidePanelSection {
                    title: qsTr("Mesure")
                    iconName: "ruler"
                    collapsible: false
                    info: qsTr("Mesurer place les points automatiquement. Ajustez A au museau et B à la queue dans les deux vues, ou posez les quatre points à la main. La longueur est facultative pour enregistrer un poisson.")
                    RowLayout {
                        Layout.fillWidth: true
                        AppLabel {
                            objectName: "draftMeasurementValue"
                            Layout.fillWidth: true
                            Layout.minimumWidth: 0
                            text: Data.editMeasurementMm > 0
                                ? qsTr("%1 mm").arg(Data.editMeasurementMm.toFixed(1))
                                : (Measure.distanceMm > 0
                                    ? qsTr("%1 mm").arg(Measure.distanceMm.toFixed(1)) : "— mm")
                            font.family: Theme.monoFamily
                            font.pixelSize: 22
                            font.weight: Font.DemiBold
                            color: Theme.accentText
                            elide: Text.ElideRight
                        }
                        GhostButton {
                            objectName: "measureSelectedBoxButton"
                            text: qsTr("Mesurer")
                            small: true
                            requires: ((Fish.focusBox.valid === true && Fish.focusBox.frame === Measure.frameIndex)
                                || root.selectedBoxAvailable || Fish.lastBoxCount === 1)
                                && !Measure.playing && !Fish.trackFollowActive
                            disabledReason: qsTr("Mettez en pause et sélectionnez le poisson sur l'image à mesurer.")
                            onClicked: Fish.measureSelectedBoxLength()
                        }
                    }
                    AppLabel {
                        Layout.fillWidth: true
                        Layout.minimumWidth: 0
                        visible: Measure.frameCount > 0 && root.hasFish
                        text: Measure.measureStep >= 4 ? qsTr("Ajustez les points dans les deux vues.")
                            : (Measure.measureHint || qsTr("Placez le point A sur l'image gauche."))
                        font.pixelSize: Theme.fzXs
                        color: Theme.textMuted
                        wrapMode: Text.WordWrap
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        visible: Measure.measureStep > 0 || (Data.draftActive && Data.editMeasurementMm > 0)
                        GhostButton {
                            objectName: "clearStereoPointsButton"
                            Layout.fillWidth: true
                            small: true
                            text: qsTr("Effacer les points")
                            requires: Measure.measureStep > 0
                            onClicked: Measure.clearPoints()
                        }
                        GhostButton {
                            objectName: "clearMeasurementButton"
                            Layout.fillWidth: true
                            small: true
                            text: qsTr("Retirer la mesure")
                            visible: Data.draftActive && Data.editMeasurementMm > 0
                            onClicked: Data.clearDraftMeasurement()
                        }
                    }
                    PrimaryButton {
                        objectName: "saveFishButton"
                        Layout.fillWidth: true
                        Layout.minimumWidth: 0
                        text: qsTr("Enregistrer le poisson")
                        attention: Data.draftActive || Data.registryRowDirty
                        requires: Data.dbAvailable && !Data.busy && !Measure.playing && root.hasFish
                            && !Fish.trackFollowActive
                        disabledReason: qsTr("Sélectionnez un poisson et mettez la vidéo en pause.")
                        tooltipText: qsTr("Enregistre l'identification et la longueur sur cette même fiche.")
                        onClicked: {
                            Data.saveFish(familyField.inputText, genusField.inputText, speciesField.inputText)
                            root.syncTaxonFields()
                        }
                    }
                    GhostButton {
                        objectName: "discardTaxonButton"
                        Layout.fillWidth: true
                        text: qsTr("Annuler les modifications")
                        small: true
                        visible: Data.registryRowDirty
                        onClicked: { Data.discardRowEdits(); root.syncTaxonFields() }
                    }
                }

                SidePanelSection {
                    title: qsTr("Suivi et annotations")
                    iconName: "track"
                    collapsible: false
                    info: qsTr("Comportement : une action à un instant ou sur une durée. Trajectoire : le parcours du poisson, avec des points facultatifs. Posez In là où le poisson est localisé ; Revoir revient à l'image enregistrée. Sur toute piste, un clic sur le cadre pointillé, Marquer ou le raccourci pose le même point.")
                    AppLabel {
                        Layout.fillWidth: true
                        Layout.minimumWidth: 0
                        visible: Data.selectedAnnId.length === 0
                        text: qsTr("Enregistrez le poisson pour commencer.")
                        color: Theme.textDim
                        font.pixelSize: Theme.fzXs
                        wrapMode: Text.WordWrap
                    }
                    FishFollowPanel { Layout.fillWidth: true }
                }

                SidePanelSection {
                    title: qsTr("Événements enregistrés")
                    iconName: "list"
                    collapsible: false
                    visible: Data.selectedAnnId.length > 0
                        && (Data.selectedObservationEvents.length > 0 || Pecks.markerCount > 0)
                    TrackEventList { Layout.fillWidth: true }
                }
                RowLayout {
                    Layout.fillWidth: true
                    visible: root.hasFish
                    GhostButton {
                        objectName: "clearSelectionButton"
                        Layout.fillWidth: true
                        small: true
                        text: qsTr("Désélectionner")
                        requires: !Fish.trackFollowActive
                        onClicked: { Data.clearSelection(); root.syncTaxonFields() }
                    }
                    GhostButton {
                        objectName: "deleteObservationButton"
                        Layout.fillWidth: true
                        small: true
                        text: qsTr("Supprimer")
                        visible: Data.selectedAnnId.length > 0
                        requires: !Fish.trackFollowActive && !Data.busy
                        onClicked: Data.deleteSelectedObservation()
                    }
                }
            }
        }
    }
}
