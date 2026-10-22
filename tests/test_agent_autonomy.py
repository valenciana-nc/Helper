"""Act-first task handling, bounded clarification review, and in-memory follow-ups."""
from __future__ import annotations

from dataclasses import replace
import json
from threading import Event
from pathlib import Path
import tempfile
import unittest

from helper_engine.evaluate import summarize
from helper_engine.journal import JsonJournal
from helper_engine.model import ProviderModel
from helper_engine.policy import confirmation_reason
from helper_engine.protocol import parse_decision
from helper_engine.runtime import Agent, Limits
from helper_engine.schemas import decision_schema
from helper_engine.types import Action, Decision, Effect, RunStatus, Target
from test_engine import FakeDriver, ScriptModel, act, agent, click, fill, finish, frame
from test_engine_provider_contract import RecordingClient


def ask(obs):
    return Decision("ask", obs.id, "Which item do you mean?")


def resumed(action):
    return lambda obs: Decision("act", obs.id, action=action, continue_task=True)


class RecordingModel(ScriptModel):
    def __init__(self, script, **kwargs):
        super().__init__(script, **kwargs)
        self.goals = []
        self.verification_goals = []

    def decide(self, goal, observation, context):
        self.goals.append(goal)
        return super().decide(goal, observation, context)

    def verify_goal(self, goal, observation, context):
        self.verification_goals.append(goal)
        return super().verify_goal(goal, observation, context)


class AutonomyTests(unittest.TestCase):
    def test_normal_action_path_adds_no_model_calls(self):
        model = RecordingModel([act(fill()), finish])
        driver = FakeDriver([frame(), frame("o2", value="Alice"), frame("o3", value="Alice")])
        report = agent(model, driver).run_goal("Enter Alice", mode="active")
        self.assertTrue(report.ok)
        self.assertEqual(report.metrics.model_calls, 3)
        self.assertEqual(report.metrics.clarification_reviews, 0)
        self.assertEqual(len(driver.calls), 1)

    def test_initial_question_is_reviewed_then_task_executes(self):
        model = RecordingModel([ask, act(Action("open", text="https://www.youtube.com/results?search_query=no+pole")), finish])
        driver = FakeDriver()
        events = []
        report = agent(model, driver).run_goal("open yt and play no pole song", mode="active",
                                              on_event=lambda name, data: events.append((name, data)))
        self.assertEqual(report.status, RunStatus.COMPLETED)
        self.assertEqual(report.metrics.clarification_reviews, 1)
        self.assertEqual(len(driver.calls), 1)
        self.assertIn("safe independent parts", model.contexts[1]["feedback"])
        self.assertEqual(model.contexts[1]["clarification_review"]["proposed_question"], "Which item do you mean?")
        self.assertFalse(any(name == "decision" and data["kind"] == "ask" for name, data in events))

    def test_genuine_blocker_is_still_allowed_after_one_review(self):
        model = RecordingModel([ask, ask])
        driver = FakeDriver()
        report = agent(model, driver).run_goal("Send this to the person I mean", mode="active")
        self.assertEqual(report.status, RunStatus.NEEDS_INPUT)
        self.assertEqual(report.metrics.model_calls, 2)
        self.assertEqual(report.metrics.clarification_reviews, 1)
        self.assertEqual(driver.calls, [])
        self.assertTrue(driver.closed)

    def test_review_obeys_model_budget(self):
        model = RecordingModel([ask])
        report = Agent(model=model, driver=FakeDriver(), limits=Limits(max_model_calls=1)).run_goal(
            "Open the app I mean", mode="active")
        self.assertEqual(report.status, RunStatus.NEEDS_INPUT)
        self.assertEqual(report.metrics.model_calls, 1)
        self.assertEqual(report.metrics.clarification_reviews, 0)

    def test_help_mode_does_not_add_clarification_review(self):
        model = RecordingModel([ask])
        report = agent(model, FakeDriver()).run_goal("Teach this", mode="help")
        self.assertEqual(report.status, RunStatus.NEEDS_INPUT)
        self.assertEqual(report.metrics.model_calls, 1)
        self.assertEqual(report.metrics.clarification_reviews, 0)

    def test_escape_during_review_prevents_input_and_clears_pending_task(self):
        aborted = Event()
        model = RecordingModel([ask, act(click())])
        driver = FakeDriver()
        engine = agent(model, driver)
        def event(name, data):
            if name == "decision_rejected" and data.get("reason") == "clarification_review":
                aborted.set()
        report = engine.run_goal("Open the app", mode="active", on_event=event, should_abort=aborted.is_set)
        self.assertEqual(report.status, RunStatus.CANCELLED)
        self.assertEqual(driver.calls, [])
        self.assertIsNone(engine._pending_task)

    def test_frustrated_follow_up_keeps_original_goal_and_full_goal_verification(self):
        original = "open my taskbar apps and play Northern Lights on YouTube"
        follow_up = "jsut get on with it stop askign me to repeat the task"
        model = RecordingModel([ask, ask, resumed(fill()), finish])
        driver = FakeDriver([frame(), frame("o2"), frame("o3"), frame("o4", value="Alice"), frame("o5", value="Alice")])
        engine = agent(model, driver)
        self.assertEqual(engine.run_goal(original, mode="active").status, RunStatus.NEEDS_INPUT)
        report = engine.run_goal(follow_up, mode="active")
        self.assertEqual(report.status, RunStatus.COMPLETED)
        self.assertEqual(model.contexts[2]["pending_task"]["goal"], original)
        self.assertIn(original, report.goal)
        self.assertIn(follow_up, report.goal)
        self.assertEqual(model.verification_goals, [report.goal])
        self.assertIsNone(engine._pending_task)

    def test_follow_up_can_be_resolved_during_review_not_only_first_proposal(self):
        model = RecordingModel([ask, ask, ask, resumed(fill()), finish])
        engine = agent(model, FakeDriver([frame()] * 8))
        engine.run_goal("Enter Alice", mode="active")
        report = engine.run_goal("Just do it", mode="active")
        self.assertTrue(report.ok)
        self.assertIn("Enter Alice", report.goal)
        self.assertIn("Just do it", report.goal)

    def test_unrelated_new_task_does_not_resume_previous_goal(self):
        model = RecordingModel([ask, ask, act(Action("open", text="ms-settings:")), finish])
        engine = agent(model, FakeDriver())
        engine.run_goal("Find the file I mean", mode="active")
        report = engine.run_goal("Open Settings", mode="active")
        self.assertTrue(report.ok)
        self.assertEqual(report.goal, "Open Settings")
        self.assertEqual(model.verification_goals, ["Open Settings"])
        self.assertNotIn("prior_progress", model.contexts[-1])
        self.assertNotIn("pending_task", model.contexts[-1])
        self.assertIsNone(engine._pending_task)

    def test_chat_does_not_automatically_execute_or_discard_unfinished_task(self):
        model = RecordingModel([ask, ask, lambda obs: Decision("answer", obs.id, "Hello!")])
        driver = FakeDriver()
        engine = agent(model, driver)
        engine.run_goal("Open the selected file", mode="active")
        report = engine.run_goal("Hi", mode="active")
        self.assertEqual(report.status, RunStatus.ANSWERED)
        self.assertEqual(driver.calls, [])
        self.assertEqual(engine._pending_task.goal, "Open the selected file")

    def test_completed_progress_is_available_after_follow_up_without_old_screenshots(self):
        model = RecordingModel([act(fill()), ask, ask, resumed(Action("press", keys=("enter",))), finish])
        driver = FakeDriver([frame(), frame("o2", value="Alice"), frame("o3", value="Alice")])
        engine = agent(model, driver)
        engine.run_goal("Enter Alice then confirm the selected option", mode="active")
        report = engine.run_goal("Use the current option", mode="active")
        self.assertTrue(report.ok)
        pending = model.contexts[3]["pending_task"]
        self.assertEqual(pending["prior_progress"][0]["action"]["text"], "Alice")
        self.assertEqual(len(model.contexts[4]["prior_progress"]), 1)
        self.assertNotIn("png", json.dumps(pending))
        self.assertEqual([action.kind for action in driver.calls], ["fill", "press"])

    def test_resumed_task_cannot_be_reported_done_as_an_unverified_answer(self):
        def answer(obs):
            return Decision("answer", obs.id, "Done!", continue_task=True)
        model = RecordingModel([ask, ask, answer, finish])
        driver = FakeDriver()
        engine = agent(model, driver)
        engine.run_goal("Open Notepad", mode="active")
        report = engine.run_goal("Just do it", mode="active")
        self.assertEqual(report.status, RunStatus.COMPLETED)
        self.assertEqual(model.verification_goals, [report.goal])
        self.assertIn("task is in progress", model.contexts[-1]["feedback"])
        self.assertNotIn("pending_task", model.contexts[-1])
        self.assertEqual(driver.calls, [])

    def test_repeated_follow_ups_retain_the_goal_and_all_user_constraints(self):
        def continued_question(obs):
            return replace(ask(obs), continue_task=True)
        model = RecordingModel([ask, ask, continued_question, continued_question, resumed(fill()), finish])
        engine = agent(model, FakeDriver())
        engine.run_goal("Enter the requested name", mode="active")
        engine.run_goal("Use Alice and leave the document unsaved", mode="active")
        report = engine.run_goal("Continue", mode="active")
        self.assertTrue(report.ok)
        self.assertIn("Enter the requested name", report.goal)
        self.assertIn("Use Alice and leave the document unsaved", report.goal)
        self.assertIn("Continue", report.goal)
        self.assertEqual(model.verification_goals, [report.goal])

    def test_review_may_do_clear_part_then_still_ask_for_essential_input(self):
        model = RecordingModel([ask, act(fill()), ask])
        driver = FakeDriver([frame(), frame("o2"), frame("o3", value="Alice")])
        engine = agent(model, driver)
        report = engine.run_goal("Enter Alice then send it to the person I mean", mode="active")
        self.assertEqual(report.status, RunStatus.NEEDS_INPUT)
        self.assertEqual(len(driver.calls), 1)
        self.assertEqual(report.metrics.clarification_reviews, 1)
        self.assertEqual(len(engine._pending_task.progress), 1)
        self.assertNotIn("clarification_review", model.contexts[-1])

    def test_clarification_review_cannot_bypass_real_confirmation(self):
        action = Action("click", Target("u1"), description="Send the message", requires_confirmation=True)
        model = RecordingModel([ask, act(action)])
        driver = FakeDriver()
        approvals = []
        def deny(action, observation, reason):
            approvals.append(reason)
            return False
        report = agent(model, driver, confirm=deny).run_goal("Just send it", mode="active")
        self.assertEqual(report.status, RunStatus.NEEDS_INPUT)
        self.assertIn("needs your confirmation", report.message)
        self.assertEqual(len(approvals), 1)
        self.assertEqual(driver.calls, [])

    def test_failed_or_limited_runs_keep_progress_for_explicit_follow_up(self):
        for error in (RuntimeError("temporarily unavailable"), TimeoutError("request timed out")):
            with self.subTest(error=type(error).__name__):
                model = RecordingModel([act(fill()), error, resumed(Action("press", keys=("tab",))), finish])
                driver = FakeDriver([frame(), frame("o2", value="Alice")])
                engine = agent(model, driver)
                report = engine.run_goal("Enter Alice and move to the next field", mode="active")
                self.assertIn(report.status, {RunStatus.FAILED, RunStatus.LIMITED})
                self.assertEqual(len(engine._pending_task.progress), 1)
                report = engine.run_goal("Continue", mode="active")
                self.assertTrue(report.ok)
                self.assertIn("Enter Alice and move to the next field", report.goal)
                self.assertEqual([action.kind for action in driver.calls], ["fill", "press"])

    def test_uncertain_click_cannot_be_replayed_after_resuming(self):
        model = RecordingModel([act(click()), ask, ask, resumed(click())])
        driver = FakeDriver(effect=Effect.UNKNOWN)
        engine = agent(model, driver)
        engine.run_goal("Click the selected item", mode="active")
        report = engine.run_goal("Try again", mode="active")
        self.assertEqual(report.status, RunStatus.NEEDS_INPUT)
        self.assertEqual(len(driver.calls), 1)
        self.assertIn("may already have taken effect", report.message)

    def test_real_progress_resets_consecutive_failed_finish_counter(self):
        model = RecordingModel([finish, act(fill("Alice")), finish, act(fill("Bob")), finish])
        outcomes = iter((False, False, True))
        def verify(goal, obs, context):
            model.verifications.append(obs.id)
            return next(outcomes), "The requested final value is not visible yet."
        model.verify_goal = verify
        driver = FakeDriver([frame(), frame("o2"), frame("o3", value="Alice"), frame("o4", value="Alice"),
                             frame("o5", value="Bob"), frame("o6", value="Bob")])
        report = agent(model, driver).run_goal("Enter Alice, then replace it with Bob", mode="active")
        self.assertEqual(report.status, RunStatus.COMPLETED)
        self.assertEqual(len(model.verifications), 3)
        self.assertEqual([action.text for action in driver.calls], ["Alice", "Bob"])

    def test_rejected_action_does_not_reset_failed_finish_counter(self):
        model = RecordingModel([finish, act(click()), finish], verified=False)
        driver = FakeDriver([frame(), frame("o2"), frame("o3", color="black")], effect=Effect.REJECTED)
        report = agent(model, driver).run_goal("Open the item", mode="active")
        self.assertEqual(report.status, RunStatus.NEEDS_INPUT)
        self.assertEqual(len(model.verifications), 2)
        self.assertIn("Completion could not be verified", report.message)


class AutonomyContractTests(unittest.TestCase):
    def test_continuation_is_typed_and_legacy_decisions_default_to_false(self):
        raw = {"kind": "ask", "observation_id": "o1", "message": "Which file?"}
        self.assertFalse(parse_decision(json.dumps(raw)).continue_task)
        self.assertTrue(parse_decision(json.dumps({**raw, "continue_task": True})).continue_task)
        for invalid in ("true", 1, None, []):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                parse_decision(json.dumps({**raw, "continue_task": invalid}))
        self.assertEqual(decision_schema(frame())["properties"]["continue_task"], {"type": "boolean"})

    def test_provider_receives_pending_goal_and_can_return_typed_continuation(self):
        client = RecordingClient(json.dumps({"kind": "observe", "observation_id": "o1", "continue_task": True}))
        context = {"pending_task": {"goal": "Open the browser and play the song", "prior_progress": []}}
        decision = ProviderModel(client, "test").decide("Just do it", frame(), context)
        self.assertTrue(decision.continue_task)
        sent = json.loads(client.messages[0][0]["parts"][0]["text"])
        self.assertEqual(sent["goal"], "Just do it")
        self.assertEqual(sent["context"]["pending_task"]["goal"], "Open the browser and play the song")

    def test_journal_and_summary_count_reviews_without_logging_questions_or_goals(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "run.jsonl"
            journal = JsonJournal(path)
            try:
                report = agent(RecordingModel([ask, ask]), FakeDriver()).run_goal(
                    "private-user-goal", mode="active", on_event=journal)
            finally:
                journal.close()
            contents = path.read_text(encoding="utf-8")
            self.assertNotIn("private-user-goal", contents)
            self.assertNotIn("Which item", contents)
            self.assertEqual(report.metrics.clarification_reviews, 1)
            self.assertEqual(summarize([path])["mean_clarification_reviews"], 1)

    def test_search_submit_and_draft_fill_do_not_request_unnecessary_confirmation(self):
        obs = frame(name="Search")
        self.assertEqual(confirmation_reason(Action("click", Target("u1"), description="Submit the search query"), obs), "")
        draft = Action("fill", Target("u1"), text="No Pole", description="Enter the query to submit")
        self.assertEqual(confirmation_reason(draft, obs), "")
        self.assertTrue(confirmation_reason(replace(draft, requires_confirmation=True), obs))
        password = replace(obs, elements=(replace(obs.elements[0], password=True),))
        self.assertTrue(confirmation_reason(draft, password))

    def test_focused_credentials_and_terminal_enter_still_require_confirmation(self):
        obs = frame(name="Search")
        password = replace(obs, elements=(replace(obs.elements[0], password=True, focused=True),))
        self.assertTrue(confirmation_reason(Action("press", keys=("enter",)), password))
        terminal = replace(obs, title="PowerShell", elements=(replace(obs.elements[0], focused=True),))
        self.assertTrue(confirmation_reason(Action("press", keys=("enter",), description="Submit search"), terminal))

    def test_consequential_and_unidentified_submissions_still_require_confirmation(self):
        for label in ("Submit", "Send", "Delete", "Pay", "Purchase"):
            with self.subTest(label=label):
                action = Action("click", Target("u1"), description=f"Click {label}")
                self.assertTrue(confirmation_reason(action, frame(name=label)))
        obs = frame(name="Search")
        self.assertTrue(confirmation_reason(Action("click", Target("u1"), description="Send and submit"), obs))


if __name__ == "__main__":
    unittest.main()
