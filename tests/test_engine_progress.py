"""Only the initial status changes; existing task execution/presentation stays intact."""
from __future__ import annotations

import unittest

from PyQt6.QtWidgets import QApplication

from engine_bridge import EngineRunner
from helper_engine.types import Decision, RunStatus
from test_engine import FakeDriver, ScriptModel, act, agent, fill, finish, frame


class EngineProgressTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])

    def test_initial_status_is_neutral_without_starting_computer_use(self):
        statuses, control = [], []
        runner = EngineRunner()
        runner.status.connect(statuses.append)
        runner.computer_use.connect(control.append)
        model = ScriptModel([lambda obs: Decision("answer", obs.id, "Hello!")])
        driver = FakeDriver()
        report = agent(model, driver).run_goal("Hello", mode="active", on_event=runner._on_event)
        self.assertEqual(report.status, RunStatus.ANSWERED)
        self.assertEqual(statuses, ["Thinking…", "Hello!"])
        self.assertEqual(control, [])
        self.assertEqual(driver.reads, 1)
        self.assertEqual(report.metrics.model_calls, 1)
        self.assertEqual(driver.calls, [])

    def test_existing_status_behavior_after_startup_is_unchanged(self):
        runner = EngineRunner()
        statuses = []
        runner.status.connect(statuses.append)
        runner._on_event("started", {"mode": "active"})
        runner._on_event("decision", {"kind": "act", "message": "Open the app"})
        runner._on_event("action_start", {"kind": "open", "message": "Open Notepad"})
        runner._on_event("action_result", {"effect": "applied"})
        runner._on_event("goal_verification", {"status": "passed"})
        runner._on_event("finished", {})
        self.assertEqual(statuses, [
            "Thinking…", "Open the app", "Open Notepad", "Checking the result…", "Checking the complete goal…",
        ])

    def test_runtime_has_no_added_phase_events_or_model_calls(self):
        events = []
        model = ScriptModel([act(fill()), finish])
        driver = FakeDriver([frame(), frame("o2", value="Alice"), frame("o3", value="Alice")])
        report = agent(model, driver).run_goal("Enter Alice", mode="active",
                                              on_event=lambda name, payload: events.append(name))
        self.assertTrue(report.ok)
        self.assertEqual(events, ["started", "decision", "action_start", "action_result", "decision",
                                  "goal_verification", "finished"])
        self.assertEqual(report.metrics.model_calls, 3)
        self.assertEqual(len(driver.calls), 1)


if __name__ == "__main__":
    unittest.main()
