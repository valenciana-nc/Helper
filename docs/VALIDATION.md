# Validation

Validated with Python 3.12 in the repository virtual environment. The full suite,
including the isolated browser fixture and native UIA fixture, passed:

- 188 passed, 20 subtests passed in 58.61 seconds.
- Ruff passed; compileall completed; `pip check` reported no broken requirements.
- Git whitespace checks passed after the final cleanup.

The tests cover the observation-driven runtime, strict provider schemas, action
validation and recovery, cancellation, UI lifecycle, multi-monitor geometry,
local-first voice, WAV conversion, microphone recording, and provider adapters.
The browser integration uses an isolated local fixture. The native UIA fixture
uses a disposable test window, not the user's desktop. No paid model inference
was required.

The previous plan-once executor, its Qt bridge, the old screen-capture module,
and unused function-tool/route-classifier APIs have been removed. The active
voice runtime and its tests were moved out of the retired package; voice and the
current engine remain covered by the main `tests/` suite.

Reproduce with the commands in `README.md`. Browser executable location is an
environment-specific test setting and is not hardcoded into production code.
