import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import AquaMeasure

SpinBox {
    id: control

    // Les deux boutons occupent 84 px a eux seuls (4 + 32 + 6 de chaque cote).
    // En dessous, le champ de saisie est ecrase et la valeur devient
    // illisible, voire invisible : c'etait le cas de « Confiance min (%) »
    // (72 px) et du comptage par frame (88 px). Le minimum reserve de quoi
    // afficher trois chiffres.
    readonly property int chromeWidth: (4 + 32 + 6) * 2
    readonly property int minReadableWidth: chromeWidth + 48

    implicitHeight: 40
    implicitWidth: Math.max(148, minReadableWidth)
    Layout.minimumWidth: minReadableWidth
    stepSize: 1
    editable: true
    font.family: Theme.fontFamily
    font.pixelSize: Theme.fontBody

    palette {
        base: Theme.bgElevated
        window: Theme.bgElevated
        text: Theme.text
        button: Theme.surfaceHover
        buttonText: Theme.text
    }

    contentItem: TextInput {
        z: 2
        anchors.left: control.down.indicator.right
        anchors.right: control.up.indicator.left
        anchors.verticalCenter: parent.verticalCenter
        anchors.leftMargin: 6
        anchors.rightMargin: 6
        height: Math.max(24, parent.height - 8)
        text: control.textFromValue(control.value, control.locale)
        font: control.font
        color: Theme.text
        selectionColor: Theme.accent
        selectedTextColor: "#FFFFFF"
        horizontalAlignment: Qt.AlignHCenter
        verticalAlignment: Text.AlignVCenter
        readOnly: !control.editable
        validator: control.validator
        inputMethodHints: Qt.ImhFormattedNumbersOnly
        // SpinBox valide deja la saisie a la perte de focus. Entrée doit
        // aussi terminer l'edition, pas seulement actualiser le nombre.
        Keys.onReturnPressed: control.focus = false
        Keys.onEnterPressed: control.focus = false
    }

    // Les zones vides et les boutons sans focus ne quittent pas spontanement
    // un TextInput. Observer la pression dans la fenetre sans absorber le clic
    // permet de valider avant que le bouton declenche son action.
    Item {
        parent: control.Window.window ? control.Window.window.contentItem : null
        anchors.fill: parent
        // Observer avant les controles ; PointHandler laisse passer la souris.
        z: 10000
        visible: control.activeFocus && control.visible
        PointHandler {
            acceptedButtons: Qt.LeftButton | Qt.RightButton
            onActiveChanged: {
                if (!active || !control.activeFocus)
                    return
                const pos = control.mapFromItem(parent, point.position.x, point.position.y)
                if (!control.contains(pos))
                    control.focus = false
            }
        }
    }

    background: Rectangle {
        z: 0
        implicitHeight: 40
        radius: Theme.radiusSm
        color: Theme.bgElevated
        border.width: control.activeFocus ? 2 : 1
        border.color: control.activeFocus ? Theme.accentBlueSoft : Theme.border
    }

    up.indicator: Rectangle {
        z: 3
        x: parent.width - width - 4
        y: 4
        width: 32
        height: parent.height - 8
        radius: Theme.radiusSm
        color: control.up.pressed ? Theme.surfaceActive : Theme.surfaceHover
        border.color: Theme.border
        Text {
            anchors.centerIn: parent
            text: "+"
            color: Theme.text
            font.pixelSize: Theme.fontBody
        }
    }

    down.indicator: Rectangle {
        z: 3
        x: 4
        y: 4
        width: 32
        height: parent.height - 8
        radius: Theme.radiusSm
        color: control.down.pressed ? Theme.surfaceActive : Theme.surfaceHover
        border.color: Theme.border
        Text {
            anchors.centerIn: parent
            text: "−"
            color: Theme.text
            font.pixelSize: Theme.fontBody
        }
    }
}
