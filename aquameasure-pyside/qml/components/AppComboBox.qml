import QtQuick
import QtQuick.Controls
import QtQuick.Window
import AquaMeasure

ComboBox {
    id: control
    implicitHeight: 40
    leftPadding: Theme.spaceMd
    rightPadding: Theme.spaceMd + 32
    font.family: Theme.fontFamily
    font.pixelSize: Theme.fontBody

    property string placeholderText: ""

    // Largeur du plus long libelle de la liste. Le menu deroulant etait borne
    // a la largeur du champ : dans un volet etroit, « AquaMeasure - famille
    // (maison) » et « AquaMeasure - famille (ONNX, sans torch) » s'affichaient
    // tous deux « AquaMeasure - famille (... », impossibles a distinguer.
    property real _popupContentWidth: 0

    TextMetrics {
        id: itemMetrics
        font: control.font
    }

    function _recomputePopupWidth() {
        let widest = 0
        for (let i = 0; i < control.count; i++) {
            itemMetrics.text = control.itemLabel(i)
            widest = Math.max(widest, itemMetrics.width)
        }
        control._popupContentWidth = widest
    }

    onModelChanged: Qt.callLater(_recomputePopupWidth)
    onCountChanged: Qt.callLater(_recomputePopupWidth)
    Component.onCompleted: _recomputePopupWidth()

    palette {
        base: Theme.bgElevated
        window: Theme.bgElevated
        text: Theme.text
        button: Theme.surfaceHover
        buttonText: Theme.text
        highlight: Theme.accentBlueSoft
        highlightedText: "#FFFFFF"
    }

    function itemLabel(idx) {
        if (idx < 0 || idx >= count)
            return ""
        const m = model[idx]
        if (control.textRole && m && m[control.textRole] !== undefined)
            return m[control.textRole]
        if (typeof m === "object" && m !== null && control.textRole && m[control.textRole] !== undefined)
            return m[control.textRole]
        return typeof m === "string" ? m : String(m)
    }

    contentItem: TextField {
        leftPadding: Theme.spaceSm
        rightPadding: Theme.spaceSm
        text: control.editable
            ? control.editText
            : (control.displayText.length > 0 ? control.displayText : control.itemLabel(control.currentIndex))
        placeholderText: control.placeholderText
        placeholderTextColor: Theme.textDim
        // Respecter la zone de contenu calculée par ComboBox laisse la flèche
        // cliquable, y compris quand ce champ est éditable.
        enabled: control.editable
        autoScroll: control.editable
        readOnly: control.down
        font: control.font
        color: Theme.text
        verticalAlignment: Text.AlignVCenter
        background: Item {}

        onTextEdited: control.editText = text
        onAccepted: control.accepted()
    }

    indicator: Text {
        x: control.width - width - Theme.spaceMd
        y: control.topPadding + (control.availableHeight - height) / 2
        text: "▾"
        font.pixelSize: Theme.fzSm
        color: Theme.textMuted
    }

    background: Rectangle {
        implicitHeight: 40
        radius: Theme.radiusSm
        color: Theme.bgElevated
        border.width: control.activeFocus ? 2 : 1
        border.color: control.activeFocus ? Theme.accentBlueSoft : Theme.border
    }

    popup: Popup {
        id: comboPopup
        modal: false
        focus: true
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
        y: control.height + 4

        // Assez large pour le plus long libelle, sans jamais deborder de la
        // fenetre. Le menu s'affiche dans l'overlay : il peut donc etre plus
        // large que le champ et sortir du volet.
        readonly property real _chrome: Theme.spaceMd * 2 + Theme.spaceSm * 2 + 16
        readonly property real _maxWidth: {
            const win = control.Window.window
            return win ? Math.max(240, win.width - 32) : 640
        }
        width: Math.min(
            _maxWidth,
            Math.max(control.width, 220, control._popupContentWidth + _chrome))

        padding: Theme.spaceSm
        implicitHeight: contentItem.implicitHeight + padding * 2

        // Recale vers la gauche si le menu elargi sortirait par la droite.
        // Calcule a l'ouverture et non en liaison : mapToItem() n'est pas
        // reactif, la liaison restait bloquee sur sa premiere evaluation.
        function realign() {
            const win = control.Window.window
            if (!win) {
                comboPopup.x = 0
                return
            }
            const originX = control.mapToItem(null, 0, 0).x
            const overflow = originX + comboPopup.width - (win.width - 16)
            comboPopup.x = overflow > 0
                ? -Math.min(overflow, Math.max(0, originX - 16))
                : 0
        }

        onAboutToShow: {
            control._recomputePopupWidth()
            realign()
        }
        onWidthChanged: if (visible) realign()

        background: Rectangle {
            radius: Theme.radiusSm
            color: Theme.elevated
            border.color: Theme.border2
            border.width: 1
        }

        contentItem: ListView {
            clip: true
            implicitHeight: Math.min(contentHeight, 260)
            model: control.delegateModel
            currentIndex: control.highlightedIndex
            spacing: 2

            ScrollIndicator.vertical: ScrollIndicator { }

            delegate: ItemDelegate {
                width: comboPopup.width - Theme.spaceMd * 2
                height: 38
                text: control.itemLabel(index)
                highlighted: control.highlightedIndex === index
                font: control.font
                padding: Theme.spaceSm

                background: Rectangle {
                    radius: Theme.radiusSm
                    color: parent.highlighted ? Theme.surfaceActive : "transparent"
                }
                contentItem: Text {
                    leftPadding: Theme.spaceSm
                    text: parent.text
                    font: parent.font
                    color: parent.highlighted ? Theme.accentText : Theme.text
                    elide: Text.ElideRight
                    verticalAlignment: Text.AlignVCenter
                }
            }
        }
    }
}
