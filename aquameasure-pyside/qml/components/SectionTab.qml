import QtQuick
import QtQuick.Layouts
import AquaMeasure

// Onglet de navigation principale (barre horizontale).
// Propriétés : text, done (pastille ✓), active.
// Largeur fixe (réserve le gras + pastille) - l'indicateur bleu est géré par le parent.
Item {
    id: root
    property string text: ""
    property bool   done:   false
    property bool   active: false

    signal clicked()

    readonly property int badgeSlot: 16
    readonly property int hPad: Theme.s6

    TextMetrics {
        id: tmBold
        font.family: Theme.fontFamily
        font.pixelSize: Theme.fzBase
        font.weight: Font.DemiBold
        text: root.text
    }

    implicitWidth: tmBold.width + Theme.s2 + badgeSlot + hPad * 2
    implicitHeight: Theme.tabBarH
    width: implicitWidth

    // géométrie du trait bleu - aligné sur le libellé (1er item du row centré)
    readonly property real indicatorX: row.x
    readonly property real indicatorWidth: tmBold.width

    RowLayout {
        id: row
        anchors.centerIn: parent
        spacing: Theme.s2

        Text {
            id: label
            text: root.text
            font.family: Theme.fontFamily
            font.pixelSize: Theme.fzBase
            font.weight: root.active ? Font.DemiBold : Font.Normal
            color: root.active ? Theme.text : Theme.textMuted

            Behavior on color {
                ColorAnimation { duration: Theme.motionFast; easing: Theme.easeOut }
            }
        }

        // emplacement fixe pour la pastille « terminé »
        Item {
            width: root.badgeSlot
            height: root.badgeSlot

            Rectangle {
                visible: root.done && !root.active
                anchors.centerIn: parent
                width: root.badgeSlot
                height: root.badgeSlot
                radius: root.badgeSlot / 2
                color: Theme.okSoft
                border.color: Theme.ok
                border.width: 1

                Text {
                    anchors.centerIn: parent
                    text: "✓"
                    font.pixelSize: 8
                    font.weight: Font.Bold
                    color: Theme.ok
                }
            }
        }
    }

    MouseArea {
        anchors.fill: parent
        cursorShape: Qt.PointingHandCursor
        onClicked: root.clicked()
    }
}
