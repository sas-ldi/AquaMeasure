import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import AquaMeasure

// Page Sessions : préparer une sortie terrain, retrouver une session passée,
// la rouvrir d'un clic. Une session peut contenir plusieurs prises stéréo.
Item {
    id: root

    function shortPath(path) {
        if (!path || path.length === 0)
            return qsTr("-")
        return path
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: Theme.s4

        // ── En-tête ────────────────────────────────────────────────
        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.s3

            ColumnLayout {
                Layout.fillWidth: true
                spacing: Theme.s1

                RowLayout {
                    spacing: Theme.s2
                    WorkspaceIcon { name: "folder"; implicitWidth: 22; implicitHeight: 22 }
                    AppLabel {
                        text: qsTr("Sessions de terrain")
                        font.pixelSize: Theme.fzXl
                        font.weight: Font.DemiBold
                    }
                    InfoDot {
                        diameter: 15
                        text: qsTr("Une session, c'est une sortie ou une journée de travail. Elle peut contenir plusieurs prises vidéo stéréo. Créez-la une fois, puis chargez les paires l'une après l'autre : toutes les annotations resteront réunies dans le même bilan.")
                    }
                }

                AppLabel {
                    Layout.fillWidth: true
                    text: qsTr("À venir · En cours · Terminées - une ligne par sortie, toutes ses vidéos et toutes ses annotations ensemble.")
                    font.pixelSize: Theme.fzXs
                    color: Theme.textDim
                    wrapMode: Text.WordWrap
                }
            }

            // ── Qui annote ? (sélecteur discret) ───────────────────────
            RowLayout {
                spacing: Theme.s2
                Layout.alignment: Qt.AlignVCenter

                AppLabel {
                    text: qsTr("Annotateur")
                    font.pixelSize: Theme.fzXs
                    color: Theme.textDim
                }
                InfoDot {
                    diameter: 15
                    text: qsTr("Chaque poisson identifié et chaque comportement noté sont signés de ce nom : c'est ce qui permet de savoir plus tard qui a déterminé quoi, et de créditer les bonnes personnes si les données sont publiées. Changez-en quand quelqu'un d'autre prend la main.")
                }
                AppComboBox {
                    id: annotatorCombo
                    Layout.preferredWidth: 170
                    visible: Annotator.count > 1
                    model: Annotator.names
                    currentIndex: Annotator.currentIndex
                    onActivated: Annotator.selectAnnotatorAt(currentIndex)
                }
                AppLabel {
                    visible: Annotator.count <= 1
                    text: Annotator.currentName.length > 0
                        ? Annotator.currentName
                        : qsTr("non renseigné")
                    font.pixelSize: Theme.fzSm
                    color: Annotator.currentName.length > 0 ? Theme.text : Theme.warn
                }
                GhostButton {
                    small: true
                    text: Annotator.count > 0 ? qsTr("Changer…") : qsTr("S'identifier…")
                    tooltipText: qsTr("Déclare une autre personne comme annotateur courant (nom, ORCID facultatif).")
                    onClicked: Annotator.requestDialog()
                }
            }

            PrimaryButton {
                text: qsTr("Nouvelle session")
                requires: Sessions.dbAvailable
                disabledReason: qsTr("Base d'annotations indisponible - vérifiez l'installation de annotations.")
                tooltipText: qsTr("Prépare une sortie terrain : lieu, date, notes. Les vidéos s'ajouteront plus tard.")
                onClicked: newSessionDialog.open()
            }

            GhostButton {
                text: qsTr("Actualiser")
                tooltipText: qsTr("Relit la liste des sessions depuis la base.")
                onClicked: Sessions.refresh()
            }
        }

        // ── Liste + détail ─────────────────────────────────────────
        RowLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: Theme.s4

            // Liste groupée par statut
            Rectangle {
                Layout.fillWidth: true
                Layout.fillHeight: true
                radius: Theme.radiusSm
                color: Theme.elevated
                border.color: Theme.border2
                border.width: 1

                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: Theme.s2
                    spacing: Theme.s2

                    AppLabel {
                        Layout.fillWidth: true
                        visible: Sessions.sessionCount === 0
                        text: qsTr("Aucune session enregistrée pour l'instant.\nCliquez « Nouvelle session » pour préparer votre prochaine sortie.")
                        font.pixelSize: Theme.fzSm
                        color: Theme.textDim
                        wrapMode: Text.WordWrap
                    }

                    ListView {
                        id: sessionList
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        clip: true
                        model: Sessions.sessions
                        currentIndex: Sessions.selectedRow
                        spacing: 2

                        section.property: "statusLabel"
                        section.criteria: ViewSection.FullString
                        section.delegate: Rectangle {
                            width: ListView.view ? ListView.view.width : 0
                            height: 30
                            color: Theme.elevated
                            radius: Theme.radiusSm
                            AppLabel {
                                anchors.left: parent.left
                                anchors.leftMargin: Theme.s2
                                anchors.verticalCenter: parent.verticalCenter
                                text: section
                                font.pixelSize: Theme.fzXs
                                font.weight: Font.DemiBold
                                color: Theme.accentText
                            }
                            Rectangle {
                                anchors.bottom: parent.bottom
                                width: parent.width
                                height: 1
                                color: Theme.border
                            }
                        }

                        delegate: Rectangle {
                            id: rowItem
                            width: ListView.view ? ListView.view.width : 0
                            implicitHeight: rowCol.implicitHeight + Theme.s3 * 2
                            radius: Theme.radiusSm
                            color: rowItem.ListView.isCurrentItem
                                ? Theme.accentSoft
                                : (rowMouse.containsMouse ? Theme.surfaceHover : "transparent")
                            border.width: rowItem.ListView.isCurrentItem ? 1 : 0
                            border.color: Theme.accent

                            ColumnLayout {
                                id: rowCol
                                anchors.fill: parent
                                anchors.margins: Theme.s3
                                spacing: Theme.s1

                                RowLayout {
                                    Layout.fillWidth: true
                                    spacing: Theme.s2

                                    AppLabel {
                                        Layout.fillWidth: true
                                        text: name
                                        font.pixelSize: Theme.fzMd
                                        font.weight: Font.DemiBold
                                        elide: Text.ElideRight
                                    }

                                    Rectangle {
                                        implicitWidth: statusTxt.implicitWidth + Theme.s3
                                        implicitHeight: 18
                                        radius: 9
                                        color: status === "planned" ? Theme.warnSoft
                                             : (status === "active" ? Theme.accentSoft : Theme.okSoft)
                                        Text {
                                            id: statusTxt
                                            anchors.centerIn: parent
                                            text: statusLabel
                                            font.family: Theme.fontFamily
                                            font.pixelSize: Theme.fzXs
                                            color: status === "planned" ? Theme.warn
                                                 : (status === "active" ? Theme.accentText : Theme.ok)
                                        }
                                    }
                                }

                                AppLabel {
                                    Layout.fillWidth: true
                                    text: qsTr("%1 · %2").arg(site).arg(sessionDate)
                                    font.pixelSize: Theme.fzXs
                                    color: Theme.textMuted
                                    elide: Text.ElideRight
                                }

                                AppLabel {
                                    Layout.fillWidth: true
                                    text: qsTr("%1 observation(s) · %2 mesure(s) · %3 événement(s)")
                                        .arg(observationCount).arg(measurementCount).arg(eventCount)
                                    font.pixelSize: Theme.fzXs
                                    font.family: Theme.monoFamily
                                    color: Theme.textDim
                                    elide: Text.ElideRight
                                }

                                AppLabel {
                                    Layout.fillWidth: true
                                    text: hasPair && !filesReady
                                        ? qsTr("⚠ %1 - fichier absent de ce disque").arg(videosLabel)
                                        : videosLabel
                                    font.pixelSize: Theme.fzXs
                                    color: hasPair && !filesReady ? Theme.warn : Theme.textDim
                                    elide: Text.ElideRight
                                }
                            }

                            MouseArea {
                                id: rowMouse
                                anchors.fill: parent
                                hoverEnabled: true
                                cursorShape: Qt.PointingHandCursor
                                onClicked: Sessions.selectSession(index)
                                onDoubleClicked: Sessions.openSessionAt(index)
                            }
                        }
                    }
                }
            }

            // Détail de la session choisie
            Rectangle {
                Layout.preferredWidth: 380
                Layout.fillHeight: true
                radius: Theme.radiusSm
                color: Theme.panel
                border.color: Theme.border
                border.width: 1

                ScrollView {
                    anchors.fill: parent
                    anchors.margins: Theme.s3
                    clip: true
                    ScrollBar.horizontal.policy: ScrollBar.AlwaysOff

                    ColumnLayout {
                        width: 380 - Theme.s3 * 2 - 12
                        spacing: Theme.s3

                        AppLabel {
                            Layout.fillWidth: true
                            text: Sessions.selectedId.length > 0
                                ? Sessions.selectedName
                                : qsTr("Choisissez une session")
                            font.pixelSize: Theme.fzLg
                            font.weight: Font.DemiBold
                            wrapMode: Text.WordWrap
                        }

                        AppLabel {
                            Layout.fillWidth: true
                            visible: Sessions.selectedId.length === 0
                            text: qsTr("Cliquez une session dans la liste pour voir où sont ses fichiers et la rouvrir.")
                            font.pixelSize: Theme.fzXs
                            color: Theme.textDim
                            wrapMode: Text.WordWrap
                        }

                        // Où sont les fichiers de cette session
                        SectionSurface {
                            visible: Sessions.selectedId.length > 0

                            RowLayout {
                                Layout.fillWidth: true
                                spacing: Theme.s2
                                WorkspaceIcon { name: "folder"; implicitWidth: 16; implicitHeight: 16 }
                                AppLabel {
                                    Layout.fillWidth: true
                                    text: qsTr("Vidéos et données")
                                    font.pixelSize: Theme.fzSm
                                    font.weight: Font.DemiBold
                                    color: Theme.textMuted
                                }
                                InfoDot {
                                    diameter: 14
                                    text: qsTr("Les vidéos ne sont jamais copiées : l'application note seulement où elles se trouvent et leur empreinte numérique. Si vous les déplacez sur un disque externe, la session reste consultable - il faut simplement rebrancher le disque pour revoir les images.")
                                }
                            }

                            AppLabel {
                                Layout.fillWidth: true
                                visible: Sessions.selectedPairCount === 0
                                text: qsTr("Aucune prise vidéo pour l'instant")
                                color: Theme.textDim
                            }

                            Repeater {
                                model: Sessions.selectedPairs
                                delegate: Rectangle {
                                    Layout.fillWidth: true
                                    implicitHeight: pairCol.implicitHeight + Theme.s2 * 2
                                    color: Theme.bgElevated
                                    radius: Theme.radiusSm
                                    border.color: Theme.border
                                    ColumnLayout {
                                        id: pairCol
                                        anchors.fill: parent
                                        anchors.margins: Theme.s2
                                        spacing: Theme.s1
                                        AppLabel {
                                            text: qsTr("Prise %1").arg(modelData.position + 1)
                                            font.weight: Font.DemiBold
                                            color: Theme.accentText
                                        }
                                        AppLabel {
                                            Layout.fillWidth: true
                                            text: qsTr("G · %1").arg(modelData.left.name || qsTr("absente"))
                                            color: modelData.left.available ? Theme.ok : Theme.warn
                                            font.pixelSize: Theme.fzXs
                                            elide: Text.ElideMiddle
                                        }
                                        AppLabel {
                                            Layout.fillWidth: true
                                            text: qsTr("D · %1").arg(modelData.right.name || qsTr("absente"))
                                            color: modelData.right.available ? Theme.ok : Theme.warn
                                            font.pixelSize: Theme.fzXs
                                            elide: Text.ElideMiddle
                                        }
                                        GhostButton {
                                            text: qsTr("Ouvrir cette prise")
                                            small: true
                                            requires: modelData.left.available
                                            disabledReason: qsTr("La vidéo gauche est introuvable sur ce poste.")
                                            onClicked: Sessions.openPairAt(index)
                                        }
                                    }
                                }
                            }

                            AppLabel {
                                Layout.fillWidth: true
                                text: qsTr("Annotations (base de données)")
                                font.pixelSize: Theme.fzXs
                                color: Theme.textMuted
                            }
                            AppLabel {
                                Layout.fillWidth: true
                                text: Sessions.databasePath
                                font.pixelSize: Theme.fzXs
                                font.family: Theme.monoFamily
                                color: Theme.textDim
                                wrapMode: Text.WrapAnywhere
                            }
                        }

                        // Synchro figée / calibration
                        SectionSurface {
                            title: qsTr("Synchronisation et calibration")
                            iconName: "sync"
                            visible: Sessions.selectedId.length > 0

                            RowLayout {
                                Layout.fillWidth: true
                                spacing: Theme.s2
                                AppLabel {
                                    Layout.fillWidth: true
                                    text: Sessions.selectedFrameOffset > -999999
                                        ? qsTr("Décalage de synchro figé : %1 img").arg(Sessions.selectedFrameOffset)
                                        : qsTr("Décalage de synchro : pas encore figé")
                                    font.pixelSize: Theme.fzXs
                                    font.family: Theme.monoFamily
                                    color: Sessions.selectedFrameOffset > -999999 ? Theme.text : Theme.textDim
                                    wrapMode: Text.WordWrap
                                }
                                InfoDot {
                                    diameter: 14
                                    text: qsTr("Les deux caméras ne démarrent pas à la même seconde. Ce décalage, mesuré à l'étape Synchronisation, est enregistré une fois pour toutes dans la session le jour où vous lui attachez ses vidéos. Ainsi, refaire une synchro plus tard pour une autre sortie ne déplacera pas les annotations déjà prises.")
                                }
                            }

                            AppLabel {
                                Layout.fillWidth: true
                                visible: Sessions.selectedCalibration.length > 0
                                text: qsTr("Calibration : %1").arg(Sessions.selectedCalibration)
                                font.pixelSize: Theme.fzXs
                                font.family: Theme.monoFamily
                                color: Theme.textDim
                                wrapMode: Text.WrapAnywhere
                            }
                        }

                        // Actions
                        SectionSurface {
                            title: qsTr("Actions")
                            iconName: "next"
                            visible: Sessions.selectedId.length > 0

                            PrimaryButton {
                                Layout.fillWidth: true
                                text: qsTr("Ouvrir cette session")
                                requires: Sessions.selectedLeftAvailable
                                disabledReason: Sessions.selectedLeftPath.length === 0
                                    ? qsTr("Cette session n'a pas encore de vidéos : chargez la paire puis cliquez « Attacher la paire chargée ».")
                                    : qsTr("La vidéo n'est pas accessible depuis ce poste : rebranchez le disque où elle est archivée.")
                                tooltipText: qsTr("Charge la paire de vidéos et le registre, puis ouvre la page Mesure.")
                                onClicked: Sessions.openSelectedSession()
                            }

                            GhostButton {
                                fill: true
                                Layout.fillWidth: true
                                // L'attache calcule l'empreinte des deux
                                // vidéos : plusieurs Go la première fois.
                                text: Sessions.busy
                                    ? qsTr("Enregistrement de la paire…")
                                    : qsTr("Attacher la paire chargée")
                                requires: Measure.leftVideo.length > 0 && !Sessions.busy
                                disabledReason: Sessions.busy
                                    ? qsTr("Attache en cours : l'empreinte des vidéos est en train d'être calculée.")
                                    : qsTr("Aucune paire de vidéos chargée : passez par Synchronisation, ou menu Fichier → Charger les vidéos.")
                                tooltipText: qsTr("Ajoute les vidéos actuellement chargées à cette session. Chaque nouvelle paire conserve sa propre synchronisation et sa calibration. Une vidéo ne peut appartenir qu'à une seule session.")
                                onClicked: Sessions.attachCurrentPair()
                            }

                            RowLayout {
                                Layout.fillWidth: true
                                spacing: Theme.s2

                                GhostButton {
                                    fill: true
                                    Layout.fillWidth: true
                                    small: true
                                    text: qsTr("Marquer terminée")
                                    requires: Sessions.selectedStatus !== "done"
                                    disabledReason: qsTr("Cette session est déjà marquée terminée.")
                                    tooltipText: qsTr("Range la session dans « Terminées ». Elle reste consultable et rouvrable.")
                                    onClicked: Sessions.setSelectedStatus("done")
                                }

                                GhostButton {
                                    fill: true
                                    Layout.fillWidth: true
                                    small: true
                                    text: qsTr("Dossier vidéo")
                                    requires: Sessions.selectedLeftPath.length > 0
                                    disabledReason: qsTr("Aucune vidéo attachée à cette session.")
                                    tooltipText: qsTr("Ouvre l'explorateur de fichiers sur le dossier de la vidéo.")
                                    onClicked: Sessions.openSelectedFolder()
                                }
                            }

                            GhostButton {
                                fill: true
                                Layout.fillWidth: true
                                small: true
                                text: qsTr("Supprimer cette session")
                                requires: !Sessions.selectedHasPair
                                          && Sessions.selectedLeftPath.length === 0
                                disabledReason: qsTr("Des vidéos sont attachées : marquez la session terminée plutôt que de la supprimer. Rien ne serait perdu, mais le lien de paire et le décalage de synchro le seraient.")
                                tooltipText: qsTr("Retire une session planifiée créée par erreur. Aucune annotation n'est supprimée.")
                                onClicked: Sessions.deleteSelectedSession()
                            }
                        }
                    }
                }
            }
        }

        AppLabel {
            Layout.fillWidth: true
            visible: Sessions.statusText.length > 0
            text: Sessions.statusText
            font.pixelSize: Theme.fzXs
            font.family: Theme.monoFamily
            color: Theme.textDim
            wrapMode: Text.WordWrap
        }
    }

    // ── Formulaire « Nouvelle session » ────────────────────────────
    Dialog {
        id: newSessionDialog
        objectName: "newSessionDialog"   // repère pour les tests de chargement
        title: qsTr("Nouvelle session")
        modal: true
        anchors.centerIn: Overlay.overlay
        width: 460
        padding: Theme.s4

        background: Rectangle {
            color: Theme.elevated
            radius: Theme.radiusSm
            border.color: Theme.border
            border.width: 1
        }

        contentItem: ColumnLayout {
            spacing: Theme.s3

            AppLabel {
                Layout.fillWidth: true
                text: qsTr("Le lieu et la date sont obligatoires : sans eux, les observations ne sont ni retrouvables ni publiables. Les vidéos, elles, peuvent venir plus tard.")
                font.pixelSize: Theme.fzXs
                color: Theme.textMuted
                wrapMode: Text.WordWrap
            }

            AppLabel {
                text: qsTr("Nom de la session (facultatif)")
                font.pixelSize: Theme.fzXs
                color: Theme.textMuted
            }
            AppTextField {
                Layout.fillWidth: true
                placeholderText: qsTr("ex. Passe de Toliara - matin")
                text: Sessions.formName
                onTextEdited: Sessions.formName = text
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: Theme.s2
                AppLabel {
                    Layout.fillWidth: true
                    text: qsTr("Lieu (obligatoire)")
                    font.pixelSize: Theme.fzXs
                    color: Theme.warn
                }
                InfoDot {
                    diameter: 14
                    text: qsTr("Le site de plongée ou le point de dépôt du banc de caméras. C'est ce qui permet ensuite de comparer des sessions entre elles et de regrouper les observations par lieu.")
                }
            }
            AppTextField {
                Layout.fillWidth: true
                placeholderText: qsTr("ex. Récif Nord")
                text: Sessions.formSite
                onTextEdited: Sessions.formSite = text
            }

            AppLabel {
                text: qsTr("Date de la sortie (obligatoire) - AAAA-MM-JJ")
                font.pixelSize: Theme.fzXs
                color: Theme.warn
            }
            AppTextField {
                Layout.fillWidth: true
                placeholderText: "2026-08-20"
                font.family: Theme.monoFamily
                text: Sessions.formDate
                onTextEdited: Sessions.formDate = text
            }

            AppLabel {
                text: qsTr("Notes (facultatif)")
                font.pixelSize: Theme.fzXs
                color: Theme.textMuted
            }
            AppTextField {
                Layout.fillWidth: true
                placeholderText: qsTr("météo, profondeur, matériel…")
                text: Sessions.formNotes
                onTextEdited: Sessions.formNotes = text
            }

            AppLabel {
                Layout.fillWidth: true
                visible: Sessions.formHint.length > 0
                text: Sessions.formHint
                font.pixelSize: Theme.fzXs
                color: Theme.warn
                wrapMode: Text.WordWrap
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: Theme.s2

                Item { Layout.fillWidth: true }

                GhostButton {
                    text: qsTr("Annuler")
                    tooltipText: qsTr("Ferme le formulaire sans rien créer.")
                    onClicked: newSessionDialog.close()
                }

                PrimaryButton {
                    text: qsTr("Créer la session")
                    requires: Sessions.formValid
                    disabledReason: Sessions.formHint
                    tooltipText: qsTr("Crée la session « À venir ». Vous lui attacherez ses vidéos le jour venu.")
                    onClicked: {
                        Sessions.createSession()
                        newSessionDialog.close()
                    }
                }
            }
        }
    }

    Component.onCompleted: Sessions.refresh()
}
