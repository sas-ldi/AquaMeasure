import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import AquaMeasure

ColumnLayout {
    id: root
    objectName: "trackEventList"
    readonly property bool armed: typeof Pecks !== "undefined" && Pecks.selectedTrackId.length > 0
    spacing: Theme.s1
    AppLabel {
        objectName: "recordedEventCount"
        Layout.fillWidth: true
        Layout.minimumWidth: 0
        visible: root.armed
        text: qsTr("%1 point(s) sur la piste").arg(Pecks.markerCount)
        font.pixelSize: Theme.fzXs
        color: Theme.textMuted
        wrapMode: Text.WordWrap
    }
        ColumnLayout {
            id: observationEvents
            objectName: "observationEventList"
            Layout.fillWidth: true
            Layout.minimumWidth: 0
            readonly property var entries: Data.selectedObservationEvents.filter(function(row) {
                return row.scope !== "point"
            })
            visible: entries.length > 0
            Repeater {
                model: observationEvents.entries
                delegate: RowLayout {
                    required property var modelData
                    Layout.fillWidth: true
                    AppLabel {
                        objectName: "observationEventLabel"
                        Layout.fillWidth: true
                        Layout.minimumWidth: 0
                        text: modelData.symbol + " " + modelData.label + " · "
                            + (modelData.detail || qsTr("image %1").arg(modelData.frameAbs))
                        color: Theme.textMuted
                        font.pixelSize: Theme.fzXs
                        elide: Text.ElideRight
                    }
                    DangerIconButton {
                        objectName: "observationEventRemoveButton"
                        visible: modelData.scope === "instant" || modelData.scope === "interval"
                        enabled: !Data.busy
                        implicitWidth: 20
                        implicitHeight: 20
                        onClicked: {
                            if (modelData.scope === "interval")
                                Data.deleteSelectedDurationEvent(modelData.eventId)
                            else
                                Data.toggleSelectedPointEvent(modelData.key)
                        }
                    }
                }
            }
        }

        // ── Marqueurs posés, retirables un par un ───────────────────────
        ListView {
            id: markerList
            objectName: "peckMarkerList"
            Layout.fillWidth: true
            Layout.preferredHeight: Math.min(72, Math.max(0, Pecks.markerCount) * 24)
            visible: root.armed && Pecks.markerCount > 0
            clip: true
            model: Pecks.markers

            // Ancres et non layout : un RowLayout refuse de descendre sous le
            // minimum de ses enfants et debordait la liste, poussant la
            // corbeille hors du volet - donc hors d'atteinte. Ancre a droite,
            // elle reste cliquable quelle que soit la largeur.
            delegate: Item {
                width: markerList.width
                height: 24

                AppLabel {
                    id: markerSymbol
                    anchors.left: parent.left
                    anchors.verticalCenter: parent.verticalCenter
                    text: modelData.symbol
                    color: modelData.color
                    font.pixelSize: Theme.fzXs
                }
                DangerIconButton {
                    id: markerRemove
                    objectName: "peckRemoveButton"
                    anchors.right: parent.right
                    anchors.verticalCenter: parent.verticalCenter
                    implicitWidth: 20
                    implicitHeight: 20
                    onClicked: Pecks.removeMarker(modelData.eventId)
                }
                AppLabel {
                    anchors.left: markerSymbol.right
                    anchors.right: markerRemove.left
                    anchors.leftMargin: Theme.s2
                    anchors.rightMargin: Theme.s2
                    anchors.verticalCenter: parent.verticalCenter
                    text: qsTr("%1 · image %2").arg(modelData.label)
                        .arg(modelData.frameAbs)
                    font.family: Theme.monoFamily
                    font.pixelSize: Theme.fzXs
                    color: Theme.textMuted
                    elide: Text.ElideRight

                    MouseArea {
                        anchors.fill: parent
                        cursorShape: Qt.PointingHandCursor
                        onClicked: Pecks.seekToMarker(modelData.eventId)
                    }
                }
            }
        }
}
