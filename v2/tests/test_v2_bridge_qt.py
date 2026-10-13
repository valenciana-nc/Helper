"""Qt test for V2Runner signal flow. Requires display (xvfb)."""
from __future__ import annotations

import threading
import time

import pytest

pytestmark = pytest.mark.skipif(
    __import__("os").environ.get("QT_QPA_PLATFORM") not in (None, "offscreen")
    and not __import__("os").environ.get("DISPLAY"),
    reason="needs Qt display (xvfb)",
)

from PyQt6.QtWidgets import QApplication  # noqa: E402

from v2_bridge import V2Runner  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


class FakeReport:
    ok = True
    done = ["sg1"]
    failed = []


class FakeAgent:
    def __init__(self, release: threading.Event):
        self._release = release

    def run_goal(self, goal, on_event=None, should_abort=None):
        on_event("plan", {"subgoals": ["a"]})
        on_event("subgoal_start", {"id": "sg1", "description": "a"})
        # Hold the "running" state until the test releases us: the old
        # instant-return fake let the worker finish before the test's
        # second start_goal, making the refuse-while-running assert racy.
        self._release.wait(timeout=10)
        on_event("subgoal_done", {"id": "sg1"})
        on_event("finished", {"report": {"ok": True, "done": ["sg1"],
                                         "failed": []}})
        return FakeReport()


def test_runner_emits_signals(qapp, monkeypatch):
    runner = V2Runner()
    release = threading.Event()
    monkeypatch.setattr(runner, "_get_agent", lambda: FakeAgent(release))

    got = {"messages": [], "states": [], "finished": [], "busy": []}
    runner.v2_message.connect(lambda m: got["messages"].append(m))
    runner.v2_state.connect(lambda s: got["states"].append(s))
    runner.v2_finished.connect(lambda ok: got["finished"].append(ok))
    runner.v2_busy.connect(lambda b: got["busy"].append(b))

    assert runner.start_goal("do thing") is True
    # Second start while running must refuse (first goal is blocked).
    assert runner.start_goal("other") is False
    release.set()

    deadline = time.time() + 10
    while not got["finished"] and time.time() < deadline:
        qapp.processEvents()
        time.sleep(0.02)

    assert got["finished"] == [True]
    assert got["busy"][0] is True and got["busy"][-1] is False
    assert any("Done" in m for m in got["messages"])
    assert runner.is_running() is False
