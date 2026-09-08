import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import AquaMeasure

// Bilan et export de la session, affichés à côté du registre.
Item {
    id: root
    property bool advancedOpen: false
    property string lastNotifiedDirectory: ""
    property var exportFormats: [
        {
            id: "coco",
            label: qsTr("COCO - détection"),
            description: qsTr("Images JPEG et bounding boxes pour entraîner un détecteur.")
        },
        {
            id: "tracking",
            label: qsTr("COCO-VID - tracking"),
            description: qsTr("Trajectoires et actions sur piste à leur frame exacte. Copie MOTChallenge incluse.")
        },
        {
            id: "csv",
            label: qsTr("CSV - données de session"),
            description: qsTr("Mesures, pistes, actions et comptages MaxN dans des tables CSV reliées pour Excel.")
        }
    ]

    function formatIndex(formatId) {
        for (let i = 0; i < exportFormats.length; ++i) {
            if (exportFormats[i].id === formatId)
                return i
        }
        return 0
    }

    function selectedFormat() {
        return exportFormats[formatIndex(ProTools.sessionExportFormat)]
    }

    function exportButtonText() {
        if (ProTools.busy)
            return qsTr("Export en cours…")
        if (ProTools.sessionExportFormat === "tracking")
            return qsTr("Exporter en COCO-VID")
        if (ProTools.sessionExportFormat === "csv")
            return qsTr("Exporter en CSV")
        return qsTr("Exporter en COCO")
    }

    readonly property bool hasSession: Sessions.selectedId.length > 0
    readonly property string exportDisabledReason: !root.hasSession
        ? qsTr("Créez ou sélectionnez une session.")
        : Sessions.selectedPairCount <= 0
        ? qsTr("Ajoutez au moins une paire de vidéos à cette session.")
        : ProTools.sessionExportFormat === "tracking" && Sessions.selectedTrackCount <= 0
        ? qsTr("Cette session ne contient encore aucune piste de tracking.")
        : ProTools.busy
        ? qsTr("Un export est déjà en cours.") : ""

    AppCard {
        anchors.fill: parent
        wide: true
        title: qsTr("Résumé et export")
        iconName: "export"
        subtitle: root.hasSession
            ? qsTr("%1 · annotateur : %2")
                .arg(Sessions.selectedName)
                .arg(Annotator.currentName.length > 0 ? Annotator.currentName : qsTr("non renseigné"))
            : qsTr("Aucune session active")
        info: qsTr("Choisissez le résultat voulu, puis exportez. Chaque export crée un dossier autonome et indique clairement où il se trouve.")

        ColumnLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.minimumHeight: 0
            spacing: Theme.s3

            // Le bilan défile ; les commandes d'export restent en bas.
            ScrollView {
                id: summaryScroll
                objectName: "dataExportScrollView"
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.minimumHeight: 0
                Layout.preferredHeight: 0
                clip: true
                contentWidth: availableWidth
                ScrollBar.horizontal.policy: ScrollBar.AlwaysOff

                ColumnLayout {
                    width: summaryScroll.availableWidth
                    spacing: Theme.s3
                    ColumnLayout {
                        Layout.fillWidth: true
                        visible: ProTools.lastExport.kind === "session"
                            && ProTools.lastExport.status !== "idle"
                        spacing: Theme.s1
                        AppLabel {
                            Layout.fillWidth: true
                            text: ProTools.lastExport.status === "running"
                                ? qsTr("Création du paquet en cours…")
                                : ProTools.lastExport.status === "success"
                                ? qsTr("Export terminé. Ouvrez le dossier pour vérifier son contenu.")
                                : ProTools.lastExport.error
                            color: ProTools.lastExport.status === "success"
                                ? Theme.ok
                                : (ProTools.lastExport.status === "error" ? Theme.danger : Theme.textMuted)
                            wrapMode: Text.WordWrap
                        }
                        RowLayout {
                            Layout.fillWidth: true
                            Layout.minimumWidth: 0
                            visible: ProTools.lastExport.directory.length > 0
                            AppLabel {
                                Layout.fillWidth: true
                                Layout.minimumWidth: 0
                                text: ProTools.lastExport.directory
                                color: Theme.textDim
                                font.family: Theme.monoFamily
                                font.pixelSize: Theme.fzXs
                                elide: Text.ElideMiddle
                            }
                            PrimaryButton {
                                text: qsTr("Ouvrir le dossier")
                                small: true
                                onClicked: ProTools.openLastExportFolder()
                            }
                        }
                    }

                    Rectangle {
                        Layout.fillWidth: true
                        implicitHeight: missingCol.implicitHeight + Theme.s3 * 2
                        visible: ProTools.sessionExportState === "missing"
                        color: Theme.warnSoft
                        radius: Theme.radiusSm
                        border.color: Theme.warn
                        ColumnLayout {
                            id: missingCol
                            anchors.fill: parent
                            anchors.margins: Theme.s3
                            spacing: Theme.s2
                            AppLabel {
                                Layout.fillWidth: true
                                text: ProTools.missingMediaMessage
                                color: Theme.warn
                                wrapMode: Text.WordWrap
                            }
                            ColumnLayout {
                                Layout.fillWidth: true
                                Layout.minimumWidth: 0
                                GhostButton {
                                    text: qsTr("Retrouver la vidéo manquante…")
                                    Layout.fillWidth: true
                                    onClicked: ProTools.repointNextMissingMedia()
                                }
                                GhostButton {
                                    text: qsTr("Continuer avec les vidéos disponibles")
                                    Layout.fillWidth: true
                                    onClicked: ProTools.continueSessionExport()
                                }
                            }
                        }
                    }

                    Rectangle {
                        Layout.fillWidth: true
                        implicitHeight: advanced.implicitHeight + Theme.s3 * 2
                        visible: root.advancedOpen
                        color: Theme.panel
                        radius: Theme.radiusSm
                        border.color: Theme.border
                        ColumnLayout {
                            id: advanced
                            anchors.fill: parent
                            anchors.margins: Theme.s3
                            spacing: Theme.s3
                            AppLabel { text: qsTr("Version du dataset"); color: Theme.textMuted }
                            AppTextField {
                                Layout.fillWidth: true
                                Layout.minimumWidth: 0
                                text: ProTools.datasetVersion
                                placeholderText: "1.0.0"
                                onEditingFinished: ProTools.datasetVersion = text
                            }
                            AppLabel {
                                Layout.fillWidth: true
                                Layout.minimumWidth: 0
                                text: qsTr("COCO contient automatiquement une vue « fish » et une vue par espèce. Les seuils sont adaptés à la session.")
                                color: Theme.textDim
                                font.pixelSize: Theme.fzXs
                                wrapMode: Text.WordWrap
                            }
                        }
                    }

                    SectionSurface {
                        title: qsTr("Bilan de la session")
                        iconName: "chart"
                        GridLayout {
                            id: sessionMetrics
                            Layout.fillWidth: true
                            columns: width >= 420 ? 3 : 2
                            columnSpacing: Theme.s2
                            rowSpacing: Theme.s2

                            Repeater {
                                model: [
                                    { label: qsTr("Prises vidéo"), value: Sessions.selectedPairCount },
                                    { label: qsTr("Poissons annotés"), value: Sessions.selectedObservationCount },
                                    { label: qsTr("Mesures"), value: Sessions.selectedMeasurementCount },
                                    { label: qsTr("MaxN"), value: Sessions.selectedMaxN },
                                    { label: qsTr("Pistes"), value: Sessions.selectedTrackCount },
                                    { label: qsTr("Événements"), value: Sessions.selectedEventCount }
                                ]
                                delegate: Rectangle {
                                    Layout.fillWidth: true
                                    Layout.minimumWidth: 0
                                    implicitHeight: metric.implicitHeight + Theme.s2 * 2
                                    radius: Theme.radiusSm
                                    color: Theme.panel
                                    border.color: Theme.border
                                    RowLayout {
                                        id: metric
                                        anchors.fill: parent
                                        anchors.leftMargin: Theme.s3
                                        anchors.rightMargin: Theme.s3
                                        spacing: Theme.s2
                                        AppLabel {
                                            Layout.fillWidth: true
                                            text: modelData.label
                                            color: Theme.textDim
                                            font.pixelSize: Theme.fzXs
                                            elide: Text.ElideRight
                                        }
                                        AppLabel { text: String(modelData.value); color: Theme.accentText; font.weight: Font.DemiBold; font.family: Theme.monoFamily }
                                    }
                                }
                            }
                        }

                        ColumnLayout {
                            Layout.fillWidth: true
                            Layout.minimumWidth: 0
                            visible: Sessions.selectedSpeciesSummary.length > 0
                            spacing: Theme.s1
                            AppLabel {
                                text: qsTr("Espèces de la session")
                                font.weight: Font.DemiBold
                                color: Theme.textMuted
                            }
                            Repeater {
                                model: Sessions.selectedSpeciesSummary
                                delegate: RowLayout {
                                    Layout.fillWidth: true
                                    Layout.minimumWidth: 0
                                    AppLabel {
                                        Layout.fillWidth: true
                                        Layout.minimumWidth: 0
                                        text: modelData.name
                                        font.italic: true
                                        elide: Text.ElideRight
                                    }
                                    AppLabel {
                                        text: qsTr("%1 poisson(s)").arg(modelData.count)
                                        color: Theme.textDim
                                        font.family: Theme.monoFamily
                                        font.pixelSize: Theme.fzXs
                                    }
                                }
                            }
                        }
                    }
                }
            }

            SectionSurface {
                title: qsTr("Export")
                iconName: "export"
                ColumnLayout {
                    Layout.fillWidth: true
                    Layout.minimumWidth: 0
                    spacing: Theme.s2

                    AppLabel {
                        text: qsTr("Format d'export")
                        font.weight: Font.DemiBold
                        color: Theme.textMuted
                    }
                    AppComboBox {
                        id: exportFormatSelector
                        objectName: "sessionExportFormatSelector"
                        Layout.fillWidth: true
                        Layout.minimumWidth: 0
                        model: root.exportFormats
                        textRole: "label"
                        currentIndex: root.formatIndex(ProTools.sessionExportFormat)
                        enabled: !ProTools.busy
                        onActivated: function(index) {
                            ProTools.sessionExportFormat = root.exportFormats[index].id
                        }
                    }
                    AppLabel {
                        Layout.fillWidth: true
                        Layout.minimumWidth: 0
                        text: root.selectedFormat().description
                        color: Theme.textDim
                        font.pixelSize: Theme.fzXs
                        wrapMode: Text.WordWrap
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        Layout.minimumWidth: 0
                        AppLabel {
                            Layout.fillWidth: true
                            Layout.minimumWidth: 0
                            text: qsTr("Les images et vecteurs Fishial restent dans l'onglet Fishial.")
                            color: Theme.textDim
                            font.pixelSize: Theme.fzXs
                            wrapMode: Text.WordWrap
                        }
                        GhostButton {
                            objectName: "openFishialButton"
                            text: qsTr("Ouvrir Fishial")
                            small: true
                            onClicked: Data.subTab = 1
                        }
                    }
                }

                ColumnLayout {
                    Layout.fillWidth: true
                    Layout.minimumWidth: 0
                    spacing: Theme.s2

                    PrimaryButton {
                        objectName: "sessionExportButton"
                        Layout.fillWidth: true
                        text: root.exportButtonText()
                        requires: root.hasSession
                            && Sessions.selectedPairCount > 0
                            && !(ProTools.sessionExportFormat === "tracking"
                                && Sessions.selectedTrackCount <= 0)
                            && !ProTools.busy
                        disabledReason: root.exportDisabledReason
                        tooltipText: root.selectedFormat().description
                        onClicked: ProTools.exportSession(Sessions.selectedId)
                    }

                    GhostButton {
                        objectName: "advancedExportSettingsButton"
                        text: root.advancedOpen ? qsTr("Masquer les réglages") : qsTr("Réglages avancés")
                        small: true
                        Layout.alignment: Qt.AlignLeft
                        onClicked: {
                            root.advancedOpen = !root.advancedOpen
                            if (root.advancedOpen) summaryScroll.contentItem.contentY = 0
                        }
                    }
                }
            }
        }
    }

    Dialog {
        id: exportSuccessDialog
        objectName: "sessionExportSuccessDialog"
        anchors.centerIn: parent
        modal: true
        title: qsTr("Export terminé")
        standardButtons: Dialog.Close

        ColumnLayout {
            width: 460
            spacing: Theme.s3

            AppLabel {
                Layout.fillWidth: true
                text: qsTr("La session a bien été exportée dans ce dossier :")
                wrapMode: Text.WordWrap
            }
            AppLabel {
                Layout.fillWidth: true
                text: ProTools.lastExport.directory
                color: Theme.textDim
                font.family: Theme.monoFamily
                font.pixelSize: Theme.fzXs
                wrapMode: Text.WrapAnywhere
            }
            PrimaryButton {
                objectName: "sessionExportOpenFolderButton"
                text: qsTr("Ouvrir le dossier")
                onClicked: ProTools.openLastExportFolder()
            }
        }
    }

    Connections {
        target: ProTools
        function onSessionExportChanged() {
            if (ProTools.sessionExportState === "missing") summaryScroll.contentItem.contentY = 0
        }
        function onLastExportChanged() {
            const state = ProTools.lastExport
            if (state.kind === "session") summaryScroll.contentItem.contentY = 0
            if (state.kind === "session"
                    && state.status === "success"
                    && state.directory.length > 0
                    && state.directory !== root.lastNotifiedDirectory) {
                root.lastNotifiedDirectory = state.directory
                exportSuccessDialog.open()
            }
        }
    }

    Component.onCompleted: Sessions.refresh()
}
