import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import AquaMeasure

// Overlay de chargement - détection flash (synchronisation).
Item {
    id: root
    anchors.fill: parent
    visible: active
    z: 20

    property bool active: false
    property int progress: 0
    property string title: qsTr("Détection du flash")
    property string stage: ""
    property string detail: ""

    readonly property real fillRatio: Math.max(0, Math.min(1, progress / 100))

    Rectangle {
        anchors.fill: parent
        color: "#b80b1015"

        gradient: Gradient {
            orientation: Gradient.Vertical
            GradientStop { position: 0.0; color: "#990b1015" }
            GradientStop { position: 0.5; color: "#c00b1015" }
            GradientStop { position: 1.0; color: "#e60b1015" }
        }
    }

    Rectangle {
        id: card
        anchors.centerIn: parent
        width: Math.min(root.width - Theme.space2xl * 2, 460)
        implicitHeight: cardCol.implicitHeight + Theme.s6 * 2
        radius: Theme.radiusLg
        color: Theme.elevated
        border.color: Theme.border2
        border.width: 1
        clip: true

        Rectangle {
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: parent.top
            height: 3
            gradient: Gradient {
                orientation: Gradient.Horizontal
                GradientStop { position: 0.0; color: Theme.accent }
                GradientStop { position: 1.0; color: Theme.accentText }
            }
        }

        ColumnLayout {
            id: cardCol
            anchors.fill: parent
            anchors.margins: Theme.s6
            spacing: Theme.s5

            RowLayout {
                Layout.fillWidth: true
                spacing: Theme.s4

                Item {
                    Layout.preferredWidth: 44
                    Layout.preferredHeight: 44

                    Rectangle {
                        id: spinnerTrack
                        anchors.centerIn: parent
                        width: 32
                        height: 32
                        radius: 16
                        color: "transparent"
                        border.width: 3
                        border.color: Theme.panel2

                        Rectangle {
                            id: spinnerArc
                            anchors.fill: parent
                            radius: 16
                            color: "transparent"
                            border.width: 3
                            border.color: Theme.accent
                            clip: true

                            Rectangle {
                                width: parent.width
                                height: parent.height / 2
                                anchors.top: parent.top
                                color: Theme.elevated
                            }
                        }

                        RotationAnimation on rotation {
                            running: root.active
                            loops: Animation.Infinite
                            from: 0
                            to: 360
                            duration: 850
                            easing.type: Easing.Linear
                        }
                    }
                }

                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: Theme.s1

                    Text {
                        Layout.fillWidth: true
                        text: root.title
                        font.family: Theme.fontFamily
                        font.pixelSize: Theme.fzXl
                        font.weight: Font.DemiBold
                        color: Theme.text
                        wrapMode: Text.WordWrap
                    }

                    Text {
                        Layout.fillWidth: true
                        visible: root.stage !== ""
                        text: root.stage
                        font.family: Theme.fontFamily
                        font.pixelSize: Theme.fzSm
                        font.weight: Font.DemiBold
                        color: Theme.accentText
                    }
                }
            }

            Text {
                Layout.fillWidth: true
                Layout.maximumWidth: card.width - Theme.s6 * 2
                horizontalAlignment: Text.AlignHCenter
                text: root.detail
                wrapMode: Text.WordWrap
                font.family: Theme.fontFamily
                font.pixelSize: Theme.fzSm
                lineHeight: 1.35
                color: Theme.textMuted
            }

            ColumnLayout {
                Layout.fillWidth: true
                spacing: Theme.s3

                Item {
                    Layout.fillWidth: true
                    implicitHeight: 12

                    Rectangle {
                        id: track
                        anchors.fill: parent
                        radius: 6
                        color: Theme.panel2
                        border.color: Theme.border
                        border.width: 1
                    }

                    Rectangle {
                        id: fill
                        height: parent.height
                        width: Math.max(track.width * 0.02, track.width * root.fillRatio)
                        radius: 6
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
                    }
                }

                RowLayout {
                    Layout.fillWidth: true

                    Text {
                        Layout.fillWidth: true
                        text: qsTr("Analyse en cours")
                        font.family: Theme.fontFamily
                        font.pixelSize: Theme.fzXs
                        color: Theme.textDim
                    }

                    Text {
                        text: root.progress + " %"
                        font.family: Theme.monoFamily
                        font.pixelSize: Theme.fzXl
                        font.weight: Font.DemiBold
                        color: Theme.text
                    }
                }
            }
        }
    }
}
