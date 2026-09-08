import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import AquaMeasure

Popup {
    id: root

    property string caption: ""
    property string imageSource: ""

    modal: true
    focus: true
    closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
    padding: 0

    parent: Overlay.overlay
    width: parent ? parent.width : 1280
    height: parent ? parent.height : 800
    x: 0
    y: 0

    // ── Zoom / déplacement ────────────────────────────────────────────────
    readonly property real minZoom: 1.0      // 1.0 = ajusté à la fenêtre
    readonly property real maxZoom: 8.0
    property real zoom: 1.0
    property real panX: 0
    property real panY: 0

    function clampZoom(z) {
        return Math.max(root.minZoom, Math.min(root.maxZoom, z))
    }

    function fit() {
        root.zoom = 1.0
        root.panX = 0
        root.panY = 0
    }

    function clampPan() {
        if (root.zoom <= root.minZoom + 0.0001) {
            root.panX = 0
            root.panY = 0
            return
        }
        const mx = Math.max(0, (fullImg.width - viewport.width) / 2)
        const my = Math.max(0, (fullImg.height - viewport.height) / 2)
        root.panX = Math.max(-mx, Math.min(mx, root.panX))
        root.panY = Math.max(-my, Math.min(my, root.panY))
    }

    // Zoome autour du point (vx, vy) exprimé dans le repère du viewport.
    function zoomAt(factor, vx, vy) {
        const nz = clampZoom(root.zoom * factor)
        const realFactor = root.zoom > 0 ? nz / root.zoom : 1
        if (Math.abs(realFactor - 1) < 0.0001)
            return
        const w = fullImg.width
        const h = fullImg.height
        const x0 = (viewport.width - w) / 2 + root.panX
        const y0 = (viewport.height - h) / 2 + root.panY
        const fx = w > 0 ? (vx - x0) / w : 0.5
        const fy = h > 0 ? (vy - y0) / h : 0.5
        const wn = w * realFactor
        const hn = h * realFactor
        const x0n = vx - fx * wn
        const y0n = vy - fy * hn
        root.zoom = nz
        root.panX = x0n - (viewport.width - wn) / 2
        root.panY = y0n - (viewport.height - hn) / 2
        clampPan()
    }

    onOpened: fit()
    onImageSourceChanged: fit()

    background: Rectangle {
        color: Theme.bg
    }

    Overlay.modal: Rectangle {
        color: Qt.rgba(0, 0, 0, 0.85)
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 0

        // ── Barre d'outils ────────────────────────────────────────────────
        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: 48
            color: Theme.panel

            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: Theme.s3
                anchors.rightMargin: Theme.s2
                spacing: Theme.s2

                AppLabel {
                    Layout.fillWidth: true
                    text: root.caption.length > 0 ? root.caption : qsTr("Aperçu")
                    font.weight: Font.DemiBold
                    elide: Text.ElideRight
                }

                GhostButton {
                    text: "−"
                    small: true
                    enabled: root.zoom > root.minZoom + 0.001
                    onClicked: root.zoomAt(1.0 / 1.25, viewport.width / 2, viewport.height / 2)
                }

                AppLabel {
                    Layout.preferredWidth: 56
                    horizontalAlignment: Text.AlignHCenter
                    font.family: Theme.monoFamily
                    color: Theme.textMuted
                    text: Math.round(root.zoom * 100) + "%"
                }

                GhostButton {
                    text: "+"
                    small: true
                    enabled: root.zoom < root.maxZoom - 0.001
                    onClicked: root.zoomAt(1.25, viewport.width / 2, viewport.height / 2)
                }

                GhostButton {
                    text: qsTr("Ajuster")
                    small: true
                    enabled: root.zoom > root.minZoom + 0.001
                            || Math.abs(root.panX) > 0.5 || Math.abs(root.panY) > 0.5
                    onClicked: root.fit()
                }

                GhostButton {
                    text: qsTr("Fermer")
                    small: true
                    onClicked: root.close()
                }
            }

            Rectangle {
                anchors.bottom: parent.bottom
                width: parent.width
                height: 1
                color: Theme.border
            }
        }

        // ── Zone image (zoom + déplacement) ───────────────────────────────
        Item {
            id: viewport
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.margins: Theme.s2
            clip: true

            Image {
                id: fullImg
                source: root.imageSource
                cache: false
                asynchronous: true
                mipmap: true
                smooth: true
                fillMode: Image.Stretch

                readonly property real fitScale: {
                    if (sourceSize.width <= 0 || sourceSize.height <= 0)
                        return 1
                    return Math.min(viewport.width / sourceSize.width,
                                    viewport.height / sourceSize.height)
                }

                width: sourceSize.width > 0
                    ? sourceSize.width * fitScale * root.zoom
                    : viewport.width
                height: sourceSize.height > 0
                    ? sourceSize.height * fitScale * root.zoom
                    : viewport.height

                x: (viewport.width - width) / 2 + root.panX
                y: (viewport.height - height) / 2 + root.panY

                onStatusChanged: if (status === Image.Ready) root.clampPan()
            }

            WheelHandler {
                acceptedDevices: PointerDevice.Mouse | PointerDevice.TouchPad
                onWheel: (event) => {
                    const step = event.angleDelta.y > 0 ? 1.15 : (1.0 / 1.15)
                    root.zoomAt(step, event.x, event.y)
                }
            }

            MouseArea {
                id: panMa
                anchors.fill: parent
                hoverEnabled: true
                enabled: root.zoom > root.minZoom + 0.001
                cursorShape: enabled
                    ? (pressed ? Qt.ClosedHandCursor : Qt.OpenHandCursor)
                    : Qt.ArrowCursor
                preventStealing: true

                property real _lastX: 0
                property real _lastY: 0

                onPressed: (mouse) => {
                    _lastX = mouse.x
                    _lastY = mouse.y
                }
                onPositionChanged: (mouse) => {
                    if (!pressed)
                        return
                    root.panX += mouse.x - _lastX
                    root.panY += mouse.y - _lastY
                    _lastX = mouse.x
                    _lastY = mouse.y
                    root.clampPan()
                }
            }

            AppLabel {
                anchors.centerIn: parent
                visible: root.imageSource !== ""
                    && (fullImg.status === Image.Loading || fullImg.status === Image.Null)
                muted: true
                text: qsTr("Chargement…")
            }
        }

        AppLabel {
            Layout.alignment: Qt.AlignHCenter
            Layout.bottomMargin: Theme.s2
            muted: true
            font.pixelSize: Theme.fzXs
            text: qsTr("Molette ou +/− pour zoomer · glisser pour déplacer · Échap pour fermer")
        }
    }
}
