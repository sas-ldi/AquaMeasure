import QtQuick
import QtMultimedia

// Lecteur vidéo natif (WMF / décodage matériel) - aperçu sync rapide.
Item {
    id: root

    property string videoPath: ""
    property double fps: 30.0
    property int frameCount: 0
    property int frame: 0
    property int inFrame: 0
    property int outFrame: 0
    property bool playing: false

    readonly property int playheadFrame: _playhead
    readonly property bool ready: media.mediaStatus === MediaPlayer.LoadedMedia
                                || media.mediaStatus === MediaPlayer.BufferedMedia
    readonly property bool loading: videoPath.length > 0
        && (media.mediaStatus === MediaPlayer.LoadingMedia
            || media.mediaStatus === MediaPlayer.NoMedia)
    readonly property bool hasError: media.mediaStatus === MediaPlayer.InvalidMedia
                                    || media.error !== MediaPlayer.NoError

    property int _playhead: 0
    property bool _blockSeek: false
    property bool _wasPlaying: false
    property bool _primed: false
    property bool _primePending: false

    function frameToMs(f) {
        const rate = fps > 0 ? fps : 30.0
        return Math.round(f * 1000.0 / rate)
    }

    function pathToUrl(path) {
        if (!path || path.length === 0)
            return ""
        if (path.indexOf("file:") === 0)
            return path
        const normalized = String(path).replace(/\\/g, "/")
        if (normalized.indexOf("/") === 0)
            return "file://" + normalized
        return "file:///" + normalized
    }

    function msToFrame(ms) {
        const rate = fps > 0 ? fps : 30.0
        const maxF = Math.max(0, frameCount - 1)
        return Math.max(0, Math.min(maxF, Math.round(ms * rate / 1000.0)))
    }

    // Seek fiable - WMF applique mal position pendant play() sans pause brève.
    function applySeek(f) {
        if (!media.source || _blockSeek)
            return
        const maxF = Math.max(0, frameCount - 1)
        const clamped = Math.max(0, Math.min(maxF, f))
        const resume = root.playing
        _blockSeek = true
        if (resume)
            media.pause()
        media.position = frameToMs(clamped)
        _playhead = clamped
        if (resume)
            media.play()
        _blockSeek = false
    }

    function jumpToFrame(f) {
        applySeek(f)
        if (!_primed && media.source)
            primeVideo()
    }

    // WMF/Qt : la 1re frame n'apparaît qu'après un play() - on amorce puis pause.
    function primeVideo() {
        if (_primed || !media.source || _primePending)
            return
        _primePending = true
        _blockSeek = true
        media.position = frameToMs(frame)
        _playhead = frame
        media.play()
        primeTimer.start()
    }

    Timer {
        id: primeTimer
        interval: 40
        repeat: false
        onTriggered: {
            root._primePending = false
            root._primed = true
            if (!root.playing)
                media.pause()
            root._blockSeek = true
            media.position = root.frameToMs(root.frame)
            root._playhead = root.frame
            root._blockSeek = false
        }
    }

    AudioOutput {
        id: mutedOut
        volume: 0.0
    }

    MediaPlayer {
        id: media
        audioOutput: mutedOut
        videoOutput: videoOut
        playbackRate: 1.0

        onMediaStatusChanged: {
            if (mediaStatus === MediaPlayer.LoadedMedia
                    || mediaStatus === MediaPlayer.BufferedMedia) {
                root.primeVideo()
            }
        }

        onPositionChanged: {
            if (!root.playing || root._blockSeek)
                return
            const f = root.msToFrame(position)
            const outF = root.outFrame
            if (f >= outF) {
                root.applySeek(root.inFrame)
                return
            }
            root._playhead = f
            playheadChanged(f)
        }

        onErrorOccurred: function(error, errorString) {
            if (error !== MediaPlayer.NoError)
                console.warn("SyncVideoPlayer:", errorString, root.videoPath)
        }
    }

    VideoOutput {
        id: videoOut
        anchors.fill: parent
        fillMode: VideoOutput.PreserveAspectFit
    }

    onVideoPathChanged: {
        primeTimer.stop()
        _primed = false
        _primePending = false
        const url = pathToUrl(videoPath)
        _blockSeek = true
        media.source = url
        _playhead = 0
        _blockSeek = false
    }

    onFrameChanged: {
        if (_blockSeek || _primePending)
            return
        if (frame === _playhead)
            return
        if (playing) {
            // Pendant la lecture, ignorer les +/-1 du timer ; traiter le scrub.
            if (Math.abs(frame - _playhead) <= 1)
                return
        }
        applySeek(frame)
        if (!_primed && media.source && !playing)
            primeVideo()
    }

    onPlayingChanged: {
        if (playing) {
            const outF = outFrame
            const cur = msToFrame(media.position)
            if (cur < inFrame || cur >= outF)
                applySeek(inFrame)
            media.play()
        } else {
            media.pause()
            if (_wasPlaying)
                root.frameSyncRequested(_playhead)
        }
        _wasPlaying = playing
    }

    signal frameSyncRequested(int frame)
    signal playheadChanged(int frame)
}
