import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs
import QtQuick.Layouts
import AquaMeasure

// Gestionnaire des modeles de detection : choix, installation, comparaison,
// ajout de poids locaux et mise a jour du catalogue.
Popup {
    id: dlg

    property int tab: 0

    parent: Overlay.overlay
    modal: true
    focus: true
    closePolicy: Popup.CloseOnEscape
    padding: Theme.spaceLg
    width: Math.min(760, Overlay.overlay ? Overlay.overlay.width - Theme.spaceXl * 2 : 760)
    height: Overlay.overlay ? Overlay.overlay.height * 0.86 : 720

    onAboutToShow: {
        const o = Overlay.overlay
        if (o) {
            x = Math.round(Math.max(0, (o.width - width) / 2))
            y = Math.round(Math.max(0, (o.height - height) / 2))
        }
        Detectors.refresh()
        Qt.callLater(function() {
            samPromptField.text = Detectors.sam3Prompt
        })
    }

    background: Rectangle {
        radius: Theme.radiusMd
        color: Theme.surface
        border.color: Theme.border
        border.width: 1
    }

    FileDialog {
        id: weightsPicker
        title: qsTr("Choisir un fichier de poids")
        nameFilters: [
            qsTr("Poids de modele (*.pt *.onnx *.pth *.engine *.torchscript)"),
            qsTr("Tous les fichiers (*)")
        ]
        onAccepted: Detectors.addLocalModel(selectedFile, "")
    }

    contentItem: ColumnLayout {
        spacing: Theme.spaceMd

        // ── En-tete ───────────────────────────────────────────────
        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.s3

            ColumnLayout {
                spacing: 2
                AppLabel {
                    text: qsTr("Modèles de détection")
                    font.pixelSize: Theme.fzLg
                    font.weight: Font.DemiBold
                }
                AppLabel {
                    text: qsTr("%1 modèle(s) prêt(s) sur %2 - actif : %3")
                        .arg(Detectors.installedCount)
                        .arg(Detectors.models.length)
                        .arg(Detectors.activeLabel)
                    font.pixelSize: Theme.fzXs
                    color: Theme.textMuted
                }
            }

            Item { Layout.fillWidth: true }

            GhostButton {
                small: true
                text: qsTr("Actualiser")
                onClicked: Detectors.refresh()
            }
            GhostButton {
                small: true
                text: qsTr("Fermer")
                onClicked: dlg.close()
            }
        }

        // ── Barre d'état ──────────────────────────────────────────
        Rectangle {
            Layout.fillWidth: true
            visible: Detectors.statusText.length > 0
            implicitHeight: statusLabel.implicitHeight + Theme.s2 * 2
            radius: Theme.radiusSm
            color: Theme.panel2
            border.width: 1
            border.color: Theme.border
            AppLabel {
                id: statusLabel
                anchors.fill: parent
                anchors.margins: Theme.s2
                text: Detectors.statusText
                font.pixelSize: Theme.fzXs
                color: Theme.textMuted
                wrapMode: Text.WordWrap
                verticalAlignment: Text.AlignVCenter
            }
        }

        // ── Onglets ───────────────────────────────────────────────
        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.s2

            GhostButton {
                small: true
                text: qsTr("Catalogue")
                active: dlg.tab === 0
                onClicked: dlg.tab = 0
            }
            GhostButton {
                small: true
                text: qsTr("Comparer")
                active: dlg.tab === 1
                onClicked: dlg.tab = 1
            }
            GhostButton {
                small: true
                text: qsTr("Étendre")
                active: dlg.tab === 2
                onClicked: dlg.tab = 2
            }
            Item { Layout.fillWidth: true }
        }

        // ── Onglet Catalogue ──────────────────────────────────────
        ScrollView {
            visible: dlg.tab === 0
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            ScrollBar.horizontal.policy: ScrollBar.AlwaysOff

            ColumnLayout {
                width: dlg.width - dlg.padding * 2 - 16
                spacing: Theme.s2

                Repeater {
                    model: Detectors.models
                    delegate: DetectorModelRow {
                        required property var modelData
                        entry: modelData
                        active: modelData.id === Detectors.activeId
                    }
                }
            }
        }

        // ── Onglet Comparer ───────────────────────────────────────
        ColumnLayout {
            visible: dlg.tab === 1
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: Theme.s3

            AppLabel {
                Layout.fillWidth: true
                text: qsTr("Exécute tous les modèles installés sur la frame gauche courante et compare leurs résultats. Mettez la vidéo en pause sur une image représentative.")
                font.pixelSize: Theme.fzXs
                color: Theme.textMuted
                wrapMode: Text.WordWrap
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: Theme.s2

                AppLabel {
                    text: qsTr("Confiance (%)")
                    font.pixelSize: Theme.fzSm
                    color: Theme.textMuted
                }
                AppSpinBox {
                    id: compareConf
                    from: 0
                    to: 95
                    value: 0
                    Layout.preferredWidth: 132
                }
                AppLabel {
                    text: compareConf.value === 0 ? qsTr("(seuil propre à chaque modèle)") : ""
                    font.pixelSize: Theme.fzXs
                    color: Theme.textDim
                }
                Item { Layout.fillWidth: true }
                AppButton {
                    text: qsTr("Lancer la comparaison")
                    primary: true
                    enabled: !Detectors.busy
                    onClicked: Detectors.compareOnCurrentFrame(compareConf.value / 100.0)
                }
            }

            // En-tete du tableau
            RowLayout {
                Layout.fillWidth: true
                spacing: Theme.s2
                visible: Detectors.comparison.length > 0

                AppLabel {
                    Layout.fillWidth: true
                    text: qsTr("Modèle")
                    font.pixelSize: Theme.fzXs
                    color: Theme.textDim
                }
                AppLabel {
                    Layout.preferredWidth: 70
                    text: qsTr("Poissons")
                    horizontalAlignment: Text.AlignRight
                    font.pixelSize: Theme.fzXs
                    color: Theme.textDim
                }
                AppLabel {
                    Layout.preferredWidth: 80
                    text: qsTr("Durée")
                    horizontalAlignment: Text.AlignRight
                    font.pixelSize: Theme.fzXs
                    color: Theme.textDim
                }
                AppLabel {
                    Layout.preferredWidth: 70
                    text: qsTr("Conf. max")
                    horizontalAlignment: Text.AlignRight
                    font.pixelSize: Theme.fzXs
                    color: Theme.textDim
                }
                Item { Layout.preferredWidth: 74 }
            }

            ScrollView {
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                ScrollBar.horizontal.policy: ScrollBar.AlwaysOff

                ColumnLayout {
                    width: dlg.width - dlg.padding * 2 - 16
                    spacing: Theme.s1

                    Repeater {
                        model: Detectors.comparison
                        delegate: Rectangle {
                            required property var modelData

                            Layout.fillWidth: true
                            implicitHeight: 34
                            radius: Theme.radiusSm
                            color: modelData.id === Detectors.activeId ? Theme.accentSoft : Theme.panel2
                            border.width: 1
                            border.color: Theme.border

                            RowLayout {
                                anchors.fill: parent
                                anchors.leftMargin: Theme.s3
                                anchors.rightMargin: Theme.s3
                                spacing: Theme.s2

                                AppLabel {
                                    Layout.fillWidth: true
                                    text: modelData.label
                                    font.pixelSize: Theme.fzSm
                                    color: modelData.error ? Theme.danger : Theme.text
                                    elide: Text.ElideRight
                                }
                                AppLabel {
                                    Layout.preferredWidth: 70
                                    text: modelData.error ? "-" : String(modelData.count)
                                    horizontalAlignment: Text.AlignRight
                                    font.family: Theme.monoFamily
                                    font.pixelSize: Theme.fzSm
                                    color: Theme.text
                                }
                                AppLabel {
                                    Layout.preferredWidth: 80
                                    text: qsTr("%1 ms").arg(modelData.elapsedMs)
                                    horizontalAlignment: Text.AlignRight
                                    font.family: Theme.monoFamily
                                    font.pixelSize: Theme.fzSm
                                    color: Theme.textMuted
                                }
                                AppLabel {
                                    Layout.preferredWidth: 70
                                    text: modelData.bestConf > 0 ? modelData.bestConf.toFixed(2) : "-"
                                    horizontalAlignment: Text.AlignRight
                                    font.family: Theme.monoFamily
                                    font.pixelSize: Theme.fzSm
                                    color: Theme.textMuted
                                }
                                GhostButton {
                                    small: true
                                    text: qsTr("Utiliser")
                                    enabled: modelData.id !== Detectors.activeId && !modelData.error
                                    onClicked: Detectors.setActive(modelData.id)
                                }
                            }
                        }
                    }
                }
            }
        }

        // ── Onglet Étendre ────────────────────────────────────────
        ScrollView {
            visible: dlg.tab === 2
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            ScrollBar.horizontal.policy: ScrollBar.AlwaysOff

            ColumnLayout {
                width: dlg.width - dlg.padding * 2 - 16
                spacing: Theme.spaceMd

                AppCard {
                    Layout.fillWidth: true
                    title: qsTr("Ajouter des poids depuis le disque")
                    subtitle: qsTr("Un fichier .pt ou .engine part sur Ultralytics, .onnx sur ONNX Runtime, .pth sur RF-DETR. Le diagnostic charge réellement le fichier : importez uniquement des poids provenant d'une source fiable.")

                    AppButton {
                        text: qsTr("Choisir un fichier…")
                        onClicked: weightsPicker.open()
                    }
                }

                AppCard {
                    Layout.fillWidth: true
                    title: qsTr("Prompt SAM 3")
                    subtitle: qsTr("Écrivez en anglais ce que SAM 3 doit détecter. Le prompt change la requête locale ; il ne réentraîne pas le modèle et ne télécharge pas de nouveaux poids.")

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: Theme.s2

                        AppTextField {
                            id: samPromptField
                            Layout.fillWidth: true
                            placeholderText: "fish, underwater fish, shark…"
                            text: Detectors.sam3Prompt
                        }
                        AppButton {
                            text: qsTr("Appliquer et tester")
                            primary: true
                            enabled: samPromptField.text.trim().length > 0 && !Detectors.busy
                            onClicked: Detectors.setSam3Prompt(samPromptField.text)
                        }
                    }

                    AppLabel {
                        Layout.fillWidth: true
                        text: qsTr("Au tout premier test, SAM 3 peut télécharger son checkpoint depuis Hugging Face et demander une authentification. Les changements de prompt suivants réutilisent les mêmes poids.")
                        wrapMode: Text.WordWrap
                        font.pixelSize: Theme.fzXs
                        color: Theme.textDim
                    }
                }

                AppCard {
                    id: inspectionCard
                    readonly property var inspection: Detectors.modelInspection

                    Layout.fillWidth: true
                    visible: (inspection.id || "").length > 0
                    title: inspection.state === "running"
                        ? qsTr("Diagnostic du modèle en cours…")
                        : qsTr("Diagnostic du modèle")
                    subtitle: inspection.label || ""

                    Repeater {
                        model: [
                            { "label": qsTr("Moteur détecté"), "value": inspectionCard.inspection.engine || "-" },
                            { "label": qsTr("Architecture déclarée"), "value": inspectionCard.inspection.architecture || "-" },
                            { "label": qsTr("Tâche"), "value": inspectionCard.inspection.task || "-" },
                            { "label": qsTr("Classes"), "value": inspectionCard.inspection.classCount !== undefined ? String(inspectionCard.inspection.classCount) : "-" },
                            { "label": qsTr("Noms"), "value": inspectionCard.inspection.classNamesLabel || (inspectionCard.inspection.classNames || []).join(", ") || "-" },
                            { "label": qsTr("Taille d'entrée recommandée"), "value": inspectionCard.inspection.inputSize ? String(inspectionCard.inspection.inputSize) : "-" },
                            { "label": qsTr("Compatibilité AquaMeasure"), "value": inspectionCard.inspection.state === "running" ? qsTr("vérification…") : (inspectionCard.inspection.compatible ? qsTr("validée") : qsTr("non validée")) },
                            { "label": qsTr("Test d'inférence"), "value": inspectionCard.inspection.state === "running" ? qsTr("en cours…") : (inspectionCard.inspection.inferencePassed ? qsTr("réussi") : qsTr("échoué")) }
                        ]
                        delegate: RowLayout {
                            required property var modelData

                            Layout.fillWidth: true
                            spacing: Theme.s2

                            AppLabel {
                                Layout.preferredWidth: 220
                                text: modelData.label
                                font.pixelSize: Theme.fzSm
                                color: Theme.textMuted
                            }
                            AppLabel {
                                Layout.fillWidth: true
                                text: modelData.value
                                font.pixelSize: Theme.fzSm
                                font.family: Theme.monoFamily
                                color: inspectionCard.inspection.state === "error" ? Theme.danger : Theme.text
                                wrapMode: Text.WordWrap
                            }
                        }
                    }

                    AppLabel {
                        Layout.fillWidth: true
                        visible: (inspectionCard.inspection.error || "").length > 0
                        text: inspectionCard.inspection.error || ""
                        wrapMode: Text.WordWrap
                        font.pixelSize: Theme.fzXs
                        color: Theme.danger
                    }

                    GhostButton {
                        small: true
                        visible: inspectionCard.inspection.state !== "running"
                        text: qsTr("Relancer le diagnostic")
                        enabled: !Detectors.busy
                        onClicked: Detectors.inspectModel(inspectionCard.inspection.id)
                    }
                }

                AppCard {
                    Layout.fillWidth: true
                    title: qsTr("Catalogue distant")
                    subtitle: qsTr("URL d'un catalogue JSON publié par l'équipe. Récupérer de nouveaux modèles ne demande alors aucune mise à jour du logiciel.")

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: Theme.s2

                        AppTextField {
                            id: catalogField
                            Layout.fillWidth: true
                            text: Detectors.catalogUrl
                            placeholderText: "https://…/detectors.json"
                        }
                        AppButton {
                            text: qsTr("Mettre à jour")
                            primary: true
                            enabled: !Detectors.busy
                            onClicked: Detectors.updateCatalog(catalogField.text)
                        }
                    }
                }

                AppCard {
                    Layout.fillWidth: true
                    title: qsTr("Moteurs d'inférence")
                    subtitle: qsTr("Un moteur absent n'empêche pas le logiciel de tourner : seuls les modèles de cette famille restent indisponibles.")

                    Repeater {
                        model: Detectors.backends
                        delegate: RowLayout {
                            required property var modelData

                            Layout.fillWidth: true
                            spacing: Theme.s2

                            Rectangle {
                                Layout.alignment: Qt.AlignVCenter
                                Layout.preferredWidth: 8
                                Layout.preferredHeight: 8
                                radius: 4
                                color: modelData.installed ? Theme.ok : Theme.textDim
                            }
                            AppLabel {
                                Layout.fillWidth: true
                                text: modelData.label
                                font.pixelSize: Theme.fzSm
                                color: modelData.installed ? Theme.text : Theme.textMuted
                            }
                            // Ce qui manque n'est pas toujours un paquet : une
                            // version d'interpreteur et une autorisation
                            // Hugging Face ne s'installent pas avec pip. Les
                            // melanger affichait un « pip install » sans rien
                            // derriere sur la ligne de SAM 3.
                            AppLabel {
                                readonly property var pipPackages:
                                    modelData.missingRequirements.filter(function(r) {
                                        return r.indexOf("python") !== 0
                                            && r !== "huggingface-login"
                                    })
                                visible: !modelData.installed && pipPackages.length > 0
                                text: "pip install " + pipPackages.join(" ")
                                font.family: Theme.monoFamily
                                font.pixelSize: Theme.fzXs
                                color: Theme.warn
                            }
                            AppLabel {
                                visible: !modelData.installed
                                    && modelData.missingRequirements.indexOf("huggingface-login") >= 0
                                    && modelData.missingRequirements.length === 1
                                text: qsTr("autorisation à obtenir ci-dessous")
                                font.pixelSize: Theme.fzXs
                                color: Theme.warn
                            }
                            // Installer depuis l'application, avec SON
                            // interpreteur. Taper la commande dans un terminal
                            // visait souvent le Python du PATH, pas celui du
                            // venv : le paquet arrivait, le moteur restait
                            // absent, et personne ne comprenait pourquoi.
                            RibbonButton {
                                visible: !modelData.installed
                                    && modelData.missingRequirements.filter(function(r) {
                                        return r.indexOf("python") !== 0
                                            && r !== "huggingface-login"
                                    }).length > 0
                                text: qsTr("Installer")
                                small: true
                                enabled: !Detectors.busy
                                infoText: qsTr("Lance pip dans l'environnement de l'application. Comptez quelques minutes selon la taille du moteur.")
                                onClicked: Detectors.installBackend(modelData.id)
                            }
                        }
                    }
                }

                // Autorisation Hugging Face : la seule etape que le logiciel
                // ne peut pas franchir seul. Meta accorde l'acces a SAM 3 au
                // cas par cas ; l'application se contente de deposer le jeton
                // la ou la bibliotheque le relira, ce que faisait `hf auth
                // login` dans un terminal.
                AppCard {
                    Layout.fillWidth: true
                    title: qsTr("Accès Hugging Face (SAM 3)")
                    subtitle: Detectors.huggingFaceReady
                        ? qsTr("Un jeton est enregistré sur ce poste. SAM 3 peut télécharger ses poids au premier chargement.")
                        : qsTr("SAM 3 est distribué par Meta sous accès contrôlé. Demandez l'accès avec un compte Hugging Face, créez un jeton, puis collez-le ici.")

                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: Theme.s2

                        RowLayout {
                            Layout.fillWidth: true
                            spacing: Theme.s2

                            Rectangle {
                                Layout.alignment: Qt.AlignVCenter
                                Layout.preferredWidth: 8
                                Layout.preferredHeight: 8
                                radius: 4
                                color: Detectors.huggingFaceReady ? Theme.ok : Theme.textDim
                            }
                            AppLabel {
                                Layout.fillWidth: true
                                text: Detectors.huggingFaceReady
                                    ? qsTr("Jeton enregistré")
                                    : qsTr("Aucun jeton sur ce poste")
                                font.pixelSize: Theme.fzSm
                                color: Detectors.huggingFaceReady ? Theme.text : Theme.textMuted
                            }
                            RibbonButton {
                                text: qsTr("1. Demander l'accès")
                                small: true
                                infoText: qsTr("Ouvre la page du modèle SAM 3. L'autorisation est accordée manuellement par Meta.")
                                onClicked: Qt.openUrlExternally("https://huggingface.co/facebook/sam3")
                            }
                            RibbonButton {
                                text: qsTr("2. Créer un jeton")
                                small: true
                                infoText: qsTr("Ouvre la page des jetons d'accès de votre compte Hugging Face.")
                                onClicked: Qt.openUrlExternally("https://huggingface.co/settings/tokens")
                            }
                        }

                        RowLayout {
                            Layout.fillWidth: true
                            spacing: Theme.s2

                            AppTextField {
                                id: hfTokenField
                                Layout.fillWidth: true
                                placeholderText: qsTr("3. Collez le jeton ici (hf_…)")
                                // Le jeton ne doit pas rester lisible a
                                // l'ecran ni partir dans la console.
                                echoMode: TextInput.Password
                            }
                            AppButton {
                                text: qsTr("Enregistrer")
                                primary: true
                                enabled: hfTokenField.text.length > 0
                                onClicked: {
                                    if (Detectors.saveHuggingFaceToken(hfTokenField.text))
                                        hfTokenField.text = ""
                                }
                            }
                        }
                    }
                }

                AppCard {
                    Layout.fillWidth: true
                    title: qsTr("Ajouter une nouvelle architecture")
                    subtitle: qsTr("Déposez un fichier Python dans plugins/detectors/ qui déclare une sous-classe de DetectorBackend et appelle register_backend(). Elle apparaîtra ici au prochain démarrage, sans toucher au cœur de l'application.")
                }
            }
        }
    }
}
