"""Tests for v2 core: ground, plan, verify, execute, agent. No network/display."""
from __future__ import annotations

import io
from dataclasses import dataclass

import pytest
from PIL import Image

from v2.agent import HelperV2
from v2.execute import (
    _actuate,
    _crop_frame,
    _extract_key,
    _strip_verbs,
    do_subgoal,
)
from v2.ground import (
    Target,
    center,
    find_by_text,
    is_typable,
    resolve_mark,
    vision_fallback,
)
from v2.plan import PlanError, Subgoal, make_plan
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


def test_is_typable_case_insensitive():
    # Real UIA trees report capitalized types ("Edit", "Button").
    assert is_typable(_el("t", ctype="Edit")) is True
    assert is_typable(_el("t", ctype="TEXTBOX")) is True
    assert is_typable(_el("b", ctype="Button")) is False


def test_find_by_text_ranks_capitalized_types():
    w = FakeWorld({
        "a": _el("a", name="Save As...", ctype="MenuItem", rect=(0, 0, 200, 30)),
        "b": _el("b", name="Save", ctype="Button", rect=(0, 0, 80, 30)),
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
    def box(png, instr):
        return (5, 5, 10, 10)
    t = vision_fallback(_png(), "x", box, region_origin=(100, 200))
    assert t is not None and t.rect == (105, 205, 10, 10)
    assert t.source == "vision-fallback"


def test_vision_fallback_grounder_raises():
    def boom(png, instr): raise RuntimeError("nope")
    assert vision_fallback(_png(), "x", boom) is None


def test_find_by_text_skips_nonmatching_and_unnamed():
    w = FakeWorld({
        "a": _el("a", name="Cancel"),
        "b": _el("b", name="Save"),
        "c": _el("c", name=""),
    })
    t = find_by_text(w, "Save")
    assert t is not None and t.element_id == "b"
    assert find_by_text(FakeWorld({"a": _el("a", name="Cancel")}),
                        "Save") is None
    assert find_by_text(FakeWorld({"c": _el("c", name="")}),
                        "Save") is None


def test_vision_fallback_grounder_returns_none():
    assert vision_fallback(_png(), "x", lambda png, instr: None) is None


def test_vision_fallback_zero_size_box_returns_none():
    assert vision_fallback(_png(), "x",
                           lambda png, instr: (5, 5, 0, 10)) is None
    assert vision_fallback(_png(), "x",
                           lambda png, instr: (5, 5, 10, -1)) is None


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


def test_regions_differ_undecodable_counts_as_change():
    # Undecodable crops must never be reported as "no change" — that
    # would silently skip verification.
    assert regions_differ(b"not a png", b"not a png") is True
    assert regions_differ(_png(), b"not a png") is True


def test_regions_differ_resizes_mismatched():
    same = _png((255, 0, 0), (64, 64))
    assert regions_differ(same, _png((255, 0, 0), (32, 32))) is False
    assert regions_differ(same, _png((0, 0, 255), (32, 32))) is True


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


# ----------------------------------------------------------------
# execute — branches previously only reachable in production
# ----------------------------------------------------------------

class TogglingWorld(FakeWorld):
    """current_frame() returns a distinct PNG on every call (the color
    encodes the call counter), so before/after verify crops always differ
    and the VLM verifier is actually consulted — no matter how many
    current_frame() calls grounding interleaves between the two crops."""

    def __init__(self, elements: dict | None = None):
        super().__init__(elements)
        self._n = 0

    def current_frame(self):
        self._n += 1
        color = ((self._n * 53) % 256, (self._n * 97) % 256,
                 (self._n * 149) % 256)
        return _frame(self._frame.elements, png=_png(color))


class RecordingActuator(FakeActuator):
    def __init__(self, fail_invoke: bool = False):
        super().__init__()
        self.fail_invoke = fail_invoke
        self.typed: list[str] = []
        self.keys: list[str] = []

    def type_text(self, text):
        self.typed.append(text)

    def press_key(self, key):
        self.keys.append(key)


def test_do_subgoal_abort_before_first_attempt():
    world = _world_with_save()
    act = FakeActuator()
    sg = Subgoal(id="sg1", description='Click the "Save" button',
                 success_criteria="dialog visible", target_hint="Save")
    res = do_subgoal(sg, world, act, FakeLlm([]),
                     should_abort=lambda: True)
    assert res.ok is False
    assert res.detail == "aborted by user"
    assert res.attempts == 1
    assert act.clicked_elements == []  # nothing actuated


def test_do_subgoal_verifier_raises_is_not_failure_proof():
    # The VLM verifier raising (here: FakeLlm out of replies) must fail
    # the attempt and retry, never propagate out of do_subgoal.
    world = TogglingWorld({"1:42": _el()})
    act = FakeActuator()
    sg = Subgoal(id="sg1", description='Click the "Save" button',
                 success_criteria="dialog visible", target_hint="Save")
    res = do_subgoal(sg, world, act, FakeLlm([]), max_attempts=2)
    assert res.ok is False
    assert res.attempts == 2
    assert "verification failed" in res.detail


def test_do_subgoal_mark_hint_resolves():
    world = TogglingWorld({"1:42": _el()})
    act = FakeActuator()
    sg = Subgoal(id="sg1", description="Click Save",
                 success_criteria="dialog visible", target_hint="mark 1")
    res = do_subgoal(sg, world, act, FakeLlm(["yes"]),
                     marks=[FakeMark(1, "1:42")], max_attempts=1)
    assert res.ok is True and res.attempts == 1
    assert act.clicked_elements == ["1:42"]


def test_do_subgoal_type_branch_clicks_then_types():
    # Non-typable element: no SetValue path — focus-click then type_text.
    el = _el("b1", name="Name", ctype="button", rect=(0, 0, 100, 30))
    world = TogglingWorld({"b1": el})
    act = RecordingActuator()
    sg = Subgoal(id="sg1", description='Type "hello" into the Name field',
                 success_criteria="hello visible", target_hint="Name")
    res = do_subgoal(sg, world, act, FakeLlm(["yes"]), max_attempts=1)
    assert res.ok is True
    assert act.clicked_elements == ["b1"]  # focus click
    assert act.typed == ["hello"]


def test_do_subgoal_key_branch():
    # UIA click fails (pattern unsupported) → key branch via "press esc".
    world = TogglingWorld({"1:42": _el()})
    act = RecordingActuator(fail_invoke=True)
    sg = Subgoal(id="sg1", description="Press Escape to close the dialog",
                 success_criteria="dialog closed", target_hint="Save")
    res = do_subgoal(sg, world, act, FakeLlm(["yes"]), max_attempts=1)
    assert res.ok is True
    assert act.keys == ["escape"]


def test_do_subgoal_default_click():
    # Description with no verb keywords falls through to the default click.
    world = TogglingWorld({"1:42": _el()})
    act = FakeActuator()
    sg = Subgoal(id="sg1", description="Do the Save thing",
                 success_criteria="done", target_hint="Save")
    res = do_subgoal(sg, world, act, FakeLlm(["yes"]), max_attempts=1)
    assert res.ok is True
    assert act.clicked_elements == ["1:42"]


def test_actuate_type_with_no_element_clicks_at_center():
    # Vision-fallback target (no element id): click at rect center, then type.
    world = FakeWorld({})
    act = RecordingActuator()
    target = Target(element_id=None, rect=(0, 0, 100, 40),
                    source="vision-fallback", confidence=0.6)
    sg = Subgoal(id="sg1", description='Type "hi"',
                 success_criteria="hi visible")
    _actuate(sg, target, world, act)
    assert act.clicked_at == [(50, 20)]
    assert act.typed == ["hi"]


def test_strip_verbs():
    assert _strip_verbs('Click the "Save" button') == "Save"
    assert _strip_verbs("Click the Save button") == "Save"
    assert _strip_verbs("Press the Cancel link") == "Cancel"
    assert _strip_verbs("Open the File menu") == "File"
    assert _strip_verbs("Save the file") == "Save the file"  # no verb


def test_extract_key():
    assert _extract_key("Press Escape to close") == "escape"
    assert _extract_key("Press F5 to refresh") == "f5"
    assert _extract_key("Do something") == "enter"  # default


def test_crop_frame_falls_back_to_full_frame():
    frame = _frame()
    target = Target(element_id=None, rect=(0, 0, 10, 10),
                    source="vision-fallback", confidence=0.6)

    def boom(png, rect, pad=0):
        raise RuntimeError("no crop")

    assert _crop_frame(frame, target, boom) == frame.png


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


def test_agent_happy_path_all_verified():
    # The success path (done, no replan) previously had no test: every
    # agent test drove failures. TogglingWorld makes the crops differ so
    # the verifier says yes on the first attempt.
    world = TogglingWorld({"1:42": _el()})
    act = FakeActuator()
    llm = FakeLlm([_AGENT_PLAN, "yes"])
    agent = HelperV2(llm=llm, actuator=act, world=world, max_attempts=1)
    report = agent.run_goal("save")
    assert report.ok is True
    assert report.done == ["sg1"]
    assert report.replanned is False


def test_agent_replan_then_success():
    # First subgoal fails verification ("no") → replan → the fresh
    # plan's subgoal verifies ("yes") → run succeeds.
    plan2 = (
        '[{"description": "Click the \\"Save\\" button", '
        '"success_criteria": "dialog visible", "target_hint": "Save"}]'
    )
    world = TogglingWorld({"1:42": _el()})
    act = FakeActuator()
    llm = FakeLlm([_AGENT_PLAN, "no", plan2, "yes"])
    agent = HelperV2(llm=llm, actuator=act, world=world, max_attempts=1)
    report = agent.run_goal("save")
    assert report.ok is True
    assert report.done == ["sg1"]
    assert report.failed == []
    assert report.replanned is True


def test_agent_plan_error_falls_back_to_empty_plan():
    # A planner that returns garbage raises PlanError inside make_plan;
    # the agent must swallow it into an empty plan, not crash.
    world = FakeWorld({})
    agent = HelperV2(llm=FakeLlm(["not json at all"]),
                     actuator=FakeActuator(), world=world)
    report = agent.run_goal("save")
    assert report.ok is True and report.done == [] and report.failed == []


def test_agent_abort_before_first_subgoal():
    # Abort already set when the loop starts: the subgoal is recorded as
    # failed and do_subgoal is never entered.
    world = _world_with_save()
    agent = HelperV2(llm=FakeLlm([_AGENT_PLAN]), actuator=FakeActuator(),
                     world=world)
    report = agent.run_goal("save", should_abort=lambda: True)
    assert report.ok is False
    assert report.failed == ["sg1"]


def test_agent_default_world_is_constructed():
    # world=None builds a real WorldModel (no start — just construction).
    from v2.world import WorldModel

    agent = HelperV2(llm=FakeLlm([]), actuator=FakeActuator())
    assert isinstance(agent._world, WorldModel)


def test_agent_planner_transport_error_falls_back_to_empty_plan():
    # An LLM transport failure (network/API) during planning must degrade
    # like PlanError — empty plan, no crash, goal finishes cleanly.
    class BoomLlm:
        def chat(self, messages, *, system=None, max_tokens=1024) -> str:
            raise ConnectionError("transient network failure")

    world = FakeWorld({})
    events: list[str] = []
    agent = HelperV2(llm=BoomLlm(), actuator=FakeActuator(), world=world)
    report = agent.run_goal("save", on_event=lambda n, p: events.append(n))
    assert report.ok is True and report.done == [] and report.failed == []
    assert events == ["plan", "finished"]
    assert world.stopped


def test_agent_last_marks_is_per_instance():
    # _last_marks must be instance state, not a shared class-level list:
    # mutating one agent's marks must not leak into another.
    a = HelperV2(llm=FakeLlm([]), actuator=FakeActuator(), world=FakeWorld({}))
    b = HelperV2(llm=FakeLlm([]), actuator=FakeActuator(), world=FakeWorld({}))
    assert a._last_marks is not b._last_marks
    a._last_marks.append("mark-1")
    assert b._last_marks == []
