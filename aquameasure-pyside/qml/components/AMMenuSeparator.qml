import QtQuick
import QtQuick.Controls
import AquaMeasure

// Séparateur compact qui disparaît sans réserver de place avec visible: false.
MenuSeparator {
    id: control

    leftPadding: Theme.s1
    rightPadding: Theme.s1
    topPadding: control.visible ? Theme.s1 : 0
    bottomPadding: control.visible ? Theme.s1 : 0
    implicitHeight: control.visible
        ? separatorLine.implicitHeight + topPadding + bottomPadding
        : 0

    contentItem: Rectangle {
        id: separatorLine
        implicitHeight: 1
        color: Theme.border
    }
}
