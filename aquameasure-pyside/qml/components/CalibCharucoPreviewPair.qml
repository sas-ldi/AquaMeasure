import QtQuick
import QtQuick.Layouts
import AquaMeasure

// Paire d'aperçus ChArUco centrée, redimensionnable (poignée style SplitView).
ColumnLayout {
    id: root

    property real previewHeight: 300
    property real availableWidth: 600

    signal previewHeightDragged(real height)
    signal resizeActiveChanged(bool active)

    readonly property int handleSize: 6
    readonly property real minPreviewHeight: 160
    readonly property real maxPreviewHeight: 520

    readonly property real slotMaxWidth: Math.max(
        100,
        Math.floor((availableWidth - handleSize - Theme.spaceMd) / 2))

    readonly property real rowWidth: leftPreview.displayWidth + Theme.spaceMd
        + handleSize + rightPreview.displayWidth

    spacing: Theme.spaceSm
    Layout.fillWidth: true

    CalibPreviewFullscreenPopup {
        id: fullscreenDlg
    }

    function openFullscreen(caption, imageSource) {
        if (!imageSource || imageSource.length === 0)
            return
        fullscreenDlg.caption = caption
        fullscreenDlg.imageSource = imageSource
        fullscreenDlg.open()
    }

    AppLabel {
        Layout.alignment: Qt.AlignHCenter
        visible: root.scanCountsLabel !== ""
        text: root.scanCountsLabel
        font.pixelSize: Theme.fontBody
        font.weight: Font.DemiBold
        color: Theme.accent
        horizontalAlignment: Text.AlignHCenter
    }

    AppLabel {
        Layout.alignment: Qt.AlignHCenter
        visible: root.scanCountsLabel !== "" && typeof Settings !== "undefined"
        muted: true
        font.pixelSize: Theme.fzXs
        horizontalAlignment: Text.AlignHCenter
        text: qsTr("Compteur brut du scan · puis sélection %1 paires équiréparties pour OpenCV")
            .arg(Settings.maxStereoPairs)
    }

    Item {
        Layout.fillWidth: true
        Layout.preferredHeight: previewRow.height
        clip: true

        RowLayout {
            id: previewRow
            anchors.horizontalCenter: parent.horizontalCenter
            spacing: Theme.spaceMd

            CalibVideoPreview {
                id: leftPreview
                paneHeight: root.previewHeight
                slotMaxWidth: root.slotMaxWidth
                caption: leftCaption
                imageSource: leftImageSource
                onPreviewClicked: root.openFullscreen(leftCaption, leftImageSource)
            }

            Rectangle {
                Layout.preferredWidth: root.handleSize
                Layout.preferredHeight: Math.max(leftPreview.displayHeight, rightPreview.displayHeight)
                Layout.alignment: Qt.AlignVCenter
                color: Theme.border
            }

            CalibVideoPreview {
                id: rightPreview
                paneHeight: root.previewHeight
                slotMaxWidth: root.slotMaxWidth
                caption: rightCaption
                imageSource: rightImageSource
                onPreviewClicked: root.openFullscreen(rightCaption, rightImageSource)
            }
        }
    }

    // Poignée horizontale - glisser pour agrandir la hauteur des deux vues
    Item {
        Layout.alignment: Qt.AlignHCenter
        Layout.preferredWidth: root.rowWidth
        Layout.preferredHeight: Math.max(28, root.handleSize + Theme.s4 * 2)

        Rectangle {
            id: heightHandle
            anchors.centerIn: parent
            width: parent.width
            height: root.handleSize
            radius: 1
            color: heightResizeMa.pressed ? Theme.accent : Theme.border
        }

        MouseArea {
            id: heightResizeMa
            anchors.fill: parent
            hoverEnabled: true
            cursorShape: Qt.SizeVerCursor
            preventStealing: true

            property real _startH: 0
            property real _pressGlobalY: 0

            onPressed: (mouse) => {
                // Désactive le défilement parent AVANT toute autre chose pour
                // que le glisser vertical pilote la hauteur et non le scroll.
                root.resizeActiveChanged(true)
                _startH = root.previewHeight
                _pressGlobalY = mapToGlobal(mouse.x, mouse.y).y
                mouse.accepted = true
            }

            onPositionChanged: (mouse) => {
                if (!pressed)
                    return
                const globalY = mapToGlobal(mouse.x, mouse.y).y
                const delta = globalY - _pressGlobalY
                const h = Math.round(Math.max(
                    root.minPreviewHeight,
                    Math.min(root.maxPreviewHeight, _startH + delta)))
                root.previewHeightDragged(h)
                mouse.accepted = true
            }

            onReleased: (mouse) => {
                root.resizeActiveChanged(false)
                if (mouse)
                    mouse.accepted = true
            }

            onCanceled: root.resizeActiveChanged(false)
        }
    }

    AppLabel {
        Layout.alignment: Qt.AlignHCenter
        muted: true
        font.pixelSize: Theme.fzXs
        text: qsTr("Glisser la barre pour agrandir · clic sur un aperçu pour plein écran")
    }

    property string leftCaption: ""
    property string leftImageSource: ""
    property string rightCaption: ""
    property string rightImageSource: ""
    property string scanCountsLabel: ""
}
