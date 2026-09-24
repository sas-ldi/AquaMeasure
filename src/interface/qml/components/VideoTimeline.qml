import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import AquaMeasure

Column {
    id: root

    property int totalFrames: 1
    property int currentFrame: 0
    property int inFrame: 0
    property int outFrame: 0
    property int pinFrame: 0
    property real detectWindowS: 5.0
    property double fps: 30.0

    // Marqueurs ponctuels (bouchées) posés sur la piste travaillée, en index
    // timeline : [{ eventId, frame, symbol, color, label }]. Vide par défaut,
    // la barre reste alors exactement celle d'avant.
    property var eventMarkers: []
    property int eventRangeStart: -1
    property int eventRangeEnd: -1

    signal eventMarkerActivated(string eventId, int frame)
    signal seekRequested(int frame)
    signal inMarkerMoved(int frame)
    signal outMarkerMoved(int frame)
    signal flashPinMoved(int frame)
    signal detectWindowChanged(real seconds)

    readonly property int handleHalf: 10
    readonly property int _outBound: outFrame > 0 ? outFrame : Math.max(0, totalFrames - 1)
    readonly property int _maxF: Math.max(0, totalFrames - 1)
    readonly property int _halfFrames: Math.max(1, Math.round(detectWindowS * (fps > 0 ? fps : 30.0)))
    readonly property int _flashStart: Math.max(0, pinFrame - _halfFrames)
    readonly property int _flashEnd: Math.min(_maxF, pinFrame + _halfFrames)

    spacing: Theme.s2
    width: parent ? parent.width : implicitWidth
    enabled: totalFrames > 1

    function frameToX(f) {
        if (totalFrames <= 1) return 0
        return (f / (totalFrames - 1)) * bar.width
    }

    function xToFrame(x) {
        if (totalFrames <= 1) return 0
        const t = Math.max(0, Math.min(1, x / Math.max(1, bar.width)))
        return Math.round(t * (totalFrames - 1))
    }

    function clampIn(f)  { return Math.max(0, Math.min(f, _outBound)) }
    function clampOut(f) { return Math.max(inFrame, Math.min(f, totalFrames - 1)) }

    function windowSFromFrame(f) {
        const sec = Math.abs(f - pinFrame) / Math.max(fps, 1e-6)
        return Math.max(0.5, Math.min(120, sec))
    }

    function bumpWindow(delta) {
        detectWindowChanged(Math.max(0.5, Math.min(120, detectWindowS + delta)))
    }

    property int _drag: 0
    // Une poignee In/Out montre l'image de coupe pendant le glissement, puis
    // la vue revient : sinon une seule camera a bouge, l'appli signalait un
    // decalage « a valider » et le valider plantait le flash sur la poignee.
    property int _frameBeforeTrim: -1

    function _startTrim(kind, frame) {
        _drag = kind
        _frameBeforeTrim = currentFrame
        seekRequested(frame)
    }

    function _endTrim() {
        if (_frameBeforeTrim >= 0)
            seekRequested(_frameBeforeTrim)
        _frameBeforeTrim = -1
        _drag = 0
    }

    component TrimHandle: Item {
        id: h
        property bool isIn: true
        property color accent: Theme.markIn
        property bool active: false

        width: 16
        height: 36

        Canvas {
            id: bracket
            anchors.centerIn: parent
            width: 10
            height: 28
            onPaint: {
                const ctx = getContext("2d")
                ctx.clearRect(0, 0, width, height)
                ctx.strokeStyle = h.active ? h.accent : Qt.rgba(h.accent.r, h.accent.g, h.accent.b, 0.72)
                ctx.lineWidth = h.active ? 2 : 1.5
                ctx.lineCap = "square"
                const pad = 1
                const top = pad
                const bot = height - pad
                const outer = h.isIn ? width - pad : pad
                const inner = h.isIn ? pad + 2 : width - pad - 2
                ctx.beginPath()
                if (h.isIn) {
                    ctx.moveTo(outer, top)
                    ctx.lineTo(inner, top)
                    ctx.lineTo(inner, bot)
                    ctx.lineTo(outer, bot)
                } else {
                    ctx.moveTo(outer, top)
                    ctx.lineTo(inner, top)
                    ctx.lineTo(inner, bot)
                    ctx.lineTo(outer, bot)
                }
                ctx.stroke()
            }
        }

        Connections {
            target: h
            function onActiveChanged() { bracket.requestPaint() }
            function onAccentChanged() { bracket.requestPaint() }
        }
    }

    component FlashPinHandle: Item {
        id: pin
        property bool active: false

        width: 6
        height: 36

        Rectangle {
            anchors.centerIn: parent
            width: active ? 2 : 1
            height: parent.height
            color: Theme.markPin
            opacity: active ? 1 : 0.75
        }
    }

    component FlashEdgeHandle: Item {
        property bool active: false

        width: 10
        height: 32

        Rectangle {
            anchors.centerIn: parent
            width: 1
            height: parent.height - 6
            color: Theme.markPin
            opacity: active ? 0.95 : 0.55
        }
    }

    component WindowStepBtn: Rectangle {
        id: step
        property string glyph: ""

        signal clicked()

        width: 22
        height: 22
        radius: Theme.radiusSm
        color: stepMa.pressed
            ? Theme.surfaceActive
            : (stepMa.containsMouse ? Theme.surfaceHover : Theme.panel2)
        border.color: Theme.border
        border.width: 1

        Text {
            anchors.centerIn: parent
            text: step.glyph
            font.family: Theme.monoFamily
            font.pixelSize: Theme.fzSm
            color: Theme.textMuted
        }

        MouseArea {
            id: stepMa
            anchors.fill: parent
            hoverEnabled: true
            cursorShape: Qt.PointingHandCursor
            onClicked: step.clicked()
        }
    }

    Item {
        id: trackHost
        width: parent.width
        height: 52

        Item {
            anchors.fill: parent
            anchors.leftMargin:   handleHalf + 2
            anchors.rightMargin:  handleHalf + 2
            anchors.topMargin:    Theme.s2
            anchors.bottomMargin: Theme.s2

            Rectangle {
                id: bar
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                height: 4
                radius: 2
                color: Theme.panel2
                border.color: Theme.border
                border.width: 1

                Rectangle {
                    x: frameToX(_flashStart)
                    width: Math.max(2, frameToX(_flashEnd) - x)
                    anchors.verticalCenter: parent.verticalCenter
                    height: parent.height + 10
                    radius: 2
                    color: Qt.rgba(Theme.markPin.r, Theme.markPin.g, Theme.markPin.b, 0.14)
                    border.color: Qt.rgba(Theme.markPin.r, Theme.markPin.g, Theme.markPin.b, 0.35)
                    border.width: 1
                    z: 0
                }

                Rectangle {
                    x: frameToX(Math.min(inFrame, _outBound))
                    width: Math.max(4, frameToX(Math.max(inFrame, _outBound)) - x)
                    anchors.verticalCenter: parent.verticalCenter
                    height: parent.height + 6
                    radius: 2
                    color: Qt.rgba(Theme.markIn.r, Theme.markIn.g, Theme.markIn.b, 0.08)
                    border.width: 0
                    z: 1
                }

                Rectangle {
                    x: frameToX(currentFrame) - 0.5
                    width: 1
                    height: parent.height + 18
                    anchors.verticalCenter: parent.verticalCenter
                    color: Theme.textMuted
                    opacity: 0.85
                    z: 2
                }

                MouseArea {
                    z: 1
                    anchors.fill: parent
                    anchors.leftMargin: handleHalf
                    anchors.rightMargin: handleHalf
                    onPressed:         (mouse) => { _drag = 4; seekRequested(xToFrame(mouse.x)) }
                    onPositionChanged: (mouse) => { if (_drag === 4) seekRequested(xToFrame(mouse.x)) }
                    onReleased: _drag = 0
                }
            }

            // Graduation des événements ponctuels, calée sur le même rail que
            // les poignées : elle ne dessine ni rail ni curseur, la timeline
            // a déjà les siens.
            EventMarkerStrip {
                objectName: "timelineEventMarkers"
                anchors.left: bar.left
                anchors.right: bar.right
                anchors.verticalCenter: bar.verticalCenter
                height: 30
                z: 8
                visible: root.eventMarkers && root.eventMarkers.length > 0
                showBaseline: false
                barMargin: 0
                interactive: true
                frameCount: root.totalFrames
                currentFrame: root.currentFrame
                rangeStart: root.eventRangeStart
                rangeEnd: root.eventRangeEnd
                markers: root.eventMarkers
                onMarkerActivated: function(eventId, frame) {
                    root.eventMarkerActivated(eventId, frame)
                }
            }

            FlashEdgeHandle {
                x: bar.x + frameToX(_flashStart) - width / 2
                anchors.verticalCenter: bar.verticalCenter
                active: flashLoMa.containsMouse || flashLoMa.pressed || _drag === 5
                z: 9
                MouseArea {
                    id: flashLoMa
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.SizeHorCursor
                    onPressed: _drag = 5
                    onPositionChanged: (mouse) => {
                        if (_drag !== 5) return
                        const bx = mapToItem(bar, mouse.x, 0).x
                        detectWindowChanged(windowSFromFrame(xToFrame(bx)))
                    }
                    onReleased: _drag = 0
                }
            }

            FlashEdgeHandle {
                x: bar.x + frameToX(_flashEnd) - width / 2
                anchors.verticalCenter: bar.verticalCenter
                active: flashHiMa.containsMouse || flashHiMa.pressed || _drag === 6
                z: 9
                MouseArea {
                    id: flashHiMa
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.SizeHorCursor
                    onPressed: _drag = 6
                    onPositionChanged: (mouse) => {
                        if (_drag !== 6) return
                        const bx = mapToItem(bar, mouse.x, 0).x
                        detectWindowChanged(windowSFromFrame(xToFrame(bx)))
                    }
                    onReleased: _drag = 0
                }
            }

            TrimHandle {
                x: bar.x + frameToX(inFrame) - width / 2
                anchors.verticalCenter: bar.verticalCenter
                isIn: true
                accent: Theme.markIn
                active: handleInMa.containsMouse || handleInMa.pressed || _drag === 1
                z: 11
                MouseArea {
                    id: handleInMa
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.SizeHorCursor
                    onPressed: _startTrim(1, inFrame)
                    onPositionChanged: (mouse) => {
                        if (_drag !== 1) return
                        const bx = mapToItem(bar, mouse.x, 0).x
                        const f  = clampIn(xToFrame(bx))
                        inMarkerMoved(f)
                        seekRequested(f)
                    }
                    onReleased: _endTrim()
                }
            }

            TrimHandle {
                x: bar.x + frameToX(_outBound) - width / 2
                anchors.verticalCenter: bar.verticalCenter
                isIn: false
                accent: Theme.markOut
                active: handleOutMa.containsMouse || handleOutMa.pressed || _drag === 2
                z: 11
                MouseArea {
                    id: handleOutMa
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.SizeHorCursor
                    onPressed: _startTrim(2, _outBound)
                    onPositionChanged: (mouse) => {
                        if (_drag !== 2) return
                        const barX = mapToItem(bar, mouse.x, 0).x
                        const f    = clampOut(xToFrame(barX))
                        outMarkerMoved(f)
                        seekRequested(f)
                    }
                    onReleased: _endTrim()
                }
            }

            FlashPinHandle {
                x: bar.x + frameToX(pinFrame) - width / 2
                anchors.verticalCenter: bar.verticalCenter
                active: pinMa.containsMouse || pinMa.pressed || _drag === 3
                // Sous les poignees In/Out : superposes (0 au chargement), on
                // attrapait le flash en croyant saisir le In.
                z: 10
                MouseArea {
                    id: pinMa
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.SizeHorCursor
                    onPressed: {
                        _drag = 3
                        seekRequested(pinFrame)
                    }
                    onPositionChanged: (mouse) => {
                        if (_drag !== 3) return
                        const barX = mapToItem(bar, mouse.x, 0).x
                        const f    = xToFrame(barX)
                        flashPinMoved(f)
                        seekRequested(f)
                    }
                    onReleased: _drag = 0
                }
            }
        }
    }

    RowLayout {
        id: windowRow
        width: parent.width
        height: 22
        spacing: Theme.s3
        visible: totalFrames > 1 && detectWindowS > 0

        Item { Layout.fillWidth: true }

        Text {
            text: qsTr("fenêtre")
            font.family: Theme.fontFamily
            font.pixelSize: Theme.fzXs
            color: Theme.textDim
            Layout.alignment: Qt.AlignVCenter
        }

        WindowStepBtn {
            glyph: "−"
            Layout.alignment: Qt.AlignVCenter
            onClicked: bumpWindow(-1)
        }

        Text {
            text: qsTr("± %1 s").arg(detectWindowS.toFixed(detectWindowS % 1 === 0 ? 0 : 1))
            font.family: Theme.monoFamily
            font.pixelSize: Theme.fzSm
            color: Theme.markPin
            Layout.alignment: Qt.AlignVCenter
            Layout.minimumWidth: 52
            horizontalAlignment: Text.AlignHCenter
        }

        WindowStepBtn {
            glyph: "+"
            Layout.alignment: Qt.AlignVCenter
            onClicked: bumpWindow(1)
        }

        Item { Layout.fillWidth: true }
    }
}
