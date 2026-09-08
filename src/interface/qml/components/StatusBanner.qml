import QtQuick
import AquaMeasure

Rectangle {
    id: banner
    property string message: ""
    property string level: "info" // info | success | error

    width: parent ? parent.width : 400
    visible: message !== ""
    radius: Theme.radiusSm
    color: {
        if (level === "error") return Theme.errorBg
        if (level === "success") return Theme.successBg
        return "#1A2D4A"
    }
    border.color: level === "error" ? Theme.error : (level === "success" ? Theme.success : Theme.accentBlueSoft)
    border.width: 1

    implicitHeight: visible ? msg.implicitHeight + Theme.spaceLg * 2 : 0
    height: implicitHeight

    opacity: visible ? 1 : 0
    Behavior on opacity { NumberAnimation { duration: Theme.motionBase } }

    AppLabel {
        id: msg
        width: banner.width - Theme.spaceLg * 2
        x: Theme.spaceLg
        y: Theme.spaceLg
        text: banner.message
        font.pixelSize: Theme.fontBody
        color: Theme.text
        wrapMode: Text.WordWrap
    }
}
