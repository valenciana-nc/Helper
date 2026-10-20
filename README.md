<div align="center">
  <img src="assets/helper_logo.png" alt="Orb Helper logo" width="180" />
  <h1>HELPER</h1>
  <p><strong>A floating Windows assistant that teaches or performs computer tasks.</strong></p>
</div>

Helper combines chat, voice, screen awareness, and a computer-use runtime.
Ask questions or give it a task in the same chat—there is no Help/Act switch.
Helper answers questions, explains how to do things, or uses the computer when you ask it to act.
A soft lavender edge aura and purple/white status banner appear during computer use; Escape stops the task.
The chat-bubble icon and the lava sphere both open the floating chat input.

## New computer-use architecture

The desktop app and CLI use helper_engine through engine_bridge.py.
Read the [architecture and design trade-offs](docs/COMPUTER_USE_ARCHITECTURE.md).

- Current observations: screenshot, app identity, visible controls, and explicit pixel geometry.
- One next action at a time: validated JSON with observation-scoped target IDs.
- Strict schema output on Codex; schema-guided output and bounded repairs on other providers.
- Structured access first: Windows UI Automation patterns or optional browser DOM handles.
- Visual fallback: current screenshot boxes for custom controls, with fresh-scene validation.
- Outcome evidence: local value/focus/text checks, compact action receipts, and separate goal verification.
- Bounded recovery: stale-target rejection, unknown-effect handling, cancellation, and run budgets.
- The CLI's explicit Help mode is enforced in the runtime: no desktop input is dispatched.
- Metrics: observations, calls, bytes, action routes, elapsed time, and final status.

The desktop app and CLI use the same observation-driven runtime. Voice input
and speech output are provided separately by `audio_recorder.py` and
`helper_voice.py`.

## Setup

~~~powershell
cd $env:USERPROFILE\helperEngine
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
~~~

Copy .env.example to .env only for a new installation. Preserve an existing .env.
Start the orb:

~~~powershell
.\.venv\Scripts\pythonw.exe main.py
~~~

Use python.exe instead of pythonw.exe for a debugging console. Account, voice,
model, and appearance settings are available in the dashboard. ChatGPT sign-in
is the default chat/computer-use provider; configured OpenAI-compatible,
Anthropic, and Gemini providers remain supported. Voice provider requirements
are separate. No credentials are bundled with the engine.

## CLI

The CLI keeps an explicit Help mode (no input dispatched), which is its default:

~~~powershell
.\.venv\Scripts\python.exe -m helper_engine --mode help "Show me how to enable autosave"
.\.venv\Scripts\python.exe -m helper_engine --mode active "Open Notepad and enter hello"
~~~

Desktop control targets the visible Windows session. Keep the target app
available; Escape cancels desktop-app runs. CLI runs can be interrupted with Ctrl+C.

## Browser control

Install the optional adapter:

~~~powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-browser.txt
.\.venv\Scripts\python.exe -m playwright install chromium
.\.venv\Scripts\python.exe -m helper_engine --backend browser --mode active "Open https://example.com and read its heading"
~~~

Without an endpoint, browser mode launches an isolated Chromium session.
To attach a browser you explicitly enabled for remote debugging, configure a
loopback endpoint and a zero-based page index when there is more than one tab:

~~~ini
HELPER_CONTROL_BACKEND=browser
HELPER_BROWSER_CDP_URL=http://127.0.0.1:9222
HELPER_BROWSER_PAGE_INDEX=0
~~~

Hybrid mode uses Windows for desktop apps and DOM access when the selected tab
matches the foreground Chrome/Edge title. It requires an explicit endpoint.
Browser mode is more direct for a task confined to one selected tab.
The adapter disconnects from an attached browser rather than closing it.
Browser targets use DOM/CSS bounds. The desktop aura and pointer are not
per-element highlights inside a browser viewport.

## Configuration

Settings use the HELPER_ prefix. Legacy HELPLER_ and HARVIS_ aliases still load.

| Setting | Purpose |
|---|---|
| HELPER_CONTROL_BACKEND | desktop (default), browser, or hybrid |
| HELPER_AGENT_MODEL | Model used by the configured provider |
| HELPER_SCREENSHOT_MAX_EDGE | Windows observation resolution |
| HELPER_MAX_AGENT_STEPS | Decision budget |
| HELPER_RUN_TIMEOUT_SEC | Runtime deadline; defaults to HELPER_AGENT_TIMEOUT_SEC |
| HELPER_GUIDE_WAIT_SEC | User-action wait in Help mode |
| HELPER_SPEAK_TYPED_CHAT | Spoken replies for typed input |

The desktop uses automatic chat/task handling; old HELPER_DEFAULT_MODE settings
are ignored. Explicit no-input teaching is still available with the CLI's --mode help.
The runtime requests confirmation around recognized consequential actions. Those heuristics are not a complete security
boundary; an automation agent acts with the account and app access you give it.
Escape is checked before the next action; in-flight model/native calls complete
or time out before their worker returns.

## Diagnostics and evaluation

The app writes metadata-only journals to logs/computer-use. They exclude goals,
typed values, screen text, screenshots, and credentials. To summarize runs:

~~~powershell
.\.venv\Scripts\python.exe -m helper_engine.evaluate logs/computer-use
~~~

These are runtime metrics, not proof of benchmark superiority. Compare identical
tasks with an external final-state judge before claiming the architecture beats
another agent. Current limitations and extension points are in the architecture doc.

## Development checks

~~~powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
$env:QT_QPA_PLATFORM="offscreen"
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m compileall -q helper_engine engine_bridge.py main.py
.\.venv\Scripts\python.exe -m pytest tests -q
~~~

Tests use deterministic models, synthetic frames, and mocked device APIs.
Optional real browser checks use an isolated local HTML fixture:

~~~powershell
$env:HELPER_TEST_BROWSER = .\.venv\Scripts\python.exe -c "from playwright.sync_api import sync_playwright; p=sync_playwright().start(); print(p.chromium.executable_path); p.stop()"
.\.venv\Scripts\python.exe -m pytest tests/test_engine_adapters.py -k BrowserIntegration -q
~~~

GitHub Actions runs the full Windows suite and isolated browser checks.
For the opt-in native Windows fixture check, set HELPER_TEST_UIA=1 and run
the WindowsIntegration tests described in the architecture document.
