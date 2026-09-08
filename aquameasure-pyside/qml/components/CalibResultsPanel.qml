import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import AquaMeasure

Rectangle {
    id: root

    property bool expanded: true
    property bool showHeader: true

    readonly property bool hasResults: Calib.hasSavedCalibration
        || Calib.rmseStereo >= 0
        || Calib.rmseLeft >= 0
        || Calib.rmseRight >= 0
        || Calib.busy

    readonly property int headerChromeHeight: 58
    readonly property int headerTopInset: Theme.s5

    anchors.fill: parent
    color: Theme.panel

    Rectangle {
        anchors.left: parent.left
        width: 1
        height: parent.height
        color: Theme.border
    }

    Connections {
        target: Calib
        function onCalibrationComplete() {
            root.expanded = true
        }
    }

    Item {
        id: headerChrome
        visible: root.showHeader
        anchors.top: parent.top
        anchors.left: parent.left
        anchors.right: parent.right
        height: root.showHeader ? root.headerChromeHeight : 0

        Rectangle {
            id: topRule
            anchors.top: parent.top
            width: parent.width
            height: 1
            color: Theme.border
        }

        RowLayout {
            id: headerActions
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: topRule.bottom
            anchors.topMargin: root.headerTopInset
            anchors.leftMargin: Theme.s3
            anchors.rightMargin: Theme.s3
            spacing: Theme.s2

            Item {
                Layout.preferredWidth: 18
                Layout.preferredHeight: 18
                rotation: root.expanded ? 0 : -90
                Text {
                    anchors.centerIn: parent
                    text: "\u25BC"
                    font.pixelSize: 9
                    color: Theme.textMuted
                }
            }

            ColumnLayout {
                Layout.fillWidth: true
                spacing: Theme.s1
                AppLabel {
                    text: qsTr("Résultat calibration")
                    font.pixelSize: Theme.fontCaption
                    color: Theme.textMuted
                    font.weight: Font.DemiBold
                }
                AppLabel {
                    text: Calib.rmseStereo >= 0
                        ? qsTr("RMSE stéréo %1 px").arg(Calib.rmseStereo.toFixed(3))
                        : (Calib.busy ? qsTr("Calibration en cours…") : qsTr("Aucun résultat"))
                    font.pixelSize: Theme.fzXs
                    color: Calib.rmseStereo >= 0 ? Calib.rmseQualityColor(Calib.rmseStereo) : Theme.textDim
                    font.family: Theme.monoFamily
                }
            }

            InfoDot {
                id: resultsInfo
                text: qsTr("Bilan de la dernière calibration : de combien l'application se trompe (RMSE), à quelle distance sont les deux caméras et comment elles sont orientées. Vérifiez ces chiffres avant de mesurer : une calibration douteuse fausse toutes les longueurs.")
            }

            GhostButton {
                id: reloadBtn
                text: qsTr("Actualiser")
                small: true
                tooltipText: qsTr("Relit la calibration enregistrée sur le disque.")
                onClicked: Calib.reloadSavedCalibration()
            }

            GhostButton {
                id: collapseBtn
                text: root.expanded ? qsTr("Réduire") : qsTr("Afficher")
                small: true
                onClicked: root.expanded = !root.expanded
            }
        }

        MouseArea {
            anchors.left: parent.left
            anchors.top: topRule.bottom
            anchors.bottom: parent.bottom
            width: Math.max(0, headerActions.width - resultsInfo.width
                               - reloadBtn.width - collapseBtn.width - Theme.s2 * 3)
            cursorShape: Qt.PointingHandCursor
            onClicked: root.expanded = !root.expanded
        }
    }

    Item {
        id: bodyPanel
        anchors.top: headerChrome.bottom
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: root.expanded ? parent.bottom : undefined
        height: root.expanded ? undefined : 0
        visible: root.expanded
        clip: true

        ScrollView {
            id: resultsScroll
            anchors.fill: parent
            clip: true
            ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
            contentWidth: availableWidth

            ColumnLayout {
                width: Math.max(0, resultsScroll.availableWidth - Theme.s4 * 2)
                x: Theme.s4
                spacing: Theme.spaceMd

                Item { Layout.preferredHeight: Theme.s2 }

                Rectangle {
                    Layout.fillWidth: true
                    implicitHeight: qualityBannerCol.implicitHeight + Theme.spaceLg * 2
                    radius: Theme.radiusSm
                    color: Calib.overallQualityBg
                    border.color: Calib.overallQualityColor
                    border.width: 1
                    visible: Calib.overallQualityTier > 0 && !Calib.busy

                    ColumnLayout {
                        id: qualityBannerCol
                        anchors.fill: parent
                        anchors.margins: Theme.spaceLg
                        spacing: Theme.spaceSm

                        RowLayout {
                            spacing: Theme.spaceSm
                            Rectangle {
                                width: 10
                                height: 10
                                radius: 5
                                color: Calib.overallQualityColor
                            }
                            AppLabel {
                                text: qsTr("Qualité globale : %1").arg(Calib.overallQualityLabel)
                                color: Calib.overallQualityColor
                                font.weight: Font.DemiBold
                                font.pixelSize: Theme.fontSubtitle
                            }
                        }
                        AppLabel {
                            Layout.fillWidth: true
                            text: Calib.overallQualityHint
                            wrapMode: Text.WordWrap
                            font.pixelSize: Theme.fontCaption
                            color: Theme.text
                        }
                        AppLabel {
                            Layout.fillWidth: true
                            visible: Calib.detectionsLeft >= 0
                            text: qsTr("Scan : %1 détections G · %2 D · %3 paires sync")
                                .arg(Calib.detectionsLeft)
                                .arg(Calib.detectionsRight)
                                .arg(Calib.pairsSynced)
                            font.pixelSize: Theme.fontCaption
                            color: Theme.textMuted
                        }
                    }
                }

                AppLabel {
                    Layout.fillWidth: true
                    Layout.topMargin: Theme.s2
                    muted: true
                    wrapMode: Text.WordWrap
                    font.pixelSize: Theme.fontCaption
                    text: qsTr("Enregistrement automatique dans camera_parameters/ (retrouvé au prochain lancement).")
                }

                AppLabel {
                    Layout.fillWidth: true
                    Layout.topMargin: Theme.s1
                    text: Calib.busy
                        ? qsTr("Calibration en cours…")
                        : Calib.calibSummary
                    color: Theme.text
                    font.pixelSize: Theme.fontBody
                    wrapMode: Text.WordWrap
                    visible: Calib.busy || Calib.calibSummary !== ""
                }

                ColumnLayout {
                    Layout.fillWidth: true
                    Layout.topMargin: Theme.spaceMd
                    spacing: Theme.spaceSm

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: Theme.s2

                        AppLabel {
                            Layout.fillWidth: true
                            text: qsTr("Erreur de la calibration (RMSE)")
                            font.weight: Font.DemiBold
                            color: Theme.textMuted
                            font.pixelSize: Theme.fontCaption
                        }

                        InfoDot {
                            diameter: 14
                            text: qsTr("Le RMSE dit de combien l'application se trompe quand elle replace les cases de la mire sur l'image : c'est une erreur moyenne, en pixels. Plus il est petit, mieux c'est. Les deux premières lignes concernent chaque caméra prise seule ; la ligne « Stéréo » concerne la paire, c'est elle qui compte pour la mesure.")
                        }
                    }

                    Item { Layout.preferredHeight: Theme.s1 }

                    RmseQualityRow {
                        Layout.fillWidth: true
                        title: qsTr("Caméra gauche")
                        rmse: Calib.rmseLeft
                        computing: Calib.busy && Calib.workStage === 2 && Calib.rmseLeft < 0
                    }
                    RmseQualityRow {
                        Layout.fillWidth: true
                        title: qsTr("Caméra droite")
                        rmse: Calib.rmseRight
                        computing: Calib.busy && Calib.workStage === 3 && Calib.rmseRight < 0
                    }
                    RmseQualityRow {
                        Layout.fillWidth: true
                        title: qsTr("Stéréo")
                        rmse: Calib.rmseStereo
                        computing: Calib.busy && Calib.workStage === 4 && Calib.rmseStereo < 0
                    }
                }

                Rectangle {
                    Layout.fillWidth: true
                    Layout.topMargin: Theme.spaceMd
                    Layout.bottomMargin: Theme.s2
                    height: 1
                    color: Theme.border
                }

                GridLayout {
                    Layout.fillWidth: true
                    columns: 2
                    columnSpacing: Theme.spaceMd
                    rowSpacing: Theme.spaceSm

                    RowLayout {
                        spacing: Theme.s1
                        AppLabel {
                            text: qsTr("Écartement caméras")
                            color: Theme.textMuted
                            font.pixelSize: Theme.fontCaption
                        }
                        InfoDot {
                            diameter: 14
                            text: qsTr("Distance entre les deux objectifs, telle que la calibration l'a retrouvée. Comparez-la à la distance réelle sur le caisson : si l'écart dépasse quelques millimètres, la calibration est ratée et il faut la refaire.")
                        }
                    }
                    Text {
                        text: Calib.baselineMm >= 0
                            ? qsTr("%1 mm").arg(Calib.baselineMm.toFixed(2))
                            : "-"
                        font.family: Theme.monoFamily
                        font.pixelSize: Theme.fzSm
                        color: Theme.text
                    }
                    AppLabel {
                        text: qsTr("Rotation relative")
                        color: Theme.textMuted
                        font.pixelSize: Theme.fontCaption
                    }
                    Text {
                        text: Calib.rotationDeg >= 0
                            ? qsTr("%1°").arg(Calib.rotationDeg.toFixed(2))
                            : "-"
                        font.family: Theme.monoFamily
                        font.pixelSize: Theme.fzSm
                        color: Theme.text
                    }
                    AppLabel {
                        text: qsTr("Focale G / D")
                        color: Theme.textMuted
                        font.pixelSize: Theme.fontCaption
                    }
                    Text {
                        text: (Calib.focalLeftPx >= 0 && Calib.focalRightPx >= 0)
                            ? qsTr("%1 / %2 px").arg(Calib.focalLeftPx.toFixed(1)).arg(Calib.focalRightPx.toFixed(1))
                            : "-"
                        font.family: Theme.monoFamily
                        font.pixelSize: Theme.fzSm
                        color: Theme.text
                    }
                }

                AppLabel {
                    Layout.fillWidth: true
                    Layout.topMargin: Theme.spaceMd
                    muted: true
                    wrapMode: Text.WordWrap
                    font.pixelSize: Theme.fzXs
                    text: qsTr("Qualité RMSE : <1 px excellent · <3 bon · <5 correct · <15 limite · ≥15 insuffisant")
                }

                PrimaryButton {
                    objectName: "calibrationGoToMeasureButton"
                    Layout.fillWidth: true
                    Layout.topMargin: Theme.spaceLg
                    Layout.bottomMargin: Theme.spaceLg
                    text: qsTr("Passer à la mesure →")
                    requires: Calib.hasSavedCalibration && !Calib.busy
                    disabledReason: Calib.busy
                        ? qsTr("Calibration en cours - attendez le résultat.")
                        : qsTr("Aucune calibration enregistrée : lancez « Lancer la calibration » dans le panneau de gauche. Sans elle, les longueurs ne peuvent pas être calculées.")
                    tooltipText: qsTr("Ouvre la page Mesure avec cette paire de vidéos et cette calibration.")
                    onClicked: {
                        Measure.refresh(Calib.leftVideo, Calib.rightVideo)
                        App.currentPage = 4
                    }
                }
            }
        }
    }
}
