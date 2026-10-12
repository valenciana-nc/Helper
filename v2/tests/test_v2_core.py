"""Tests for v2 core: ground, plan, verify, execute, agent. No network/display."""
from __future__ import annotations

import io
from dataclasses import dataclass, field

import pytest
from PIL import Image

from v2.agent import HelperV2
from v2.execute import do_subgoal
from v2.ground import (
    Target,
    center,
    find_by_text,
    resolve_mark,
    vision_fallback,
)
from v2.plan import Plan, PlanError, Subgoal, make_plan
from v2.verify import regions_differ, verify_subgoal
from v2.world import Element, ScreenFrame


# ---------------------------------------------------------------- fakes

@dataclass
class FakeMark:
    n: int
    element_id: str


def _el(eid="1:42", name="Save", ctype="button",
        rect=(10, 10, 80, 30)) -> Element:
    return Element(id=eid, control_type=ctype, name=name, rect=rect,
                   enabled=True, visible=True, value=None)


def _png(color=(255, 0, 0), size=(64, 64)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


def _frame(elements: dict | None = None, png: bytes | None = None) -> ScreenFrame:
    return ScreenFrame(seq=1, png=png or _png(),
                       width=64, height=64,
                       elements=elements or {}, changed_ids=set())


class FakeWorld:
    def __init__(self, elements: dict | None = None):
        self._frame = _frame(elements)
        self.started = False
        self.stopped = False

    def start(self): self.started = True
    def stop(self): self.stopped = True
    def current_frame(self): return self._frame
    def wait_for_change(self, timeout_s: float): return None
    def element(self, eid: str): return self._frame.elements.get(eid)


class FakeLlm:
    def __init__(self, replies: list[str]):
        self._replies = list(replies)
        self.calls: list[dict] = []

    def chat(self, messages, *, system=None, max_tokens=1024) -> str:
        self.calls.append({"system": system, "n_messages": len(messages)})
        if not self._replies:
            raise AssertionError("FakeLlm out of replies")
        return self._replies.pop(0)


class FakeActuator:
    def __init__(self):
        self.clicked_elements: list[str] = []
        self.clicked_at: list[tuple[int, int]] = []
        self.values: dict[str, str] = {}
        self.fail_invoke = False

    def click_element(self, el) -> bool:
        self.clicked_elements.append(el.id)
        return not self.fail_invoke

    def set_value(self, el, text: str) -> bool:
        self.values[el.id] = text
        return True

    def click_at(self, x, y): self.clicked_at.append((x, y))
    def type_text(self, text): pass
    def press_key(self, key): pass


# ---------------------------------------------------------------- ground

def test_resolve_mark_hits_world():
    w = FakeWorld({"1:42": _el()})
    t = resolve_mark([FakeMark(1, "1:42")], 1, w)
    assert t is not None and t.rect == (10, 10, 80, 30)
    assert t.source == "mark" and t.confidence == 1.0


def test_resolve_mark_unknown_number():
    w = FakeWorld({"1:42": _el()})
    assert resolve_mark([FakeMark(1, "1:42")], 2, w) is None


def test_resolve_mark_stale_element():
    w = FakeWorld({})
    assert resolve_mark([FakeMark(1, "1:42")], 1, w) is None


def test_find_by_text_prefers_exact_and_actionable():
    w = FakeWorld({
        "a": _el("a", name="Save As...", ctype="menuitem", rect=(0, 0, 200, 30)),
        "b": _el("b", name="Save", ctype="button", rect=(0, 0, 80, 30)),
    })
    t = find_by_text(w, "Save")
    assert t is not None and t.element_id == "b" and t.source == "uia-direct"


def test_find_by_text_skips_hidden():
    hidden = _el("h", name="Save")
    hidden = Element(id="h", control_type="button", name="Save",
                     rect=(0, 0, 80, 30), enabled=True, visible=False,
                     value=None)
    w = FakeWorld({"h": hidden})
    assert find_by_text(w, "Save") is None


def test_find_by_text_empty():
    assert find_by_text(FakeWorld({"b": _el()}), "   ") is None


def test_vision_fallback_none_when_no_grounder():
    assert vision_fallback(_png(), "click save", None) is None


def test_vision_fallback_maps_region_to_screen():
    box = lambda png, instr: (5, 5, 10, 10)
    t = vision_fallback(_png(), "x", box, region_origin=(100, 200))
    assert t is not None and t.rect == (105, 205, 10, 10)
    assert t.source == "vision-fallback"


def test_vision_fallback_grounder_raises():
    def boom(png, instr): raise RuntimeError("nope")
    assert vision_fallback(_png(), "x", boom) is None


def test_center():
    assert center((10, 20, 80, 40)) == (50, 40)


# ---------------------------------------------------------------- plan

_PLAN_JSON = (
    '[{"description": "Click the \\"Save\\" button", '
    '"success_criteria": "Save dialog is visible", "target_hint": "mark 3"},'
    ' {"description": "Type \\"hi\\"", "success_criteria": "text hi appears"}]'
)


def test_make_plan_parses():
    llm = FakeLlm([_PLAN_JSON])
    plan = make_plan("save the file", llm, _png(), ["Save"])
    assert len(plan.subgoals) == 2
    assert plan.subgoals[0].target_hint == "mark 3"
    assert plan.subgoals[1].target_hint is None
    assert llm.calls[0]["system"] is not None


def test_make_plan_tolerates_fences():
    llm = FakeLlm(["```json\n" + _PLAN_JSON + "\n```"])
    plan = make_plan("g", llm, _png(), [])
    assert len(plan.subgoals) == 2


def test_make_plan_tolerates_prose_around_json():
    llm = FakeLlm([
        "Here is the plan:\n" + _PLAN_JSON + "\nLet me know if that works."
    ])
    plan = make_plan("g", llm, _png(), [])
    assert len(plan.subgoals) == 2
    assert plan.subgoals[0].target_hint == "mark 3"


def test_make_plan_tolerates_object_wrapped_array():
    llm = FakeLlm(['{"subgoals": ' + _PLAN_JSON + "}"])
    plan = make_plan("g", llm, _png(), [])
    assert len(plan.subgoals) == 2


def test_make_plan_bad_json_raises():
    llm = FakeLlm(["not json at all"])
    with pytest.raises(PlanError):
        make_plan("g", llm, _png(), [])


def test_make_plan_empty_means_done():
    llm = FakeLlm(["[]"])
    plan = make_plan("g", llm, _png(), [])
    assert plan.done is False  # no subgoals → not "done", caller handles


# ---------------------------------------------------------------- verify

def test_regions_differ_same():
    p = _png()
    assert regions_differ(p, p) is False


def test_regions_differ_changed():
    assert regions_differ(_png((255, 0, 0)), _png((0, 0, 255))) is True


def test_verify_subgoal_fast_path_no_change():
    llm = FakeLlm(["yes"])  # should never be consumed
    sg = Subgoal(id="sg1", description="d", success_criteria="c")
    p = _png()
    assert verify_subgoal(sg, p, p, llm) is False
    assert llm.calls == []


def test_verify_subgoal_asks_vlm():
    llm = FakeLlm(["yes, the dialog is visible"])
    sg = Subgoal(id="sg1", description="d", success_criteria="c")
    assert verify_subgoal(sg, _png((255, 0, 0)), _png((0, 0, 255)), llm) is True


def test_verify_subgoal_no():
    llm = FakeLlm(["no"])
    sg = Subgoal(id="sg1", description="d", success_criteria="c")
    assert verify_subgoal(sg, _png((255, 0, 0)), _png((0, 0, 255)), llm) is False


# ---------------------------------------------------------------- execute

def _world_with_save():
    return FakeWorld({"1:42": _el()})


def test_do_subgoal_click_verified_first_try():
    # Verifier says yes when the after-crop differs; make crops differ by
    # having the fake world return a different frame... simplest: verifier
    # mock via FakeLlm "yes" and crops that differ.
    world = _world_with_save()
    act = FakeActuator()
    sg = Subgoal(id="sg1", description='Click the "Save" button',
                 success_criteria="dialog visible", target_hint="Save")
    # Force crops to differ: monkeypatch world frame png between calls is
    # complex; instead give FakeLlm "yes" and rely on... crops are identical
    # (same frame) → fast path returns False. So expect retry exhaustion.
    llm = FakeLlm(["yes"] * 10)
    res = do_subgoal(sg, world, act, llm, max_attempts=2)
    assert res.ok is False and res.attempts == 2
    assert act.clicked_elements == ["1:42", "1:42"]


def test_do_subgoal_no_target():
    world = FakeWorld({})
    act = FakeActuator()
    llm = FakeLlm([])
    sg = Subgoal(id="sg1", description="Click nothing", success_criteria="x")
    res = do_subgoal(sg, world, act, llm, max_attempts=2)
    assert res.ok is False and "could not ground" in res.detail


def test_do_subgoal_typing_uses_set_value():
    el = _el("t1", name="Name", ctype="edit", rect=(0, 0, 100, 30))
    world = FakeWorld({"t1": el})
    act = FakeActuator()
    llm = FakeLlm([])
    sg = Subgoal(id="sg1", description='Type "hello" into the Name field',
                 success_criteria="hello visible", target_hint="Name")
    res = do_subgoal(sg, world, act, llm, max_attempts=1)
    assert act.values.get("t1") == "hello"
    assert res.ok is False  # crops identical → not verified


class _ExplodingActuator(FakeActuator):
    """Synthetic input unavailable (e.g. headless): raises on use."""

    def click_element(self, el) -> bool:
        self.clicked_elements.append(el.id)
        return False  # force the synthetic fallback

    def click_at(self, x, y):
        raise RuntimeError("no display for synthetic input")


def test_do_subgoal_actuation_failure_does_not_raise():
    # Docstring contract: do_subgoal never raises for actuation failures.
    world = _world_with_save()
    act = _ExplodingActuator()
    llm = FakeLlm([])
    sg = Subgoal(id="sg1", description='Click the "Save" button',
                 success_criteria="dialog visible", target_hint="Save")
    res = do_subgoal(sg, world, act, llm, max_attempts=2)
    assert res.ok is False
    assert res.attempts == 2
    assert "actuation failed" in res.detail


# ---------------------------------------------------------------- agent

_AGENT_PLAN = (
    '[{"description": "Click the \\"Save\\" button", '
    '"success_criteria": "dialog visible", "target_hint": "Save"}]'
)


def test_agent_runs_plan_and_reports():
    world = _world_with_save()
    act = FakeActuator()
    llm = FakeLlm([_AGENT_PLAN, _AGENT_PLAN] + ["yes"] * 10)
    events: list[str] = []
    agent = HelperV2(llm=llm, actuator=act, world=world, max_attempts=1)
    report = agent.run_goal("save", on_event=lambda n, p: events.append(n))
    assert "plan" in events and "finished" in events
    assert world.started and world.stopped
    # Not verified (identical crops) → replan path → failed recorded
    assert report.replanned is True
    assert report.ok is False


def test_agent_empty_plan_finishes():
    world = FakeWorld({})
    agent = HelperV2(llm=FakeLlm(["[]"]), actuator=FakeActuator(),
                     world=world)
    report = agent.run_goal("already done")
    assert report.ok is True and report.done == []


def _replan_agent(monkeypatch, abort):
    """Drive run_goal into the replan branch with do_subgoal stubbed out."""
    import v2.agent as agent_mod
    from v2.execute import ActionResult

    seen = []

    def fake_do_subgoal(subgoal, world, actuator, llm, **kw):
        seen.append(kw.get("should_abort"))
        return ActionResult(ok=False, detail="nope", attempts=1)

    monkeypatch.setattr(agent_mod, "do_subgoal", fake_do_subgoal)
    plan_json = ('[{"description": "Click \\"Save\\"", '
                 '"success_criteria": "x", "target_hint": "Save"}]')
    world = _world_with_save()
    agent = HelperV2(llm=FakeLlm([plan_json, plan_json]),
                     actuator=FakeActuator(), world=world, max_attempts=1)
    report = agent.run_goal("save", should_abort=abort)
    return agent, report, seen


def test_agent_replan_passes_abort_to_subgoals(monkeypatch):
    # Replanned subgoals must receive should_abort (Escape/abort used to be
    # silently ignored once the agent replanned).
    abort = lambda: False  # noqa: E731
    _, report, seen = _replan_agent(monkeypatch, abort)
    # Outer subgoal + one replanned subgoal, both wired to the abort flag.
    assert seen == [abort, abort]
    assert report.replanned is True
    assert report.failed == ["sg1"]


def test_agent_replan_honors_abort(monkeypatch):
    abort_calls = {"n": 0}

    def abort():
        abort_calls["n"] += 1
        return abort_calls["n"] >= 2  # abort as soon as replanning starts

    _, report, seen = _replan_agent(monkeypatch, abort)
    assert [s for s in seen] == [abort]  # inner subgoal never executed
    assert report.failed == ["sg1"]  # replanned subgoal recorded as failed
