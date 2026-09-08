import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import AquaMeasure

Item {
    id: root

    property string activePlayer: "left"

    readonly property bool _transportKeysEnabled:
        Sync.bothVideosSelected && App.currentPage === 2 && !Sync.busy

    function _pauseIfPlaying(side) {
        if (side === "left" && Sync.leftPlaying)
            Sync.toggleLeftPlay()
        else if (side === "right" && Sync.rightPlaying)
            Sync.toggleRightPlay()
    }

    function _handleTransportKey(key, modifiers) {
        if (!root._transportKeysEnabled)
            return false
        const side = root.activePlayer
        if (key === Qt.Key_Space) {
            if (side === "left")
                Sync.toggleLeftPlay()
            else
                Sync.toggleRightPlay()
            return true
        }
        if (key === Qt.Key_Left || key === Qt.Key_Right) {
            const fps = side === "left" ? Sync.leftFps : Sync.rightFps
            const step = (modifiers & Qt.ControlModifier)
                ? Settings.transportJumpStep(fps) : 1
            const delta = key === Qt.Key_Left ? -step : step
            root._pauseIfPlaying(side)
            if (side === "left")
                Sync.stepLeft(delta)
            else
                Sync.stepRight(delta)
            return true
        }
        return false
    }

    FocusScope {
        id: transportFocus
        anchors.fill: parent
        focus: root._transportKeysEnabled
        Keys.onPressed: function(event) {
            if (root._handleTransportKey(event.key, event.modifiers))
                event.accepted = true
        }
        Keys.onSpacePressed: function(event) {
            if (root._handleTransportKey(Qt.Key_Space, event.modifiers))
                event.accepted = true
        }
    }

    Connections {
        target: App
        function onCurrentPageChanged() {
            if (App.currentPage === 2 && Sync.bothVideosSelected)
                transportFocus.forceActiveFocus()
        }
    }

    Component.onCompleted: {
        if (App.currentPage === 2 && Sync.bothVideosSelected)
            transportFocus.forceActiveFocus()
    }

    readonly property string syncProgressStage: {
        if (!Sync.busy)
            return ""
        const p = Sync.progress
        if (p < 45)
            return qsTr("Étape 1/3 · Caméra gauche")
        if (p < 88)
            return qsTr("Étape 2/3 · Caméra droite")
        if (p < 100)
            return qsTr("Étape 3/3 · Enregistrement")
        return qsTr("Terminé")
    }

    readonly property string syncProgressDetail: {
        if (!Sync.busy)
            return ""
        const p = Sync.progress
        if (p < 45)
            return qsTr("Analyse de la luminosité autour du flash (vidéo gauche)…")
        if (p < 88)
            return qsTr("Analyse de la luminosité autour du flash (vidéo droite)…")
        return qsTr("Calcul du décalage et génération des courbes…")
    }

    // ══════════════════════════════════════════════════════════════
    ColumnLayout {
        anchors.fill: parent
        spacing: 0

        // ── Barre de statut (steps + progress) ──────────────────
        Rectangle {
            Layout.fillWidth: true
            implicitHeight: 42
            color: Theme.panel
            border.color: Theme.border
            // bottom only
            Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: Theme.border }

            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: Theme.s4
                anchors.rightMargin: Theme.s4
                spacing: Theme.s3

                // Mini étapes inline
                Repeater {
                    model: [
                        { label: qsTr("Vidéos"),    step: 0 },
                        { label: qsTr("Détection"), step: 1 },
                        { label: qsTr("Résultat"),  step: 2 },
                        { label: qsTr("Trim"),      step: 3 },
                        { label: qsTr("Terminé"),   step: 4 }
                    ]
                    RowLayout {
                        spacing: Theme.s2
                        Rectangle {
                            width: 20; height: 20; radius: 10
                            color: Sync.step > modelData.step
                                ? Theme.okSoft
                                : (Sync.step === modelData.step ? Theme.accentSoft : Theme.panel2)
                            border.color: Sync.step > modelData.step
                                ? Theme.ok
                                : (Sync.step === modelData.step ? Theme.accent : Theme.border)
                            border.width: 1
                            Text {
                                anchors.centerIn: parent
                                text: Sync.step > modelData.step ? "✓" : (modelData.step + 1)
                                font.family: Theme.monoFamily; font.pixelSize: Theme.fzXs
                                font.weight: Font.DemiBold
                                color: Sync.step > modelData.step
                                    ? Theme.ok
                                    : (Sync.step === modelData.step ? Theme.accentText : Theme.textDim)
                            }
                        }
                        Text {
                            text: modelData.label
                            font.family: Theme.fontFamily; font.pixelSize: Theme.fzSm
                            color: Sync.step === modelData.step ? Theme.text
                                 : (Sync.step > modelData.step  ? Theme.ok : Theme.textDim)
                            font.weight: Sync.step === modelData.step ? Font.DemiBold : Font.Normal
                        }
                        // séparateur entre étapes
                        Rectangle {
                            visible: modelData.step < 4
                            width: 16; height: 1
                            color: Theme.border2
                        }
                    }
                }

                InfoDot {
                    diameter: 14
                    text: qsTr("Les cinq étapes de la synchronisation : choisir les deux vidéos, détecter le flash, vérifier le décalage trouvé, découper la partie utile, terminer. L'étape en cours est en bleu, celles qui sont faites passent au vert. Synchroniser sert à ce que les deux vues montrent le même instant : sans cela, la mesure compare deux moments différents et se trompe.")
                }

                Item { Layout.fillWidth: true }

                RowLayout {
                    visible: Sync.step >= 2
                    spacing: Theme.s2
                    Text {
                        text: qsTr("DÉCALAGE %1 img · G f%2 · D f%3")
                            .arg((Sync.syncOffset >= 0 ? "+" : "") + Sync.syncOffset)
                            .arg(Sync.leftPinFrame)
                            .arg(Sync.rightPinFrame)
                        font.family: Theme.monoFamily
                        font.pixelSize: Theme.fzXs
                        color: Theme.accentText
                    }
                    InfoDot {
                        diameter: 14
                        text: qsTr("Décalage retenu entre les deux caméras, en nombre d'images. « G f120 » indique l'image où le flash a été vu à gauche, « D f127 » celle où il a été vu à droite : le décalage est la différence entre les deux. Vérifiez sur les aperçus que le flash tombe bien sur ces deux images.")
                    }
                }

                // Indicateur compact (barre d’étapes)
                RowLayout {
                    visible: Sync.busy
                    spacing: Theme.s2
                    Layout.preferredWidth: 200
                    Layout.alignment: Qt.AlignVCenter

                    ProgressBar {
                        Layout.fillWidth: true
                        Layout.preferredWidth: 140
                        value: Sync.progress / 100.0
                        background: Rectangle {
                            implicitHeight: 6
                            radius: 3
                            color: Theme.panel2
                            border.color: Theme.border
                            border.width: 1
                        }
                        contentItem: Item {
                            implicitHeight: 6
                            Rectangle {
                                width: parent.parent.visualPosition * parent.parent.width
                                height: parent.height
                                radius: 3
                                color: Theme.accent
                            }
                        }
                    }
                    Text {
                        text: Sync.progress + " %"
                        font.family: Theme.monoFamily
                        font.pixelSize: Theme.fzXs
                        color: Theme.accentText
                    }
                }
            }
        }

        // ── Zone vidéo principale ────────────────────────────────
        Item {
            Layout.fillWidth: true
            Layout.fillHeight: true

            // Placeholder (pas de vidéos)
            Column {
                anchors.centerIn: parent
                spacing: Theme.s4
                visible: !Sync.bothVideosSelected

                Text {
                    anchors.horizontalCenter: parent.horizontalCenter
                    text: "▣"
                    font.pixelSize: 48; color: Theme.border2
                }
                Text {
                    anchors.horizontalCenter: parent.horizontalCenter
                    text: qsTr("Sélectionnez les deux vidéos via le panneau de gauche")
                    font.family: Theme.fontFamily; font.pixelSize: Theme.fzBase
                    color: Theme.textDim
                }
            }

            // Deux visionneuses côte à côte - largeur égale forcée (50 / 50)
            Row {
                id: playersRow
                anchors.fill: parent
                anchors.margins: Theme.s4
                spacing: Theme.s3
                visible: Sync.bothVideosSelected

                readonly property real panelW: Math.max(0, (width - spacing - 1) / 2)

                VideoSyncPanel {
                    id: leftPanel
                    width:  playersRow.panelW
                    height: playersRow.height
                    active: root.activePlayer === "left"
                    sideLabel:       qsTr("CAM G")
                    videoPath:       Sync.leftVideo
                    frameCount:      Sync.leftFrameCount
                    fps:             Sync.leftFps
                    currentFrame:    Sync.leftCurrentFrame
                    inFrame:         Sync.leftInFrame
                    outFrame:        Sync.leftOutFrame
                    pinFrame:        Sync.leftPinFrame
                    playing:         Sync.leftPlaying
                    detectWindowS:   Sync.detectWindowS
                    roiDrawEnabled:  Sync.roiModeEnabled
                    flashRoi:        Sync.leftRoi
                    videoWidth:      Sync.leftVideoWidth
                    videoHeight:     Sync.leftVideoHeight
                    onActivated: {
                        root.activePlayer = "left"
                        transportFocus.forceActiveFocus()
                    }
                    onSeek:        (f) => Sync.seekLeft(f)
                    onStepBy:      (d) => Sync.stepLeft(d)
                    onTogglePlay:  Sync.toggleLeftPlay()
                    onInChanged:   (f) => Sync.setLeftIn(f)
                    onOutChanged:  (f) => Sync.setLeftOut(f)
                    onPinChanged:  (f) => Sync.setLeftPin(f)
                    onDetectWindowChanged: (s) => Sync.setDetectWindowS(s)
                    onFlashRoiEdited: (r) => Sync.setLeftRoiFromList(r)
                }

                Rectangle {
                    width: 1
                    height: parent.height
                    color: Theme.border
                    opacity: 0.5
                }

                VideoSyncPanel {
                    id: rightPanel
                    width:  playersRow.panelW
                    height: playersRow.height
                    active: root.activePlayer === "right"
                    sideLabel:       qsTr("CAM D")
                    videoPath:       Sync.rightVideo
                    frameCount:      Sync.rightFrameCount
                    fps:             Sync.rightFps
                    currentFrame:    Sync.rightCurrentFrame
                    inFrame:         Sync.rightInFrame
                    outFrame:        Sync.rightOutFrame
                    pinFrame:        Sync.rightPinFrame
                    playing:         Sync.rightPlaying
                    detectWindowS:   Sync.detectWindowS
                    roiDrawEnabled:  Sync.roiModeEnabled
                    flashRoi:        Sync.rightRoi
                    videoWidth:      Sync.rightVideoWidth
                    videoHeight:     Sync.rightVideoHeight
                    onActivated: {
                        root.activePlayer = "right"
                        transportFocus.forceActiveFocus()
                    }
                    onSeek:        (f) => Sync.seekRight(f)
                    onStepBy:      (d) => Sync.stepRight(d)
                    onTogglePlay:  Sync.toggleRightPlay()
                    onInChanged:   (f) => Sync.setRightIn(f)
                    onOutChanged:  (f) => Sync.setRightOut(f)
                    onPinChanged:  (f) => Sync.setRightPin(f)
                    onDetectWindowChanged: (s) => Sync.setDetectWindowS(s)
                    onFlashRoiEdited: (r) => Sync.setRightRoiFromList(r)
                }
            }

            SyncBusyOverlay {
                anchors.fill: parent
                active: Sync.busy
                progress: Sync.progress
                stage: root.syncProgressStage
                detail: root.syncProgressDetail
            }
        }

        // ── Courbes luminosité ───────────────────────────────────
        BrightnessCurvesPanel {
            Layout.fillWidth: true
        }

        // ── Console ──────────────────────────────────────────────
        ConsolePanel {
            Layout.fillWidth: true
            expanded: false
            bodyHeight: 180
            logModel: Sync.logs
            autoScroll: true
        }
    }
}
