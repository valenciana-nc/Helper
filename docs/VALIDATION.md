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

The historical v2 tests emit one pywinauto COM-threading warning. Production UIA
access in the new engine lives in its own disposable process.

Reproduce with the commands in README.md. Browser executable location is an
environment-specific test setting and is not hardcoded into production code.
