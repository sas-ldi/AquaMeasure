import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import AquaMeasure

// Barre de transport commune (Mesure / preview Données).
RowLayout {
    id: root
    property int frameIndex: 0
    property int frameCount: 0
    property double fps: 30
    property bool playing: false
    property bool showInOut: false
    property bool showJumps: true

    signal togglePlay()
    signal stepBy(int delta)
    signal seek(int frame)
    signal setIn()
    signal setOut()

    spacing: width < 760 ? Theme.s1 : Theme.s3
    Layout.fillWidth: true
    implicitHeight: Theme.controlH + Theme.s2

    // Saut rapide encadrant le pas a pas : recul a gauche, avance a droite.
    // Le pas se regle en survolant le bouton (voir TransportJumpButton) et
    // vaut pour les deux sens.
    TransportJumpButton {
        visible: root.showJumps
        direction: -1
        fps: root.fps
        jumpEnabled: root.frameCount > 0 && root.frameIndex > 0
        onJump: function(delta) { root.stepBy(delta) }
    }
    TransportIconButton {
        glyphKind: TransportGlyph.StepBack
        enabled: root.frameCount > 0 && root.frameIndex > 0
        tooltipText: qsTr("Image précédente (←)")
        onClicked: root.stepBy(-1)
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
        enabled: root.frameCount > 0 && root.frameIndex < root.frameCount - 1
        tooltipText: qsTr("Image suivante (→)")
        onClicked: root.stepBy(1)
    }
    TransportJumpButton {
        visible: root.showJumps
        direction: 1
        fps: root.fps
        jumpEnabled: root.frameCount > 0 && root.frameIndex < root.frameCount - 1
        onJump: function(delta) { root.stepBy(delta) }
    }

    Rectangle { width: 1; height: 22; color: Theme.border }

    AppLabel {
        text: root.frameCount > 0
            ? (root.width < 760 ? qsTr("%1 / %2  %3") : qsTr("Frame %1 / %2  %3"))
                .arg(String(root.frameIndex + 1).padStart(4, "0"))
                .arg(root.frameCount)
                .arg(Sync.formatTimecode(
                    typeof Measure !== "undefined" ? Measure.leftAbsFrame : root.frameIndex,
                    root.fps))
            : "-"
        color: Theme.textMuted
        font.family: Theme.monoFamily
        font.pixelSize: Theme.fzSm
    }

    Slider {
        id: scrub
        from: 0
        to: Math.max(0, root.frameCount - 1)
        value: root.frameIndex
        enabled: root.frameCount > 0
        Layout.fillWidth: true
        Layout.minimumWidth: 120
        onMoved: root.seek(Math.round(value))
        onPressedChanged: if (pressed)
            root.seek(Math.round(scrub.value))
    }

    // showInOut n'est activé par aucun appelant : les signaux setIn/setOut
    // n'étaient donc branchés nulle part. Conservés pour la découpe (Sync),
    // mais avec une infobulle explicite si la barre est réutilisée.
    RowLayout {
        visible: root.showInOut
        spacing: Theme.s2
        RibbonButton {
            text: "In"
            small: true
            infoText: qsTr("Marque le début de la découpe à la frame courante")
            onClicked: root.setIn()
        }
        RibbonButton {
            text: "Out"
            small: true
            infoText: qsTr("Marque la fin de la découpe à la frame courante")
            onClicked: root.setOut()
        }
    }
}
