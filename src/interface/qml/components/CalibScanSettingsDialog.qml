import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import AquaMeasure

Popup {
    id: dlg
    objectName: "calibrationSettingsDialog"
    parent: Overlay.overlay
    modal: true
    focus: true
    closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
    padding: width < 420 ? Theme.s3 : Theme.s4
    width: Math.min(560, Overlay.overlay ? Overlay.overlay.width - 32 : 560)
    height: Math.min(dialogContent.implicitHeight + padding * 2,
                     Overlay.overlay ? Overlay.overlay.height - 32 : 840)
    x: Overlay.overlay ? Math.max(0, (Overlay.overlay.width - width) / 2) : 0
    y: Overlay.overlay ? Math.max(0, (Overlay.overlay.height - height) / 2) : 0

    property bool blocked: Calib.busy

    readonly property int scanSpanPerCam: {
        const spanL = Sync.leftFrameCount > 0
            ? Math.max(0, (Sync.leftOutFrame > 0 ? Sync.leftOutFrame : Sync.leftFrameCount - 1) - Sync.leftInFrame + 1) : 0
        const spanR = Sync.rightFrameCount > 0
            ? Math.max(0, (Sync.rightOutFrame > 0 ? Sync.rightOutFrame : Sync.rightFrameCount - 1) - Sync.rightInFrame + 1) : 0
        if (spanL <= 0) return spanR
        if (spanR <= 0) return spanL
        return Math.min(spanL, spanR)
    }
    readonly property int scanProbesV2: scanSpanPerCam > 0
        ? Math.ceil(scanSpanPerCam / Math.max(1, Settings.calibScanStride)) : 0
    readonly property bool trimSpanLong: scanSpanPerCam > 9000
    // Le résumé apporte la dépendance aux réglages : l'appel au slot seul
    // ne réévaluait l'estimation qu'après un changement de plage vidéo.
    readonly property string durationDetails: Settings.calibSettingsSummary.length > 0
        ? Settings.calibrationDurationHint(scanSpanPerCam) : ""

    background: Rectangle {
        radius: Theme.radiusMd
        color: Theme.panel
        border.color: Theme.border2
    }

    contentItem: ColumnLayout {
        id: dialogContent
        spacing: Theme.s3

        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.s2
            WorkspaceIcon { name: "settings"; implicitWidth: 22; implicitHeight: 22 }
            AppLabel {
                Layout.fillWidth: true
                Layout.minimumWidth: 0
                text: qsTr("Paramètres de calibration")
                font.pixelSize: Theme.fontTitle
                font.weight: Font.DemiBold
                wrapMode: Text.WordWrap
            }
            Button {
                id: closeIcon
                objectName: "calibrationSettingsCloseIcon"
                implicitWidth: 32
                implicitHeight: 32
                padding: 8
                enabled: !dlg.blocked
                Accessible.name: qsTr("Fermer les paramètres de calibration")
                contentItem: WorkspaceIcon { name: "close"; tint: Theme.textMuted }
                background: Rectangle {
                    radius: Theme.radiusSm
                    color: closeIcon.hovered ? Theme.surfaceHover : Theme.panel2
                    border.color: closeIcon.activeFocus ? Theme.accent : Theme.border2
                }
                onClicked: dlg.close()
            }
        }

        ColumnLayout {
            Layout.fillWidth: true
            spacing: Theme.s2
            RowLayout {
                Layout.fillWidth: true
                AppLabel {
                    text: qsTr("Préréglage général")
                    font.weight: Font.DemiBold
                    font.pixelSize: Theme.fzSm
                }
                AppLabel {
                    Layout.fillWidth: true
                    Layout.minimumWidth: 0
                    text: Settings.calibQualityPresetLabel
                    color: Theme.accentText
                    font.pixelSize: Theme.fzXs
                    horizontalAlignment: Text.AlignRight
                    elide: Text.ElideRight
                }
                InfoDot { text: Settings.calibSettingsSummary }
            }
            RowLayout {
                Layout.fillWidth: true
                spacing: Theme.s2
                Repeater {
                    model: [
                        { key: "fast", label: qsTr("Rapide") },
                        { key: "standard", label: qsTr("Standard") },
                        { key: "precise", label: qsTr("Précis") }
                    ]
                    delegate: AppButton {
                        objectName: "calibrationPreset_" + modelData.key
                        Layout.fillWidth: true
                        Layout.preferredWidth: 0
                        text: modelData.label
                        leftPadding: Theme.s2
                        rightPadding: Theme.s2
                        primary: Settings.calibQualityPreset === modelData.key
                        enabled: !dlg.blocked
                        onClicked: Settings.applyCalibQualityPreset(modelData.key)
                    }
                }
            }
        }

        ScrollView {
            id: scroll
            objectName: "calibrationSettingsScroll"
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.minimumHeight: 0
            implicitHeight: settingsContent.implicitHeight
            clip: true
            contentWidth: availableWidth
            ScrollBar.horizontal.policy: ScrollBar.AlwaysOff

            ColumnLayout {
                id: settingsContent
                width: scroll.availableWidth
                spacing: Theme.s3

                SectionSurface {
                    objectName: "calibrationScanSection"
                    title: qsTr("Recherche de la mire")
                    iconName: "detect"
                    color: Theme.elevated
                    RowLayout {
                        Layout.fillWidth: true
                        AppLabel {
                            Layout.fillWidth: true
                            Layout.minimumWidth: 0
                            text: qsTr("Plus d’images analysées = un scan plus long.")
                            color: Theme.textMuted
                            font.pixelSize: Theme.fzXs
                            wrapMode: Text.WordWrap
                        }
                        InfoDot {
                            text: qsTr("Les deux caméras sont analysées ensemble. Sans mire, le scan espace les analyses ; après une détection, il examine les images suivantes en rafale. Les détections sont ensuite réparties dans la séquence pour le calcul stéréo.")
                        }
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: Theme.s1
                        Repeater {
                            model: [
                                { key: "sparse", label: qsTr("Économe") },
                                { key: "balanced", label: qsTr("Équilibré") },
                                { key: "detailed", label: qsTr("Détaillé") }
                            ]
                            delegate: AppButton {
                                objectName: "calibrationDensity_" + modelData.key
                                Layout.fillWidth: true
                                Layout.preferredWidth: 0
                                text: modelData.label
                                leftPadding: Theme.s2
                                rightPadding: Theme.s2
                                enabled: !dlg.blocked
                                onClicked: Settings.applyScanDensityPreset(modelData.key)
                            }
                        }
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: Theme.s2
                        GhostButton {
                            objectName: "calibrationDensityLess"
                            Layout.fillWidth: true
                            Layout.preferredWidth: 0
                            text: qsTr("− d’images")
                            small: true
                            enabled: !dlg.blocked
                            tooltipText: qsTr("Espace les analyses et raccourcit la rafale.")
                            onClicked: Settings.adjustScanDensity(-1)
                        }
                        GhostButton {
                            objectName: "calibrationDensityMore"
                            Layout.fillWidth: true
                            Layout.preferredWidth: 0
                            text: qsTr("+ d’images")
                            small: true
                            enabled: !dlg.blocked
                            tooltipText: qsTr("Rapproche les analyses et prolonge la rafale.")
                            onClicked: Settings.adjustScanDensity(1)
                        }
                    }
                    CalibScanFieldRow {
                        objectName: "calibrationScanStride"
                        label: qsTr("Saut sans mire (images)")
                        hint: qsTr("Une analyse toutes les N images tant que la mire n'est pas détectée.")
                        fieldEnabled: !dlg.blocked
                        spinFrom: 3; spinTo: 15
                        spinValue: Settings.calibScanStride
                        onValueModified: function(v) { Settings.calibScanStride = v }
                    }
                    CalibScanFieldRow {
                        objectName: "calibrationDenseWindow"
                        label: qsTr("Analyses après détection")
                        hint: qsTr("Nombre d'analyses en rafale après chaque détection de la mire.")
                        fieldEnabled: !dlg.blocked
                        spinFrom: 10; spinTo: 60
                        spinValue: Settings.calibDenseWindow
                        onValueModified: function(v) { Settings.calibDenseWindow = v }
                    }
                    CalibScanFieldRow {
                        objectName: "calibrationDenseStride"
                        label: qsTr("Pas en rafale (images)")
                        hint: qsTr("1 : chaque image. 3 : une image sur trois. Les images gauche et droite restent synchronisées.")
                        fieldEnabled: !dlg.blocked
                        spinFrom: 1; spinTo: 10
                        spinValue: Settings.calibDenseStride
                        onValueModified: function(v) { Settings.calibDenseStride = v }
                    }
                }

                SectionSurface {
                    objectName: "calibrationCalculationSection"
                    title: qsTr("Calcul stéréo")
                    iconName: "calibrate"
                    color: Theme.elevated
                    AppLabel {
                        Layout.fillWidth: true
                        text: qsTr("Images retenues pour calculer la calibration.")
                        color: Theme.textMuted
                        font.pixelSize: Theme.fzXs
                        wrapMode: Text.WordWrap
                    }
                    CalibScanFieldRow {
                        objectName: "calibrationMaxViews"
                        label: qsTr("Vues par caméra (max.)")
                        hint: qsTr("Nombre maximal d'images utilisées pour calibrer chaque caméra.")
                        fieldEnabled: !dlg.blocked
                        spinFrom: 20; spinTo: 300
                        spinValue: Settings.maxCalibViews
                        onValueModified: function(v) { Settings.maxCalibViews = v }
                    }
                    CalibScanFieldRow {
                        objectName: "calibrationMaxPairs"
                        label: qsTr("Paires stéréo (max.)")
                        hint: qsTr("Nombre maximal de paires gauche/droite retenues pour le calcul stéréo.")
                        fieldEnabled: !dlg.blocked
                        spinFrom: 10; spinTo: 500
                        spinValue: Settings.maxStereoPairs
                        onValueModified: function(v) { Settings.maxStereoPairs = v }
                    }
                    CalibScanFieldRow {
                        objectName: "calibrationMinCorners"
                        label: qsTr("Coins ChArUco (min.)")
                        hint: qsTr("Nombre minimal de coins de la mire détectés pour retenir une image.")
                        fieldEnabled: !dlg.blocked
                        spinFrom: 4; spinTo: 30
                        spinValue: Settings.charucoMinCorners
                        onValueModified: function(v) { Settings.charucoMinCorners = v }
                    }
                }

                SectionSurface {
                    objectName: "calibrationRigSection"
                    title: qsTr("Montage des caméras")
                    iconName: "ruler"
                    color: Theme.elevated
                    CalibScanFieldRow {
                        objectName: "calibrationBaseline"
                        label: qsTr("Écartement (mm)")
                        hint: qsTr("Écartement nominal des deux caméras sur le montage. Valeur par défaut : 800 mm.")
                        fieldEnabled: !dlg.blocked
                        spinFrom: 300; spinTo: 1200; spinStep: 10
                        spinValue: Math.round(Settings.stereoNominalBaselineMm)
                        onValueModified: function(v) { Settings.stereoNominalBaselineMm = v }
                    }
                }

                RowLayout {
                    Layout.fillWidth: true
                    spacing: Theme.s2
                    WorkspaceIcon { name: "clock"; tint: Theme.textDim; implicitWidth: 16; implicitHeight: 16 }
                    AppLabel {
                        Layout.fillWidth: true
                        Layout.minimumWidth: 0
                        text: dlg.scanProbesV2 > 0
                            ? qsTr("Au moins %1 instants à analyser.").arg(dlg.scanProbesV2)
                            : qsTr("Chargez les vidéos pour estimer le temps.")
                        color: Theme.textDim
                        font.pixelSize: Theme.fzXs
                        wrapMode: Text.WordWrap
                    }
                    InfoDot { text: dlg.durationDetails }
                }
                AppLabel {
                    Layout.fillWidth: true
                    visible: dlg.trimSpanLong
                    text: qsTr("Séquence longue : resserrez In / Out dans Synchronisation pour réduire le temps d’analyse.")
                    color: Theme.warn
                    font.pixelSize: Theme.fzXs
                    wrapMode: Text.WordWrap
                }
            }
        }

        Rectangle { Layout.fillWidth: true; implicitHeight: 1; color: Theme.border2 }
        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.s2
            AppButton {
                objectName: "calibrationSettingsDefaults"
                Layout.fillWidth: true
                Layout.preferredWidth: 0
                text: qsTr("Par défaut")
                enabled: !dlg.blocked
                onClicked: Settings.resetCalibScanToDefaults()
            }
            AppButton {
                objectName: "calibrationSettingsClose"
                Layout.fillWidth: true
                Layout.preferredWidth: 0
                text: qsTr("Fermer")
                primary: true
                enabled: !dlg.blocked
                onClicked: dlg.close()
            }
        }
    }
}
