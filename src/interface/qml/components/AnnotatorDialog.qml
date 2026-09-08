import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import AquaMeasure

// « Qui annote ? » - posé une seule fois, au premier lancement.
//
// Jusqu'ici chaque observation était signée « operator » : impossible de savoir
// qui avait identifié un poisson, ni de créditer qui que ce soit dans un jeu de
// données publié. Le nom saisi ici est écrit sur chaque annotation et chaque
// événement, puis mémorisé - la question n'est plus reposée.
Dialog {
    id: dlg

    title: qsTr("Qui annote ?")
    modal: true
    // On ne se dérobe pas à la question au premier lancement : le clic dehors
    // et la touche Échap ne ferment que si l'on peut annuler.
    closePolicy: dlg.allowCancel
        ? (Popup.CloseOnEscape | Popup.CloseOnPressOutside)
        : Popup.NoAutoClose
    // Instancié via un Loader sans taille : sans parent explicite, le dialogue
    // se centrerait dans ce point de 0×0 en haut à gauche, largeur négative.
    parent: Overlay.overlay
    anchors.centerIn: parent
    width: Math.min(520, Overlay.overlay ? Overlay.overlay.width - 48 : 520)

    // Vrai quand on change d'annotateur en cours de route (l'ancien reste
    // valable) ; faux au premier lancement, où il faut bien répondre.
    property bool allowCancel: false

    background: Rectangle {
        color: Theme.elevated
        radius: Theme.radiusMd
        border.color: Theme.border
        border.width: 1
    }

    onOpened: {
        nameField.currentIndex = -1
        nameField.editText = Annotator.currentName
        orcidField.text = Annotator.currentOrcid
        errorLabel.text = ""
        nameField.forceActiveFocus()
    }

    function submit() {
        if (nameField.editText.trim().length === 0)
            return
        if (Annotator.createAnnotator(nameField.editText, orcidField.text)) {
            errorLabel.text = ""
            dlg.close()
        } else {
            errorLabel.text = Annotator.statusText
        }
    }

    contentItem: ColumnLayout {
        spacing: Theme.s3

        AppLabel {
            Layout.fillWidth: true
            text: qsTr("Votre nom sera enregistré avec chaque poisson que vous identifiez et chaque comportement que vous notez. C'est ce qui permet, plus tard, de savoir qui a déterminé quoi - et de vous créditer si les données sont publiées.")
            font.pixelSize: Theme.fzSm
            color: Theme.textMuted
            wrapMode: Text.WordWrap
        }

        // ── Nom affiché ───────────────────────────────────────────────
        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.s2
            AppLabel {
                text: qsTr("Nom affiché")
                font.pixelSize: Theme.fzSm
            }
            InfoDot {
                diameter: 15
                text: qsTr("Prénom et nom, ou le nom sous lequel vous voulez apparaître dans les données. Il doit permettre à quelqu'un d'autre de vous reconnaître dans six mois.")
            }
            Item { Layout.fillWidth: true }
        }

        AppComboBox {
            id: nameField
            Layout.fillWidth: true
            editable: true
            model: Annotator.suggestedNames
            placeholderText: qsTr("Prénom Nom")
            onEditTextChanged: {
                // Ne jamais recopier l'ORCID de la personne précédente quand
                // un nouveau nom est saisi. Un nom déjà connu le préremplit.
                orcidField.text = Annotator.orcidForName(editText)
            }
            onActivated: {
                editText = currentText
                orcidField.text = Annotator.orcidForName(currentText)
            }
            onAccepted: dlg.submit()
        }

        AppLabel {
            Layout.fillWidth: true
            visible: Annotator.suggestedNames.length > 0
            text: qsTr("Choisissez un nom déjà utilisé, ou saisissez une nouvelle personne.")
            font.pixelSize: Theme.fzXs
            color: Theme.textDim
            wrapMode: Text.WordWrap
        }

        // ── ORCID (facultatif) ────────────────────────────────────────
        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.s2
            AppLabel {
                text: qsTr("ORCID")
                font.pixelSize: Theme.fzSm
            }
            AppLabel {
                text: qsTr("facultatif")
                font.pixelSize: Theme.fzXs
                color: Theme.textDim
            }
            InfoDot {
                diameter: 15
                text: qsTr("L'ORCID est un identifiant international de chercheur (orcid.org), du type 0000-0002-1825-0097. Il vous suit toute votre carrière et lève les homonymies : deux « Martin » ne seront pas confondus quand les observations seront publiées (GBIF, OBIS, article scientifique). Laissez vide si vous n'en avez pas - rien ne sera bloqué.")
            }
            Item { Layout.fillWidth: true }
        }

        AppTextField {
            id: orcidField
            Layout.fillWidth: true
            placeholderText: qsTr("0000-0002-1825-0097")
            onAccepted: dlg.submit()
        }

        AppLabel {
            id: errorLabel
            Layout.fillWidth: true
            visible: text.length > 0
            text: ""
            font.pixelSize: Theme.fzXs
            color: Theme.danger
            wrapMode: Text.WordWrap
        }

        AppLabel {
            Layout.fillWidth: true
            text: qsTr("Vous pourrez changer d'annotateur à tout moment depuis la page Sessions.")
            font.pixelSize: Theme.fzXs
            color: Theme.textDim
            wrapMode: Text.WordWrap
        }

        RowLayout {
            Layout.fillWidth: true
            Layout.topMargin: Theme.s1
            spacing: Theme.s2

            Item { Layout.fillWidth: true }

            GhostButton {
                visible: dlg.allowCancel
                text: qsTr("Annuler")
                tooltipText: qsTr("Ferme sans changer l'annotateur courant.")
                onClicked: dlg.close()
            }

            PrimaryButton {
                text: qsTr("C'est moi")
                requires: nameField.editText.trim().length > 0
                disabledReason: qsTr("Indiquez au moins un nom : les annotations ne doivent plus être anonymes.")
                tooltipText: qsTr("Enregistre cette identité et l'utilise pour toutes vos annotations.")
                onClicked: dlg.submit()
            }
        }
    }
}
