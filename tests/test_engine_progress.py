"""Activity text follows real execution stages; no fake reading or reasoning stream."""
from __future__ import annotations

from dataclasses import replace
from threading import Event
import unittest

from PyQt6.QtWidgets import QApplication

from engine_bridge import EngineRunner
from helper_engine.types import Decision, RunStatus
from test_engine import FakeDriver, ScriptModel, act, agent, fill, finish, frame


class EngineProgressTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])

    def test_question_starts_with_neutral_progress_without_computer_use_status(self):
        statuses, control = [], []
        runner = EngineRunner()
        runner.status.connect(statuses.append)
        runner.computer_use.connect(control.append)
        model = ScriptModel([lambda obs: Decision("answer", obs.id, "Hello!")])
        driver = FakeDriver()
        report = agent(model, driver).run_goal("Hello", mode="active", on_event=runner._on_event)
        self.assertEqual(report.status, RunStatus.ANSWERED)
        self.assertEqual(statuses[0], "Thinking…")
        self.assertEqual(statuses[-1], "Hello!")
        self.assertNotIn("Looking at the current app…", statuses)
        self.assertNotIn("Checking the current screen…", statuses)
        self.assertEqual(control, [])
        # This is accurate status presentation, not a claim that questions are screen-free.
        self.assertEqual(driver.reads, 1)
        self.assertEqual(report.metrics.model_calls, 1)
        self.assertEqual(driver.calls, [])

    def test_action_capture_and_verification_statuses_are_shown_during_those_operations(self):
        statuses, seen = [], []
        runner = EngineRunner()
        runner.status.connect(statuses.append)
        class Driver(FakeDriver):
            def observe(self):
                seen.append(("capture", statuses[-1]))
                return super().observe()
            def execute(self, action, observation):
                seen.append(("execute", statuses[-1]))
                return super().execute(action, observation)
        class Model(ScriptModel):
            def decide(self, goal, observation, context):
                seen.append(("decision", statuses[-1]))
                return super().decide(goal, observation, context)
            def verify_goal(self, goal, observation, context):
                seen.append(("verify", statuses[-1]))
                return super().verify_goal(goal, observation, context)
        description = "Enter Alice in the Name field"
        model = Model([act(replace(fill(), description=description)), finish])
        driver = Driver([frame(), frame("o2", value="Alice"), frame("o3", value="Alice")])
        report = agent(model, driver).run_goal("Enter Alice", mode="active", on_event=runner._on_event)
        self.assertTrue(report.ok)
        self.assertEqual(seen, [
            ("capture", "Thinking…"),
            ("decision", "Thinking…"),
            ("execute", description),
            ("capture", "Checking the current screen…"),
            ("decision", "Choosing the next action…"),
            ("capture", "Checking the current screen…"),
            ("verify", "Verifying the full result…"),
        ])
        self.assertEqual(statuses[-1], "Result verified.")
        self.assertEqual(report.metrics.model_calls, 3)
        self.assertEqual(len(driver.calls), 1)

    def test_finish_claim_is_not_displayed_as_a_completed_result_before_verification(self):
        runner = EngineRunner()
        statuses = []
        runner.status.connect(statuses.append)
        runner._on_event("started", {"mode": "active"})
        runner._on_event("decision", {"kind": "finish", "message": "Everything is done!"})
        self.assertEqual(statuses[-1], "Preparing the final check…")
        self.assertNotIn("Everything is done!", statuses)
        runner._on_event("model_call_started", {"kind": "verification"})
        self.assertEqual(statuses[-1], "Verifying the full result…")
        runner._on_event("goal_verification", {"status": "unproven"})
        self.assertEqual(statuses[-1], "Result not yet verified.")

    def test_completed_action_event_does_not_claim_it_has_just_started_checking(self):
        runner = EngineRunner()
        statuses = []
        runner.status.connect(statuses.append)
        runner._on_event("action_result", {"effect": "applied"})
        self.assertEqual(statuses, [])

    def test_escape_from_model_start_event_prevents_the_call_and_any_input(self):
        abort = Event()
        driver = FakeDriver()
        model = ScriptModel([])
        def event(name, payload):
            if name == "model_call_started":
                abort.set()
        report = agent(model, driver).run_goal("Open Notepad", mode="active", on_event=event,
                                              should_abort=abort.is_set)
        self.assertEqual(report.status, RunStatus.CANCELLED)
        self.assertEqual(report.metrics.model_calls, 0)
        self.assertEqual(model.contexts, [])
        self.assertEqual(driver.calls, [])
        self.assertTrue(driver.closed)

    def test_escape_from_capture_start_event_prevents_observation_and_input(self):
        abort = Event()
        driver = FakeDriver()
        def event(name, payload):
            if name == "observation_started":
                abort.set()
        report = agent(ScriptModel([]), driver).run_goal("Open Notepad", mode="active", on_event=event,
                                                       should_abort=abort.is_set)
        self.assertEqual(report.status, RunStatus.CANCELLED)
        self.assertEqual(driver.reads, 0)
        self.assertEqual(report.metrics.model_calls, 0)
        self.assertEqual(driver.calls, [])
        self.assertTrue(driver.closed)


if __name__ == "__main__":
    unittest.main()
