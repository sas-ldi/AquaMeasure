import QtQuick
import QtQuick.Layouts
import AquaMeasure

Item {
    id: root

    property string text: ""
    property bool checked: false
    property bool toggleEnabled: true
    // Explication (i) affichée en bout de ligne.
    property string info: ""

    signal toggled()

    implicitHeight: row.implicitHeight
    Layout.fillWidth: true
    opacity: root.toggleEnabled ? 1 : 0.5

    RowLayout {
        id: row
        anchors.left: parent.left
        anchors.right: parent.right
        spacing: Theme.s2

        Rectangle {
            Layout.preferredWidth: 16
            Layout.preferredHeight: 16
            radius: 4
            color: root.checked ? Theme.accent : "transparent"
            border.color: root.checked ? Theme.accent : Theme.border2
            border.width: 1

            Canvas {
                anchors.fill: parent
                visible: root.checked
                onPaint: {
                    const ctx = getContext("2d")
                    ctx.reset()
                    ctx.strokeStyle = "#ffffff"
                    ctx.lineWidth = 1.5
                    ctx.lineCap = "round"
                    ctx.lineJoin = "round"
                    ctx.beginPath()
                    ctx.moveTo(3.5, 8)
                    ctx.lineTo(6.5, 11)
                    ctx.lineTo(12.5, 5)
                    ctx.stroke()
                }
                Component.onCompleted: requestPaint()
                onVisibleChanged: if (visible) requestPaint()
            }
        }

        Text {
            Layout.fillWidth: true
            text: root.text
            font.family: Theme.fontFamily
            font.pixelSize: Theme.fzSm
            color: root.checked ? Theme.text : Theme.textMuted
            wrapMode: Text.WordWrap
        }

        InfoDot {
            diameter: 14
            text: root.info
        }
    }

    // Le (i) est hors de la zone de bascule : le survoler ou le cliquer ne
    // doit pas cocher/décocher l'option.
    MouseArea {
        anchors.fill: parent
        anchors.rightMargin: root.info.length > 0 ? 20 : 0
        enabled: root.toggleEnabled
        cursorShape: enabled ? Qt.PointingHandCursor : Qt.ArrowCursor
        onClicked: root.toggled()
    }
}
