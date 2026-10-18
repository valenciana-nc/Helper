# Violet desktop UI

The chat, dashboard, transcript viewer, floating input, menus and native dialogs
share the light and dark palettes in `theme.py`. Text and controls use opaque
surfaces so readability does not depend on the desktop wallpaper or OS theme.
Card styles target their frames instead of leaking borders into child widgets.

`ui_lava.py` follows the original Helper orb's luminous shading: white at the
top, lavender through the middle, and violet at the bottom. Translucent flowing
folds and soft light drift inside the sphere. A blurred premultiplied layer
blends the motion into the glass without hard blob outlines. Broad highlights,
edge shading, and a slight surface movement retain the original rounded look.
The geometry scales with the painter, including high DPI displays. Visible
orbs animate at 30 fps while idle; the floating orb uses 60 fps during
interaction. Hidden orbs stop their timers. A bounded texture cache allows
the decorative orbs to share frames.

Chat and saved transcripts use selectable text with word-boundary wrapping and
fallback wrapping for long paths and URLs. The composer stays outside the scroll
area. Dashboard account settings scroll, form rows can wrap, and the statistics
column hides in narrow windows.

Visual review included light and dark themes, the empty conversation, long
messages, 420-pixel chat windows, 760-pixel dashboard windows, all dashboard
pages, floating controls, and multiple lava animation phases. The live Windows
chat was also opened and inspected. Ruff passed on the changed UI modules.
Automated tests were not run for this UI change.
