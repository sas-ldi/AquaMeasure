import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import AquaMeasure

ColumnLayout {
    id: root

    required property string sideLabel
    required property string videoPath
    required property int    frameCount
    required property double fps
    required property int    currentFrame
    required property int    inFrame
    required property int    outFrame
    required property int    pinFrame
    required property bool   playing
    required property real   detectWindowS

    property bool  roiDrawEnabled: false
    property var   flashRoi: []
    property int   videoWidth: 0
    property int   videoHeight: 0

    property bool active: false

    signal seek(int frame)
    signal stepBy(int delta)
    signal togglePlay()
    signal inChanged(int frame)
    signal outChanged(int frame)
    signal pinChanged(int frame)
    signal detectWindowChanged(real seconds)
    signal flashRoiEdited(var roi)
    signal activated()

    spacing: Theme.s3
    width:  parent ? parent.width : implicitWidth
    height: parent ? parent.height : implicitHeight

    readonly property int timelineFrame: playing ? player.playheadFrame : currentFrame
    readonly property int _outBound: outFrame > 0 ? outFrame : Math.max(0, frameCount - 1)

    function jumpTo(f) {
        player.jumpToFrame(f)
        root.seek(f)
    }

    function stepOneFrame(delta) {
        if (root.playing)
            root.togglePlay()
        const f = Math.max(0, Math.min(root.frameCount - 1, root.timelineFrame + delta))
        player.jumpToFrame(f)
        root.stepBy(delta)
    }

    TapHandler {
        acceptedButtons: Qt.LeftButton
        onTapped: root.activated()
    }

    Rectangle {
        Layout.fillWidth: true
        Layout.fillHeight: true
        Layout.minimumHeight: 180
        color: Theme.video
        radius: Theme.radiusSm
        clip: true
        border.color: root.active
            ? Theme.accentText
            : (playing ? Theme.accentText : Theme.border)
        border.width: root.active ? 2 : (playing ? 2 : 1)
        Behavior on border.color { ColorAnimation { duration: Theme.motionBase } }

        SyncVideoPlayer {
            id: player
            anchors.fill: parent
            anchors.margins: 1
            videoPath: root.videoPath
            fps: root.fps
            frameCount: root.frameCount
            frame: root.currentFrame
            inFrame: root.inFrame
            outFrame: root._outBound
            playing: root.playing
            onFrameSyncRequested: (f) => root.seek(f)
        }

        FlashRoiOverlay {
            id: flashRoi
            anchors.fill: parent
            anchors.margins: 1
            z: 5
            drawEnabled: root.roiDrawEnabled
            frameWidth: root.videoWidth
            frameHeight: root.videoHeight
            roi: root.flashRoi
            onRoiEdited: (r) => root.flashRoiEdited(r)
        }

        Text {
            anchors.centerIn: parent
            visible: frameCount > 0 && player.loading
            text: qsTr("Chargement vidéo…")
            font.family: Theme.monoFamily
            font.pixelSize: Theme.fzSm
            color: Theme.textDim
        }

        Text {
            anchors.centerIn: parent
            visible: frameCount <= 0 || player.hasError
            text: frameCount <= 0
                ? qsTr("Vidéo illisible\n(chemin / fichier verrouillé)")
                : qsTr("Lecture impossible\n(vérifiez le fichier MP4)")
            font.family: Theme.fontFamily
            font.pixelSize: Theme.fzSm
            color: Theme.textDim
            horizontalAlignment: Text.AlignHCenter
            wrapMode: Text.WordWrap
            width: parent.width - Theme.spaceLg
        }

        Rectangle {
            x: 10
            y: 8
            width: badgeLabel.implicitWidth + 14
            height: badgeLabel.implicitHeight + 8
            radius: Theme.radiusSm
            color: Qt.rgba(0.01, 0.03, 0.05, 0.6)
            Text {
                id: badgeLabel
                anchors.centerIn: parent
                text: root.sideLabel
                font.family: Theme.monoFamily
                font.pixelSize: 10
                font.weight: Font.DemiBold
                color: Theme.accentText
            }
        }

        Rectangle {
            anchors.right: parent.right
            anchors.bottom: parent.bottom
            anchors.margins: 8
            width: frmText.implicitWidth + 14
            height: frmText.implicitHeight + 8
            radius: Theme.radiusSm
            color: Qt.rgba(0.01, 0.03, 0.05, 0.6)
            Text {
                id: frmText
                anchors.centerIn: parent
                text: {
                    const f = root.timelineFrame >= 0 ? root.timelineFrame : 0
                    const n = Math.max(1, root.frameCount)
                    return f.toString().padStart(4, "0") + " / " + n
                }
                font.family: Theme.monoFamily
                font.pixelSize: 10
                color: "#cbd5e1"
            }
        }
    }

    SectionSurface {
        padding: 8
        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.s2

            TransportIconButton {
                glyphKind: TransportGlyph.Rewind
                enabled: root.frameCount > 0
                tooltipText: qsTr("Retour au début")
                onClicked: jumpTo(0)
            }

            TransportIconButton {
                glyphKind: TransportGlyph.StepBack
                enabled: root.frameCount > 0 && root.timelineFrame > 0
                tooltipText: qsTr("Image précédente (←)")
                onClicked: root.stepOneFrame(-1)
            }

            TransportIconButton {
                glyphKind: root.playing ? TransportGlyph.Pause : TransportGlyph.Play
                primary: true
                enabled: root.frameCount > 0
                tooltipText: root.playing ? qsTr("Pause (Espace)") : qsTr("Lecture (Espace)")
                onClicked: root.togglePlay()
            }

            TransportIconButton {
                glyphKind: TransportGlyph.StepForward
                enabled: root.frameCount > 0 && root.timelineFrame < root.frameCount - 1
                tooltipText: qsTr("Image suivante (→)")
                onClicked: root.stepOneFrame(1)
            }

            Rectangle {
                width: 1
                height: 22
                color: Theme.border
                Layout.alignment: Qt.AlignVCenter
            }

            Text {
                text: qsTr("Frame %1").arg(root.timelineFrame >= 0 ? root.timelineFrame : 0)
                font.family: Theme.monoFamily
                font.pixelSize: Theme.fzSm
                color: Theme.text
                Layout.alignment: Qt.AlignVCenter
            }

            Text {
                text: Sync.formatTimecode(root.timelineFrame >= 0 ? root.timelineFrame : 0, root.fps)
                font.family: Theme.monoFamily
                font.pixelSize: Theme.fzSm
                color: Theme.textDim
                Layout.alignment: Qt.AlignVCenter
            }

            Item { Layout.fillWidth: true }

            GhostButton {
                text: qsTr("Flash manuel")
                small: true
                requires: root.frameCount > 0 && root.timelineFrame >= 0
                disabledReason: qsTr("Aucune vidéo chargée sur cette caméra : choisissez-la dans le panneau de gauche.")
                tooltipText: qsTr("Désigne vous-même l'image où le flash apparaît sur cette caméra, quand la détection automatique se trompe.")
                onClicked: {
                    const f = root.timelineFrame >= 0 ? root.timelineFrame : 0
                    jumpTo(f)
                    root.pinChanged(f)
                }
            }
        }

        VideoTimeline {
            Layout.fillWidth: true
            totalFrames:  Math.max(1, root.frameCount)
            currentFrame: root.timelineFrame
            inFrame:      root.inFrame
            outFrame:     root._outBound
            pinFrame:     root.pinFrame
            detectWindowS: root.detectWindowS
            fps:          root.fps
            onSeekRequested: (f) => jumpTo(f)
            onInMarkerMoved: (f) => root.inChanged(f)
            onOutMarkerMoved:(f) => root.outChanged(f)
            onFlashPinMoved: (f) => root.pinChanged(f)
            onDetectWindowChanged: (s) => root.detectWindowChanged(s)
        }
    }
}
