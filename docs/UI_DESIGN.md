# White desktop UI, purple orb and breathing computer-use glow

The dashboard, transcript viewer, floating input, menus and native dialogs
share the monochrome palettes in `theme.py`. Light mode uses white windows,
sidebar, and cards with black text/buttons and neutral gray borders. The optional
dark theme is grayscale too. Purple is reserved for the sphere artwork and the
computer-use edge aura, banner, and cursor halo—not buttons, selection, alerts,
navigation, or input borders.
Text and controls use opaque surfaces so readability does not depend on the
desktop wallpaper or OS theme.
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
chat-bubble icon in a neutral circle, opening the same floating input
as the lava sphere. Questions and computer tasks share that composer, with no
Help/Act selector. The dashboard is the sole main window: normal launches and
tray activation open it directly. The separate welcome/chat window is removed;
conversation transcripts remain available in the dashboard.

The logo PNG, 8-bit web thumbnail in `assets/readme/helper-orb-purple.png`, and
multi-size Windows ICO are generated from the same violet orb painter with
`python tools/build_brand_assets.py`. The README uses a separate web asset URL
instead of reusing the old blue logo URL. The app's window/tray
icons and desktop shortcut load that ICO, keeping the branding consistent.
Only the dashboard-facing Helper shortcut is retained; obsolete Chat shortcuts
are removed when Helper refreshes its shortcuts.

The sidebar contains Conversations, Settings, and Account; the Status page and
embedded log viewer are removed. Account places matching Claude and Grok cards
directly below ChatGPT. Both cards support native subscription sign-in,
cancellation, sign-out, and **Use subscription and restart**. Grok shows a browser
approval code. Claude offers a browser callback or copy-code login, with a masked
manual authorization input that clears on submission/completion. Claude's card
also states the third-party OAuth compatibility limitation. Separate API-console
buttons remain available; keys stay masked and separate billing is explicit.
Opening an API console alone never marks an account connected. Saving selects
the provider and model; restarting applies it to the running engine.

`ui_computer_use.py` draws a diffuse purple aura fading smoothly inward from all
four screen edges, without a solid stroke or rounded frame. The reference is a
colored edge bloom plus a matching colored banner, not a static outline. A
4.2-second cosine cycle begins at the original faint lavender (`#c79bff`), expands
55% farther inward, raises edge opacity 80%, and shifts toward a more saturated
violet (`#b478ff`), then smoothly returns and repeats. The desktop center stays
fully transparent throughout the cycle.

The lightly rounded purple-gradient banner near the top center reads “Helper is
using your computer · Esc to cancel” in white semibold text. Its soft purple halo
also expands and brightens with the same breath, while the text/body stay opaque
and legible. There is no white banner, dark pill, separate keycap, or status dot.
One monotonic clock and an approximately 30-fps timer keep all monitors in phase;
hotplug does not restart the cycle. Only the edge strips and banner halo are
repainted, and the timer stops while inactive. Escape hides the windows and stops
the animation immediately; a new task starts at the faint resting state.

The active-control pointer stays small and black with a white outline and a
diffuse purple halo matching the edge aura (`#c79bff`). The computer-use glow and
banner are purple; dashboard chrome remains monochrome. Active tasks do not add target rectangles or a cursor speech bubble;
action details remain in the existing chat/status surface. The aura appears only
once the engine starts an active computer action, stays visible through outcome
checks, and hides on completion, error, Escape, or shutdown. Per-monitor windows
never accept focus or pointer input and request Windows capture exclusion. They
follow display geometry changes without animating over the model's evidence.

Only the initial status-pill label changes to “Thinking…” instead of the fixed
“Looking at the current app…” opener. Existing action/status presentation stays
unchanged. No new step narration, reasoning stream, explanation overlay, arrows,
or model calls are added.

Chat and saved transcripts use selectable text with word-boundary wrapping and
fallback wrapping for long paths and URLs. The composer stays outside the scroll
area. Dashboard account settings scroll, form rows can wrap, and the statistics
column hides in narrow windows.

Visual checks include white/black chrome, purple orb branding, the breathing aura
and purple/white banner, dashboard startup and tray
activation, floating input, and transcript wrapping. Regressions cover the
PNG/ICO artwork and all icon sizes, foreground/background launch flags,
minimized dashboard restoration, automatic task handling, passive presentation,
monitor geometry/hotplug, failures, ordinary answers, and Escape cleanup.
Current full-suite results are recorded in `VALIDATION.md`.
