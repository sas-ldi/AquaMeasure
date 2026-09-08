import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import AquaMeasure

ColumnLayout {
    spacing: Theme.spaceLg

    RowLayout {
        Layout.fillWidth: true
        spacing: Theme.s3

        AppLabel {
            Layout.fillWidth: true
            muted: true
            text: qsTr("Connexion série à la carte Arduino qui pilote les deux caméras (alimentation, boutons, flash).")
            color: Theme.textMuted
            wrapMode: Text.WordWrap
        }

        InfoDot {
            text: qsTr("Cette page ne sert qu'à préparer le matériel avant la plongée : la carte allume les deux caméras en même temps et déclenche le coup de flash qui servira ensuite de repère commun pour la synchronisation. Une fois les vidéos rapportées, elle n'est plus nécessaire.")
        }

        AppButton {
            text: qsTr("Guide carte")
            tooltipText: qsTr("Rappel des commandes que comprend la carte et du branchement des caméras.")
            onClicked: boardGuide.open()
        }
    }

    ArduinoProtocolDialog {
        id: boardGuide
        objectName: "boardGuide"
    }

    StatusBanner {
        Layout.fillWidth: true
        message: Device.lastError
        level: "error"
        visible: Device.lastError !== ""
    }

    StatusBanner {
        Layout.fillWidth: true
        message: qsTr("pyserial absent - installez les dépendances (pip install pyserial)")
        level: "warn"
        visible: !Device.serialAvailable
    }

    RowLayout {
        Layout.fillWidth: true
        spacing: Theme.spaceMd

        AppStatusCard {
            Layout.fillWidth: true
            title: qsTr("Port série")
            iconName: "device"
            subtitle: Device.connected ? qsTr("Connecté") : qsTr("Déconnecté")
            status: Device.connected ? "ok" : "pending"
        }

        AppStatusCard {
            Layout.fillWidth: true
            title: qsTr("Carte Arduino")
            iconName: "device"
            subtitle: Device.boardResponding
                ? qsTr("Répond")
                : (Device.connected ? qsTr("Pas encore de réponse") : qsTr("-"))
            status: Device.boardResponding ? "ok" : (Device.connected ? "warn" : "pending")
        }

        AppStatusCard {
            Layout.fillWidth: true
            title: qsTr("Séquence")
            iconName: "clock"
            subtitle: Device.sequenceSynced
                ? qsTr("%1 min rec · %2 min veille (lu dans la carte)")
                    .arg(Device.recordDurationMin).arg(Device.pauseDurationMin)
                : qsTr("%1 min rec · %2 min veille (non vérifié)")
                    .arg(Device.recordDurationMin).arg(Device.pauseDurationMin)
            status: Device.sequenceSynced ? "ok" : "pending"
        }
    }

    // Alimentation des caméras : reflète les commandes cam1/cam2 envoyées.
    // La carte n'accuse pas réception, l'état est donc déclaratif.
    RowLayout {
        Layout.fillWidth: true
        spacing: Theme.spaceMd
        visible: Device.cam1Power !== "" || Device.cam2Power !== ""

        Repeater {
            model: [
                { name: qsTr("Caméra 1"), state: Device.cam1Power },
                { name: qsTr("Caméra 2"), state: Device.cam2Power }
            ]
            delegate: Rectangle {
                required property var modelData
                Layout.fillWidth: true
                implicitHeight: 34
                radius: Theme.radiusSm
                color: Theme.surface
                border.color: modelData.state === "on" ? Theme.ok : Theme.border

                RowLayout {
                    anchors.fill: parent
                    anchors.leftMargin: Theme.spaceMd
                    anchors.rightMargin: Theme.spaceMd
                    spacing: Theme.s2

                    Rectangle {
                        width: 8; height: 8; radius: 4
                        color: modelData.state === "on" ? Theme.ok
                            : (modelData.state === "off" ? Theme.textDim : Theme.border)
                    }
                    AppLabel {
                        Layout.fillWidth: true
                        text: modelData.name
                        font.pixelSize: Theme.fontCaption
                    }
                    AppLabel {
                        text: modelData.state === "on" ? qsTr("alimentée")
                            : (modelData.state === "off" ? qsTr("coupée") : qsTr("-"))
                        color: modelData.state === "on" ? Theme.ok : Theme.textMuted
                        font.pixelSize: Theme.fontCaption
                        font.family: Theme.monoFamily
                    }
                }
            }
        }
    }

    AppCard {
        Layout.fillWidth: true
        title: qsTr("Connexion série")
        iconName: "device"
        subtitle: qsTr("8N1 · câble USB de la carte Arduino (l'ouverture du port redémarre la carte)")
        ColumnLayout {
            width: parent.width
            spacing: Theme.spaceMd

            RowLayout {
                Layout.fillWidth: true
                spacing: Theme.spaceMd
                AppLabel {
                    text: qsTr("Port")
                    color: Theme.textMuted
                    Layout.preferredWidth: 88
                    Layout.alignment: Qt.AlignVCenter
                }
                AppComboBox {
                    id: portCombo
                    Layout.fillWidth: true
                    Layout.preferredHeight: 40
                    Layout.alignment: Qt.AlignVCenter
                    model: Device.availablePorts
                    onActivated: Device.portName = currentText
                    Component.onCompleted: {
                        Device.refreshPorts()
                        syncPortSelection()
                    }
                    function syncPortSelection() {
                        if (Device.portName.length === 0 && count > 0)
                            Device.portName = textAt(0)
                        for (let i = 0; i < count; i++) {
                            if (textAt(i) === Device.portName) {
                                currentIndex = i
                                return
                            }
                        }
                    }
                    Connections {
                        target: Device
                        function onAvailablePortsChanged() { portCombo.syncPortSelection() }
                        function onPortNameChanged() { portCombo.syncPortSelection() }
                    }
                }
                AppButton {
                    text: qsTr("Actualiser")
                    Layout.alignment: Qt.AlignVCenter
                    onClicked: Device.refreshPorts()
                }
            }

            AppLabel {
                Layout.fillWidth: true
                visible: Device.portDetailText.length > 0
                text: Device.portDetailText
                font.pixelSize: Theme.fontCaption
                font.family: Theme.monoFamily
                color: Theme.textDim
                wrapMode: Text.WordWrap
                lineHeight: 1.35
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: Theme.spaceMd
                AppLabel {
                    text: qsTr("Baud")
                    color: Theme.textMuted
                    Layout.preferredWidth: 88
                    Layout.alignment: Qt.AlignVCenter
                }
                AppEditableComboBox {
                    id: baudCombo
                    Layout.preferredWidth: 132
                    Layout.alignment: Qt.AlignVCenter
                    model: [9600, 19200, 38400, 57600, 115200, 230400, 460800, 921600]
                    numericValue: Device.baudRate
                    onValueCommitted: (v) => Device.baudRate = v
                    Connections {
                        target: Device
                        function onBaudRateChanged() {
                            baudCombo.syncFromValue(Device.baudRate)
                        }
                    }
                }
                AppButton {
                    text: qsTr("Connecter")
                    primary: true
                    enabled: !Device.connected && Device.serialAvailable
                    onClicked: Device.connectPort()
                }
                AppButton {
                    text: qsTr("Reconnecter")
                    enabled: Device.serialAvailable
                    onClicked: Device.reconnectPort()
                }
                AppButton {
                    text: qsTr("Déconnecter")
                    enabled: Device.connected
                    onClicked: Device.disconnectPort()
                }
                AppButton {
                    text: qsTr("Tester la carte")
                    enabled: Device.connected
                    onClicked: Device.requestSequence()
                }
            }
        }
    }

    AppCard {
        Layout.fillWidth: true
        title: qsTr("Séquence d'enregistrement")
        iconName: "clock"
        subtitle: qsTr("Réglage appliqué dans la carte : elle enchaîne rec / veille en boucle, PC débranché")
        ColumnLayout {
            width: parent.width
            spacing: Theme.spaceMd

            RowLayout {
                Layout.fillWidth: true
                Layout.maximumWidth: Theme.panelInnerMaxWidth
                spacing: Theme.spaceMd
                AppLabel {
                    text: qsTr("Enregistrement (min)")
                    color: Theme.text
                    Layout.preferredWidth: Theme.formLabelWidth
                    Layout.alignment: Qt.AlignVCenter
                }
                AppSpinBox {
                    Layout.alignment: Qt.AlignVCenter
                    from: 1
                    to: 24 * 60
                    stepSize: 1
                    value: Device.recordDurationMin
                    onValueModified: Device.recordDurationMin = value
                }
                AppLabel {
                    Layout.alignment: Qt.AlignVCenter
                    Layout.preferredHeight: 40
                    verticalAlignment: Text.AlignVCenter
                    text: qsTr("≈ %1 h %2 min").arg(Math.floor(Device.recordDurationMin / 60))
                        .arg(Device.recordDurationMin % 60)
                    color: Theme.textDim
                    font.pixelSize: Theme.fontCaption
                }
            }

            RowLayout {
                Layout.fillWidth: true
                Layout.maximumWidth: Theme.panelInnerMaxWidth
                spacing: Theme.spaceMd
                AppLabel {
                    text: qsTr("Veille (min)")
                    color: Theme.text
                    Layout.preferredWidth: Theme.formLabelWidth
                    Layout.alignment: Qt.AlignVCenter
                }
                AppSpinBox {
                    Layout.alignment: Qt.AlignVCenter
                    from: 1
                    to: 24 * 60
                    stepSize: 5
                    value: Device.pauseDurationMin
                    onValueModified: Device.pauseDurationMin = value
                }
                AppLabel {
                    Layout.alignment: Qt.AlignVCenter
                    Layout.preferredHeight: 40
                    verticalAlignment: Text.AlignVCenter
                    text: qsTr("≈ %1 h %2 min").arg(Math.floor(Device.pauseDurationMin / 60))
                        .arg(Device.pauseDurationMin % 60)
                    color: Theme.textDim
                    font.pixelSize: Theme.fontCaption
                }
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: Theme.spaceMd
                AppLabel {
                    Layout.fillWidth: true
                    text: qsTr("Envoie « rec %1 » puis « veille %2 », et relit la config")
                        .arg(Device.recordDurationMin).arg(Device.pauseDurationMin)
                    color: Theme.textMuted
                    font.family: Theme.monoFamily
                    font.pixelSize: Theme.fontCaption
                    wrapMode: Text.WordWrap
                }
                AppButton {
                    text: qsTr("Envoyer la séquence")
                    primary: true
                    enabled: Device.connected
                    onClicked: Device.applySequence()
                }
                AppButton {
                    text: qsTr("Lire la séquence")
                    enabled: Device.connected
                    onClicked: Device.requestSequence()
                }
            }

            // Conditionné à boardResponding : si la carte reste muette, réclamer
            // une relecture est inutile et l'avertissement deviendrait permanent.
            AppLabel {
                Layout.fillWidth: true
                visible: !Device.sequenceSynced && Device.connected && Device.boardResponding
                text: qsTr("⚠ Durées non vérifiées : cliquez « Lire la séquence » pour les relire dans la carte.")
                color: Theme.warn
                font.pixelSize: Theme.fontCaption
                wrapMode: Text.WordWrap
            }

            AppLabel {
                Layout.fillWidth: true
                visible: Device.connected && !Device.boardResponding
                text: qsTr("La carte n'a encore rien renvoyé. Au démarrage elle affiche son menu : "
                    + "s'il n'apparaît pas dans le journal, vérifiez le débit série puis « Reconnecter ».")
                color: Theme.textDim
                font.pixelSize: Theme.fontCaption
                wrapMode: Text.WordWrap
            }

            ColumnLayout {
                Layout.fillWidth: true
                spacing: Theme.s2
                visible: Device.sequenceText !== ""
                AppLabel {
                    text: qsTr("Réponse de la carte à « seq »")
                    color: Theme.textMuted
                    font.pixelSize: Theme.fontCaption
                }
                AppLabel {
                    Layout.fillWidth: true
                    text: Device.sequenceText
                    font.family: Theme.monoFamily
                    font.pixelSize: Theme.fontCaption
                    color: Theme.text
                    wrapMode: Text.WordWrap
                }
            }
        }
    }

    AppCard {
        Layout.fillWidth: true
        title: qsTr("Commandes carte")
        iconName: "settings"
        subtitle: qsTr("Envoi immédiat - texte ASCII terminé par LF")
        ColumnLayout {
            width: parent.width
            spacing: Theme.spaceMd

            Repeater {
                model: Device.commandGroups
                delegate: SectionSurface {
                    id: groupItem
                    required property var modelData
                    title: groupItem.modelData.title
                    iconName: "device"
                    Flow {
                        Layout.fillWidth: true
                        spacing: Theme.s2
                        Repeater {
                            model: groupItem.modelData.commands
                            delegate: GhostButton {
                                id: cmdButton
                                required property var modelData
                                text: cmdButton.modelData.label
                                small: true
                                enabled: Device.connected
                                onClicked: Device.sendCommand(cmdButton.modelData.key)
                            }
                        }
                    }
                }
            }
        }
    }

    AppCard {
        Layout.fillWidth: true
        title: qsTr("Journal série")
        iconName: "list"
        subtitle: qsTr("Commandes envoyées et lignes renvoyées par la carte")
        ColumnLayout {
            width: parent.width
            spacing: Theme.s2

            ScrollView {
                Layout.fillWidth: true
                Layout.preferredHeight: 160
                clip: true
                ScrollBar.vertical: ScrollBar {
                    policy: ScrollBar.AsNeeded
                }

                TextEdit {
                    width: parent.availableWidth
                    readOnly: true
                    selectByMouse: true
                    wrapMode: TextEdit.Wrap
                    text: Device.serialLog
                    color: Theme.textMuted
                    font.family: Theme.monoFamily
                    font.pixelSize: Theme.fontCaption
                }
            }

            RowLayout {
                Layout.fillWidth: true
                Item { Layout.fillWidth: true }
                GhostButton {
                    text: qsTr("Vider le journal")
                    small: true
                    onClicked: Device.clearLog()
                }
            }
        }
    }

    // -- Mode Pro --
    Rectangle {
        Layout.fillWidth: true
        implicitHeight: proRow.implicitHeight + Theme.spaceMd * 2
        radius: Theme.radiusSm
        color: Theme.surface
        border.color: Theme.border

        RowLayout {
            id: proRow
            anchors.fill: parent
            anchors.margins: Theme.spaceMd
            spacing: Theme.spaceMd

            AppLabel {
                Layout.fillWidth: true
                text: qsTr("Options avancées")
                font.weight: Font.DemiBold
                color: Theme.text
            }
            AppLabel {
                text: qsTr("Pro")
                font.pixelSize: Theme.fontCaption
                color: Theme.textMuted
            }
            Switch {
                checked: Settings.proMode
                onCheckedChanged: Settings.proMode = checked
                palette {
                    window: Theme.surfaceHover
                    button: Settings.proMode ? Theme.accent : Theme.surfaceHover
                    highlight: Theme.accentSoft
                }
            }
        }
    }

    AppCard {
        Layout.fillWidth: true
        visible: Settings.proMode
        title: qsTr("Série - envoi brut (Pro)")
        iconName: "next"
        subtitle: qsTr("La ligne est envoyée telle quelle, le LF est ajouté automatiquement")
        ColumnLayout {
            width: parent.width
            spacing: Theme.spaceMd
            AppTextField {
                Layout.fillWidth: true
                placeholderText: qsTr("Commande texte (ex. cam1 on)…")
                id: rawCmd
                onAccepted: if (Device.connected) Device.sendRaw(rawCmd.text)
            }
            AppButton {
                text: qsTr("Envoyer")
                enabled: Device.connected
                onClicked: Device.sendRaw(rawCmd.text)
            }
        }
    }

    Item { Layout.fillHeight: true }
}
