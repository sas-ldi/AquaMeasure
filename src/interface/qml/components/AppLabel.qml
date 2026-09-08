import QtQuick
import AquaMeasure

Text {
    id: root
    property bool muted: false
    property bool hero: false

    font.family: hero ? Theme.fontFamilyDisplay : Theme.fontFamily
    font.pixelSize: hero ? Theme.fontHero
                          : (muted ? Theme.fontBody : Theme.fontSubtitle)
    font.weight: hero ? Font.DemiBold : (muted ? Font.Normal : Font.Medium)
    color: {
        if (root.hero) return Theme.text
        if (root.muted) return Theme.textMuted
        return Theme.text
    }
    wrapMode: Text.WordWrap
}
