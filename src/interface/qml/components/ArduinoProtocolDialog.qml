import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import AquaMeasure

Dialog {
    id: dlg

    title: qsTr("Carte Arduino - commandes série")
    modal: true
    anchors.centerIn: parent
    width: Math.min(680, parent ? parent.width - 48 : 680)
    height: Math.min(720, parent ? parent.height - 80 : 720)

    background: Rectangle {
        color: Theme.elevated
        radius: Theme.radiusMd
        border.color: Theme.border
        border.width: 1
    }

    contentItem: ScrollView {
        clip: true
        ScrollBar.horizontal.policy: ScrollBar.AlwaysOff

        ColumnLayout {
            width: dlg.width - 48
            spacing: Theme.s4

            // ── Bandeau ──
            Rectangle {
                Layout.fillWidth: true
                implicitHeight: bannerCol.implicitHeight + Theme.s4 * 2
                radius: Theme.radiusSm
                color: Theme.accentSoft
                border.color: Theme.accent
                border.width: 1

                ColumnLayout {
                    id: bannerCol
                    anchors.fill: parent
                    anchors.margins: Theme.s4
                    spacing: Theme.s2

                    Text {
                        text: qsTr("Carte Arduino · pilotage simultané des deux caméras")
                        font.family: Theme.fontFamily
                        font.pixelSize: Theme.fzMd
                        font.weight: Font.DemiBold
                        color: Theme.accentText
                        wrapMode: Text.WordWrap
                        Layout.fillWidth: true
                    }
                    Text {
                        text: qsTr("Le PC ne parle jamais aux caméras : il envoie des commandes texte à la carte, qui gère l'alimentation, les boutons et le flash.")
                        font.family: Theme.fontFamily
                        font.pixelSize: Theme.fzSm
                        color: Theme.textMuted
                        wrapMode: Text.WordWrap
                        Layout.fillWidth: true
                    }
                }
            }

            // ── 1. Prérequis ──
            Text {
                text: qsTr("1 · Raccordement et prérequis")
                font.family: Theme.fontFamily
                font.pixelSize: Theme.fzSm
                font.weight: Font.DemiBold
                color: Theme.textMuted
                Layout.fillWidth: true
            }

            Text {
                Layout.fillWidth: true
                wrapMode: Text.WordWrap
                font.family: Theme.fontFamily
                font.pixelSize: Theme.fzSm
                color: Theme.text
                lineHeight: 1.45
                text: qsTr(
                    "• Brancher la carte en USB : elle apparaît en COM sous Windows.\n"
                    + "• L'ouverture du port bascule DTR et redémarre la carte - attendre ~2 s avant la première commande.\n"
                    + "• Au démarrage la carte affiche son menu de configuration : il apparaît dans le journal série.\n"
                    + "• Les caméras sont alimentées par les power banks pilotées par la carte (cam1 / cam2).")
            }

            // ── 2. Série ──
            Text {
                text: qsTr("2 · Paramètres série")
                font.family: Theme.fontFamily
                font.pixelSize: Theme.fzSm
                font.weight: Font.DemiBold
                color: Theme.textMuted
                Layout.fillWidth: true
            }

            GridLayout {
                Layout.fillWidth: true
                columns: 2
                columnSpacing: Theme.s3
                rowSpacing: Theme.s2

                Repeater {
                    model: [
                        [qsTr("Vitesse"), qsTr("%1 baud par défaut - ajustable si le firmware diffère").arg(Device.defaultBaud)],
                        [qsTr("Format"), qsTr("8 bits · pas de parité · 1 stop (8N1)")],
                        [qsTr("Fin de ligne"), qsTr("LF seul (\\n) - ajouté automatiquement à l'envoi")],
                        [qsTr("Réception"), qsTr("Lignes texte, relevées en continu dans le journal")]
                    ]
                    delegate: RowLayout {
                        id: paramRow
                        required property var modelData
                        Layout.fillWidth: true
                        spacing: Theme.s2
                        Text {
                            text: paramRow.modelData[0]
                            font.family: Theme.fontFamily
                            font.pixelSize: Theme.fzXs
                            color: Theme.textDim
                            Layout.preferredWidth: 110
                        }
                        Text {
                            text: paramRow.modelData[1]
                            font.family: Theme.fontFamily
                            font.pixelSize: Theme.fzSm
                            color: Theme.text
                            Layout.fillWidth: true
                            wrapMode: Text.WordWrap
                        }
                    }
                }
            }

            // ── 3. Format ──
            Text {
                text: qsTr("3 · Format de trame")
                font.family: Theme.fontFamily
                font.pixelSize: Theme.fzSm
                font.weight: Font.DemiBold
                color: Theme.textMuted
                Layout.fillWidth: true
            }

            Rectangle {
                Layout.fillWidth: true
                implicitHeight: formatCol.implicitHeight + Theme.s3 * 2
                radius: Theme.radiusSm
                color: Theme.bgElevated
                border.color: Theme.border

                ColumnLayout {
                    id: formatCol
                    anchors.fill: parent
                    anchors.margins: Theme.s3
                    spacing: Theme.s2

                    Text {
                        font.family: Theme.monoFamily
                        font.pixelSize: Theme.fzSm
                        color: Theme.accentText
                        text: "[commande texte ASCII]<LF>"
                    }
                    Text {
                        Layout.fillWidth: true
                        wrapMode: Text.WordWrap
                        font.family: Theme.fontFamily
                        font.pixelSize: Theme.fzXs
                        color: Theme.textDim
                        text: qsTr(
                            "Pas de somme de contrôle : la carte lit une ligne, l'exécute et répond en texte. "
                            + "Les réponses apparaissent dans le journal série préfixées « < ».")
                    }
                }
            }

            // ── 4. Commandes ──
            Text {
                text: qsTr("4 · Menu de configuration de la carte")
                font.family: Theme.fontFamily
                font.pixelSize: Theme.fzSm
                font.weight: Font.DemiBold
                color: Theme.textMuted
                Layout.fillWidth: true
            }

            Repeater {
                model: Device.commandReference
                delegate: ColumnLayout {
                    id: groupItem
                    required property var modelData
                    Layout.fillWidth: true
                    spacing: Theme.s2

                    Text {
                        text: groupItem.modelData.title
                        font.family: Theme.fontFamily
                        font.pixelSize: Theme.fzXs
                        color: Theme.textDim
                    }
                    Repeater {
                        model: groupItem.modelData.commands
                        delegate: ProtocolFrameRow {
                            id: frameRow
                            required property var modelData
                            label: frameRow.modelData.label
                            frameText: frameRow.modelData.text
                            note: frameRow.modelData.note
                            onCopyRequested: (text) => Device.copyToClipboard(text)
                        }
                    }
                }
            }

            // ── 5. Séquence ──
            Text {
                text: qsTr("5 · Séquence rec / veille")
                font.family: Theme.fontFamily
                font.pixelSize: Theme.fzSm
                font.weight: Font.DemiBold
                color: Theme.textMuted
                Layout.fillWidth: true
            }

            Text {
                Layout.fillWidth: true
                wrapMode: Text.WordWrap
                font.family: Theme.fontFamily
                font.pixelSize: Theme.fzSm
                color: Theme.text
                lineHeight: 1.45
                text: qsTr(
                    "1. Régler les deux durées, puis « Envoyer la séquence » : la carte reçoit "
                    + "« rec xx » puis « veille xx », espacées de 250 ms\n"
                    + "2. Un « seq » est envoyé dans la foulée - la réponse de la carte s'affiche sous le formulaire\n"
                    + "3. La boucle tourne ensuite dans la carte : le PC peut être débranché\n"
                    + "4. Réglage type : 10 min d'enregistrement puis 50 min de veille")
            }

            // ── 6. Mode Pro ──
            Text {
                text: qsTr("6 · Outils Mode Pro (AquaMeasure)")
                font.family: Theme.fontFamily
                font.pixelSize: Theme.fzSm
                font.weight: Font.DemiBold
                color: Theme.textMuted
                Layout.fillWidth: true
            }

            Text {
                Layout.fillWidth: true
                wrapMode: Text.WordWrap
                font.family: Theme.fontFamily
                font.pixelSize: Theme.fzSm
                color: Theme.text
                lineHeight: 1.45
                text: qsTr(
                    "• Activer Pro dans Options avancées (bas de page)\n"
                    + "• Envoi brut : taper n'importe quelle ligne, le LF est ajouté\n"
                    + "• Journal série : trace complète des commandes et des réponses")
            }

            Item { Layout.preferredHeight: Theme.s2 }
        }
    }

    footer: DialogButtonBox {
        standardButtons: DialogButtonBox.Close
        onRejected: dlg.close()
        onAccepted: dlg.close()
    }
}
