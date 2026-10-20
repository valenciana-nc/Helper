# Rewrite validation

Checked on Windows, 18 October 2026, using Python 3.12.10 in the repository's
virtual environment.

- Full suite: 226 passed, 1 skipped, 16 subtests passed in 10.87 seconds.
- The skipped test exercises the historical v2 Linux-only world-model branch.
- Ruff: passed across the repository.
- Compilation: passed for the new core, Qt bridge, entry points, and capture helper.
- Dependency consistency: pip check reported no broken requirements.
- Git whitespace check: passed.

The full run enabled both optional integrations:

1. An isolated headless Chromium fixture exercised DOM observation, Unicode fill,
   click and read-back, detached-target rejection, and the complete runtime loop.
2. A disposable native Windows Qt window exercised UIA Value and Invoke patterns
   in the new worker process, then checked its field and resulting label.
   It did not use synthetic mouse or keyboard input.

Deterministic regressions cover invalid JSON, stale observation IDs, changed
targets, uncertain side effects, repeated clicks without progress, denied
confirmation, cancellation before and after dispatch, Help mode's no-input
boundary, missing goal evidence, ambiguous controls, screenshot geometry, journal
content filtering, and Qt runner startup/mode/busy behavior.

No paid model calls were made for validation. Model decisions in tests were
scripted, so these results validate implementation behavior rather than model
judgment, autonomous real-task completion rates, or superiority to other agents.

## Automatic chat and computer-use frame

After removing the desktop Help/Act switch and revising the passive screen
presentation to a soft lavender aura, violet/white banner, and small glowing pointer, the full
suite was rerun with both optional integrations enabled:
248 passed, 1 skipped, and 16 subtests passed in 16.46 seconds. Ruff and compilation
also passed. The skip remains the historical Linux-only world-model branch.

New UI checks cover identical chat-icon/orb behavior, legacy Help settings no
longer disabling desktop actions, no frame for ordinary answers or CLI Help mode,
frame cleanup after failure and Escape, click-through/no-focus flags, capture
exclusion requests, transparent screen centers, and monitor geometry/hotplug.
Additional pixel checks cover smooth edge falloff, the violet cancel banner,
and the pointer halo. Active control no longer draws target boxes or captions.
Offscreen previews were rendered and inspected. These tests use
scripted models; no model inference was needed for this UI validation.

## Failed-run audit and regression validation

The latest playback failure was a stale proposal followed by three malformed
model decisions. Action fields were returned outside the decision envelope.
The request contract now uses native strict JSON-schema output on Codex and
complete nested examples/schema instructions for other providers. Local checks
remain mandatory. No raw model output, goals, or screen text were added to journals.

The audited suite passed with both optional integrations enabled:
279 passed, 1 skipped, 20 subtests passed in 120.01 seconds. Ruff, compilation,
`pip check`, and Git whitespace checks passed. New regressions cover:

- Nested decision output, current observation/target IDs, nullable unused fields,
  malformed-response repair, and the real provider-to-browser serialization path.
- Escape during native validation and presentation, pre-dispatch errors versus
  unknown post-dispatch effects, detached DOM controls, and UIA lease timeouts.
- Voice handoff to Qt, rejected second submissions preserving cancellation,
  mute/Escape stopping speech, configured speech settings, late pointer events,
  journal open/write/close failures, and worker-thread startup failure.
- Saved settings surviving restart, failed/invalid saves not restarting,
  unsafe custom endpoints, invalid budgets, provider-specific model defaults,
  and startup animation not overriding an active task.

Separate live-model probes used synthetic observations or isolated browser
pages, not the user's desktop. The playback-state fixture completed with one
DOM click and three model calls, with no format rejections. A real YouTube probe
reported that its video was unavailable and returned needs_input; it is not a
successful real-video playback result. These probes do not establish arbitrary
Windows-task reliability or benchmark performance.

The historical v2 tests emit one pywinauto COM-threading warning. Production UIA
access in the new engine lives in its own disposable process.

Reproduce with the commands in README.md. Browser executable location is an
environment-specific test setting and is not hardcoded into production code.
