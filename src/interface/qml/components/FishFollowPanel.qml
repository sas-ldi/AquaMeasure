import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import AquaMeasure

ColumnLayout {
    id: root
    objectName: "trackFollowBlock"
    property bool compact: true
    Layout.fillWidth: true
    visible: Data.selectedAnnId.length > 0
    spacing: Theme.s2
    // Le mode se choisit avant In. La piste et les points déjà écrits restent indépendants.
    property int followMode: Fish.trackFollowBehaviorKey.length > 0 ? 0 : 1
    property int behaviorSpan: Fish.trackFollowBehaviorKey.length > 0 ? 1 : 0
    readonly property bool durationMode: followMode === 0 && behaviorSpan === 1
    readonly property bool showFollow: durationMode || Fish.trackFollowActive
        || (followMode === 1 && Data.selectedTrackDbId.length === 0)
    readonly property var durationTypes: Data.behaviorTypes.filter(function(row) {
        return row.isActive && row.scope === "interval"
    })
    property string durationKey: "grazing"
    readonly property int durationIndex: {
        for (let i = 0; i < durationTypes.length; ++i)
            if (durationTypes[i].key === durationKey) return i
        return -1
    }
    onDurationTypesChanged: {
        if (durationIndex < 0 && durationTypes.length > 0 && !Fish.trackFollowActive)
            durationKey = durationTypes[0].key
    }


    // Détacher est irréversible du point de vue de l'utilisateur
    // (il faudra refaire le rattachement) : le geste s'arme, dit
    // ce qu'il coûte, puis se confirme. Changer de poisson le
    // désarme, sinon la phrase du précédent resterait affichée
    // sous la fiche du suivant.
    property bool detachArmed: false
    property string detachCost: ""
    readonly property string detachKey: Data.selectedAnnId
        + "|" + Data.selectedTrackDbId
    onDetachKeyChanged: {
        root.detachArmed = false
        root.detachCost = ""
    }

    ChoiceTabs {
        objectName: "followModeTabs"
        Layout.fillWidth: true
        options: [qsTr("Comportement"), qsTr("Trajectoire")]
        currentIndex: root.followMode
        enabled: !Fish.trackFollowActive
        onChosen: function(i) { root.followMode = i }
    }
    ChoiceTabs {
        objectName: "behaviorSpanTabs"
        Layout.fillWidth: true
        visible: root.followMode === 0
        enabled: !Fish.trackFollowActive
        options: [qsTr("À cet instant"), qsTr("Sur une durée")]
        currentIndex: root.behaviorSpan
        onChosen: function(i) { root.behaviorSpan = i }
    }
    AppComboBox {
        objectName: "durationBehaviorSelector"
        Layout.fillWidth: true
        visible: root.durationMode
        enabled: !Fish.trackFollowActive
        model: root.durationTypes
        textRole: "label"
        currentIndex: root.durationIndex
        placeholderText: qsTr("Choisir un comportement…")
        onActivated: function(i) { root.durationKey = root.durationTypes[i].key }
    }
    AppLabel {
        Layout.fillWidth: true
        Layout.minimumWidth: 0
        visible: root.showFollow && !Fish.trackFollowActive
        text: root.durationMode ? (Data.selectedTrackDbId.length > 0
                ? qsTr("L'action dure de In à Out sur cette piste.") : qsTr("L'action dure de In à Out."))
            : qsTr("Le parcours du poisson, avec des points facultatifs.")
        font.pixelSize: Theme.fzXs
        color: Theme.textMuted
        wrapMode: Text.WordWrap
    }
    AppLabel {
        objectName: "trackFollowStatusLabel"
        Layout.fillWidth: true
        // Un libellé qui enveloppe déclare sa largeur NON enveloppée
        // comme largeur préférée : sans ce plancher à zéro, elle
        // devient le minimum de tout le volet, qui n'en a pas.
        Layout.minimumWidth: 0
        visible: Fish.trackFollowActive || Fish.grazingWorkflowState === "error"
            || Fish.grazingWorkflowState === "warning"
        text: Fish.grazingWorkflowMessage
        color: Fish.grazingWorkflowState === "error"
            || Fish.grazingWorkflowState === "warning"
            ? Theme.warn
            : Theme.textDim
        font.pixelSize: Theme.fzXs
        wrapMode: Text.WordWrap
    }

    AppLabel {
        objectName: "trackAssistProgressLabel"
        Layout.fillWidth: true
        Layout.minimumWidth: 0
        visible: Fish.assistActive
        text: Fish.assistWaiting
            ? qsTr("Suivi perdu : cliquez sur son cadre ou réencadrez-le pour continuer.")
            : qsTr("Suivi en cours : %1 %").arg(Fish.assistProgress)
        color: Fish.assistWaiting ? Theme.warn : Theme.textDim
        font.pixelSize: Theme.fzXs
        wrapMode: Text.WordWrap
    }

    AppLabel {
        objectName: "trackFollowDoneLabel"
        Layout.fillWidth: true
        Layout.minimumWidth: 0
        visible: !Fish.trackFollowActive
            && Data.selectedTrackDbId.length > 0
        text: Data.selectedTrackNumber >= 0
            ? qsTr("Piste #%1").arg(Data.selectedTrackNumber)
            : qsTr("Ce poisson est rattaché à une piste.")
        color: Theme.textDim
        font.pixelSize: Theme.fzXs
        wrapMode: Text.WordWrap
    }

    // Même remède que les boutons d'intervalle : sans
    // Layout.preferredWidth à 0, le libellé le plus long rafle
    // l'espace et écrase l'autre dans un volet à 300 px.
    GridLayout {
        Layout.fillWidth: true
        visible: root.showFollow
        columns: 2
        columnSpacing: Theme.s1

        GhostButton {
            objectName: "trackFollowInButton"
            Layout.fillWidth: true
            Layout.preferredWidth: 0
            fill: true
            small: root.compact
            text: qsTr("Début (In)")
            requires: !Fish.busy && Measure.frameCount > 0
                && !Fish.trackFollowStartMarked
                && Data.selectedAnnId.length > 0
                && (!root.durationMode || root.durationIndex >= 0)
            disabledReason: Fish.busy
                ? qsTr("Un suivi est déjà en cours.")
                : (root.durationMode && root.durationIndex < 0
                    ? qsTr("Créez un type Durée dans Préférences → Comportements.")
                : (Measure.frameCount <= 0
                    ? qsTr("Chargez d'abord une paire de vidéos.")
                    : (Data.selectedAnnId.length === 0
                        ? qsTr("Enregistrez puis sélectionnez le poisson.")
                        : qsTr("In est déjà posé : avancez puis « Fin (Out) »."))))
            tooltipText: qsTr("Mémorise ce poisson et l'image affichée comme début du suivi.")
            onClicked: {
                if (root.durationMode) Fish.beginBehaviorFollow(root.durationKey)
                else Fish.beginTrackFollow()
            }
        }

        PrimaryButton {
            objectName: "trackFollowOutButton"
            Layout.fillWidth: true
            Layout.preferredWidth: 0
            small: root.compact
            text: qsTr("Fin (Out)")
            requires: !Fish.busy && Fish.trackFollowStartMarked
            disabledReason: Fish.busy
                ? qsTr("Un suivi est déjà en cours.")
                : qsTr("Posez d'abord « Début (In) », puis avancez jusqu'à la dernière image du poisson.")
            tooltipText: qsTr("Calcule la piste de In à Out et, en mode Comportement sur une durée, enregistre aussi cette action.")
            onClicked: Fish.finishTrackFollow()
        }
    }

    GhostButton {
        objectName: "trackFollowCancelButton"
        Layout.fillWidth: true
        fill: true
        small: root.compact
        visible: Fish.trackFollowActive
        text: qsTr("Annuler le suivi")
        tooltipText: qsTr("Abandonne le suivi en cours sans rien enregistrer.")
        onClicked: Fish.cancelGrazingAnalysis()
    }

    AppLabel {
        Layout.fillWidth: true
        visible: Data.selectedTrackDbId.length > 0
        text: qsTr("Points sur la piste")
        color: Theme.text
        font.pixelSize: Theme.fzSm
        font.weight: Font.DemiBold
    }
    TrackPeckPanel {
        Layout.fillWidth: true
        compact: root.compact
        showHeader: false
        visible: Data.selectedTrackDbId.length > 0 || (root.followMode === 0 && root.behaviorSpan === 0)
    }
    // ── Défaire le rattachement ─────────────────────────
    // Le rattachement refuse d'écraser une piste existante : juste,
    // mais l'utilisateur était prévenu sans issue - aucun geste ne
    // défaisait le lien, il fallait supprimer l'observation et la
    // refaire. Le geste inverse existe maintenant, et il annonce ce
    // qu'il coûte avant d'agir : ni la piste ni ses événements ne
    // sont supprimés, ils cessent seulement d'être joints à ce
    // poisson.
    GhostButton {
        objectName: "trackDetachButton"
        Layout.fillWidth: true
        fill: true
        small: root.compact
        visible: Data.selectedTrackDbId.length > 0
            && !Fish.trackFollowActive
            && !root.detachArmed
        text: qsTr("Détacher de la piste")
        tooltipText: qsTr("Défait le lien entre ce poisson et sa piste. La piste, ses positions et ses événements restent en base : seul le lien saute. Le compte des événements concernés est affiché avant confirmation.")
        onClicked: {
            root.detachCost = Data.detachCostSummary()
            root.detachArmed = true
        }
    }

    ColumnLayout {
        objectName: "trackDetachConfirm"
        Layout.fillWidth: true
        visible: root.detachArmed
        spacing: Theme.s1

        AppLabel {
            objectName: "trackDetachCostLabel"
            Layout.fillWidth: true
            // Même plancher que partout dans ce volet : un libellé
            // qui enveloppe impose sinon sa largeur non enveloppée
            // comme minimum de tout le panneau.
            Layout.minimumWidth: 0
            text: root.detachCost
            color: Theme.warn
            font.pixelSize: Theme.fzXs
            wrapMode: Text.WordWrap
        }

        GridLayout {
            Layout.fillWidth: true
            columns: 2
            columnSpacing: Theme.s1

            GhostButton {
                objectName: "trackDetachCancelButton"
                Layout.fillWidth: true
                Layout.preferredWidth: 0
                fill: true
                small: root.compact
                text: qsTr("Garder la piste")
                tooltipText: qsTr("Referme l'avertissement sans rien modifier.")
                onClicked: root.detachArmed = false
            }

            PrimaryButton {
                objectName: "trackDetachConfirmButton"
                Layout.fillWidth: true
                Layout.preferredWidth: 0
                small: root.compact
                text: qsTr("Détacher")
                tooltipText: qsTr("Remet la piste de ce poisson à vide et recalcule la taxonomie de la piste.")
                onClicked: {
                    Data.detachSelectedObservationFromTrack()
                    root.detachArmed = false
                }
            }
        }
    }
}
