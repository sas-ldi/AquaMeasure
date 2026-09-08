import QtQuick
import AquaMeasure

// Icônes vectorielles indépendantes des glyphes de la police système.
Item {
    property string name: "settings"
    property color tint: Theme.accentText
    readonly property var paths: ({
        "database": "M3 6c0-4 18-4 18 0s-18 4-18 0zm0 0v12c0 4 18 4 18 0V6M3 12c0 4 18 4 18 0",
        "export": "M14 3h7v7m0-7L10 14M11 4H3v17h17v-8",
        "import": "M3 10v11h18V10M12 2v13m-5-5 5 5 5-5",
        "clock": "M12 3a9 9 0 1 0 0 18 9 9 0 1 0 0-18zm0 4v6h5",
        "close": "M6 6l12 12M18 6 6 18",
        "device": "M5 4h14v12H5zM9 16v4m6-4v4M7 20h10M8 8h8m-8 4h4",
        "user": "M8 7a4 4 0 1 0 8 0 4 4 0 1 0-8 0M4 21v-3c0-7 16-7 16 0v3",
        "save": "M3 3h15l3 3v15H3zM7 3v6h10V3M7 21v-8h10v8",
        "fish": "M3 12c4-7 11-7 16 0-5 7-12 7-16 0zm0 0L1 8v8zm12-2h.01",
        "ruler": "M3 8h18v9H3zM7 8v5m4-5v3m4-3v5m4-5v3",
        "track": "M5 18c0-8 14 2 14-7 0-5-8-6-8-2M5 16v4m-2-2h4M11 7v4m-2-2h4",
        "list": "M8 5h13M8 12h13M8 19h13M3 5h.01M3 12h.01M3 19h.01",
        "folder": "M3 6h6l2 2h10v12H3zM3 6V4h7l2 2h9v2",
        "video": "M3 6h13v13H3zM16 10l5-3v11l-5-3z",
        "detect": "M8 3H3v5m13-5h5v5M3 16v5h5m13-5v5h-5M7 8h10v9H7z",
        "eye": "M2 12c5-9 15-9 20 0-5 9-15 9-20 0zm7 0a3 3 0 1 0 6 0 3 3 0 1 0-6 0",
        "sync": "M4 8a8 8 0 0 1 14-3l3 3m0-5v5h-5M20 16a8 8 0 0 1-14 3l-3-3m0 5v-5h5",
        "cut": "M9 8l11 12M9 16L20 4M3 6a3 3 0 1 0 6 0 3 3 0 1 0-6 0M3 18a3 3 0 1 0 6 0 3 3 0 1 0-6 0",
        "calibrate": "M3 3h18v18H3zM9 3v18M15 3v18M3 9h18M3 15h18",
        "chart": "M4 3v18h17M8 17v-5m5 5V7m5 10V4",
        "next": "M4 12h16m-6-6 6 6-6 6",
        "settings": "M4 6h16M4 12h16M4 18h16M8 3v6m8 0v6m-8 0v6",
        "point": "M12 3v4m0 10v4M3 12h4m10 0h4M8 12a4 4 0 1 0 8 0 4 4 0 1 0-8 0"
    })
    implicitWidth: 18
    implicitHeight: 18
    Image {
        anchors.fill: parent
        sourceSize.width: Math.round(width * 2)
        sourceSize.height: Math.round(height * 2)
        source: "data:image/svg+xml," + encodeURIComponent(
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path d="'
            + (paths[name] || paths.settings) + '" fill="none" stroke="' + tint
            + '" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>')
    }
}
