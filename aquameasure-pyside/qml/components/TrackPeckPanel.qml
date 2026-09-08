import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import AquaMeasure

// Un même événement se pose sur une observation ou sur sa piste.
ColumnLayout {
    id: root
    objectName: "trackPeckPanel"

    property bool compact: false
    property bool showHeader: true
    // Déplié dès qu'un type ponctuel existe : replié par défaut, le point
    // d'entrée du chantier (choisir la piste) serait invisible. La liaison
    // saute au premier clic sur l'en-tête, comportement QML habituel.
    property bool expanded: root.available && Pecks.hasPointType

    readonly property bool available: typeof Pecks !== "undefined"
    readonly property bool hasType: root.available && Pecks.hasPointType
    readonly property bool armed: root.available && Pecks.armed

    // Le poisson sélectionné commande la piste. Avant, il fallait retrouver
    // « sa » piste à la main dans une liste qui en montre autant que la vidéo
    // en compte : le client, lui, décrit une seule chaîne (« ce poisson, sa
    // taxonomie, sa taille, son tracking et ses broutes »), et à ce moment-là
    // il ne reste plus rien à choisir.
    readonly property bool fishSelected: Data.selectedAnnId.length > 0
    readonly property string fishTrackId: Data.selectedTrackDbId
    readonly property bool fishHasTrack: root.fishSelected && root.fishTrackId.length > 0
    readonly property bool observationMarked: root.available
        && Data.selectedPointEventKeys.indexOf(Pecks.typeKey) >= 0
    // Deux champs concaténés : passer d'un poisson sans piste à un AUTRE
    // poisson sans piste ne change pas l'identité de piste, mais doit bien
    // relancer le calage - sinon le bloc resterait armé sur la piste du
    // précédent et les bouchées iraient au mauvais individu.
    readonly property string fishSyncKey: Data.selectedAnnId + "|" + root.fishTrackId
    onFishSyncKeyChanged: root.syncTrackWithFish()
    Component.onCompleted: root.syncTrackWithFish()

    function syncTrackWithFish() {
        if (!root.available || !root.fishSelected)
            return  // aucun poisson désigné : annotation libre, on ne touche à rien
        if (root.fishTrackId.length === 0) {
            // Poisson sans piste : désarmer, sinon un raccourci clavier
            // poserait une bouchée sur la piste du poisson précédent.
            if (Pecks.selectedTrackId.length > 0)
                Pecks.clearSelection()
            return
        }
        if (Pecks.selectedTrackId !== root.fishTrackId)
            Pecks.selectTrack(root.fishTrackId, false)
    }

    spacing: Theme.s1

    // ── En-tête repliable : le compte reste visible même replié ────────
    Rectangle {
        visible: root.showHeader
        Layout.fillWidth: true
        Layout.preferredHeight: 30
        radius: Theme.radiusSm
        color: root.expanded ? Theme.panel2 : "transparent"
        border.color: root.expanded ? Theme.border : "transparent"
        border.width: 1

        RowLayout {
            anchors.fill: parent
            anchors.leftMargin: Theme.s2
            anchors.rightMargin: Theme.s2
            spacing: Theme.s2

            Text {
                text: root.expanded ? "▼" : "▶"
                font.pixelSize: 9
                color: Theme.textMuted
            }

            AppLabel {
                Layout.minimumWidth: 0
                text: qsTr("Événements")
                font.pixelSize: Theme.fontCaption
                font.weight: Font.DemiBold
                color: Theme.textMuted
                elide: Text.ElideRight
            }

            InfoDot {
                diameter: 14
                text: qsTr("Choisissez un événement, puis marquez l’image du poisson ou un instant sur sa piste. Les types se créent dans Édition → Préférences → Comportements.")
            }

            // Le compte prend la place qui reste et s'élide : sa largeur
            // naturelle imposait à elle seule plus de 180 px au volet.
            AppLabel {
                objectName: "peckCountBadge"
                Layout.fillWidth: true
                Layout.minimumWidth: 0
                horizontalAlignment: Text.AlignRight
                text: root.available
                    ? qsTr("%1 sur la piste · %2 sur la vidéo")
                        .arg(Pecks.markerCount).arg(Pecks.videoMarkerCount)
                    : ""
                font.family: Theme.monoFamily
                font.pixelSize: Theme.fzXs
                color: root.available && Pecks.markerCount > 0
                    ? Theme.accentText : Theme.textDim
                elide: Text.ElideRight
            }
        }

        MouseArea {
            anchors.fill: parent
            cursorShape: Qt.PointingHandCursor
            onClicked: root.expanded = !root.expanded
        }
    }

    // ── Corps ──────────────────────────────────────────────────────────
    ColumnLayout {
        Layout.fillWidth: true
        visible: (root.expanded || !root.showHeader) && root.available
        spacing: Theme.s1

        // Aucun type ponctuel : on ne code rien en dur, on dit quoi faire.
        Rectangle {
            objectName: "peckNoTypeBanner"
            Layout.fillWidth: true
            Layout.preferredHeight: noTypeLabel.implicitHeight + Theme.s3
            visible: !root.hasType
            radius: Theme.radiusSm
            color: Theme.warnSoft
            border.color: Theme.warn

            AppLabel {
                id: noTypeLabel
                anchors.fill: parent
                anchors.margins: Theme.s2
                text: qsTr("Créez un événement dans Édition → Préférences → Comportements, par exemple Bouchée.")
                color: Theme.warn
                font.pixelSize: Theme.fzXs
                wrapMode: Text.WordWrap
            }
        }

        RowLayout {
            Layout.fillWidth: true
            visible: root.hasType
            spacing: Theme.s2

            AppLabel {
                text: qsTr("Point à marquer")
                font.pixelSize: Theme.fzXs
                color: Theme.textMuted
            }

            AppComboBox {
                objectName: "peckTypeSelector"
                Layout.fillWidth: true
                implicitHeight: 30
                model: {
                    const out = []
                    const rows = Pecks.pointTypes
                    for (let i = 0; i < rows.length; i++)
                        out.push((rows[i].symbol || "●") + " " + rows[i].label)
                    return out
                }
                currentIndex: Pecks.typeIndex
                onActivated: function(i) { Pecks.setTypeIndex(i) }
            }

            Rectangle {
                objectName: "peckShortcutBadge"
                visible: Pecks.typeShortcut.length > 0
                implicitWidth: shortcutLabel.implicitWidth + Theme.s2
                implicitHeight: 20
                radius: Theme.radiusSm
                color: Theme.panel2
                border.color: Theme.border2
                AppLabel {
                    id: shortcutLabel
                    anchors.centerIn: parent
                    text: Pecks.typeShortcut
                    font.family: Theme.monoFamily
                    font.pixelSize: Theme.fzXs
                    font.weight: Font.DemiBold
                    color: Theme.accentText
                }
            }
        }

        // ── La piste du poisson sélectionné, sans rien à choisir ────────
        AppLabel {
            objectName: "peckFishTrackLabel"
            Layout.fillWidth: true
            // Un libellé qui enveloppe déclare sa largeur NON enveloppée comme
            // largeur préférée : sans ce plancher à zéro, elle devient le
            // minimum de tout le volet, qui n'a que 300 px replié.
            Layout.minimumWidth: 0
            visible: root.fishHasTrack
            text: qsTr("%1 · images %2–%3")
                .arg(Pecks.selectedTrackLabel.length > 0
                    ? Pecks.selectedTrackLabel : qsTr("piste"))
                .arg(Pecks.selectedFirstFrame).arg(Pecks.selectedLastFrame)
            font.pixelSize: Theme.fzXs
            color: Theme.accentText
            wrapMode: Text.WordWrap
        }

        // Un poisson enregistré sans piste : dire comment en obtenir une, au
        // lieu d'étaler des pistes qui appartiennent à d'autres individus.
        AppLabel {
            objectName: "peckNoFishTrackLabel"
            Layout.fillWidth: true
            Layout.minimumWidth: 0
            visible: root.fishSelected && !root.fishHasTrack
            text: qsTr("Image de l’observation : %1").arg(Data.selectedFrameIndex)
            font.pixelSize: Theme.fzXs
            color: Theme.textDim
            wrapMode: Text.WordWrap
        }

        GhostButton {
            objectName: "pointReturnToObservationButton"
            Layout.fillWidth: true
            visible: root.fishSelected && !root.fishHasTrack
                && Measure.frameIndex !== Data.selectedFrameIndex
            small: true
            text: qsTr("Revenir à cette image")
            onClicked: Data.focusSelectedObservation()
        }

        // ── Choix de la piste : le point d'entrée hors de toute fiche ───
        AppLabel {
            Layout.fillWidth: true
            Layout.minimumWidth: 0
            visible: Pecks.trackCount <= 0 && !root.fishSelected
            text: qsTr("Aucune piste enregistrée sur cette vidéo. Suivez d'abord un poisson : sélectionnez sa ligne du registre, puis « Début (In) » et « Fin (Out) ».")
            font.pixelSize: Theme.fzXs
            color: Theme.textDim
            wrapMode: Text.WordWrap
        }

        ListView {
            id: trackList
            objectName: "peckTrackList"
            Layout.fillWidth: true
            Layout.preferredHeight: Math.min(78, Math.max(0, Pecks.trackCount) * 26)
            // Masquée dès qu'un poisson est sélectionné : c'est lui qui désigne
            // la piste, et cette liste reprenait 78 px pour un choix déjà fait.
            visible: Pecks.trackCount > 0 && !root.fishSelected
            clip: true
            model: Pecks.tracks

            delegate: Rectangle {
                width: trackList.width
                height: 26
                color: Pecks.selectedTrackId === modelData.trackId
                    ? Theme.accentSoft
                    : (index % 2 ? Theme.panel2 : "transparent")

                RowLayout {
                    anchors.fill: parent
                    anchors.leftMargin: Theme.s2
                    anchors.rightMargin: Theme.s2
                    spacing: Theme.s2

                    AppLabel {
                        text: modelData.label
                        font.pixelSize: Theme.fzXs
                        font.weight: Pecks.selectedTrackId === modelData.trackId
                            ? Font.DemiBold : Font.Normal
                        color: Pecks.selectedTrackId === modelData.trackId
                            ? Theme.accentText : Theme.text
                    }
                    AppLabel {
                        Layout.fillWidth: true
                        Layout.minimumWidth: 0
                        text: qsTr("f%1 → f%2").arg(modelData.firstFrame)
                            .arg(modelData.lastFrame)
                        font.family: Theme.monoFamily
                        font.pixelSize: Theme.fzXs
                        color: Theme.textDim
                        elide: Text.ElideRight
                    }
                    AppLabel {
                        text: modelData.markerCount > 0
                            ? qsTr("%1 ●").arg(modelData.markerCount) : "—"
                        font.family: Theme.monoFamily
                        font.pixelSize: Theme.fzXs
                        color: modelData.markerCount > 0
                            ? Theme.markPin : Theme.textDim
                    }
                }

                MouseArea {
                    anchors.fill: parent
                    cursorShape: Qt.PointingHandCursor
                    onClicked: Pecks.selectTrack(modelData.trackId)
                }
            }
        }

        PrimaryButton {
            objectName: "observationEventButton"
            Layout.fillWidth: true
            visible: root.fishSelected && !root.fishHasTrack && root.hasType
            small: true
            text: root.observationMarked
                ? qsTr("%1 Retirer %2").arg(Pecks.typeSymbol).arg(Pecks.typeLabel)
                : qsTr("%1 Marquer %2").arg(Pecks.typeSymbol).arg(Pecks.typeLabel)
            requires: !Data.busy && !Measure.playing
                && Data.selectedFrameIndex === Measure.frameIndex
            disabledReason: Data.busy
                ? qsTr("Une opération est en cours.")
                : qsTr("Revenez en pause sur l’image du poisson enregistré.")
            tooltipText: qsTr("Pose ou retire cet événement sur l’image du poisson enregistré, sans créer de suivi.")
            onClicked: Data.toggleSelectedPointEvent(Pecks.typeKey)
        }

        // ── Piste sélectionnée : pose, retrait, relecture ───────────────
        // Deux rangées serrées : le panneau de droite partage sa hauteur avec
        // le registre, et un bloc plus haut rejetait la liste des marqueurs
        // hors de l'écran.
        RowLayout {
            Layout.fillWidth: true
            visible: root.armed
            spacing: Theme.s2

            PrimaryButton {
                objectName: "peckMarkButton"
                Layout.fillWidth: true
                small: true
                text: qsTr("%1 Marquer %2%3")
                    .arg(Pecks.typeSymbol).arg(Pecks.typeLabel)
                    .arg(Pecks.typeShortcut.length > 0
                        ? qsTr(" (%1)").arg(Pecks.typeShortcut) : "")
                requires: !Pecks.currentFrameMarked
                disabledReason: qsTr("Un marqueur est déjà posé sur cette image pour ce type.")
                tooltipText: qsTr("Enregistre un événement d'une seule frame sur la piste sélectionnée, à l'image affichée. Au clavier : la lettre du type pose, Maj + la lettre retire. À la souris : clic sur la bbox pointillée du poisson suivi.")
                onClicked: Pecks.markAtCurrentFrame()
            }

            GhostButton {
                objectName: "peckRemoveHereButton"
                Layout.fillWidth: true
                Layout.maximumWidth: implicitWidth
                small: true
                text: qsTr("Retirer ici")
                requires: Pecks.currentFrameMarked
                disabledReason: qsTr("Aucun marqueur sur l'image affichée.")
                tooltipText: qsTr("Retire le marqueur posé sur l'image affichée.")
                onClicked: Pecks.removeMarkerAtCurrentFrame()
            }
        }

        RowLayout {
            Layout.fillWidth: true
            visible: root.armed
            spacing: Theme.s1

            // Quatre boutons qui gardaient chacun leur largeur naturelle :
            // leur somme imposait plus de 500 px au volet, qui n'en a que 300
            // replié, et les derniers passaient sous le bord. Ils se partagent
            // désormais la rangée sans jamais dépasser leur libellé.
            GhostButton {
                objectName: "peckReplayButton"
                Layout.fillWidth: true
                Layout.maximumWidth: implicitWidth
                text: qsTr("Rejouer")
                small: true
                tooltipText: qsTr("Repart au début de la piste et lance la lecture de sa plage.")
                onClicked: Pecks.replayTrack()
            }
            // Libellés en toutes lettres : les flèches ◀ ▶ manquent à
            // plusieurs polices système et sortaient en carrés vides.
            GhostButton {
                objectName: "peckPrevMarkerButton"
                Layout.fillWidth: true
                Layout.maximumWidth: implicitWidth
                text: qsTr("Préc.")
                small: true
                tooltipText: qsTr("Marqueur précédent de cette piste.")
                onClicked: Pecks.stepToMarker(-1)
            }
            GhostButton {
                objectName: "peckNextMarkerButton"
                Layout.fillWidth: true
                Layout.maximumWidth: implicitWidth
                text: qsTr("Suiv.")
                small: true
                tooltipText: qsTr("Marqueur suivant de cette piste.")
                onClicked: Pecks.stepToMarker(1)
            }
            GhostButton {
                Layout.fillWidth: true
                Layout.maximumWidth: implicitWidth
                text: qsTr("Désélectionner")
                small: true
                // Sans poisson désigné, ce bouton rend le clic gauche à la
                // mesure. Avec un poisson désigné, la piste revient aussitôt
                // par le calage automatique : un bouton sans effet visible.
                visible: !root.fishHasTrack
                tooltipText: qsTr("Quitte le mode marquage : le clic gauche revient à la mesure.")
                onClicked: Pecks.clearSelection()
            }
        }

        AppLabel {
            Layout.fillWidth: true
            Layout.minimumWidth: 0
            visible: root.hasType && !root.armed && Pecks.trackCount > 0
                && !root.fishSelected
            text: qsTr("Choisissez la piste à annoter dans la liste ci-dessus.")
            font.pixelSize: Theme.fzXs
            color: Theme.textDim
            wrapMode: Text.WordWrap
        }

        // Le refus de poser (marqueur déjà présent, piste non choisie) doit se
        // lire quelque part : la bannière de la page, elle, ne montre que
        // Data et Fish.
        AppLabel {
            objectName: "peckStatusLabel"
            Layout.fillWidth: true
            Layout.minimumWidth: 0
            visible: Pecks.statusText.length > 0
            text: Pecks.statusText
            font.family: Theme.monoFamily
            font.pixelSize: Theme.fzXs
            color: Theme.accentText
            elide: Text.ElideRight
        }

    }
}
