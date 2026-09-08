import QtQuick
import QtQuick.Layouts
import AquaMeasure

RowLayout {
    property int currentStep: 0
    property var steps: []

    spacing: Theme.spaceSm
    Repeater {
        model: parent.steps
        delegate: RowLayout {
            spacing: Theme.spaceSm
            Rectangle {
                width: 28
                height: 28
                radius: 14
                color: index <= parent.parent.currentStep ? Theme.accentBlue : Theme.surfaceHover
                border.color: index === parent.parent.currentStep ? Theme.accent : "transparent"
                border.width: 2
                AppLabel {
                    anchors.centerIn: parent
                    text: (index + 1).toString()
                    font.weight: Font.Bold
                    font.pixelSize: Theme.fontCaption
                }
            }
            AppLabel {
                visible: index < parent.parent.steps.length
                text: modelData
                color: index <= parent.parent.currentStep ? Theme.text : Theme.textMuted
                font.pixelSize: Theme.fontCaption
            }
            Rectangle {
                visible: index < parent.parent.steps.length - 1
                Layout.preferredWidth: 24
                height: 2
                color: Theme.border
            }
        }
    }
}
