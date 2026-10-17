"""HelperV2: the v2 agent loop.

run_goal(): observe → plan ONCE → for each subgoal: closed-loop execute →
verify → (retry locally, replan once if needed) → report.

Events emitted via on_event(name, payload): "plan", "subgoal_start",
"attempt", "subgoal_done", "subgoal_failed", "replan", "finished".
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, TYPE_CHECKING

from .execute import Actuator, do_subgoal
from .observe import annotate
from .plan import LlmPort, Plan, PlanError, make_plan

if TYPE_CHECKING:
    from .world import WorldModel

log = logging.getLogger("helper.v2.agent")

# Re-export for consumers that expect it here (spec canonical home).
__all__ = ["HelperV2", "LlmPort", "RunReport"]


@dataclass
class RunReport:
    goal: str
    done: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    replanned: bool = False

    @property
    def ok(self) -> bool:
        return not self.failed


class HelperV2:
    def __init__(
        self,
        *,
        llm: LlmPort,
        actuator: Actuator,
        world: WorldModel | None = None,
        max_attempts: int = 3,
    ) -> None:
        self._llm = llm
        self._actuator = actuator
        if world is None:
            from .world import WorldModel as _WM
            world = _WM()
        self._world = world
        self._max_attempts = max_attempts
        self._last_marks: list = []

    def run_goal(
        self,
        goal: str,
        *,
        on_event: Callable[[str, dict[str, Any]], None] | None = None,
        should_abort: Callable[[], bool] | None = None,
    ) -> RunReport:
        emit = on_event or (lambda _n, _p: None)
        aborted = should_abort or (lambda: False)
        report = RunReport(goal=goal)
        self._world.start()
        try:
            plan = self._observe_and_plan(goal, emit)
            if not plan.subgoals:
                emit("finished", {"report": _report_dict(report)})
                return report

            for subgoal in plan.subgoals:
                if aborted():
                    emit("finished", {"report": _report_dict(report)})
                    report.failed.append(subgoal.id)
                    break
                emit("subgoal_start", {"id": subgoal.id,
                                      "description": subgoal.description})
                result = do_subgoal(
                    subgoal, self._world, self._actuator, self._llm,
                    marks=self._last_marks,
                    max_attempts=self._max_attempts,
                    should_abort=aborted,
                )
                emit("attempt", {"id": subgoal.id, "ok": result.ok,
                                 "detail": result.detail,
                                 "attempts": result.attempts})
                if result.ok:
                    subgoal.done = True
                    report.done.append(subgoal.id)
                    emit("subgoal_done", {"id": subgoal.id})
                    continue

                # Local retries exhausted → one replan before giving up.
                emit("subgoal_failed", {"id": subgoal.id,
                                       "detail": result.detail})
                if not report.replanned:
                    report.replanned = True
                    emit("replan", {"after": subgoal.id})
                    plan = self._observe_and_plan(
                        f"{goal}\n\nProgress so far: "
                        f"{len(report.done)} step(s) done. "
                        f"Stuck on: {subgoal.description} ({result.detail}). "
                        "Replan the remaining work.",
                        emit,
                    )
                    # Execute the fresh plan's subgoals inline.
                    for sg in plan.subgoals:
                        if aborted():
                            report.failed.append(sg.id)
                            break
                        emit("subgoal_start", {"id": sg.id,
                                              "description": sg.description})
                        r2 = do_subgoal(
                            sg, self._world, self._actuator, self._llm,
                            marks=self._last_marks,
                            max_attempts=self._max_attempts,
                            should_abort=aborted,
                        )
                        if r2.ok:
                            sg.done = True
                            report.done.append(sg.id)
                            emit("subgoal_done", {"id": sg.id})
                        else:
                            report.failed.append(sg.id)
                            emit("subgoal_failed", {"id": sg.id,
                                                   "detail": r2.detail})
                    break
                report.failed.append(subgoal.id)

            emit("finished", {"report": _report_dict(report)})
            return report
        finally:
            self._world.stop()

    # -- internals ------------------------------------------------------

    def _observe_and_plan(self, goal: str, emit) -> Plan:
        frame = self._world.current_frame()
        annotated_png, marks = annotate(frame)
        self._last_marks = marks
        element_names = [
            el.name for el in frame.elements.values() if el.name
        ]
        try:
            plan = make_plan(goal, self._llm, annotated_png, element_names)
        except PlanError as exc:
            log.warning("planner failed: %s", exc)
            plan = Plan(goal=goal, subgoals=[])
        except Exception as exc:  # LLM transport/API failure ≠ crash the goal:
            log.warning("planner LLM call failed: %r", exc)  # degrade like
            plan = Plan(goal=goal, subgoals=[])  # PlanError — no plan, keep going
        emit("plan", {"goal": goal,
                      "subgoals": [s.description for s in plan.subgoals]})
        return plan


def _report_dict(report: RunReport) -> dict[str, Any]:
    return {"goal": report.goal, "done": report.done,
            "failed": report.failed, "ok": report.ok,
            "replanned": report.replanned}
