<div align="center">
  <img src="assets/helper_logo.png" alt="Orb Helper logo" width="180" />
  <h1>HELPER</h1>
  <p><strong>A floating Windows assistant with chat, voice, screen awareness, and guided computer help.</strong></p>
</div>

## What We Are Building

Orb Helper is a desktop assistant that lives as a small floating orb on Windows. It can open a chat window, listen for voice input, understand the current screen, guide the user with overlays, and optionally control the mouse and keyboard after confirmation.

The goal is a helper that feels always available but stays out of the way: quick to summon, clear about what it is doing, and careful around risky actions.

## Core Pieces

- **Floating orb:** a PyQt6 widget that stays on top and acts as the main entry point.
- **Dashboard:** account, voice, model, and behavior settings.
- **Chat:** typed conversations backed by ChatGPT sign-in.
- **Voice:** transcription and spoken replies through `OPENAI_API_KEY`.
- **Screen capture:** lets the assistant reason about what is visible.
- **Help mode:** shows ghost cursor guidance and highlight rectangles while the user clicks.
- **Active mode:** can execute actions, with confirmation for risky steps.

## Setup

```powershell
cd $env:USERPROFILE\Helper
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
copy .env.example .env
```

Run the orb:

```powershell
.\.venv\Scripts\pythonw.exe main.py
```

Run with a visible console while debugging:

```powershell
.\.venv\Scripts\python.exe main.py
```

## Sign-In

Chat and computer-use features use the **Sign in with ChatGPT** button in the dashboard. The app still starts when signed out, but chat and computer-use stay disabled until sign-in succeeds.

Voice transcription and spoken replies are separate and need `OPENAI_API_KEY` in `.env`.

## Configuration

Canonical settings use the `HELPER_*` prefix. Legacy `HELPLER_*` and `HARVIS_*` keys are still read as fallbacks, but the dashboard saves new changes as `HELPER_*`.

Useful settings:

- `HELPER_HOTKEY=ctrl+shift+space`
- `HELPER_DEFAULT_MODE=help`
- `HELPER_AGENT_MODEL=gpt-5.5`
- `HELPER_REASONING_MODEL=gpt-5.5`
- `HELPER_SPEAK_TYPED_CHAT=false`
- `OPENAI_API_KEY=` for voice only

## Modes

- **Help mode:** walks through a task with a ghost cursor and persistent highlight rectangles while the user does the clicks.
- **Active mode:** can execute actions. Risky actions, destructive text, purchases, sends, and terminal Enter require confirmation.

## Help Highlight Engine v2

Help mode resolves screen targets through UI Automation candidates, text matching,
fresh screenshot revalidation, geometry/visual quality checks, and a final OCR
text check before emitting an overlay. The safety policy is conservative: when
evidence is stale, ambiguous, visually misaligned, or the final OCR crop clearly
contradicts or only partially proves the expected visible label, Helper
downgrades to narration instead of highlighting a likely wrong target.

Recent guardrails also refuse neutral same-label state controls when a checkbox,
radio button, and button all look plausible; repeated table cells must have a
unique row and column context match before geometry can win; raw UIA snapping
applies the same neutral state-control ambiguity check; and stale revalidation
compares literal nearby labels plus containing section labels before preserving
an old highlight. The latest pass also groups repeated actions whose label is
split between visible text and automation ID, treats mixed cell/datagrid/grid
cell subtypes as duplicate table-cell peers, rejects raw snaps that only match
generic settings words while missing the requested settings area, reruns OCR on
the final fresh capture, and treats weak container-only dialog/window context
as contextless for generic action revalidation. Newer v4 hardening refuses
available-but-blank OCR for text-bearing targets, parses menu paths such as
`File > Export` as parent context plus leaf target, includes row and header
context during stale field revalidation, and prevents toolbar/menu actions from
borrowing unrelated column-header context by x-alignment alone. The current v4
slice extends that to ordered multi-level menu paths like `File > Export > CSV`,
symbol-only action labels such as `+`, `...`, and `X`, stale same-value grid
cells whose row context changes, and raw UIA snaps that hit a repeated row
action in the wrong containing row. The newest hardening also rejects recycled
ephemeral candidate IDs when final revalidation loses window identity or visual
context changes, treats substring-like OCR fuzzy matches such as `Save`/`Saved`
as partial crops, verifies filled dropdown/input identity through nearby labels,
requires delimited context matches to remain unique, and makes raw row-context
snapping independent of UIA enumeration order. The latest safety pass refuses
duplicate state-only checkbox/radio values unless the request supplies positional
or identity evidence, OCR-verifies slider/spinner targets that rely on nearby
labels, rejects targets hidden behind same-root child HWND overlays, and detects
stable surface IDs whose visible pane/list/menu/table identity changed at the
same rectangle. The following pass tightens the same fail-safe policy for
handleless UIA targets under same-root child overlays, repeated state controls
with identical nearby labels but missing section context, explicit row+column
cell requests whose correct cell value differs from the row label, and one-letter
OCR crops that are too weak to prove the expected target text. The newest pass
also blocks final overlays when a newly appeared same-window popup/menu surface
covers the target, rejects mixed text+numeric OCR crops such as `Quantity 4`
recognized only as `4`, refuses broad role-only row/cell/header model rectangles
that only partially expose one child candidate, and keeps duplicate current-value
dropdowns ambiguous unless section or row context distinguishes them.
This slice extends the same policy to duplicate numeric slider values, explicit
row+column cell requests whose intended cell is missing from inventory, OCR
labels with one-character suffix differences such as `Plan A`/`Plan B`, signed
or punctuated numeric text such as `$1.00`/`$100`, and over-broad fuzzy matches
such as `Cancel`/`Cancer`.
The current coverage-gate pass includes `row` and `tableitem` as structural
coverers, runs the same final foreground-cover check in synthetic highlight QA,
and distinguishes newly appeared covering surfaces from stable parent rows,
tabs, split buttons, and dialogs that legitimately own the selected child.
It also refuses a stale background target when a foreground control with the
same type and exact same rectangle is now above it; same-rectangle duplicate
controls are ignored only when they share the same foreground rank and were
already present in the previous inventory with the same identity/context.
The next hardening pass refuses current-screen revalidation when a stale
candidate ID is replaced by a nearby same-label control via fallback text
matching, and prevents table/grid cells from borrowing row or column context
evidence from candidates in another window rank.
The latest coverage/revalidation slice also rejects same-rank duplicate controls
that move onto the selected rectangle between snapshots, and refuses stale cell
revalidation when row/header context comes from a different window rank or
window title than the cell itself.
The current pass extends that same stale-context rule to action and state/input
controls, so row, section, and direct-label evidence cannot prove continuity
when it belongs to another window rank or incompatible window title.
The latest resolver hardening applies the same invariant during initial
target resolution: nearby labels, row context, sections, menu parents, and
container evidence must come from the same window rank and compatible window
title before they can make a target ID, text match, or snap look safe.
The next safety slice removes rank-only `modal`/`dialog` evidence: a duplicate
button is no longer promoted into a supposed modal solely because it belongs to
a different window rank. Helper now requires explicit modal/dialog surface
evidence, compatible foreground transient-surface evidence, or refuses the
highlight. A stale lower-rank target also cannot clean itself with only its own
`dialog`/`modal`/`popup` window title when a same-label foreground duplicate is
present. Explicit dialog/modal surface evidence must also come from the same
window rank and compatible window-title context as the target it is proving.
Duplicate same-label table/grid cells now refuse when the request lacks row,
column, or other distinguishing context, even if the stale target ID and model
rectangle both point at one duplicate.
Same-rank `option` overlays and newly appeared owning containers such as
`listitem` rows are treated as final pre-overlay blockers when they cover a
revalidated target.
Stale text-box revalidation also refuses same-rectangle foreground partial
matches, such as a foreground `Email` field replacing a background
`Billing Email` field, and rejects long-label swaps when a requested
discriminator such as `Billing` disappears from the current field label.
The newest resolver pass refuses one-word action requests such as `Click Save.`
when the only evidence is a longer action label such as `Save as`, a broad
row/container label such as `Archive Save status`, or a blank text field whose
nearby label has unrequested identity tokens such as `Billing Email`. It keeps
full-label requests, visible shortcut hints such as `Save Ctrl S`, current-value
dropdowns, and object/title matches out of that refusal path, and applies the
same rule across target IDs, text matching, candidate snapping, raw UIA
snapping, and final pre-overlay revalidation.

OCR uses native Windows OCR through PyWinRT and is optional at runtime. Set
`HELP_OCR_TEXT_VERIFY=0` to disable the OCR text gate while keeping the UIA,
visual, and stale-target guards active. Target diagnostics include `quality`,
`ocr`, resolution, candidate, and overlay fields for QA review.

## Privacy and Safety

- Local `.env` files, logs, screenshots, app data, virtual environments, and assistant state are ignored by git.
- ChatGPT sign-in tokens are stored in the OS keyring when possible.
- Active mode is opt-in for each session and keeps confirmation prompts around risky actions.

## Troubleshooting

- If sign-in fails, close other Helper instances and retry. OAuth needs local callback port `127.0.0.1:1455`.
- If chat says you are not signed in, open Dashboard > Account and sign in with ChatGPT.
- If voice says an API key is missing, add `OPENAI_API_KEY` in Dashboard > Account.
- Logs live in `logs/startup.log`.
- If the shortcut stops working after a folder rename, rerun `scratch/create_shortcut.ps1`.

## Checks

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests
.\.venv\Scripts\python.exe -m pip check
```

Help-mode target precision checks:

```powershell
.\.venv\Scripts\python.exe -m help_highlight_qa --artifacts logs/help_qa/latest
.\.venv\Scripts\python.exe -m help_live_probe --capture primary --artifacts logs/help_live_probe/primary
.\.venv\Scripts\python.exe -m help_precision_selftest --artifacts logs/help_precision_selftest/latest
```

`help_highlight_qa` runs model-free synthetic scenarios and writes failure
artifacts when a target resolves incorrectly. `help_live_probe` captures the
desktop, collects Windows UI Automation candidates, and draws their rectangles
over the screenshot so monitor/DPI alignment can be inspected.
`help_precision_selftest` opens a known local test window, captures it, resolves
the Save button through the same Help targeting pipeline, and writes pass/fail
artifacts.
