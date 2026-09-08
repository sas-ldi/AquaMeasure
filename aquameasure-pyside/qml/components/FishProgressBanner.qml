import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import AquaMeasure

// Où en est CE poisson, en une ligne : taxon, taille, piste, bouchées.
//
// Le parcours complet passe par quatre écritures dans trois tables et deux
// panneaux différents ; rien à l'écran ne disait ce que le poisson portait
// déjà. Il fallait ouvrir le tableau pour la taille, la liste des pistes pour
// le suivi, le bloc des bouchées pour les marqueurs — et se souvenir du reste.
//
// Les jalons non atteints restent affichés, éteints : les chemins courts sont
// légitimes (mesurer et s'arrêter là est une observation valable), donc aucun
// jalon éteint n'est présenté comme un manque. C'est un état, pas une
// checklist.
//
// La disposition est un Flow et non une Row : à 300 px de volet replié, quatre
// jalons ne tiennent pas sur une ligne, et un débordement a déjà été signalé
// deux fois par le client. Ils passent à la ligne plutôt que de sortir.
Rectangle {
    id: root
    objectName: "fishProgressBanner"

    // Un poisson est désigné : soit une fiche ouverte (rien n'est encore
    // écrit), soit une ligne du registre sélectionnée.
    readonly property bool hasFish: Data.draftActive || Data.selectedAnnId.length > 0
    readonly property bool saved: Data.selectedAnnId.length > 0

    // ── Jalon 1 : taxon ────────────────────────────────────────────────
    // Le rang le plus fin renseigné fait foi, comme partout ailleurs. « NA »
    // est une identification à part entière (quelqu'un a regardé et tranché),
    // pas un jalon éteint.
    readonly property string taxonName: {
        if (Data.editSpecies.length > 0) return Data.editSpecies
        if (Data.editGenus.length > 0) return Data.editGenus
        if (Data.editFamily.length > 0) return Data.editFamily
        return ""
    }
    readonly property bool taxonDone: root.taxonName.length > 0
    // Pas de coche : le glyphe manque à plusieurs polices système et sortait
    // en carré vide, comme les flèches de la barre de transport avant lui.
    // La pastille allumée dit déjà que le jalon est atteint.
    readonly property string taxonText: root.taxonDone
        ? root.taxonName
        : qsTr("Taxon -")

    // ── Jalon 2 : taille ───────────────────────────────────────────────
    readonly property bool lengthDone: Data.editMeasurementMm > 0
    readonly property string lengthText: root.lengthDone
        ? qsTr("%1 mm").arg(Data.editMeasurementMm.toFixed(1))
        : qsTr("Taille -")

    // ── Jalon 3 : piste ────────────────────────────────────────────────
    // Une piste rattachée sans numéro lisible reste une piste : le dire, plutôt
    // que d'afficher « Piste - » sur un poisson qui en porte bien une.
    readonly property bool trackDone: root.saved && Data.selectedTrackDbId.length > 0
    readonly property string trackText: {
        if (!root.trackDone) return qsTr("Piste -")
        if (Data.selectedTrackNumber >= 0)
            return qsTr("Piste #%1").arg(Data.selectedTrackNumber)
        return qsTr("Piste rattachée")
    }

    // ── Jalon 4 : bouchées ─────────────────────────────────────────────
    // Le compte se lit sur la piste du poisson, pas sur la piste que le bloc
    // « Bouchées » a sous la main : les deux coïncident presque toujours, mais
    // c'est « presque » qui ferait mentir le bandeau. `Pecks.tracks` porte le
    // compte par piste et se recharge après chaque écriture de marqueur.
    readonly property int peckCount: {
        if (!root.trackDone || typeof Pecks === "undefined") return 0
        const rows = Pecks.tracks
        for (let i = 0; i < rows.length; i++) {
            if (rows[i].trackId === Data.selectedTrackDbId)
                return rows[i].markerCount
        }
        return 0
    }
    readonly property string peckText: {
        if (!root.trackDone) return qsTr("Bouchées -")
        if (root.peckCount <= 0) return qsTr("0 bouchée")
        return qsTr("%1 bouchée(s)").arg(root.peckCount)
    }

    implicitHeight: hasFish ? milestones.implicitHeight + Theme.s2 * 2 : 0
    radius: Theme.radiusSm
    color: Theme.panel2
    border.color: Theme.border2

    Flow {
        id: milestones
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.leftMargin: Theme.s2
        anchors.rightMargin: Theme.s2
        anchors.topMargin: Theme.s2
        spacing: Theme.s1

        Repeater {
            model: [
                {"name": "bannerTaxon", "text": root.taxonText, "done": root.taxonDone},
                {"name": "bannerLength", "text": root.lengthText, "done": root.lengthDone},
                {"name": "bannerTrack", "text": root.trackText, "done": root.trackDone},
                {"name": "bannerPecks", "text": root.peckText,
                 "done": root.trackDone && root.peckCount > 0},
            ]

            delegate: Rectangle {
                required property var modelData

                objectName: modelData.name
                // Un jalon plus large que le bandeau passe seul à la ligne et
                // son texte s'élide : rien ne sort jamais des marges.
                width: Math.min(milestones.width,
                                pill.implicitWidth + Theme.s2 * 2)
                height: 20
                radius: Theme.radiusSm
                color: modelData.done ? Theme.accentSoft : "transparent"
                border.width: 1
                border.color: modelData.done ? Theme.accent : Theme.border2

                AppLabel {
                    id: pill
                    anchors.fill: parent
                    anchors.leftMargin: Theme.s2
                    anchors.rightMargin: Theme.s2
                    verticalAlignment: Text.AlignVCenter
                    text: modelData.text
                    color: modelData.done ? Theme.accentText : Theme.textDim
                    font.pixelSize: Theme.fzXs
                    font.weight: modelData.done ? Font.DemiBold : Font.Normal
                    // NoWrap explicite : AppLabel enveloppe par défaut, et un
                    // `implicitWidth` qui dépendrait de la largeur reçue
                    // boucherait la largeur que ce même implicitWidth calcule.
                    wrapMode: Text.NoWrap
                    elide: Text.ElideRight
                }
            }
        }
    }
}
