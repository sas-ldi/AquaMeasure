import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import AquaMeasure

Item {
    id: root

    property real charucoPreviewHeight: 300
    property bool charucoResizeActive: false

    readonly property int consoleWidth: Math.min(
        Theme.consoleMaxWidth,
        Math.round(Math.max(Theme.contentMaxWidth, width * Theme.consoleWidthRatio))
    )

    Connections {
        target: App
        function onCurrentPageChanged() {
            if (App.currentPage === 3)
                App.prepareCalibrationPage()
        }
    }

    Component.onCompleted: {
        if (App.currentPage === 3)
            App.prepareCalibrationPage()
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: Theme.s2

        SplitView {
            id: mainSplit
            Layout.fillWidth: true
            Layout.fillHeight: true
            orientation: Qt.Horizontal
            handle: Rectangle {
                implicitWidth: 6
                implicitHeight: parent.height
                color: SplitHandle.pressed ? Theme.accent : Theme.border
            }

            ScrollView {
                id: scroll
                SplitView.fillWidth: true
                SplitView.minimumWidth: 420
                clip: true
                ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
                contentWidth: availableWidth

                Binding {
                    target: scroll.contentItem
                    property: "interactive"
                    value: !root.charucoResizeActive
                    when: scroll.contentItem !== null
                }

                ColumnLayout {
                    id: contentCol
                    width: Math.min(Theme.contentMaxWidth, scroll.availableWidth - Theme.spaceLg * 2)
                    x: Math.max(Theme.spaceLg, (scroll.availableWidth - width) / 2)
                    spacing: Theme.spaceLg

                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: Theme.spaceSm

                        RowLayout {
                            Layout.fillWidth: true
                            spacing: Theme.s2

                            AppLabel {
                                Layout.fillWidth: true
                                muted: true
                                wrapMode: Text.WordWrap
                                text: qsTr("Calibration : l'application repère la mire dans les deux vidéos, corrige la déformation de chaque objectif, puis mesure la position des caméras l'une par rapport à l'autre. Réglages dans le panneau de gauche.")
                            }

                            InfoDot {
                                text: qsTr("Sans calibration, l'application ne voit que des pixels : elle ne peut pas dire si un poisson fait 10 cm ou 1 m. La mire - la planche à damier imprimée - sert d'étalon : ses cases ont une taille connue, ce qui donne l'échelle. Une calibration reste valable tant que les caméras ne bougent pas dans leur caisson.")
                            }
                        }

                        AppLabel {
                            Layout.fillWidth: true
                            muted: true
                            font.pixelSize: Theme.fontCaption
                            wrapMode: Text.WordWrap
                            text: Settings.charucoSummary + " · " + Settings.calibSettingsSummary
                        }

                        StepIndicator {
                            Layout.alignment: Qt.AlignHCenter
                            currentStep: Calib.step
                            steps: [
                                qsTr("Import"),
                                qsTr("Scan"),
                                qsTr("Focal G/D"),
                                qsTr("Stéréo"),
                                qsTr("Résultat")
                            ]
                        }
                    }

                    AppCard {
                        Layout.fillWidth: true
                        visible: Calib.busy
                        title: qsTr("Progression")
                        iconName: "chart"
                        CalibProgressPanel {
                            Layout.fillWidth: true
                        }
                    }

                    AppCard {
                        id: charucoPreviewCard
                        Layout.fillWidth: true
                        visible: Calib.busy
                        title: qsTr("Aperçu détection ChArUco")
                        iconName: "calibrate"
                        CalibCharucoPreviewPair {
                            Layout.fillWidth: true
                            availableWidth: Math.max(
                                280,
                                charucoPreviewCard.width - Theme.spaceLg * 2)
                            previewHeight: root.charucoPreviewHeight
                            scanCountsLabel: Calib.scanCountsLabel
                            leftCaption: Calib.leftPreviewCaption || qsTr("Caméra gauche")
                            leftImageSource: Calib.busy
                                ? ("image://frames/calib_left?" + Calib.previewTick)
                                : ""
                            rightCaption: Calib.rightPreviewCaption || qsTr("Caméra droite")
                            rightImageSource: Calib.busy
                                ? ("image://frames/calib_right?" + Calib.previewTick)
                                : ""
                            onPreviewHeightDragged: (h) => { root.charucoPreviewHeight = h }
                            onResizeActiveChanged: (active) => { root.charucoResizeActive = active }
                        }
                    }

                    AppCard {
                        Layout.fillWidth: true
                        title: qsTr("Vidéos")
                        iconName: "video"
                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: Theme.spaceMd

                            AppLabel {
                                Layout.fillWidth: true
                                muted: true
                                wrapMode: Text.WordWrap
                                // Ne pas affirmer « issues de la synchronisation »
                                // pour une paire choisie a la main sur le PC.
                                text: !Calib.bothVideosSelected
                                    ? qsTr("Sélectionnez les deux vidéos stéréo, ou terminez d'abord la synchronisation flash.")
                                    : (Calib.videosFromSync
                                        ? qsTr("Vidéos issues de la synchronisation · décalage %1%2.")
                                            .arg(Calib.syncOffsetLabel)
                                            .arg(Calib.syncFramesLabel.length > 0 ? " · " + Calib.syncFramesLabel : "")
                                        : qsTr("Vidéos choisies à la main sur le PC (hors synchronisation)."))
                            }

                            RowLayout {
                                Layout.fillWidth: true
                                spacing: Theme.spaceMd

                                ColumnLayout {
                                    spacing: Theme.spaceXs
                                    Layout.fillWidth: true
                                    AppLabel {
                                        text: qsTr("Vidéo gauche")
                                        font.pixelSize: Theme.fontCaption
                                        color: Theme.textMuted
                                    }
                                    AppLabel {
                                        text: Calib.leftVideo ? Calib.leftVideo : qsTr("Aucun fichier")
                                        color: Theme.textMuted
                                        elide: Text.ElideMiddle
                                        Layout.fillWidth: true
                                        font.pixelSize: Theme.fontCaption
                                    }
                                }

                                ColumnLayout {
                                    spacing: Theme.spaceXs
                                    Layout.fillWidth: true
                                    AppLabel {
                                        text: qsTr("Vidéo droite")
                                        font.pixelSize: Theme.fontCaption
                                        color: Theme.textMuted
                                    }
                                    AppLabel {
                                        text: Calib.rightVideo ? Calib.rightVideo : qsTr("Aucun fichier")
                                        color: Theme.textMuted
                                        elide: Text.ElideMiddle
                                        Layout.fillWidth: true
                                        font.pixelSize: Theme.fontCaption
                                    }
                                }
                            }

                            Item {
                                id: videoPreviewHost
                                Layout.fillWidth: true
                                Layout.preferredHeight: videoPreviewRow.height
                                visible: Calib.bothVideosSelected && !Calib.busy
                                clip: true

                                readonly property real videoSlotWidth: Math.max(
                                    120,
                                    Math.floor((contentCol.width - 6 - Theme.spaceMd) / 2))

                                RowLayout {
                                    id: videoPreviewRow
                                    anchors.horizontalCenter: parent.horizontalCenter
                                    spacing: Theme.spaceMd

                                    ColumnLayout {
                                        spacing: Theme.spaceXs
                                        AppLabel {
                                            text: qsTr("Aperçu gauche")
                                            font.pixelSize: Theme.fontCaption
                                            color: Theme.textMuted
                                            Layout.alignment: Qt.AlignHCenter
                                        }
                                        Item {
                                            width: Math.min(Theme.previewWidth, videoPreviewHost.videoSlotWidth)
                                            height: Math.round(width * 9 / 16)
                                            SyncVideoPlayer {
                                                anchors.fill: parent
                                                videoPath: Calib.leftVideo
                                                fps: 30
                                                frameCount: 20000
                                                frame: 0
                                                inFrame: 0
                                                outFrame: 19999
                                                playing: false
                                            }
                                        }
                                    }

                                    Rectangle {
                                        Layout.preferredWidth: 6
                                        Layout.preferredHeight: 180
                                        Layout.alignment: Qt.AlignVCenter
                                        color: Theme.border
                                    }

                                    ColumnLayout {
                                        spacing: Theme.spaceXs
                                        AppLabel {
                                            text: qsTr("Aperçu droite")
                                            font.pixelSize: Theme.fontCaption
                                            color: Theme.textMuted
                                            Layout.alignment: Qt.AlignHCenter
                                        }
                                        Item {
                                            width: Math.min(Theme.previewWidth, videoPreviewHost.videoSlotWidth)
                                            height: Math.round(width * 9 / 16)
                                            SyncVideoPlayer {
                                                anchors.fill: parent
                                                videoPath: Calib.rightVideo
                                                fps: 30
                                                frameCount: 20000
                                                frame: 0
                                                inFrame: 0
                                                outFrame: 19999
                                                playing: false
                                            }
                                        }
                                    }
                                }
                            }

                            AppLabel {
                                Layout.fillWidth: true
                                visible: Calib.busy
                                muted: true
                                wrapMode: Text.WordWrap
                                font.pixelSize: Theme.fontCaption
                                text: qsTr("Pendant la calibration, l'aperçu ChArUco (coins détectés) remplace le lecteur vidéo.")
                            }
                        }
                    }

                    Item {
                        Layout.fillWidth: true
                        Layout.preferredHeight: Theme.spaceLg
                    }
                }
            }

            Item {
                id: resultsPane
                SplitView.preferredWidth: 340
                SplitView.minimumWidth: 280
                SplitView.maximumWidth: 500
                clip: true

                CalibResultsPanel {
                    anchors.fill: parent
                }
            }
        }

        ConsolePanel {
            Layout.fillWidth: true
            Layout.preferredWidth: root.consoleWidth
            Layout.maximumWidth: root.consoleWidth
            Layout.alignment: Qt.AlignHCenter
            bodyHeight: 200
            logModel: Calib.logs
            autoScroll: true
            openLogsFolder: function() { Calib.openCalibLogsFolder() }
        }
    }
}
