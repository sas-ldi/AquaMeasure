import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import AquaMeasure

MenuItem {
    id: control

    leftPadding: Theme.s3
    rightPadding: Theme.s3
    topPadding: Theme.s2
    bottomPadding: Theme.s2

    readonly property string shortcutText: {
        if (!control.action || !control.action.shortcut)
            return ""
        const seq = control.action.shortcut.sequence
        if (seq === undefined || seq === "")
            return ""
        return typeof seq === "string" ? seq : seq.toString()
    }
    readonly property bool hasShortcut: shortcutText.length > 0

    // Les Menu gardent aussi dans leur contentModel les commandes propres aux
    // autres pages. Une commande invisible doit donc réellement occuper 0 px,
    // sinon elle laisse un grand blanc dans le menu courant.
    implicitHeight: control.visible
        ? Math.max(Theme.controlH, contentRow.implicitHeight + topPadding + bottomPadding)
        : 0

    indicator: Item {
        implicitWidth: 0
        implicitHeight: 0
    }

    contentItem: RowLayout {
        id: contentRow
        width: parent.width
        spacing: Theme.s2

        Item {
            visible: control.checkable
            Layout.preferredWidth: control.checkable ? 16 : 0
            Layout.preferredHeight: 16
            Layout.alignment: Qt.AlignVCenter

            Canvas {
                anchors.fill: parent
                visible: control.checked
                onPaint: {
                    const ctx = getContext("2d")
                    ctx.reset()
                    ctx.strokeStyle = Theme.accent
                    ctx.lineWidth = 1.75
                    ctx.lineCap = "round"
                    ctx.lineJoin = "round"
                    ctx.beginPath()
                    ctx.moveTo(3, 8)
                    ctx.lineTo(6.5, 11.5)
                    ctx.lineTo(13, 4)
                    ctx.stroke()
                }
                Component.onCompleted: requestPaint()
                onVisibleChanged: if (visible) requestPaint()
            }
        }

        Text {
            Layout.fillWidth: true
            Layout.alignment: Qt.AlignVCenter
            text: control.text
            elide: Text.ElideRight
            font.family: Theme.fontFamily
            font.pixelSize: Theme.fzBase
            color: control.enabled ? Theme.text : Theme.textDim
        }

        Text {
            visible: control.hasShortcut
            Layout.alignment: Qt.AlignVCenter
            text: control.shortcutText
            font.family: Theme.monoFamily
            font.pixelSize: Theme.fzXs
            color: Theme.textDim
        }
    }

    background: Rectangle {
        radius: Theme.radiusSm
        color: control.highlighted ? Theme.accentSoft : "transparent"
    }
}
