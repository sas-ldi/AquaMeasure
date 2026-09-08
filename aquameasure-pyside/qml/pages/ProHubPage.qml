import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import AquaMeasure

Item {
    id: root

    // Largeur de la console alignée sur les autres pages, mais bornée par la
    // largeur réelle : la formule partagée impose un plancher de
    // Theme.contentMaxWidth, ce qui débordait sur une fenêtre plus étroite.
    readonly property int consoleWidth: Math.min(
        Math.max(240, width - Theme.spaceLg * 2),
        Theme.consoleMaxWidth,
        Math.round(Math.max(Theme.contentMaxWidth, width * Theme.consoleWidthRatio))
    )

    ColumnLayout {
        anchors.fill: parent
        spacing: Theme.spaceMd

        ScrollView {
            id: scroll
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
            contentWidth: availableWidth

            // Colonne centrée et bornée, comme les pages Calibration et Mesure.
            // Elle occupait auparavant toute la largeur sans marge, collée au
            // bord gauche.
            ColumnLayout {
                id: contentCol
                width: Math.min(Theme.contentMaxWidth,
                                scroll.availableWidth - Theme.spaceLg * 2)
                x: Math.max(Theme.spaceLg, (scroll.availableWidth - width) / 2)
                spacing: Theme.spaceMd

                Item {
                    Layout.fillWidth: true
                    Layout.preferredHeight: Theme.spaceLg
                }

                AppLabel {
                    Layout.fillWidth: true
                    text: qsTr("Hub datasets - Mode Pro")
                    font.pixelSize: Theme.fzLg
                    font.weight: Font.DemiBold
                }

                AppLabel {
                    Layout.fillWidth: true
                    muted: true
                    wrapMode: Text.WordWrap
                    font.pixelSize: Theme.fzSm
                    text: qsTr("Outils de préparation des jeux d'entraînement : récupérer un jeu public, réimporter un export CVAT, regénérer COCO ou YOLO depuis la base, puis ré-entraîner le détecteur. Rien ici ne touche vos mesures ni vos sessions.")
                }

                AppCard {
                    Layout.fillWidth: true
                    title: qsTr("Téléchargement")
                    iconName: "import"
                    subtitle: qsTr("Jeux de poissons publics, prêts pour un pré-entraînement YOLO")
                    info: qsTr("Ces jeux ne viennent pas de YOLO : ce sont des jeux publics annotés par d'autres équipes (Roboflow, Zenodo, Orange/Tenaka), retenus par le projet et téléchargés par fish-vision/scripts/download_public_dataset.py. Ils servent à donner une base au détecteur avant de l'affiner sur vos propres annotations.")

                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: Theme.spaceSm

                        RowLayout {
                            Layout.fillWidth: true
                            spacing: Theme.spaceSm

                            AppLabel {
                                text: qsTr("Jeu de données")
                                color: Theme.textMuted
                            }

                            // Le champ etait une saisie libre alors que seuls
                            // sept noms exacts sont acceptes : une faute de
                            // frappe faisait echouer le telechargement sans
                            // qu'on sache ou trouver la liste.
                            AppComboBox {
                                id: datasetCombo
                                Layout.fillWidth: true
                                Layout.minimumWidth: 220
                                model: ProTools.datasetOptions
                                textRole: "label"
                                currentIndex: ProTools.datasetIndex
                                onActivated: ProTools.selectDatasetAt(currentIndex)
                            }

                            AppButton {
                                text: qsTr("Télécharger")
                                primary: true
                                enabled: !ProTools.busy
                                onClicked: ProTools.downloadPublicDataset()
                            }
                        }

                        AppLabel {
                            Layout.fillWidth: true
                            wrapMode: Text.WordWrap
                            font.pixelSize: Theme.fzXs
                            color: Theme.textDim
                            text: qsTr("Certains jeux pèsent plusieurs gigaoctets ; les jeux Roboflow demandent une clé ROBOFLOW_API_KEY. Le détail de chaque jeu est indiqué dans la liste.")
                        }
                    }
                }

                AppCard {
                    Layout.fillWidth: true
                    title: qsTr("Import")
                    iconName: "folder"
                    subtitle: qsTr("Export CVAT → base SQLite")
                    AppButton {
                        text: qsTr("Importer export CVAT…")
                        enabled: !ProTools.busy
                        onClicked: ProTools.pickIngestCvat()
                    }
                }

                AppCard {
                    Layout.fillWidth: true
                    title: qsTr("Entraînement")
                    iconName: "detect"
                    Flow {
                        Layout.fillWidth: true
                        spacing: Theme.spaceSm

                        Row {
                            spacing: Theme.spaceSm
                            AppLabel {
                                text: qsTr("Epochs")
                                color: Theme.textMuted
                                anchors.verticalCenter: parent.verticalCenter
                            }
                            AppSpinBox {
                                from: 10; to: 300; value: ProTools.epochs
                                onValueModified: ProTools.epochs = value
                            }
                        }

                        AppButton {
                            text: qsTr("Train detect")
                            enabled: !ProTools.busy
                            onClicked: ProTools.trainDetect()
                        }
                        AppButton {
                            text: qsTr("Ré-entraîner DB")
                            primary: true
                            enabled: !ProTools.busy
                            onClicked: ProTools.retrainFromDb()
                        }
                    }
                }

                AppCard {
                    Layout.fillWidth: true
                    title: qsTr("Audit")
                    iconName: "list"
                    Flow {
                        Layout.fillWidth: true
                        spacing: Theme.spaceSm
                        AppButton {
                            text: qsTr("Audit dataset")
                            enabled: !ProTools.busy
                            onClicked: ProTools.auditDataset()
                        }
                        GhostButton {
                            text: qsTr("Annuler job")
                            enabled: ProTools.busy
                            onClicked: ProTools.cancelJob()
                        }
                    }
                }

                Item {
                    Layout.fillWidth: true
                    Layout.preferredHeight: Theme.spaceLg
                }
            }
        }

        ConsolePanel {
            Layout.fillWidth: true
            Layout.preferredWidth: root.consoleWidth
            Layout.maximumWidth: root.consoleWidth
            Layout.alignment: Qt.AlignHCenter
            bodyHeight: 180
            logModel: ProTools.logs
            autoScroll: true
        }
    }
}
