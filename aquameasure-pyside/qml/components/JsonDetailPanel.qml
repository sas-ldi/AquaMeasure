import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import AquaMeasure

// Panneau JSON - affichage monospace + édition métadonnées autorisée.
AppCard {
    id: root
    // Contenu venant du contrôleur (binding : ne jamais écrire dedans).
    property string jsonText: ""
    property bool editable: false
    property string selectionType: ""

    // Brouillon local. Auparavant la saisie écrivait dans `jsonText`, ce qui
    // détruisait le binding sur DbExplorer.detailJson : le panneau restait
    // ensuite figé sur l'ancien enregistrement à chaque nouvelle sélection.
    // Réaffecté explicitement (et non lié) pour survivre à l'édition.
    property string draftText: ""
    readonly property bool dirty: root.draftText !== root.jsonText

    onJsonTextChanged: root.draftText = root.jsonText
    Component.onCompleted: root.draftText = root.jsonText

    signal applyRequested()
    signal openInMeasureRequested()
    signal exportCsvRequested()
    signal promoteSpeciesRequested()
    title: qsTr("Détail JSON")
    subtitle: root.selectionType.length > 0
        ? qsTr("Type : %1").arg(root.selectionType)
        : qsTr("Sélectionnez une ligne dans les tableaux")

    ColumnLayout {
        Layout.fillWidth: true
        spacing: Theme.s2

        AppTextArea {
            id: jsonArea
            Layout.fillWidth: true
            Layout.preferredHeight: 160
            readOnly: !root.editable
            text: root.draftText
            font.family: Theme.monoFamily
            font.pixelSize: Theme.fzXs
            wrapMode: TextEdit.NoWrap
            onTextEdited: if (root.editable) root.draftText = text
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.s2

            AppLabel {
                visible: root.dirty
                text: qsTr("● modifications non appliquées")
                color: Theme.warn
                font.pixelSize: Theme.fzXs
            }

            GhostButton {
                text: qsTr("Appliquer")
                requires: root.editable && root.dirty
                disabledReason: !root.editable
                    ? qsTr("Cette sélection est en lecture seule - seules les sessions et espèces sont modifiables.")
                    : qsTr("Aucune modification à appliquer : éditez le JSON ci-dessus d'abord.")
                tooltipText: qsTr("Enregistre le JSON édité dans la base.")
                onClicked: root.applyRequested()
            }
            GhostButton {
                text: qsTr("Rétablir")
                requires: root.dirty
                disabledReason: qsTr("Le texte affiché correspond déjà à la base.")
                tooltipText: qsTr("Annule les modifications non appliquées.")
                onClicked: root.draftText = root.jsonText
            }
            GhostButton {
                text: root.selectionType === "observation"
                    ? qsTr("Voir ce poisson dans Mesure")
                    : qsTr("Ouvrir dans Mesure")
                requires: root.selectionType === "session" || root.selectionType === "observation"
                disabledReason: qsTr("Sélectionnez une session ou une observation dans les tableaux ci-dessus.")
                tooltipText: qsTr("Charge la vidéo et bascule sur la page Mesure.")
                onClicked: root.openInMeasureRequested()
            }
            GhostButton {
                text: qsTr("Export CSV session")
                requires: root.selectionType === "session"
                disabledReason: qsTr("Sélectionnez une ligne dans l'onglet « Sessions ».")
                onClicked: root.exportCsvRequested()
            }
            GhostButton {
                text: qsTr("Promouvoir espèce")
                requires: root.selectionType === "species"
                disabledReason: qsTr("Sélectionnez une ligne dans l'onglet « Espèces ».")
                tooltipText: qsTr("Ajoute l'espèce à la galerie Fishial (références few-shot).")
                onClicked: root.promoteSpeciesRequested()
            }
            Item { Layout.fillWidth: true }
        }
    }
}
