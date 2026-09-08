import QtQuick
import QtQuick.Layouts
import AquaMeasure

// Deux destinations seulement : travailler/relire une session, ou alimenter
// la bibliothèque Fishial locale. Les anciennes pages Explorateur, Pistes et
// Fin de session fragmentaient un seul et même travail.
Rectangle {
    id: root

    property int currentIndex: 0
    property var labels: [
        qsTr("Session"),
        qsTr("Fishial")
    ]

    signal tabClicked(int index)

    implicitHeight: 40
    color: "transparent"

    Rectangle {
        anchors.bottom: parent.bottom
        width: parent.width
        height: 1
        color: Theme.border
    }

    RowLayout {
        anchors.fill: parent
        spacing: 0

        Repeater {
            model: root.labels
            delegate: Item {
                Layout.preferredHeight: 40
                Layout.preferredWidth: tabLabel.implicitWidth + Theme.s5 * 2 + 24

                ColumnLayout {
                    anchors.fill: parent
                    spacing: 0

                    RowLayout {
                        Layout.alignment: Qt.AlignHCenter | Qt.AlignVCenter
                        spacing: Theme.s2
                        WorkspaceIcon {
                            name: index === 0 ? "folder" : "detect"
                            tint: index === root.currentIndex ? Theme.accentText : Theme.textMuted
                            implicitWidth: 16
                            implicitHeight: 16
                        }
                        AppLabel {
                            id: tabLabel
                            text: modelData
                            font.pixelSize: Theme.fzSm
                            font.weight: index === root.currentIndex ? Font.DemiBold : Font.Normal
                            color: index === root.currentIndex ? Theme.accentText : Theme.textMuted
                        }
                    }

                    Rectangle {
                        Layout.fillWidth: true
                        Layout.preferredHeight: 2
                        Layout.topMargin: 6
                        color: index === root.currentIndex ? Theme.accent : "transparent"
                    }
                }

                MouseArea {
                    anchors.fill: parent
                    cursorShape: Qt.PointingHandCursor
                    // Ne pas écrire currentIndex ici : cela détruisait le binding
                    // sur Data.subTab et désynchronisait la barre de la pile.
                    onClicked: root.tabClicked(index)
                }
            }
        }

        Item { Layout.fillWidth: true }
    }
}
