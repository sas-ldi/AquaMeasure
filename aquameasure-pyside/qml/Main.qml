import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Window
import AquaMeasure

ApplicationWindow {
    id: win
    property int pageBeforePreferences: 0
    minimumWidth:  1280
    minimumHeight: 800
    visibility: Window.Maximized
    visible: true
    title: "AquaMeasure"
    color: Theme.bg

    // Dialogues chargés à la demande (évite conflits Overlay / layer au démarrage).
    Loader {
        id: aboutDlgLoader
        active: false
        sourceComponent: AboutDialog {}
    }

    function openAboutDialog() {
        if (!aboutDlgLoader.active)
            aboutDlgLoader.active = true
        aboutDlgLoader.item.open()
    }

    Loader {
        id: charucoDlgLoader
        active: false
        sourceComponent: CalibCharucoSettingsDialog {}
    }
    Loader {
        id: printDlgLoader
        active: false
        sourceComponent: PrintCharucoDialog {}
    }

    Loader {
        id: calibScanDlgLoader
        active: false
        sourceComponent: CalibScanSettingsDialog {}
    }

    Loader {
        id: detectorDlgLoader
        active: false
        sourceComponent: DetectorManagerDialog {}
    }

    function openDetectorManager() {
        if (!detectorDlgLoader.active)
            detectorDlgLoader.active = true
        detectorDlgLoader.item.open()
    }

    function openPreferences() {
        if (App.currentPage !== 8)
            pageBeforePreferences = App.currentPage
        App.currentPage = 8
    }

    function closePreferences() {
        App.currentPage = pageBeforePreferences === 8 ? 0 : pageBeforePreferences
    }

    // « Qui annote ? » : posé une seule fois, au premier lancement - sans quoi
    // les annotations repartiraient anonymes (author = 'operator' en dur).
    Loader {
        id: annotatorDlgLoader
        active: false
        sourceComponent: AnnotatorDialog {}
    }

    function openAnnotatorDialog(allowCancel) {
        if (!annotatorDlgLoader.active)
            annotatorDlgLoader.active = true
        annotatorDlgLoader.item.allowCancel = allowCancel === true
        annotatorDlgLoader.item.open()
    }

    Timer {
        // Après le premier rendu : ouvrir un modal pendant la construction de
        // la fenêtre laisse l'Overlay dans un état instable.
        interval: 600
        running: true
        repeat: false
        onTriggered: {
            if (Annotator.needsIdentity)
                win.openAnnotatorDialog(false)
        }
    }

    function openCharucoSettingsDialog() {
        if (!charucoDlgLoader.active)
            charucoDlgLoader.active = true
        charucoDlgLoader.item.open()
    }

    function openCalibScanSettingsDialog() {
        if (!calibScanDlgLoader.active)
            calibScanDlgLoader.active = true
        calibScanDlgLoader.item.open()
    }

    Connections {
        target: App
        function onOpenCharucoSettingsRequested() { win.openCharucoSettingsDialog() }
        function onOpenCalibScanSettingsRequested() { win.openCalibScanSettingsDialog() }
    }

    Connections {
        target: Detectors
        function onOpenManagerRequested() { win.openDetectorManager() }
    }

    Connections {
        target: Annotator
        function onOpenDialogRequested(allowCancel) {
            win.openAnnotatorDialog(allowCancel)
        }
    }

    // ── Palette système ────────────────────────────────────
    palette {
        window:          Theme.bg
        windowText:      Theme.text
        base:            Theme.panel2
        alternateBase:   Theme.surfaceHover
        text:            Theme.text
        button:          Theme.panel
        buttonText:      Theme.text
        highlight:       Theme.accent
        highlightedText: "#ffffff"
        link:            Theme.accentText
        placeholderText: Theme.textDim
    }

    font.family:    Theme.fontFamily
    font.pixelSize: Theme.fzBase

    ListModel { id: appTabs }

    function rebuildTabs() {
        appTabs.clear()
        // L'onglet Sessions suit l'Accueil (c'est par là qu'on entre dans une
        // sortie) ; son numéro de page reste 7 pour ne pas décaler les autres.
        const items = [
            [qsTr("Accueil"), 0],
            [qsTr("Sessions"), 7],
            [qsTr("Machine"), 1],
            [qsTr("Synchronisation"), 2],
            [qsTr("Calibration"), 3],
            [qsTr("Mesure"), 4],
            [qsTr("Données & IA"), 5]
        ]
        for (let i = 0; i < items.length; i++)
            appTabs.append({ name: items[i][0], page: items[i][1] })
        if (Settings.proMode)
            appTabs.append({ name: qsTr("Hub Pro"), page: 6 })
    }

    Component.onCompleted: rebuildTabs()

    Connections {
        target: Settings
        function onProModeChanged() {
            win.rebuildTabs()
            if (!Settings.proMode && App.currentPage === 6)
                App.currentPage = 5
        }
    }

    // ══════════════════════════════════════════════════════
    //  BARRE DE MENU
    // ══════════════════════════════════════════════════════
    menuBar: MenuBar {
        id: menuBar
        leftPadding:  80    // réserve la place pour le logo IRD à gauche
        rightPadding: 220   // réserve la place pour le statut machine à droite

        background: Rectangle {
            color: Theme.panel
            Rectangle {
                anchors.bottom: parent.bottom
                width: parent.width; height: 1
                color: Theme.border
            }
            // Logo IRD à gauche
            Image {
                anchors.left:           parent.left
                anchors.leftMargin:     Theme.s3
                anchors.verticalCenter: parent.verticalCenter
                source: typeof AppLogoUrl !== "undefined" ? AppLogoUrl : ""
                height: 28
                width:  28 * (sourceSize.width / Math.max(1, sourceSize.height))
                fillMode: Image.PreserveAspectFit
                smooth: true
                mipmap: true
            }
            // Statut machine à droite
            RowLayout {
                anchors.right:           parent.right
                anchors.rightMargin:     Theme.s4
                anchors.verticalCenter:  parent.verticalCenter
                spacing: Theme.s4
                RowLayout {
                    spacing: Theme.s2
                    Rectangle {
                        width: 7; height: 7; radius: 3.5
                        color: Device.connected ? Theme.ok : Theme.textDim
                    }
                    Text {
                        text: Device.connected
                            ? qsTr("Machine · %1").arg(Device.portName)
                            : qsTr("Machine · déconnectée")
                        color: Theme.textMuted
                        font.family: Theme.fontFamily
                        font.pixelSize: Theme.fzSm
                    }
                }
                Row {
                    spacing: Theme.s2
                    visible: App.syncOk
                    Rectangle { width: 6; height: 6; radius: 3; color: Theme.ok; anchors.verticalCenter: parent.verticalCenter }
                    Text {
                        text: "Sync OK"
                        color: Theme.ok
                        font.family: Theme.fontFamily
                        font.pixelSize: Theme.fzSm
                    }
                }
            }
        }

        // délégué de colonne (items de menu)
        delegate: MenuBarItem {
            id: mbi
            // Les commandes de détection n'ont de sens que devant la vidéo.
            // Masquer le menu ailleurs évite de présenter une barre globale
            // pleine d'actions inopérantes.
            visible: mbi.text !== qsTr("Détection & suivi") || App.currentPage === 4
            leftPadding:  Theme.s3
            rightPadding: Theme.s3
            topPadding:   6
            bottomPadding: 6
            contentItem: Text {
                text:  mbi.text
                font.family:    Theme.fontFamily
                font.pixelSize: Theme.fzBase
                color: mbi.highlighted ? Theme.accentText : Theme.textMuted
                verticalAlignment: Text.AlignVCenter
            }
            background: Rectangle {
                implicitHeight: Theme.menuBarH
                radius: Theme.radiusSm
                color: mbi.highlighted ? Theme.accentSoft : "transparent"
            }
        }

        // ── Fichier ──
        Menu {
            title: qsTr("Fichier")
            padding: Theme.s2
            implicitWidth: 360
            background: Rectangle { color: Theme.elevated; radius: Theme.radiusSm; border.color: Theme.border; border.width: 1 }
            delegate: AMMenuItem { }

            AMMenuItem {
                text: qsTr("Charger les vidéos de la Synchronisation")
                visible: App.currentPage === 4
                onTriggered: Measure.loadVideosFromSync()
            }
            AMMenuItem {
                text: qsTr("Choisir la vidéo gauche…")
                visible: App.currentPage === 4
                onTriggered: Measure.pickLeftVideo()
            }
            AMMenuItem {
                text: qsTr("Choisir la vidéo droite…")
                visible: App.currentPage === 4
                onTriggered: Measure.pickRightVideo()
            }
            AMMenuSeparator {
                visible: App.currentPage === 4
            }
            AMMenuItem {
                // Le lieu et la date sont exigés à l'écriture : le dire ici
                // évite un clic qui échoue sans que l'on sache pourquoi.
                text: Data.busy
                    ? qsTr("Enregistrement de la session en cours…")
                    : (Data.sessionSite.length > 0 && Data.sessionDate.length > 0
                        ? qsTr("Enregistrer la session en base")
                        : qsTr("Enregistrer la session (renseignez lieu et date)"))
                enabled: Data.dbAvailable
                         && !Data.busy
                         && Data.sessionSite.length > 0
                         && Data.sessionDate.length > 0
                visible: App.currentPage === 4
                onTriggered: Data.saveSession()
            }
            AMMenuSeparator {
                visible: App.currentPage === 4
            }
            AMMenuItem {
                visible: App.currentPage === 3
                action: Action {
                    text: qsTr("Enregistrer une copie de la calibration…")
                    shortcut: "Ctrl+S"
                    onTriggered: Calib.saveCalibrationBackup()
                }
            }
            AMMenuItem {
                text: qsTr("Importer une calibration…")
                visible: App.currentPage === 3
                onTriggered: Calib.importCalibrationPackage()
            }
            AMMenuItem {
                text: qsTr("Exporter la calibration…")
                visible: App.currentPage === 3
                onTriggered: Calib.exportCalibrationPackage()
            }
            AMMenuSeparator {
                visible: App.currentPage === 3
            }
            Action { text: qsTr("Quitter"); shortcut: "Ctrl+Q"; onTriggered: Qt.quit() }
        }

        // ── Édition ──
        Menu {
            title: qsTr("Édition")
            padding: Theme.s2
            implicitWidth: 360
            background: Rectangle { color: Theme.elevated; radius: Theme.radiusSm; border.color: Theme.border; border.width: 1 }
            delegate: AMMenuItem { }
            AMMenuItem {
                text: qsTr("Réglages de la mire ChArUco…")
                visible: App.currentPage === 3
                onTriggered: App.openCharucoSettings()
            }
            AMMenuItem {
                text: qsTr("Réglages de la calibration…")
                visible: App.currentPage === 3
                onTriggered: App.openCalibScanSettings()
            }
            AMMenuSeparator {
                visible: App.currentPage === 3
            }
            AMMenuItem {
                text: qsTr("Effacer les repères de découpe (début / fin)")
                visible: App.currentPage === 2
                onTriggered: {
                    Sync.setLeftIn(0); Sync.setLeftOut(0)
                    Sync.setRightIn(0); Sync.setRightOut(0)
                }
            }
            AMMenuItem {
                text: qsTr("Effacer les points de mesure A / B")
                visible: App.currentPage === 4
                onTriggered: Measure.clearPoints()
            }
            AMMenuItem {
                text: qsTr("Recharger la calibration enregistrée")
                visible: App.currentPage === 3 || App.currentPage === 4
                onTriggered: { Measure.loadCalibration(); Calib.reloadSavedCalibration() }
            }
            AMMenuSeparator {
                visible: App.currentPage >= 2 && App.currentPage <= 4
            }
            Action {
                text: qsTr("Préférences…")
                onTriggered: win.openPreferences()
            }
        }

        // ── Affichage ──
        Menu {
            title: qsTr("Affichage")
            padding: Theme.s2
            implicitWidth: 320
            background: Rectangle { color: Theme.elevated; radius: Theme.radiusSm; border.color: Theme.border; border.width: 1 }
            delegate: AMMenuItem { }
            Action {
                text: qsTr("Plein écran"); shortcut: "F11"
                onTriggered: win.visibility === Window.FullScreen
                    ? (win.visibility = Window.Maximized)
                    : (win.visibility = Window.FullScreen)
            }
            AMMenuSeparator {
                visible: App.currentPage === 4
            }
            // Un contrôle checkable réécrit `checked` au clic, ce qui détruit
            // le binding : la coche se fige ensuite sur son dernier état. On
            // resynchronise donc explicitement depuis le contrôleur.
            AMMenuItem {
                id: epipolarItem
                text: qsTr("Lignes de contrôle (épipolaires)")
                visible: App.currentPage === 4
                checkable: true
                checked: Measure.epipolar
                onToggled: Measure.epipolar = checked
                Connections {
                    target: Measure
                    function onEpipolarChanged() { epipolarItem.checked = Measure.epipolar }
                }
            }
            AMMenuItem {
                id: trailsItem
                text: qsTr("Traces de déplacement des poissons")
                visible: App.currentPage === 4
                checkable: true
                checked: Fish.showTrails
                onToggled: Fish.showTrails = checked
                Connections {
                    target: Fish
                    function onShowTrailsChanged() { trailsItem.checked = Fish.showTrails }
                }
            }
        }

        // ── Détection & suivi ──
        // Ce menu suit le déroulé du travail : détecter · régler · suivre ·
        // compter · marquer. L'entraînement du détecteur (administration) a
        // rejoint le menu Outils, à côté du Mode Pro qu'il exige.
        Menu {
            title: qsTr("Détection & suivi")
            padding: Theme.s2
            implicitWidth: 380
            background: Rectangle { color: Theme.elevated; radius: Theme.radiusSm; border.color: Theme.border; border.width: 1 }
            delegate: AMMenuItem { }

            AMMenuItem {
                id: fishIaItem
                text: qsTr("Détection des poissons par l'IA")
                checkable: true
                checked: Fish.fishIaEnabled
                onToggled: Fish.fishIaEnabled = checked
                Connections {
                    target: Fish
                    function onFishIaEnabledChanged() { fishIaItem.checked = Fish.fishIaEnabled }
                }
            }
            AMMenuItem {
                id: autoPauseItem
                text: qsTr("Détecter à chaque pause de la vidéo")
                checkable: true
                checked: Fish.autoOnPause
                onToggled: Fish.autoOnPause = checked
                Connections {
                    target: Fish
                    function onAutoOnPauseChanged() { autoPauseItem.checked = Fish.autoOnPause }
                }
            }
            Action {
                text: qsTr("Détecter sur l'image affichée")
                enabled: Fish.fishIaEnabled && !Fish.busy && !Measure.playing
                onTriggered: Fish.detectCurrentFrame()
            }

            AMMenuSeparator { }

            // Le seuil se règle aussi dans le panneau Détection IA ; ici on
            // l'ajuste sans quitter la vidéo des yeux.
            Action {
                text: qsTr("Seuil de détection %1 % - être moins strict").arg(Math.round(Fish.confidence * 100))
                enabled: Fish.confidence > 0.101
                onTriggered: Fish.confidence = Math.max(0.05, Fish.confidence - 0.05)
            }
            Action {
                text: qsTr("Seuil de détection %1 % - être plus strict").arg(Math.round(Fish.confidence * 100))
                enabled: Fish.confidence < 0.899
                onTriggered: Fish.confidence = Math.min(0.95, Fish.confidence + 0.05)
            }

            AMMenuSeparator { visible: App.currentPage !== 4 }

            // Rattrapage d'un décrochage, rien de plus : un suivi se lance
            // depuis la fiche du poisson, par « Début du suivi (In) », pour
            // que la piste produite rejoigne bien une observation. Le menu
            // proposait aussi de démarrer un suivi, qui produisait alors une
            // piste rattachée à personne.
            AMMenuItem {
                visible: App.currentPage !== 4
                text: qsTr("Reprendre le suivi du poisson encadré")
                enabled: Fish.assistWaiting && !Fish.busy
                onTriggered: Fish.resumeAssistFromBox()
            }
            AMMenuItem {
                visible: App.currentPage !== 4
                text: qsTr("Arrêter le suivi")
                enabled: Fish.assistActive
                onTriggered: Fish.stopAssistedTracking()
            }

            AMMenuSeparator { visible: App.currentPage !== 4 }

            AMMenuItem {
                id: trackingItem
                visible: App.currentPage !== 4
                text: qsTr("Numéroter les poissons (suivi automatique)")
                checkable: true
                checked: Fish.trackingEnabled
                onToggled: Fish.trackingEnabled = checked
                Connections {
                    target: Fish
                    function onTrackingEnabledChanged() { trackingItem.checked = Fish.trackingEnabled }
                }
            }
            // « Analyser le morceau choisi (comptage) » a disparu avec la
            // section « Suivi automatique (comptage) » du volet de gauche :
            // masquée depuis longtemps, elle était le seul endroit où poser
            // les bornes que cette entrée exigeait. Restait un menu que rien
            // n'allumait, sur un troisième chemin de suivi dont le client ne
            // veut plus.
            AMMenuItem {
                visible: App.currentPage !== 4
                text: qsTr("Interrompre l'analyse en cours")
                enabled: Fish.busy
                onTriggered: Fish.cancelTracking()
            }

        }

        // ── Outils ──
        Menu {
            title: qsTr("Outils")
            padding: Theme.s2
            implicitWidth: 400
            background: Rectangle { color: Theme.elevated; radius: Theme.radiusSm; border.color: Theme.border; border.width: 1 }
            delegate: AMMenuItem { }
            AMMenuItem {
                text: qsTr("Imprimer la mire ChArUco…")
                visible: App.currentPage === 3
                onTriggered: {
                    if (!printDlgLoader.active)
                        printDlgLoader.active = true
                    printDlgLoader.item.open()
                }
            }
            AMMenuItem {
                text: qsTr("Gérer les modèles de détection…")
                visible: App.currentPage === 4 || App.currentPage === 6
                onTriggered: Detectors.openManager()
            }
            AMMenuItem {
                text: qsTr("Console série de la machine")
                visible: App.currentPage === 1
                onTriggered: App.currentPage = 1
            }
            AMMenuSeparator {
                visible: App.currentPage === 1 || App.currentPage === 3
                         || App.currentPage === 4 || App.currentPage === 6
            }
            AMMenuItem {
                id: proModeItem
                text: qsTr("Mode Pro (outils d'administration)")
                checkable: true
                checked: Settings.proMode
                onToggled: {
                    Settings.proMode = checked
                    if (checked)
                        App.currentPage = 6
                }
                Connections {
                    target: Settings
                    function onProModeChanged() { proModeItem.checked = Settings.proMode }
                }
            }
        }

        // ── Aide ──
        Menu {
            title: qsTr("Aide")
            padding: Theme.s2
            implicitWidth: 300
            background: Rectangle { color: Theme.elevated; radius: Theme.radiusSm; border.color: Theme.border; border.width: 1 }
            delegate: AMMenuItem { }
            Action {
                text: qsTr("À propos d'AquaMeasure")
                onTriggered: win.openAboutDialog()
            }
        }
    }

    // ══════════════════════════════════════════════════════
    //  ONGLETS
    // ══════════════════════════════════════════════════════
    header: Rectangle {
        implicitHeight: Theme.tabBarH
        color: Theme.panel

        Rectangle {
            anchors.bottom: parent.bottom
            width: parent.width
            height: 1
            color: Theme.border
        }

        RowLayout {
            anchors.fill: parent
            anchors.leftMargin: Theme.s4
            anchors.rightMargin: Theme.s4
            spacing: 0

            Item {
                id: tabBarStrip
                implicitHeight: Theme.tabBarH
                implicitWidth: tabRow.width

                Row {
                    id: tabRow
                    height: parent.height

                    Repeater {
                        id: tabRepeater
                        model: appTabs
                        SectionTab {
                            height: tabBarStrip.height
                            text: name
                            active: App.currentPage === page
                            done: {
                                if (page === 1) return Device.connected
                                if (page === 2) return App.syncOk
                                if (page === 3) return App.calibOk
                                return false
                            }
                            onClicked: App.currentPage = page
                        }
                    }
                }

                Rectangle {
                    id: tabSlideIndicator
                    height: 2
                    radius: 1
                    color: Theme.accent
                    y: tabBarStrip.height - height
                    visible: width > 0

                    function tabForPage(pageId) {
                        for (let i = 0; i < tabRepeater.count; ++i) {
                            const item = tabRepeater.itemAt(i)
                            if (item && appTabs.get(i).page === pageId)
                                return item
                        }
                        return null
                    }

                    property Item activeTab: tabForPage(App.currentPage)
                    width: activeTab ? activeTab.indicatorWidth : 0
                    x: activeTab ? activeTab.x + activeTab.indicatorX : 0

                    Behavior on x {
                        NumberAnimation {
                            duration: Theme.motionBase
                            easing: Theme.easeOut
                        }
                    }
                    Behavior on width {
                        NumberAnimation {
                            duration: Theme.motionBase
                            easing: Theme.easeOut
                        }
                    }
                }
            }

            Item { Layout.fillWidth: true }
        }
    }

    StackLayout {
        anchors.fill: parent
        currentIndex: App.currentPage

        // 0 · Accueil - padded + scroll, contenu centré
        Item {
            ScrollView {
                id: homeScroll
                anchors.fill: parent
                anchors.margins: Theme.spaceLg
                clip: true
                ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
                contentWidth: availableWidth
                HomePage {
                    width: Math.min(homeScroll.availableWidth, Theme.contentMaxWidth)
                    x: Math.max(0, (homeScroll.availableWidth - width) / 2)
                }
            }
        }

        // 1 · Machine - padded + scroll, contenu centré
        Item {
            ScrollView {
                id: deviceScroll
                anchors.fill: parent
                anchors.margins: Theme.spaceLg
                clip: true
                ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
                contentWidth: availableWidth
                DevicePage {
                    width: Math.min(deviceScroll.availableWidth, Theme.contentMaxWidth)
                    x: Math.max(0, (deviceScroll.availableWidth - width) / 2)
                }
            }
        }

        // 2 · Synchronisation
        Item {
            SidePanelSplitShell {
                anchors.fill: parent
                SyncPage {
                    anchors.fill: parent
                    anchors.margins: Theme.s4
                }
            }
        }

        // 3 · Calibration
        Item {
            SidePanelSplitShell {
                anchors.fill: parent
                CalibrationPage {
                    anchors.fill: parent
                    anchors.margins: Theme.spaceLg
                }
            }
        }

        // 4 · Mesure (chargé à la demande - évite crash TaxonSearchField au démarrage)
        Item {
            Loader {
                anchors.fill: parent
                active: App.currentPage === 4
                sourceComponent: measurePageRoot
            }
            Component {
                id: measurePageRoot
                SidePanelSplitShell {
                    collapsibleSettings: true
                    MeasurePage {
                        anchors.fill: parent
                        anchors.margins: Theme.s2
                    }
                }
            }
        }

        // 5 · Données & IA (chargé à la demande)
        Item {
            Loader {
                anchors.fill: parent
                active: App.currentPage === 5
                sourceComponent: dataPageRoot
            }
            Component {
                id: dataPageRoot
                DataPage {
                    anchors.fill: parent
                    anchors.margins: Theme.s4
                }
            }
        }

        // 6 · Hub Pro (chargé à la demande)
        Item {
            Loader {
                anchors.fill: parent
                active: App.currentPage === 6
                sourceComponent: proHubRoot
            }
            Component {
                id: proHubRoot
                SidePanelSplitShell {
                    ProHubPage {
                        anchors.fill: parent
                        anchors.margins: Theme.s4
                    }
                }
            }
        }

        // 7 · Sessions (chargé à la demande)
        Item {
            Loader {
                anchors.fill: parent
                active: App.currentPage === 7
                sourceComponent: sessionsPageRoot
            }
            Component {
                id: sessionsPageRoot
                SessionsPage {
                    anchors.fill: parent
                    anchors.margins: Theme.s4
                }
            }
        }

        // 8 · Paramètres / Stockage (chargé à la demande)
        Item {
            Loader {
                anchors.fill: parent
                active: App.currentPage === 8
                sourceComponent: settingsPageRoot
            }
            Component {
                id: settingsPageRoot
                SettingsPage {
                    anchors.fill: parent
                    anchors.margins: Theme.spaceXl
                    onCloseRequested: win.closePreferences()
                }
            }
        }
    }
}
