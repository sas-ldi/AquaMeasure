import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import AquaMeasure

// Panneau latéral gauche + contenu principal - largeur ajustable (SplitView).
Item {
    id: root

    default property alias content: mainHost.children

    property int sidePreferredWidth: Theme.sidePanelWidth
    property int sideMinimumWidth: 220
    property int sideMaximumWidth: 420
    property int contentMargin: Theme.s4
    property bool collapsibleSettings: false
    onVisibleChanged: if (!visible) settingsDrawer.close()
    Component.onDestruction: settingsDrawer.close()
    Connections {
        target: App
        function onCurrentPageChanged() {
            if (root.collapsibleSettings && App.currentPage !== 4)
                settingsDrawer.close()
        }
    }

    SplitView {
        anchors.fill: parent
        orientation: Qt.Horizontal
        handle: Rectangle {
            implicitWidth: 6
            implicitHeight: parent.height
            color: SplitHandle.pressed ? Theme.accent : Theme.border
        }

        Item {
            id: sideHost
            SplitView.preferredWidth: root.collapsibleSettings ? 44 : root.sidePreferredWidth
            SplitView.minimumWidth: root.collapsibleSettings ? 44 : root.sideMinimumWidth
            SplitView.maximumWidth: root.collapsibleSettings ? 44 : root.sideMaximumWidth
            clip: true

            Loader {
                anchors.fill: parent
                active: !root.collapsibleSettings
                sourceComponent: ContextSidePanel { }
            }
            ToolButton {
                objectName: "measurementSettingsToggle"
                visible: root.collapsibleSettings
                x: 4; y: 8; width: 36; height: 40
                Accessible.name: qsTr("Réglages de la mesure")
                ToolTip.visible: hovered
                ToolTip.text: settingsDrawer.opened ? qsTr("Fermer les réglages") : qsTr("Réglages")
                background: Rectangle {
                    color: settingsDrawer.opened ? Theme.accentSoft : Theme.elevated
                    radius: Theme.radiusSm
                    border.color: settingsDrawer.opened ? Theme.accent : Theme.border2
                }
                contentItem: WorkspaceIcon { name: "settings" }
                onClicked: settingsDrawer.opened ? settingsDrawer.close() : settingsDrawer.open()
            }
        }

        Item {
            id: mainHost
            SplitView.fillWidth: true
            clip: true
        }
    }
    Popup {
        id: settingsDrawer
        objectName: root.collapsibleSettings ? "measurementSettingsDrawer" : ""
        x: 44; y: 0
        width: 292
        height: root.height
        padding: 0
        modal: false
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
        parent: root
        background: Rectangle { objectName: "measurementSettingsBackground"; color: Theme.bg; border.color: Theme.border2 }
        contentItem: Item {
            RowLayout {
                id: drawerHeader
                anchors.top: parent.top
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.margins: 12
                AppLabel { Layout.fillWidth: true; text: qsTr("Réglages"); font.weight: Font.DemiBold }
                GhostButton { text: qsTr("Fermer"); small: true; onClicked: settingsDrawer.close() }
            }
            Loader {
                anchors.top: drawerHeader.bottom
                anchors.bottom: parent.bottom
                width: parent.width
                active: root.collapsibleSettings
                sourceComponent: ContextSidePanel { objectName: "measurementSettingsBody" }
            }
        }
    }
}
