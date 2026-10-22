# Testing Helper-E

## Quick start

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt   # Windows: full set
# Linux: install the subset (PyQt6, pytest, pytest-qt); skip pywin32, winrt-*, pywinauto
xvfb-run -a env QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/ -q -p no:cacheprovider
```

## Layout

- `tests/test_engine.py` — core runtime: protocol parsing, verification evidence,
  policy gate, runtime loop, perception, bootstrap validation.
- `tests/test_agent_autonomy.py` — act-first clarification review, budget/cancellation
  boundaries, in-memory follow-ups, preserved progress/uncertain effects, full-goal
  verification, and harmless-search versus consequential-action policy checks.
- `tests/test_engine_progress.py` — initial-label-only correction, unchanged
  subsequent status behavior, and no added runtime phase events/model calls.
- `tests/test_readme.py` — compact overview, resolving documentation links, purple
  web artwork, and a self-contained action-loop/verification SVG.
- `tests/test_engine_adapters.py` — Qt bridge, Windows UIA worker, browser/Hybrid
  drivers (headless Chromium fixture where available).
- `tests/test_engine_provider_contract.py` — provider message translation
  (Codex/Anthropic/Gemini/OpenAI-compatible) and final-reply visibility.
- `tests/test_provider_accounts.py` — Claude/Grok account cards, safe persistence,
  Status-page removal, provider selection, and xAI text/image request routing.
- `tests/test_core.py` — config, conversation store, launch safety, UI widgets.
- `tests/test_theme.py` — monochrome chrome, white surfaces, contrast, neutral controls, and purple orb branding.
  The separate computer-use UI tests cover the breathing purple edge aura,
  purple/white banner and halo, synchronized timing, dirty regions, and idle/Escape cleanup.
- `tests/test_computer_use_ui.py` and `tests/test_screen_geometry.py` — passive desktop presentation and monitor geometry.
- `tests/test_voice.py`, `tests/test_voice_bridge.py`, and `tests/test_audio_recorder.py` — active voice capture/transcription pipeline.

## Conventions

- Tests are hermetic: no network, no real credentials. Provider clients are
  replaced with recording fakes.
- Qt tests need `QT_QPA_PLATFORM=offscreen` (and `xvfb-run` on headless Linux).
- `-p no:cacheprovider` keeps the repo free of `.pytest_cache` noise.
- Name new test classes after the module under test (`PerceptionTests`,
  `BootstrapTests`, `LimitsTests`, `RouterTests`).

## What "green" means

The last Windows adapter-first full run before narrowing the status change passed
283 tests and 183 subtests. The initial-label-only correction then passed 125
focused tests and 43 subtests; source parity confirms no other execution or
presentation changes remain.
Default-order runs also encountered order-sensitive browser screenshot timeouts;
see `VALIDATION.md` for the results and caveat. The focused computer-use/theme/
geometry checks passed 31 tests and 113 subtests.
Some Windows-only UIA checks may be skipped on Linux.
