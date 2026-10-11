"""Tests for v2 integration pieces: llm adapter, actuator, bridge helpers."""
from __future__ import annotations

import io
import struct
import sys
import types
import wave

import pytest

from v2.actuator import V2Actuator


# ---------------------------------------------------------------- llm adapter

def test_llm_adapter_uses_v1_client(monkeypatch):
    import v2.llm as llm_mod

    calls = {}

    class FakeResult:
        text = "hello from v1"

    class FakeClient:
        def chat(self, messages, *, model, system_prompt=None,
                 max_tokens=None):
            calls["model"] = model
            calls["system"] = system_prompt
            calls["n"] = len(messages)
            return FakeResult()

    fake_mod = types.ModuleType("openai_client")
    fake_mod.make_openai_client = lambda: FakeClient()
    monkeypatch.setitem(sys.modules, "openai_client", fake_mod)

    adapter = llm_mod.V2LlmAdapter(model="test-model")
    out = adapter.chat([{"role": "user", "content": "hi"}],
                       system="sys", max_tokens=10)
    assert out == "hello from v1"
    assert calls["model"] == "test-model"
    assert calls["system"] == "sys"


def test_llm_adapter_empty_text():
    import v2.llm as llm_mod

    class FakeResult:
        text = ""

    class FakeClient:
        def chat(self, *a, **k):
            return FakeResult()

    fake_mod = types.ModuleType("openai_client")
    fake_mod.make_openai_client = lambda: FakeClient()
    sys.modules["openai_client"] = fake_mod
    try:
        adapter = llm_mod.V2LlmAdapter()
        assert adapter.chat([]) == ""
    finally:
        del sys.modules["openai_client"]


# ---------------------------------------------------------------- actuator

def _fake_pyautogui(monkeypatch):
    calls = []
    mod = types.ModuleType("pyautogui")

    def click(x, y): calls.append(("click", x, y))
    def write(t): calls.append(("write", t))
    def press(k): calls.append(("press", k))

    mod.click = click
    mod.write = write
    mod.press = press
    monkeypatch.setitem(sys.modules, "pyautogui", mod)
    return calls


def test_actuator_synthetic_click(monkeypatch):
    calls = _fake_pyautogui(monkeypatch)
    act = V2Actuator()
    act.click_at(10, 20)
    assert calls == [("click", 10, 20)]


def test_actuator_type_and_key(monkeypatch):
    calls = _fake_pyautogui(monkeypatch)
    act = V2Actuator()
    act.type_text("hi")
    act.press_key("enter")
    assert ("write", "hi") in calls and ("press", "enter") in calls


def test_actuator_element_paths_fail_gracefully_off_windows(monkeypatch):
    # On this Linux box UIA is unavailable → element ops return False.
    act = V2Actuator()
    assert act.uia_available is False

    class El:
        id = "1:2"
        rect = (0, 0, 10, 10)

    assert act.click_element(El()) is False
    assert act.set_value(El(), "x") is False


# ---------------------------------------------------------------- bridge helpers (no Qt needed)

def _make_wav(pcm_bytes: bytes, n_channels=1, sampwidth=2,
              framerate=16000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(n_channels)
        wf.setsampwidth(sampwidth)
        wf.setframerate(framerate)
        wf.writeframes(pcm_bytes)
    return buf.getvalue()


def test_wav_to_pcm16_mono():
    from v2_bridge import wav_to_pcm16

    pcm = struct.pack("<4h", 100, -100, 1000, -1000)
    out = wav_to_pcm16(_make_wav(pcm))
    assert out == pcm


def test_wav_to_pcm16_stereo_to_mono():
    from v2_bridge import wav_to_pcm16

    stereo = struct.pack("<4h", 100, 200, 300, 400)  # 2 frames
    out = wav_to_pcm16(_make_wav(stereo, n_channels=2))
    vals = struct.unpack("<2h", out)
    assert vals == (150, 350)


def test_highlight_actuator_reports_rects():
    from v2_bridge import _HighlightActuator

    seen = []

    class Inner:
        def click_element(self, el): return True
        def set_value(self, el, t): return True
        def click_at(self, x, y): pass
        def type_text(self, t): pass
        def press_key(self, k): pass

    class El:
        id = "1:2"
        name = "Save"
        rect = (10, 20, 80, 30)

    wrap = _HighlightActuator(Inner(), lambda r, l: seen.append((r, l)))
    assert wrap.click_element(El()) is True
    assert seen[0][0] == (10, 20, 80, 30)
    wrap.click_at(100, 200)
    assert seen[1][0] == (88, 188, 24, 24)  # 24px box centered
