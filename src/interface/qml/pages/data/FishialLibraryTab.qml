import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import AquaMeasure

// Bibliothèque locale de crops et de vecteurs : séparée du paquet COCO.
Item {
    id: root
    property bool advancedOpen: false

    ScrollView {
        id: libraryScroll
        anchors.fill: parent
        clip: true
        ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
        contentWidth: availableWidth

        ColumnLayout {
            width: libraryScroll.availableWidth
            spacing: Theme.s4

            AppCard {
                Layout.fillWidth: true
                title: qsTr("Bibliothèque Fishial locale")
                iconName: "fish"
                subtitle: qsTr("%1 espèce(s) sur ce PC").arg(Data.galleryCount)
                info: qsTr("Vos images validées complètent la reconnaissance locale, même si l'espèce existe déjà dans Fishial. Toutes les sessions de ce poste sont réunies. Les références déjà calculées sont conservées.")

                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: Theme.s3

                    SectionSurface {
                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: Theme.s2
                            AppLabel {
                                Layout.fillWidth: true
                                text: Data.fishialPendingImages > 0
                                    ? qsTr("%1 nouvelle(s) image(s) à ajouter pour %2 espèce(s). Toutes les sessions sont incluses.")
                                        .arg(Data.fishialPendingImages).arg(Data.fishialPendingSpecies)
                                    : qsTr("Aucune nouvelle image pour les espèces ayant atteint le seuil.")
                                color: Theme.textMuted
                                wrapMode: Text.WordWrap
                            }
                            Flow {
                                Layout.fillWidth: true
                                spacing: Theme.s2
                                PrimaryButton {
                                    objectName: "fishialPromoteAllButton"
                                    text: qsTr("Ajouter toutes les nouvelles images")
                                    width: Math.min(implicitWidth, parent.width)
                                    requires: Data.fishialPendingImages > 0
                                        && Data.fishialProjectState !== "running" && !ProTools.busy
                                    disabledReason: ProTools.busy
                                        ? qsTr("Un import ou export est en cours.")
                                        : Data.fishialProjectState === "running"
                                        ? qsTr("Un ajout Fishial est en cours.")
                                        : qsTr("Aucune nouvelle image pour une espèce ayant atteint le seuil réglé.")
                                    tooltipText: qsTr("Ajoute les images validées des espèces éligibles, y compris celles déjà connues de Fishial. Les anciennes références sont conservées.")
                                    onClicked: Data.promoteAllNewFishialImages()
                                }
                                GhostButton {
                                    text: qsTr("Actualiser")
                                    small: true
                                    onClicked: Data.refreshGallery()
                                }
                                GhostButton {
                                    text: qsTr("Exporter les images Fishial")
                                    small: true
                                    requires: Data.galleryCount > 0 && !ProTools.busy
                                    disabledReason: qsTr("Aucune image Fishial disponible, ou export déjà en cours.")
                                    onClicked: ProTools.exportFishialLibrary()
                                }
                                GhostButton {
                                    text: root.advancedOpen ? qsTr("Masquer les réglages") : qsTr("Réglages avancés")
                                    small: true
                                    onClicked: root.advancedOpen = !root.advancedOpen
                                }
                            }
                            AppLabel {
                                Layout.fillWidth: true
                                text: qsTr("Rouge : seuil non atteint · Orange : nouvelles images · Vert : à jour")
                                color: Theme.textDim
                                font.pixelSize: Theme.fzXs
                                wrapMode: Text.WordWrap
                            }
                        }
                    }

                    SectionSurface {
                        title: qsTr("Passer sur un autre PC")
                        iconName: "folder"
                        AppLabel {
                            Layout.fillWidth: true
                            text: qsTr("Transférez les références déjà ajoutées à Fishial, toutes sessions confondues, dans un ZIP.")
                            color: Theme.textMuted
                            wrapMode: Text.WordWrap
                        }
                        Flow {
                            Layout.fillWidth: true
                            spacing: Theme.s2
                            PrimaryButton {
                                objectName: "fishialLocalExportButton"
                                text: qsTr("Exporter mon Fishial local")
                                width: Math.min(implicitWidth, parent.width)
                                requires: Data.galleryCount > 0 && !ProTools.busy
                                    && Data.fishialProjectState !== "running"
                                disabledReason: qsTr("Ajoutez des références à Fishial et attendez la fin des calculs.")
                                tooltipText: qsTr("Crée un ZIP avec les références numériques, les espèces et les clichés disponibles. Aucun poids officiel ni vidéo n'est nécessaire dans ce ZIP.")
                                onClicked: ProTools.exportFishialLocal()
                            }
                            GhostButton {
                                objectName: "fishialLocalImportButton"
                                text: qsTr("Importer un Fishial local")
                                width: Math.min(implicitWidth, parent.width)
                                requires: !ProTools.busy && Data.fishialProjectState !== "running"
                                disabledReason: qsTr("Attendez la fin du calcul en cours.")
                                tooltipText: qsTr("Ajoute les références du ZIP sans remplacer celles de ce poste. Réimporter le même ZIP ne crée pas de doublons.")
                                onClicked: ProTools.importFishialLocal()
                            }
                        }
                    }

                    Rectangle {
                        Layout.fillWidth: true
                        implicitHeight: fishialAdvanced.implicitHeight + Theme.s3 * 2
                        visible: root.advancedOpen
                        color: Theme.panel
                        radius: Theme.radiusSm
                        border.color: Theme.border
                        RowLayout {
                            id: fishialAdvanced
                            anchors.fill: parent
                            anchors.margins: Theme.s3
                            WorkspaceIcon { name: "settings" }
                            AppLabel { text: qsTr("Minimum d'images avant activation"); color: Theme.textMuted }
                            AppSpinBox {
                                from: 1
                                to: 50
                                value: Data.fishialMinRefs
                                enabled: Data.fishialProjectState !== "running"
                                onValueModified: Data.fishialMinRefs = value
                            }
                            Item { Layout.fillWidth: true }
                        }
                    }

                    SectionSurface {
                        title: qsTr("Espèces locales")
                        iconName: "fish"
                        ListView {
                            objectName: "fishialLibraryList"
                            Layout.fillWidth: true
                            Layout.preferredHeight: Math.min(560, Math.max(80, Data.galleryCount * 72))
                            clip: true
                            model: Data.gallery
                            spacing: 2
                            ScrollBar.vertical: ScrollBar {}

                            delegate: Rectangle {
                                id: speciesRow
                                readonly property bool activeInFishial: refCount >= Math.max(1, promotionMinRefs)
                                readonly property bool readyForFishial: promotionEligible
                                readonly property bool compact: width < 760
                                width: ListView.view.width
                                height: speciesLayout.implicitHeight + Theme.s3 * 2
                                color: index % 2 ? Theme.panel : "transparent"
                                radius: Theme.radiusSm

                                GridLayout {
                                    id: speciesLayout
                                    anchors.fill: parent
                                    anchors.margins: Theme.s3
                                    columns: speciesRow.compact ? 2 : 3
                                    columnSpacing: Theme.s3
                                    rowSpacing: Theme.s2

                                    Rectangle {
                                        Layout.row: 0
                                        Layout.column: 0
                                        width: 12
                                        height: 12
                                        radius: 6
                                        color: readyForFishial && pendingCount > 0 ? Theme.warn
                                            : (activeInFishial && pendingCount === 0 ? Theme.ok : Theme.danger)
                                    }
                                    ColumnLayout {
                                        Layout.row: 0
                                        Layout.column: 1
                                        Layout.fillWidth: true
                                        spacing: 0
                                        AppLabel {
                                            Layout.fillWidth: true
                                            text: scientificName
                                            font.italic: true
                                            elide: Text.ElideRight
                                        }
                                        AppLabel {
                                            Layout.fillWidth: true
                                            text: qsTr("%1 image(s) conservée(s) · %2 référence(s) locale(s) · %3 nouvelle(s)")
                                                .arg(cropCount).arg(refCount).arg(pendingCount)
                                            color: Theme.textDim
                                            font.pixelSize: Theme.fzXs
                                            wrapMode: Text.WordWrap
                                        }
                                    }
                                    PrimaryButton {
                                        Layout.row: speciesRow.compact ? 1 : 0
                                        Layout.column: speciesRow.compact ? 1 : 2
                                        Layout.minimumWidth: implicitWidth
                                        Layout.alignment: Qt.AlignLeft
                                        objectName: "fishialProjectPromoteButton"
                                        text: pendingCount > 0
                                            ? (refCount > 0 ? qsTr("Ajouter les nouvelles images") : qsTr("Ajouter à Fishial"))
                                            : (activeInFishial ? qsTr("À jour") : qsTr("Ajouter à Fishial"))
                                        small: true
                                        requires: pendingCount > 0 && readyForFishial
                                            && Data.fishialProjectState !== "running" && !ProTools.busy
                                        disabledReason: ProTools.busy
                                            ? qsTr("Un import ou export est en cours.")
                                            : Data.fishialProjectState === "running"
                                            ? qsTr("Un ajout Fishial est en cours.")
                                            : !readyForFishial ? promotionReason
                                            : qsTr("Toutes les images validées de cette espèce ont déjà leur référence locale.")
                                        onClicked: Data.promoteGalleryTaxonAt(index)
                                    }
                                }
                            }
                        }

                        AppLabel {
                            Layout.fillWidth: true
                            visible: Data.galleryCount === 0
                            text: qsTr("Aucune espèce validée pour l'instant. Les poissons apparaîtront ici dès que leur identification aura été confirmée.")
                            color: Theme.textDim
                            wrapMode: Text.WordWrap
                        }
                    }

                    AppLabel {
                        objectName: "fishialProjectStatusLabel"
                        Layout.fillWidth: true
                        visible: Data.fishialProjectState !== "idle"
                        text: Data.fishialProjectMessage
                        color: Data.fishialProjectState === "error" ? Theme.danger : Theme.textDim
                        wrapMode: Text.WordWrap
                    }

                    ColumnLayout {
                        Layout.fillWidth: true
                        visible: ProTools.lastExport.kind.indexOf("fishial") === 0
                            && ProTools.lastExport.status !== "idle"
                        spacing: Theme.s1
                        AppLabel {
                            Layout.fillWidth: true
                            text: ProTools.lastExport.status === "running"
                                ? (ProTools.lastExport.kind === "fishial_local_import"
                                    ? qsTr("Import du Fishial local en cours…") : qsTr("Export Fishial en cours…"))
                                : ProTools.lastExport.status === "success"
                                ? (ProTools.lastExport.kind === "fishial"
                                    ? qsTr("Images Fishial exportées.") : ProTools.fishialTransferSummary)
                                : ProTools.lastExport.error
                            color: ProTools.lastExport.status === "success"
                                ? Theme.ok
                                : (ProTools.lastExport.status === "error" ? Theme.danger : Theme.textMuted)
                            wrapMode: Text.WordWrap
                        }
                        RowLayout {
                            Layout.fillWidth: true
                            visible: ProTools.lastExport.directory.length > 0
                            AppLabel {
                                Layout.fillWidth: true
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
                }
            }
        }
    }

    Component.onCompleted: Data.refreshGallery()
}
