# Helper computer-use architecture

This is Helper's production observation-driven computer-use runtime. The
floating orb, account/provider settings, chat, voice, cursor, and highlight
widgets connect through `engine_bridge.py` to the engine implemented in
`helper_engine`. Desktop tasks and CLI tasks use the same engine.

## What changed and why

The previous engine generated a complete sequence of UI targets from one
observation, guessed operation types from descriptions, tried the same
interaction after failed verification, and could treat an empty plan produced by
a provider error as success. It also mixed screenshot pixels with physical
screen coordinates, and its UIA observer and actuator formatted runtime IDs
differently. UI mode selection affected appearance without constraining input.

The replacement gives every decision an observation ID, makes operations typed,
revalidates targets immediately before input, records whether an operation was
applied/rejected/unknown, and makes completion a separate verification decision.
The desktop uses one conversation: the model answers questions and performs actions
only for requests to operate the computer. There is no desktop mode switch.
The CLI still offers explicit Help mode, which cannot call the execution driver.

## Execution path

~~~mermaid
flowchart TD
    UI[Orb / typed chat / voice / CLI] --> Core[Bounded task runtime]
    Core --> Obs[Fresh observation]
    Obs --> Model[One next decision + updated milestones]
    Model --> Schema[Validate typed action and observation ID]
    Schema --> Mode{Help or active}
    Mode -->|Help| Guide[Highlight / explain / wait for user]
    Mode -->|Active| Gate[Policy / consent / fresh target validation]
    Gate --> Route{Available action route}
    Route --> UIA[Windows control pattern]
    Route --> DOM[Browser DOM handle]
    Route --> Pixels[Guarded visual input]
    UIA --> Receipt[Record effect before waiting]
    DOM --> Receipt
    Pixels --> Receipt
    Guide --> Check[Observe outcome]
    Receipt --> Check
    Check --> Local[Check explicit postconditions locally]
    Local --> Model
    Model -->|First proposed question, active only| Review[One bounded clarification review]
    Review --> Obs
    Model -->|Essential question after review| Input[Needs user input + in-memory pending task]
    Model -->|Finish request| Final[Fresh full observation + independent goal verifier]
    Final -->|Unproven| Model
    Final -->|Verified| Result[Final report + metrics]
~~~

There is no speculative batch of clicks across unseen menus or pages. A fill
operation can focus, select the existing value, and replace it as one operation;
the next action is chosen from the resulting observation. This reduces drift
while still avoiding a model round trip per physical keystroke.

## Modules

| Module | Responsibility |
|---|---|
| types.py | Immutable observations, elements, geometry, typed actions, receipts, reports |
| protocol.py | Local validation of JSON, action names, bounded values, target geometry, confidence |
| schemas.py | Provider output schemas, bound to the current observation ID and exposed element IDs |
| ports.py | Small model and driver interfaces; no Qt or platform imports |
| runtime.py | Task budgets, cancellation, teaching/active modes, bounded recovery, completion |
| model.py | Existing configured provider, current screenshot and compact task context |
| perception.py | Screenshot annotation, pixel/physical coordinate mapping, cheap frame comparisons |
| verification.py | Exact value/text/focus/checkbox/URL/title checks when evidence is available |
| policy.py | Shared decision boundary before every active route |
| windows.py | Active-window/monitor scope, screenshot leases, UIA preference, visual fallback |
| uia_worker.py | Disposable process owns COM, bounded UI tree, and cached live control handles |
| browser.py | Optional Playwright/CDP adapter with exact handles and visual fallback |
| router.py | Optional DOM enrichment only when the explicitly selected tab matches the app title |
| bootstrap.py | Reads existing settings and constructs a real model/driver/runtime |
| journal.py | Optional metadata-only JSONL records |
| evaluate.py | Summarizes run status, time, calls, actions, bytes and routes |
| engine_bridge.py | Worker-thread execution, queued Qt presentation signals and approval dialog |
| ui_computer_use.py | Passive breathing purple edge aura and glowing purple/white banner; capture exclusion and synchronized multi-monitor presentation |

## Observation and targeting rules

An observation includes the screenshot, origin and scale, visible controls,
application identity, current URL where available, warnings, and a unique ID.
The model must echo that ID. Target IDs are short marks valid only for that
observation; they resolve to cached live controls, never a fuzzy text search.
Codex requests use strict JSON-schema output with an observation-specific ID
and current target IDs. The provider receives no computer-use/action tools: it
returns a decision, while the local runtime validates it and dispatches through
its own Windows or browser drivers. Other configured providers receive the same
schema in their instructions, without assuming a shared structured-output API.
Both paths still pass the same local semantic validation; malformed or stale output never
dispatches input. Nullable unused fields support the strict schema without
accepting bare actions, type aliases, or flattened targets.

For custom controls, the model can supply a normalized screenshot bounding box
and confidence of at least 0.85. Confidence is a model estimate, not a measured
probability of correctness. A new screenshot checks that the target region is
still consistent before input. The driver rejects movement, navigation, control
replacement, disabled elements, or a changed foreground app.

Windows keeps physical screen coordinates separate from downscaled image
coordinates and preserves negative monitor origins. Browser screenshots use CSS
pixel scale, matching DOM bounding boxes even on a high-DPI display.

The computer-use frame and ghost cursor request Windows capture exclusion so
their animation does not become evidence of app progress. That mechanism depends
on Windows/DWM support; it is not a secrecy guarantee. The aura and banner halo
breathe together on a 4.2-second cycle using one monotonic clock across monitors.
The approximately 30-fps presentation timer only runs during active computer use,
repainting the edge strips/banner rather than the desktop center. Escape and
shutdown stop it immediately. This adds no model calls or execution delays.

## Efficient platform access

The Windows worker walks the selected window, not the entire desktop. It bounds
the walk by depth, count, and time and keeps wrappers in its own COM process.
The parent imposes a hard response timeout and terminates an unresponsive worker.
This prevents a native UIA provider hang from blocking the Qt event loop.

Native Invoke, SelectionItem, Toggle, and Value patterns are preferred.
Synthetic input is used only after a pattern reports unsupported *before*
dispatch. Exceptions or timeouts after dispatch are treated as unknown effects,
so the runtime does not replay that click through another route.

The optional browser adapter uses bounded visible DOM controls, exact element
handles, and the browser's native fill/click/navigation operations. The model
cannot send arbitrary JavaScript or selectors. It can fall back to the current
screenshot for canvas or iframe controls.

The hybrid adapter is intentionally explicit: set a loopback CDP endpoint and,
when multiple pages exist, a page index. Browser app title matching is a
conservative heuristic, not a browser extension identity guarantee. Browser-only
mode is preferable when a task must stay in one selected tab. New popups/tabs are
not automatically selected.

## Context and verification cost

Each decision receives one current annotated screenshot plus at most 100
structured controls, the eight most recent conversation turns, milestones, feedback,
and the eight most recent receipts. Prior screenshot history is not resent.
The last annotation/base64 encoding is cached for invalid-response repairs.

Exact checks run locally after an action. The next normal decision also assesses
the prior result; routine steps do not require an extra verifier model call.
Only a request to finish triggers a separate fresh full-state goal check.

### Task autonomy and follow-ups

The actor instructions prefer routine reversible work over clarification: inspect the
current UI, search the supplied words despite typos, choose reasonable defaults,
and complete independent clear parts of a multi-part request. Essential missing
private input and consequential choices are not guessed. These instructions are
behavioral guidance, not a guarantee that every task will succeed.

In active mode, the first proposed `ask` gets one review opportunity within the
existing decision/model/time budgets. The runtime supplies the proposed question
as feedback and takes a fresh observation; it does not expose that question as a
final reply yet. The model may act, inspect, or still ask if genuinely blocked.
There is at most one review per run, no review on the normal action path, and no
extra review in explicit CLI Help mode. Review counts are included in metadata.

An unfinished active task is kept in memory with its original goal, user follow-up
instructions, milestones, up to eight recent receipts, and uncertain-action
fingerprints. On the next request, the normal decision call chooses `continue_task`
in the typed response. A continuation restores that task's goal and progress;
an unrelated new task starts fresh, and an ordinary answer does not automatically
resume the pending task. Once selected, the pending-task candidate is removed
from the current decision context so it cannot distract a new task. Continuations
must still pass target validation and policy checks, and cannot use an ordinary
`answer` to bypass final-goal verification. Completion or cancellation clears the
pending task. This is not durable resumption: restarting Helper loses this state,
and saved transcripts alone do not restore it.

Exact search submissions and ordinary field filling avoid keyword-only consent
false positives. Explicit confirmation requests, credential fields, consequential
commit controls, and terminal commits retain their checks. Policy remains a
heuristic boundary, not a complete security system.

The independent verifier call shares the configured model but has separate
instructions. It is not an independent external benchmark judge. Partial UIA/DOM
snapshots cannot prove an element is absent. Pixel changes detect possible
progress; they never establish success by themselves.

Activity is reported by runtime events, not invented model narration. Observation
and model-call start events fire before the corresponding work. The desktop uses
neutral initial progress, the selected action at dispatch, follow-up screen checks,
next-decision progress, and final verification while it is actually in flight. A
proposed finish message is not displayed as a completed result before verification.
These stage labels are not a model-reasoning transcript and add no model calls.
The current loop still takes an initial screen observation for ordinary questions;
changing the status label does not create a screen-free chat path.

The screenshot-byte metric tracks source PNG bytes submitted for calls. It does
not claim exact billable image tokens, which vary by model and provider.

## Failure and cancellation behavior

- A parser or model error cannot create a successful empty plan.
- Invalid schemas get bounded correction opportunities and perform no input.
- Stale decisions cause a fresh observation and new grounding.
- Validation errors before dispatch never become receipts claiming a possible click.
  Cancellation is checked again after validation and after presentation callbacks.
- Ambiguous post-dispatch effects prevent repeating the matching non-idempotent action,
  including after an in-memory task continuation.
- A failed finish can be corrected; observable non-rejected/non-unknown action progress
  resets the consecutive failed-finish counter. Two unproven finishes without new
  progress still stop rather than spin indefinitely.
- Repeated clicks on unchanged state are blocked and eventually request user help.
  "Unchanged" compares each control's name, role, geometry, value, and state
  flags (enabled, visible, focused, password), so a newly enabled button is
  never mistaken for the same scene.
- Receipts are created before post-action waiting, preserving effects on cancellation.
- Escape and shutdown request cancellation. The desktop frame hides immediately on Escape.
  The runtime checks
  before model calls, after them, during waits, and immediately before input.
- In-flight HTTP/model calls and individual native/browser calls are not instantly
  interruptible. Their existing transport/driver timeout applies; no next action
  is dispatched after cancellation is observed.
- Journal failures cannot disable task execution, hide presentation events, or leave the runner busy.
- Budgets bound decisions, model calls, elapsed time between blocking calls, and
  waiting for the user. The outcome distinguishes completed, answered,
  needs_input, failed, cancelled, and limited.

## Extending it

The model chooses typed actions; platform adapters choose their implementation.
An Office adapter can implement the same Driver interface and return structured
read-back evidence. A dedicated visual grounder can propose Target boxes before
schema validation. A replay driver can feed recorded/synthetic observations
through the exact runtime. These seams exist; Office COM, a trained grounding
model, autonomous multi-tab routing, durable task resumption, and learned
workflow macros are not implemented in this rewrite.

## Measuring whether it is better

Run the same tasks, model/version, reasoning settings, starting files, app
versions, permissions, and single-attempt budgets against each candidate.
Use an external judge that inspects final documents/app state. Report binary
completion, latency, model tokens/cost, action count, interventions, recovery,
and permission adherence across repeated trials.

Helper writes metadata journals under logs/computer-use. Summarize them with:

~~~powershell
.\.venv\Scripts\python.exe -m helper_engine.evaluate logs/computer-use
~~~

The completion rate from those journals is the runtime's own verified completion
rate. It is useful for regressions, not evidence of superiority over other
systems. The current tests establish behavior under controlled conditions;
broad real-task superiority requires a separate evaluation.

The optional Windows integration check opens a disposable, credential-free Qt
fixture in a separate process. It uses UIA Value and Invoke patterns to fill its
field and click its button, checks the resulting value and label, and terminates
the fixture. It does not send physical keyboard or pointer input:

~~~powershell
$env:HELPER_TEST_UIA="1"
.\.venv\Scripts\python.exe -m pytest tests/test_engine_adapters.py -k WindowsIntegration -q
~~~

## Platform references

- [Windows UIA patterns](https://learn.microsoft.com/en-us/windows/win32/winauto/uiauto-controlpatternsoverview)
- [pywinauto UIA controls](https://pywinauto.readthedocs.io/en/latest/code/pywinauto.controls.uia_controls.html)
- [Playwright CDP connection](https://playwright.dev/python/docs/api/class-browsertype#browser-type-connect-over-cdp)
- [Windows capture exclusion](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setwindowdisplayaffinity)
