import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import AquaMeasure

GridLayout {
    id: root

    property string label: ""
    property string hint: ""
    property bool fieldEnabled: true
    property int spinFrom: 0
    property int spinTo: 100
    property int spinStep: 1
    property int spinValue: 0

    signal valueModified(int value)

    Layout.fillWidth: true
    Layout.minimumWidth: 0
    columns: width >= 320 ? 2 : 1
    columnSpacing: Theme.s3
    rowSpacing: Theme.s1

    RowLayout {
        Layout.fillWidth: true
        Layout.minimumWidth: 0
        spacing: Theme.s2

        Text {
            Layout.fillWidth: true
            Layout.minimumWidth: 0
            text: root.label
            wrapMode: Text.WordWrap
            font.family: Theme.fontFamily
            font.pixelSize: Theme.fontBody
            color: root.fieldEnabled ? Theme.text : Theme.textDim
        }

        InfoDot { text: root.hint }
    }

    AppSpinBox {
        id: spinBox
        objectName: root.objectName + "SpinBox"
        Accessible.name: root.label
        Layout.preferredWidth: 148
        Layout.alignment: Qt.AlignRight | Qt.AlignVCenter
        from: root.spinFrom
        to: root.spinTo
        stepSize: root.spinStep
        value: root.spinValue
        enabled: root.fieldEnabled
        onValueModified: root.valueModified(spinBox.value)
    }
}
