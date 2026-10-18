"""Deterministic runtime regressions: no model API, credentials, input devices or display."""
from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from threading import Event

from PIL import Image

from helper_engine.journal import JsonJournal
from helper_engine.model import ProviderModel
from helper_engine.protocol import parse_decision
from helper_engine.runtime import Agent, Limits
from helper_engine.types import (
    Action, Decision, Effect, Element, Execution, Observation, Outcome, Predicate, ProtocolError,
    Rect, RunStatus, StaleObservation, Target,
)
from helper_engine.verification import evaluate


def frame(oid="o1", *, value="", title="Test App", color="white", name="Name", origin=0, scale=1):
    out = io.BytesIO()
    Image.new("RGB", (300, 200), color).save(out, "PNG")
    return Observation(oid, "desktop", "app1", title, out.getvalue(), 300, 200,
                       (Element("u1", name, "edit", Rect(10, 10, 100, 30), value),),
                       origin_x=origin, scale=scale)


def click():
    return Action("click", Target("u1"), description="Click Name")


def fill(text="Alice"):
    return Action("fill", Target("u1"), text=text, expect=(Predicate("value_equals", "Name", value=text),))


class FakeDriver:
    def __init__(self, frames=None, *, effect=Effect.APPLIED, on_execute=None, validate=None):
        self.frames = list(frames or [frame()])
        self.calls = []
        self.effect = effect
        self.on_execute = on_execute
        self.validate_hook = validate
        self.closed = False
        self.reads = 0

    def observe(self):
        result = self.frames[min(self.reads, len(self.frames) - 1)]
        self.reads += 1
        return result

    def validate(self, action, observation):
        if self.validate_hook:
            self.validate_hook(action, observation)

    def execute(self, action, observation):
        self.calls.append(action)
        if self.on_execute:
            self.on_execute()
        return Execution(self.effect, "fake")

    def close(self):
        self.closed = True


class ScriptModel:
    def __init__(self, script, *, verified=True):
        self.script = list(script)
        self.contexts = []
        self.verifications = []
        self.verified = verified

    def decide(self, goal, observation, context):
        self.contexts.append(json.loads(json.dumps(context)))
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item(observation) if callable(item) else item

    def verify_goal(self, goal, observation, context):
        self.verifications.append(observation.id)
        self.verified_replies = getattr(self, "verified_replies", []) + [context.get("proposed_reply")]
        return self.verified, "Expected final content is visible." if self.verified else "Saved result is not visible."


def act(action):
    return lambda obs: Decision("act", obs.id, action=action)


def finish(obs):
    return Decision("finish", obs.id, evidence="Final result is visible.")


def agent(model, driver, **kwargs):
    return Agent(model=model, driver=driver, limits=Limits(
        max_steps=12, max_model_calls=15, max_seconds=3, help_wait_seconds=0.06,
        poll_seconds=0.005), **kwargs)


class ProtocolTests(unittest.TestCase):
    def test_typed_unicode_fill_and_postcondition(self):
        decision = parse_decision(json.dumps({
            "kind": "act", "observation_id": "o1",
            "action": {"kind": "fill", "target": {"element_id": "u1"}, "text": "¡Hola 世界!",
                       "expect": [{"kind": "value_equals", "name": "Name", "value": "¡Hola 世界!"}]},
        }))
        self.assertEqual(decision.action.text, "¡Hola 世界!")

    def test_rejects_invented_actions_and_unknown_fields(self):
        for action in ({"kind": "run_script", "text": "evil"}, {"kind": "click", "selector": "#save"}):
            with self.subTest(action=action), self.assertRaises(ProtocolError):
                parse_decision(json.dumps({"kind": "act", "observation_id": "o1", "action": action}))

    def test_visual_bounds_and_confidence(self):
        for box, confidence in (([100, 100, 950, 5], 1), ([0, 0, 10, 10], 0.5),
                                ([0, 0, float("nan"), 10], 1), ([True, 0, 10, 10], 1)):
            with self.subTest(box=box), self.assertRaises(ProtocolError):
                parse_decision(json.dumps({"kind": "act", "observation_id": "o1", "action": {
                    "kind": "click", "target": {"box": box, "confidence": confidence}}}))

    def test_duplicate_fields_rejected(self):
        with self.assertRaises(ProtocolError):
            parse_decision('{"kind":"finish","kind":"act","observation_id":"o1"}')

    def test_answer_requires_message(self):
        with self.assertRaises(ProtocolError):
            parse_decision('{"kind":"answer","observation_id":"o1"}')

    def test_two_target_schemes_cannot_be_combined(self):
        with self.assertRaises(ProtocolError):
            parse_decision(json.dumps({"kind": "act", "observation_id": "o1", "action": {
                "kind": "click", "target": {"element_id": "u1", "box": [1, 2, 3, 4]}}}))


class RuntimeTests(unittest.TestCase):
    def test_fill_verified_locally_then_full_goal_verified_on_new_observation(self):
        driver = FakeDriver([frame(), frame("o2", value="Alice"), frame("o3", value="Alice")])
        model = ScriptModel([act(fill()), finish])
        report = agent(model, driver).run_goal("Enter Alice", mode="active")
        self.assertEqual(report.status, RunStatus.COMPLETED)
        self.assertEqual(report.metrics.actions, 1)
        self.assertEqual(report.done, ["a1"])
        self.assertEqual(model.verifications, ["o3"])
        self.assertTrue(driver.closed)

    def test_verified_finish_returns_and_checks_the_users_reply(self):
        driver = FakeDriver([frame(), frame("o2", value="Alice"), frame("o3", value="Alice")])
        def reply(obs):
            return Decision("finish", obs.id, message="The field now says Alice.")
        model = ScriptModel([act(fill()), reply])
        report = agent(model, driver).run_goal("Enter Alice and tell me the value", mode="active")
        self.assertEqual(report.status, RunStatus.COMPLETED)
        self.assertEqual(report.message, "The field now says Alice.")
        self.assertEqual(model.verified_replies, ["The field now says Alice."])
        self.assertNotIn("proposed_reply", model.contexts[-1])

    def test_already_true_expectation_does_not_block_or_prove_a_click(self):
        # Real failure: "Skip ad" was expected to exist, which was true before clicking it.
        skip = Action("click", Target("u1"), description="Skip ad", expect=(Predicate("exists", "Name"),))
        driver = FakeDriver([frame(), frame("o2", color="black"), frame("o3", color="black")])
        model = ScriptModel([act(skip), finish])
        report = agent(model, driver).run_goal("Skip the ad", mode="active")
        self.assertEqual(len(driver.calls), 1)
        self.assertEqual(report.status, RunStatus.COMPLETED)
        self.assertEqual(report.receipts[0].outcome, Outcome.UNCERTAIN)
        self.assertIn("already true before", model.contexts[1]["feedback"])

    def test_redundant_fill_is_skipped_without_input(self):
        driver = FakeDriver([frame(value="Alice")] * 4)
        model = ScriptModel([act(fill()), act(fill()), act(fill())])
        report = agent(model, driver).run_goal("Enter Alice", mode="active")
        self.assertEqual(driver.calls, [])
        self.assertEqual(report.status, RunStatus.NEEDS_INPUT)

    def test_help_mode_waits_when_expectation_was_already_true(self):
        guided = Action("click", Target("u1"), description="Click Name", expect=(Predicate("exists", "Name"),))
        driver = FakeDriver([frame()] * 40)
        report = agent(ScriptModel([act(guided)]), driver).run_goal("Show me", mode="help")
        self.assertEqual(driver.calls, [])
        self.assertEqual(report.status, RunStatus.NEEDS_INPUT)
        self.assertIn("Follow the highlighted step", report.message)

    def test_model_error_never_reports_success(self):
        driver = FakeDriver()
        report = agent(ScriptModel([RuntimeError("transport offline")]), driver).run_goal("Do task")
        self.assertFalse(report.ok)
        self.assertEqual(report.status, RunStatus.FAILED)
        self.assertFalse(driver.calls)
        self.assertTrue(driver.closed)

    def test_invalid_decisions_bounded_without_input(self):
        driver = FakeDriver()
        report = agent(ScriptModel([ProtocolError("bad JSON")] * 3), driver).run_goal("Do task")
        self.assertFalse(report.ok)
        self.assertEqual(report.metrics.model_calls, 3)
        self.assertFalse(driver.calls)

    def test_stale_target_refreshes_before_any_execution(self):
        def validate(action, obs):
            if obs.id == "o1":
                raise StaleObservation("Window moved")
        driver = FakeDriver([frame(), frame("o2"), frame("o3", value="Alice"), frame("o4", value="Alice")],
                            validate=validate)
        model = ScriptModel([act(fill()), act(fill()), finish])
        report = agent(model, driver).run_goal("Enter Alice", mode="active")
        self.assertTrue(report.ok)
        self.assertEqual(report.metrics.stale_decisions, 1)
        self.assertEqual(len(driver.calls), 1)

    def test_timeout_after_click_does_not_replay(self):
        driver = FakeDriver(effect=Effect.UNKNOWN)
        report = agent(ScriptModel([act(click()), act(click())]), driver).run_goal("Click Name", mode="active")
        self.assertEqual(report.status, RunStatus.NEEDS_INPUT)
        self.assertEqual(len(driver.calls), 1)

    def test_duplicate_click_on_unchanged_scene_is_blocked(self):
        driver = FakeDriver()
        report = agent(ScriptModel([act(click())] * 3), driver).run_goal("Open Name", mode="active")
        self.assertEqual(len(driver.calls), 1)
        self.assertEqual(report.status, RunStatus.NEEDS_INPUT)

    def test_abort_during_model_call_prevents_dispatch(self):
        aborted = Event()
        def decide(obs):
            aborted.set()
            return Decision("act", obs.id, action=click())
        driver = FakeDriver()
        report = agent(ScriptModel([decide]), driver).run_goal("Click", mode="active", should_abort=aborted.is_set)
        self.assertEqual(report.status, RunStatus.CANCELLED)
        self.assertFalse(driver.calls)

    def test_cancel_before_first_observation_still_cleans_up(self):
        driver = FakeDriver()
        report = agent(ScriptModel([]), driver).run_goal("Click", should_abort=lambda: True)
        self.assertEqual(driver.reads, 0)
        self.assertTrue(driver.closed)
        self.assertEqual(report.status, RunStatus.CANCELLED)

    def test_cancel_after_dispatch_preserves_possible_side_effect(self):
        aborted = Event()
        driver = FakeDriver(on_execute=aborted.set)
        report = agent(ScriptModel([act(click())]), driver).run_goal(
            "Click", mode="active", should_abort=aborted.is_set)
        self.assertEqual(report.status, RunStatus.CANCELLED)
        self.assertEqual(report.metrics.actions, 1)
        self.assertEqual(len(report.receipts), 1)
        self.assertEqual(report.receipts[0].execution.effect, Effect.APPLIED)

    def test_old_observation_id_cannot_dispatch(self):
        driver = FakeDriver()
        script = [Decision("act", "expired", action=click())] * 3
        report = agent(ScriptModel(script), driver).run_goal("Click", mode="active")
        self.assertFalse(report.ok)
        self.assertFalse(driver.calls)

    def test_help_mode_never_dispatches_any_input(self):
        driver = FakeDriver([frame(), frame("o2", value="Alice"), frame("o3", value="Alice")])
        report = agent(ScriptModel([act(fill()), finish]), driver).run_goal("Teach entering Alice", mode="help")
        self.assertTrue(report.ok)
        self.assertFalse(driver.calls)
        self.assertEqual(report.receipts[0].execution.effect, Effect.GUIDED)

    def test_help_waits_and_stops_when_user_has_not_acted(self):
        driver = FakeDriver()
        report = agent(ScriptModel([act(click())]), driver).run_goal("Teach click", mode="help")
        self.assertEqual(report.status, RunStatus.NEEDS_INPUT)
        self.assertFalse(driver.calls)

    def test_negative_goal_verification_cannot_claim_completion(self):
        model = ScriptModel([finish, finish], verified=False)
        report = agent(model, FakeDriver()).run_goal("Save document", mode="active")
        self.assertFalse(report.ok)
        self.assertEqual(len(model.verifications), 2)

    def test_confirmation_denial_does_not_execute(self):
        driver = FakeDriver([frame(name="Send")])
        report = agent(ScriptModel([act(click())]), driver, confirm=lambda *_: False).run_goal("Send", mode="active")
        self.assertEqual(report.status, RunStatus.NEEDS_INPUT)
        self.assertFalse(driver.calls)

    def test_goal_answer_does_not_need_completion_verifier(self):
        model = ScriptModel([lambda obs: Decision("answer", obs.id, "Hello!")])
        report = agent(model, FakeDriver()).run_goal("Hi")
        self.assertEqual(report.status, RunStatus.ANSWERED)
        self.assertFalse(model.verifications)

    def test_recent_context_contains_receipt_not_screenshot_history(self):
        driver = FakeDriver([frame(), frame("o2", value="Alice"), frame("o3", value="Alice")])
        model = ScriptModel([act(fill()), finish])
        agent(model, driver).run_goal("Enter Alice", mode="active")
        context = model.contexts[1]
        self.assertEqual(len(context["recent_actions"]), 1)
        self.assertEqual(context["recent_actions"][0]["outcome"], "achieved")
        self.assertNotIn("png", json.dumps(context))


class EvidenceTests(unittest.TestCase):
    def test_ambiguous_names_do_not_verify_value(self):
        obs = frame()
        from dataclasses import replace
        obs = replace(obs, elements=obs.elements + (Element("u2", "Name", "edit", Rect(5, 50, 100, 20), "Alice"),))
        outcome, _ = evaluate((Predicate("value_equals", "Name", value="Alice"),), obs)
        self.assertEqual(outcome, Outcome.UNCERTAIN)

    def test_absence_in_partial_snapshot_is_not_proof(self):
        outcome, _ = evaluate((Predicate("absent", "Not present"),), frame())
        self.assertEqual(outcome, Outcome.UNCERTAIN)


class SignatureTests(unittest.TestCase):
    def test_enabled_state_change_alters_signature(self):
        from dataclasses import replace
        obs = frame()
        disabled = replace(obs.elements[0], enabled=False)
        obs2 = replace(obs, elements=(disabled,))
        self.assertNotEqual(obs.signature, obs2.signature)

    def test_focus_change_alters_signature(self):
        from dataclasses import replace
        obs = frame()
        focused = replace(obs.elements[0], focused=True)
        obs2 = replace(obs, elements=(focused,))
        self.assertNotEqual(obs.signature, obs2.signature)

    def test_identical_observations_share_signature(self):
        self.assertEqual(frame().signature, frame().signature)

    def test_negative_monitor_origin_and_scaled_screenshot(self):
        obs = frame(origin=-1920, scale=0.5)
        rect = obs.screen_box((500, 500, 100, 100))
        self.assertEqual(rect, Rect(-1620, 200, 60, 40))
        self.assertEqual(obs.image_box(rect), (150, 100, 180, 120))

    def test_journal_filters_typed_text_and_screenshots(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "run.jsonl"
            journal = JsonJournal(path)
            journal("action_result", {"route": "uia", "text": "SECRET", "goal": "PRIVATE", "png": "DATA"})
            journal.close()
            result = path.read_text()
            self.assertIn('"route": "uia"', result)
            for forbidden in ("SECRET", "PRIVATE", "DATA"):
                self.assertNotIn(forbidden, result)

    def test_verifier_requires_explicit_evidence_and_no_missing_items(self):
        from types import SimpleNamespace
        class Client:
            def chat(self, *args, **kwargs):
                return SimpleNamespace(text='{"satisfied":true,"evidence":"looks done","missing":["saved file"]}')
        ok, evidence = ProviderModel(Client(), "test").verify_goal("Save", frame(), {})
        self.assertFalse(ok)
        self.assertEqual(evidence, "saved file")

    def test_evaluation_deduplicates_runs_and_excludes_chat_from_task_rate(self):
        from helper_engine.evaluate import summarize
        rows = [
            {"event": "finished", "run_id": "r1", "status": "completed", "model_calls": 3},
            {"event": "finished", "run_id": "r1", "status": "completed", "model_calls": 3},
            {"event": "finished", "run_id": "r2", "status": "failed", "model_calls": 1},
            {"event": "finished", "run_id": "r3", "status": "answered", "model_calls": 1},
        ]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "runs.jsonl"
            path.write_text("\n".join(json.dumps(row) for row in rows) + "\n{invalid}\n")
            result = summarize([path])
        self.assertEqual(result["finished_runs"], 3)
        self.assertEqual(result["task_completion_rate"], 0.5)
        self.assertEqual(result["mean_model_calls"], 2)
        self.assertEqual(result["invalid_lines"], 1)


class PolicyTests(unittest.TestCase):
    def test_destructive_words_require_confirmation(self):
        from helper_engine.policy import confirmation_reason
        obs = frame()
        for word in ("remove", "uninstall", "format"):
            action = Action("click", Target("u1"), "", (), 0, None, (), False, f"Click to {word} item")
            self.assertTrue(confirmation_reason(action, obs), word)

    def test_benign_click_needs_no_confirmation(self):
        from helper_engine.policy import confirmation_reason
        obs = frame()
        action = Action("click", Target("u1"), "", (), 0, None, (), False, "Open settings")
        self.assertEqual(confirmation_reason(action, obs), "")


class PerceptionTests(unittest.TestCase):
    def test_corrupt_images_report_max_difference(self):
        from helper_engine.perception import image_difference
        self.assertEqual(image_difference(b"not an image", b"also not"), 1.0)
        self.assertEqual(image_difference(frame().png, b"corrupt"), 1.0)

    def test_identical_images_have_zero_difference(self):
        from helper_engine.perception import image_difference
        png = frame().png
        self.assertEqual(image_difference(png, png), 0.0)


class BootstrapTests(unittest.TestCase):
    def test_invalid_page_index_raises_clear_error(self):
        import os
        from helper_engine.bootstrap import _int_env
        os.environ["TEST_PAGE_INDEX"] = "not-a-number"
        try:
            with self.assertRaises(ValueError) as ctx:
                _int_env("TEST_PAGE_INDEX", None)
            self.assertIn("TEST_PAGE_INDEX", str(ctx.exception))
        finally:
            del os.environ["TEST_PAGE_INDEX"]

    def test_valid_page_index_parses(self):
        import os
        from helper_engine.bootstrap import _int_env
        os.environ["TEST_PAGE_INDEX"] = "3"
        try:
            self.assertEqual(_int_env("TEST_PAGE_INDEX", None), 3)
        finally:
            del os.environ["TEST_PAGE_INDEX"]


if __name__ == "__main__":
    unittest.main()
