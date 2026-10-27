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

    def test_element_target_defaults_to_full_confidence(self):
        decision = parse_decision(json.dumps({"kind": "act", "observation_id": "o1", "action": {
            "kind": "click", "target": {"element_id": "u1"}}}))
        self.assertEqual(decision.action.target.confidence, 1.0)

    def test_visual_target_requires_explicit_high_confidence(self):
        with self.assertRaises(ProtocolError):
            parse_decision(json.dumps({"kind": "act", "observation_id": "o1", "action": {
                "kind": "click", "target": {"box": [10, 10, 50, 50]}}}))


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

    def test_proposed_reply_cleaned_up_when_verification_raises(self):
        # Bug: context.pop("proposed_reply") was skipped if call_model raised,
        # leaking the stale reply into subsequent model decisions.
        driver = FakeDriver([frame(), frame("o2", value="Alice"), frame("o3", value="Alice")])
        def reply(obs):
            return Decision("finish", obs.id, message="The field now says Alice.")
        model = ScriptModel([act(fill()), reply])
        def boom(goal, obs, context):
            raise TimeoutError("model call timed out")
        model.verify_goal = boom
        report = agent(model, driver).run_goal("Enter Alice and tell me the value", mode="active")
        self.assertEqual(report.status, RunStatus.LIMITED)
        for ctx in model.contexts:
            self.assertNotIn("proposed_reply", ctx)

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

    def test_help_mode_stale_target_re_grounds_with_scene_changed_feedback(self):
        def validate(action, obs):
            if obs.id == "o1":
                raise StaleObservation("Window moved")
        driver = FakeDriver([frame(), frame("o2")], validate=validate)
        model = ScriptModel([act(fill()), act(fill())])
        report = agent(model, driver).run_goal("Enter Alice", mode="help")
        self.assertEqual(report.metrics.stale_decisions, 1)
        self.assertFalse(driver.calls)
        self.assertIn("Scene changed", model.contexts[1]["feedback"])

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

    def test_password_values_are_not_searchable(self):
        from dataclasses import replace
        obs = frame()
        secret = replace(obs.elements[0], value="hunter2", password=True)
        obs = replace(obs, elements=(secret,))
        outcome, _ = evaluate((Predicate("text_contains", value="hunter2"),), obs)
        self.assertEqual(outcome, Outcome.UNCERTAIN)

    def test_none_predicate_fields_behave_like_empty_strings(self):
        # JSON-shaped payloads can carry None for name/role/value; the
        # guards in helper_engine/verification.py must degrade to the
        # empty-string behavior, never raise AttributeError.
        obs = frame()
        cases = [
            Predicate("exists", None),
            Predicate("exists", "Name", role=None),
            Predicate("text_contains", value=None),
        ]
        empties = [
            Predicate("exists", ""),
            Predicate("exists", "Name", role=""),
            Predicate("text_contains", value=""),
        ]
        for pred, empty in zip(cases, empties):
            outcome_none, _ = evaluate((pred,), obs)
            outcome_empty, _ = evaluate((empty,), obs)
            self.assertEqual(outcome_none, outcome_empty)

    def test_text_blob_is_built_once_per_evaluate(self):
        from unittest.mock import patch
        from helper_engine import verification
        obs = frame()
        preds = tuple(Predicate("text_contains", value="Name") for _ in range(3))
        with patch.object(verification, "_text_blob", wraps=verification._text_blob) as spy:
            outcome, _ = verification.evaluate(preds, obs)
        self.assertEqual(outcome, Outcome.ACHIEVED)
        self.assertEqual(spy.call_count, 1)

    def test_text_blob_matches_per_predicate_results(self):
        # Case-insensitive match across visible text, names, and values;
        # password values stay out of the haystack.
        from dataclasses import replace
        from helper_engine.verification import _text_blob
        obs = frame(value="Alice")
        blob = _text_blob(obs)
        self.assertIn("alice", blob)
        self.assertIn("name", blob)
        secret = replace(obs.elements[0], password=True, value="hunter2")
        self.assertNotIn("hunter2", _text_blob(replace(obs, elements=(secret,))))


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

    def test_signature_is_memoized_per_observation(self):
        obs = frame()
        first = obs.signature
        self.assertIn("signature", obs.__dict__)
        self.assertEqual(obs.signature, first)

    def test_negative_monitor_origin_and_scaled_screenshot(self):
        obs = frame(origin=-1920, scale=0.5)
        rect = obs.screen_box((500, 500, 100, 100))
        self.assertEqual(rect, Rect(-1620, 200, 60, 40))
        self.assertEqual(obs.image_box(rect), (150, 100, 180, 120))

    def test_image_box_off_screen_rect_returns_empty_box(self):
        # Bug: fully off-screen rects produced a misleading 1-pixel sliver
        # from clamping instead of an empty (invalid) box.
        obs = frame()
        box = obs.image_box(Rect(x=-500, y=10, width=50, height=50))
        self.assertLessEqual(box[2], box[0], f"Expected invalid box, got {box}")
        box = obs.image_box(Rect(x=10, y=-500, width=50, height=50))
        self.assertLessEqual(box[3], box[1], f"Expected invalid box, got {box}")

    def test_browser_observe_skips_elements_vanished_mid_snapshot(self):
        # Bug: DOM mutating between the evaluate() and get_properties()
        # round-trips caused KeyError on properties[str(index)].
        from unittest.mock import MagicMock
        from helper_engine.browser import BrowserDriver
        driver = BrowserDriver.__new__(BrowserDriver)
        driver._handles = {}
        driver._selection = None
        driver._page = MagicMock()
        driver._page.is_closed.return_value = False
        driver._connect = lambda: None
        # Two records, but only one property (second element vanished).
        sel = MagicMock()
        sel.evaluate.return_value = [
            {"name": "a", "role": "button", "rect": [0, 0, 10, 10], "value": "",
             "enabled": True, "focused": False, "checked": False, "password": False},
            {"name": "b", "role": "button", "rect": [20, 20, 10, 10], "value": "",
             "enabled": True, "focused": False, "checked": False, "password": False},
        ]
        sel.get_properties.return_value = {"0": MagicMock(as_element=lambda: MagicMock())}
        driver._page.evaluate_handle.return_value = sel
        driver._page.screenshot.return_value = self._png_bytes(100, 100)
        driver._page.url = "http://x/"
        driver._page.title.return_value = "t"
        driver._page.evaluate.return_value = ""
        obs = driver.observe()
        self.assertEqual(len(obs.elements), 1)
        self.assertEqual(obs.elements[0].name, "a")

    def _png_bytes(self, w, h):
        import io
        from PIL import Image
        buf = io.BytesIO()
        Image.new("RGB", (w, h), "white").save(buf, format="PNG")
        return buf.getvalue()

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

    def test_malformed_open_url_is_not_run_fatal(self):
        # urlsplit raises ValueError on inputs like http://[::1; the policy
        # gate must not turn that into a run-killing exception.
        from helper_engine.policy import confirmation_reason
        obs = frame()
        action = Action("open", None, text="http://[::1")
        self.assertEqual(confirmation_reason(action, obs), "")

    def test_non_web_open_scheme_needs_confirmation(self):
        from helper_engine.policy import confirmation_reason
        obs = frame()
        action = Action("open", None, text="ftp://example.com/x")
        self.assertTrue(confirmation_reason(action, obs))


class OpenTargetTests(unittest.TestCase):
    def test_windows_open_rejects_unparseable_url(self):
        from unittest.mock import Mock
        from helper_engine.windows import WindowsDriver
        driver = WindowsDriver(uia=Mock())
        result = driver.execute(Action("open", None, text="http://[::1"), frame())
        self.assertEqual(result.effect, Effect.REJECTED)
        self.assertEqual(result.route, "launch")

    def test_windows_open_rejects_refused_launch(self):
        from unittest.mock import Mock, patch
        from helper_engine.windows import WindowsDriver
        from launch_safety import LaunchError
        driver = WindowsDriver(uia=Mock())
        with patch("launch_safety.launch_target", side_effect=LaunchError("nope")):
            result = driver.execute(Action("open", None, text="https://example.com"), frame())
        self.assertEqual(result.effect, Effect.REJECTED)
        self.assertEqual(result.route, "launch")

    def test_browser_open_rejects_unparseable_url(self):
        from unittest.mock import Mock
        from helper_engine.browser import BrowserDriver
        driver = BrowserDriver(page=Mock())
        result = driver.execute(Action("open", None, text="http://[::1"), frame())
        self.assertEqual(result.effect, Effect.REJECTED)
        self.assertEqual(result.route, "dom")

    def test_launch_target_raises_launch_error_for_unparseable_url(self):
        from launch_safety import LaunchError, launch_target
        with self.assertRaises(LaunchError):
            launch_target("http://[::1")


class PerceptionTests(unittest.TestCase):
    def test_corrupt_images_report_max_difference(self):
        from helper_engine.perception import image_difference
        self.assertEqual(image_difference(b"not an image", b"also not"), 1.0)
        self.assertEqual(image_difference(frame().png, b"corrupt"), 1.0)

    def test_identical_images_have_zero_difference(self):
        from helper_engine.perception import image_difference
        png = frame().png
        self.assertEqual(image_difference(png, png), 0.0)

    def test_invalid_crop_box_reports_max_difference(self):
        from helper_engine.perception import image_difference
        png = frame().png
        self.assertEqual(image_difference(png, png, box=(50, 50, 10, 10)), 1.0)
        self.assertEqual(image_difference(png, png, box=(0, 0)), 1.0)

    def test_repeated_comparisons_reuse_decoded_images(self):
        from helper_engine.perception import _decoded_rgb, image_difference
        _decoded_rgb.cache_clear()
        png = frame().png
        try:
            self.assertEqual(image_difference(png, png), 0.0)
            info = _decoded_rgb.cache_info()
            self.assertGreater(info.misses, 0)
            self.assertEqual(image_difference(png, png), 0.0)
            self.assertGreater(_decoded_rgb.cache_info().hits, info.hits)
        finally:
            _decoded_rgb.cache_clear()

    def test_decode_cache_is_bounded(self):
        import io
        from PIL import Image
        from helper_engine.perception import _decoded_rgb, image_difference
        _decoded_rgb.cache_clear()
        try:
            pngs = []
            for i in range(10):
                out = io.BytesIO()
                Image.new("RGB", (64, 64), (i * 25 % 256, 0, 0)).save(out, "PNG")
                pngs.append(out.getvalue())
            for png in pngs:
                image_difference(png, png)
            self.assertLessEqual(_decoded_rgb.cache_info().currsize, 4)
        finally:
            _decoded_rgb.cache_clear()

    def test_corrupt_images_are_not_cached(self):
        from helper_engine.perception import _decoded_rgb, image_difference
        _decoded_rgb.cache_clear()
        try:
            self.assertEqual(image_difference(b"not an image", b"also not"), 1.0)
            self.assertEqual(image_difference(b"not an image", b"also not"), 1.0)
            self.assertEqual(_decoded_rgb.cache_info().currsize, 0)
        finally:
            _decoded_rgb.cache_clear()


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


class RouterTests(unittest.TestCase):
    def test_browser_markers_cover_major_browsers(self):
        from helper_engine.router import BROWSER_TITLE_MARKERS
        for name in ("Google Chrome", "Microsoft Edge", "Mozilla Firefox", "Brave"):
            self.assertIn(name, BROWSER_TITLE_MARKERS)

    def test_hybrid_close_clears_selected_when_browser_close_raises(self):
        # Bug: _selected kept pointing at a closed driver if browser.close() raised.
        from unittest.mock import MagicMock
        from helper_engine.router import HybridDriver
        driver = HybridDriver.__new__(HybridDriver)
        driver.desktop = MagicMock()
        driver.browser = MagicMock()
        driver.browser.close.side_effect = RuntimeError("boom")
        driver._selected = driver.browser
        with self.assertRaises(RuntimeError):
            driver.close()
        self.assertIsNone(driver._selected)

    def test_uia_close_terminates_process_when_pipe_close_raises(self):
        # Bug: worker process leaked if pipe.close() raised during close().
        from unittest.mock import MagicMock
        from helper_engine.uia_worker import UiaClient
        client = UiaClient.__new__(UiaClient)
        client._pipe = MagicMock()
        client._pipe.close.side_effect = OSError("broken pipe")
        proc = MagicMock()
        proc.is_alive.return_value = True
        client._process = proc
        with self.assertRaises(OSError):
            client.close()
        proc.terminate.assert_called_once()
        proc.join.assert_called_once()
        self.assertIsNone(client._process)
        self.assertIsNone(client._pipe)


class LimitsTests(unittest.TestCase):
    def test_negative_budgets_rejected(self):
        from helper_engine.runtime import Limits
        with self.assertRaises(ValueError):
            Limits(max_steps=0)
        with self.assertRaises(ValueError):
            Limits(max_seconds=-1)

    def test_infinite_budgets_rejected(self):
        from helper_engine.runtime import Limits
        with self.assertRaises(ValueError):
            Limits(max_seconds=float("inf"))


class RunReportTests(unittest.TestCase):
    def test_ok_covers_completed_and_answered(self):
        from helper_engine.types import RunReport, RunStatus
        for status in (RunStatus.COMPLETED, RunStatus.ANSWERED):
            self.assertTrue(RunReport("g", status, "m").ok)
        for status in (RunStatus.FAILED, RunStatus.CANCELLED, RunStatus.LIMITED, RunStatus.NEEDS_INPUT):
            self.assertFalse(RunReport("g", status, "m").ok)


class CliJournalTests(unittest.TestCase):
    def test_unwritable_trace_journal_does_not_abort_the_run(self):
        from unittest.mock import Mock, patch
        from helper_engine.__main__ import main
        from helper_engine.types import RunReport
        agent = Mock()
        agent.run_goal.return_value = RunReport("Do thing", RunStatus.ANSWERED, "done")
        with tempfile.TemporaryDirectory() as tmp:
            blocker = Path(tmp) / "blocker"
            blocker.write_text("not a directory")
            trace = blocker / "trace.jsonl"
            with patch("helper_engine.bootstrap.build_agent", return_value=agent):
                self.assertEqual(main(["Do", "thing", "--trace", str(trace)]), 0)
        agent.run_goal.assert_called_once()


class SummarizeTests(unittest.TestCase):
    def test_boolean_metrics_do_not_skew_means(self):
        import json
        import tempfile
        from helper_engine.evaluate import summarize
        row = {"event": "finished", "run_id": "r1", "status": "completed",
               "model_calls": True, "actions": 4}
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as f:
            f.write(json.dumps(row) + "\n")
            path = f.name
        result = summarize([path])
        self.assertIsNone(result["mean_model_calls"])
        self.assertEqual(result["mean_actions"], 4.0)


if __name__ == "__main__":
    unittest.main()
