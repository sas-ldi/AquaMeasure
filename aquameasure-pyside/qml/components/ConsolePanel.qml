import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import AquaMeasure

ColumnLayout {
    id: root

    property var logModel: null
    property bool autoScroll: true
    property bool expanded: true
    property int bodyHeight: 180
    property var openLogsFolder: null

    // logModel.count est une propriété notifiante (LogModel.countChanged) :
    // logModel.rowCount() était une simple méthode, donc évaluée une seule fois
    // et jamais rafraîchie - compteur, bouton « Effacer » et message
    // « Aucun message » restaient figés sur l'état initial (console vide).
    readonly property int logCount: root.logModel ? root.logModel.count : 0

    readonly property int headerHeight: headerRow.implicitHeight
    readonly property int expandedLayoutHeight: headerHeight + Theme.spaceSm + bodyHeight

    spacing: Theme.spaceSm
    Layout.fillWidth: true
    Layout.preferredHeight: expanded ? expandedLayoutHeight : headerHeight
    Layout.maximumHeight: expanded ? expandedLayoutHeight : headerHeight

    Behavior on Layout.preferredHeight {
        NumberAnimation {
            duration: Theme.motionSlow
            easing.type: Theme.easeOut
        }
    }

    function scrollLogToEnd() {
        if (!logList.count)
            return
        logList.positionViewAtEnd()
    }

    RowLayout {
        id: headerRow
        Layout.fillWidth: true
        spacing: Theme.spaceSm

        Item {
            id: headerToggle
            Layout.fillWidth: true
            Layout.preferredHeight: headerToggleRow.implicitHeight

            RowLayout {
                id: headerToggleRow
                anchors.fill: parent
                spacing: Theme.spaceSm

                Item {
                    Layout.preferredWidth: 22
                    Layout.preferredHeight: 22
                    rotation: root.expanded ? 0 : -90
                    Behavior on rotation {
                        NumberAnimation {
                            duration: Theme.motionBase
                            easing.type: Theme.easeOut
                        }
                    }
                    Text {
                        anchors.centerIn: parent
                        text: "\u25BC"
                        font.pixelSize: 10
                        color: Theme.textMuted
                    }
                }

                AppLabel {
                    text: qsTr("Console")
                    font.pixelSize: Theme.fontCaption
                    color: Theme.textMuted
                    font.weight: Font.DemiBold
                }

                AppLabel {
                    visible: root.logCount > 0
                    text: "(" + root.logCount + ")"
                    font.pixelSize: Theme.fontCaption
                    color: Theme.textDim
                }

                Item { Layout.fillWidth: true }
            }

            MouseArea {
                anchors.fill: parent
                cursorShape: Qt.PointingHandCursor
                onClicked: root.expanded = !root.expanded
            }
        }

        AppButton {
            text: qsTr("Dossier logs")
            visible: root.expanded && root.openLogsFolder
            opacity: visible ? 1 : 0
            Behavior on opacity { NumberAnimation { duration: Theme.motionFast } }
            onClicked: {
                if (root.openLogsFolder)
                    root.openLogsFolder()
            }
        }

        AppButton {
            text: qsTr("Effacer")
            visible: root.expanded
            enabled: root.logCount > 0
            opacity: visible ? 1 : 0
            Behavior on opacity { NumberAnimation { duration: Theme.motionFast } }
            onClicked: {
                if (root.logModel && root.logModel.clear)
                    root.logModel.clear()
            }
        }

        AppButton {
            text: root.expanded ? qsTr("Réduire") : qsTr("Afficher console")
            onClicked: root.expanded = !root.expanded
        }
    }

    Item {
        id: bodyClip
        Layout.fillWidth: true
        Layout.preferredHeight: root.expanded ? root.bodyHeight : 0
        clip: true
        opacity: root.expanded ? 1 : 0

        Behavior on Layout.preferredHeight {
            NumberAnimation {
                duration: Theme.motionSlow
                easing.type: Theme.easeOut
            }
        }
        Behavior on opacity {
            NumberAnimation {
                duration: Theme.motionBase
                easing.type: Theme.easeOut
            }
        }

        Rectangle {
            width: parent.width
            height: root.bodyHeight
            color: "#0a0e14"
            radius: Theme.radiusSm
            border.color: Theme.border

            AppLabel {
                objectName: "consoleEmptyPlaceholder"
                z: 10
                anchors.centerIn: parent
                visible: root.expanded && root.logCount === 0
                muted: true
                text: qsTr("Aucun message - lancez sync, calibration ou mesure")
                font.pixelSize: Theme.fontCaption
            }

            ListView {
                id: logList
                anchors.fill: parent
                anchors.margins: Theme.spaceSm
                clip: true
                boundsBehavior: Flickable.StopAtBounds
                model: root.logModel
                spacing: 2
                ScrollBar.vertical: ScrollBar {
                    policy: ScrollBar.AsNeeded
                }

                delegate: TextEdit {
                    width: logList.width - Theme.spaceSm * 2
                    height: contentHeight
                    readOnly: true
                    selectByMouse: true
                    wrapMode: TextEdit.Wrap
                    text: model.message
                    color: "#94a3b8"
                    font.family: Theme.monoFamily
                    font.pixelSize: Theme.fontCaption
                }

                onCountChanged: {
                    if (root.autoScroll)
                        Qt.callLater(root.scrollLogToEnd)
                }
            }
        }
    }

    Connections {
        target: root.logModel
        function onFullTextChanged() {
            if (root.autoScroll)
                Qt.callLater(root.scrollLogToEnd)
        }
    }

    onExpandedChanged: {
        if (expanded && autoScroll)
            Qt.callLater(scrollLogToEnd)
    }
}
