import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import AquaMeasure

ColumnLayout {
    id: root

    property string label: ""
    property string fieldText: ""
    property var options: []
    // Option toujours proposee en tete, quel que soit le filtre saisi (NA).
    property string pinnedOption: ""
    readonly property string inputText: field.text
    signal textEdited(string text)

    spacing: 2

    function _norm(s) {
        return (s || "").toLowerCase()
    }

    function _matchLabel(label, ft) {
        var lo = _norm(label)
        if (!ft)
            return true
        var p = lo.indexOf(" (")
        var vern = p >= 0 ? lo.substring(0, p) : lo
        var sci = p >= 0 ? lo.substring(p + 2, lo.length - 1) : ""
        if (vern.indexOf(ft) === 0 || sci.indexOf(ft) === 0)
            return true
        if (vern.indexOf(ft) >= 0)
            return true
        return false
    }

    readonly property var filteredOptions: {
        var ft = _norm(field.text)
        var src = root.options || []
        var pin = root.pinnedOption
        var out = []
        if (pin)
            out.push(pin)
        for (var i = 0; i < src.length; i++) {
            var item = String(src[i])
            if (pin && item === pin)
                continue
            if (!ft || _matchLabel(item, ft))
                out.push(item)
        }
        return out
    }

    onFieldTextChanged: {
        if (!field.activeFocus && field.text !== root.fieldText)
            field.text = root.fieldText
    }

    // Resynchronisation FORCÉE, focus compris. Le garde `!activeFocus`
    // ci-dessus protège la frappe en cours, mais il fait aussi que « Annuler »
    // et « Valider » laissaient à l'écran le texte abandonné pendant que le
    // modèle, lui, avait changé. Les deux boutons appellent donc ceci.
    function syncFromModel() {
        if (field.text !== root.fieldText)
            field.text = root.fieldText
    }

    AppLabel {
        visible: root.label.length > 0
        text: root.label
        color: Theme.textMuted
        font.pixelSize: Theme.fzXs
    }

    Rectangle {
        Layout.fillWidth: true
        implicitHeight: 36
        radius: Theme.radiusSm
        color: Theme.bgElevated
        border.width: field.activeFocus ? 2 : 1
        border.color: field.activeFocus ? Theme.accent : Theme.border

        RowLayout {
            anchors.fill: parent
            anchors.leftMargin: Theme.s2
            anchors.rightMargin: Theme.s1
            spacing: 0

            TextField {
                id: field
                objectName: "taxonSearchInput"
                Layout.fillWidth: true
                font.family: Theme.fontFamily
                font.pixelSize: Theme.fzSm
                color: Theme.text
                selectionColor: Theme.accentSoft
                selectedTextColor: Theme.accentText
                verticalAlignment: Text.AlignVCenter
                placeholderText: qsTr("Tapez pour filtrer…")
                background: Item {}

                Component.onCompleted: field.text = root.fieldText

                onTextEdited: function(text) {
                    root.textEdited(text)
                    suggestPopup.open()
                }

                onTextChanged: {
                    if (activeFocus)
                        root.textEdited(text)
                }

                onActiveFocusChanged: {
                    if (activeFocus)
                        suggestPopup.open()
                }
            }

            ToolButton {
                Layout.preferredWidth: 28
                Layout.preferredHeight: 28
                text: "▾"
                font.pixelSize: Theme.fzSm
                onClicked: {
                    if (suggestPopup.visible)
                        suggestPopup.close()
                    else
                        suggestPopup.open()
                }
            }
        }
    }

    Popup {
        id: suggestPopup
        parent: Overlay.overlay
        modal: false
        focus: false
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
        padding: Theme.s1
        width: Math.max(field.width + 36, 240)

        onAboutToShow: {
            const anchor = Overlay.overlay
            if (!anchor)
                return
            const p = field.mapToItem(anchor, 0, field.height)
            x = p.x
            y = p.y + 4
        }

        background: Rectangle {
            radius: Theme.radiusSm
            color: Theme.panel
            border.color: Theme.border
        }

        contentItem: ListView {
            id: suggestList
            clip: true
            implicitHeight: Math.min(contentHeight, 220)
            model: root.filteredOptions

            onCountChanged: {
                if (root.filteredOptions.length > 0 && field.activeFocus)
                    suggestPopup.open()
            }

            delegate: ItemDelegate {
                width: ListView.view.width
                height: 32
                text: modelData
                font.family: Theme.fontFamily
                font.pixelSize: Theme.fzSm
                onClicked: {
                    field.text = modelData
                    root.textEdited(modelData)
                    suggestPopup.close()
                }
            }
        }
    }
}
