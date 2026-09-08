import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import AquaMeasure

Button {
    id: control
    property bool primary: false
    property bool loading: false
    // true = occupe la cellule jusqu'à buttonMaxWidth (pas toute la fenêtre)
    property bool fill: false

    // Infobulle d'aide. Contrairement à GhostButton/PrimaryButton, AppButton
    // dérive de Button : le handler onClicked appartient à l'appelant, on ne
    // peut donc pas intercepter le clic ici. Pas de `requires` : utilisez
    // `enabled` + `disabledReason` (l'explication passe par le survol du
    // conteneur, Qt coupant le hover d'un Control désactivé).
    property string disabledReason: ""
    property string tooltipText: ""

    ToolTip.delay: 400
    ToolTip.visible: control.hovered && ToolTip.text.length > 0
    ToolTip.text: control.tooltipText

    Layout.maximumWidth: fill ? Theme.buttonMaxWidth : -1
    Layout.fillWidth: fill
    Layout.alignment: fill ? Qt.AlignHCenter : (Qt.AlignLeft | Qt.AlignVCenter)

    implicitWidth: Math.min(
        implicitContentWidth + leftPadding + rightPadding,
        Theme.buttonMaxWidth)
    implicitHeight: 40
    leftPadding:  Theme.spaceLg
    rightPadding: Theme.spaceLg
    topPadding:   Theme.spaceSm
    bottomPadding: Theme.spaceSm

    font.family:    Theme.fontFamily
    font.pixelSize: Theme.fzBase
    font.weight: Font.Medium

    background: Rectangle {
        radius: Theme.radiusSm
        color: {
            // surfaceHover pour l'état désactivé était indistinguable du survol.
            if (!control.enabled)   return Theme.surfaceDisabled
            if (control.pressed)    return control.primary ? Theme.accentHover : Theme.surfaceActive
            if (control.hovered)    return control.primary ? Theme.accentHover : Theme.surfaceHover
            return control.primary ? Theme.accent : Theme.panel2
        }
        border.width: (control.primary && control.enabled) ? 0 : 1
        border.color: control.enabled ? Theme.border : Theme.borderDisabled
        Behavior on color { ColorAnimation { duration: Theme.motionFast } }
    }

    contentItem: Text {
        text: control.text
        font: control.font
        color: {
            if (!control.enabled) return Theme.textDisabled
            return control.primary ? "#ffffff" : Theme.text
        }
        horizontalAlignment: Text.AlignHCenter
        verticalAlignment:   Text.AlignVCenter
        elide: Text.ElideRight
        opacity: control.loading ? 0.6 : 1.0
    }
}
