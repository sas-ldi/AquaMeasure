import QtQuick
import QtQuick.Layouts
import AquaMeasure

// Aperçu calibration ChArUco - taille = image (PreserveAspectFit), loupe x4, clic = plein écran.
ColumnLayout {
    id: root

    required property string caption
    required property string imageSource

    signal previewClicked()

    /** Hauteur partagée (contrôlée par la barre sous les deux vues). */
    property real paneHeight: 300
    /** Largeur max par vue (pour tenir dans la colonne centrale). */
    property real slotMaxWidth: -1

    readonly property real minPaneHeight: 160
    readonly property real maxPaneHeight: 520
    readonly property real maxPaneWidth: 680
    readonly property real zoomFactor: 4.0

    readonly property real imgAspect: {
        if (preview.status === Image.Ready && preview.sourceSize.height > 0)
            return preview.sourceSize.width / preview.sourceSize.height
        return 16 / 9
    }

    readonly property real displayHeight: {
        var h = Math.max(root.minPaneHeight, Math.min(root.maxPaneHeight, root.paneHeight))
        if (root.slotMaxWidth > 0 && root.imgAspect > 0) {
            const wAtH = h * root.imgAspect
            if (wAtH > root.slotMaxWidth)
                h = root.slotMaxWidth / root.imgAspect
        }
        return Math.round(Math.max(root.minPaneHeight, Math.min(root.maxPaneHeight, h)))
    }
    readonly property real displayWidth: {
        var w = Math.round(root.displayHeight * root.imgAspect)
        if (root.slotMaxWidth > 0)
            w = Math.min(w, root.slotMaxWidth)
        return Math.round(Math.min(w, root.maxPaneWidth))
    }

    spacing: Theme.spaceXs
    Layout.preferredWidth: displayWidth
    Layout.maximumWidth: displayWidth
    Layout.alignment: Qt.AlignHCenter

    AppLabel {
        text: root.caption
        font.pixelSize: Theme.fontCaption
        color: Theme.textMuted
        horizontalAlignment: Text.AlignHCenter
        Layout.preferredWidth: root.displayWidth
        elide: Text.ElideRight
    }

    Item {
        id: viewport
        width: root.displayWidth
        height: root.displayHeight
        Layout.preferredWidth: root.displayWidth
        Layout.preferredHeight: root.displayHeight

        function imageMapping(mx, my) {
            const iw = preview.sourceSize.width
            const ih = preview.sourceSize.height
            if (iw <= 0 || ih <= 0)
                return { valid: false, ix: 0, iy: 0, scale: 1, ox: 0, oy: 0, dw: 0, dh: 0 }
            const scale = Math.min(root.displayWidth / iw, root.displayHeight / ih)
            const dw = iw * scale
            const dh = ih * scale
            const ox = (root.displayWidth - dw) / 2
            const oy = (root.displayHeight - dh) / 2
            const ix = (mx - ox) / scale
            const iy = (my - oy) / scale
            const inside = mx >= ox && mx <= ox + dw && my >= oy && my <= oy + dh
            return { valid: inside, ix: ix, iy: iy, scale: scale, ox: ox, oy: oy, dw: dw, dh: dh, iw: iw, ih: ih }
        }

        Rectangle {
            id: frame
            anchors.fill: parent
            color: "#000000"
            radius: Theme.radiusMd
            border.color: viewportMa.containsMouse ? Theme.accent : Theme.border
            border.width: viewportMa.containsMouse ? 2 : 1
            clip: false

            Behavior on border.color { ColorAnimation { duration: Theme.motionFast } }

            Image {
                id: preview
                anchors.centerIn: parent
                width: root.displayWidth
                height: root.displayHeight
                fillMode: Image.PreserveAspectFit
                asynchronous: true
                cache: false
                mipmap: true
                source: root.imageSource
            }

            AppLabel {
                anchors.centerIn: parent
                visible: root.imageSource !== ""
                        && (preview.status === Image.Loading || preview.status === Image.Null)
                muted: true
                text: qsTr("Chargement aperçu…")
            }

            MouseArea {
                id: viewportMa
                anchors.fill: parent
                hoverEnabled: true
                acceptedButtons: Qt.LeftButton
                cursorShape: containsMouse ? Qt.PointingHandCursor : Qt.ArrowCursor
                preventStealing: true

                onClicked: root.previewClicked()
            }
        }

        Item {
            id: loupeHost
            visible: viewportMa.containsMouse
                    && preview.status === Image.Ready
                    && preview.sourceSize.width > 0
            z: 20
            width: Math.min(240, root.displayWidth * 0.55)
            height: Math.min(180, root.displayHeight * 0.55)

            readonly property var map: viewport.imageMapping(viewportMa.mouseX, viewportMa.mouseY)

            x: {
                const pad = 8
                let lx = viewportMa.mouseX - width / 2
                if (lx < pad)
                    lx = pad
                if (lx + width > viewport.width - pad)
                    lx = viewport.width - pad - width
                return lx
            }
            y: {
                const pad = 8
                let ly = viewportMa.mouseY - height - 12
                if (ly < pad)
                    ly = viewportMa.mouseY + 16
                if (ly + height > viewport.height - pad)
                    ly = viewport.height - pad - height
                return ly
            }
            opacity: map.valid ? 1 : 0

            Behavior on opacity { NumberAnimation { duration: Theme.motionFast } }

            Rectangle {
                anchors.fill: parent
                radius: Theme.radiusSm
                color: "#050810"
                border.color: Theme.accent
                border.width: 2
                clip: true

                Image {
                    source: preview.source
                    fillMode: Image.Stretch
                    width: (loupeHost.map.iw || 1) * root.zoomFactor
                    height: (loupeHost.map.ih || 1) * root.zoomFactor
                    x: loupeHost.map.valid ? (-loupeHost.map.ix * root.zoomFactor + loupeHost.width / 2) : 0
                    y: loupeHost.map.valid ? (-loupeHost.map.iy * root.zoomFactor + loupeHost.height / 2) : 0
                }

                Rectangle {
                    anchors.centerIn: parent
                    width: 10
                    height: 10
                    radius: 5
                    color: "transparent"
                    border.color: Theme.accentText
                    border.width: 1
                    opacity: 0.85
                }
            }

            Text {
                anchors.left: parent.left
                anchors.bottom: parent.top
                anchors.bottomMargin: 4
                text: qsTr("×%1").arg(root.zoomFactor)
                font.family: Theme.monoFamily
                font.pixelSize: Theme.fzXs
                color: Theme.accentText
            }
        }
    }

    AppLabel {
        Layout.preferredWidth: root.displayWidth
        visible: viewportMa.containsMouse && loupeHost.map.valid
        horizontalAlignment: Text.AlignHCenter
        font.pixelSize: Theme.fzXs
        font.family: Theme.monoFamily
        color: Theme.textDim
        text: qsTr("Clic pour plein écran · loupe ×%1 au survol").arg(root.zoomFactor)
    }
}
