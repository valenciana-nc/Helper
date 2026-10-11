"""Qt bridge for Helper-E v2.

Runs the v2 engine (plan-once, set-of-marks, closed-loop) in a worker thread
and translates v2 events into Qt signals the desktop app understands.

`v2/` stays Qt-free; all Qt lives here.
"""
from __future__ import annotations

import logging
import threading
import wave
from io import BytesIO
from typing import Any, Callable

from PyQt6.QtCore import QObject, pyqtSignal

log = logging.getLogger("helper.v2bridge")


def wav_to_pcm16(wav_bytes: bytes) -> bytes:
    """Strip a WAV container → raw mono int16 PCM bytes (v1 records 16kHz)."""
    with wave.open(BytesIO(wav_bytes), "rb") as wf:
        n_channels = wf.getnchannels()
        sampwidth = wf.getsampwidth()
        frames = wf.readframes(wf.getnframes())
    import numpy as np

    audio = np.frombuffer(frames, dtype=np.int16 if sampwidth == 2 else np.int32)
    if sampwidth != 2:
        audio = (audio >> 16).astype(np.int16)
    if n_channels > 1:
        audio = audio.reshape(-1, n_channels).mean(axis=1).astype(np.int16)
    return np.ascontiguousarray(audio).tobytes()


def transcribe_v2(wav_bytes: bytes, *, prefer_local: bool = True) -> str:
    """Transcribe WAV bytes through v2's streaming STT (local-first)."""
    from v2.voice import SttStreamer

    pcm = wav_to_pcm16(wav_bytes)
    stt = SttStreamer(prefer_local=prefer_local)
    stt.start()
    try:
        # Feed in ~1s chunks so partials stream even for recorded audio.
        chunk = 32000  # 16000 Hz * 2 bytes
        for i in range(0, len(pcm), chunk):
            stt.feed(pcm[i : i + chunk])
        return stt.finish().strip()
    finally:
        try:
            stt.finish()
        except Exception:
            pass


class _HighlightActuator:
    """Wraps a v2 Actuator, reporting each action's target rect for the UI."""

    def __init__(
        self,
        inner: Any,
        on_target: Callable[[tuple[int, int, int, int], str], None],
    ) -> None:
        self._inner = inner
        self._on_target = on_target

    def _report_el(self, el: Any, label: str) -> None:
        try:
            rect = tuple(int(v) for v in el.rect)
            self._on_target(rect, label)
        except Exception:
            pass

    def click_element(self, el: Any) -> bool:
        self._report_el(el, getattr(el, "name", "") or "click")
        return bool(self._inner.click_element(el))

    def set_value(self, el: Any, text: str) -> bool:
        self._report_el(el, f"type into {getattr(el, 'name', '')}")
        return bool(self._inner.set_value(el, text))

    def click_at(self, x: int, y: int) -> None:
        self._on_target((int(x) - 12, int(y) - 12, 24, 24), "click")
        self._inner.click_at(x, y)

    def type_text(self, text: str) -> None:
        self._inner.type_text(text)

    def press_key(self, key: str) -> None:
        self._inner.press_key(key)


class V2Runner(QObject):
    """Runs v2 goals off the GUI thread; emits Qt signals for the app."""

    v2_message = pyqtSignal(str)                    # chat bubble text
    v2_status = pyqtSignal(str)                     # status line text
    v2_state = pyqtSignal(str)                      # FloatingCircle state
    v2_highlight = pyqtSignal(int, int, int, int, str)  # x,y,w,h,label
    v2_speak = pyqtSignal(str)                      # text to speak aloud
    v2_finished = pyqtSignal(bool)                  # overall ok
    v2_busy = pyqtSignal(bool)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._lock = threading.Lock()
        self._running = False
        self._agent = None  # lazy HelperV2

    def is_running(self) -> bool:
        with self._lock:
            return self._running

    def _get_agent(self):
        if self._agent is None:
            from v2.actuator import V2Actuator
            from v2.agent import HelperV2
            from v2.llm import V2LlmAdapter

            inner = V2Actuator()
            wrapped = _HighlightActuator(inner, self._on_action_target)
            self._agent = HelperV2(llm=V2LlmAdapter(), actuator=wrapped)
        return self._agent

    def _on_action_target(
        self, rect: tuple[int, int, int, int], label: str
    ) -> None:
        x, y, w, h = rect
        self.v2_highlight.emit(x, y, w, h, label[:60])

    def start_goal(self, goal: str) -> bool:
        """Start a v2 goal in a worker thread. False if one is running."""
        with self._lock:
            if self._running:
                return False
            self._running = True
        self.v2_busy.emit(True)
        thread = threading.Thread(
            target=self._run, args=(goal,), daemon=True,
            name="v2-goal",
        )
        thread.start()
        return True

    def _run(self, goal: str) -> None:
        ok = False
        try:
            agent = self._get_agent()
            report = agent.run_goal(goal, on_event=self._on_v2_event)
            ok = report.ok
            summary = self._summarize(report)
            self.v2_message.emit(summary)
            self.v2_speak.emit(summary)
        except Exception as exc:
            log.exception("v2 goal failed")
            self.v2_message.emit(f"v2 error: {exc}")
        finally:
            with self._lock:
                self._running = False
            self.v2_busy.emit(False)
            self.v2_finished.emit(ok)

    def _on_v2_event(self, name: str, payload: dict) -> None:
        # Called on the worker thread; signals queue to the GUI thread.
        if name == "plan":
            n = len(payload.get("subgoals", []))
            self.v2_status.emit(f"v2 plan: {n} step(s)")
            self.v2_state.emit("thinking")
        elif name == "subgoal_start":
            self.v2_status.emit(f"v2: {payload.get('description', '')}")
            self.v2_state.emit("thinking")
        elif name == "subgoal_done":
            self.v2_message.emit(f"✓ {payload.get('id')}")
        elif name == "subgoal_failed":
            self.v2_message.emit(
                f"✗ {payload.get('id')}: {payload.get('detail', '')}")
        elif name == "replan":
            self.v2_status.emit("v2 replanning…")
        elif name == "finished":
            self.v2_status.emit("")
            self.v2_state.emit("idle")

    @staticmethod
    def _summarize(report) -> str:
        if report.ok:
            n = len(report.done)
            return f"Done — {n} step(s) completed and verified."
        failed = ", ".join(report.failed) or "unknown"
        done = len(report.done)
        return (f"Couldn't finish after {done} verified step(s). "
                f"Stuck on: {failed}.")
