import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import AquaMeasure

// Panneau de progression calibration - étapes visibles, RMSE en direct, barre animée.
ColumnLayout {
    id: root
    spacing: Theme.s5
    Layout.fillWidth: true

    readonly property real fillRatio: Math.max(0, Math.min(1, Calib.progress / 100))
    property int elapsedSec: 0

    Timer {
        running: Calib.busy
        repeat: true
        interval: 1000
        onTriggered: root.elapsedSec += 1
        onRunningChanged: if (!running) root.elapsedSec = 0
    }

    readonly property string elapsedLabel: {
        const m = Math.floor(elapsedSec / 60)
        const s = elapsedSec % 60
        return m > 0 ? qsTr("%1:%2").arg(m).arg(s < 10 ? "0" + s : s)
                     : qsTr("0:%1").arg(s < 10 ? "0" + s : s)
    }

    // ── Étapes pipeline ─────────────────────────────────────────────────
    RowLayout {
        spacing: Theme.s2
        Layout.alignment: Qt.AlignLeft

        Repeater {
            model: [
                { label: qsTr("Scan"),       stage: 0 },
                { label: qsTr("Paires"),    stage: 1 },
                { label: qsTr("Focale G"),  stage: 2 },
                { label: qsTr("Focale D"),  stage: 3 },
                { label: qsTr("Stéréo"),    stage: 4 },
                { label: qsTr("Sauve"),     stage: 5 }
            ]

            RowLayout {
                spacing: Theme.s2

                Rectangle {
                    width: 22
                    height: 22
                    radius: 11
                    color: Calib.workStage > modelData.stage
                        ? Theme.okSoft
                        : (Calib.workStage === modelData.stage ? Theme.accentSoft : Theme.panel2)
                    border.color: Calib.workStage > modelData.stage
                        ? Theme.ok
                        : (Calib.workStage === modelData.stage ? Theme.accent : Theme.border)
                    border.width: Calib.workStage === modelData.stage ? 2 : 1

                    Text {
                        anchors.centerIn: parent
                        text: Calib.workStage > modelData.stage
                            ? "✓"
                            : (Calib.workStage === modelData.stage && !Calib.progressActive ? "…" : String(modelData.stage + 1))
                        font.family: Theme.monoFamily
                        font.pixelSize: Theme.fzXs
                        font.weight: Font.DemiBold
                        color: Calib.workStage > modelData.stage
                            ? Theme.ok
                            : (Calib.workStage === modelData.stage ? Theme.accentText : Theme.textDim)
                    }

                    SequentialAnimation on opacity {
                        running: Calib.busy && Calib.workStage === modelData.stage && !Calib.progressActive
                        loops: Animation.Infinite
                        NumberAnimation { to: 0.45; duration: Theme.motionSlow; easing.type: Easing.InOutSine }
                        NumberAnimation { to: 1.0; duration: Theme.motionSlow; easing.type: Easing.InOutSine }
                    }
                }

                Text {
                    text: modelData.label
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.fzXs
                    font.weight: Calib.workStage === modelData.stage ? Font.DemiBold : Font.Normal
                    color: Calib.workStage === modelData.stage
                        ? Theme.text
                        : (Calib.workStage > modelData.stage ? Theme.ok : Theme.textDim)
                }

                Rectangle {
                    visible: modelData.stage < 5
                    Layout.preferredWidth: 10
                    Layout.preferredHeight: 1
                    color: Calib.workStage > modelData.stage ? Theme.ok : Theme.border2
                }
            }
        }
    }

    // ── Phase + détail ──────────────────────────────────────────────────
    ColumnLayout {
        Layout.fillWidth: true
        spacing: Theme.s1

        Text {
            Layout.fillWidth: true
            text: Calib.phase !== "" ? Calib.phase : qsTr("Calibration en cours…")
            wrapMode: Text.Wrap
            font.family: Theme.fontFamily
            font.pixelSize: Theme.fzLg
            font.weight: Font.DemiBold
            color: Theme.accentText
        }

        Text {
            Layout.fillWidth: true
            visible: Calib.detail !== ""
            text: Calib.detail
            wrapMode: Text.Wrap
            font.family: Theme.fontFamily
            font.pixelSize: Theme.fzSm
            color: Theme.textMuted
            lineHeight: 1.35
        }
    }

    // ── Barre de progression + arrêt ────────────────────────────────────
    RowLayout {
        Layout.fillWidth: true
        spacing: Theme.s3

        ColumnLayout {
            Layout.fillWidth: true
            spacing: Theme.s2

            Item {
                Layout.fillWidth: true
                implicitHeight: 14

                Rectangle {
                    id: track
                    anchors.fill: parent
                    radius: 7
                    color: Theme.panel2
                    border.color: Theme.border
                    border.width: 1
                }

                Rectangle {
                    id: fill
                    height: parent.height
                width: Math.max(track.width * 0.015, track.width * root.fillRatio)
                radius: 7
                clip: true

                gradient: Gradient {
                    orientation: Gradient.Horizontal
                    GradientStop { position: 0.0; color: "#1d4ed8" }
                    GradientStop { position: 0.55; color: Theme.accent }
                    GradientStop { position: 1.0; color: Theme.accentText }
                }

                Behavior on width {
                    NumberAnimation {
                        duration: Theme.motionBase
                        easing.type: Easing.OutCubic
                    }
                }

                Rectangle {
                    anchors.right: parent.right
                    anchors.verticalCenter: parent.verticalCenter
                    width: 32
                    height: parent.height
                    gradient: Gradient {
                        orientation: Gradient.Horizontal
                        GradientStop { position: 0.0; color: "transparent" }
                        GradientStop { position: 1.0; color: "#55ffffff" }
                    }
                    opacity: 0.4
                }
            }

            // Shimmer quand OpenCV travaille sans tick de progression
            Rectangle {
                visible: Calib.busy && !Calib.progressActive
                height: parent.height
                width: Math.min(80, track.width * 0.22)
                radius: 7
                x: shimmerX
                color: Theme.accentText
                opacity: 0.28

                property real shimmerX: track.width * 0.08

                SequentialAnimation on shimmerX {
                    running: parent.visible
                    loops: Animation.Infinite
                    NumberAnimation {
                        from: 0
                        to: Math.max(0, track.width - 80)
                        duration: 1600
                        easing.type: Easing.InOutQuad
                    }
                    NumberAnimation {
                        from: Math.max(0, track.width - 80)
                        to: 0
                        duration: 1600
                        easing.type: Easing.InOutQuad
                    }
                }
            }
            }

            RowLayout {
                Layout.fillWidth: true

                Text {
                    Layout.fillWidth: true
                    text: Calib.progressActive
                        ? qsTr("Progression · %1 écoulées").arg(root.elapsedLabel)
                        : qsTr("Calcul OpenCV… · %1 (normal si long)").arg(root.elapsedLabel)
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.fzXs
                    color: Theme.textDim
                    wrapMode: Text.Wrap
                }

                Text {
                    text: Calib.progress + " %"
                    font.family: Theme.monoFamily
                    font.pixelSize: Theme.fzXl
                    font.weight: Font.DemiBold
                    color: Theme.text
                }
            }
        }

        DangerIconButton {
            id: cancelBtn
            Layout.alignment: Qt.AlignVCenter
            enabled: Calib.busy
            onClicked: Calib.cancel()

            ToolTip.visible: cancelBtn.hoverActive
            ToolTip.text: qsTr("Arrêter la calibration - la calibration précédente sera rétablie si elle existait.")
            ToolTip.delay: 400
        }
    }

    // ── RMSE en direct ──────────────────────────────────────────────────
    Rectangle {
        Layout.fillWidth: true
        implicitHeight: rmseCol.implicitHeight + Theme.s4 * 2
        radius: Theme.radiusSm
        color: Theme.panel2
        border.color: Theme.border2
        border.width: 1

        ColumnLayout {
            id: rmseCol
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: parent.top
            anchors.margins: Theme.s4
            spacing: Theme.s2

            Text {
                text: qsTr("RMSE de reprojection (temps réel)")
                font.family: Theme.fontFamily
                font.pixelSize: Theme.fzXs
                font.weight: Font.Medium
                color: Theme.textMuted
            }

            RmseQualityRow {
                Layout.fillWidth: true
                title: qsTr("Caméra gauche")
                rmse: Calib.rmseLeft
                computing: Calib.busy && Calib.workStage === 2 && Calib.rmseLeft < 0
            }
            RmseQualityRow {
                Layout.fillWidth: true
                title: qsTr("Caméra droite")
                rmse: Calib.rmseRight
                computing: Calib.busy && Calib.workStage === 3 && Calib.rmseRight < 0
            }
            RmseQualityRow {
                Layout.fillWidth: true
                title: qsTr("Stéréo")
                rmse: Calib.rmseStereo
                computing: Calib.busy && Calib.workStage === 4 && Calib.rmseStereo < 0
            }
        }
    }

    Text {
        Layout.fillWidth: true
        wrapMode: Text.WordWrap
        font.family: Theme.fontFamily
        font.pixelSize: Theme.fzXs
        color: Theme.textDim
        text: qsTr("Arrêt : si une calibration était déjà enregistrée, elle sera rétablie.")
    }
}
