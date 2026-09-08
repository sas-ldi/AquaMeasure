import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import AquaMeasure

Rectangle {
    id: root
    readonly property bool compact: width < 840

    // Cette image porte-t-elle le MaxN de la session ?
    readonly property bool isSessionMax: Data.frameCountValidated
        && Data.sessionMaxVisibleFish >= 0
        && Data.frameManualCount === Data.sessionMaxVisibleFish

    implicitHeight: 52
    radius: Theme.radiusMd
    color: Theme.surface
    border.color: Data.frameCountValidated ? Theme.ok : Theme.border

    RowLayout {
        id: row
        anchors.fill: parent
        anchors.margins: root.compact ? Theme.s2 : Theme.s3
        spacing: root.compact ? Theme.s1 : Theme.s3

        AppLabel {
            Layout.fillWidth: root.compact
            Layout.minimumWidth: 0
            text: root.compact ? qsTr("Poissons visibles") : qsTr("Poissons visibles (image %1)").arg(Data.currentFrameIndex)
            font.pixelSize: root.compact ? Theme.fzXs : Theme.fzMd
            elide: Text.ElideRight
            font.weight: Font.DemiBold
        }

        InfoDot {
            diameter: 14
            text: qsTr("Valider un MaxN, image par image : 1) placez-vous sur l'image voulue ; 2) « IA » indique ce que la détection a trouvé - corrigez le nombre à côté si elle s'est trompée ; 3) cliquez « Valider comptage ». Le MaxN de la session est simplement le plus grand comptage validé : il n'y a pas de bouton « MaxN », il se met à jour tout seul à chaque validation. C'est l'indicateur d'abondance qui évite de compter deux fois le même poisson.")
        }

        Item { Layout.fillWidth: true }

        // Le lien entre << valider le comptage d'une image >> et le MaxN de la
        // session n'apparaissait nulle part a l'ecran : on ne savait pas ou
        // atterrissait la validation.
        AppLabel {
            text: (root.compact ? qsTr("MaxN %1") : qsTr("MaxN session %1"))
                .arg(Data.sessionMaxVisibleFish >= 0 ? Data.sessionMaxVisibleFish : "—")
            font.pixelSize: Theme.fzSm
            font.family: Theme.monoFamily
            font.weight: Font.DemiBold
            color: root.isSessionMax ? Theme.ok : Theme.textMuted
            ToolTip.visible: maxNHover.hovered
            ToolTip.text: root.isSessionMax
                ? qsTr("Cette image porte le MaxN de la session.")
                : qsTr("Plus grand comptage validé de la session, toutes images confondues.")
            HoverHandler { id: maxNHover }
        }

        Rectangle {
            Layout.preferredWidth: 1
            Layout.preferredHeight: 20
            Layout.alignment: Qt.AlignVCenter
            color: Theme.border
        }

        AppLabel {
            text: qsTr("IA %1").arg(Data.frameAiCount)
            color: Theme.textMuted
            font.pixelSize: Theme.fzSm
            font.family: Theme.monoFamily
        }

        AppSpinBox {
            id: countSpin
            objectName: "measurementCountSpin"
            from: 0
            to: 999
            value: Data.frameManualCount
            // 88 px n'affichait qu'un chiffre sur trois entre les boutons.
            width: Math.max(132, implicitWidth)
            onValueModified: Data.frameManualCount = value
        }

        PrimaryButton {
            objectName: "validateFrameCountButton"
            text: qsTr("Valider comptage")
            requires: Data.dbAvailable && Data.mediaId.length > 0
                && Data.frameCountCurrent
            disabledReason: !Data.dbAvailable
                ? qsTr("Base d'annotations indisponible - vérifiez l'installation de fish-vision.")
                : Data.mediaId.length === 0
                    ? qsTr("Enregistrez d'abord la session : panneau de gauche → « Enregistrer session ».")
                    : qsTr("Saisissez le nombre de poissons visibles dans le champ ci-contre (ou lancez la détection IA), puis validez.")
            tooltipText: qsTr("Enregistre le nombre de poissons visibles sur cette image. Le MaxN de la session suit le plus grand comptage validé.")
            onClicked: Data.validateFrameCount(countSpin.value)
        }

        Rectangle {
            width: 10
            height: 10
            radius: 5
            color: Data.frameCountValidated ? Theme.ok : Theme.border
            ToolTip.visible: validHover.hovered
            ToolTip.text: Data.frameCountValidated
                ? (root.isSessionMax
                    ? qsTr("Comptage validé - c'est le MaxN de la session")
                    : qsTr("Comptage validé pour cette image"))
                : qsTr("Comptage non validé - cette image ne compte pas dans le MaxN")
            HoverHandler { id: validHover }
        }
    }

    Connections {
        target: Data
        function onFrameManualCountChanged() {
            if (countSpin.value !== Data.frameManualCount)
                countSpin.value = Data.frameManualCount
        }
    }
}
