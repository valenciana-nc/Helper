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
- `tests/test_engine_adapters.py` — Qt bridge, Windows UIA worker, browser/Hybrid
  drivers (headless Chromium fixture where available).
- `tests/test_engine_provider_contract.py` — provider message translation
  (Codex/Anthropic/Gemini/OpenAI-compatible) and final-reply visibility.
- `tests/test_core.py` — config, conversation store, launch safety, UI widgets.
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

Full suite green: 213 passed, 20 subtests passed in the verified Windows run.
Some Windows-only UIA checks may be skipped on Linux.
