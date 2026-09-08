import QtQuick
import AquaMeasure

Item {
    id: root

    property int frameWidth: 0
    property int frameHeight: 0
    property var boxes: []
    property var trails: []
    property var behaviorMarkers: []
    property var epipolarYs: []
    property bool showTrails: true
    property bool showEpipolar: false
    property bool isLeft: true
    property int selectedBoxIndex: -1
    property int hoveredBoxIndex: -1
    property int editingBoxIndex: -1
    // Poignees de redimensionnement : affichees seulement quand
    // l'edition est armee, pas des la selection.
    property int handlesBoxIndex: -1
    property var editingBox: ({ "valid": false })
    // BBox de l'observation ouverte depuis le registre (Fish.focusBox).
    property var focusBox: null

    // Cible de marquage ponctuel : la bbox de la piste choisie pour poser des
    // bouchées, à l'image affichée. Elle ne vient PAS de `boxes` — à l'arrêt,
    // l'overlay publie les boîtes du détecteur, pas les échantillons de la
    // piste, et la piste travaillée n'aurait donc rien à cliquer.
    property var peckBox: null
    property color peckColor: "#f59e0b"
    property string peckSymbol: "●"
    property string peckLabel: ""
    // Un marqueur existe déjà sur cette image : le pictogramme est plein.
    property bool peckMarked: false

    // Transformation zoom/pan partagee avec MeasureStereoView.
    property real viewScale: 0
    property real contentOx: 0
    property real contentOy: 0

    signal boxTrackPicked(int trackId, int boxIndex)

    function _transform() {
        const iw = Math.max(1, frameWidth)
        const ih = Math.max(1, frameHeight)
        if (viewScale > 0) {
            return { s: viewScale, ox: contentOx, oy: contentOy, iw: iw, ih: ih }
        }
        const s = Math.min(width / iw, height / ih)
        const ox = (width - iw * s) / 2
        const oy = (height - ih * s) / 2
        return { s: s, ox: ox, oy: oy, iw: iw, ih: ih }
    }

    function _toDisp(xo, yo) {
        const t = _transform()
        return Qt.point(t.ox + xo * t.s, t.oy + yo * t.s)
    }

    function _toOrig(xd, yd) {
        const t = _transform()
        return Qt.point(
            Math.max(0, Math.min((xd - t.ox) / t.s, t.iw - 1)),
            Math.max(0, Math.min((yd - t.oy) / t.s, t.ih - 1)))
    }

    function _hitBox(mx, my) {
        if (!root.boxes || root.boxes.length === 0)
            return null
        const o = root._toOrig(mx, my)
        for (let bi = root.boxes.length - 1; bi >= 0; bi--) {
            const b = root.boxes[bi]
            if (o.x >= b.x1 && o.x <= b.x2 && o.y >= b.y1 && o.y <= b.y2) {
                const tid = b.trackId !== undefined && b.trackId >= 0 ? b.trackId : -1
                return { trackId: tid, boxIndex: bi }
            }
        }
        return null
    }

    // Coordonnées image d'un point de la vue : le marquage ponctuel doit
    // interroger le contrôleur dans le repère de la frame, pas en pixels
    // d'écran (zoom et pan changent le second, jamais le premier).
    function imagePointAt(mx, my) {
        return root._toOrig(mx, my)
    }

    function _hitPeckBox(mx, my) {
        const pb = root.peckBox
        if (!root.isLeft || !pb || pb.valid !== true)
            return false
        const o = root._toOrig(mx, my)
        return o.x >= pb.x1 && o.x <= pb.x2 && o.y >= pb.y1 && o.y <= pb.y2
    }

    function _paintBox(boxIndex, originalBox) {
        if (boxIndex === root.editingBoxIndex && root.editingBox
                && root.editingBox.valid === true)
            return root.editingBox
        return originalBox
    }

    function _trailColor(trackId) {
        const hues = [120, 200, 40, 280, 15, 170]
        const h = hues[Math.abs(trackId) % hues.length]
        return Qt.hsla(h / 360, 0.75, 0.55, 0.9)
    }

    function _paintBehaviorBadges(ctx, badges, p1, p2) {
        if (!badges || badges.length === 0)
            return
        const size = 20
        const gap = 3
        let x = p2.x + 5
        if (x + size > root.width)
            x = Math.max(0, p2.x - size - 5)
        ctx.save()
        ctx.textAlign = "center"
        ctx.textBaseline = "middle"
        ctx.font = "bold 13px " + Theme.canvasFontStack
        for (let i = 0; i < badges.length; i++) {
            const badge = badges[i]
            const y = Math.max(0, Math.min(root.height - size,
                p1.y + i * (size + gap)))
            ctx.fillStyle = "rgba(2, 20, 34, 0.90)"
            ctx.fillRect(x - 2, y - 2, size + 4, size + 4)
            ctx.beginPath()
            ctx.arc(x + size / 2, y + size / 2, size / 2, 0, Math.PI * 2)
            ctx.fillStyle = badge.color || "#f59e0b"
            ctx.fill()
            ctx.strokeStyle = "rgba(255, 255, 255, 0.85)"
            ctx.lineWidth = 1.5
            ctx.stroke()
            ctx.fillStyle = "#ffffff"
            ctx.fillText(badge.symbol || "●", x + size / 2, y + size / 2 + 0.5)
        }
        ctx.restore()
    }

    Canvas {
        id: canvas
        anchors.fill: parent
        onPaint: {
            const ctx = getContext("2d")
            ctx.clearRect(0, 0, width, height)
            const t = root._transform()

            if (root.showTrails && root.trails) {
                for (let ti = 0; ti < root.trails.length; ti++) {
                    const trail = root.trails[ti]
                    const pts = trail.points || []
                    if (pts.length < 2)
                        continue
                    const tid = trail.trackId !== undefined ? trail.trackId : ti
                    ctx.strokeStyle = root._trailColor(tid)
                    ctx.lineWidth = 2
                    ctx.beginPath()
                    for (let pi = 0; pi < pts.length; pi++) {
                        const d = root._toDisp(pts[pi].x, pts[pi].y)
                        if (pi === 0)
                            ctx.moveTo(d.x, d.y)
                        else
                            ctx.lineTo(d.x, d.y)
                    }
                    ctx.stroke()
                }
            }

            if (root.showEpipolar && root.epipolarYs) {
                for (let ei = 0; ei < root.epipolarYs.length; ei++) {
                    const y = root.epipolarYs[ei]
                    const d = root._toDisp(0, y)
                    ctx.strokeStyle = ei === 0 ? "#4ade80" : "#60c8ff"
                    ctx.lineWidth = 2
                    ctx.beginPath()
                    ctx.moveTo(t.ox, d.y)
                    ctx.lineTo(t.ox + t.iw * t.s, d.y)
                    ctx.stroke()
                }
            }

            if (root.boxes) {
                ctx.font = "bold 11px " + Theme.canvasFontStack
                for (let bi = 0; bi < root.boxes.length; bi++) {
                    const b = root.boxes[bi]
                    if (!root.isLeft && b.stereoProjected === false)
                        continue
                    const painted = root._paintBox(bi, b)
                    const p1 = root._toDisp(painted.x1, painted.y1)
                    const p2 = root._toDisp(painted.x2, painted.y2)
                    const grazing = b.manualGrazing === true
                    const manualDraw = b.manualDraw === true
                    const selected = bi === root.selectedBoxIndex
                    const hovered = bi === root.hoveredBoxIndex
                    if (root.isLeft && (selected || hovered)) {
                        ctx.fillStyle = selected
                            ? "rgba(192, 132, 252, 0.18)"
                            : "rgba(192, 132, 252, 0.10)"
                        ctx.fillRect(p1.x, p1.y, p2.x - p1.x, p2.y - p1.y)
                    }
                    if (manualDraw) {
                        ctx.strokeStyle = selected ? "#f0abfc"
                            : (hovered ? "#d8b4fe" : "#c084fc")
                    } else if (grazing) {
                        ctx.strokeStyle = "#f59e0b"
                    } else if (b.replayTrack === true) {
                        ctx.strokeStyle = "#38bdf8"
                    } else if (!root.isLeft && b.stereoProjected) {
                        ctx.strokeStyle = "#60c8ff"
                    } else {
                        ctx.strokeStyle = "#4ade80"
                    }
                    ctx.lineWidth = selected ? 3 : (hovered ? 3 : 2)
                    ctx.strokeRect(p1.x, p1.y, p2.x - p1.x, p2.y - p1.y)
                    const species = (b.speciesName || "").trim()
                    const cls = (b.clsName || "poisson").trim()
                    const taxon = species.length > 0 ? species : cls
                    const speciesScore = Number(b.speciesConf || 0)
                    const label = (manualDraw ? "manuel " : "")
                        + (grazing ? "broute " : "")
                        + (b.trackId >= 0 ? "#" + b.trackId + " " : "")
                        + taxon
                        + (b.identificationSource === "validated" ? " · identification validée" : "")
                        + (!b.identificationSource && species.length > 0 && speciesScore > 0
                            ? " · Fishial " + Math.round(speciesScore * 100) + "%"
                            : "")
                        + (b.conf > 0
                            ? " · détection " + Math.round(b.conf * 100) + "%"
                            : "")
                    // Bleu ciel : piste rejouée depuis la base, pas une
                    // détection calculée sur la frame affichée.
                    ctx.fillStyle = manualDraw ? "#e9d5ff"
                        : (grazing ? "#fbbf24"
                        : (b.replayTrack === true ? "#7dd3fc" : "#ffffff"))
                    ctx.fillText(label, p1.x + 2, p1.y - 4)
                    root._paintBehaviorBadges(ctx, b.behaviorBadges || [], p1, p2)
                }

                const handleIndex = root.handlesBoxIndex
                if (root.isLeft && handleIndex >= 0 && handleIndex < root.boxes.length) {
                    const original = root.boxes[handleIndex]
                    if (original.stereoProjected !== true) {
                        const hb = root._paintBox(handleIndex, original)
                        const hp1 = root._toDisp(hb.x1, hb.y1)
                        const hp2 = root._toDisp(hb.x2, hb.y2)
                        const hcx = (hp1.x + hp2.x) / 2
                        const hcy = (hp1.y + hp2.y) / 2
                        const handles = [
                            [hp1.x, hp1.y], [hcx, hp1.y], [hp2.x, hp1.y],
                            [hp2.x, hcy], [hp2.x, hp2.y], [hcx, hp2.y],
                            [hp1.x, hp2.y], [hp1.x, hcy]
                        ]
                        ctx.fillStyle = "#ffffff"
                        ctx.strokeStyle = "#9333ea"
                        ctx.lineWidth = 2
                        for (let hi = 0; hi < handles.length; hi++) {
                            const hx = handles[hi][0]
                            const hy = handles[hi][1]
                            ctx.fillRect(hx - 4, hy - 4, 8, 8)
                            ctx.strokeRect(hx - 4, hy - 4, 8, 8)
                        }
                    }
                }
            }

            if (root.isLeft && root.behaviorMarkers) {
                for (let mi = 0; mi < root.behaviorMarkers.length; mi++) {
                    const marker = root.behaviorMarkers[mi]
                    const mp1 = root._toDisp(marker.x1, marker.y1)
                    const mp2 = root._toDisp(marker.x2, marker.y2)
                    const badges = marker.behaviorBadges || []
                    const color = badges.length > 0
                        ? (badges[0].color || "#f59e0b") : "#f59e0b"
                    ctx.strokeStyle = color
                    ctx.lineWidth = 2
                    ctx.setLineDash([5, 4])
                    ctx.strokeRect(mp1.x, mp1.y, mp2.x - mp1.x, mp2.y - mp1.y)
                    ctx.setLineDash([])
                    root._paintBehaviorBadges(ctx, badges, mp1, mp2)
                }
            }

            const pb = root.peckBox
            if (root.isLeft && pb && pb.valid === true) {
                const q1 = root._toDisp(pb.x1, pb.y1)
                const q2 = root._toDisp(pb.x2, pb.y2)
                const qw = q2.x - q1.x
                const qh = q2.y - q1.y

                ctx.strokeStyle = root.peckColor
                ctx.lineWidth = 2
                // Pointillé serré quand la position est interpolée entre deux
                // échantillons : la cible reste cliquable, mais on ne fait pas
                // passer une position déduite pour une position suivie.
                ctx.setLineDash(pb.interpolated === true ? [3, 3] : [8, 4])
                ctx.strokeRect(q1.x, q1.y, qw, qh)
                ctx.setLineDash([])

                if (root.peckMarked) {
                const badge = 22
                const bxc = q2.x - badge / 2 - 2
                const byc = q1.y + badge / 2 + 2
                ctx.beginPath()
                ctx.arc(bxc, byc, badge / 2, 0, Math.PI * 2)
                ctx.fillStyle = root.peckMarked
                    ? root.peckColor : "rgba(2, 20, 34, 0.82)"
                ctx.fill()
                ctx.strokeStyle = root.peckColor
                ctx.lineWidth = 1.5
                ctx.stroke()
                ctx.save()
                ctx.textAlign = "center"
                ctx.textBaseline = "middle"
                ctx.font = "bold 13px " + Theme.canvasFontStack
                ctx.fillStyle = root.peckMarked ? "#0b1015" : root.peckColor
                ctx.fillText(root.peckSymbol || "●", bxc, byc + 0.5)
                ctx.restore()

                }

                const peckText = (root.peckLabel || "").trim()
                if (peckText.length > 0) {
                    ctx.font = "bold 12px " + Theme.canvasFontStack
                    const pw = ctx.measureText(peckText).width
                    const px = q1.x
                    const py = Math.min(root.height - 18, q2.y + 3)
                    ctx.fillStyle = "rgba(2, 20, 34, 0.88)"
                    ctx.fillRect(px, py, pw + 12, 18)
                    ctx.strokeStyle = root.peckColor
                    ctx.lineWidth = 1
                    ctx.strokeRect(px, py, pw + 12, 18)
                    ctx.fillStyle = root.peckColor
                    ctx.fillText(peckText, px + 6, py + 13)
                }
            }

            const fb = root.focusBox
            if (root.isLeft && fb && fb.valid === true) {
                const f1 = root._toDisp(fb.x1, fb.y1)
                const f2 = root._toDisp(fb.x2, fb.y2)
                const fw = f2.x - f1.x
                const fh = f2.y - f1.y

                ctx.fillStyle = "rgba(56, 189, 248, 0.16)"
                ctx.fillRect(f1.x, f1.y, fw, fh)

                ctx.strokeStyle = "#38bdf8"
                ctx.lineWidth = 3
                ctx.setLineDash([7, 4])
                ctx.strokeRect(f1.x, f1.y, fw, fh)
                ctx.setLineDash([])

                // Equerres de coin : la bbox reste lisible sur un fond charge.
                const arm = Math.max(8, Math.min(20, Math.min(fw, fh) * 0.28))
                ctx.strokeStyle = "#e0f2fe"
                ctx.lineWidth = 3
                ctx.beginPath()
                ctx.moveTo(f1.x, f1.y + arm); ctx.lineTo(f1.x, f1.y); ctx.lineTo(f1.x + arm, f1.y)
                ctx.moveTo(f2.x - arm, f1.y); ctx.lineTo(f2.x, f1.y); ctx.lineTo(f2.x, f1.y + arm)
                ctx.moveTo(f1.x, f2.y - arm); ctx.lineTo(f1.x, f2.y); ctx.lineTo(f1.x + arm, f2.y)
                ctx.moveTo(f2.x - arm, f2.y); ctx.lineTo(f2.x, f2.y); ctx.lineTo(f2.x, f2.y - arm)
                ctx.stroke()

                const focusLabel = (fb.label || qsTr("Poisson sélectionné")).trim()
                ctx.font = "bold 12px " + Theme.canvasFontStack
                const tw = ctx.measureText(focusLabel).width
                const bx = f1.x
                const by = Math.max(0, f1.y - 21)
                ctx.fillStyle = "rgba(2, 20, 34, 0.88)"
                ctx.fillRect(bx, by, tw + 12, 18)
                ctx.strokeStyle = "#38bdf8"
                ctx.lineWidth = 1
                ctx.strokeRect(bx, by, tw + 12, 18)
                ctx.fillStyle = "#7dd3fc"
                ctx.fillText(focusLabel, bx + 6, by + 13)
            }
        }
    }

    Connections {
        target: root
        function onBoxesChanged() { canvas.requestPaint() }
        function onTrailsChanged() { canvas.requestPaint() }
        function onBehaviorMarkersChanged() { canvas.requestPaint() }
        function onEpipolarYsChanged() { canvas.requestPaint() }
        function onShowTrailsChanged() { canvas.requestPaint() }
        function onShowEpipolarChanged() { canvas.requestPaint() }
        function onFrameWidthChanged() { canvas.requestPaint() }
        function onWidthChanged() { canvas.requestPaint() }
        function onHeightChanged() { canvas.requestPaint() }
        function onSelectedBoxIndexChanged() { canvas.requestPaint() }
        function onHoveredBoxIndexChanged() { canvas.requestPaint() }
        function onEditingBoxIndexChanged() { canvas.requestPaint() }
        function onHandlesBoxIndexChanged() { canvas.requestPaint() }
        function onEditingBoxChanged() { canvas.requestPaint() }
        function onFocusBoxChanged() { canvas.requestPaint() }
        function onPeckBoxChanged() { canvas.requestPaint() }
        function onPeckMarkedChanged() { canvas.requestPaint() }
        function onPeckColorChanged() { canvas.requestPaint() }
        function onPeckSymbolChanged() { canvas.requestPaint() }
        function onPeckLabelChanged() { canvas.requestPaint() }
        function onViewScaleChanged() { canvas.requestPaint() }
        function onContentOxChanged() { canvas.requestPaint() }
        function onContentOyChanged() { canvas.requestPaint() }
    }

    Connections {
        target: typeof Fish !== "undefined" ? Fish : null
        function onOverlayChanged() { canvas.requestPaint() }
    }
}
