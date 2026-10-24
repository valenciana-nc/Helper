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
  Status-page removal, native Grok browser-code UI, cancellation, auth-mode selection,
  and API-key text/image request routing.
- `tests/test_oauth_xai.py` — isolated device-code polling, refresh/rotation,
  credential-manager failures, cancellation, and untrusted verification URLs.
- `tests/test_xai_subscription.py` — native Grok Responses routing, engine text/image/
  schema serialization, auth retries, and subscription/API-key precedence.
- `tests/test_oauth_anthropic.py` — Claude PKCE, local callback/state checks, port
  fallback, manual-code parsing, cancellation/timeout, refresh, and isolated storage.
- `tests/test_anthropic_subscription.py` — native Claude card, explicit auth modes,
  OAuth-only Messages headers/system blocks, engine serialization, and safe errors.
- `tests/test_core.py` — config, conversation store, launch safety, UI widgets.
- `tests/test_theme.py` — monochrome chrome, white surfaces, contrast, neutral controls, and purple orb branding.
  The separate computer-use UI tests cover the breathing purple edge aura,
  purple/white banner and halo, synchronized timing, dirty regions, and idle/Escape cleanup.
- `tests/test_computer_use_ui.py` and `tests/test_screen_geometry.py` — passive desktop presentation and monitor geometry.
- `tests/test_voice.py`, `tests/test_voice_bridge.py`, and `tests/test_audio_recorder.py` — active voice capture/transcription pipeline.

## Conventions

- Tests are hermetic: no external network or real credentials. Provider clients
  are replaced with recording fakes. Claude callback tests use a local loopback listener.
- Qt tests need `QT_QPA_PLATFORM=offscreen` (and `xvfb-run` on headless Linux).
- `-p no:cacheprovider` keeps the repo free of `.pytest_cache` noise.
- Name new test classes after the module under test (`PerceptionTests`,
  `BootstrapTests`, `LimitsTests`, `RouterTests`).

## What "green" means

After native Claude and Grok sign-in were added, the default-order Windows run
passed **334 tests and 262 subtests**, with **4 skipped** (`QT_QPA_PLATFORM=offscreen`).
Ruff and compilation checks also passed. No real account login or inference was
used. The earlier Grok-only run passed 304 tests and 224 subtests (4 skipped).
A separate unauthenticated Grok device-endpoint smoke check returned HTTP 200
with Helper's referrer; no browser was opened, account approved, or tokens saved.
Claude tests do not establish whether Anthropic permits a particular account/client.

The last Windows adapter-first full run before narrowing the status change passed
283 tests and 183 subtests. The initial-label-only correction then passed 125
focused tests and 43 subtests; source parity confirms no other execution or
presentation changes remain.
Default-order runs also encountered order-sensitive browser screenshot timeouts;
see `VALIDATION.md` for the results and caveat. The focused computer-use/theme/
geometry checks passed 31 tests and 113 subtests.
Some Windows-only UIA checks may be skipped on Linux.
