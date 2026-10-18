# Helper-E v2 — Architecture Spec

> Historical design. The production app and CLI now use helper_engine.
> See [the current architecture](../docs/COMPUTER_USE_ARCHITECTURE.md).
> The following spec records the old research baseline, not current guarantees.
> v2/__main__.py forwards to helper_engine; the v2 control loop is not executed.

A clean-room rebuild of the agent stack alongside v1. v1 stays untouched;
v2 lives in this package.

## Design principles

1. **Nothing is re-derived from scratch between steps.** The world model persists;
   observations are diffs; grounding reuses the cached tree.
2. **The model never emits raw coordinates.** Set-of-marks: it returns mark IDs.
3. **Every action is verified.** Act → re-observe (targeted) → verify → retry (≤3×)
   before the planner is ever re-invoked.
4. **Plan once.** The planner produces a subgoal DAG; the executor walks it.
5. **Platform-gated.** Windows-only paths (UIA events, WinRT) degrade gracefully
   on other platforms. No import-time crashes anywhere.

## Modules

### `world.py` — persistent world model
```python
@dataclass(frozen=True)
class Element:            # one UI element, stable identity
    id: str               # stable within a session: f"{pid}:{runtime_id}"
    control_type: str
    name: str
    rect: tuple[int,int,int,int]  # screen px: x, y, w, h
    enabled: bool
    visible: bool
    value: str | None     # text/value pattern if available

@dataclass
class ScreenFrame:        # one observed moment
    seq: int
    png: bytes            # full-frame PNG (encoded ONCE per frame)
    width: int; height: int
    elements: dict[str, Element]   # id -> Element, from tree snapshot
    changed_ids: set[str]          # ids added/removed/moved since last frame

class WorldModel:
    def __init__(self, poll_interval_s: float = 0.5)
    def start(self) -> None        # begins background refresh (events if available, else poll)
    def stop(self) -> None
    def current_frame(self) -> ScreenFrame
    def wait_for_change(self, timeout_s: float) -> ScreenFrame | None
    def element(self, element_id: str) -> Element | None
```
- Windows: subscribe to UIA structure-changed events; fall back to polling.
- Non-Windows: polling only (still functional; tree may be empty → vision fallback).
- `changed_ids` computed by diffing element dicts (added/removed/rect-changed/value-changed).

### `observe.py` — set-of-marks annotation
```python
@dataclass(frozen=True)
class Mark:
    n: int                # 1-based label drawn on the image
    element_id: str

def annotate(frame: ScreenFrame, *, max_marks: int = 40) -> tuple[bytes, list[Mark]]:
    """Draw numbered badges on clickable/typable elements. Returns (annotated PNG, marks).
    Pure function of the frame. Works headless (PIL only)."""
```
- Picks top-N interactable elements (buttons, links, text fields, menu items…),
  draws a small numbered badge at each rect's top-left, returns PNG + mapping.
- The VLM is instructed: "return the mark number, never coordinates."

### `ground.py` — grounding (mark → action target)
```python
@dataclass(frozen=True)
class Target:
    element_id: str | None
    rect: tuple[int,int,int,int]   # screen px
    source: str                   # "mark" | "uia-direct" | "vision-fallback"
    confidence: float

def resolve_mark(marks: list[Mark], n: int, world: WorldModel) -> Target | None
def find_by_text(world: WorldModel, text: str) -> Target | None   # UIA name search
def vision_fallback(region_png: bytes, instruction: str, grounder) -> Target | None
    # grounder: callable(region_png, instruction) -> (x,y,w,h) in region px, or None.
    # Default grounder is None → returns None (pluggable: OS-Atlas/UGround later).
```

### `plan.py` — planner (goal → subgoal DAG)
```python
@dataclass
class Subgoal:
    id: str
    description: str            # e.g. 'Click the "Save" button'
    success_criteria: str       # e.g. 'Save dialog is visible'
    target_hint: str | None     # e.g. 'button named Save'
    done: bool = False

@dataclass
class Plan:
    goal: str
    subgoals: list[Subgoal]

def make_plan(goal: str, llm: LlmPort, world: WorldModel) -> Plan
    # Single LLM call. Prompt includes annotated screenshot + element names.
```
`LlmPort` (defined in `agent.py`, imported by plan/verify):
```python
class LlmPort(Protocol):
    def chat(self, messages: list[dict], *, system: str | None = None,
             max_tokens: int = 1024) -> str: ...
```

### `verify.py` — verifier (did the subgoal happen?)
```python
def verify_subgoal(subgoal: Subgoal, before: ScreenFrame, after: ScreenFrame,
                   llm: LlmPort) -> bool:
    # 1. Fast path: pixel-diff of the target region; if nothing changed → False.
    # 2. Ask the VLM on CROPPED before/after regions + success_criteria → "yes"/"no".
    # Never a full screenshot; crops only.
```

### `execute.py` — closed-loop executor
```python
@dataclass
class ActionResult:
    ok: bool
    detail: str
    attempts: int

class Actuator(Protocol):       # implemented per platform
    def click_element(self, el: Element) -> bool      # UIA InvokePattern; False if unsupported
    def set_value(self, el: Element, text: str) -> bool
    def click_at(self, x: int, y: int) -> None         # synthetic fallback
    def type_text(self, text: str) -> None
    def press_key(self, key: str) -> None

def do_subgoal(subgoal: Subgoal, world: WorldModel, actuator: Actuator,
               llm: LlmPort, *, max_attempts: int = 3) -> ActionResult:
    # 1. Ground: mark/UIA-text → Target (no coordinates from the model).
    # 2. Actuate: element pattern first (no cursor), else synthetic at rect center.
    # 3. Re-observe: world.wait_for_change(0.3) or fresh frame; crop-diff the target region.
    # 4. Verify via verify.verify_subgoal on crops.
    # 5. On failure: re-ground from the fresh frame, retry. After max_attempts → not ok.
```

### `voice.py` — streaming voice
```python
class Vad(Protocol):
    def is_speech(self, pcm16_16k: bytes) -> bool: ...

class SttStreamer:          # local-first
    def __init__(self, *, prefer_local: bool = True)
    def start(self) -> None
    def feed(self, pcm16_16k: bytes) -> str | None   # returns new partial text, if any
    def finish(self) -> str                          # final transcript
    # Local: faster-whisper (lazy import; raises RuntimeError with install hint if missing).
    # Cloud fallback: whisper-1 via v1 openai_client (lazy import).

class TtsStreamer:
    def speak(self, text_stream) -> None   # sentence-chunked; first audio <1s after first chunk
    def stop(self) -> None                 # barge-in: halts playback immediately
    # Local: lazy (e.g. kokoro/onnx) if installed; else v1 cloud TTS chunked by sentence.
```

### `agent.py` — the v2 loop
```python
class HelperV2:
    def __init__(self, *, llm: LlmPort, actuator: Actuator,
                 world: WorldModel | None = None)
    def run_goal(self, goal: str, *, on_event: Callable[[str, dict], None] | None = None) -> dict:
        # world.start()
        # frame = world.current_frame(); annotated, marks = annotate(frame)
        # plan = make_plan(goal, llm, world)          # ONE planning call
        # for subgoal in plan.subgoals:
        #     result = do_subgoal(subgoal, world, actuator, llm)   # closed loop
        #     subgoal.done = result.ok
        #     if not result.ok and not self._replan(...): break
        # return {"goal": goal, "done": [...], "failed": [...]}
    # Events emitted: "plan", "subgoal_start", "attempt", "subgoal_done",
    #                 "subgoal_failed", "replan", "finished".
```

### `v2/__main__.py`
`python -m v2 "goal text"` — builds a real LlmPort from v1's `openai_client`
(Codex default), a synthetic/Xvfb-safe actuator on non-Windows, and runs one goal.

## Testing
`v2/tests/test_v2_*.py`, run under pytest with the repo's suite. Rules:
- No network, no real LLM, no display in unit tests — fake `LlmPort`,
  `FakeActuator`, synthetic `WorldModel` frames.
- Windows-only paths tested via mocks or skipped with reason.
- Every public function in SPEC has at least one test.
