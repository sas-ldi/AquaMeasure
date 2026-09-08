import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import AquaMeasure

Item {
    id: root

    readonly property bool _transportKeysEnabled:
        App.currentPage === 4 && Measure.frameCount > 0

    // Le champ `shortcut` du catalogue d'événements n'était branché nulle
    // part : marquer des dizaines de bouchées à la souris, image par image,
    // n'est pas tenable. C'est ici que la lettre devient un geste.
    readonly property bool _peckKeysEnabled:
        root._transportKeysEnabled && typeof Pecks !== "undefined"

    function textEditorFocused() {
        const win = root.Window.window
        const item = win ? win.activeFocusItem : null
        return item && (item instanceof TextInput || item instanceof TextEdit)
    }

    FocusScope {
        id: transportFocus
        anchors.fill: parent
        focus: root._transportKeysEnabled
        Keys.onPressed: function(event) {
            if (!root._transportKeysEnabled || root.textEditorFocused())
                return
            if (event.key === Qt.Key_Space) {
                Measure.togglePlay()
                event.accepted = true
            } else if (event.key === Qt.Key_Left || event.key === Qt.Key_Right) {
                const step = (event.modifiers & Qt.ControlModifier)
                    ? Settings.transportJumpStep(Measure.leftFps) : 1
                Measure.stepFrame(event.key === Qt.Key_Left ? -step : step)
                event.accepted = true
            } else if (root._peckKeysEnabled && Pecks.handleShortcut(
                    event.text,
                    (event.modifiers & Qt.ShiftModifier) !== 0)) {
                // Le transport passe en premier : un raccourci de catalogue ne
                // doit jamais voler Espace ni les flèches.
                event.accepted = true
            }
        }
    }

    Connections {
        target: App
        function onCurrentPageChanged() {
            if (App.currentPage !== 4)
                return
            if (Measure.frameCount <= 0)
                Measure.refresh(Sync.leftVideo, Sync.rightVideo)
            if (Measure.frameCount > 0)
                transportFocus.forceActiveFocus()
        }
    }

    Component.onCompleted: {
        if (App.currentPage !== 4)
            return
        if (Measure.frameCount <= 0)
            Measure.refresh(Sync.leftVideo, Sync.rightVideo)
        if (Measure.frameCount > 0)
            transportFocus.forceActiveFocus()
    }

    TapHandler {
        acceptedButtons: Qt.LeftButton
        onTapped: {
            if (!root.textEditorFocused())
                transportFocus.forceActiveFocus()
        }
    }

    CalibPreviewFullscreenPopup {
        id: viewFullscreen
        parent: Overlay.overlay
    }

    SplitView {
        id: mainSplit
        objectName: "measurementWorkspaceSplit"
        anchors.fill: parent
        orientation: Qt.Horizontal
        handle: Rectangle {
            implicitWidth: 8
            implicitHeight: parent.height
            color: SplitHandle.pressed ? Theme.accent : "transparent"
        }
        Item {
            id: workflowPane
            objectName: "measurementWorkflowPane"
            SplitView.preferredWidth: root.width < 1400 ? 280 : 300
            SplitView.minimumWidth: 280
            SplitView.maximumWidth: 400
            clip: true
            FishWorkflowPanel { anchors.fill: parent }
        }
        Item {
            id: videoPane
            objectName: "measurementVideoPane"
            SplitView.fillWidth: true
            SplitView.minimumWidth: 480
            clip: true
            ColumnLayout {
                anchors.fill: parent
                spacing: Theme.s2
                RowLayout {
                    Layout.fillWidth: true
                    Layout.preferredHeight: 32
                    WorkspaceIcon { name: "video" }
                    AppLabel {
                        Layout.fillWidth: true
                        Layout.minimumWidth: 0
                        text: Data.selectedLabel.length > 0 ? Data.selectedLabel : qsTr("Vues stéréo")
                        font.weight: Font.DemiBold
                        elide: Text.ElideRight
                    }
                    AppLabel {
                        text: qsTr("Image %1 / %2").arg(Measure.leftAbsFrame).arg(Measure.frameCount)
                        color: Theme.textMuted
                        font.pixelSize: Theme.fzXs
                    }
                }
                Rectangle {
                    Layout.fillWidth: true
                    implicitHeight: 30
                    radius: Theme.radiusSm
                    color: Theme.accentSoft
                    visible: message.length > 0
                    readonly property string message: Data.statusText.length > 0 ? Data.statusText : Fish.statusText
                    RowLayout {
                        anchors.fill: parent
                        anchors.margins: 6
                        AppLabel {
                            Layout.fillWidth: true
                            Layout.minimumWidth: 0
                            text: parent.parent.message
                            elide: Text.ElideRight
                            font.pixelSize: Theme.fzXs
                            color: Theme.accentText
                        }
                        InfoDot { text: parent.parent.message }
                    }
                }
        Rectangle {
            id: followBanner
            Layout.fillWidth: true
            visible: Fish.busy || Fish.assistActive || followBanner.lost
            implicitHeight: 36
            radius: Theme.radiusSm
            color: followBanner.lost ? Theme.warnSoft : Theme.accentSoft
            border.color: followBanner.lost ? Theme.warn : Theme.accent
            border.width: 1

            readonly property bool lost: Fish.assistWaiting

            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: Theme.s3
                anchors.rightMargin: Theme.s2
                spacing: Theme.s2

                AppLabel {
                    Layout.fillWidth: true
                    Layout.minimumWidth: 0
                    text: followBanner.lost
                        ? Fish.assistMessage
                        : (Fish.assistActive
                            ? qsTr("Suivi du poisson en cours… (%1 %)").arg(Fish.assistProgress)
                            : qsTr("Analyse en cours…"))
                    color: followBanner.lost ? Theme.warn : Theme.accentText
                    font.pixelSize: Theme.fzXs
                    font.weight: Font.DemiBold
                    elide: Text.ElideRight
                }

                GhostButton {
                    visible: followBanner.lost
                    small: true
                    text: qsTr("Reprendre le suivi")
                    requires: !Fish.busy && (Fish.selectedFishIndex >= 0 || Fish.lastBoxCount === 1)
                    disabledReason: Fish.busy
                        ? qsTr("Un calcul est déjà en cours - attendez la fin.")
                        : qsTr("Cliquez sur le cadre du poisson dans l'image de gauche, ou réencadrez-le pour reprendre le suivi.")
                    tooltipText: qsTr("Repart du cadre sélectionné ou du dernier rectangle tracé et continue le suivi.")
                    onClicked: Fish.resumeAssistFromBox()
                }

                GhostButton {
                    visible: Fish.assistActive
                    small: true
                    text: qsTr("Arrêter le suivi")
                    tooltipText: qsTr("Arrête le suivi à l'image affichée. Les positions déjà suivies sont conservées.")
                    onClicked: Fish.stopAssistedTracking()
                }

                GhostButton {
                    visible: Fish.busy && !Fish.assistActive
                    small: true
                    text: qsTr("Interrompre l'analyse")
                    tooltipText: qsTr("Arrête tout de suite le calcul en cours. Ce qui a déjà été analysé est conservé.")
                    onClicked: Fish.cancelTracking()
                }
            }
        }

        // Mesurer demande 4 clics (A puis B à gauche, A puis B à droite) sans
        // que rien n'indiquait l'étape en cours ni comment repartir de zéro.
                    Rectangle {
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        Layout.minimumHeight: 200
                        color: Theme.bgElevated
                        radius: Theme.radiusMd
                        border.color: Measure.playing ? Theme.accentText : Theme.border
                        border.width: Measure.playing ? 2 : 1
                        clip: true

                        RowLayout {
                            anchors.fill: parent
                            anchors.margins: Theme.s2
                            spacing: Theme.s2

                            MeasureStereoView {
                                Layout.fillWidth: true
                                Layout.fillHeight: true
                                Layout.minimumWidth: 180
                                sideLabel: qsTr("Gauche")
                                isLeft: true
                                videoPath: Measure.leftVideo
                                videoFrameCount: Measure.leftVideoFrameCount
                                frameWidth: Measure.frameWidth
                                frameHeight: Measure.frameHeight
                                fps: Measure.leftFps
                                absFrame: Measure.leftAbsFrame
                                pointA: Measure.leftPointA
                                pointB: Measure.leftPointB
                                pointADisplay: Measure.leftPointADisplay
                                pointBDisplay: Measure.leftPointBDisplay
                                playing: Measure.playing
                                rectifiedReady: Measure.rectifiedReady
                                previewTick: Measure.previewTick
                                placementEnabled: !Measure.playing && Measure.frameCount > 0
                                    && (Measure.measureStep === 0 || Measure.measureStep === 1)
                                interactionEnabled: !Measure.playing && Measure.frameCount > 0
                                onPointPlaced: function(x, y) { Measure.placePoint(true, x, y) }
                                onPointMoved: function(idx, x, y) { Measure.movePoint(true, idx, x, y) }
                                onPointDeleteRequested: function(idx) { Measure.removePoint(true, idx) }
                                onPointDragFinished: Measure.finishPointDrag()
                                onFullscreenRequested: function(caption, src) {
                                    viewFullscreen.caption = caption
                                    viewFullscreen.imageSource = src
                                    viewFullscreen.open()
                                }
                            }

                            MeasureStereoView {
                                Layout.fillWidth: true
                                Layout.fillHeight: true
                                Layout.minimumWidth: 180
                                sideLabel: qsTr("Droite")
                                isLeft: false
                                videoPath: Measure.rightVideo
                                videoFrameCount: Measure.rightVideoFrameCount
                                frameWidth: Measure.frameWidth
                                frameHeight: Measure.frameHeight
                                fps: Measure.rightFps
                                absFrame: Measure.rightAbsFrame
                                pointA: Measure.rightPointA
                                pointB: Measure.rightPointB
                                pointADisplay: Measure.rightPointADisplay
                                pointBDisplay: Measure.rightPointBDisplay
                                playing: Measure.playing
                                rectifiedReady: Measure.rectifiedReady
                                previewTick: Measure.previewTick
                                placementEnabled: !Measure.playing && Measure.frameCount > 0
                                    && (Measure.measureStep === 2 || Measure.measureStep === 3)
                                interactionEnabled: !Measure.playing && Measure.frameCount > 0
                                onPointPlaced: function(x, y) { Measure.placePoint(false, x, y) }
                                onPointMoved: function(idx, x, y) { Measure.movePoint(false, idx, x, y) }
                                onPointDeleteRequested: function(idx) { Measure.removePoint(false, idx) }
                                onPointDragFinished: Measure.finishPointDrag()
                                onFullscreenRequested: function(caption, src) {
                                    viewFullscreen.caption = caption
                                    viewFullscreen.imageSource = src
                                    viewFullscreen.open()
                                }
                            }
                        }

                        AppLabel {
                            anchors.centerIn: parent
                            visible: Measure.frameCount <= 0
                            muted: true
                            horizontalAlignment: Text.AlignHCenter
                            wrapMode: Text.WordWrap
                            width: Math.min(parent.width - Theme.s6, 420)
                            text: Measure.leftVideo.length > 0
                                ? qsTr("Timeline vide - menu Fichier → Charger vidéos depuis Sync")
                                : qsTr("Pas de vidéo - panneau gauche : Caméra G/D ou Sync")
                        }
                    }

                    FrameAbundanceBar {
                        Layout.fillWidth: true
                        Layout.preferredHeight: 52
                        visible: Data.dbAvailable && Measure.frameCount > 0
                    }

                    // Marqueurs ponctuels de la piste travaillée. Un événement
                    // d'une seule frame est invisible sur la barre de
                    // progression : il lui faut sa propre graduation.
                    RowLayout {
                        objectName: "peckStripRow"
                        Layout.fillWidth: true
                        Layout.preferredHeight: 34
                        spacing: Theme.s2
                        visible: typeof Pecks !== "undefined"
                            && Measure.frameCount > 0
                            && (Pecks.armed || Pecks.markerCount > 0)

                        AppLabel {
                            text: Pecks.selectedTrackLabel.length > 0
                                ? Pecks.selectedTrackLabel : qsTr("Piste")
                            font.pixelSize: Theme.fzXs
                            font.weight: Font.DemiBold
                            color: Theme.accentText
                        }

                        EventMarkerStrip {
                            objectName: "measurePeckStrip"
                            zoomEnabled: true
                            viewKey: Measure.leftVideo + "|" + Pecks.selectedTrackId
                            Layout.fillWidth: true
                            Layout.preferredHeight: 34
                            frameCount: Measure.frameCount
                            currentFrame: Measure.frameIndex
                            rangeStart: Pecks.rangeStart
                            rangeEnd: Pecks.rangeEnd
                            markers: Pecks.markers
                            onSeekRequested: function(f) { Measure.frameIndex = f }
                            onMarkerActivated: function(eventId, frame) {
                                Pecks.seekToMarker(eventId)
                            }
                        }

                        AppLabel {
                            text: Pecks.markerCount > 0
                                ? qsTr("%1 %2").arg(Pecks.markerCount)
                                    .arg(Pecks.typeLabel)
                                : qsTr("aucun marqueur")
                            font.family: Theme.monoFamily
                            font.pixelSize: Theme.fzXs
                            color: Pecks.markerCount > 0
                                ? Theme.markPin : Theme.textDim
                        }
                    }

                    VideoTransportBar {
                        Layout.fillWidth: true
                        frameIndex: Measure.frameIndex
                        frameCount: Measure.frameCount
                        fps: Measure.leftFps
                        playing: Measure.playing
                        onTogglePlay: Measure.togglePlay()
                        onStepBy: function(d) { Measure.stepFrame(d) }
                        onSeek: function(f) { Measure.frameIndex = f }
                    }
                AppLabel {
                    objectName: "transportKeysHint"
                    Layout.fillWidth: true
                    Layout.minimumWidth: 0
                    visible: Measure.frameCount > 0
                    text: qsTr("Espace : lecture · ←/→ : 1 image · Ctrl+←/→ : %1").arg(Settings.transportJumpLabel)
                        + (Pecks.armed && Pecks.typeShortcut.length > 0
                            ? qsTr(" · %1 : %2 · Maj+%1 : retirer").arg(Pecks.typeShortcut).arg(Pecks.typeLabel) : "")
                    color: Theme.textDim
                    font.pixelSize: Theme.fzXs
                    elide: Text.ElideRight
                }
            }
        }
        Item {
            id: registryPane
            objectName: "measurementRegistryPane"
            SplitView.preferredWidth: root.width < 1400 ? 220 : 260
            SplitView.minimumWidth: 220
            SplitView.maximumWidth: 380
            clip: true
            CompactRegistryPanel { anchors.fill: parent }
        }
    }
}
