import QtQuick
import AquaMeasure

// Cadre dessinable pour limiter la détection du flash à une zone fixe de l'image.
Item {
    id: root

    property bool drawEnabled: false
    property int frameWidth: 0
    property int frameHeight: 0
    property var roi: []   // [x, y, w, h] normalisés 0..1 ou vide

    signal roiEdited(var roi)

    function _transform() {
        const iw = Math.max(1, frameWidth)
        const ih = Math.max(1, frameHeight)
        const s = Math.min(width / iw, height / ih)
        const ox = (width - iw * s) / 2
        const oy = (height - ih * s) / 2
        return { s: s, ox: ox, oy: oy, iw: iw, ih: ih }
    }

    function _toDisp(xn, yn, wn, hn) {
        const t = _transform()
        return {
            x: t.ox + xn * t.iw * t.s,
            y: t.oy + yn * t.ih * t.s,
            w: wn * t.iw * t.s,
            h: hn * t.ih * t.s
        }
    }

    function _toNorm(xd, yd) {
        const t = _transform()
        return {
            x: Math.max(0, Math.min(1, (xd - t.ox) / (t.iw * t.s))),
            y: Math.max(0, Math.min(1, (yd - t.oy) / (t.ih * t.s)))
        }
    }

    property real _dragX0: -1
    property real _dragY0: -1
    property real _dragX1: -1
    property real _dragY1: -1

    Canvas {
        id: canvas
        anchors.fill: parent
        onPaint: {
            const ctx = getContext("2d")
            ctx.clearRect(0, 0, width, height)

            function drawRect(xn, yn, wn, hn, stroke, fill) {
                const d = root._toDisp(xn, yn, wn, hn)
                ctx.strokeStyle = stroke
                ctx.lineWidth = 2
                ctx.fillStyle = fill
                ctx.fillRect(d.x, d.y, d.w, d.h)
                ctx.strokeRect(d.x, d.y, d.w, d.h)
            }

            if (root.roi && root.roi.length >= 4 && root.roi[2] > 0 && root.roi[3] > 0) {
                drawRect(root.roi[0], root.roi[1], root.roi[2], root.roi[3],
                         "#38bdf8", "rgba(56, 189, 248, 0.18)")
            }

            if (root._dragX0 >= 0 && root._dragX1 >= 0) {
                const p1 = root._toNorm(root._dragX0, root._dragY0)
                const p2 = root._toNorm(root._dragX1, root._dragY1)
                const x0 = Math.min(p1.x, p2.x)
                const y0 = Math.min(p1.y, p2.y)
                const w = Math.abs(p2.x - p1.x)
                const h = Math.abs(p2.y - p1.y)
                drawRect(x0, y0, w, h, "#f59e0b", "rgba(245, 158, 11, 0.08)")
            }
        }
    }

    onRoiChanged: canvas.requestPaint()

    MouseArea {
        anchors.fill: parent
        enabled: root.drawEnabled && root.frameWidth > 0 && root.frameHeight > 0
        cursorShape: enabled ? Qt.CrossCursor : Qt.ArrowCursor

        onPressed: function(mouse) {
            root._dragX0 = mouse.x
            root._dragY0 = mouse.y
            root._dragX1 = mouse.x
            root._dragY1 = mouse.y
            canvas.requestPaint()
        }

        onPositionChanged: function(mouse) {
            if (!pressed)
                return
            root._dragX1 = mouse.x
            root._dragY1 = mouse.y
            canvas.requestPaint()
        }

        onReleased: function(mouse) {
            const p1 = root._toNorm(root._dragX0, root._dragY0)
            const p2 = root._toNorm(mouse.x, mouse.y)
            root._dragX0 = -1
            root._dragY1 = -1
            root._dragX1 = -1
            root._dragY0 = -1
            const x0 = Math.min(p1.x, p2.x)
            const y0 = Math.min(p1.y, p2.y)
            const w = Math.abs(p2.x - p1.x)
            const h = Math.abs(p2.y - p1.y)
            if (w >= 0.02 && h >= 0.02) {
                const next = [x0, y0, w, h]
                root.roiEdited(next)
            }
            canvas.requestPaint()
        }

        onCanceled: {
            root._dragX0 = -1
            root._dragY0 = -1
            root._dragX1 = -1
            root._dragY1 = -1
            canvas.requestPaint()
        }
    }
}
