"""One observation-driven loop for help and active mode, with bounded recovery."""
from __future__ import annotations

import logging
import math
import time
import uuid
from collections import deque
from copy import deepcopy
from dataclasses import asdict, dataclass
from typing import Callable

from .model import receipt_context
from .perception import image_difference, target_rect
from .policy import confirmation_reason
from .ports import Confirmation, Driver, EventSink, Model
from .types import (
    Cancelled, Effect, Execution, Metrics, Outcome, ProtocolError, Receipt,
    RunReport, RunStatus, StaleObservation,
)
from .verification import evaluate

log = logging.getLogger(__name__)


__all__ = ["Limits", "Agent"]


@dataclass(frozen=True)
class Limits:
    """Per-run budgets: steps, model calls, wall-clock time, and invalid-decision tolerance."""
    max_steps: int = 40
    max_model_calls: int = 60
    max_seconds: float = 300
    help_wait_seconds: float = 90
    poll_seconds: float = 0.35
    max_invalid_decisions: int = 3

    def __post_init__(self):
        if min(self.max_steps, self.max_model_calls, self.max_invalid_decisions) < 1:
            raise ValueError("Run budgets must be positive")
        if min(self.max_seconds, self.help_wait_seconds, self.poll_seconds) <= 0:
            raise ValueError("Time budgets must be positive")
        if not all(math.isfinite(n) for n in (self.max_seconds, self.help_wait_seconds, self.poll_seconds)):
            raise ValueError("Time budgets must be finite")


@dataclass(frozen=True)
class _PendingTask:
    goal: str
    follow_ups: tuple[str, ...]
    question: str
    milestones: tuple[str, ...]
    progress: tuple[dict, ...]
    uncertain_effects: frozenset[str]

    def model_context(self) -> dict:
        return {"goal": self.goal, "follow_up_instructions": list(self.follow_ups), "question": self.question,
                "milestones": list(self.milestones), "prior_progress": deepcopy(list(self.progress))}


class Agent:
    """Observation-driven computer-use loop.

    Each iteration: observe → model decides one typed action → validate
    against the fresh observation → execute → verify locally. Help mode
    never dispatches input; active mode requires policy confirmation for
    destructive actions.
    """

    def __init__(self, *, model: Model, driver: Driver, limits: Limits | None = None,
                 confirm: Confirmation | None = None):
        self.model, self.driver = model, driver
        self.limits = limits or Limits()
        self.confirm = confirm
        self._conversation: deque[dict] = deque(maxlen=8)
        self._pending_task: _PendingTask | None = None

    def run_goal(self, goal: str, *, mode: str = "help", on_event: EventSink | None = None,
                 should_abort: Callable[[], bool] | None = None) -> RunReport:
        """Run one computer-use goal to completion, verification, or budget exhaustion.

        Mode "help" guides the user without dispatching input; mode "active"
        executes typed actions through the driver with policy confirmation.
        Returns a RunReport with status, message, receipts, and metrics.
        """
        if mode not in {"help", "active"}:
            raise ValueError("mode must be help or active")
        if not isinstance(goal, str) or not goal.strip():
            raise ValueError("goal cannot be empty")
        goal = goal.strip()
        user_request = base_goal = goal
        follow_ups: tuple[str, ...] = ()
        pending = self._pending_task if mode == "active" else None
        continuation_selected = False
        task_selected = False
        callback = on_event or (lambda *_: None)
        abort = should_abort or (lambda: False)
        start = time.monotonic()
        deadline = start + self.limits.max_seconds
        report = RunReport(goal, RunStatus.FAILED, "The task did not complete.")
        metrics: Metrics = report.metrics
        run_id = uuid.uuid4().hex
        context = {"mode": mode, "conversation": list(self._conversation), "milestones": [], "feedback": ""}
        if pending is not None:
            context["pending_task"] = pending.model_context()
        invalid = 0
        finish_attempts = 0
        uncertain_effects: set[str] = set()
        previous_effect = None
        no_progress = 0

        def emit(name: str, data: dict):
            try:
                callback(name, {"run_id": run_id, **data})
            except Exception:
                log.exception("Event sink failed; action will not be replayed")

        def checkpoint():
            if abort():
                raise Cancelled("Stopped by the user.")
            if time.monotonic() >= deadline:
                raise TimeoutError("The run reached its time budget.")

        def observe():
            checkpoint()
            obs = self.driver.observe()
            checkpoint()
            metrics.observations += 1
            if not obs.png or obs.width <= 1 or obs.height <= 1:
                raise RuntimeError("No usable screenshot; refusing to act on a placeholder.")
            return obs

        def call_model(fn, obs):
            checkpoint()
            if metrics.model_calls >= self.limits.max_model_calls:
                raise TimeoutError("The run reached its model-call budget.")
            metrics.model_calls += 1
            metrics.screenshot_bytes += len(obs.png)
            began = time.monotonic()
            try:
                result = fn()
            finally:
                metrics.model_seconds += time.monotonic() - began
            checkpoint()
            return result

        def pause(seconds):
            stop = min(deadline, time.monotonic() + seconds)
            while time.monotonic() < stop:
                checkpoint()
                time.sleep(min(0.04, max(0, stop - time.monotonic())))

        def handle_stale(exc, *, scene_changed: bool):
            """Record one stale-target rejection; the caller re-observes and retries.

            Help mode tells the model the scene changed and to re-ground;
            active mode reports the shorter form. Both paths share the
            metric, the event, and the re-observe-and-continue recovery.
            """
            metrics.stale_decisions += 1
            context["feedback"] = (
                f"Nothing executed. Scene changed: {exc}. Re-ground on this new observation."
                if scene_changed else f"Nothing executed: {exc}.")
            emit("decision_rejected", {"step": step, "status": "stale", "reason": "stale_target"})

        emit("started", {"mode": mode})
        try:
            obs = observe()
            for step in range(self.limits.max_steps):
                checkpoint()
                context["recent_actions"] = [receipt_context(r) for r in report.receipts[-8:]]
                try:
                    decision = call_model(lambda: self.model.decide(goal, obs, context), obs)
                    if decision.observation_id != obs.id:
                        raise ProtocolError("Decision refers to an expired observation.")
                    if decision.action:
                        target_rect(decision.action, obs)
                except ProtocolError as exc:
                    invalid += 1
                    log.warning("Rejected model decision %s/%s: %s", invalid, self.limits.max_invalid_decisions, exc)
                    context["feedback"] = (
                        f"Rejected without executing: {exc}. Return one DECISION envelope with top-level kind "
                        "act/observe/finish/answer/ask and observation_id. Put the operation inside action.kind; "
                        "target/description/expect belong inside action, never at the top level. Do not use type. "
                        "For non-act decisions set action:null. Use the complete examples in the system instructions."
                    )
                    emit("decision_rejected", {"step": step, "status": "invalid", "reason": "invalid_schema"})
                    if invalid >= self.limits.max_invalid_decisions:
                        report.message = "The model repeatedly returned invalid actions. Nothing further was executed."
                        break
                    continue
                invalid = 0
                # Resolve continuation in the existing decision call, not a separate intent/model pass.
                if pending is not None and not continuation_selected and (decision.continue_task or decision.kind != "ask"):
                    continuation_selected = True
                    context.pop("pending_task", None)
                    if decision.continue_task:
                        base_goal = pending.goal
                        follow_ups = (*pending.follow_ups, user_request)
                        goal = base_goal + "\nUser follow-up instructions:\n" + "\n".join(follow_ups)
                        report.goal = goal
                        context["milestones"] = list(pending.milestones)
                        context["prior_progress"] = deepcopy(list(pending.progress))
                        uncertain_effects.update(pending.uncertain_effects)
                        task_selected = True
                if decision.kind in {"act", "observe", "finish"}:
                    task_selected = True
                context["milestones"] = list(decision.milestones) or context["milestones"]
                if report.receipts:
                    last = report.receipts[-1]
                    if (last.outcome != Outcome.ACHIEVED and decision.evidence
                            and last.execution.effect != Effect.REJECTED):
                        last.outcome = decision.previous_outcome
                        last.evidence = decision.evidence
                metrics.verified_actions = len(report.done)
                if (decision.kind == "ask" and mode == "active" and metrics.clarification_reviews == 0
                        and step + 1 < self.limits.max_steps and metrics.model_calls < self.limits.max_model_calls):
                    # A proposed question is not automatically a user blocker. Review it once before exposing it.
                    metrics.clarification_reviews += 1
                    context["clarification_review"] = {"proposed_question": decision.message,
                                                       "actions_taken": metrics.actions}
                    context["feedback"] = (
                        "Before asking the user, reassess whether this is essential. Perform safe independent parts, "
                        "inspect/search the supplied words, and use reversible reasonable defaults. Do not ask the user "
                        "to do routine computer steps you can execute. If this message refers to pending_task, resume "
                        "that original task with continue_task:true instead of asking them to restate it. Only return "
                        "ask again for an essential unresolved choice, missing private input, or human-only blocker. "
                        "Do not invent secrets, recipients, amounts, or bypass confirmation. Explain the concrete blocker."
                    )
                    emit("decision_rejected", {"step": step, "status": "reviewing", "reason": "clarification_review"})
                    obs = observe()
                    continue
                if decision.kind != "ask":
                    context.pop("clarification_review", None)
                emit("decision", {"step": step, "kind": decision.kind, "message": decision.message,
                                  "milestones": context["milestones"]})
                if decision.kind == "ask":
                    report.status, report.message = RunStatus.NEEDS_INPUT, decision.message or "More information is needed."
                    break
                if decision.kind == "answer":
                    if report.receipts or task_selected:
                        context["feedback"] = "This task is in progress. Use finish and verify the full goal, or ask."
                        obs = observe()
                        continue
                    report.status, report.message = RunStatus.ANSWERED, decision.message
                    break
                if decision.kind == "finish":
                    # Always independently verify against a NEW full observation, never the old target crop.
                    obs = observe()
                    context["recent_actions"] = [receipt_context(r) for r in report.receipts[-8:]]
                    # The verifier checks the reply the user will receive, not only the screen.
                    context["proposed_reply"] = decision.message
                    try:
                        ok, evidence = call_model(lambda: self.model.verify_goal(goal, obs, context), obs)
                    finally:
                        context.pop("proposed_reply", None)
                    emit("goal_verification", {"status": "passed" if ok else "unproven", "message": evidence})
                    if ok:
                        report.status = RunStatus.COMPLETED
                        report.message = decision.message.strip() or evidence
                        break
                    finish_attempts += 1
                    if finish_attempts >= 2:
                        report.status, report.message = RunStatus.NEEDS_INPUT, "Completion could not be verified: " + evidence
                        break
                    context["feedback"] = "Goal verifier rejected completion: " + evidence
                    continue
                if decision.kind == "observe":
                    pause(decision.wait_ms / 1000)
                    obs = observe()
                    continue
                action = decision.action
                if action is None:
                    raise ProtocolError("Missing action")
                fingerprint = action.fingerprint(obs)
                if action.kind != "fill" and fingerprint in uncertain_effects:
                    report.status = RunStatus.NEEDS_INPUT
                    report.message = "An earlier matching action may already have taken effect. Check its result before retrying."
                    break
                if previous_effect and action.kind != "fill":
                    old_fingerprint, before = previous_effect
                    if (fingerprint == old_fingerprint and obs.signature == before.signature
                            and image_difference(before.png, obs.png) < 0.025):
                        no_progress += 1
                        context["feedback"] = "The same action on unchanged state was blocked. Observe, use another route, or ask."
                        metrics.rejected_actions += 1
                        if no_progress >= 2:
                            report.status, report.message = RunStatus.NEEDS_INPUT, "No observable progress; stopped repeating this action."
                            break
                        obs = observe()
                        continue
                already, _ = evaluate(action.expect, obs)
                preheld = already == Outcome.ACHIEVED
                # Only a fill whose exact target value is already present is a redundant step.
                # Other expectations (e.g. "Skip button exists") can be true before the action.
                element = obs.element(action.target.element_id) if action.target and action.target.element_id else None
                if (preheld and action.kind == "fill" and element is not None and not element.password
                        and element.value == action.text and any(
                            p.kind == "value_equals" and p.value == action.text
                            and p.name.casefold() == element.name.casefold() for p in action.expect)):
                    context["feedback"] = "That field already has the requested value. Continue with the remaining goal."
                    no_progress += 1
                    if no_progress >= 3:
                        report.status = RunStatus.NEEDS_INPUT
                        report.message = "I kept proposing a value that is already entered, so I stopped."
                        break
                    obs = observe()
                    continue
                no_progress = 0
                if mode == "help":
                    try:
                        self.driver.validate(action, obs)
                    except StaleObservation as exc:
                        handle_stale(exc, scene_changed=True)
                        obs = observe()
                        continue
                rect = target_rect(action, obs)
                def action_start():
                    emit("action_start", {"step": step, "kind": action.kind, "message": action.description,
                                          "rect": rect.as_list() if rect else None, "surface": obs.surface})

                before = obs
                if mode == "help":
                    action_start()
                    # The teaching path never calls execute(), even for keys or launch operations.
                    execution = Execution(Effect.GUIDED, "user", decision.message or action.description)
                    emit("guided", {"message": decision.message or action.description or action.kind})
                    until = min(deadline, time.monotonic() + self.limits.help_wait_seconds)
                    while time.monotonic() < until:
                        pause(self.limits.poll_seconds)
                        obs = observe()
                        achieved, _ = evaluate(action.expect, obs)
                        if ((achieved == Outcome.ACHIEVED and not preheld) or obs.signature != before.signature
                                or image_difference(before.png, obs.png) > 0.04):
                            break
                    else:
                        report.status, report.message = RunStatus.NEEDS_INPUT, "Follow the highlighted step, then ask me to continue."
                        break
                else:
                    reason = confirmation_reason(action, obs)
                    if reason and (self.confirm is None or not self.confirm(action, obs, reason)):
                        checkpoint()
                        report.status, report.message = RunStatus.NEEDS_INPUT, "This action needs your confirmation: " + reason
                        break
                    checkpoint()
                    try:
                        # Consent dialogs and a slow model can both change the scene.
                        self.driver.validate(action, obs)
                    except StaleObservation as exc:
                        handle_stale(exc, scene_changed=False)
                        obs = observe()
                        continue
                    except Exception as exc:
                        log.exception("Action validation failed before dispatch")
                        report.message = f"Could not validate the next action; no input was sent: {type(exc).__name__}."
                        emit("decision_rejected", {"step": step, "status": "failed", "reason": "validation_error"})
                        break
                    # Native validation can block; Escape during it must prevent dispatch.
                    checkpoint()
                    action_start()
                    checkpoint()
                    try:
                        execution = self.driver.execute(action, obs)
                    except StaleObservation as exc:
                        handle_stale(exc, scene_changed=False)
                        obs = observe()
                        continue
                    except Exception as exc:
                        # Only an exception during dispatch can have an UNKNOWN effect.
                        execution = Execution(Effect.UNKNOWN, "driver", type(exc).__name__)
                    metrics.actions += 1
                    metrics.routes[execution.route] = metrics.routes.get(execution.route, 0) + 1
                    if execution.effect == Effect.UNKNOWN:
                        uncertain_effects.add(fingerprint)
                    if execution.effect == Effect.REJECTED:
                        metrics.rejected_actions += 1
                    # Record dispatch BEFORE waiting or observing: cancellation/capture failure
                    # after input must retain the fact that a side effect may have occurred.
                    receipt = Receipt(f"a{len(report.receipts) + 1}", action, execution)
                    report.receipts.append(receipt)
                    pause(0.15)
                    obs = observe()
                if mode == "help":
                    receipt = Receipt(f"a{len(report.receipts) + 1}", action, execution)
                    report.receipts.append(receipt)
                if execution.effect not in {Effect.REJECTED, Effect.UNKNOWN}:
                    receipt.outcome, receipt.evidence = evaluate(action.expect, obs)
                    if preheld and receipt.outcome == Outcome.ACHIEVED:
                        receipt.outcome = Outcome.UNCERTAIN
                        receipt.evidence = ("The declared postconditions were already true before this action, "
                                            "so they cannot confirm its effect. Inspect the current state.")
                if execution.effect not in {Effect.REJECTED, Effect.UNKNOWN} and (
                        receipt.outcome == Outcome.ACHIEVED or obs.signature != before.signature
                        or image_difference(before.png, obs.png) > 0.025):
                    # Failed finish attempts are consecutive only while no new progress has been made.
                    finish_attempts = 0
                previous_effect = (fingerprint, before)
                metrics.verified_actions = len(report.done)
                context["feedback"] = execution.detail or receipt.evidence
                emit("action_result", {"step": step, "effect": execution.effect.value,
                                       "route": execution.route, "outcome": receipt.outcome.value})
            else:
                report.status, report.message = RunStatus.LIMITED, "The run reached its decision budget."
        except Cancelled as exc:
            report.status, report.message = RunStatus.CANCELLED, str(exc)
        except TimeoutError as exc:
            report.status, report.message = RunStatus.LIMITED, str(exc)
        except Exception as exc:
            log.exception("Computer-use run failed")
            detail = str(exc).strip() or type(exc).__name__
            report.status, report.message = RunStatus.FAILED, f"Task stopped: {detail[:600]}"
        finally:
            try:
                self.driver.close()
            except Exception:
                log.exception("Driver cleanup failed")
            metrics.elapsed_seconds = time.monotonic() - start
            emit("finished", {"status": report.status.value, **asdict(metrics)})
        if mode == "active":
            if report.status == RunStatus.NEEDS_INPUT or (
                    report.status in {RunStatus.FAILED, RunStatus.LIMITED} and (report.receipts or task_selected)):
                progress = context.get("prior_progress", []) + [receipt_context(r) for r in report.receipts]
                self._pending_task = _PendingTask(base_goal, follow_ups, report.message,
                                                  tuple(context["milestones"]), tuple(progress[-8:]),
                                                  frozenset(uncertain_effects))
            elif report.status in {RunStatus.COMPLETED, RunStatus.CANCELLED} or task_selected:
                self._pending_task = None
        self._conversation.append({"user": user_request, "status": report.status.value, "reply": report.message})
        return report
