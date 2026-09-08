import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import AquaMeasure

Rectangle {
    id: root

    function fileName(path) {
        if (!path || path.length === 0)
            return ""
        const i = Math.max(path.lastIndexOf("/"), path.lastIndexOf("\\"))
        return i >= 0 ? path.substring(i + 1) : path
    }

    implicitWidth: Theme.sidePanelWidth
    color: Theme.panel

    Rectangle {
        anchors.right: parent.right
        width: 1
        height: parent.height
        color: Theme.border
    }

    ScrollView {
        anchors.fill: parent
        anchors.margins: Theme.s3
        clip: true
        ScrollBar.horizontal.policy: ScrollBar.AlwaysOff

        ColumnLayout {
            width: root.width - Theme.s3 * 2
            spacing: Theme.s4

            // ── Sync ──────────────────────────────────────────────
            ColumnLayout {
                visible: App.currentPage === 2
                Layout.fillWidth: true
                spacing: Theme.s4

                SidePanelSection {
                    title: qsTr("Vidéos")
                    iconName: "video"
                    info: qsTr("Les deux vidéos filmées en même temps par la caméra gauche et la caméra droite. Elles ne démarrent presque jamais à la même seconde : c'est ce décalage que la synchronisation va mesurer.")
                    Layout.fillWidth: true

                    SidePanelField {
                        label: qsTr("Caméra gauche")
                        value: root.fileName(Sync.leftVideo)
                        onActivated: Sync.pickLeftVideo()
                        menuItems: [
                            { id: "reload", text: qsTr("Recharger dernière paire") },
                            { id: "pickPc", text: qsTr("Choisir sur le PC…") }
                        ]
                        onMenuTriggered: (id) => {
                            if (id === "reload") Sync.reloadSavedVideos()
                            else if (id === "pickPc") Sync.pickLeftVideo()
                        }
                    }

                    SidePanelField {
                        label: qsTr("Caméra droite")
                        value: root.fileName(Sync.rightVideo)
                        onActivated: Sync.pickRightVideo()
                        menuItems: [
                            { id: "reload", text: qsTr("Recharger dernière paire") },
                            { id: "pickPc", text: qsTr("Choisir sur le PC…") }
                        ]
                        onMenuTriggered: (id) => {
                            if (id === "reload") Sync.reloadSavedVideos()
                            else if (id === "pickPc") Sync.pickRightVideo()
                        }
                    }
                }

                SidePanelSection {
                    title: qsTr("Détection flash (auto)")
                    iconName: "detect"
                    info: qsTr("Le coup de flash déclenché devant les deux caméras sert de repère commun. L'application suit la luminosité image par image sur chaque vidéo, repère le pic lumineux, et en déduit de combien d'images une caméra est en retard sur l'autre.")
                    Layout.fillWidth: true

                    PrimaryButton {
                        Layout.fillWidth: true
                        text: Sync.busy ? qsTr("Détection en cours…") : qsTr("Détecter flash")
                        requires: Sync.bothVideosSelected && !Sync.busy
                        disabledReason: Sync.busy
                            ? qsTr("Détection déjà en cours - patientez.")
                            : qsTr("Sélectionnez les deux vidéos (caméra gauche et droite) ci-dessus.")
                        tooltipText: qsTr("Cherche le flash sur les deux vidéos et en déduit le décalage.")
                        onClicked: Sync.runDetectFromUi()
                    }

                    AppLabel {
                        Layout.fillWidth: true
                        text: qsTr("Analyse la luminosité autour du marqueur flash sur G et D.")
                        font.pixelSize: Theme.fzXs
                        color: Theme.textDim
                        wrapMode: Text.WordWrap
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: Theme.s2

                        CheckBox {
                            id: roiModeCheck
                            text: qsTr("Cadre de détection")
                            checked: Sync.roiModeEnabled
                            onToggled: Sync.setRoiMode(checked)
                            ToolTip.visible: roiModeHover.hovered
                            ToolTip.text: qsTr("Limite l'analyse à un rectangle dessiné sur chaque aperçu - utile si le flash est toujours au même endroit.")
                            HoverHandler { id: roiModeHover }
                        }

                        GhostButton {
                            text: qsTr("✕ ROI")
                            small: true
                            requires: Sync.roiModeEnabled
                            disabledReason: qsTr("Aucun cadre de détection à effacer : cochez « Cadre de détection » d'abord.")
                            tooltipText: qsTr("Efface les rectangles tracés sur les deux aperçus.")
                            onClicked: Sync.clearFlashRois()
                        }
                    }

                    AppLabel {
                        Layout.fillWidth: true
                        visible: Sync.roiModeEnabled
                        text: qsTr("Tracez un rectangle autour du flash sur l'aperçu gauche, puis sur l'aperçu droite, avant « Détecter flash ».")
                        font.pixelSize: Theme.fzXs
                        color: Theme.accentText
                        wrapMode: Text.WordWrap
                    }
                }

                SidePanelSection {
                    title: qsTr("Synchronisation manuelle")
                    iconName: "sync"
                    info: qsTr("À utiliser quand le flash n'est pas trouvé automatiquement (eau trouble, flash hors champ). Vous placez vous-même les deux vues sur le même instant, puis vous validez : le décalage est déduit des deux images affichées.")
                    Layout.fillWidth: true
                    // Les deux vues ne sont plus sur le décalage enregistré :
                    // la section se déplie et se signale d'elle-même.
                    attention: Sync.syncDirty

                    PrimaryButton {
                        Layout.fillWidth: true
                        text: qsTr("Appliquer sync (frames courantes)")
                        requires: Sync.bothVideosSelected
                        attention: Sync.syncDirty
                        disabledReason: qsTr("Sélectionnez les deux vidéos (caméra gauche et droite) ci-dessus.")
                        tooltipText: qsTr("Fixe le décalage à partir des deux images actuellement affichées.")
                        onClicked: Sync.applyManualFromCurrent()
                    }

                    AppLabel {
                        Layout.fillWidth: true
                        visible: Sync.syncDirty
                        text: qsTr("Vues déplacées : Δ %1 frames à l'écran, %2 enregistré. Validez pour retenir le nouveau décalage.")
                            .arg(Sync.pendingSyncOffset).arg(Sync.syncOffset)
                        font.pixelSize: Theme.fzXs
                        font.weight: Font.DemiBold
                        color: Theme.warn
                        wrapMode: Text.WordWrap
                    }

                    AppLabel {
                        Layout.fillWidth: true
                        text: qsTr("Placez chaque vue sur le flash, puis validez le décalage entre les deux caméras.")
                        font.pixelSize: Theme.fzXs
                        color: Theme.textDim
                        wrapMode: Text.WordWrap
                    }
                }

                SidePanelSection {
                    title: qsTr("Découpe vidéo (trim)")
                    iconName: "cut"
                    info: qsTr("Limite le travail à la partie utile des vidéos, celle réellement filmée sous l'eau. Ce qui est en dehors des bornes début/fin est ignoré par la calibration et par la mesure : moins d'attente, et pas d'images parasites.")
                    Layout.fillWidth: true
                    // Poignées In/Out bougées sans enregistrement.
                    attention: Sync.trimDirty

                    PrimaryButton {
                        Layout.fillWidth: true
                        text: qsTr("Enregistrer In / Out")
                        requires: Sync.bothVideosSelected
                        attention: Sync.trimDirty
                        disabledReason: qsTr("Sélectionnez les deux vidéos (caméra gauche et droite) ci-dessus.")
                        tooltipText: qsTr("Mémorise les bornes de découpe pour la calibration et la mesure.")
                        onClicked: Sync.saveTrim()
                    }

                    AppLabel {
                        Layout.fillWidth: true
                        visible: Sync.trimDirty
                        text: qsTr("Bornes modifiées, pas encore enregistrées : G %1–%2 · D %3–%4.")
                            .arg(Sync.leftInFrame).arg(Sync.leftOutFrame)
                            .arg(Sync.rightInFrame).arg(Sync.rightOutFrame)
                        font.pixelSize: Theme.fzXs
                        font.weight: Font.DemiBold
                        color: Theme.warn
                        wrapMode: Text.WordWrap
                    }

                    AppLabel {
                        Layout.fillWidth: true
                        text: qsTr("Mémorise les limites de début et fin des deux vidéos pour la calibration et la mesure.")
                        font.pixelSize: Theme.fzXs
                        color: Theme.textDim
                        wrapMode: Text.WordWrap
                    }
                }

                // Résultat de la synchronisation - feedback toujours visible
                // dès qu'un décalage a été calculé.
                Rectangle {
                    Layout.fillWidth: true
                    visible: Sync.step >= 2
                    Layout.topMargin: Theme.s2
                    implicitHeight: syncResultCol.implicitHeight + Theme.s4 * 2
                    radius: Theme.radiusSm
                    color: Theme.okSoft
                    border.color: Theme.ok
                    border.width: 1

                    ColumnLayout {
                        id: syncResultCol
                        anchors.fill: parent
                        anchors.margins: Theme.s4
                        spacing: Theme.s1

                        RowLayout {
                            Layout.fillWidth: true
                            spacing: Theme.s1

                            AppLabel {
                                Layout.fillWidth: true
                                text: qsTr("Résultat synchronisation")
                                font.pixelSize: Theme.fzXs
                                font.weight: Font.DemiBold
                                color: Theme.ok
                            }

                            InfoDot {
                                diameter: 14
                                text: qsTr("Δ est le décalage retenu, en nombre d'images. L'application le rejoue ensuite toute seule pour que les deux vues montrent toujours le même instant. Un Δ faux fausse toutes les mesures : vérifiez que le flash apparaît bien sur les deux images indiquées en dessous.")
                            }
                        }

                        Text {
                            Layout.fillWidth: true
                            text: qsTr("Δ %1 frames").arg(Sync.syncOffset)
                            font.family: Theme.monoFamily
                            font.pixelSize: Theme.fzXl
                            font.weight: Font.DemiBold
                            color: Theme.text
                        }

                        AppLabel {
                            Layout.fillWidth: true
                            text: qsTr("Flash G : frame %1 · Flash D : frame %2")
                                .arg(Sync.leftPinFrame).arg(Sync.rightPinFrame)
                            font.pixelSize: Theme.fzXs
                            font.family: Theme.monoFamily
                            color: Theme.textMuted
                            wrapMode: Text.WordWrap
                        }
                    }
                }

                SidePanelSection {
                    title: qsTr("Prochaine étape")
                    iconName: "next"
                    info: qsTr("La calibration compare la même scène vue par les deux caméras : elle a donc besoin d'une paire synchronisée. Faites la synchro d'abord, sinon les deux images comparées ne montrent pas le même instant.")
                    collapsible: false
                    Layout.fillWidth: true

                    PrimaryButton {
                        Layout.fillWidth: true
                        text: qsTr("→ Calibration")
                        requires: Sync.bothVideosSelected
                        disabledReason: qsTr("Sélectionnez les deux vidéos (caméra gauche et droite) ci-dessus.")
                        tooltipText: qsTr("Passe à l'étalonnage stéréo avec les vidéos trimées.")
                        onClicked: App.goToCalibrationFromSync()
                    }

                    AppLabel {
                        Layout.fillWidth: true
                        text: qsTr("Passe à l'étalonnage stéréo avec les vidéos trimées.")
                        font.pixelSize: Theme.fzXs
                        color: Theme.textDim
                        wrapMode: Text.WordWrap
                    }
                }
            }

            // ── Calibration ───────────────────────────────────────
            ColumnLayout {
                visible: App.currentPage === 3
                Layout.fillWidth: true
                spacing: Theme.s4

                SidePanelSection {
                    title: qsTr("Vidéos de la mire")
                    iconName: "video"
                    info: qsTr("Ce sont les vidéos où l'on promène la planche à damier devant les deux caméras - pas les vidéos de comptage des poissons. La mire doit y apparaître nette, sous plusieurs angles, et sur toute la surface de l'image.")
                    Layout.fillWidth: true
                    expanded: true

                    // Import de la paire synchronisée : action principale de
                    // l'étape, donc un vrai bouton en tête de section. Elle
                    // était auparavant enfouie dans le menu « ▾ » de chaque
                    // champ, là où personne ne va la chercher.
                    PrimaryButton {
                        Layout.fillWidth: true
                        text: qsTr("Importer les vidéos")
                        requires: Calib.syncImportAvailable && !Calib.busy
                        disabledReason: Calib.busy
                            ? qsTr("Calibration en cours - patientez ou cliquez « Annuler ».")
                            : qsTr("Aucune paire synchronisée : terminez d'abord l'étape Synchronisation.")
                        tooltipText: qsTr("Reprend les deux vidéos enregistrées à l'étape Synchronisation.")
                        onClicked: Calib.loadVideosFromSync()
                    }

                    AppLabel {
                        visible: Calib.videosFromSync
                        Layout.fillWidth: true
                        text: Calib.syncFramesLabel.length > 0
                            ? qsTr("Paire synchronisée · décalage %1 · %2")
                                .arg(Calib.syncOffsetLabel).arg(Calib.syncFramesLabel)
                            : qsTr("Paire synchronisée · décalage %1").arg(Calib.syncOffsetLabel)
                        font.pixelSize: Theme.fzXs
                        font.weight: Font.DemiBold
                        color: Theme.ok
                        wrapMode: Text.WordWrap
                    }

                    SidePanelField {
                        label: qsTr("Caméra gauche")
                        value: root.fileName(Calib.leftVideo)
                        subtitle: Calib.leftVideo.length === 0
                            ? ""
                            : (Calib.videosFromSync ? "" : qsTr("Import manuel (PC)"))
                        onActivated: Calib.pickLeftVideo()
                        menuItems: [
                            { id: "pickPc", text: qsTr("Choisir sur le PC…") }
                        ]
                        onMenuTriggered: (id) => {
                            if (id === "pickPc") Calib.pickLeftVideo()
                        }
                    }

                    SidePanelField {
                        label: qsTr("Caméra droite")
                        value: root.fileName(Calib.rightVideo)
                        subtitle: Calib.rightVideo.length === 0
                            ? ""
                            : (Calib.videosFromSync ? "" : qsTr("Import manuel (PC)"))
                        onActivated: Calib.pickRightVideo()
                        menuItems: [
                            { id: "pickPc", text: qsTr("Choisir sur le PC…") }
                        ]
                        onMenuTriggered: (id) => {
                            if (id === "pickPc") Calib.pickRightVideo()
                        }
                    }
                }

                SidePanelSection {
                    title: qsTr("Calibration")
                    iconName: "calibrate"
                    info: qsTr("La calibration mesure où sont les deux caméras l'une par rapport à l'autre et corrige la déformation de leurs objectifs. C'est elle qui permet ensuite de traduire des pixels en millimètres : sans calibration, aucune mesure de taille n'est possible.")
                    Layout.fillWidth: true

                    SidePanelField {
                        label: qsTr("Mire ChArUco")
                        info: qsTr("La mire est la planche à damier imprimée que l'on filme. L'application y reconnaît des cases dont elle connaît la taille réelle : c'est la référence qui donne l'échelle. Ces réglages doivent décrire exactement la planche imprimée, sinon toutes les mesures seront fausses.

Au lancement, l'analyse essaie d'abord la grille saisie ici. Si elle n'y voit pas assez de coins, elle balaie les grilles de 3 à 13 colonnes sur 3 à 9 lignes - sans changer la taille de case ni le dictionnaire ArUco - et propose celle qui colle aux marqueurs vus. Elle ne l'applique jamais toute seule : la proposition s'affiche ici, à vous de la valider.")
                        value: Settings.charucoSummary
                        onActivated: App.openCharucoSettings()
                    }

                    // Grille proposee par la sonde de detection. Elle n'etait
                    // qu'une ligne de console : la calibration continuait avec
                    // la mauvaise grille sans que personne ne le voie.
                    ColumnLayout {
                        visible: Calib.charucoSuggestionAvailable
                        Layout.fillWidth: true
                        spacing: Theme.s1

                        AppLabel {
                            Layout.fillWidth: true
                            text: qsTr("Mire détectée %1 dans la vidéo, vos réglages disent %2×%3.")
                                .arg(Calib.charucoSuggestionLabel)
                                .arg(Settings.charucoSquaresX)
                                .arg(Settings.charucoSquaresY)
                            font.pixelSize: Theme.fzXs
                            font.weight: Font.DemiBold
                            color: Theme.warn
                            wrapMode: Text.WordWrap
                        }

                        RowLayout {
                            Layout.fillWidth: true
                            spacing: Theme.s2

                            PrimaryButton {
                                Layout.fillWidth: true
                                small: true
                                text: qsTr("Appliquer %1").arg(Calib.charucoSuggestionLabel)
                                requires: !Calib.busy
                                disabledReason: qsTr("Attendez la fin de la calibration en cours.")
                                tooltipText: qsTr("Règle colonnes et lignes sur la grille détectée. Relancez ensuite la calibration.")
                                onClicked: Calib.applyCharucoSuggestion()
                            }

                            GhostButton {
                                small: true
                                text: qsTr("Ignorer")
                                tooltipText: qsTr("Garde vos réglages et masque la proposition.")
                                onClicked: Calib.dismissCharucoSuggestion()
                            }
                        }
                    }

                    SidePanelField {
                        label: qsTr("Paramètres calibration")
                        info: qsTr("Règle la finesse de l'analyse : combien d'images des vidéos de mire sont examinées et avec quelle exigence. Plus il y en a, plus le résultat est précis - et plus le calcul est long.")
                        value: Settings.calibSettingsSummary
                        onActivated: App.openCalibScanSettings()
                    }

                    PrimaryButton {
                        Layout.fillWidth: true
                        Layout.topMargin: Theme.s2
                        text: Calib.busy
                            ? qsTr("Calibration…")
                            : qsTr("Lancer la calibration")
                        requires: Calib.bothVideosSelected && !Calib.busy
                        disabledReason: Calib.busy
                            ? qsTr("Calibration déjà en cours - patientez ou cliquez « Annuler ».")
                            : qsTr("Sélectionnez les deux vidéos de mire (caméra gauche et droite) ci-dessus.")
                        tooltipText: qsTr("Analyse la mire ChArUco et calcule les paramètres stéréo.")
                        onClicked: Calib.runCalibration()
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: Theme.s1

                        AppLabel {
                            Layout.fillWidth: true
                            text: Calib.rmseStereo >= 0
                                ? qsTr("Erreur (RMSE) %1 px - sous 1 px c'est excellent").arg(Calib.rmseStereo.toFixed(2))
                                : qsTr("Erreur (RMSE) attendue : moins de 1 px")
                            font.pixelSize: Theme.fzXs
                            color: Theme.textDim
                            wrapMode: Text.WordWrap
                        }

                        InfoDot {
                            diameter: 14
                            text: qsTr("Le RMSE dit de combien l'application se trompe quand elle replace les cases de la mire sur l'image : c'est une erreur moyenne, exprimée en pixels. Plus il est petit, mieux c'est. Sous 1 pixel c'est excellent ; au-delà de 5, refaites la calibration en filmant la mire sous plus d'angles et jusqu'aux bords de l'image.")
                        }
                    }

                    GhostButton {
                        Layout.fillWidth: true
                        text: qsTr("Annuler")
                        requires: Calib.busy
                        disabledReason: qsTr("Aucune calibration en cours.")
                        tooltipText: qsTr("Interrompt le calcul en cours.")
                        onClicked: Calib.cancel()
                    }
                }
            }

            // ── Mesure ────────────────────────────────────────────
            ColumnLayout {
                visible: App.currentPage === 4
                Layout.fillWidth: true
                spacing: Theme.s4

                // Contexte de la session en premier : on renseigne le lieu,
                // le titre et la date avant de mesurer.
                SidePanelSection {
                    title: qsTr("Session")
                    iconName: "folder"
                    info: qsTr("Une session, c'est UNE paire de vidéos (gauche + droite) et tout ce que vous y annotez. Le lieu et la date sont obligatoires : ce sont eux qui permettent de retrouver ces observations plus tard et de les exporter. Enregistrez la session avant d'annoter : sans elle, la paire n'est pas mémorisée.")
                    expanded: true
                    Layout.fillWidth: true

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: Theme.s2
                        AppLabel {
                            Layout.fillWidth: true
                            text: qsTr("Lieu (obligatoire)")
                            color: Data.sessionSite.length > 0 ? Theme.textMuted : Theme.warn
                            font.pixelSize: Theme.fzXs
                        }
                        InfoDot {
                            diameter: 14
                            text: qsTr("Le site de plongée ou le point de dépôt du banc de caméras. Sans lieu, impossible de comparer des sessions ou de publier les observations.")
                        }
                    }
                    AppTextField {
                        Layout.fillWidth: true
                        text: Data.sessionSite
                        onTextEdited: Data.sessionSite = text
                    }
                    AppLabel { text: qsTr("Titre"); color: Theme.textMuted; font.pixelSize: Theme.fzXs }
                    AppTextField {
                        Layout.fillWidth: true
                        text: Data.sessionTitle
                        onTextEdited: Data.sessionTitle = text
                    }
                    AppLabel {
                        text: qsTr("Date (obligatoire)")
                        color: Data.sessionDate.length > 0 ? Theme.textMuted : Theme.warn
                        font.pixelSize: Theme.fzXs
                    }
                    AppTextField {
                        Layout.fillWidth: true
                        text: Data.sessionDate
                        font.family: Theme.monoFamily
                        onTextEdited: Data.sessionDate = text
                    }
                    AppLabel { text: qsTr("Notes"); color: Theme.textMuted; font.pixelSize: Theme.fzXs }
                    AppTextField {
                        Layout.fillWidth: true
                        text: Data.sessionNotes
                        onTextEdited: Data.sessionNotes = text
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        PrimaryButton {
                            Layout.fillWidth: true
                            // L'enregistrement calcule l'empreinte des deux
                            // vidéos (plusieurs Go la première fois) : il part
                            // dans un thread, et le bouton le dit.
                            text: Data.busy
                                ? qsTr("Enregistrement…")
                                : qsTr("Enregistrer session")
                            requires: Data.dbAvailable
                                      && !Data.busy
                                      && Data.videoPath.length > 0
                                      && Data.sessionSite.length > 0
                                      && Data.sessionDate.length > 0
                            disabledReason: {
                                if (!Data.dbAvailable)
                                    return qsTr("Base d'annotations indisponible - vérifiez l'installation de fish-vision.")
                                if (Data.busy)
                                    return qsTr("Enregistrement en cours : l'empreinte des vidéos est en train d'être calculée.")
                                if (Data.videoPath.length === 0)
                                    return qsTr("Chargez d'abord une vidéo (section « Vidéos » ci-dessous).")
                                if (Data.sessionSite.length === 0)
                                    return qsTr("Renseignez le lieu : il est obligatoire.")
                                return qsTr("Renseignez la date de la sortie : elle est obligatoire.")
                            }
                            tooltipText: qsTr("Enregistre la paire de vidéos, le lieu et la date, fige le décalage de synchronisation et trace la calibration active.")
                            onClicked: Data.saveSession()
                        }
                        GhostButton {
                            text: qsTr("Dossier DB")
                            requires: Data.dbAvailable
                            disabledReason: qsTr("Base d'annotations indisponible.")
                            tooltipText: qsTr("Ouvre le dossier contenant le fichier SQLite.")
                            onClicked: Data.openDbFolder()
                        }
                    }

                    // Session active choisie dans l'onglet Sessions. Le volet
                    // ne disait que « pas encore de session pour cette paire » :
                    // on avait beau selectionner une session, rien ici ne le
                    // montrait, et le geste qui manquait - attacher la paire -
                    // n'etait proposé que sur l'autre page.
                    Rectangle {
                        Layout.fillWidth: true
                        Layout.topMargin: Theme.s1
                        implicitHeight: activeSessionCol.implicitHeight + Theme.s3 * 2
                        radius: Theme.radiusSm
                        color: Sessions.activePairLoaded
                            ? Theme.okSoft
                            : (Sessions.hasActiveSession ? Theme.warnSoft : "transparent")
                        border.width: 1
                        border.color: Sessions.activePairLoaded
                            ? Theme.ok
                            : (Sessions.hasActiveSession ? Theme.warn : Theme.border)

                        ColumnLayout {
                            id: activeSessionCol
                            anchors.fill: parent
                            anchors.margins: Theme.s3
                            spacing: Theme.s1

                            AppLabel {
                                Layout.fillWidth: true
                                text: Sessions.hasActiveSession
                                    ? qsTr("Session active : %1").arg(Sessions.activeSessionName)
                                    : qsTr("Aucune session choisie")
                                font.pixelSize: Theme.fzXs
                                font.weight: Font.DemiBold
                                color: Sessions.activePairLoaded
                                    ? Theme.ok
                                    : (Sessions.hasActiveSession ? Theme.warn : Theme.textDim)
                                wrapMode: Text.WordWrap
                            }

                            AppLabel {
                                Layout.fillWidth: true
                                visible: text.length > 0
                                text: {
                                    if (!Sessions.hasActiveSession)
                                        return qsTr("Choisissez-en une dans l'onglet Sessions : c'est elle qui portera tout ce que vous annotez ici.")
                                    if (!Sessions.activeSessionHasPair)
                                        return qsTr("Cette session n'a aucune paire de vidéos : rien de ce que vous annotez ne lui sera rattaché tant que vous n'aurez pas attaché la paire chargée.")
                                    if (!Sessions.activePairLoaded)
                                        return qsTr("La paire chargée ici n'est pas celle de la session : ouvrez la session, ou attachez cette paire.")
                                    return qsTr("La paire chargée appartient bien à cette session.")
                                }
                                font.pixelSize: Theme.fzXs
                                color: Theme.textDim
                                wrapMode: Text.WordWrap
                            }

                            PrimaryButton {
                                Layout.fillWidth: true
                                small: true
                                visible: Sessions.hasActiveSession && !Sessions.activePairLoaded
                                attention: visible && Measure.leftVideo.length > 0
                                text: Sessions.busy
                                    ? qsTr("Attache en cours…")
                                    : qsTr("Attacher cette paire à « %1 »").arg(Sessions.activeSessionName)
                                requires: !Sessions.busy && Measure.leftVideo.length > 0
                                disabledReason: Sessions.busy
                                    ? qsTr("Attache déjà en cours - l'empreinte des vidéos est en cours de calcul.")
                                    : qsTr("Chargez d'abord la paire de vidéos ci-dessous.")
                                tooltipText: qsTr("Rattache la paire chargée à la session active et fige le décalage de synchronisation.")
                                onClicked: Sessions.attachCurrentPair()
                            }
                        }
                    }

                    AppLabel {
                        Layout.fillWidth: true
                        text: Data.sessionId.length > 0
                            ? qsTr("Métadonnées enregistrées pour cette paire.")
                            : qsTr("Métadonnées pas encore enregistrées pour cette paire.")
                        font.pixelSize: Theme.fzXs
                        color: Data.sessionId.length > 0 ? Theme.ok : Theme.textDim
                        wrapMode: Text.WordWrap
                    }

                    GhostButton {
                        fill: true
                        Layout.fillWidth: true
                        small: true
                        text: Sessions.hasActiveSession
                            ? qsTr("Changer de session")
                            : qsTr("Choisir une session")
                        tooltipText: qsTr("Ouvre la page Sessions : sorties à venir, en cours et terminées.")
                        onClicked: App.currentPage = 7
                    }
                }

                SidePanelSection {
                    title: qsTr("Vidéos")
                    iconName: "video"
                    info: qsTr("La paire de vidéos à annoter. « Charger depuis Sync » reprend les vidéos et le décalage calculés à l'étape Synchronisation : c'est la voie normale, celle qui garantit que les deux vues montrent le même instant.")
                    Layout.fillWidth: true

                    AppLabel {
                        visible: Measure.videosFromSync
                        Layout.fillWidth: true
                        text: qsTr("Paire synchronisée · Δ %1").arg(Measure.syncOffsetLabel)
                        font.pixelSize: Theme.fzXs
                        font.weight: Font.DemiBold
                        color: Theme.ok
                        wrapMode: Text.WordWrap
                    }

                    SidePanelField {
                        label: qsTr("Caméra gauche")
                        value: root.fileName(Measure.leftVideo)
                        subtitle: Measure.leftVideoSourceHint
                        onActivated: Measure.pickLeftVideo()
                        menuItems: [
                            { id: "fromSync", text: qsTr("Charger depuis Sync") },
                            { id: "pickPc", text: qsTr("Choisir sur le PC…") },
                            { id: "reload", text: qsTr("Recharger dernière paire") }
                        ]
                        onMenuTriggered: (id) => {
                            if (id === "fromSync") Measure.loadVideosFromSync()
                            else if (id === "pickPc") Measure.pickLeftVideo()
                            else if (id === "reload") Measure.refresh("", "")
                        }
                    }

                    SidePanelField {
                        label: qsTr("Caméra droite")
                        value: root.fileName(Measure.rightVideo)
                        subtitle: Measure.rightVideoSourceHint
                        onActivated: Measure.pickRightVideo()
                        menuItems: [
                            { id: "fromSync", text: qsTr("Charger depuis Sync") },
                            { id: "pickPc", text: qsTr("Choisir sur le PC…") },
                            { id: "reload", text: qsTr("Recharger dernière paire") }
                        ]
                        onMenuTriggered: (id) => {
                            if (id === "fromSync") Measure.loadVideosFromSync()
                            else if (id === "pickPc") Measure.pickRightVideo()
                            else if (id === "reload") Measure.refresh("", "")
                        }
                    }

                    // Quelle calibration la mesure utilise vraiment. Deux
                    // calibrations pour la meme paire de videos etaient
                    // indistinguables : rien a l'ecran ne disait laquelle
                    // etait chargee, ni quand elle avait ete faite.
                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: Theme.s1
                        Layout.topMargin: Theme.s1

                        RowLayout {
                            Layout.fillWidth: true
                            spacing: Theme.s1
                            AppLabel {
                                Layout.fillWidth: true
                                text: qsTr("Calibration utilisée")
                                font.pixelSize: Theme.fzXs
                                color: Theme.textDim
                            }
                            InfoDot {
                                diameter: 14
                                text: qsTr("La calibration réellement chargée par la mesure, avec la date à laquelle elle a été faite. Une archive importée garde la date de sa calibration d'origine, pas celle de l'import. Le dossier exact est écrit dans la console à chaque chargement.")
                            }
                        }

                        AppLabel {
                            Layout.fillWidth: true
                            text: Measure.calibrationSummary.length > 0
                                ? Measure.calibrationSummary
                                : qsTr("Aucune calibration complète - passez par l'onglet Calibration.")
                            font.pixelSize: Theme.fzXs
                            font.family: Theme.monoFamily
                            color: Measure.calibrationSummary.length > 0
                                ? Theme.ok
                                : Theme.warn
                            wrapMode: Text.WordWrap
                        }
                    }
                }

                SidePanelSection {
                    title: qsTr("Détection IA")
                    iconName: "detect"
                    info: qsTr("L'IA propose des cadres autour des poissons qu'elle croit reconnaître. Elle ne décide rien : vous gardez ce qui est juste, vous ignorez le reste, et vous encadrez vous-même à la souris ce qu'elle a raté.")
                    Layout.fillWidth: true

                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: Theme.s1

                        RowLayout {
                            Layout.fillWidth: true
                            spacing: Theme.s1

                            AppLabel {
                                Layout.fillWidth: true
                                text: qsTr("Modèle")
                                font.pixelSize: Theme.fzSm
                                color: Theme.textMuted
                            }

                            InfoDot {
                                diameter: 14
                                text: qsTr("Le modèle est le « cerveau » de la détection. Certains savent seulement dire « poisson », d'autres proposent la famille. Changez-en si les cadres proposés sont mauvais sur vos images ; le seuil conseillé s'ajuste automatiquement.")
                            }
                        }

                        AppComboBox {
                            id: detectorCombo
                            Layout.fillWidth: true
                            textRole: "label"
                            model: Detectors.models.filter(function(m) { return m.usable })
                            currentIndex: model.findIndex(function(m) { return m.id === Detectors.activeId })
                            onActivated: function(i) { Detectors.setActive(model[i].id) }
                        }

                        AppLabel {
                            Layout.fillWidth: true
                            text: Detectors.ready
                                ? qsTr("%1 · seuil conseillé %2 %")
                                    .arg(Detectors.activeBackend)
                                    .arg(Math.round(Detectors.activeDefaultConf * 100))
                                : Detectors.activeReason
                            font.pixelSize: Theme.fzXs
                            color: Detectors.ready ? Theme.textDim : Theme.warn
                            wrapMode: Text.WordWrap
                        }

                        // Un re-entrainement ecrase le fichier de poids sans
                        // changer le nom du modele : sans cette ligne, rien ne
                        // distinguait le modele livre de celui qu'on vient de
                        // produire.
                        AppLabel {
                            Layout.fillWidth: true
                            visible: Detectors.activeTrainingLabel.length > 0
                            text: qsTr("Entraîné ici · %1").arg(Detectors.activeTrainingLabel)
                            font.pixelSize: Theme.fzXs
                            font.family: Theme.monoFamily
                            color: Theme.ok
                            wrapMode: Text.WordWrap
                        }

                        GhostButton {
                            Layout.fillWidth: true
                            fill: true
                            small: true
                            text: qsTr("Gérer les modèles…")
                            tooltipText: qsTr("Télécharger, ajouter ou comparer les modèles de détection installés.")
                            onClicked: Detectors.openManager()
                        }
                    }

                    SidePanelToggle {
                        text: qsTr("Détection poisson (IA)")
                        info: qsTr("Décochée, l'IA ne propose plus aucun cadre : vous encadrez tous les poissons à la main. Utile quand elle se trompe trop sur ces images, ou pour annoter une espèce qu'elle ne connaît pas.")
                        checked: Fish.fishIaEnabled
                        onToggled: Fish.fishIaEnabled = !Fish.fishIaEnabled
                    }

                    SidePanelToggle {
                        text: qsTr("Détecter à chaque pause")
                        info: qsTr("Lance la détection toute seule dès que vous mettez la vidéo en pause : vous parcourez la vidéo et les poissons se retrouvent encadrés sans rien cliquer. À décocher si l'ordinateur peine.")
                        checked: Fish.autoOnPause
                        onToggled: Fish.autoOnPause = !Fish.autoOnPause
                    }

                    // Libelle au-dessus et champ sur toute la largeur, comme
                    // les autres reglages du volet : sur une seule ligne, le
                    // champ tombait a 72 px et la valeur n'etait plus visible
                    // entre les boutons − et +.
                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: Theme.s1

                        RowLayout {
                            Layout.fillWidth: true
                            spacing: Theme.s1
                            AppLabel {
                                Layout.fillWidth: true
                                text: qsTr("Confiance min (%)")
                                font.pixelSize: Theme.fzXs
                                color: Theme.textDim
                            }
                            InfoDot {
                                diameter: 14
                                text: qsTr("Certitude minimale pour qu'un cadre soit affiché. Bas (30 %) : l'IA propose beaucoup de poissons, mais aussi des cailloux et des ombres. Haut (80 %) : presque pas de fausses alertes, mais elle rate les poissons lointains ou flous. Partez du seuil conseillé indiqué sous le modèle.")
                            }
                        }

                        AppSpinBox {
                            Layout.fillWidth: true
                            from: 5
                            to: 95
                            value: Math.round(Fish.confidence * 100)
                            onValueModified: Fish.confidence = value / 100.0
                        }
                    }

                    PrimaryButton {
                        Layout.fillWidth: true
                        text: Fish.busy ? qsTr("Détection en cours…") : qsTr("Détecter sur cette image")
                        requires: !Fish.busy && Measure.frameCount > 0 && Fish.fishIaEnabled && !Measure.playing
                        disabledReason: {
                            if (Fish.busy)
                                return qsTr("Un calcul est déjà en cours - patientez ou cliquez « Interrompre l'analyse ».")
                            if (Measure.frameCount <= 0)
                                return qsTr("Chargez d'abord une vidéo dans la section « Vidéos ».")
                            if (!Fish.fishIaEnabled)
                                return qsTr("La détection IA est décochée juste au-dessus : recochez « Détection poisson (IA) ».")
                            return qsTr("Mettez la lecture en pause : l'IA travaille sur une image fixe.")
                        }
                        tooltipText: qsTr("Lance l'IA sur l'image affichée et encadre les poissons trouvés.")
                        onClicked: Fish.detectCurrentFrame()
                    }

                    AppLabel {
                        Layout.fillWidth: true
                        text: qsTr("Poisson raté par l'IA : glissez la souris sur l'image de gauche pour l'encadrer, puis clic droit sur le cadre tracé pour ouvrir sa fiche. Identifiez, mesurez si besoin, puis « Enregistrer le poisson ».")
                        font.pixelSize: Theme.fzXs
                        color: Theme.textDim
                        wrapMode: Text.WordWrap
                    }
                }

                SidePanelSection {
                    title: qsTr("Affichage")
                    iconName: "eye"
                    info: qsTr("La longueur est calculée en relief : le même point du poisson, pointé sur l'image de gauche puis sur celle de droite, donne sa position dans l'espace. La distance entre les points A et B est ensuite donnée en millimètres.")
                    Layout.fillWidth: true

                    SidePanelToggle {
                        text: qsTr("Lignes de contrôle (épipolaires)")
                        info: qsTr("Affiche des repères horizontaux : un même point du poisson doit se retrouver à la même hauteur sur les deux images. Si ce n'est pas le cas, la calibration est à refaire - et les mesures seront fausses.")
                        checked: Measure.epipolar
                        onToggled: Measure.toggleEpipolar()
                    }

                }

                SidePanelSection {
                    title: qsTr("Stats session")
                    iconName: "chart"
                    info: qsTr("Résumé écologique sans prétendre compter des individus uniques. MaxN est uniquement le maximum de poissons visibles simultanément sur UNE frame validée ; le registre contient des observations, pas un total de poissons.")
                    Layout.fillWidth: true

                    AppLabel {
                        Layout.fillWidth: true
                        text: Data.sessionMaxVisibleFish >= 0
                            ? qsTr("MaxN (une frame validée) : %1").arg(Data.sessionMaxVisibleFish)
                            : qsTr("MaxN (une frame validée) : -")
                        font.family: Theme.monoFamily
                        font.pixelSize: Theme.fzSm
                    }

                    AppLabel {
                        Layout.fillWidth: true
                        text: Data.sessionStatsSummary.length > 0
                            ? Data.sessionStatsSummary
                            : qsTr("Validez au moins un comptage de frame")
                        font.pixelSize: Theme.fzXs
                        color: Theme.textDim
                        wrapMode: Text.WordWrap
                    }
                }

                SidePanelSection {
                    title: qsTr("Journal")
                    iconName: "list"
                    info: qsTr("Trace détaillée de ce que fait l'application : détections, suivi, erreurs. À ouvrir quand quelque chose ne se passe pas comme prévu, et à recopier si vous demandez de l'aide.")
                    Layout.fillWidth: true

                    ConsolePanel {
                        Layout.fillWidth: true
                        expanded: false
                        bodyHeight: 100
                        logModel: Fish.logs
                        autoScroll: true
                    }
                }
            }

            // ── Hub Pro ───────────────────────────────────────────
            ColumnLayout {
                visible: App.currentPage === 6
                Layout.fillWidth: true
                spacing: Theme.s4

                SidePanelSection {
                    title: qsTr("Entraînement avancé")
                    iconName: "detect"
                    info: qsTr("Réservé à l'administration du modèle. L'export normal se fait uniquement dans l'onglet « Fin de session ».")
                    Layout.fillWidth: true

                    PrimaryButton {
                        Layout.fillWidth: true
                        text: ProTools.busy ? qsTr("Traitement en cours…") : qsTr("Ré-entraîner")
                        requires: !ProTools.busy
                        disabledReason: qsTr("Un traitement est déjà en cours - patientez ou cliquez « Annuler ».")
                        tooltipText: qsTr("Exporte la base puis relance l'entraînement du détecteur.")
                        onClicked: ProTools.retrainFromDb()
                    }

                    GhostButton {
                        Layout.fillWidth: true
                        text: qsTr("Annuler")
                        requires: ProTools.busy
                        disabledReason: qsTr("Aucun traitement en cours.")
                        tooltipText: qsTr("Interrompt l'export ou l'entraînement en cours.")
                        onClicked: ProTools.cancelJob()
                    }
                }
            }

            Item { Layout.fillHeight: true; Layout.minimumHeight: Theme.s4 }
        }
    }
}
