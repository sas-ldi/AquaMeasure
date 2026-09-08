import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Window
import AquaMeasure

// Préférences de l'application - ouvertes depuis Édition > Préférences.
//
// Demande explicite du client : voir tous les emplacements réels, pouvoir les
// ouvrir, et pouvoir déplacer la racine des données (disque externe, dossier
// synchronisé). Rien n'est deviné : chaque chemin affiché est celui que
// l'application utilise vraiment.
Item {
    id: root
    property var activeShortcutEditor: null

    // Écouter les clics sans les consommer : même le fond d'une carte ou
    // la barre de navigation doit permettre de quitter la saisie.
    MouseArea {
        objectName: "shortcutOutsideClickArea"
        parent: root.Window.window ? root.Window.window.contentItem : root
        anchors.fill: parent
        z: 10000
        enabled: root.visible && root.activeShortcutEditor !== null
        acceptedButtons: Qt.LeftButton | Qt.RightButton
        onPressed: function(mouse) {
            const editor = root.activeShortcutEditor
            if (editor && editor.activeFocus) {
                const pos = editor.mapFromItem(parent, mouse.x, mouse.y)
                if (!editor.contains(pos))
                    root.forceActiveFocus()
            }
            mouse.accepted = false
        }
    }

    readonly property var behaviorLogos: [
        { "symbol": "●", "label": qsTr("● Rond") },
        { "symbol": "◆", "label": qsTr("◆ Losange") },
        { "symbol": "■", "label": qsTr("■ Carré") },
        { "symbol": "▲", "label": qsTr("▲ Triangle") },
        { "symbol": "★", "label": qsTr("★ Étoile") },
        { "symbol": "✚", "label": qsTr("✚ Croix") },
        { "symbol": "▼", "label": qsTr("▼ Triangle bas") },
        { "symbol": "◐", "label": qsTr("◐ Demi-cercle") },
        { "symbol": "❋", "label": qsTr("❋ Éclat") },
        { "symbol": "⬢", "label": qsTr("⬢ Hexagone") }
    ]

    signal closeRequested()

    Component.onCompleted: {
        Storage.refresh()
        Data.loadEventTypes()
    }

    Shortcut {
        sequence: "Esc"
        enabled: root.visible && !moveDialog.opened
        onActivated: root.closeRequested()
    }

    ScrollView {
        id: settingsScroll
        objectName: "settingsScroll"
        anchors.fill: parent
        clip: true
        ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
        contentWidth: availableWidth

        ColumnLayout {
            width: Math.min(settingsScroll.availableWidth, Theme.contentMaxWidth)
            x: Math.max(0, (settingsScroll.availableWidth - width) / 2)
            spacing: Theme.spaceMd

            // ── En-tête ────────────────────────────────────────────
            ColumnLayout {
                Layout.fillWidth: true
                spacing: Theme.s1

                RowLayout {
                    Layout.fillWidth: true
                    spacing: Theme.s2
                    AppLabel {
                        text: qsTr("Préférences")
                        font.pixelSize: Theme.fzXl
                        font.weight: Font.DemiBold
                    }
                    InfoDot {
                        diameter: 15
                        text: qsTr("Réglages propres à ce poste : emplacement des données, sauvegardes et identité utilisée pour signer les annotations.")
                    }
                    Item { Layout.fillWidth: true }
                    GhostButton {
                        small: true
                        text: qsTr("Recalculer les tailles")
                        requires: !Storage.busy
                        disabledReason: qsTr("Un traitement est en cours - patientez.")
                        tooltipText: qsTr("Reparcourt les dossiers pour remesurer leur taille. Le calcul se fait en arrière-plan, l'application reste utilisable.")
                        onClicked: Storage.recompute()
                    }
                    GhostButton {
                        small: true
                        implicitWidth: implicitHeight
                        text: "\u2715"
                        tooltipText: qsTr("Fermer les Préférences (Échap)")
                        onClicked: root.closeRequested()
                    }
                }

                AppLabel {
                    Layout.fillWidth: true
                    text: qsTr("Stockage, sauvegarde et identité de l'annotateur")
                    font.pixelSize: Theme.fzSm
                    color: Theme.textMuted
                    wrapMode: Text.WordWrap
                }
            }

            // ══════════════════════════════════════════════════════
            //  1 · Racine des données
            // ══════════════════════════════════════════════════════
            AppCard {
                Layout.fillWidth: true
                title: qsTr("Emplacement des données")
                iconName: "folder"
                info: qsTr("Le dossier qui contient tout ce que l'application écrit. Par défaut, c'est le dossier du programme. Vous pouvez le placer ailleurs - un disque externe, un dossier synchronisé - pour libérer de la place ou pour que vos données soient sauvegardées automatiquement par ailleurs.")
                subtitle: Storage.isDefaultRoot
                    ? qsTr("Emplacement par défaut (dossier du programme)")
                    : qsTr("Emplacement choisi par vous")

                AppLabel {
                    Layout.fillWidth: true
                    text: qsTr("La base, les calibrations, les exports et les vignettes sont regroupés ici. Les vidéos originales restent à leur emplacement : AquaMeasure ne les copie pas.")
                    color: Theme.textMuted
                    font.pixelSize: Theme.fzSm
                    wrapMode: Text.WordWrap
                }

                TextEdit {
                    Layout.fillWidth: true
                    text: Storage.dataRoot
                    readOnly: true
                    selectByMouse: true
                    wrapMode: Text.WrapAnywhere
                    color: Theme.text
                    font.family: Theme.monoFamily
                    font.pixelSize: Theme.fzSm
                }

                RowLayout {
                    Layout.fillWidth: true
                    spacing: Theme.s2

                    PrimaryButton {
                        text: qsTr("Modifier…")
                        requires: !Storage.busy
                        disabledReason: qsTr("Une copie est en cours - attendez qu'elle se termine.")
                        tooltipText: qsTr("Choisit un nouveau dossier racine. L'application vous proposera ensuite de copier vos données existantes ; l'ancien emplacement n'est jamais effacé.")
                        onClicked: Storage.pickDataRoot()
                    }
                    GhostButton {
                        text: qsTr("Ouvrir")
                        tooltipText: qsTr("Ouvre la racine des données dans l'explorateur de fichiers.")
                        onClicked: Storage.openPath(Storage.dataRoot)
                    }
                    GhostButton {
                        text: qsTr("Revenir au dossier par défaut")
                        requires: !Storage.isDefaultRoot && !Storage.busy
                        disabledReason: Storage.busy
                            ? qsTr("Une copie est en cours - attendez qu'elle se termine.")
                            : qsTr("Vous êtes déjà sur l'emplacement par défaut.")
                        tooltipText: qsTr("Repointe l'application vers le dossier du programme. Aucun fichier n'est déplacé ni supprimé.")
                        onClicked: Storage.resetDataRoot()
                    }
                    Item { Layout.fillWidth: true }
                }

                // Progression de la copie
                ColumnLayout {
                    Layout.fillWidth: true
                    visible: Storage.busy
                    spacing: Theme.s1

                    ProgressBar {
                        Layout.fillWidth: true
                        indeterminate: true
                    }
                    AppLabel {
                        Layout.fillWidth: true
                        text: Storage.progressText
                        font.pixelSize: Theme.fzXs
                        color: Theme.textMuted
                        elide: Text.ElideMiddle
                    }
                }

                // Changer `storage.json` suffit aux chemins, mais pas aux
                // objets déjà vivants : la base est ouverte sur l'ancien
                // fichier et les pages ont chargé leur registre depuis lui.
                // On le dit plutôt que d'afficher une page cohérente sur des
                // données qui ne le sont pas.
                Rectangle {
                    Layout.fillWidth: true
                    visible: Storage.restartNeeded
                    implicitHeight: restartLabel.implicitHeight + Theme.s3 * 2
                    radius: Theme.radiusSm
                    color: Theme.warnSoft
                    border.color: Theme.warn
                    border.width: 1

                    AppLabel {
                        id: restartLabel
                        anchors.fill: parent
                        anchors.margins: Theme.s3
                        text: qsTr("Emplacement changé. Fermez et rouvrez AquaMeasure pour que toutes les pages travaillent sur le nouveau dossier : la base d'annotations est encore ouverte sur l'ancien.")
                        color: Theme.warn
                        wrapMode: Text.WordWrap
                    }
                }

                RowLayout {
                    Layout.fillWidth: true
                    spacing: Theme.s2

                    AppLabel {
                        text: qsTr("Réglage enregistré dans :")
                        font.pixelSize: Theme.fzXs
                        color: Theme.textDim
                    }
                    InfoDot {
                        diameter: 14
                        text: qsTr("Ce petit fichier ne contient que votre choix d'emplacement, pas vos données. Il vit dans votre profil Windows, donc il survit à une mise à jour de l'application et ne dépend pas de l'endroit d'où vous la lancez - c'est ce qui empêche les calibrations de se perdre.")
                    }
                    AppLabel {
                        Layout.fillWidth: true
                        text: Storage.configPath
                        font.pixelSize: Theme.fzXs
                        font.family: Theme.monoFamily
                        color: Theme.textDim
                        elide: Text.ElideMiddle
                    }
                }
            }

            // ══════════════════════════════════════════════════════
            //  2 · Détail des emplacements
            // ══════════════════════════════════════════════════════
            AppCard {
                Layout.fillWidth: true
                title: qsTr("Espace utilisé")
                iconName: "database"
                info: qsTr("Le détail de ce qui est écrit et où. Chaque ligne s'ouvre dans l'explorateur de fichiers, et le (i) dit ce qu'on perdrait si l'emplacement disparaissait.")

                StorageLocationRow {
                    label: qsTr("Base d'annotations")
                    path: Storage.dbPath
                    warn: !Storage.dbExists
                    details: Storage.dbExists
                        ? qsTr("%1 · modifiée le %2").arg(Storage.dbSize).arg(Storage.dbModified)
                        : qsTr("fichier absent")
                    info: qsTr("Le fichier qui contient TOUT votre travail : les poissons encadrés, les espèces déterminées, les mesures, les comportements, les sessions. C'est la seule source de vérité. Si ce fichier est perdu, tout le travail d'annotation est perdu avec lui - les vidéos ne suffisent pas à le reconstituer. C'est lui que sauvegarde le bouton « Sauvegarder maintenant » ci-dessous.")
                }

                StorageLocationRow {
                    label: qsTr("Calibrations")
                    path: Storage.calibrationsPath
                    details: qsTr("%1 · profil actif « %2 » · %3 profil(s) complet(s)")
                        .arg(Storage.calibrationsSize)
                        .arg(Storage.calibrationProfile)
                        .arg(Storage.calibrationProfileCount >= 0
                             ? Storage.calibrationProfileCount : "?")
                    info: qsTr("Les mesures internes du banc stéréo : la géométrie des deux caméras, leur écartement, et le repère de synchronisation des deux vidéos. Sans elles, plus aucune longueur ne peut être calculée et les images ne peuvent plus être redressées. Les perdre oblige à refaire une calibration avec la mire - et invalide la comparaison avec les mesures passées.")
                }

                StorageLocationRow {
                    label: qsTr("Exports")
                    path: Storage.exportsPath
                    details: qsTr("%1 · %2 export(s) tracé(s)%3")
                        .arg(Storage.exportsSize)
                        .arg(Storage.exportCount >= 0 ? Storage.exportCount : "?")
                        .arg(Storage.lastExport.length > 0
                             ? qsTr(" · dernier : %1").arg(Storage.lastExport) : "")
                    info: qsTr("Les jeux de données produits : tableaux CSV pour l'analyse, datasets d'images pour réentraîner l'IA. Chaque export est autonome - il emporte ses images - et daté ; aucun n'écrase le précédent. Les perdre ne perd pas votre travail (on peut ré-exporter depuis la base), mais perd la trace exacte de ce qui a été livré à un collègue ou publié.")
                }

                StorageLocationRow {
                    label: qsTr("Médias et vignettes")
                    path: Storage.mediaPath
                    details: Storage.mediaSize
                    info: qsTr("Les images extraites des vidéos et gardées sous la main : vignettes du registre, images de référence des espèces. Ce sont des fichiers recalculables - les perdre ne coûte que du temps de recalcul, à condition d'avoir encore les vidéos. Les vidéos originales, elles, ne sont jamais copiées ici.")
                }
            }

            // ══════════════════════════════════════════════════════
            //  3 · Sauvegarde
            // ══════════════════════════════════════════════════════
            AppCard {
                Layout.fillWidth: true
                title: qsTr("Sauvegarde")
                iconName: "save"
                info: qsTr("Copier la base d'annotations ailleurs est la SEULE protection contre une panne de disque, une erreur de manipulation ou un vol. L'application ne le fait pas toute seule : c'est un geste volontaire, à répéter après chaque grosse session.")

                Rectangle {
                    Layout.fillWidth: true
                    implicitHeight: backupRow.implicitHeight + Theme.s3 * 2
                    radius: Theme.radiusSm
                    color: Storage.backupOverdue ? Theme.warnSoft : Theme.panel
                    border.color: Storage.backupOverdue ? Theme.warn : Theme.border
                    border.width: 1

                    ColumnLayout {
                        id: backupRow
                        anchors.fill: parent
                        anchors.margins: Theme.s3
                        spacing: Theme.s1

                        AppLabel {
                            Layout.fillWidth: true
                            text: Storage.backupSummary
                            color: Storage.backupOverdue ? Theme.warn : Theme.text
                            wrapMode: Text.WordWrap
                        }
                        AppLabel {
                            Layout.fillWidth: true
                            visible: Storage.lastBackupPath.length > 0
                            text: Storage.lastBackupPath
                            font.family: Theme.monoFamily
                            font.pixelSize: Theme.fzXs
                            color: Theme.textDim
                            elide: Text.ElideMiddle
                        }
                    }
                }

                RowLayout {
                    Layout.fillWidth: true
                    spacing: Theme.s2

                    PrimaryButton {
                        text: qsTr("Sauvegarder maintenant")
                        requires: Storage.dbExists && !Storage.busy
                        disabledReason: Storage.dbExists
                            ? qsTr("Un traitement est en cours - patientez.")
                            : qsTr("Aucune base à sauvegarder : le fichier d'annotations n'existe pas encore.")
                        tooltipText: qsTr("Copie le fichier d'annotations dans un dossier de votre choix, avec la date dans son nom. Placez-le sur un autre disque que celui de travail - une copie sur le même disque ne protège de rien.")
                        onClicked: Storage.backupNow()
                    }
                    GhostButton {
                        text: qsTr("Ouvrir la dernière sauvegarde")
                        requires: Storage.lastBackupPath.length > 0
                        disabledReason: qsTr("Aucune sauvegarde enregistrée pour l'instant.")
                        tooltipText: qsTr("Ouvre le dossier où se trouve la dernière copie enregistrée.")
                        onClicked: Storage.openPath(Storage.lastBackupPath)
                    }
                    Item { Layout.fillWidth: true }
                }
            }

            // ══════════════════════════════════════════════════════
            //  4 · Comportements
            // ══════════════════════════════════════════════════════
            AppCard {
                Layout.fillWidth: true
                title: qsTr("Comportements")
                iconName: "point"
                info: qsTr("Créez un événement, par exemple Bouchée ou Passage d’une raie. Ponctuel marque une seule image, avec ou sans piste. Durée décrit une action de In à Out. Les deux se combinent : Broutage sur une durée, puis Bouchées aux instants précis. La lettre de raccourci pose un point sur la piste ; Maj + la lettre le retire.")

                RowLayout {
                    Layout.fillWidth: true
                    spacing: Theme.s2

                    AppTextField {
                        id: behaviorName
                        objectName: "behaviorNameField"
                        Layout.fillWidth: true
                        placeholderText: qsTr("Nom du comportement")
                        onAccepted: addBehaviorButton.clicked()
                    }
                    AppComboBox {
                        id: behaviorLogo
                        objectName: "behaviorLogoSelector"
                        Layout.preferredWidth: 170
                        model: root.behaviorLogos
                        textRole: "label"
                        currentIndex: 0
                    }
                    AppComboBox {
                        id: behaviorScope
                        objectName: "behaviorScopeSelector"
                        Layout.preferredWidth: 150
                        model: [qsTr("Ponctuel"), qsTr("Durée")]
                        currentIndex: 0
                    }
                    PrimaryButton {
                        id: addBehaviorButton
                        objectName: "addBehaviorButton"
                        text: qsTr("Ajouter")
                        requires: behaviorName.text.trim().length > 0 && Data.dbAvailable
                        disabledReason: !Data.dbAvailable
                            ? qsTr("Base d'annotations indisponible.")
                            : qsTr("Saisissez un nom de comportement.")
                        onClicked: {
                            const scope = behaviorScope.currentIndex === 0 ? "instant" : "interval"
                            const logo = root.behaviorLogos[behaviorLogo.currentIndex].symbol
                            if (Data.addBehaviorType(behaviorName.text, scope, logo))
                                behaviorName.text = ""
                        }
                    }
                }

                Repeater {
                    model: Data.behaviorTypes
                    delegate: Rectangle {
                        required property var modelData
                        Layout.fillWidth: true
                        implicitHeight: behaviorRow.implicitHeight + Theme.s2 * 2
                        radius: Theme.radiusSm
                        color: Theme.panel
                        border.color: Theme.border

                        RowLayout {
                            id: behaviorRow
                            anchors.fill: parent
                            anchors.margins: Theme.s2
                            spacing: Theme.s2

                            Rectangle {
                                Layout.preferredWidth: 30
                                Layout.preferredHeight: 30
                                radius: 15
                                color: modelData.color || Theme.accent
                                border.color: Qt.lighter(color, 1.35)
                                AppLabel {
                                    anchors.centerIn: parent
                                    text: modelData.symbol || "●"
                                    color: "#ffffff"
                                    font.pixelSize: Theme.fzMd
                                    font.weight: Font.Bold
                                }
                            }

                            AppLabel {
                                Layout.fillWidth: true
                                text: modelData.label
                                color: modelData.isActive ? Theme.text : Theme.textDim
                            }
                            AppLabel {
                                text: modelData.scope === "instant"
                                    ? qsTr("Ponctuel") : qsTr("Durée")
                                color: Theme.textMuted
                                font.pixelSize: Theme.fzXs
                            }

                            // Lettre de raccourci : le champ manquait, donc un
                            // type créé ici n'en avait jamais et le clavier
                            // restait inutilisable pour marquer vite.
                            RowLayout {
                                spacing: Theme.s1
                                visible: typeof Pecks !== "undefined" && modelData.scope === "instant"
                                AppLabel {
                                    text: qsTr("Touche")
                                    color: Theme.textMuted
                                    font.pixelSize: Theme.fzXs
                                }
                                AppTextField {
                                    id: shortcutEditor
                                    objectName: "behaviorShortcutField"
                                    Layout.preferredWidth: 46
                                    horizontalAlignment: Text.AlignHCenter
                                    maximumLength: 1
                                    placeholderText: "—"
                                    text: modelData.shortcut || ""
                                    onAccepted: root.forceActiveFocus()
                                    onActiveFocusChanged: {
                                        if (activeFocus)
                                            root.activeShortcutEditor = shortcutEditor
                                        else if (root.activeShortcutEditor === shortcutEditor)
                                            root.activeShortcutEditor = null
                                    }
                                    onEditingFinished: {
                                        if (text.toUpperCase()
                                                === (modelData.shortcut || ""))
                                            return
                                        Pecks.setTypeShortcut(modelData.id, text)
                                    }
                                }
                            }
                            AppLabel {
                                visible: !modelData.isActive
                                text: qsTr("désactivé")
                                color: Theme.warn
                                font.pixelSize: Theme.fzXs
                            }
                            GhostButton {
                                objectName: "behaviorRemoveButton"
                                small: true
                                visible: !modelData.isBuiltin && modelData.isActive
                                text: modelData.usage > 0 ? qsTr("Désactiver") : qsTr("Retirer")
                                // Le garde-fou « ce type est figé par le suivi
                                // en cours » n'a plus d'objet : le suivi
                                // conservé ne fige aucun comportement, il ne
                                // produit qu'une piste.
                                tooltipText: modelData.usage > 0
                                    ? qsTr("Désactive le type sans supprimer ses annotations historiques.")
                                    : qsTr("Supprime ce type encore inutilisé.")
                                onClicked: Data.removeBehaviorType(modelData.id)
                            }
                            AppLabel {
                                visible: modelData.isBuiltin
                                text: qsTr("intégré")
                                color: Theme.textDim
                                font.pixelSize: Theme.fzXs
                            }
                        }
                    }
                }

                AppLabel {
                    Layout.fillWidth: true
                    visible: Data.statusText.length > 0
                    text: Data.statusText
                    color: Theme.textDim
                    font.pixelSize: Theme.fzXs
                    wrapMode: Text.WordWrap
                }
            }

            // ══════════════════════════════════════════════════════
            //  5 · Identité
            // ══════════════════════════════════════════════════════
            AppCard {
                Layout.fillWidth: true
                title: qsTr("Identité")
                iconName: "user"
                info: qsTr("Chaque poisson identifié et chaque comportement noté sont signés de ce nom. C'est ce qui permet de savoir plus tard qui a déterminé quoi, et de créditer les bonnes personnes si les données sont publiées.")

                RowLayout {
                    Layout.fillWidth: true
                    spacing: Theme.s3

                    AppLabel {
                        text: qsTr("Annotateur courant")
                        color: Theme.textMuted
                    }
                    AppLabel {
                        text: Annotator.currentName.length > 0
                            ? Annotator.currentName
                            : qsTr("non renseigné")
                        font.weight: Font.DemiBold
                        color: Annotator.currentName.length > 0 ? Theme.text : Theme.warn
                    }
                    AppLabel {
                        visible: Annotator.currentOrcid.length > 0
                        text: qsTr("ORCID %1").arg(Annotator.currentOrcid)
                        font.family: Theme.monoFamily
                        font.pixelSize: Theme.fzXs
                        color: Theme.textDim
                    }
                    GhostButton {
                        small: true
                        text: Annotator.count > 0 ? qsTr("Changer…") : qsTr("S'identifier…")
                        requires: Annotator.dbAvailable
                        disabledReason: qsTr("Base d'annotations indisponible - vérifiez l'installation de fish-vision.")
                        tooltipText: qsTr("Déclare une autre personne comme annotateur courant (nom, ORCID facultatif).")
                        onClicked: Annotator.requestDialog()
                    }
                    Item { Layout.fillWidth: true }
                }
            }

            AppLabel {
                Layout.fillWidth: true
                text: Storage.statusText
                visible: Storage.statusText.length > 0
                color: Theme.textDim
                font.pixelSize: Theme.fzXs
                wrapMode: Text.WordWrap
            }
        }
    }

    // ══════════════════════════════════════════════════════════
    //  Dialogue : que faire des données existantes ?
    // ══════════════════════════════════════════════════════════
    //
    // `parent: Overlay.overlay` - leçon du correctif 3b44ff7 : un Dialog
    // instancié dans un élément sans taille se centrerait dans un point 0×0
    // en haut à gauche, avec une largeur négative.
    Dialog {
        id: moveDialog

        property string target: ""

        title: qsTr("Déplacer la racine des données")
        modal: true
        closePolicy: Popup.CloseOnEscape
        parent: Overlay.overlay
        anchors.centerIn: parent
        width: Math.min(600, Overlay.overlay ? Overlay.overlay.width - 48 : 600)

        background: Rectangle {
            color: Theme.elevated
            radius: Theme.radiusMd
            border.color: Theme.border
            border.width: 1
        }

        contentItem: ColumnLayout {
            spacing: Theme.s3

            AppLabel {
                Layout.fillWidth: true
                text: qsTr("Nouvel emplacement :")
                font.pixelSize: Theme.fzSm
                color: Theme.textMuted
            }
            AppLabel {
                Layout.fillWidth: true
                text: moveDialog.target
                font.family: Theme.monoFamily
                font.pixelSize: Theme.fzSm
                wrapMode: Text.WrapAnywhere
            }

            AppLabel {
                Layout.fillWidth: true
                text: qsTr("Voulez-vous y copier les données actuelles (base d'annotations, calibrations, exports, images) ?")
                wrapMode: Text.WordWrap
            }

            AppLabel {
                Layout.fillWidth: true
                text: qsTr("Dans les deux cas, l'ancien emplacement est CONSERVÉ tel quel : rien n'y est effacé. Une fois la copie vérifiée, c'est à vous de décider de le supprimer ou non.")
                font.pixelSize: Theme.fzXs
                color: Theme.textDim
                wrapMode: Text.WordWrap
            }

            RowLayout {
                Layout.fillWidth: true
                Layout.topMargin: Theme.s1
                spacing: Theme.s2

                GhostButton {
                    text: qsTr("Annuler")
                    tooltipText: qsTr("Ferme sans rien changer : la racine des données reste celle d'aujourd'hui.")
                    onClicked: moveDialog.close()
                }
                Item { Layout.fillWidth: true }
                GhostButton {
                    text: qsTr("Ne rien copier")
                    tooltipText: qsTr("Pointe l'application vers le nouveau dossier sans y copier quoi que ce soit : elle y repartira d'une base vide. À réserver au démarrage d'un nouveau poste.")
                    onClicked: {
                        moveDialog.close()
                        Storage.applyDataRoot(false)
                    }
                }
                PrimaryButton {
                    text: qsTr("Copier puis basculer")
                    tooltipText: qsTr("Copie d'abord toutes les données vers le nouveau dossier, puis y bascule l'application. La copie peut durer plusieurs minutes selon la taille des exports.")
                    onClicked: {
                        moveDialog.close()
                        Storage.applyDataRoot(true)
                    }
                }
            }
        }
    }

    Connections {
        target: Storage
        function onRootProposalRequested(path) {
            moveDialog.target = path
            moveDialog.open()
        }
    }
}
