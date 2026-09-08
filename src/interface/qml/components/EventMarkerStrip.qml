import QtQuick
import QtQuick.Controls
import AquaMeasure

// Graduation des marqueurs ponctuels sous la timeline.
//
// Un événement d'une seule frame est invisible sur une barre de progression :
// à 30 images par seconde, trois bouchées en dix secondes tiennent dans un
// pixel. Chaque marqueur reçoit donc une hampe de largeur fixe, avec le
// pictogramme et la couleur de son type, et la plage de la piste travaillée
// est teintée derrière pour dire où l'on doit chercher.
Item {
    id: root

    property int frameCount: 0
    property int currentFrame: 0
    // Plage de la piste sélectionnée, en index timeline. -1 = aucune piste.
    property int rangeStart: -1
    property int rangeEnd: -1
    // [{ eventId, frame, symbol, color, label }] — frame en index timeline.
    property var markers: []
    property bool interactive: true
    // Posée par-dessus une barre existante (VideoTimeline), la graduation ne
    // doit ni redessiner un rail ni un second curseur de lecture.
    property bool showBaseline: true
    property int barMargin: Theme.s2
    property bool zoomEnabled: false
    // Changer de piste ou de vidéo revient à la vue complète.
    property string viewKey: ""
    property real viewStart: 0
    property real zoomFactor: 1
    readonly property real viewSpan: Math.max(0, frameCount - 1) / zoomFactor
    readonly property real viewEnd: viewStart + viewSpan

    onFrameCountChanged: resetZoom()
    onViewKeyChanged: resetZoom()
    onZoomEnabledChanged: resetZoom()

    function resetZoom() {
        viewStart = 0
        zoomFactor = 1
    }

    // x est relatif au rail. L'image sous la souris conserve sa position,
    // sauf lorsque la fenêtre atteint une extrémité de la vidéo.
    function zoomAt(factor, x) {
        if (!zoomEnabled || frameCount <= 1 || bar.width <= 0)
            return
        const t = Math.max(0, Math.min(1, x / bar.width))
        const anchor = viewStart + t * viewSpan
        const nextZoom = Math.max(1, Math.min(frameCount - 1, zoomFactor * factor))
        const nextSpan = (frameCount - 1) / nextZoom
        viewStart = Math.max(0, Math.min(frameCount - 1 - nextSpan,
                                       anchor - t * nextSpan))
        zoomFactor = nextZoom
    }

    WheelHandler {
        target: null
        enabled: root.zoomEnabled && root.interactive && root.frameCount > 1
        acceptedDevices: PointerDevice.Mouse | PointerDevice.TouchPad
        onWheel: (event) => {
            const delta = event.angleDelta.y !== 0
                ? event.angleDelta.y / 120 : event.pixelDelta.y / 40
            if (delta === 0) {
                event.accepted = false
                return
            }
            root.zoomAt(Math.pow(1.25, delta), event.x - bar.x)
            event.accepted = true
        }
    }

    signal seekRequested(int frame)
    signal markerActivated(string eventId, int frame)

    objectName: "eventMarkerStrip"
    implicitHeight: 34

    readonly property int markerCount: markers ? markers.length : 0
    readonly property bool hasRange: rangeStart >= 0 && rangeEnd >= rangeStart

    function frameToX(f) {
        if (frameCount <= 1)
            return 0
        return ((f - viewStart) / viewSpan) * bar.width
    }

    function xToFrame(x) {
        if (frameCount <= 1)
            return 0
        const t = Math.max(0, Math.min(1, x / Math.max(1, bar.width)))
        return Math.round(viewStart + t * viewSpan)
    }

    Rectangle {
        id: bar
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.leftMargin: root.barMargin
        anchors.rightMargin: root.barMargin
        anchors.verticalCenter: parent.verticalCenter
        height: 4
        radius: 2
        color: root.showBaseline ? Theme.panel2 : "transparent"
        border.color: Theme.border
        border.width: root.showBaseline ? 1 : 0

        // Plage suivie : le repère qui évite de chercher la piste au hasard.
        Rectangle {
            objectName: "peckRangeBand"
            visible: root.hasRange && root.frameCount > 1
                && root.rangeEnd >= root.viewStart && root.rangeStart <= root.viewEnd
            x: Math.max(0, root.frameToX(root.rangeStart))
            width: Math.min(bar.width - x,
                Math.max(2, Math.min(bar.width, root.frameToX(root.rangeEnd)) - x))
            anchors.verticalCenter: parent.verticalCenter
            height: parent.height + 6
            radius: 2
            color: Qt.rgba(Theme.accent.r, Theme.accent.g, Theme.accent.b, 0.22)
            border.color: Qt.rgba(Theme.accent.r, Theme.accent.g, Theme.accent.b, 0.55)
            border.width: 1
        }

        Rectangle {
            objectName: "peckPlayhead"
            visible: root.showBaseline && root.currentFrame >= root.viewStart
                && root.currentFrame <= root.viewEnd
            x: root.frameToX(root.currentFrame) - 0.5
            width: 1
            height: parent.height + 16
            anchors.verticalCenter: parent.verticalCenter
            color: Theme.playhead
            opacity: 0.8
            z: 2
        }

        // Posée par-dessus une autre barre, la graduation ne prend QUE les
        // clics sur ses marqueurs : avaler le reste volerait le déplacement du
        // curseur et les poignées de la timeline qui l'accueille.
        MouseArea {
            anchors.fill: parent
            anchors.topMargin: -8
            anchors.bottomMargin: -8
            enabled: root.interactive && root.showBaseline && root.frameCount > 1
            onPressed: (mouse) => root.seekRequested(root.xToFrame(mouse.x))
        }

        Repeater {
            model: root.markers
            delegate: Item {
                objectName: "peckMarker"
                property string eventId: modelData.eventId
                property int markerFrame: modelData.frame
                visible: markerFrame >= root.viewStart && markerFrame <= root.viewEnd

                x: root.frameToX(modelData.frame) - width / 2
                anchors.verticalCenter: parent.verticalCenter
                width: 14
                height: 30
                z: 3

                Rectangle {
                    anchors.horizontalCenter: parent.horizontalCenter
                    anchors.verticalCenter: parent.verticalCenter
                    width: markerMa.containsMouse ? 3 : 2
                    height: 18
                    radius: 1
                    color: modelData.color || Theme.markPin
                }

                Text {
                    anchors.horizontalCenter: parent.horizontalCenter
                    anchors.bottom: parent.top
                    anchors.bottomMargin: -4
                    text: modelData.symbol || "●"
                    font.pixelSize: 10
                    color: modelData.color || Theme.markPin
                }

                MouseArea {
                    id: markerMa
                    anchors.fill: parent
                    hoverEnabled: true
                    enabled: root.interactive
                    cursorShape: Qt.PointingHandCursor
                    ToolTip.visible: containsMouse
                    ToolTip.text: qsTr("%1 · image %2 — cliquer pour y revenir")
                        .arg(modelData.label || "").arg(modelData.frame)
                    onClicked: root.markerActivated(
                        modelData.eventId, modelData.frame)
                }
            }
        }
    }
}
