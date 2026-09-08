import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import AquaMeasure

// Validation des pistes de suivi.
//
// Le suivi automatique se trompe : il crée des pistes d'une seule image sur un
// reflet, et il recolle deux poissons différents sous le même numéro. Tant que
// personne ne relit ces pistes, tout export de suivi ou de comportement hérite
// de ces erreurs. Cet onglet sert exactement à ça - et rien qu'à ça : repérer
// les pistes douteuses, puis les supprimer, les fusionner ou les couper.
Item {
    id: root

    readonly property bool hasSession: Data.dbAvailable && Data.mediaId.length > 0

    readonly property string noSessionReason: !Data.dbAvailable
        ? qsTr("Base d'annotations indisponible - vérifiez l'installation de annotations.")
        : qsTr("Aucune session enregistrée en base : allez sur la page Mesure et cliquez « Enregistrer session ».")

    readonly property string noSelectionReason: qsTr("Sélectionnez d'abord une piste dans la liste ci-dessous.")

    AppCard {
        width: root.width
        height: root.height
        wide: true
        title: qsTr("Pistes suivies")
        info: qsTr("Une piste, c'est le même poisson suivi d'image en image. Le suivi automatique se trompe : il fabrique parfois une piste sur un reflet d'une seule image, ou colle deux poissons différents sous le même numéro quand ils se croisent. Cette page trie les pistes de la vidéo courante en mettant en tête les plus douteuses, et donne les trois gestes qui les corrigent : supprimer, fusionner, couper. Corriger ici, c'est corriger tous les exports de suivi et de comportement à venir.")
        subtitle: Tracks.count > 0
            ? qsTr("%1 piste(s) · %2 à vérifier · %3 position(s) enregistrée(s)")
                .arg(Tracks.count).arg(Tracks.suspiciousCount).arg(Tracks.sampleCount)
            : qsTr("Aucune piste sur cette vidéo - lancez « Suivi automatique » depuis la page Mesure.")

        ColumnLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: Theme.s3

            // ── Actions ───────────────────────────────────────────
            RowLayout {
                Layout.fillWidth: true
                spacing: Theme.s2

                GhostButton {
                    text: qsTr("Actualiser")
                    requires: root.hasSession && !Tracks.busy
                    disabledReason: root.noSessionReason
                    tooltipText: qsTr("Relit les pistes de la vidéo courante depuis la base.")
                    onClicked: Tracks.refresh()
                }
                GhostButton {
                    text: qsTr("Voir")
                    requires: Tracks.selectedTrackId.length > 0 && !Tracks.busy
                    disabledReason: root.noSelectionReason
                    tooltipText: qsTr("Ramène la lecture sur cette piste, à la position la plus proche de l'image affichée, et l'entoure à l'écran. C'est le moyen de juger si la piste suit vraiment un poisson.")
                    onClicked: Tracks.viewSelected()
                }
                GhostButton {
                    text: qsTr("Scinder ici")
                    requires: Tracks.selectedTrackId.length > 0 && !Tracks.busy
                    disabledReason: root.noSelectionReason
                    tooltipText: qsTr("Coupe la piste à l'image affichée : tout ce qui suit devient une piste distincte. À utiliser quand une piste a sauté d'un poisson à un autre - c'est le cas des pistes marquées « très longue ».")
                    onClicked: Tracks.splitSelectedAtCurrentFrame()
                }
                GhostButton {
                    text: Tracks.compareTrackId.length > 0
                        ? qsTr("Fusionner %1 → %2").arg(Tracks.compareLabel).arg(Tracks.selectedLabel)
                        : qsTr("Fusionner")
                    requires: Tracks.canMerge && !Tracks.busy
                    disabledReason: Tracks.selectedTrackId.length === 0
                        ? root.noSelectionReason
                        : qsTr("Cochez une deuxième piste (colonne de gauche) : la fusion en demande exactement deux.")
                    tooltipText: qsTr("Recolle deux pistes en une seule : la piste cochée rejoint la piste sélectionnée. À utiliser quand le suivi a perdu un poisson puis lui a donné un nouveau numéro.")
                    onClicked: Tracks.mergeSelected()
                }
                GhostButton {
                    text: qsTr("Supprimer")
                    requires: Tracks.selectedTrackId.length > 0 && !Tracks.busy
                    disabledReason: root.noSelectionReason
                    tooltipText: qsTr("Retire définitivement la piste et toutes ses positions. Les poissons identifiés à la main sur cette piste sont conservés - ils perdent seulement leur lien vers elle. Une piste portant un comportement annoté à la main n'est pas supprimée sans confirmation.")
                    onClicked: confirmDelete.open()
                }

                Item { Layout.fillWidth: true }

                RowLayout {
                    spacing: Theme.s1
                    AppLabel {
                        text: qsTr("Trou à signaler")
                        color: Theme.textMuted
                        font.pixelSize: Theme.fzXs
                    }
                    InfoDot {
                        diameter: 14
                        text: qsTr("Nombre d'images manquantes au-delà duquel une interruption de suivi est signalée. Le suivi n'enregistre pas forcément toutes les images (souvent une sur deux) : ce rythme normal n'est pas compté comme un trou, seul ce qui le dépasse l'est.")
                    }
                    AppSpinBox {
                        from: 2
                        to: 3000
                        stepSize: 10
                        value: Tracks.gapThreshold
                        onValueModified: Tracks.gapThreshold = value
                    }
                }
            }

            // ── Ce que veulent dire les drapeaux ──────────────────
            Rectangle {
                Layout.fillWidth: true
                implicitHeight: legend.implicitHeight + Theme.s3 * 2
                color: Theme.panel
                radius: Theme.radiusSm
                border.color: Theme.border

                RowLayout {
                    id: legend
                    anchors.fill: parent
                    anchors.margins: Theme.s3
                    spacing: Theme.s4

                    RowLayout {
                        spacing: Theme.s1
                        AppLabel {
                            text: qsTr("1 frame : %1").arg(Tracks.singleFrameCount)
                            color: Tracks.singleFrameCount > 0 ? Theme.warn : Theme.textDim
                            font.pixelSize: Theme.fzXs
                        }
                        InfoDot {
                            diameter: 13
                            text: qsTr("La piste n'existe que sur une seule image. C'est presque toujours une fausse détection - un reflet, une particule, une ombre. Vérifiez avec « Voir », puis supprimez.")
                        }
                    }
                    RowLayout {
                        spacing: Theme.s1
                        AppLabel {
                            text: qsTr("très longue : %1").arg(Tracks.veryLongCount)
                            color: Tracks.veryLongCount > 0 ? Theme.warn : Theme.textDim
                            font.pixelSize: Theme.fzXs
                        }
                        InfoDot {
                            diameter: 13
                            text: qsTr("La piste s'étend sur plus d'une minute de vidéo. Un poisson reste rarement aussi longtemps dans le champ : le suivi a probablement sauté d'un individu à un autre. Parcourez-la et coupez-la là où l'identité change.")
                        }
                    }
                    RowLayout {
                        spacing: Theme.s1
                        AppLabel {
                            text: qsTr("avec trous : %1").arg(Tracks.gapCount)
                            color: Tracks.gapCount > 0 ? Theme.textMuted : Theme.textDim
                            font.pixelSize: Theme.fzXs
                        }
                        InfoDot {
                            diameter: 13
                            text: qsTr("Le suivi a perdu le poisson puis l'a retrouvé, laissant un vide dans la trajectoire. Ce n'est pas forcément une erreur - mais si la piste reprend sur un autre poisson, il faut la couper ; et si le même poisson porte deux numéros, il faut fusionner.")
                        }
                    }
                    Item { Layout.fillWidth: true }
                    AppLabel {
                        text: qsTr("Trié par suspicion décroissante")
                        color: Theme.textDim
                        font.pixelSize: Theme.fzXs
                    }
                    InfoDot {
                        diameter: 13
                        text: qsTr("Les pistes les plus douteuses sont en tête : une piste d'une seule image pèse plus qu'une piste très longue, qui pèse plus qu'un simple trou. Le nombre n'est qu'un ordre de lecture - ce sont les mentions en fin de ligne qui disent ce qu'il faut vérifier.")
                    }
                }
            }

            // ── En-tête du tableau ────────────────────────────────
            Rectangle {
                Layout.fillWidth: true
                height: 30
                color: Theme.panel

                RowLayout {
                    anchors.fill: parent
                    anchors.leftMargin: Theme.s3
                    anchors.rightMargin: Theme.s3
                    spacing: Theme.s2

                    AppLabel { Layout.preferredWidth: 34; text: qsTr("2e"); color: Theme.textDim; font.pixelSize: Theme.fzXs }
                    AppLabel { Layout.preferredWidth: 70; text: qsTr("Piste"); color: Theme.textDim; font.pixelSize: Theme.fzXs }
                    AppLabel { Layout.preferredWidth: 150; text: qsTr("Images"); color: Theme.textDim; font.pixelSize: Theme.fzXs }
                    AppLabel { Layout.preferredWidth: 80; text: qsTr("Positions"); color: Theme.textDim; font.pixelSize: Theme.fzXs }
                    AppLabel { Layout.preferredWidth: 90; text: qsTr("Couverture"); color: Theme.textDim; font.pixelSize: Theme.fzXs }
                    AppLabel { Layout.preferredWidth: 150; text: qsTr("Identification"); color: Theme.textDim; font.pixelSize: Theme.fzXs }
                    AppLabel { Layout.fillWidth: true; text: qsTr("À vérifier"); color: Theme.textDim; font.pixelSize: Theme.fzXs }
                }
            }

            ListView {
                id: trackList
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                model: Tracks.tracks

                delegate: Rectangle {
                    width: trackList.width
                    height: 32
                    color: Tracks.selectedTrackId === trackId
                        ? Theme.accentSoft
                        : (index % 2 ? Theme.bgElevated : "transparent")

                    RowLayout {
                        anchors.fill: parent
                        anchors.leftMargin: Theme.s3
                        anchors.rightMargin: Theme.s3
                        spacing: Theme.s2

                        // Case « deuxième piste » : la fusion en demande deux.
                        CheckBox {
                            Layout.preferredWidth: 34
                            checked: Tracks.compareTrackId === trackId
                            onToggled: Tracks.toggleCompareRow(index)
                            ToolTip.visible: hovered
                            ToolTip.text: qsTr("Retient cette piste comme deuxième piste d'une fusion.")
                        }
                        AppLabel {
                            Layout.preferredWidth: 70
                            text: "#" + externalId
                            font.family: Theme.monoFamily
                            font.pixelSize: Theme.fzSm
                        }
                        AppLabel {
                            Layout.preferredWidth: 150
                            text: qsTr("%1 → %2").arg(firstFrame).arg(lastFrame)
                            font.family: Theme.monoFamily
                            font.pixelSize: Theme.fzXs
                            color: Theme.textMuted
                        }
                        AppLabel {
                            Layout.preferredWidth: 80
                            text: String(sampleCount)
                            font.family: Theme.monoFamily
                            font.pixelSize: Theme.fzSm
                            color: singleFrame ? Theme.warn : Theme.text
                        }
                        AppLabel {
                            Layout.preferredWidth: 90
                            text: Math.round(coverage * 100) + " %"
                            font.family: Theme.monoFamily
                            font.pixelSize: Theme.fzXs
                            color: coverage < 0.5 ? Theme.warn : Theme.textMuted
                        }
                        AppLabel {
                            Layout.preferredWidth: 150
                            text: taxon.length > 0 ? taxon : "-"
                            font.pixelSize: Theme.fzXs
                            color: taxon.length > 0 ? Theme.text : Theme.textDim
                            elide: Text.ElideRight
                        }
                        AppLabel {
                            Layout.fillWidth: true
                            text: flagsText.length > 0 ? flagsText : "-"
                            font.pixelSize: Theme.fzXs
                            color: flagsText.length > 0 ? Theme.warn : Theme.textDim
                            elide: Text.ElideRight
                        }
                    }

                    MouseArea {
                        anchors.fill: parent
                        anchors.leftMargin: 40   // laisse la case à cocher accessible
                        cursorShape: Qt.PointingHandCursor
                        onClicked: Tracks.selectRow(index)
                        onDoubleClicked: {
                            Tracks.selectRow(index)
                            Tracks.viewSelected()
                        }
                    }
                }
            }

            AppLabel {
                Layout.fillWidth: true
                text: Tracks.statusText
                color: Theme.textDim
                font.pixelSize: Theme.fzXs
                wrapMode: Text.WordWrap
            }
        }
    }

    // La suppression d'une piste est irréversible : on la confirme, et on
    // rappelle ce qui est conservé - sinon personne n'ose cliquer.
    Dialog {
        id: confirmDelete
        title: qsTr("Supprimer cette piste ?")
        modal: true
        anchors.centerIn: Overlay.overlay
        standardButtons: Dialog.Cancel

        ColumnLayout {
            spacing: Theme.s3

            AppLabel {
                Layout.preferredWidth: 420
                wrapMode: Text.WordWrap
                text: qsTr("%1 et toutes ses positions seront retirées de la base. Les poissons que vous avez identifiés à la main sur cette piste sont conservés : ils perdent seulement leur lien vers elle.")
                    .arg(Tracks.selectedLabel)
            }
            AppLabel {
                Layout.preferredWidth: 420
                wrapMode: Text.WordWrap
                color: Theme.textDim
                font.pixelSize: Theme.fzXs
                text: qsTr("Si un comportement a été annoté à la main sur cette piste, la suppression est refusée et le message vous le dira : supprimez d'abord l'événement.")
            }
            RowLayout {
                Layout.alignment: Qt.AlignRight
                spacing: Theme.s2
                GhostButton {
                    text: qsTr("Supprimer")
                    tooltipText: qsTr("Confirme le retrait de la piste et de ses positions.")
                    onClicked: {
                        Tracks.deleteSelected(false)
                        confirmDelete.close()
                    }
                }
            }
        }
    }

    Connections {
        target: Data
        function onMediaIdChanged() { Tracks.refresh() }
    }
}
