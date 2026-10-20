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

The floating orb is 32 pixels across. Its chat bar is 360 × 40 pixels, with
scaled side controls and a compact status pill. The left control is a stroked
chat-bubble icon in the existing violet circle, opening the same floating input
as the lava sphere. The Help/Act selector is removed from both chat surfaces:
questions and computer tasks share one composer.

`ui_computer_use.py` draws a faint lavender aura fading smoothly inward from the
screen edges, without a solid stroke or rounded frame. The desktop center is
fully transparent. A violet, lightly rounded banner near the top center reads
“Helper is using your computer · Esc to cancel” in white semibold text. There
is no dark pill, separate keycap, or ornamental status dot.

The active-control pointer is small and dark with a white outline and a diffuse
lavender halo. The aura, banner, and white text use Helper's existing violet/white
palette. Active tasks do not add target rectangles or a cursor speech bubble;
action details remain in the existing chat/status surface. The aura appears only
once the engine starts an active computer action, stays visible through outcome
checks, and hides on completion, error, Escape, or shutdown. Per-monitor windows
never accept focus or pointer input and request Windows capture exclusion. They
follow display geometry changes without animating over the model's evidence.

Chat and saved transcripts use selectable text with word-boundary wrapping and
fallback wrapping for long paths and URLs. The composer stays outside the scroll
area. Dashboard account settings scroll, form rows can wrap, and the statistics
column hides in narrow windows.

Visual review included light and dark themes, the empty conversation, long
messages, 420-pixel chat windows, 760-pixel dashboard windows, all dashboard
pages, floating controls, and multiple lava animation phases. The live Windows
chat was also opened and inspected. Ruff passed on the changed UI modules.
The automatic-chat/frame update also has offscreen light/dark previews and
regressions for the chat icon, automatic action mode, transparent/passive frame,
monitor geometry/hotplug, worker failures, ordinary answers, and Escape cleanup.
The full suite with the isolated browser and native UIA fixtures passed:
248 tests, 1 skipped, and 16 subtests. The aura revision also checks smooth
edge falloff, the violet cancel banner, the lavender pointer halo, and removal of target
boxes/captions from active control.
