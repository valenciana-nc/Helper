from __future__ import annotations

import unittest
from dataclasses import dataclass, field
from typing import Any, Callable

from agent import (
    GuideAction,
    GuideSession,
    GuideTurn,
    HelplerAgent,
    LimitExceeded,
)
from computer_control import AbortController
from main import run_active_continuation_loop


def _turn(message: str, actions: list[GuideAction]) -> GuideTurn:
    return GuideTurn(
        message=message,
        actions=actions,
        done=not actions,
        step_index=0,
        elapsed_sec=0.0,
        capture=None,
    )


def _action(name: str = "click_at") -> GuideAction:
    return GuideAction(name=name, summary=f"Run {name}")


@dataclass
class ScriptedAgent:
    """Agent stub that returns pre-scripted turns from continue_guide.

    Supports optional verifier responses and per-call side effects for testing
    reliability hooks in `run_active_continuation_loop`.
    """

    scripted: list[GuideTurn]
    calls: list[dict] = field(default_factory=list)
    side_effects: list[Callable[[], Any] | None] = field(default_factory=list)
    verifier_results: list[tuple[bool, str]] = field(default_factory=list)
    verifier_calls: list[GuideSession] = field(default_factory=list)
    verifier_side_effect: Callable[[], Any] | None = None

    def continue_guide(self, session, capture=None, note=None):
        self.calls.append({"note": note})
        if self.side_effects:
            side = self.side_effects.pop(0)
            if side is not None:
                result = side()
                if isinstance(result, GuideTurn):
                    return result
        if not self.scripted:
            return _turn("done.", [])
        return self.scripted.pop(0)

    def verify_goal_complete(self, session, capture=None):
        self.verifier_calls.append(session)
        if self.verifier_side_effect is not None:
            self.verifier_side_effect()
        if not self.verifier_results:
            return True, "no verifier result scripted"
        return self.verifier_results.pop(0)


class RecordingSleep:
    def __init__(self) -> None:
        self.durations: list[float] = []

    def __call__(self, abort, seconds: float) -> None:
        self.durations.append(seconds)


class ActiveLoopTests(unittest.TestCase):
    def _session(self) -> GuideSession:
        return GuideSession(goal="test")

    def test_zero_action_intent_triggers_one_shot_retry(self) -> None:
        agent = ScriptedAgent(scripted=[_turn("clicking now.", [_action()]), _turn("all done.", [])])
        abort = AbortController()
        sleeper = RecordingSleep()
        seen: list[GuideTurn] = []

        final = run_active_continuation_loop(
            agent,
            self._session(),
            abort,
            _turn("I'll open Gmail for you.", []),
            settle_sec=0.05,
            on_turn=seen.append,
            sleep_fn=sleeper,
        )

        self.assertEqual(len(agent.calls), 2)
        self.assertEqual(agent.calls[0]["note"], "You said you'd act but did not call a tool. Call the appropriate tool now to perform the requested action.")
        self.assertIsNone(agent.calls[1]["note"])
        self.assertEqual([t.message for t in seen], ["clicking now.", "all done."])
        self.assertEqual(final.message, "all done.")

    def test_no_retry_when_first_turn_has_no_intent(self) -> None:
        agent = ScriptedAgent(scripted=[_turn("should not run", [_action()])])
        abort = AbortController()
        sleeper = RecordingSleep()

        final = run_active_continuation_loop(
            agent,
            self._session(),
            abort,
            _turn("Sure, here's the weather forecast.", []),
            settle_sec=0.05,
            sleep_fn=sleeper,
        )

        self.assertEqual(agent.calls, [])
        self.assertEqual(final.message, "Sure, here's the weather forecast.")
        self.assertEqual(sleeper.durations, [])

    def test_settle_delay_between_iterations(self) -> None:
        agent = ScriptedAgent(
            scripted=[
                _turn("step 2", [_action()]),
                _turn("step 3", [_action()]),
                _turn("done", []),
            ]
        )
        abort = AbortController()
        sleeper = RecordingSleep()

        run_active_continuation_loop(
            agent,
            self._session(),
            abort,
            _turn("step 1", [_action()]),
            settle_sec=0.25,
            sleep_fn=sleeper,
        )

        self.assertEqual(sleeper.durations, [0.25, 0.25, 0.25])
        self.assertEqual(len(agent.calls), 3)

    def test_abort_mid_loop_exits_cleanly(self) -> None:
        abort = AbortController()

        class AbortingSleep:
            def __init__(self) -> None:
                self.calls = 0

            def __call__(self, abort_ctrl, seconds: float) -> None:
                self.calls += 1
                if self.calls == 1:
                    abort_ctrl.request_abort("test abort")

        agent = ScriptedAgent(scripted=[_turn("step 2", [_action()]), _turn("step 3", [_action()])])
        sleeper = AbortingSleep()

        final = run_active_continuation_loop(
            agent,
            self._session(),
            abort,
            _turn("step 1", [_action()]),
            settle_sec=0.05,
            sleep_fn=sleeper,
        )

        self.assertEqual(agent.calls, [])
        self.assertEqual(final.message, "step 1")
        self.assertTrue(abort.is_aborted())

    def test_loop_stops_when_actions_empty(self) -> None:
        agent = ScriptedAgent(scripted=[_turn("done", [])])
        abort = AbortController()
        sleeper = RecordingSleep()

        final = run_active_continuation_loop(
            agent,
            self._session(),
            abort,
            _turn("step 1", [_action()]),
            settle_sec=0.0,
            sleep_fn=sleeper,
        )

        self.assertEqual(len(agent.calls), 1)
        self.assertTrue(final.done)
        self.assertEqual(sleeper.durations, [0.0])

    def test_pre_aborted_no_iterations(self) -> None:
        agent = ScriptedAgent(scripted=[_turn("should not run", [_action()])])
        abort = AbortController()
        abort.request_abort("already aborted")
        sleeper = RecordingSleep()

        final = run_active_continuation_loop(
            agent,
            self._session(),
            abort,
            _turn("step 1", [_action()]),
            settle_sec=0.05,
            sleep_fn=sleeper,
        )

        self.assertEqual(agent.calls, [])
        self.assertEqual(final.message, "step 1")
        self.assertEqual(sleeper.durations, [])

    def test_verifier_blocks_premature_exit(self) -> None:
        agent = ScriptedAgent(
            scripted=[
                _turn("done filling form", []),     # iter 1: model claims done
                _turn("really done now", []),       # iter 2: model claims done again after followup
            ],
            verifier_results=[(False, "form is empty")],
        )
        abort = AbortController()
        sleeper = RecordingSleep()

        final = run_active_continuation_loop(
            agent,
            self._session(),
            abort,
            _turn("step 1", [_action()]),
            settle_sec=0.05,
            sleep_fn=sleeper,
        )

        self.assertEqual(len(agent.verifier_calls), 1)
        self.assertEqual(len(agent.calls), 2)
        followup_note = agent.calls[1]["note"] or ""
        self.assertIn("form is empty", followup_note)
        self.assertIn("not done", followup_note)
        self.assertEqual(final.message, "really done now")

    def test_verifier_confirms_done_passes_through(self) -> None:
        agent = ScriptedAgent(
            scripted=[_turn("done.", [])],
            verifier_results=[(True, "complete")],
        )
        abort = AbortController()
        sleeper = RecordingSleep()

        final = run_active_continuation_loop(
            agent,
            self._session(),
            abort,
            _turn("step 1", [_action()]),
            settle_sec=0.0,
            sleep_fn=sleeper,
        )

        self.assertEqual(len(agent.verifier_calls), 1)
        self.assertEqual(len(agent.calls), 1)
        self.assertEqual(final.message, "done.")

    def test_verifier_runs_at_most_once(self) -> None:
        agent = ScriptedAgent(
            scripted=[
                _turn("still trying", [_action()]),
                _turn("really done", []),
            ],
            verifier_results=[(False, "almost there"), (False, "should never run")],
        )
        abort = AbortController()
        sleeper = RecordingSleep()

        run_active_continuation_loop(
            agent,
            self._session(),
            abort,
            _turn("step 1", [_action()]),
            settle_sec=0.0,
            sleep_fn=sleeper,
        )

        self.assertEqual(len(agent.verifier_calls), 1)

    def test_verifier_skipped_when_no_actions_ever_taken(self) -> None:
        """Initial chat-only turn (e.g., 'Sure, here's the weather forecast.')
        should pass through without calling the verifier — there's nothing to verify."""
        agent = ScriptedAgent(scripted=[_turn("should not run", [_action()])])
        abort = AbortController()
        sleeper = RecordingSleep()

        final = run_active_continuation_loop(
            agent,
            self._session(),
            abort,
            _turn("Here's the answer to your question.", []),
            settle_sec=0.05,
            sleep_fn=sleeper,
        )

        self.assertEqual(agent.verifier_calls, [])
        self.assertEqual(agent.calls, [])
        self.assertEqual(final.message, "Here's the answer to your question.")

    def test_mid_loop_intent_nudge_capped_per_streak(self) -> None:
        """Within a streak of zero-action turns the nudge can fire at most once,
        even if the model keeps repeating 'I will do X' without acting. After the
        nudge, the second back-to-back stall falls through to the verifier."""
        agent = ScriptedAgent(
            scripted=[
                _turn("I'll click submit", []),         # iter after step 1 -> empty+intent
                _turn("I will type the password", []),  # nudge response -> still empty+intent
            ],
            verifier_results=[(True, "ok")],
        )
        abort = AbortController()
        sleeper = RecordingSleep()

        final = run_active_continuation_loop(
            agent,
            self._session(),
            abort,
            _turn("step 1", [_action()]),
            settle_sec=0.0,
            sleep_fn=sleeper,
        )

        notes = [c["note"] for c in agent.calls]
        nudge_count = sum(1 for n in notes if n and "did not call a tool" in n)
        self.assertEqual(nudge_count, 1)
        self.assertEqual(len(agent.verifier_calls), 1)
        self.assertEqual(final.message, "I will type the password")

    def test_exhaustion_returns_wrapup_turn(self) -> None:
        def _raise_steps() -> None:
            raise LimitExceeded("step limit", kind="steps")

        agent = ScriptedAgent(
            scripted=[_turn("step 2", [_action()])],
            side_effects=[None, _raise_steps],
        )
        abort = AbortController()
        sleeper = RecordingSleep()
        seen: list[GuideTurn] = []

        final = run_active_continuation_loop(
            agent,
            self._session(),
            abort,
            _turn("step 1", [_action()]),
            settle_sec=0.0,
            on_turn=seen.append,
            sleep_fn=sleeper,
        )

        self.assertTrue(final.done)
        self.assertIn("maximum step count", final.message)
        self.assertIs(seen[-1], final)

    def test_provider_error_retry_then_success(self) -> None:
        from openai_client import RateLimited

        def _raise_rate_limited() -> None:
            raise RateLimited("slow down")

        agent = ScriptedAgent(
            scripted=[_turn("done.", [])],
            side_effects=[_raise_rate_limited, _raise_rate_limited],
        )
        abort = AbortController()
        sleeper = RecordingSleep()

        final = run_active_continuation_loop(
            agent,
            self._session(),
            abort,
            _turn("step 1", [_action()]),
            settle_sec=0.0,
            sleep_fn=sleeper,
            provider_retry_backoff=(0.0, 0.0, 0.0),
        )

        self.assertEqual(final.message, "done.")
        self.assertEqual(len(agent.calls), 3)

    def test_provider_error_gives_up_after_retries(self) -> None:
        from openai_client import RateLimited

        def _raise_rate_limited() -> None:
            raise RateLimited("slow down")

        agent = ScriptedAgent(
            scripted=[],
            side_effects=[
                _raise_rate_limited,
                _raise_rate_limited,
                _raise_rate_limited,
                _raise_rate_limited,
            ],
        )
        abort = AbortController()
        sleeper = RecordingSleep()

        final = run_active_continuation_loop(
            agent,
            self._session(),
            abort,
            _turn("step 1", [_action()]),
            settle_sec=0.0,
            sleep_fn=sleeper,
            provider_retry_backoff=(0.0, 0.0, 0.0),
        )

        self.assertTrue(final.done)
        self.assertIn("provider error", final.message)


class ContinuationNoteTests(unittest.TestCase):
    def test_goal_is_prefixed_in_continuation_note(self) -> None:
        session = GuideSession(goal="Open Notepad and type hello")
        note = HelplerAgent._build_continuation_note(session)
        self.assertIn("Original goal: Open Notepad and type hello", note)

    def test_continuation_note_without_goal_still_works(self) -> None:
        session = GuideSession(goal="")
        note = HelplerAgent._build_continuation_note(session)
        self.assertNotIn("Original goal:", note)
        self.assertTrue(note)


if __name__ == "__main__":
    unittest.main()
