import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import AquaMeasure

Popup {
    id: dlg
    parent: Overlay.overlay
    modal: true
    focus: true
    closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
    padding: Theme.spaceLg
    width: Math.min(480, Overlay.overlay ? Overlay.overlay.width - Theme.spaceXl * 2 : 480)

    onAboutToShow: {
        const o = Overlay.overlay
        if (!o)
            return
        x = Math.round(Math.max(0, (o.width - width) / 2))
        y = Math.round(Math.max(0, (o.height - implicitHeight) / 2))
    }

    readonly property var pagePresets: [
        { label: qsTr("A3 portrait (297 × 420 mm)"), w: 297, h: 420 },
        { label: qsTr("A3 paysage (420 × 297 mm)"), w: 420, h: 297 },
        { label: qsTr("A4 portrait (210 × 297 mm)"), w: 210, h: 297 },
        { label: qsTr("Personnalisé"), w: -1, h: -1 }
    ]

    property int pagePresetIdx: 0
    property double customPageW: 840
    property double customPageH: 297
    property int dpi: 300

    readonly property double pageW: pagePresetIdx === 3 ? customPageW : pagePresets[pagePresetIdx].w
    readonly property double pageH: pagePresetIdx === 3 ? customPageH : pagePresets[pagePresetIdx].h
    readonly property double boardW: Settings.charucoSquaresX * Settings.charucoSquareLengthMm
    readonly property double boardH: Settings.charucoSquaresY * Settings.charucoSquareLengthMm
    readonly property bool fits: boardW <= pageW + 0.5 && boardH <= pageH + 0.5

    function autoFitGrid() {
        const sq = Settings.charucoSquareLengthMm
        if (sq <= 0 || pageW <= 0 || pageH <= 0)
            return
        Settings.charucoSquaresX = Math.min(40, Math.max(3, Math.floor(pageW / sq)))
        Settings.charucoSquaresY = Math.min(40, Math.max(3, Math.floor(pageH / sq)))
    }

    function autoMarker() {
        const sq = Settings.charucoSquareLengthMm
        if (sq <= 0)
            return
        Settings.charucoMarkerLengthMm = Math.round(sq * (37.0 / 49.5) * 10) / 10
    }

    background: Rectangle {
        radius: Theme.radiusMd
        color: Theme.surface
        border.color: Theme.border
        border.width: 1
    }

    contentItem: ColumnLayout {
        width: parent.width
        spacing: Theme.spaceMd

        RowLayout {
            Layout.fillWidth: true
            AppLabel {
                Layout.fillWidth: true
                text: qsTr("Générer la mire ChArUco")
                font.pixelSize: Theme.fontTitle
                font.weight: Font.DemiBold
            }
            AppButton {
                text: "✕"
                implicitWidth: 36
                implicitHeight: 32
                onClicked: dlg.close()
            }
        }

        AppLabel {
            Layout.fillWidth: true
            muted: true
            wrapMode: Text.WordWrap
            font.pixelSize: Theme.fontCaption
            text: qsTr("PNG à l'échelle réelle (300 dpi) - mêmes paramètres qu'en calibration.")
        }

        GridLayout {
            Layout.fillWidth: true
            columns: 2
            columnSpacing: Theme.spaceLg
            rowSpacing: Theme.spaceMd

            AppLabel { text: qsTr("Colonnes"); Layout.fillWidth: true }
            AppSpinBox {
                from: 3; to: 40
                value: Settings.charucoSquaresX
                Layout.preferredWidth: 132
                Layout.alignment: Qt.AlignRight
                onValueModified: Settings.charucoSquaresX = value
            }

            AppLabel { text: qsTr("Lignes"); Layout.fillWidth: true }
            AppSpinBox {
                from: 3; to: 40
                value: Settings.charucoSquaresY
                Layout.preferredWidth: 132
                Layout.alignment: Qt.AlignRight
                onValueModified: Settings.charucoSquaresY = value
            }

            AppLabel { text: qsTr("Côté case (mm)"); Layout.fillWidth: true }
            AppSpinBox {
                from: 1; to: 10000
                value: Math.round(Settings.charucoSquareLengthMm * 10)
                Layout.preferredWidth: 132
                Layout.alignment: Qt.AlignRight
                textFromValue: (v, locale) => (v / 10).toFixed(1)
                valueFromText: (text, locale) => Math.round(parseFloat(text) * 10)
                onValueModified: Settings.charucoSquareLengthMm = value / 10
            }

            AppLabel { text: qsTr("Marqueur (mm)"); Layout.fillWidth: true }
            RowLayout {
                Layout.preferredWidth: 132
                Layout.alignment: Qt.AlignRight
                spacing: 4
                AppSpinBox {
                    Layout.fillWidth: true
                    from: 1; to: 10000
                    value: Math.round(Settings.charucoMarkerLengthMm * 10)
                    textFromValue: (v, locale) => (v / 10).toFixed(1)
                    valueFromText: (text, locale) => Math.round(parseFloat(text) * 10)
                    onValueModified: Settings.charucoMarkerLengthMm = value / 10
                }
                AppButton {
                    text: qsTr("Auto")
                    implicitHeight: 32
                    implicitWidth: 48
                    onClicked: dlg.autoMarker()
                }
            }

            AppLabel { text: qsTr("Feuille"); Layout.fillWidth: true }
            AppComboBox {
                Layout.preferredWidth: 220
                Layout.alignment: Qt.AlignRight
                model: pagePresets.map(p => p.label)
                currentIndex: dlg.pagePresetIdx
                onActivated: dlg.pagePresetIdx = currentIndex
            }
        }

        GridLayout {
            Layout.fillWidth: true
            columns: 2
            columnSpacing: Theme.spaceLg
            rowSpacing: Theme.spaceMd
            visible: dlg.pagePresetIdx === 3

            AppLabel { text: qsTr("Largeur (mm)"); Layout.fillWidth: true }
            AppSpinBox {
                from: 50; to: 2000
                value: Math.round(dlg.customPageW)
                Layout.preferredWidth: 132
                Layout.alignment: Qt.AlignRight
                onValueModified: dlg.customPageW = value
            }

            AppLabel { text: qsTr("Hauteur (mm)"); Layout.fillWidth: true }
            AppSpinBox {
                from: 50; to: 2000
                value: Math.round(dlg.customPageH)
                Layout.preferredWidth: 132
                Layout.alignment: Qt.AlignRight
                onValueModified: dlg.customPageH = value
            }
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.spaceSm
            AppButton {
                text: qsTr("Grille auto")
                implicitHeight: 32
                enabled: Settings.charucoSquareLengthMm > 0
                onClicked: dlg.autoFitGrid()
            }
            AppLabel {
                Layout.fillWidth: true
                muted: true
                font.pixelSize: Theme.fontCaption
                wrapMode: Text.WordWrap
                text: qsTr("Mire %1 × %2 mm - feuille %3 × %4 mm")
                    .arg(dlg.boardW.toFixed(0))
                    .arg(dlg.boardH.toFixed(0))
                    .arg(dlg.pageW.toFixed(0))
                    .arg(dlg.pageH.toFixed(0))
            }
        }

        Rectangle {
            Layout.fillWidth: true
            implicitHeight: 28
            radius: Theme.radiusSm
            color: dlg.fits ? Theme.okSoft : Theme.dangerSoft
            border.color: dlg.fits ? Theme.ok : Theme.danger
            border.width: 1
            AppLabel {
                anchors.centerIn: parent
                font.pixelSize: Theme.fzSm
                color: dlg.fits ? Theme.ok : Theme.danger
                text: dlg.fits
                    ? qsTr("OK - la mire tient sur la feuille")
                    : qsTr("La mire dépasse la feuille")
            }
        }

        RowLayout {
            Layout.fillWidth: true
            Layout.topMargin: Theme.spaceSm
            spacing: Theme.spaceSm
            AppButton {
                text: qsTr("Fermer")
                fill: true
                onClicked: dlg.close()
            }
            AppButton {
                text: qsTr("Télécharger PNG…")
                primary: true
                fill: true
                enabled: dlg.fits
                onClicked: {
                    Calib.exportCharucoBoardPng(dlg.pageW, dlg.pageH, dlg.dpi)
                }
            }
        }
    }
}
