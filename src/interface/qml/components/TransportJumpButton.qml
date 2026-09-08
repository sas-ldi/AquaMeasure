import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import AquaMeasure

// Bouton d'avance / recul rapide, avec son reglage au clic droit.
//
// Le pas est le meme pour les deux sens et vit dans les preferences
// (`Settings.transportJump*`) : il est donc conserve d'une session a l'autre.
// Un clic droit ouvre le panneau pour choisir l'unite puis la quantite.
// Le clic gauche conserve le saut ; le survol ne fait pas apparaitre le panneau.
Item {
    id: root

    // -1 = recul, +1 = avance.
    property int direction: 1
    property double fps: 30
    // Le panneau s'ouvre meme bouton grise : regler son pas avant d'avoir
    // charge une video est legitime.
    property bool jumpEnabled: true

    // Saut demande, exprime en images et deja signe.
    signal jump(int delta)

    implicitWidth: 32
    implicitHeight: 32

    readonly property bool _forward: direction >= 0
    readonly property int _step: Settings.transportJumpStep(root.fps)
    readonly property string _amountLabel: Settings.transportJumpLabel
    readonly property bool _hovered: buttonArea.containsMouse || settingsPanel.opened

    function _emitJump() {
        if (!root.jumpEnabled)
            return
        root.jump(root._forward ? root._step : -root._step)
    }

    Rectangle {
        id: button
        anchors.fill: parent
        radius: Theme.radiusSm
        opacity: root.jumpEnabled ? 1 : 0.45

        color: {
            if (!root.jumpEnabled)
                return Theme.panel2
            if (buttonArea.pressed)
                return Theme.surfaceActive
            if (root._hovered)
                return Theme.surfaceHover
            return Theme.panel2
        }
        border.color: root._hovered && root.jumpEnabled ? Theme.border2 : Theme.border
        border.width: 1

        TransportGlyph {
            anchors.centerIn: parent
            width: 13
            height: 13
            kind: root._forward ? TransportGlyph.FastForward : TransportGlyph.Rewind
            glyphColor: root.jumpEnabled ? Theme.textMuted : Theme.textDim
        }

        MouseArea {
            id: buttonArea
            anchors.fill: parent
            hoverEnabled: true
            acceptedButtons: Qt.LeftButton | Qt.RightButton
            cursorShape: root.jumpEnabled ? Qt.PointingHandCursor : Qt.ArrowCursor
            onClicked: function(mouse) {
                if (mouse.button === Qt.RightButton)
                    settingsPanel.open()
                else
                    root._emitJump()
            }
        }
    }

    // Pastille du pas courant : sans elle, rien ne dit de combien on avance
    // avant d'avoir clique.
    Rectangle {
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        anchors.rightMargin: -2
        anchors.bottomMargin: -3
        width: stepBadge.implicitWidth + 6
        height: stepBadge.implicitHeight + 2
        radius: 3
        color: Theme.panel
        border.color: Theme.border2
        border.width: 1
        visible: root.jumpEnabled

        Text {
            id: stepBadge
            anchors.centerIn: parent
            text: root._amountLabel
            font.family: Theme.monoFamily
            font.pixelSize: 8
            color: Theme.textDim
        }
    }

    Popup {
        id: settingsPanel

        readonly property bool seconds: Settings.transportJumpUnit === "seconds"
        readonly property var presets: seconds ? [1, 2, 5, 10, 30] : [5, 10, 15, 30, 60]
        readonly property double amount: seconds
            ? Settings.transportJumpSeconds
            : Settings.transportJumpFrames

        // Ancre au-dessus du bouton, aligne sur son bord exterieur pour que le
        // panneau ne sorte pas de la fenetre aux extremites de la barre.
        x: root._forward ? root.width - width : 0
        y: -height - 6
        padding: Theme.s3
        focus: true
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside

        // `margins` active le repositionnement automatique de Qt : la barre de
        // transport occupe toute la largeur, et le premier bouton est colle au
        // bord gauche. Sans cette marge, son panneau sortait de la fenetre et
        // le libelle etait coupe.
        margins: Theme.s2

        background: Rectangle {
            color: Theme.panel
            radius: Theme.radius
            border.color: Theme.border2
            border.width: 1
        }

        contentItem: ColumnLayout {
            spacing: Theme.s2

            AppLabel {
                text: root._forward
                    ? qsTr("Avance rapide")
                    : qsTr("Recul rapide")
                font.pixelSize: Theme.fzXs
                color: Theme.textDim
            }

            RowLayout {
                spacing: Theme.s1

                RibbonButton {
                    text: qsTr("Images")
                    toggle: true
                    checked: !settingsPanel.seconds
                    infoText: qsTr("Le saut compte un nombre d'images, quelle que soit la cadence.")
                    onClicked: Settings.transportJumpUnit = "frames"
                }
                RibbonButton {
                    text: qsTr("Secondes")
                    toggle: true
                    checked: settingsPanel.seconds
                    infoText: qsTr("Le saut compte une durée, convertie en images selon la cadence de la vidéo.")
                    onClicked: Settings.transportJumpUnit = "seconds"
                }
            }

            RowLayout {
                spacing: Theme.s1

                Repeater {
                    model: settingsPanel.presets
                    RibbonButton {
                        required property var modelData
                        text: settingsPanel.seconds
                            ? qsTr("%1 s").arg(modelData)
                            : String(modelData)
                        toggle: true
                        checked: Math.abs(settingsPanel.amount - modelData) < 0.001
                        onClicked: Settings.setTransportJump(
                            Settings.transportJumpUnit, modelData)
                    }
                }
            }

            RowLayout {
                spacing: Theme.s2

                AppLabel {
                    text: qsTr("Autre :")
                    font.pixelSize: Theme.fzXs
                    color: Theme.textDim
                }

                AppSpinBox {
                    id: customAmount
                    // Le spin travaille en entier : en secondes, on compte en
                    // dixiemes pour autoriser 0,5 s sans champ flottant.
                    from: 1
                    to: settingsPanel.seconds ? 6000 : 9999
                    value: settingsPanel.seconds
                        ? Math.round(Settings.transportJumpSeconds * 10)
                        : Settings.transportJumpFrames
                    editable: true
                    Layout.preferredWidth: 132

                    textFromValue: function(v) {
                        return settingsPanel.seconds
                            ? (v / 10).toLocaleString(Qt.locale(), "f", 1) + " s"
                            : v + " img"
                    }
                    valueFromText: function(text) {
                        const digits = String(text).replace(",", ".").replace(/[^0-9.]/g, "")
                        const parsed = parseFloat(digits)
                        if (isNaN(parsed))
                            return customAmount.value
                        return settingsPanel.seconds
                            ? Math.round(parsed * 10)
                            : Math.round(parsed)
                    }
                    onValueModified: Settings.setTransportJump(
                        Settings.transportJumpUnit,
                        settingsPanel.seconds ? value / 10 : value)
                }
            }

            AppLabel {
                text: settingsPanel.seconds
                    ? qsTr("Soit %1 images à %2 img/s")
                        .arg(root._step)
                        .arg((root.fps > 0 ? root.fps : 30).toFixed(2))
                    : qsTr("Soit %1 s à %2 img/s")
                        .arg((root._step / (root.fps > 0 ? root.fps : 30)).toFixed(2))
                        .arg((root.fps > 0 ? root.fps : 30).toFixed(2))
                font.pixelSize: Theme.fzXs
                color: Theme.textDim
            }
        }
    }

    ToolTip.visible: buttonArea.containsMouse && !settingsPanel.opened
    ToolTip.text: root._forward
        ? qsTr("Avancer de %1 · Ctrl+→ (clic droit pour régler)").arg(root._amountLabel)
        : qsTr("Reculer de %1 · Ctrl+← (clic droit pour régler)").arg(root._amountLabel)
}
