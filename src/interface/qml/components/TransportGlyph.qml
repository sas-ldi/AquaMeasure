import QtQuick

// Icônes vectorielles pour les contrôles vidéo (play, pause, pas à pas…).
Item {
    id: root

    enum Kind { Rewind, StepBack, Play, Pause, StepForward, FastForward }

    property int kind: TransportGlyph.Play
    property color glyphColor: "#e2e8f0"

    implicitWidth: 12
    implicitHeight: 12

    Canvas {
        id: canvas
        anchors.fill: parent

        onPaint: {
            const ctx = getContext("2d")
            ctx.reset()
            ctx.clearRect(0, 0, width, height)
            ctx.fillStyle = root.glyphColor

            const w = width
            const h = height

            function fillTriangle(x1, y1, x2, y2, x3, y3) {
                ctx.beginPath()
                ctx.moveTo(x1, y1)
                ctx.lineTo(x2, y2)
                ctx.lineTo(x3, y3)
                ctx.closePath()
                ctx.fill()
            }

            switch (root.kind) {
            case TransportGlyph.Play:
                fillTriangle(w * 0.28, h * 0.18, w * 0.78, h * 0.5, w * 0.28, h * 0.82)
                break
            case TransportGlyph.Pause: {
                const barW = w * 0.24
                const gap = w * 0.1
                const left = (w - 2 * barW - gap) / 2
                ctx.fillRect(left, h * 0.2, barW, h * 0.6)
                ctx.fillRect(left + barW + gap, h * 0.2, barW, h * 0.6)
                break
            }
            case TransportGlyph.StepBack:
                fillTriangle(w * 0.72, h * 0.18, w * 0.34, h * 0.5, w * 0.72, h * 0.82)
                break
            case TransportGlyph.StepForward:
                fillTriangle(w * 0.28, h * 0.18, w * 0.66, h * 0.5, w * 0.28, h * 0.82)
                break
            // Double chevron : les deux triangles couvrent la meme largeur a
            // gauche et a droite, sinon l'icone parait decalee dans son bouton.
            case TransportGlyph.Rewind:
                fillTriangle(w * 0.50, h * 0.18, w * 0.08, h * 0.5, w * 0.50, h * 0.82)
                fillTriangle(w * 0.92, h * 0.18, w * 0.50, h * 0.5, w * 0.92, h * 0.82)
                break
            case TransportGlyph.FastForward:
                fillTriangle(w * 0.08, h * 0.18, w * 0.50, h * 0.5, w * 0.08, h * 0.82)
                fillTriangle(w * 0.50, h * 0.18, w * 0.92, h * 0.5, w * 0.50, h * 0.82)
                break
            }
        }
    }

    onKindChanged: canvas.requestPaint()
    onGlyphColorChanged: canvas.requestPaint()
    onWidthChanged: canvas.requestPaint()
    onHeightChanged: canvas.requestPaint()
    Component.onCompleted: canvas.requestPaint()
}
