import QtQuick
import QtQuick.Controls
import AquaMeasure

// Combo déroulant + saisie libre (ex. baud série).
ComboBox {
    id: control

    property int numericValue: 115200

    signal valueCommitted(int value)

    editable: true
    implicitHeight: 40
    leftPadding: Theme.spaceMd
    rightPadding: Theme.spaceMd + 28
    font.family: Theme.fontFamily
    font.pixelSize: Theme.fontBody

    function commitFromText(text) {
        const cleaned = String(text).replace(/[\s,]/g, "")
        const v = parseInt(cleaned, 10)
        if (isNaN(v) || v <= 0)
            return
        if (control.numericValue !== v)
            control.numericValue = v
        control.valueCommitted(v)
        control.syncFromValue(v)
    }

    function syncFromValue(v) {
        const s = String(v)
        let idx = -1
        for (let i = 0; i < control.count; ++i) {
            if (String(control.model[i]) === s) {
                idx = i
                break
            }
        }
        if (idx >= 0)
            control.currentIndex = idx
        else
            control.editText = s
    }

    onActivated: control.commitFromText(currentText)

    Component.onCompleted: syncFromValue(numericValue)

    palette {
        base: Theme.bgElevated
        window: Theme.bgElevated
        text: Theme.text
        button: Theme.surfaceHover
        buttonText: Theme.text
        highlight: Theme.accentBlueSoft
        highlightedText: "#ffffff"
    }

    contentItem: TextField {
        text: control.editable ? control.editText : control.displayText
        font: control.font
        color: Theme.text
        selectionColor: Theme.accent
        selectedTextColor: "#ffffff"
        verticalAlignment: Text.AlignVCenter
        leftPadding: Theme.spaceSm
        rightPadding: Theme.spaceSm
        validator: IntValidator { bottom: 1; top: 10000000 }
        inputMethodHints: Qt.ImhDigitsOnly
        background: Item {}

        onTextEdited: control.editText = text
        onAccepted: control.commitFromText(text)
        onEditingFinished: control.commitFromText(text)
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
        y: control.height + 4
        width: control.width
        padding: Theme.spaceSm
        implicitHeight: contentItem.implicitHeight + padding * 2

        background: Rectangle {
            radius: Theme.radiusSm
            color: Theme.surface
            border.color: Theme.border
            border.width: 1
        }

        contentItem: ListView {
            clip: true
            implicitHeight: Math.min(contentHeight, 240)
            model: control.popup.visible ? control.delegateModel : null
            currentIndex: control.highlightedIndex
            spacing: 2

            ScrollIndicator.vertical: ScrollIndicator { }

            delegate: ItemDelegate {
                width: control.width - Theme.spaceMd * 2
                height: 36
                text: modelData !== undefined ? String(modelData) : ""
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
                    color: Theme.text
                    elide: Text.ElideRight
                    verticalAlignment: Text.AlignVCenter
                }
            }
        }
    }
}
