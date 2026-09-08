import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import AquaMeasure

Item {
    id: root
    objectName: "measureRegistryPanel"

    property bool wide: false
    property bool compact: false
    property bool expanded: true
    property bool showHeader: true

    readonly property int taxonColumns: root.compact ? 2 : 3
    readonly property int headerChromeHeight: 58
    readonly property int headerTopInset: Theme.s5
    readonly property bool selectedBoxAvailable: Data.selectedBoxIndex >= 0
        && Data.selectedBoxIndex < Fish.lastBoxCount
    // La fiche décrit le NOUVEL ordre : on choisit le poisson, la taxonomie se
    // propose, on mesure, un seul bouton enregistre. Cette ligne doit suivre
    // cet ordre mot pour mot, sinon le panneau ment sur ce qui va se passer.
    readonly property string nextAction: {
        if (Measure.frameCount <= 0)
            return qsTr("Chargez une paire de vidéos pour commencer.")
        if (Measure.playing)
            return qsTr("Mettez en pause pour encadrer et enregistrer un poisson.")
        // Une fiche n'existe que tant qu'aucune ligne n'est sélectionnée :
        // l'enregistrement la referme et sélectionne la ligne écrite.
        if (Data.draftActive && Data.selectedAnnId.length === 0) {
            if (Data.editMeasurementMm > 0)
                return qsTr("Vérifiez la taxonomie et la longueur, puis « Enregistrer le poisson ».")
            return qsTr("Vérifiez la taxonomie, cliquez « Mesurer », puis « Enregistrer le poisson ».")
        }
        if (Data.selectedAnnId.length > 0 || Fish.focusBox.valid === true) {
            if (Data.measurementPersisting)
                return qsTr("Enregistrement de la longueur en cours…")
            if (Data.registryRowDirty)
                return qsTr("Identification non enregistrée : « Enregistrer le poisson », ou « Annuler ».")
            if (Data.selectedIdentificationStatus === qsTr("Non relu"))
                return qsTr("Identifiez le poisson, ou choisissez NA, puis « Enregistrer le poisson ».")
            return qsTr("Enregistré : « Désélectionner », puis clic droit sur le poisson suivant.")
        }
        if (Fish.lastBoxCount <= 0)
            return qsTr("Tracez un cadre sur l'image gauche, ou lancez la détection.")
        if (Fish.lastBoxCount > 1 && !root.selectedBoxAvailable)
            return qsTr("Clic droit sur le poisson à mesurer : sa taxonomie se préremplit ici.")
        return qsTr("Clic droit sur le cadre du poisson : sa taxonomie se préremplit ici.")
    }

    readonly property string statsLine: {
        const parts = []
        if (Fish.statusText.length > 0)
            parts.push(Fish.statusText)
        if (Data.statusText.length > 0)
            parts.push(Data.statusText)
        return parts.join(" · ")
    }

    // Remet les trois champs sur ce que dit le contrôleur, curseur ou pas.
    // Appelée après « Valider » et « Annuler » : ces deux actions changent le
    // modèle sous un champ qui peut avoir le focus, et un champ focalisé
    // refuse justement de se resynchroniser tout seul.
    function syncTaxonFields() {
        familyField.syncFromModel()
        genusField.syncFromModel()
        speciesField.syncFromModel()
    }

    Item {
        id: headerChrome
        visible: root.showHeader
        anchors.top: parent.top
        anchors.left: parent.left
        anchors.right: parent.right
        height: root.showHeader ? root.headerChromeHeight : 0

        Rectangle {
            id: topRule
            anchors.top: parent.top
            width: parent.width
            height: 1
            color: Theme.border
        }

        RowLayout {
            id: headerActions
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: topRule.bottom
            anchors.topMargin: root.headerTopInset
            spacing: Theme.s2

            Item {
                Layout.preferredWidth: 18
                Layout.preferredHeight: 18
                rotation: root.expanded ? 0 : -90
                Text {
                    anchors.centerIn: parent
                    text: "\u25BC"
                    font.pixelSize: 9
                    color: Theme.textMuted
                }
            }

            // Le titre cède la place aux deux boutons quand le volet est
            // replié : sans plancher à zéro, il imposait sa largeur naturelle
            // et « Actualiser » comme « Réduire » sortaient du cadre.
            ColumnLayout {
                Layout.fillWidth: true
                Layout.minimumWidth: 0
                spacing: 0
                AppLabel {
                    Layout.fillWidth: true
                    Layout.minimumWidth: 0
                    text: qsTr("Registre session")
                    font.pixelSize: Theme.fontCaption
                    color: Theme.textMuted
                    font.weight: Font.DemiBold
                    elide: Text.ElideRight
                }
                AppLabel {
                    Layout.fillWidth: true
                    Layout.minimumWidth: 0
                    text: qsTr("%1 / %2").arg(Data.registryCount).arg(Data.registryTotalCount)
                    font.pixelSize: Theme.fzXs
                    color: Theme.textDim
                    font.family: Theme.monoFamily
                    elide: Text.ElideRight
                }
            }

            InfoDot {
                id: registryInfo
                text: qsTr("Le registre liste les observations de cette session. Le parcours d'un poisson : clic droit sur son cadre (la taxonomie proposée se remplit ici, rien n'est écrit), « Mesurer » pour la longueur, puis « Enregistrer le poisson » qui crée la ligne AVEC son taxon et sa longueur, sans créer de doublon. Pour corriger ce poisson, modifiez sa fiche puis utilisez le même bouton « Enregistrer le poisson ». Choisissez « NA » rang par rang quand l'identification n'est pas possible - mais un rang parent en NA interdit un rang plus fin renseigné, le binôme portant déjà son genre. L'image n'est pas dupliquée : vidéo, frame absolue, bbox et espace de référence permettent de reconstruire le crop.")
            }

            // Les deux boutons de l'en-tête cèdent, eux aussi : à eux seuls
            // leurs largeurs naturelles dépassaient le volet replié, et
            // « Réduire » sortait du cadre. `fillWidth` les rend réductibles
            // sans les laisser grandir au-delà de leur libellé.
            GhostButton {
                id: refreshBtn
                objectName: "refreshRegistryButton"
                Layout.fillWidth: true
                Layout.maximumWidth: implicitWidth
                text: qsTr("Actualiser")
                small: true
                requires: Data.dbAvailable
                disabledReason: qsTr("Base d'annotations indisponible - vérifiez l'installation de annotations.")
                tooltipText: qsTr("Relit la base pour afficher les observations les plus récentes.")
                onClicked: Data.refreshRegistry()
            }

            GhostButton {
                id: collapseBtn
                Layout.fillWidth: true
                Layout.maximumWidth: implicitWidth
                text: root.expanded ? qsTr("Réduire") : qsTr("Afficher")
                small: true
                onClicked: root.expanded = !root.expanded
            }
        }

        MouseArea {
            anchors.left: parent.left
            anchors.top: topRule.bottom
            anchors.bottom: parent.bottom
            // La pastille (i) reste hors de la zone de repli : la survoler ou
            // la cliquer ne doit pas replier le registre. Utiliser sa position
            // réelle évite aussi que cette MouseArea, empilée au-dessus des
            // actions, ne déborde sur le bouton « Actualiser ».
            width: Math.max(0, headerActions.x + registryInfo.x)
            cursorShape: Qt.PointingHandCursor
            onClicked: root.expanded = !root.expanded
        }
    }

    Rectangle {
        id: bodyPanel
        anchors.top: headerChrome.bottom
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: root.expanded ? parent.bottom : undefined
        height: root.expanded ? undefined : 0
        visible: root.expanded
        clip: true
        color: Theme.bgElevated
        radius: Theme.radiusSm
        border.color: Theme.border

        // Le contenu du volet est plus haut que le volet, et il le restera :
        // fiche, suivi, comportement, tableau et bouchées ne tiennent pas
        // ensemble dans 860 px. Sans défilement, le bloc du bas - justement
        // celui des bouchées, dernière étape du parcours - était simplement
        // rogné et devenait inatteignable. La barre n'apparaît que lorsqu'il
        // y a de quoi défiler.
        Flickable {
            id: bodyFlick
            objectName: "registryBodyFlick"
            anchors.fill: parent
            anchors.leftMargin: Theme.s2
            anchors.rightMargin: Theme.s2
            anchors.bottomMargin: Theme.s2
            anchors.topMargin: Theme.s3
            clip: true
            contentWidth: width
            contentHeight: bodyColumn.implicitHeight
            boundsBehavior: Flickable.StopAtBounds
            ScrollBar.vertical: ScrollBar {
                policy: bodyFlick.contentHeight > bodyFlick.height
                    ? ScrollBar.AlwaysOn : ScrollBar.AlwaysOff
            }

        // Colonne laissée à son indentation d'origine : la ré-indenter aurait
        // noyé le vrai changement sous 800 lignes de diff blanc.
        ColumnLayout {
            id: bodyColumn
            width: bodyFlick.width
                - (bodyFlick.ScrollBar.vertical.visible
                    ? bodyFlick.ScrollBar.vertical.width : 0)
            spacing: Theme.s2

            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: 44
                radius: Theme.radiusSm
                color: Sessions.hasActiveSession ? Theme.panel : Theme.warnSoft
                border.color: Sessions.hasActiveSession ? Theme.border : Theme.warn

                RowLayout {
                    anchors.fill: parent
                    anchors.leftMargin: Theme.s3
                    anchors.rightMargin: Theme.s3
                    spacing: Theme.s2

                    // Les trois libellés déclaraient leur largeur naturelle
                    // sans jamais s'élider : leur somme imposait un plancher de
                    // 444 px à TOUT le panneau, qui n'en fait que 380 (300
                    // replié). C'est le débordement signalé par le client :
                    // rien ne sortait visuellement du cadre - le volet est
                    // clippé - mais tout le contenu était rogné à droite.
                    AppLabel {
                        Layout.fillWidth: true
                        Layout.minimumWidth: 0
                        text: Sessions.hasActiveSession
                            ? qsTr("Session : %1").arg(Sessions.activeSessionName)
                            : qsTr("Aucune session active")
                        color: Sessions.hasActiveSession ? Theme.accentText : Theme.warn
                        font.weight: Font.DemiBold
                        elide: Text.ElideRight
                    }
                    AppLabel {
                        Layout.minimumWidth: 0
                        text: qsTr("· %1 prise(s)").arg(Sessions.selectedPairCount)
                        visible: Sessions.hasActiveSession
                        color: Theme.textDim
                        font.pixelSize: Theme.fzXs
                        elide: Text.ElideRight
                    }
                    AppLabel {
                        Layout.fillWidth: true
                        Layout.minimumWidth: 0
                        horizontalAlignment: Text.AlignRight
                        text: Annotator.currentName.length > 0
                            ? qsTr("Annotateur : %1").arg(Annotator.currentName)
                            : qsTr("Annotateur non renseigné")
                        color: Annotator.currentName.length > 0 ? Theme.textMuted : Theme.warn
                        font.pixelSize: Theme.fzXs
                        elide: Text.ElideRight
                    }
                }
            }

            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: 42
                radius: Theme.radiusSm
                color: Theme.accentSoft
                border.color: Theme.accent

                RowLayout {
                    anchors.fill: parent
                    anchors.leftMargin: Theme.s2
                    anchors.rightMargin: Theme.s2
                    spacing: Theme.s2

                    AppLabel {
                        text: qsTr("Prochaine action")
                        color: Theme.accentText
                        font.pixelSize: Theme.fzXs
                        font.weight: Font.DemiBold
                    }
                    AppLabel {
                        Layout.fillWidth: true
                        Layout.minimumWidth: 0
                        text: root.nextAction
                        color: Theme.text
                        font.pixelSize: Theme.fzXs
                        elide: Text.ElideRight
                    }
                }
            }

            // Où en est ce poisson : taxon, taille, piste, bouchées. Juste
            // sous « Prochaine action » — l'instruction, puis l'état, sans
            // avoir à descendre dans le panneau pour reconstituer l'un ou
            // l'autre.
            FishProgressBanner {
                Layout.fillWidth: true
                Layout.minimumWidth: 0
                visible: hasFish
            }

            RowLayout {
                Layout.fillWidth: true
                visible: Fish.lastBoxCount > 1
                spacing: Theme.s2

                AppLabel {
                    text: qsTr("Cadre à ajouter")
                    color: Theme.textMuted
                    font.pixelSize: Theme.fzXs
                }
                AppComboBox {
                    objectName: "boxToAddSelector"
                    Layout.fillWidth: true
                    implicitHeight: 34
                    model: {
                        const labels = []
                        for (let i = 0; i < Fish.lastBoxCount; ++i)
                            labels.push(qsTr("Cadre %1").arg(i + 1))
                        return labels
                    }
                    currentIndex: root.selectedBoxAvailable ? Data.selectedBoxIndex : -1
                    placeholderText: qsTr("Choisir un cadre…")
                    onActivated: function(index) {
                        // selectBoxExplicit préremplit déjà la fiche et écrit
                        // son propre statut : l'écraser ici ferait mentir le
                        // panneau sur ce qui vient de se passer.
                        Data.selectBoxExplicit(index)
                        root.syncTaxonFields()
                    }
                }
            }

            GridLayout {
                Layout.fillWidth: true
                columns: root.compact ? 2 : 3
                columnSpacing: Theme.s1
                rowSpacing: Theme.s1

                GhostButton {
                    objectName: "measureSelectedBoxButton"
                    Layout.fillWidth: true
                    text: qsTr("Mesurer")
                    small: root.compact
                    requires: ((Fish.focusBox.valid === true
                                && Fish.focusBox.frame === Measure.frameIndex)
                               || root.selectedBoxAvailable
                               || Fish.lastBoxCount === 1)
                             && !Measure.playing
                    disabledReason: Measure.playing
                        ? qsTr("Mettez la lecture en pause pour mesurer.")
                        : (Fish.lastBoxCount > 1
                            ? qsTr("Plusieurs cadres sont visibles : choisissez d'abord « Cadre à ajouter ».")
                            : qsTr("Aucun poisson encadré sur cette frame : lancez « Détecter frame » d'abord."))
                    tooltipText: Data.draftActive
                        ? qsTr("Mesure la longueur du poisson encadré (stéréo) et la préremplit dans la fiche ci-dessous.")
                        : qsTr("Mesure automatiquement la longueur du poisson encadré (stéréo).")
                    onClicked: Fish.measureSelectedBoxLength()
                }
                GhostButton {
                    objectName: "clearSelectionButton"
                    Layout.fillWidth: true
                    text: qsTr("Désélectionner")
                    small: root.compact
                    requires: Data.draftActive || Data.selectedAnnId.length > 0
                        || Data.selectedBoxIndex >= 0
                    disabledReason: qsTr("Aucun poisson sélectionné.")
                    tooltipText: qsTr("Referme la fiche et efface les points A / B : le poisson suivant repart d'une page blanche.")
                    onClicked: {
                        Data.clearSelection()
                        root.syncTaxonFields()
                    }
                }
                GhostButton {
                    objectName: "deleteObservationButton"
                    Layout.fillWidth: true
                    text: qsTr("Supprimer")
                    small: root.compact
                    requires: Data.selectedAnnId.length > 0
                    disabledReason: qsTr("Sélectionnez d'abord une ligne du registre ci-dessous.")
                    tooltipText: qsTr("Retire cette observation de la base.")
                    onClicked: Data.deleteSelectedObservation()
                }
            }

            GridLayout {
                Layout.fillWidth: true
                columns: root.taxonColumns
                columnSpacing: Theme.s2
                rowSpacing: Theme.s2

                TaxonSearchField {
                    id: familyField
                    objectName: "familyTaxonField"
                    Layout.fillWidth: true
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
                    label: qsTr("Genre")
                    fieldText: Data.editGenus
                    options: Data.genusOptions
                    pinnedOption: Data.naLabel
                    onTextEdited: function(text) { Data.setGenusFilter(text) }
                }
                // Le wrapper Item + ColumnLayout n'existait que pour loger la
                // coche ✓ à côté du champ : elle a sa propre rangée désormais.
                TaxonSearchField {
                    id: speciesField
                    objectName: "speciesTaxonField"
                    Layout.fillWidth: true
                    Layout.columnSpan: root.compact ? 2 : 1
                    label: qsTr("Espèce")
                    fieldText: Data.editSpecies
                    options: Data.speciesOptions
                    pinnedOption: Data.naLabel
                    onTextEdited: function(text) { Data.setSpeciesFilter(text) }
                }
            }

            // ── Longueur de la fiche ───────────────────────
            // Le champ de mesure n'existait nulle part dans ce panneau : la
            // longueur ne se voyait que dans la colonne « Taille » d'une ligne
            // DÉJÀ écrite. Impossible, donc, de vérifier une mesure avant de
            // l'enregistrer - c'est le cœur de l'inversion signalée.
            RowLayout {
                objectName: "draftMeasurementRow"
                Layout.fillWidth: true
                visible: Data.draftActive || Data.selectedAnnId.length > 0
                spacing: Theme.s2

                AppLabel {
                    text: qsTr("Longueur")
                    color: Theme.textMuted
                    font.pixelSize: Theme.fzXs
                }
                AppLabel {
                    objectName: "draftMeasurementValue"
                    Layout.fillWidth: true
                    Layout.minimumWidth: 0
                    text: Data.editMeasurementMm > 0
                        ? qsTr("%1 mm").arg(Data.editMeasurementMm.toFixed(1))
                        : qsTr("non mesurée - « Mesurer » la préremplit ici")
                    color: Data.editMeasurementMm > 0 ? Theme.accentText : Theme.textDim
                    font.family: Data.editMeasurementMm > 0 ? Theme.monoFamily : Theme.fontFamily
                    font.pixelSize: Theme.fzXs
                    font.weight: Data.editMeasurementMm > 0 ? Font.DemiBold : Font.Normal
                    elide: Text.ElideRight
                }
                GhostButton {
                    objectName: "clearMeasurementButton"
                    text: qsTr("Retirer la mesure")
                    small: true
                    visible: Data.draftActive && Data.editMeasurementMm > 0
                    tooltipText: qsTr("Oublie la longueur de la fiche et efface les points A / B. Rien n'a encore été écrit en base.")
                    onClicked: Data.clearDraftMeasurement()
                }
            }

            // Une seule action pour creer la fiche ou enregistrer ses corrections.
            RowLayout {
                Layout.fillWidth: true
                spacing: Theme.s1

                PrimaryButton {
                    objectName: "saveFishButton"
                    Layout.fillWidth: true
                    Layout.minimumWidth: 0
                    text: qsTr("Enregistrer le poisson")
                    small: root.compact
                    attention: Data.draftActive || Data.registryRowDirty
                    requires: Data.dbAvailable && !Data.busy && !Measure.playing
                        && (Data.draftActive || Data.selectedAnnId.length > 0)
                    disabledReason: !Data.dbAvailable
                        ? qsTr("Base d'annotations indisponible.")
                        : (Data.busy ? qsTr("Un enregistrement est déjà en cours.")
                        : (Measure.playing ? qsTr("Mettez la lecture en pause pour enregistrer le poisson.")
                        : qsTr("Clic droit sur le poisson, ou sélectionnez sa fiche dans le registre.")))
                    tooltipText: qsTr("Enregistre ce poisson avec son identification et sa mesure. Si sa fiche existe déjà, enregistre vos modifications sur cette même fiche.")
                    onClicked: {
                        Data.saveFish(familyField.inputText, genusField.inputText, speciesField.inputText)
                        root.syncTaxonFields()
                    }
                }

                GhostButton {
                    objectName: "discardTaxonButton"
                    text: qsTr("Annuler")
                    small: root.compact
                    visible: Data.registryRowDirty
                    tooltipText: qsTr("Rétablit le taxon enregistré de la ligne et débloque l'ajout d'un autre poisson.")
                    onClicked: {
                        Data.discardRowEdits()
                        root.syncTaxonFields()
                    }
                }

                Item { Layout.fillWidth: true }
            }

            AppLabel {
                Layout.fillWidth: true
                visible: Data.selectedAnnId.length > 0
                text: qsTr("Événements enregistrés")
                font.pixelSize: Theme.fzXs
                color: Theme.textMuted
            }
            TrackEventList {
                Layout.fillWidth: true
                visible: Data.selectedAnnId.length > 0
            }

            // ── Provenance et relecture de la ligne sélectionnée ────────
            // La ligne survit à la validation : c'est ce qui permet de savoir,
            // plus tard, si le modèle avait raison.
            RowLayout {
                Layout.fillWidth: true
                visible: Data.selectedIdentificationStatus.length > 0
                spacing: Theme.s2

                AppLabel {
                    text: Data.selectedIdentificationStatus
                    font.pixelSize: Theme.fzXs
                    font.weight: Font.DemiBold
                    color: Data.selectedIdentificationStatus === qsTr("Non relu")
                        ? Theme.warn
                        : Theme.ok
                }
                InfoDot {
                    diameter: 14
                    text: qsTr("« Non relu » : la boîte vient du modèle (ou n'a pas encore de taxon), personne ne l'a vérifiée. « Identifié » : quelqu'un a relu et retenu un taxon. « Non identifiable » : quelqu'un a bien regardé et le cliché ne permet aucune détermination (tous les rangs en NA). Le modèle d'origine et sa confiance restent enregistrés même après votre validation.")
                }
                AppLabel {
                    Layout.fillWidth: true
                    visible: Data.selectedProvenance.length > 0
                    text: Data.selectedProvenance
                    font.pixelSize: Theme.fzXs
                    color: Theme.textDim
                    elide: Text.ElideRight
                }
            }

            AppLabel {
                Layout.fillWidth: true
                Layout.minimumWidth: 0
                visible: root.statsLine.length > 0
                text: root.statsLine
                color: Theme.accentText
                font.family: Theme.monoFamily
                font.pixelSize: Theme.fzXs
                wrapMode: Text.WordWrap
            }

            Rectangle {
                id: focusStrip
                Layout.fillWidth: true
                Layout.preferredHeight: 30
                visible: Data.selectedAnnId.length > 0 && Data.selectedFrameIndex >= 0
                radius: Theme.radiusSm
                color: Theme.accentSoft
                border.color: Theme.accent

                readonly property bool onFocusFrame: Measure.frameIndex === Data.selectedFrameIndex

                RowLayout {
                    anchors.fill: parent
                    anchors.leftMargin: Theme.s2
                    anchors.rightMargin: Theme.s1
                    spacing: Theme.s1

                    AppLabel {
                        Layout.fillWidth: true
                        Layout.minimumWidth: 0
                        text: qsTr("%1 · frame %2").arg(Data.selectedLabel).arg(Data.selectedFrameIndex)
                        color: Theme.accentText
                        font.pixelSize: Theme.fzXs
                        font.weight: Font.DemiBold
                        elide: Text.ElideRight
                    }

                    AppLabel {
                        visible: focusStrip.onFocusFrame
                        text: qsTr("à l'écran")
                        color: Theme.textDim
                        font.pixelSize: Theme.fzXs
                    }

                    GhostButton {
                        visible: !focusStrip.onFocusFrame
                        text: qsTr("Revoir")
                        small: true
                        onClicked: Data.focusSelectedObservation()
                    }
                }
            }

            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: 28
                color: Theme.panel
                DataTableRow {
                    objectName: "registryTableHeaderRow"
                    anchors.fill: parent
                    tableWidth: registryList.width
                    compact: root.compact
                    isHeader: true
                    colFrame: qsTr("Fr.")
                    colEvent: qsTr("Événements")
                    colFamily: qsTr("Fam.")
                    colGenus: qsTr("Gen.")
                    colSpecies: qsTr("Esp.")
                    colMm: qsTr("Taille (mm)")
                }
            }

            ListView {
                id: registryList
                Layout.fillWidth: true
                // Hauteur définie, plus « tout ce qui reste » : le volet défile
                // désormais dans son ensemble, et une liste extensible n'aurait
                // plus de « reste » à occuper. Elle montre jusqu'à sept lignes
                // et scrolle au-delà, ce qui laisse voir le bloc « Bouchées »
                // sans avoir à descendre trop loin.
                Layout.preferredHeight: Math.max(
                    60, Math.min(210, Data.registryCount * 30 + 2))
                clip: true
                model: Data.registry

                Connections {
                    target: Data
                    function onSelectedObservationChanged() {
                        if (Data.selectedRegistryRow >= 0)
                            registryList.positionViewAtIndex(
                                Data.selectedRegistryRow, ListView.Contain)
                    }
                }
                delegate: Rectangle {
                    width: registryList.width
                    height: Math.max(30, registryRow.implicitHeight)
                    color: Data.selectedAnnId === annId
                        ? Theme.accentSoft
                        : (index % 2 ? Theme.panel2 : "transparent")

                    DataTableRow {
                        anchors.fill: parent
                        tableWidth: registryList.width
                        compact: root.compact
                        colFrame: String(frameIndex)
                        id: registryRow
                        colEvent: eventSummary
                        colEventColor: behaviorColor
                        colFamily: family
                        colGenus: genus
                        colSpecies: species
                        colMm: {
                            if (Data.selectedAnnId === annId && Data.editMeasurementMm > 0)
                                return Data.editMeasurementMm.toFixed(1)
                            return measurementMm > 0 ? measurementMm.toFixed(1) : "-"
                        }
                    }

                    MouseArea {
                        anchors.fill: parent
                        cursorShape: Qt.PointingHandCursor
                        onClicked: Data.focusRegistryRow(index)
                    }
                }
            }

        }
        }
    }
}
