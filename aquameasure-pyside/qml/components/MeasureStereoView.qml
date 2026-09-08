import QtQuick
import QtQuick.Controls
import AquaMeasure

Rectangle {
    id: root

    required property string sideLabel
    required property bool isLeft
    required property string videoPath
    required property int videoFrameCount
    required property int frameWidth
    required property int frameHeight
    required property double fps
    required property int absFrame
    property point pointA: Qt.point(-1, -1)
    property point pointB: Qt.point(-1, -1)
    // Les memes poignees, dans l'espace de l'image REELLEMENT affichee.
    // A l'arret l'image est rectifiee et ces points valent pointA / pointB au
    // pixel pres ; pendant la lecture l'image est brute, et sans ce recalage
    // les deux ronds restaient a cote du poisson. Le pointage, lui, continue
    // de travailler sur pointA / pointB : c'est l'espace de la mesure.
    property point pointADisplay: pointA
    property point pointBDisplay: pointB
    required property bool placementEnabled
    required property bool interactionEnabled
    property bool rectifiedReady: false
    property int previewTick: 0
    property bool playing: false
    property bool showOverlays: true

    signal pointPlaced(double x, double y)
    signal pointMoved(int index, double x, double y)
    signal pointDeleteRequested(int index)
    signal pointDragFinished()
    signal fullscreenRequested(string caption, string imageSource)

    readonly property string fullscreenCaption: sideLabel + (showRectified ? qsTr(" (rectifiée)") : "")
    readonly property string fullscreenImageSource: showRectified
        ? ("image://frames/measure_" + (isLeft ? "left" : "right") + "?" + previewTick)
        : ""

    color: "#000000"
    radius: Theme.radiusSm
    clip: true

    onAbsFrameChanged: {
        // Pendant la lecture, le nettoyage a deja ete fait au demarrage du
        // play : le refaire a chaque notification de position du lecteur natif
        // rejouait quatre appels Python par vue, pour rien.
        if (root.playing)
            return
        root._finishManualEdit(false)
        root._manualEditArmed = -1
        root._hoveredManualIndex = -1
        root._clearBoxSelection()
        root._selectedPointIndex = -1
    }
    onPlayingChanged: {
        if (playing) {
            root._finishManualEdit(false)
            root._hoveredManualIndex = -1
            root._clearBoxSelection()
            root._selectedPointIndex = -1
        }
    }

    readonly property bool _hasA: pointA.x >= 0 && pointA.y >= 0
    readonly property bool _hasB: pointB.x >= 0 && pointB.y >= 0
    readonly property real _hitDist: 18
    readonly property string _frameImageSource: root.showRectified
        ? ("image://frames/measure_" + (isLeft ? "left" : "right") + "?" + previewTick)
        : ""

    readonly property bool showRectified: root.rectifiedReady && !root.playing

    readonly property real zoomFactor: 4.0
    readonly property real loupeWidth: Math.min(240, width * 0.45)
    readonly property real loupeHeight: Math.min(180, height * 0.45)

    // Zoom molette (gauche + droite) : purement visuel, partage par l'image,
    // les bbox, marqueurs A/B et lignes epipolaires.
    readonly property real minViewZoom: 1.0
    readonly property real maxViewZoom: 8.0
    property real viewZoom: 1.0
    property real panX: 0
    property real panY: 0
    readonly property real _fitScale: Math.min(
        width / Math.max(1, frameWidth), height / Math.max(1, frameHeight))
    readonly property real _viewScale: _fitScale * viewZoom
    readonly property real _contentOx: (width - Math.max(1, frameWidth) * _viewScale) / 2 + panX
    readonly property real _contentOy: (height - Math.max(1, frameHeight) * _viewScale) / 2 + panY

    function clampViewZoom(z) {
        return Math.max(minViewZoom, Math.min(maxViewZoom, z))
    }

    function clampPan() {
        if (viewZoom <= minViewZoom + 1e-4) {
            panX = 0
            panY = 0
            return
        }
        const mx = Math.max(0, (frameImg.width - width) / 2)
        const my = Math.max(0, (frameImg.height - height) / 2)
        panX = Math.max(-mx, Math.min(mx, panX))
        panY = Math.max(-my, Math.min(my, panY))
    }

    function zoomAt(factor, vx, vy) {
        const nz = clampViewZoom(viewZoom * factor)
        const realFactor = viewZoom > 0 ? nz / viewZoom : 1
        if (Math.abs(realFactor - 1) < 1e-4)
            return
        const w = frameImg.width
        const h = frameImg.height
        const x0 = (width - w) / 2 + panX
        const y0 = (height - h) / 2 + panY
        const fx = w > 0 ? (vx - x0) / w : 0.5
        const fy = h > 0 ? (vy - y0) / h : 0.5
        const wn = w * realFactor
        const hn = h * realFactor
        const x0n = vx - fx * wn
        const y0n = vy - fy * hn
        viewZoom = nz
        panX = x0n - (width - wn) / 2
        panY = y0n - (height - hn) / 2
        clampPan()
    }

    function resetViewZoom() {
        viewZoom = 1.0
        panX = 0
        panY = 0
    }

    function imageMapping(mx, my) {
        const iw = Math.max(1, frameWidth)
        const ih = Math.max(1, frameHeight)
        if (iw <= 0 || ih <= 0)
            return { valid: false, ix: 0, iy: 0, iw: iw, ih: ih }
        const scale = _viewScale
        const ox = _contentOx
        const oy = _contentOy
        const dw = iw * scale
        const dh = ih * scale
        const ix = (mx - ox) / scale
        const iy = (my - oy) / scale
        const inside = mx >= ox && mx <= ox + dw && my >= oy && my <= oy + dh
        return { valid: inside, ix: ix, iy: iy, iw: iw, ih: ih }
    }

    Image {
        id: frameImg
        visible: root.showRectified
        fillMode: Image.Stretch
        source: root._frameImageSource
        cache: false
        asynchronous: false
        x: root._contentOx
        y: root._contentOy
        width: Math.max(1, frameWidth) * root._viewScale
        height: Math.max(1, frameHeight) * root._viewScale

        onWidthChanged: root.clampPan()
        onHeightChanged: root.clampPan()
    }

    SyncVideoPlayer {
        id: player
        objectName: "stereoVideoPlayer"
        // Le lecteur brut partage le zoom et le panoramique de l'image
        // rectifiee et des overlays, y compris au passage pause / lecture.
        x: root._contentOx
        y: root._contentOy
        width: Math.max(1, frameWidth) * root._viewScale
        height: Math.max(1, frameHeight) * root._viewScale
        visible: !root.showRectified
        videoPath: root.videoPath
        fps: root.fps
        frameCount: Math.max(1, root.videoFrameCount)
        inFrame: 0
        outFrame: Math.max(0, root.videoFrameCount - 1)
        playing: root.playing

        onFrameSyncRequested: function(f) {
            if (root.isLeft)
                Measure.seekFromLeftAbsFrame(f)
        }

        onPlayheadChanged: function(f) {
            if (root.playing && root.isLeft)
                Measure.seekFromLeftAbsFrame(f)
        }
    }

    // `player.frame` n'est pilote qu'a l'arret. Pendant la lecture, absFrame
    // suit le playhead du lecteur gauche : le reinjecter dans les lecteurs
    // declenchait un pause/seek/play par notification de position
    // (SyncVideoPlayer.applySeek), et faisait saccader la vue droite qui,
    // elle, decode a son propre rythme. La page Synchronisation ne pousse
    // rien pendant le play - c'est pour cela qu'elle reste fluide.
    Binding {
        target: player
        property: "frame"
        value: root.absFrame
        when: !root.playing
        restoreMode: Binding.RestoreNone
    }

    VideoOverlayCanvas {
        id: overlay
        objectName: "videoOverlayCanvas"
        z: 7
        anchors.fill: parent
        frameWidth: root.frameWidth
        frameHeight: root.frameHeight
        boxes: root.isLeft ? Fish.overlayBoxes : Fish.overlayBoxesRight
        trails: root.isLeft && Fish.showTrails ? Fish.overlayTrails : []
        behaviorMarkers: root.isLeft ? Fish.overlayBehaviorMarkers : []
        epipolarYs: Measure.epipolarLinesY
        showTrails: Fish.showTrails && root.isLeft
        // Une epipolaire est une horizontale DANS l'image rectifiee. Sur
        // l'image brute, la distorsion la courbe : la tracer droite mentirait
        // sur la geometrie. Tant que la vue montre le brut, on ne la trace
        // pas plutot que de la tracer faux.
        showEpipolar: Measure.epipolar && !Measure.overlayRemapActive
        isLeft: root.isLeft
        viewScale: root._viewScale
        contentOx: root._contentOx
        contentOy: root._contentOy
        selectedBoxIndex: root._rightSelectedBoxIndex
        hoveredBoxIndex: root._hoveredManualIndex
        editingBoxIndex: root._editingManualIndex
        handlesBoxIndex: root._manualEditActive
            ? (root._editingManualIndex >= 0
                ? root._editingManualIndex : root._selectedManualIndex)
            : -1
        editingBox: root._manualEditBox
        focusBox: (root.isLeft && typeof Fish !== "undefined") ? Fish.focusBox : null
        // Cible de marquage ponctuel. `Pecks` n'existe pas dans les harnais de
        // test qui montent cette vue seule : le garde est obligatoire.
        peckBox: (root.isLeft && typeof Pecks !== "undefined" && Pecks.armed)
            ? Pecks.trackBox : null
        peckColor: typeof Pecks !== "undefined" ? Pecks.typeColor : "#f59e0b"
        peckSymbol: typeof Pecks !== "undefined" ? Pecks.typeSymbol : "●"
        peckMarked: typeof Pecks !== "undefined" && Pecks.currentFrameMarked
        peckLabel: (typeof Pecks !== "undefined" && Pecks.armed)
            ? Pecks.selectedTrackLabel + (Pecks.currentFrameMarked ? " · " + Pecks.typeLabel : "") : ""
        visible: root.showOverlays && root.frameWidth > 0

        onBoxTrackPicked: function(trackId, boxIndex) {
            if (typeof Fish !== "undefined")
                Fish.selectTrackFromBox(trackId, boxIndex)
            if (typeof Data !== "undefined") {
                Data.setSelectedTrackFromId(trackId)
                Data.selectBoxIndex(boxIndex)
            }
        }
    }

    Column {
        anchors.left: parent.left
        anchors.top: parent.top
        anchors.margins: 8
        spacing: 4
        z: 2

        Rectangle {
            width: camBadge.implicitWidth + 12
            height: camBadge.implicitHeight + 8
            radius: Theme.radiusSm
            color: Qt.rgba(0.01, 0.03, 0.05, 0.72)
            Text {
                id: camBadge
                anchors.centerIn: parent
                text: sideLabel + (root.showRectified ? qsTr(" rect.") : "")
                font.family: Theme.monoFamily
                font.pixelSize: 10
                font.weight: Font.DemiBold
                color: isLeft ? "#fca5a5" : "#93c5fd"
            }
        }

        Rectangle {
            width: metaBadge.implicitWidth + 12
            height: metaBadge.implicitHeight + 8
            radius: Theme.radiusSm
            color: Qt.rgba(0.01, 0.03, 0.05, 0.72)
            Text {
                id: metaBadge
                anchors.centerIn: parent
                text: qsTr("%1 img/s · f %2")
                    .arg(fps > 0 ? fps.toFixed(2) : "-")
                    .arg(absFrame)
                font.family: Theme.monoFamily
                font.pixelSize: 10
                color: "#cbd5e1"
            }
        }
    }

    Item {
        anchors.fill: parent
        z: 4
        visible: frameWidth > 0 && frameHeight > 0

        Canvas {
            id: markers
            anchors.fill: parent
            onPaint: {
                const ctx = getContext("2d")
                ctx.clearRect(0, 0, width, height)
                const s = root._viewScale
                const ox = root._contentOx
                const oy = root._contentOy
                function toDisp(xo, yo) {
                    return Qt.point(ox + xo * s, oy + yo * s)
                }
                const col = isLeft ? "#dc2626" : "#2563eb"
                function drawHandle(pt, label, active) {
                    const d = toDisp(pt.x, pt.y)
                    const r = active ? 10 : 7
                    ctx.fillStyle = col
                    ctx.strokeStyle = active ? "#facc15" : "#ffffff"
                    ctx.lineWidth = active ? 3 : 1.5
                    ctx.beginPath()
                    ctx.arc(d.x, d.y, r, 0, 2 * Math.PI)
                    ctx.fill()
                    ctx.stroke()
                    ctx.fillStyle = "#ffffff"
                    ctx.font = "bold 11px " + Theme.canvasFontStack
                    ctx.fillText(label, d.x + 10, d.y + 4)
                }
                if (root._hasA)
                    drawHandle(root.pointADisplay, "A", root._selectedPointIndex === 0)
                if (root._hasB)
                    drawHandle(root.pointBDisplay, "B", root._selectedPointIndex === 1)
                if (root._hasA && root._hasB) {
                    const da = toDisp(root.pointADisplay.x, root.pointADisplay.y)
                    const db = toDisp(root.pointBDisplay.x, root.pointBDisplay.y)
                    ctx.strokeStyle = col
                    ctx.lineWidth = 2
                    ctx.beginPath()
                    ctx.moveTo(da.x, da.y)
                    ctx.lineTo(db.x, db.y)
                    ctx.stroke()
                }
            }
        }

        Connections {
            target: root
            function onPointAChanged() {
                root._normalizePointSelection()
                markers.requestPaint()
            }
            function onPointBChanged() {
                root._normalizePointSelection()
                markers.requestPaint()
            }
            function onPointADisplayChanged() { markers.requestPaint() }
            function onPointBDisplayChanged() { markers.requestPaint() }
            function onFrameWidthChanged() { markers.requestPaint() }
            function onWidthChanged() { markers.requestPaint() }
            function onHeightChanged() { markers.requestPaint() }
            function onViewZoomChanged() { markers.requestPaint() }
            function onPanXChanged() { markers.requestPaint() }
            function onPanYChanged() { markers.requestPaint() }
            function on_SelectedPointIndexChanged() { markers.requestPaint() }
        }
    }

    property int _dragIndex: -1
    property int _selectedPointIndex: -1
    property int _hoverPointIndex: -1
    property int _rightSelectedBoxIndex: -1
    property real _pressX: 0
    property real _pressY: 0
    property bool _drawingManual: false
    property bool _pendingPointPlace: false
    property bool _resumeBoxGesture: false
    property int _resumeBoxIndex: -1
    property real _manualIx1: 0
    property real _manualIy1: 0
    property real _manualIx2: 0
    property real _manualIy2: 0
    property int _hoveredManualIndex: -1
    property int _editingManualIndex: -1
    // Selectionner une bbox manuelle la rendait immediatement
    // deplacable : le moindre clic gauche dessus declenchait une
    // modification, laquelle vide la selection (_invalidate_box_selection)
    // - et « Mesurer » n'avait alors plus de cible. L'edition doit
    // desormais etre armee explicitement par le crayon.
    property int _manualEditArmed: -1
    // Glisser sur la bbox selectionnee mais non armee : on ne deplace pas
    // (edition non armee) et on ne trace surtout pas une nouvelle bbox
    // par-dessus. Le glisser est simplement ignore.
    property bool _blockManualDraw: false
    property string _manualEditHandle: ""
    property real _manualEditStartX: 0
    property real _manualEditStartY: 0
    property bool _manualEditChanged: false
    property var _manualEditOrigin: ({ "valid": false })
    property var _manualEditBox: ({ "valid": false })
    readonly property int _selectedManualIndex: root._isEditableBoxIndex(
        root._rightSelectedBoxIndex) ? root._rightSelectedBoxIndex : -1

    readonly property bool _manualEditActive:
        root._selectedManualIndex >= 0
        && root._manualEditArmed === root._selectedManualIndex

    function _boxAt(index) {
        if (!overlay.boxes || index < 0 || index >= overlay.boxes.length)
            return null
        return overlay.boxes[index]
    }

    function _normalizePointSelection() {
        if ((root._selectedPointIndex === 0 && !root._hasA)
                || (root._selectedPointIndex === 1 && !root._hasB))
            root._selectedPointIndex = -1
    }

    // Au zoom, un pixel image vaut plusieurs pixels ecran : la souris ne
    // permet plus de viser le pixel voulu. Les fleches deplacent le point
    // selectionne d'un pixel image, dix avec Maj.
    readonly property int _nudgeStep: 1
    readonly property int _nudgeStepFast: 10

    // Chaque appui vaut un glisser complet ; recalculer la mesure a chaque
    // fois (et journaliser le calcul) noierait la console des que la touche
    // est maintenue. On attend donc la fin de la rafale.
    Timer {
        id: nudgeCommit
        interval: 250
        onTriggered: root.pointDragFinished()
    }

    function _nudgeSelectedPoint(dx, dy) {
        const index = root._selectedPointIndex
        if (index < 0 || !root.interactionEnabled || root.playing)
            return false
        const pt = index === 0 ? root.pointA : root.pointB
        if (pt.x < 0 || pt.y < 0)
            return false
        const maxX = Math.max(1, root.frameWidth) - 1
        const maxY = Math.max(1, root.frameHeight) - 1
        const x = Math.max(0, Math.min(pt.x + dx, maxX))
        const y = Math.max(0, Math.min(pt.y + dy, maxY))
        // Le point colle deja au bord : la touche reste avalee, sinon buter
        // sur le cadre ferait defiler les images par surprise.
        if (x !== pt.x || y !== pt.y) {
            root.pointMoved(index, x, y)
            markers.requestPaint()
            nudgeCommit.restart()
        }
        return true
    }

    function _isEditableBoxIndex(index) {
        const box = root._boxAt(index)
        return box !== null && box.stereoProjected !== true
    }

    function _manualHit(mx, my) {
        const hit = overlay._hitBox(mx, my)
        if (!hit || !root._isEditableBoxIndex(hit.boxIndex))
            return null
        return hit
    }

    function _selectBoxByRightClick(index) {
        const box = root._boxAt(index)
        if (!box)
            return
        root._finishManualEdit(false)
        // Le clic droit selectionne, comme sur n'importe quelle bbox :
        // il n'arme jamais l'edition.
        root._manualEditArmed = -1
        root._rightSelectedBoxIndex = index
        root._selectedPointIndex = -1
        if (typeof Fish !== "undefined")
            Fish.selectTrackFromBox(
                box.trackId !== undefined ? box.trackId : -1, index)
        if (typeof Data !== "undefined") {
            Data.setSelectedTrackFromId(
                box.trackId !== undefined ? box.trackId : -1)
            // selectBoxExplicit ouvre la fiche du poisson et pose lui-même un
            // statut qui décrit la taxonomie préremplie. Le remplacer par un
            // rappel d'édition de bbox cacherait justement ce qui vient
            // d'arriver dans le panneau de droite.
            Data.selectBoxExplicit(index)
            // Ce que l'on peut faire du cadre reste utile à dire, mais en
            // complément du statut de la fiche, jamais à sa place.
            Data.appendStatusText(qsTr("crayon : déplacer ou redimensionner · × : supprimer le cadre"))
        }
    }

    function _clearBoxSelection() {
        root._finishManualEdit(false)
        root._manualEditArmed = -1
        root._rightSelectedBoxIndex = -1
        if (typeof Fish !== "undefined")
            Fish.setSelectedFishIndex(-1)
        if (typeof Data !== "undefined") {
            Data.setSelectedTrackFromId(-1)
            Data.selectBoxIndex(-1)
        }
    }

    function _handlePoints(box) {
        if (!box)
            return []
        const p1 = overlay._toDisp(box.x1, box.y1)
        const p2 = overlay._toDisp(box.x2, box.y2)
        const cx = (p1.x + p2.x) / 2
        const cy = (p1.y + p2.y) / 2
        return [
            { name: "nw", x: p1.x, y: p1.y },
            { name: "n",  x: cx,   y: p1.y },
            { name: "ne", x: p2.x, y: p1.y },
            { name: "e",  x: p2.x, y: cy },
            { name: "se", x: p2.x, y: p2.y },
            { name: "s",  x: cx,   y: p2.y },
            { name: "sw", x: p1.x, y: p2.y },
            { name: "w",  x: p1.x, y: cy }
        ]
    }

    function _manualHandleHit(mx, my, index) {
        if (index !== root._selectedManualIndex || !root._manualEditActive)
            return ""
        const points = root._handlePoints(root._boxAt(index))
        const hitRadius = 11
        for (let i = 0; i < points.length; i++) {
            const dx = points[i].x - mx
            const dy = points[i].y - my
            if (Math.sqrt(dx * dx + dy * dy) <= hitRadius)
                return points[i].name
        }
        return ""
    }

    function _beginManualEdit(index, handle, mx, my) {
        const box = root._boxAt(index)
        if (!box)
            return
        const p = pointer.imageCoords(mx, my)
        root._editingManualIndex = index
        root._manualEditHandle = handle.length > 0 ? handle : "move"
        root._manualEditStartX = p.x
        root._manualEditStartY = p.y
        root._manualEditChanged = false
        root._manualEditOrigin = {
            valid: true, x1: box.x1, y1: box.y1, x2: box.x2, y2: box.y2
        }
        root._manualEditBox = {
            valid: true, x1: box.x1, y1: box.y1, x2: box.x2, y2: box.y2
        }
    }

    function _updateManualEdit(mx, my) {
        if (root._editingManualIndex < 0 || !root._manualEditOrigin.valid)
            return
        const p = pointer.imageCoords(mx, my)
        const o = root._manualEditOrigin
        let x1 = o.x1
        let y1 = o.y1
        let x2 = o.x2
        let y2 = o.y2
        const handle = root._manualEditHandle
        const minSize = 6
        if (handle === "move") {
            let dx = p.x - root._manualEditStartX
            let dy = p.y - root._manualEditStartY
            dx = Math.max(-o.x1, Math.min(root.frameWidth - o.x2, dx))
            dy = Math.max(-o.y1, Math.min(root.frameHeight - o.y2, dy))
            x1 += dx; x2 += dx
            y1 += dy; y2 += dy
        } else {
            if (handle.indexOf("w") >= 0)
                x1 = Math.min(p.x, o.x2 - minSize)
            if (handle.indexOf("e") >= 0)
                x2 = Math.max(p.x, o.x1 + minSize)
            if (handle.indexOf("n") >= 0)
                y1 = Math.min(p.y, o.y2 - minSize)
            if (handle.indexOf("s") >= 0)
                y2 = Math.max(p.y, o.y1 + minSize)
        }
        root._manualEditBox = {
            valid: true, x1: x1, y1: y1, x2: x2, y2: y2
        }
        root._manualEditChanged = Math.abs(x1 - o.x1) > 0.01
            || Math.abs(y1 - o.y1) > 0.01
            || Math.abs(x2 - o.x2) > 0.01
            || Math.abs(y2 - o.y2) > 0.01
    }

    function _finishManualEdit(commitChanges) {
        const index = root._editingManualIndex
        const box = root._manualEditBox
        const changed = root._manualEditChanged
        root._editingManualIndex = -1
        root._manualEditHandle = ""
        root._manualEditOrigin = ({ "valid": false })
        root._manualEditBox = ({ "valid": false })
        root._manualEditChanged = false
        if (!commitChanges || !changed || index < 0 || !box.valid
                || typeof Fish === "undefined")
            return
        if (Fish.updateBox(index, box.x1, box.y1, box.x2, box.y2)
                && typeof Data !== "undefined")
            Data.selectBoxIndex(index)
    }

    function _deleteSelectedManualBox() {
        const index = root._selectedManualIndex
        if (index < 0 || typeof Fish === "undefined")
            return
        root._finishManualEdit(false)
        root._manualEditArmed = -1
        if (Fish.removeBox(index)) {
            root._hoveredManualIndex = -1
            root._rightSelectedBoxIndex = -1
            if (typeof Data !== "undefined")
                Data.selectBoxIndex(-1)
        }
    }

    function _cursorForHandle(handle) {
        if (handle === "n" || handle === "s")
            return Qt.SizeVerCursor
        if (handle === "e" || handle === "w")
            return Qt.SizeHorCursor
        if (handle === "nw" || handle === "se")
            return Qt.SizeFDiagCursor
        if (handle === "ne" || handle === "sw")
            return Qt.SizeBDiagCursor
        return Qt.SizeAllCursor
    }

    function _interactionCursor(mx, my) {
        if (!pointer.enabled || !root.interactionEnabled)
            return Qt.ArrowCursor
        if (root.isLeft && !root.playing && typeof Fish !== "undefined"
                && Fish.assistWaiting && !Fish.busy && overlay._hitBox(mx, my))
            return Qt.PointingHandCursor
        if (root._dragIndex >= 0)
            return Qt.ClosedHandCursor
        if (pointer.findHandleHit(mx, my) >= 0)
            return Qt.OpenHandCursor
        if (root._editingManualIndex >= 0)
            return root._cursorForHandle(root._manualEditHandle)
        const handle = root._manualHandleHit(mx, my, root._selectedManualIndex)
        if (handle.length > 0)
            return root._cursorForHandle(handle)
        const hit = root._manualHit(mx, my)
        if (root._manualEditActive && hit
                && hit.boxIndex === root._selectedManualIndex)
            return Qt.SizeAllCursor
        return Qt.CrossCursor
    }

    function _manualActionRect() {
        let box = null
        if (root._editingManualIndex >= 0 && root._manualEditBox.valid)
            box = root._manualEditBox
        else
            box = root._boxAt(root._selectedManualIndex)
        if (!box)
            return { valid: false, x: 0, y: 0, width: 0, height: 0 }
        const r = root.displayRectFromImage(box.x1, box.y1, box.x2, box.y2)
        r.valid = true
        return r
    }

    function displayRectFromImage(ix1, iy1, ix2, iy2) {
        const s = _viewScale
        const ox = _contentOx
        const oy = _contentOy
        const x1 = Math.min(ix1, ix2)
        const y1 = Math.min(iy1, iy2)
        const x2 = Math.max(ix1, ix2)
        const y2 = Math.max(iy1, iy2)
        return {
            x: ox + x1 * s,
            y: oy + y1 * s,
            width: Math.max(1, (x2 - x1) * s),
            height: Math.max(1, (y2 - y1) * s)
        }
    }

    Rectangle {
        z: 5
        visible: root._drawingManual && root.isLeft
        color: "transparent"
        border.color: "#c084fc"
        border.width: 2
        x: root.displayRectFromImage(
            root._manualIx1, root._manualIy1, root._manualIx2, root._manualIy2).x
        y: root.displayRectFromImage(
            root._manualIx1, root._manualIy1, root._manualIx2, root._manualIy2).y
        width: root.displayRectFromImage(
            root._manualIx1, root._manualIy1, root._manualIx2, root._manualIy2).width
        height: root.displayRectFromImage(
            root._manualIx1, root._manualIy1, root._manualIx2, root._manualIy2).height
    }

    Rectangle {
        id: manualEditHint
        z: 11
        visible: root.isLeft && root._selectedManualIndex >= 0
            && !root.playing && !root._drawingManual
        height: 24
        width: Math.min(Math.max(0, root.width - 16), manualHintText.implicitWidth + 14)
        radius: Theme.radiusSm
        color: Qt.rgba(0.03, 0.02, 0.07, 0.88)
        border.color: "#c084fc"
        border.width: 1
        x: {
            const r = root._manualActionRect()
            return Math.max(8, Math.min(root.width - width - 8, r.x))
        }
        y: {
            const r = root._manualActionRect()
            const below = r.y + r.height + 8
            return below + height <= root.height - 8
                ? below : Math.max(8, r.y - height - 8)
        }

        Text {
            id: manualHintText
            anchors.fill: parent
            anchors.leftMargin: 7
            anchors.rightMargin: 7
            text: root._manualEditActive
                ? qsTr("Édition : glisser pour déplacer · poignées pour redimensionner · Échap pour terminer")
                : qsTr("Sélectionnée · crayon pour modifier · × pour supprimer")
            color: "#f3e8ff"
            font.family: Theme.fontFamily
            font.pixelSize: 10
            font.weight: Font.DemiBold
            verticalAlignment: Text.AlignVCenter
            elide: Text.ElideRight
        }
    }

    // Crayon : arme l'edition de la bbox selectionnee, manuelle ou IA. Sans lui,
    // selectionner suffisait a la rendre deplacable, et le premier clic
    // gauche la modifiait - ce qui vidait la selection et privait
    // « Mesurer » de sa cible.
    Rectangle {
        id: editManualButton
        objectName: "editSelectedBoxButton"
        z: 12
        visible: manualEditHint.visible
        width: 26
        height: 26
        radius: 13
        color: root._manualEditActive
            ? "#9333ea"
            : (editManualMouse.containsMouse ? "#7e22ce" : "#3b0764")
        border.color: "#e9d5ff"
        border.width: 1
        x: {
            const r = root._manualActionRect()
            const outside = r.x - width - 7
            return outside >= 6
                ? outside : Math.min(root.width - width - 6, r.x + 5)
        }
        y: {
            const r = root._manualActionRect()
            return Math.max(6, Math.min(root.height - height - 6, r.y + 5))
        }

        // Segoe UI n'a aucun glyphe crayon (U+270E, U+270F, U+2710 absents) :
        // un Text afficherait un carre vide. On dessine donc l'icone.
        Canvas {
            anchors.centerIn: parent
            width: 16
            height: 16
            onPaint: {
                const ctx = getContext("2d")
                ctx.reset()
                ctx.fillStyle = "#ffffff"
                ctx.strokeStyle = "#ffffff"
                ctx.lineWidth = 1
                ctx.beginPath()
                ctx.moveTo(2.0, 14.0)
                ctx.lineTo(4.6, 13.2)
                ctx.lineTo(13.2, 4.6)
                ctx.lineTo(11.4, 2.8)
                ctx.lineTo(2.8, 11.4)
                ctx.closePath()
                ctx.fill()
                ctx.beginPath()
                ctx.moveTo(2.8, 11.4)
                ctx.lineTo(4.6, 13.2)
                ctx.stroke()
            }
        }

        ToolTip.visible: editManualMouse.containsMouse
        ToolTip.text: root._manualEditActive
            ? qsTr("Terminer la modification")
            : qsTr("Modifier cette bbox (déplacer, redimensionner)")
        Accessible.name: qsTr("Modifier le cadre sélectionné")

        MouseArea {
            id: editManualMouse
            anchors.fill: parent
            hoverEnabled: true
            cursorShape: Qt.PointingHandCursor
            onClicked: {
                if (root._manualEditActive) {
                    root._finishManualEdit(true)
                    root._manualEditArmed = -1
                } else {
                    root._manualEditArmed = root._selectedManualIndex
                }
            }
        }
    }

    Rectangle {
        id: deleteManualButton
        objectName: "deleteSelectedBoxButton"
        z: 12
        visible: manualEditHint.visible
        width: 26
        height: 26
        radius: 13
        color: deleteManualMouse.containsMouse ? "#ef4444" : "#b91c1c"
        border.color: "#fee2e2"
        border.width: 1
        x: {
            const r = root._manualActionRect()
            const outside = r.x + r.width + 7
            return outside + width <= root.width - 6
                ? outside : Math.max(6, r.x + r.width - width - 5)
        }
        y: {
            const r = root._manualActionRect()
            return Math.max(6, Math.min(root.height - height - 6, r.y + 5))
        }

        Text {
            anchors.centerIn: parent
            text: "×"
            color: "#ffffff"
            font.pixelSize: 18
            font.weight: Font.Bold
        }

        ToolTip.visible: deleteManualMouse.containsMouse
        ToolTip.text: qsTr("Supprimer ce cadre")
        Accessible.name: qsTr("Supprimer le cadre sélectionné")

        MouseArea {
            id: deleteManualMouse
            anchors.fill: parent
            hoverEnabled: true
            cursorShape: Qt.PointingHandCursor
            onClicked: root._deleteSelectedManualBox()
        }
    }

    // « Quand il y a une bouchee, ca fait un petit pop du logo sur la frame ou
    // on a vu la bouchee. » Un seul element pour toute la vue : deux bouchees
    // rapprochees redemarrent la meme animation au lieu d'en empiler deux, et
    // rien n'est peint ni compose quand elle ne tourne pas.
    Item {
        id: peckPop
        objectName: "peckPopBadge"
        z: 9
        visible: root.isLeft && peckPopAnim.running
        width: 44
        height: 44

        // Position du poisson sur CETTE image, deja recalee sur l'image
        // affichee par le controleur : rien a convertir ici.
        property real imgX: 0
        property real imgY: 0
        property string symbol: "●"
        property color tint: "#f59e0b"
        property real grow: 1.0
        property real fade: 0.0

        x: root._contentOx + peckPop.imgX * root._viewScale - width / 2
        y: root._contentOy + peckPop.imgY * root._viewScale - height / 2

        Rectangle {
            anchors.centerIn: parent
            width: parent.width
            height: parent.height
            radius: width / 2
            color: "transparent"
            border.color: peckPop.tint
            border.width: 3
            opacity: peckPop.fade * 0.85
            scale: peckPop.grow
        }

        Rectangle {
            anchors.centerIn: parent
            width: 26
            height: 26
            radius: 13
            color: peckPop.tint
            border.color: "#ffffff"
            border.width: 1
            opacity: peckPop.fade
            scale: 0.75 + 0.45 * peckPop.grow

            Text {
                anchors.centerIn: parent
                text: peckPop.symbol
                color: "#0b1015"
                font.family: Theme.fontFamily
                font.pixelSize: 15
                font.weight: Font.Bold
            }
        }

        SequentialAnimation {
            id: peckPopAnim
            ParallelAnimation {
                NumberAnimation {
                    target: peckPop; property: "fade"
                    from: 0; to: 1; duration: 70
                }
                NumberAnimation {
                    target: peckPop; property: "grow"
                    from: 0.35; to: 1.2; duration: 170
                    easing.type: Easing.OutBack
                }
            }
            PauseAnimation { duration: 150 }
            ParallelAnimation {
                NumberAnimation {
                    target: peckPop; property: "fade"
                    to: 0; duration: 240; easing.type: Easing.InQuad
                }
                NumberAnimation {
                    target: peckPop; property: "grow"
                    to: 1.65; duration: 240; easing.type: Easing.InQuad
                }
            }
        }
    }

    Connections {
        // `Pecks` n'existe pas dans les harnais qui montent la vue seule, et
        // la vue droite n'a pas de marqueur : dans les deux cas, pas de cible.
        target: (root.isLeft && typeof Pecks !== "undefined") ? Pecks : null
        function onPeckPopped(x, y, symbol, color, label) {
            peckPop.imgX = x
            peckPop.imgY = y
            peckPop.symbol = (symbol && symbol.length > 0) ? symbol : "●"
            peckPop.tint = (color && color.length > 0) ? color : "#f59e0b"
            peckPopAnim.restart()
        }
    }

    MouseArea {
        id: pointer
        anchors.fill: parent
        z: 10
        hoverEnabled: true
        enabled: frameWidth > 0
        acceptedButtons: Qt.LeftButton | Qt.RightButton
        focus: true
        objectName: "measurePointer"
        cursorShape: root._interactionCursor(mouseX, mouseY)

        function imageCoords(mx, my) {
            const iw = Math.max(1, frameWidth)
            const ih = Math.max(1, frameHeight)
            const s = root._viewScale
            const ox = root._contentOx
            const oy = root._contentOy
            const x = Math.max(0, Math.min((mx - ox) / s, iw - 1))
            const y = Math.max(0, Math.min((my - oy) / s, ih - 1))
            return { x: x, y: y }
        }

        function findHandleHit(mx, my) {
            function dist(pt) {
                if (pt.x < 0)
                    return 9999
                const s = root._viewScale
                const ox = root._contentOx
                const oy = root._contentOy
                const dx = ox + pt.x * s - mx
                const dy = oy + pt.y * s - my
                return Math.sqrt(dx * dx + dy * dy)
            }
            const distanceA = root._hasA ? dist(root.pointA) : 9999
            const distanceB = root._hasB ? dist(root.pointB) : 9999
            const nearest = distanceA <= distanceB ? 0 : 1
            const nearestDistance = nearest === 0 ? distanceA : distanceB
            return nearestDistance < root._hitDist ? nearest : -1
        }

        onPressed: function(mouse) {
            pointer.forceActiveFocus()
            if (!root.interactionEnabled)
                return

            root._pendingPointPlace = false
            root._resumeBoxGesture = false
            // Pendant une perte, désigner le cadre reprend le même poisson :
            // ne pas ouvrir une autre fiche ni adopter la taxonomie de l'IA.
            if (root.isLeft && !root.playing && typeof Fish !== "undefined"
                    && Fish.assistWaiting && !Fish.busy) {
                const resumeHit = overlay._hitBox(mouse.x, mouse.y)
                if (resumeHit) {
                    root._resumeBoxGesture = true
                    root._resumeBoxIndex = resumeHit.boxIndex
                    root._pressX = mouse.x
                    root._pressY = mouse.y
                    root._blockManualDraw = false
                    root._drawingManual = false
                    root._dragIndex = -1
                    mouse.accepted = true
                    return
                }
            }
            if (mouse.button === Qt.RightButton) {
                const hit = overlay._hitBox(mouse.x, mouse.y)
                if (hit)
                    root._selectBoxByRightClick(hit.boxIndex)
                else
                    root._clearBoxSelection()
                mouse.accepted = true
                return
            }

            root._dragIndex = findHandleHit(mouse.x, mouse.y)
            if (root._dragIndex >= 0) {
                root._selectedPointIndex = root._dragIndex
                markers.requestPaint()
                mouse.accepted = true
                return
            }

            // Marquage ponctuel : une piste armée transforme le clic gauche
            // DANS sa bbox en pose de marqueur. Le clic hors de la boîte n'est
            // pas avalé, sinon la mesure deviendrait impossible tant qu'une
            // piste est sélectionnée. Les poignées A/B gardent la priorité :
            // ajuster une mesure reste possible sans désarmer.
            if (root.isLeft && !root.playing && typeof Pecks !== "undefined"
                    && Pecks.armed && overlay._hitPeckBox(mouse.x, mouse.y)) {
                const peckPoint = overlay.imagePointAt(mouse.x, mouse.y)
                // Le clic est consommé même quand la pose est refusée (un
                // marqueur existe déjà sur cette image) : sinon le refus se
                // transformerait en point de mesure posé par surprise.
                Pecks.markFromOverlay(peckPoint.x, peckPoint.y)
                root._pendingPointPlace = false
                root._drawingManual = false
                mouse.accepted = true
                return
            }

            if (root.isLeft && !root.playing) {
                const selectedHandle = root._manualHandleHit(
                    mouse.x, mouse.y, root._selectedManualIndex)
                if (selectedHandle.length > 0) {
                    root._beginManualEdit(
                        root._selectedManualIndex, selectedHandle, mouse.x, mouse.y)
                    root._pendingPointPlace = false
                    mouse.accepted = true
                    return
                }
                const boxHit = overlay._hitBox(mouse.x, mouse.y)
                if (root._manualEditActive && boxHit
                        && boxHit.boxIndex === root._selectedManualIndex) {
                    root._beginManualEdit(
                        root._selectedManualIndex, "", mouse.x, mouse.y)
                    root._pendingPointPlace = false
                    mouse.accepted = true
                    return
                }
            }
            root._selectedPointIndex = -1
            root._pressX = mouse.x
            root._pressY = mouse.y
            root._drawingManual = false
            root._blockManualDraw = false
            if (root.isLeft && !root.playing && !root._manualEditActive
                    && root._selectedManualIndex >= 0) {
                const onSelected = overlay._hitBox(mouse.x, mouse.y)
                root._blockManualDraw = !!onSelected
                    && onSelected.boxIndex === root._selectedManualIndex
            }
            root._pendingPointPlace = root.placementEnabled
        }

        onPositionChanged: function(mouse) {
            if (root._resumeBoxGesture) {
                const dx = mouse.x - root._pressX
                const dy = mouse.y - root._pressY
                if (!(mouse.buttons & Qt.LeftButton) || Math.sqrt(dx * dx + dy * dy) <= 8)
                    return
                // Un glisser reste un réencadrement, même commencé dans une bbox.
                root._resumeBoxGesture = false
            }
            root._hoverPointIndex = findHandleHit(mouse.x, mouse.y)
            if (root._editingManualIndex >= 0) {
                root._hoveredManualIndex = root._editingManualIndex
                if (pressed)
                    root._updateManualEdit(mouse.x, mouse.y)
                return
            }
            if (root.isLeft && !root.playing) {
                const hoverHit = overlay._hitBox(mouse.x, mouse.y)
                root._hoveredManualIndex = hoverHit ? hoverHit.boxIndex : -1
            } else {
                root._hoveredManualIndex = -1
            }
            if (root._dragIndex >= 0 && (mouse.buttons & Qt.LeftButton)) {
                const p = imageCoords(mouse.x, mouse.y)
                root.pointMoved(root._dragIndex, p.x, p.y)
                markers.requestPaint()
                return
            }
            if ((mouse.buttons & Qt.LeftButton) && !root._drawingManual
                    && !root._blockManualDraw
                    && root.isLeft && !root.playing
                    && root.interactionEnabled) {
                const dx = mouse.x - root._pressX
                const dy = mouse.y - root._pressY
                if (Math.sqrt(dx * dx + dy * dy) > 8) {
                    root._pendingPointPlace = false
                    root._drawingManual = true
                    const p1 = imageCoords(root._pressX, root._pressY)
                    root._manualIx1 = p1.x
                    root._manualIy1 = p1.y
                }
            }
            if (root._drawingManual && (mouse.buttons & Qt.LeftButton)) {
                const p2 = imageCoords(mouse.x, mouse.y)
                root._manualIx2 = p2.x
                root._manualIy2 = p2.y
            }
        }

        onReleased: function(mouse) {
            if (root._resumeBoxGesture) {
                root._resumeBoxGesture = false
                Fish.resumeAssistFromBox(root._resumeBoxIndex)
                return
            }
            if (mouse.button === Qt.RightButton)
                return
            const blocked = root._blockManualDraw
            root._blockManualDraw = false
            if (root._editingManualIndex >= 0) {
                root._updateManualEdit(mouse.x, mouse.y)
                root._finishManualEdit(true)
                const hoverHit = overlay._hitBox(mouse.x, mouse.y)
                root._hoveredManualIndex = hoverHit ? hoverHit.boxIndex : -1
                return
            }
            if (blocked && !root._pendingPointPlace)
                return
            if (root._drawingManual) {
                const p1 = imageCoords(root._pressX, root._pressY)
                const p2 = imageCoords(mouse.x, mouse.y)
                const x1 = Math.min(p1.x, p2.x)
                const y1 = Math.min(p1.y, p2.y)
                const x2 = Math.max(p1.x, p2.x)
                const y2 = Math.max(p1.y, p2.y)
                root._drawingManual = false
                if (!root.playing && root.isLeft && typeof Fish !== "undefined"
                        && (x2 - x1) > 6 && (y2 - y1) > 6) {
                    Fish.addManualBox(x1, y1, x2, y2)
                    // Le dessin ne vaut pas sélection : seul un clic droit
                    // explicite donne ensuite accès à l'édition de la bbox.
                    root._clearBoxSelection()
                }
                return
            }
            if (root._dragIndex >= 0) {
                root._dragIndex = -1
                markers.requestPaint()
                root.pointDragFinished()
                return
            }
            if (root._pendingPointPlace) {
                root._pendingPointPlace = false
                const p = imageCoords(mouse.x, mouse.y)
                root.pointPlaced(p.x, p.y)
            }
        }

        onCanceled: {
            root._resumeBoxGesture = false
            root._finishManualEdit(false)
            root._drawingManual = false
            root._blockManualDraw = false
            root._pendingPointPlace = false
            if (root._dragIndex < 0)
                return
            root._dragIndex = -1
            markers.requestPaint()
            root.pointDragFinished()
        }

        onExited: {
            root._hoverPointIndex = -1
            if (!pressed && root._editingManualIndex < 0)
                root._hoveredManualIndex = -1
        }

        Keys.onPressed: function(event) {
            if ((event.key === Qt.Key_Delete || event.key === Qt.Key_Backspace)
                    && root._selectedPointIndex >= 0) {
                const index = root._selectedPointIndex
                root._selectedPointIndex = -1
                root._dragIndex = -1
                root.pointDeleteRequested(index)
                event.accepted = true
            } else if (event.key === Qt.Key_Left || event.key === Qt.Key_Right
                    || event.key === Qt.Key_Up || event.key === Qt.Key_Down) {
                // Tant qu'un point est selectionne, les fleches lui
                // appartiennent : elles ne defilent plus les images (Echap
                // rend la main au transport).
                const step = (event.modifiers & Qt.ShiftModifier)
                    ? root._nudgeStepFast : root._nudgeStep
                const dx = event.key === Qt.Key_Left ? -step
                    : (event.key === Qt.Key_Right ? step : 0)
                const dy = event.key === Qt.Key_Up ? -step
                    : (event.key === Qt.Key_Down ? step : 0)
                if (root._nudgeSelectedPoint(dx, dy))
                    event.accepted = true
            } else if (event.key === Qt.Key_Escape) {
                if (root._editingManualIndex >= 0)
                    root._finishManualEdit(false)
                else if (root._manualEditActive)
                    root._manualEditArmed = -1
                else if (root._selectedPointIndex >= 0)
                    root._selectedPointIndex = -1
                else
                    root._clearBoxSelection()
                event.accepted = true
            }
        }
    }

    Item {
        id: loupeHost
        z: 8
        visible: pointer.containsMouse
                && root.rectifiedReady
                && frameImg.status === Image.Ready
                && frameImg.sourceSize.width > 0
        width: root.loupeWidth
        height: root.loupeHeight

        readonly property var map: root.imageMapping(pointer.mouseX, pointer.mouseY)

        x: {
            const pad = 8
            let lx = pointer.mouseX - width / 2
            if (lx < pad)
                lx = pad
            if (lx + width > root.width - pad)
                lx = root.width - pad - width
            return lx
        }
        y: {
            const pad = 8
            let ly = pointer.mouseY - height - 12
            if (ly < pad)
                ly = pointer.mouseY + 16
            if (ly + height > root.height - pad)
                ly = root.height - pad - height
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
                source: frameImg.source
                fillMode: Image.Stretch
                width: (loupeHost.map.iw || 1) * root.zoomFactor
                height: (loupeHost.map.ih || 1) * root.zoomFactor
                x: loupeHost.map.valid
                     ? (-loupeHost.map.ix * root.zoomFactor + loupeHost.width / 2)
                     : 0
                y: loupeHost.map.valid
                     ? (-loupeHost.map.iy * root.zoomFactor + loupeHost.height / 2)
                     : 0
            }

            Canvas {
                id: loupeMarkers
                anchors.fill: parent
                z: 1

                onPaint: {
                    const ctx = getContext("2d")
                    ctx.clearRect(0, 0, width, height)
                    if (!loupeHost.map.valid)
                        return

                    const zf = root.zoomFactor
                    const cx = loupeHost.map.ix
                    const cy = loupeHost.map.iy
                    const col = isLeft ? "#dc2626" : "#2563eb"

                    function toLoupe(px, py) {
                        return {
                            x: (px - cx) * zf + width / 2,
                            y: (py - cy) * zf + height / 2
                        }
                    }

                    function drawCross(pt, label, selected) {
                        if (pt.x < 0 || pt.y < 0)
                            return
                        const p = toLoupe(pt.x, pt.y)
                        if (p.x < -12 || p.x > width + 12
                                || p.y < -12 || p.y > height + 12)
                            return
                        const arm = 7
                        ctx.strokeStyle = selected ? "#facc15" : "rgba(255,255,255,0.9)"
                        ctx.lineWidth = selected ? 4 : 2
                        ctx.beginPath()
                        ctx.moveTo(p.x - arm, p.y)
                        ctx.lineTo(p.x + arm, p.y)
                        ctx.moveTo(p.x, p.y - arm)
                        ctx.lineTo(p.x, p.y + arm)
                        ctx.stroke()
                        ctx.strokeStyle = col
                        ctx.lineWidth = 1
                        ctx.beginPath()
                        ctx.moveTo(p.x - arm, p.y)
                        ctx.lineTo(p.x + arm, p.y)
                        ctx.moveTo(p.x, p.y - arm)
                        ctx.lineTo(p.x, p.y + arm)
                        ctx.stroke()
                        ctx.fillStyle = col
                        ctx.font = "bold 9px " + Theme.canvasFontStack
                        ctx.fillText(label, p.x + arm + 2, p.y - arm - 1)
                    }

                    if (root._hasA && root._hasB) {
                        const da = toLoupe(root.pointA.x, root.pointA.y)
                        const db = toLoupe(root.pointB.x, root.pointB.y)
                        ctx.strokeStyle = col
                        ctx.globalAlpha = 0.55
                        ctx.lineWidth = 1
                        ctx.beginPath()
                        ctx.moveTo(da.x, da.y)
                        ctx.lineTo(db.x, db.y)
                        ctx.stroke()
                        ctx.globalAlpha = 1
                    }
                    if (root._hasA)
                        drawCross(root.pointA, "A", root._selectedPointIndex === 0
                            || root._hoverPointIndex === 0)
                    if (root._hasB)
                        drawCross(root.pointB, "B", root._selectedPointIndex === 1
                            || root._hoverPointIndex === 1)
                }

                Connections {
                    target: root
                    function onPointAChanged() { loupeMarkers.requestPaint() }
                    function onPointBChanged() { loupeMarkers.requestPaint() }
                }
                Connections {
                    target: pointer
                    function onMouseXChanged() {
                        if (loupeHost.visible)
                            loupeMarkers.requestPaint()
                    }
                    function onMouseYChanged() {
                        if (loupeHost.visible)
                            loupeMarkers.requestPaint()
                    }
                }
            }

            Rectangle {
                anchors.centerIn: parent
                z: 2
                width: root._hoverPointIndex >= 0 ? 16 : 10
                height: width
                radius: width / 2
                color: "transparent"
                border.color: root._hoverPointIndex >= 0 ? "#facc15" : Theme.accentText
                border.width: root._hoverPointIndex >= 0 ? 3 : 1
                opacity: 0.85
            }

            Text {
                objectName: "pointLoupeHint"
                visible: root._hoverPointIndex >= 0
                anchors.horizontalCenter: parent.horizontalCenter
                anchors.bottom: parent.bottom
                anchors.bottomMargin: 7
                text: root._hoverPointIndex === 0
                    ? qsTr("Cliquer pour sélectionner A")
                    : qsTr("Cliquer pour sélectionner B")
                color: "#facc15"
                font.family: Theme.fontFamily
                font.pixelSize: Theme.fzXs
                font.weight: Font.DemiBold
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

    WheelHandler {
        enabled: root.rectifiedReady && root.frameWidth > 0
        acceptedDevices: PointerDevice.Mouse | PointerDevice.TouchPad
        onWheel: (event) => {
            const step = event.angleDelta.y > 0 ? 1.15 : (1.0 / 1.15)
            root.zoomAt(step, event.x, event.y)
            event.accepted = true
        }
    }

    TapHandler {
        target: null
        acceptedButtons: Qt.LeftButton
        onDoubleTapped: {
            if (root.fullscreenImageSource.length > 0)
                root.fullscreenRequested(root.fullscreenCaption, root.fullscreenImageSource)
        }
    }
}
