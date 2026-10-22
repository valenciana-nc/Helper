# Validation

Validated with Python 3.12 in the repository virtual environment. The full suite,
including the isolated browser fixture and native UIA fixture, passed:

- 266 passed, 161 subtests passed in 137.91 seconds, with adapter tests collected first.
- Ruff passed; compileall completed; `pip check` reported no broken requirements.
- Git whitespace checks passed after the final cleanup.

Validation caveat: two default-order runs hit the browser fixture's existing
3-second screenshot timeout (three failures, then two failures). All three browser
checks passed alone, the combined computer-use/theme/adapter run passed (39 tests,
116 subtests; native fixture not enabled in that run), and the adapter-first full
run above passed including the native fixture. The order-sensitive screenshot
timeout has not been diagnosed or changed as part of this presentation update.
The focused computer-use/theme/geometry run passed: 31 tests and 113 subtests.

The tests cover the observation-driven runtime, strict provider schemas, action
validation and recovery, cancellation, UI lifecycle, multi-monitor geometry,
local-first voice, WAV conversion, microphone recording, and provider adapters.
The browser integration uses an isolated local fixture. The native UIA fixture
uses a disposable test window, not the user's desktop. No paid model inference
was required.

Autonomy regressions cover bounded clarification review before exposing a question,
no added calls on the normal action path, model-call budgets, cancellation during
review, in-memory follow-ups, unrelated questions/new tasks, retained completed
steps, and prevention of uncertain-click replay across continuation. They also
cover independent full-goal verification after follow-ups, corrective progress
between failed finishes, and preserving consequential-action confirmation.
Review counts are logged without the proposed question, goal, or typed values.

Separately, two manual calls to the configured provider used a synthetic desktop
with visible taskbar controls. The direct multi-part request returned an action;
a frustrated follow-up also returned an action and selected continuation of the
original task. No desktop input was dispatched in these probes. They test model
choice on those two inputs, not actual app launching, media playback, or broad
real-world task reliability. Unlike the deterministic suite, these probes used a
live configured provider.

The previous plan-once executor, its Qt bridge, the old screen-capture module,
and unused function-tool/route-classifier APIs have been removed. The active
voice runtime and its tests were moved out of the retired package; voice and the
current engine remain covered by the main `tests/` suite.

Dashboard-first navigation is covered for startup, legacy launch flags, tray
activation, and restoring a minimized dashboard. The separate welcome/chat page
has been removed; the orb input and dashboard transcripts remain covered.
All Windows ICO sizes are checked against the purple README PNG. Windows
shortcuts were refreshed and the obsolete Chat shortcut removed.

Claude/Grok account-card checks cover placement below ChatGPT, official-console
browser links, masked keys, atomic persistence, provider/model selection,
restart only after successful saves, key removal, and preserving unrelated or
unsaved settings. xAI requests carry the current screenshot and Helper decision
schema through the existing provider adapter, without computer-use tools.
The Status page, navigation item, and embedded log viewer are absent. Light and
dark Account previews were inspected. Provider requests use recording fakes;
these checks do not establish live-key validity or subscription OAuth support.

Purple is reserved for orb artwork and computer-use aura/banner presentation.
Every light/dark chrome token is grayscale; light window/sidebar/card surfaces are
white. Tests cover text/button/selection contrast, neutral SVG and floating
controls, breathing purple edge lighting with a transparent center, a purple
banner with white text and a synchronized halo, and a black/white pointer with
a matching purple halo.
Account and Settings previews were inspected in light and grayscale dark themes.

The computer-use effect was compared with the supplied references for the edge
bloom and colored/white-text banner, using the requested expansion/intensification
and return rather than claiming animation timing can be measured from stills.
Pixel/Qt checks cover the seamless 4.2-second cycle, increased depth/opacity and
saturation at the peak, transparent center at every sampled phase, white-text
contrast, a real timer tick, synchronized monitors and hotplug, limited repaint
regions, and stopping/resetting animation on idle/Escape/close. Synthetic resting,
peak, cycle, and animated GIF previews were rendered and inspected; private
references and previews remain outside version control.

Reproduce with the commands in `README.md`. Browser executable location is an
environment-specific test setting and is not hardcoded into production code.
