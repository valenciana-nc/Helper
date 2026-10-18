# Testing Helper-E

## Quick start

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt   # Windows: full set
# Linux: install the subset (PyQt6, pytest, pytest-qt); skip pywin32, winrt-*, pywinauto
xvfb-run -a env QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/ v2/tests/ -q -p no:cacheprovider
```

## Layout

- `tests/test_engine.py` — core runtime: protocol parsing, verification evidence,
  policy gate, runtime loop, perception, bootstrap validation.
- `tests/test_engine_adapters.py` — Qt bridge, Windows UIA worker, browser/Hybrid
  drivers (headless Chromium fixture where available).
- `tests/test_engine_provider_contract.py` — provider message translation
  (Codex/Anthropic/Gemini/OpenAI-compatible) and final-reply visibility.
- `tests/test_core.py` — config, conversation store, launch safety, UI widgets.
- `tests/test_overlay_geometry.py` — overlay highlight geometry.
- `v2/tests/` — historical v2 baseline (research only, not executed by the app).

## Conventions

- Tests are hermetic: no network, no real credentials. Provider clients are
  replaced with recording fakes.
- Qt tests need `QT_QPA_PLATFORM=offscreen` (and `xvfb-run` on headless Linux).
- `-p no:cacheprovider` keeps the repo free of `.pytest_cache` noise.
- Name new test classes after the module under test (`PerceptionTests`,
  `BootstrapTests`, `LimitsTests`, `RouterTests`).

## What "green" means

Full suite green: 252 passed, 3 skipped, 16 subtests passed. The 3 skips are
expected (Windows-only paths on Linux, plus the historical v2 Linux-only
world-model branch).
