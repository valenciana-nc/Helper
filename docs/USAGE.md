# Using Helper

[← Project overview](../README.md) · [Architecture](COMPUTER_USE_ARCHITECTURE.md) · [Testing](TESTING.md)

## Install and launch

Helper controls the visible Windows session. Python 3.12 is the tested version.
From a source checkout:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\pythonw.exe main.py
```

Use `python.exe` instead of `pythonw.exe` for a debugging console. The app opens
its dashboard and floating orb. A new installation can copy `.env.example` to
`.env` for manual configuration; **never overwrite an existing `.env`**.
Dashboard saves also write local settings to that file.

## Desktop controls

| Control | Behavior |
|---|---|
| Click the orb or its chat icon | Open the floating input for a question or task. |
| Hold the orb or configured hotkey | Record voice input; release to transcribe and submit. |
| Esc | Cancel recording or request task cancellation and hide the computer-use glow. |
| Tray icon | Open/restore the dashboard. |
| Dashboard | View saved conversations, change Settings, and manage Account connections. |

There is no desktop Help/Act toggle. Ask **how** to do something for an explanation;
ask Helper to **do** it for computer execution. Keep the intended app available.
The purple edge aura, banner, and cursor halo appear during active computer use.

### What the activity text means

Only the initial label changes: **Thinking…** replaces the fixed “Looking at the
current app…” opener. The existing action/status display and execution behavior
stay as before. There is no added step-by-step narration, reasoning stream, or
extra model call.

The current loop collects a screen observation before every decision, including
ordinary questions. An ordinary answer does not dispatch computer input, but it
is not a screen-free chat path. Current screenshots, control text, and recent
conversation/task context are submitted to the configured model provider.

Esc is checked before input and at runtime checkpoints. In-flight model/native
calls are not instantly interruptible; their existing timeout still applies.
Consequential-action approval checks are heuristics, **not a security sandbox**.

## Connect a provider

| Provider | Setup |
|---|---|
| ChatGPT / Codex | Use **Sign in with ChatGPT** in Account. No API key is needed for this model connection. |
| Claude | **Sign in with Claude**, approve in the browser (or paste the displayed code), choose a model, then **Use subscription and restart**. An Anthropic API key is an alternative. |
| Grok | Click **Sign in with Grok**, approve the displayed code on xAI's site, choose a model, then **Use subscription and restart**. An xAI API key is an alternative. |
| Gemini | Select Gemini in Settings and supply its API key. |
| OpenAI-compatible | Set the endpoint, API key, and model in Settings. |

Grok sign-in uses native device-code OAuth for eligible **SuperGrok / X Premium**
accounts. The Grok card shows the code and official verification address, including
when the browser cannot open automatically. You can cancel while waiting. Model
access and usage limits are determined by xAI; sign-in does not guarantee access
to every model. See [Grok authentication](GROK_AUTH.md) for protocol and routing details.

Claude sign-in implements Pi's **Claude Pro/Max** OAuth flow with PKCE, a local
browser callback, and a **Copy code (manual)** option. If the callback does not
arrive, paste the final redirect URL in the card. This uses Claude Code's public
OAuth client and compatibility request format, not a separately registered Helper
integration. Anthropic may restrict third-party use. See [Claude authentication](CLAUDE_AUTH.md).

**Open Anthropic API console** and **Open xAI API console** only open their API
consoles; they do not connect Helper by themselves. API-key billing is separate
from subscriptions. Saved API keys are not automatically validated. **Save**
persists settings; restarting applies a changed provider to the running engine.
Using an API-key card explicitly selects API billing instead of subscription
access. Signing out of Claude or Grok leaves other accounts and saved API keys unchanged.

Credentials are stored locally (`.env` for API settings; Claude/Grok OAuth tokens
use separate system credential-manager entries and refresh automatically;
ChatGPT uses its own token store). Treat `.env`, saved transcripts, and shared screenshots as private.
Do not commit credentials, conversation data, or run artifacts.

## Model choices

Settings → **Providers & models** now includes the expanded OpenAI, Grok, and
Claude chat catalogues. **Refresh models** fetches the saved connection's current
account list; Claude/Grok also have a refresh button in Account. Refreshing does
not run inference or change a selected model. Public suggestions do not guarantee
subscription/endpoint access. Computer-use choices require known image support;
text-only models remain available for reasoning. Custom IDs and saved defaults
are preserved. See [model catalogues and sources](MODEL_CATALOGUE.md).

## Voice

Recording requires a working microphone and PortAudio/sounddevice. If PortAudio
is unavailable, the rest of the app can still load, but capture cannot start.
The voice bridge converts recorded WAV audio to mono 16 kHz PCM for transcription.

Transcription prefers the optional local voice runtime when available and can
fall back to OpenAI. Speech output has its own provider requirements. A ChatGPT
model sign-in alone does **not** supply an OpenAI audio API key. Review the voice
settings and optional dependency handling in [`helper_voice.py`](../helper_voice.py)
and [`voice_bridge.py`](../voice_bridge.py); no local models or credentials are bundled.

## CLI

The CLI defaults to **Help**, a no-input path in which the user performs the steps.
Use explicit active mode to execute:

```powershell
.\.venv\Scripts\python.exe -m helper_engine --mode help "Show me how to enable autosave"
.\.venv\Scripts\python.exe -m helper_engine --mode active "Open Notepad and enter hello"
```

Interrupt CLI runs with Ctrl+C. Both CLI modes use the same observation-driven
engine as the desktop; no retired executor is used.

## Optional browser control

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-browser.txt
.\.venv\Scripts\python.exe -m playwright install chromium
.\.venv\Scripts\python.exe -m helper_engine --backend browser --mode active "Open https://example.com and read its heading"
```

Without a remote-debugging endpoint, browser mode launches isolated Chromium.
To attach an explicitly enabled browser, use a loopback CDP endpoint and a
zero-based page index if more than one tab exists:

```ini
HELPER_CONTROL_BACKEND=browser
HELPER_BROWSER_CDP_URL=http://127.0.0.1:9222
HELPER_BROWSER_PAGE_INDEX=0
```

Hybrid mode uses Windows input for desktop apps and DOM access only when the
selected browser tab matches the foreground browser title. It requires an
explicit endpoint. New tabs/popups are not selected automatically. An attached
browser is disconnected, not closed. Browser coordinates are DOM/CSS pixels;
the desktop pointer is not an in-page per-element highlight.

## Configuration reference

Settings use the `HELPER_` prefix. Legacy `HELPLER_` and `HARVIS_` aliases still load.
See [`.env.example`](../.env.example) and [`config.py`](../config.py) for all fields.

| Setting | Purpose |
|---|---|
| `HELPER_CONTROL_BACKEND` | `desktop` (default), `browser`, or `hybrid`. |
| `HELPER_AGENT_MODEL` | Model used by the selected provider. |
| `HELPER_SCREENSHOT_MAX_EDGE` | Maximum Windows observation resolution. |
| `HELPER_MAX_AGENT_STEPS` | Decision budget. |
| `HELPER_RUN_TIMEOUT_SEC` | Run deadline; defaults to `HELPER_AGENT_TIMEOUT_SEC`. |
| `HELPER_GUIDE_WAIT_SEC` | Wait for a user action in CLI Help mode. |
| `HELPER_SPEAK_TYPED_CHAT` | Speak responses to typed requests. |

Old `HELPER_DEFAULT_MODE` values do not change automatic desktop task handling.
Unfinished-task follow-ups last only in the running agent. Saved conversations
do not restore executable task state after restart.

## Diagnostics and development

Run journals under `logs/computer-use` contain allowlisted metadata, not goals,
typed values, screenshots, or credentials. Summarize them with:

```powershell
.\.venv\Scripts\python.exe -m helper_engine.evaluate logs/computer-use
```

The completion rate is the runtime's own model-verified rate, not an external
benchmark. See [validation results and caveats](VALIDATION.md).

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
$env:QT_QPA_PLATFORM="offscreen"
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m compileall -q helper_engine engine_bridge.py main.py
.\.venv\Scripts\python.exe -m pytest tests -q
```

For isolated browser and native UIA integration checks, follow
[Testing](TESTING.md) and [Architecture](COMPUTER_USE_ARCHITECTURE.md).
The [validation record](VALIDATION.md) includes the known default-order browser
screenshot timeout; passing deterministic tests is not a real-task reliability claim.
