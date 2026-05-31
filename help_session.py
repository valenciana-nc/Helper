from __future__ import annotations

import logging
import json
import subprocess
import threading
import time
from dataclasses import replace
from threading import Event, Thread
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from PyQt6.QtCore import QObject, pyqtSignal

from control_inventory import (
    ControlCandidate,
    TargetResolution,
    collect_control_candidates,
    resolve_candidate_target,
    snap_candidate_target,
)
from history import HistoryManager
from rect_snap import SnapResult, snap_to_control
from screen import capture_active_monitor
from target_quality import TargetQuality, evaluate_target_quality

if TYPE_CHECKING:
    from agent import HelplerAgent, LiveHelpDecision
    from computer_control import ComputerController
    from screen import Capture

CandidateProvider = Callable[["Capture"], list[ControlCandidate]]
CaptureProvider = Callable[[], "Capture"]
Snapper = Callable[[tuple[int, int, int, int], str], SnapResult]

log = logging.getLogger("helper.help_session")

IDLE_RECHECK_SEC = 5.0
MAX_TURNS = 25
POST_ACTION_SETTLE_SEC = 0.6
POST_CLICK_SETTLE_SEC = 0.35
CLICK_HIT_MARGIN_PX = 24

OVERSIZED_AREA_THRESHOLD = 100_000
OVERSIZED_EDGE_THRESHOLD = 400


def looks_oversized(decision: "LiveHelpDecision") -> bool:
    """A target rect in normalized 0-1000 space that is panel-sized rather
    than a tight control bounding box. The model occasionally returns a
    whole-panel box when it can't localize precisely; better to show no
    rectangle than a wrong one.
    """
    w = decision.target_norm_width
    h = decision.target_norm_height
    return (w * h) > OVERSIZED_AREA_THRESHOLD or max(w, h) > OVERSIZED_EDGE_THRESHOLD


def build_target_diagnostic(
    *,
    decision: "LiveHelpDecision",
    capture: "Capture",
    candidates: list[ControlCandidate],
    target: TargetResolution,
    quality: TargetQuality | None = None,
    overlay_rect: tuple[int, int, int, int] | None = None,
    rejected_reason: str = "",
) -> dict[str, Any]:
    model_rect = decision.screen_rect(capture) if decision.has_target_rect else None
    return {
        "capture": {
            "width": capture.width,
            "height": capture.height,
            "monitor_left": capture.monitor_left,
            "monitor_top": capture.monitor_top,
            "scale": capture.scale,
        },
        "model": {
            "kind": decision.kind,
            "instruction": decision.instruction,
            "expected_change": decision.expected_change,
            "target_id": decision.target_id,
            "target_norm": {
                "x": decision.target_norm_x,
                "y": decision.target_norm_y,
                "width": decision.target_norm_width,
                "height": decision.target_norm_height,
            },
            "screen_rect": model_rect,
        },
        "resolution": {
            "rect": target.rect,
            "confidence": round(float(target.confidence), 4),
            "source": target.source,
            "matched_text": target.matched_text,
            "target_id": target.target_id,
            "rejected_reason": target.rejected_reason,
        },
        "quality": _quality_payload(quality),
        "overlay": {
            "emitted": overlay_rect is not None,
            "rect": overlay_rect,
            "rejected_reason": rejected_reason or target.rejected_reason,
        },
        "candidates": [
            {
                "id": candidate.id,
                "text": candidate.text,
                "control_type": candidate.control_type,
                "rect": candidate.rect,
                "automation_id": candidate.automation_id,
            }
            for candidate in candidates[:20]
        ],
        "candidate_count": len(candidates),
    }


def _quality_payload(quality: TargetQuality | None) -> dict[str, Any] | None:
    if quality is None:
        return None
    return {
        "accepted": quality.accepted,
        "reason": quality.reason,
        "visible_fraction": round(float(quality.visible_fraction), 4),
        "visual_activity": round(float(quality.visual_activity), 4),
    }


def resolve_help_target(
    decision: "LiveHelpDecision",
    capture: "Capture",
    candidates: list[ControlCandidate],
    *,
    snapper: Snapper = snap_to_control,
    clip_to_capture: bool = True,
) -> TargetResolution:
    model_rect = decision.screen_rect(capture) if decision.has_target_rect else None

    target = resolve_candidate_target(
        target_id=decision.target_id,
        instruction=decision.instruction,
        candidates=candidates,
        model_rect=model_rect,
    )
    if target is not None and not target.rejected_reason:
        return _maybe_clip_resolution_to_capture(target, capture, clip_to_capture)
    if target is not None:
        log.info(
            "Ignoring invalid target_id=%s for instruction=%r: %s",
            decision.target_id,
            decision.instruction,
            target.rejected_reason,
        )

    if decision.target_id:
        if (
            target is not None
            and target.rejected_reason == "unknown target_id"
            and model_rect is None
        ):
            return target
        text_target = resolve_candidate_target(
            target_id="",
            instruction=decision.instruction,
            candidates=candidates,
            model_rect=model_rect,
        )
        if text_target is not None and not text_target.rejected_reason:
            return _maybe_clip_resolution_to_capture(text_target, capture, clip_to_capture)

        if model_rect is not None:
            candidate_snap = snap_candidate_target(
                instruction=decision.instruction,
                candidates=candidates,
                model_rect=model_rect,
            )
            if candidate_snap is not None and not candidate_snap.rejected_reason:
                return _maybe_clip_resolution_to_capture(candidate_snap, capture, clip_to_capture)
            if candidate_snap is not None:
                return candidate_snap

        if target is not None and target.rejected_reason:
            return target

    if model_rect is None:
        return TargetResolution(
            rect=(0, 0, 0, 0),
            confidence=0.0,
            source="none",
            rejected_reason="no resolvable target",
        )

    candidate_snap = snap_candidate_target(
        instruction=decision.instruction,
        candidates=candidates,
        model_rect=model_rect,
    )
    if candidate_snap is not None and not candidate_snap.rejected_reason:
        return _maybe_clip_resolution_to_capture(candidate_snap, capture, clip_to_capture)
    if candidate_snap is not None:
        return candidate_snap

    try:
        snap = snapper(model_rect, decision.instruction)
    except Exception:
        log.exception("snap_to_control raised")
        snap = SnapResult(rect=model_rect, confidence=0.0, source="model")

    if snap.source == "uia":
        return _maybe_clip_resolution_to_capture(TargetResolution(
            rect=snap.rect,
            confidence=snap.confidence,
            source="snap",
            matched_text=snap.matched_text,
        ), capture, clip_to_capture)

    if looks_oversized(decision):
        return TargetResolution(
            rect=model_rect,
            confidence=snap.confidence,
            source="model",
            matched_text=snap.matched_text,
            rejected_reason="oversized target",
        )

    return _maybe_clip_resolution_to_capture(TargetResolution(
        rect=model_rect,
        confidence=snap.confidence,
        source="model",
        matched_text=snap.matched_text,
    ), capture, clip_to_capture)


def _maybe_clip_resolution_to_capture(
    target: TargetResolution,
    capture: "Capture",
    clip_to_capture: bool,
) -> TargetResolution:
    if not clip_to_capture:
        return target
    return clip_resolution_to_capture(target, capture)


def clip_resolution_to_capture(
    target: TargetResolution,
    capture: "Capture",
) -> TargetResolution:
    clipped = _clip_rect_to_capture(target.rect, capture)
    if clipped is None:
        return replace(target, rejected_reason="target outside capture")
    if clipped == target.rect:
        return target
    return replace(target, rect=clipped)


def _clip_rect_to_capture(
    rect: tuple[int, int, int, int],
    capture: "Capture",
) -> tuple[int, int, int, int] | None:
    scale = max(capture.scale, 0.001)
    bounds = (
        capture.monitor_left,
        capture.monitor_top,
        int(capture.width / scale),
        int(capture.height / scale),
    )
    return _intersection_rect(rect, bounds)


def _intersection_rect(
    a: tuple[int, int, int, int],
    b: tuple[int, int, int, int],
) -> tuple[int, int, int, int] | None:
    ax1, ay1, aw, ah = a
    bx1, by1, bw, bh = b
    ax2, ay2 = ax1 + aw, ay1 + ah
    bx2, by2 = bx1 + bw, by1 + bh
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    if ix2 <= ix1 or iy2 <= iy1:
        return None
    return (ix1, iy1, ix2 - ix1, iy2 - iy1)


class HelpSession(QObject):
    ghost_clear = pyqtSignal()
    highlight_show = pyqtSignal(int, int, int, int, str)
    highlight_clear = pyqtSignal()
    chat_message = pyqtSignal(str)
    chat_status = pyqtSignal(str)
    finished = pyqtSignal(str)
    failed = pyqtSignal(str)
    step_skipped = pyqtSignal(str)
    target_diagnostic = pyqtSignal(dict)

    def __init__(
        self,
        agent: "HelplerAgent",
        controller: "ComputerController",
        *,
        parent: QObject | None = None,
        capture_provider: CaptureProvider = capture_active_monitor,
        candidate_provider: CandidateProvider = collect_control_candidates,
        snapper: Snapper = snap_to_control,
    ) -> None:
        super().__init__(parent)
        self._agent = agent
        self._controller = controller
        self._capture_provider = capture_provider
        self._candidate_provider = candidate_provider
        self._snapper = snapper
        self._thread: Thread | None = None
        self._cancelled = Event()
        self._click_inside_event = Event()
        self._check_now_event = Event()
        self._active_rect: tuple[int, int, int, int] | None = None
        self._rect_lock = threading.Lock()

    def cancel(self) -> None:
        self._cancelled.set()
        self._click_inside_event.set()
        self._check_now_event.set()
        thread = self._thread
        if (
            thread is not None
            and thread.is_alive()
            and thread is not threading.current_thread()
        ):
            thread.join(timeout=0.1)

    def notify_user_click(self, screen_x: int, screen_y: int) -> None:
        """Called from the global mouse listener whenever the user clicks.

        Any click forces an immediate re-evaluation. A click inside the active
        rect (expanded by CLICK_HIT_MARGIN_PX) is recorded so the next outcome
        note can tell the model "the user followed the suggestion"; clicks
        outside are recorded as "the user clicked elsewhere".
        """
        with self._rect_lock:
            rect = self._active_rect
        if rect is None:
            self._check_now_event.set()
            return
        rx, ry, rw, rh = rect
        margin = CLICK_HIT_MARGIN_PX
        inside = (
            (rx - margin) <= screen_x < (rx + rw + margin)
            and (ry - margin) <= screen_y < (ry + rh + margin)
        )
        if inside:
            self._click_inside_event.set()
        else:
            self._check_now_event.set()

    def start(self, message: str) -> None:
        self.cancel()
        self._cancelled = Event()
        self._click_inside_event = Event()
        self._check_now_event = Event()
        self._thread = Thread(target=self._run, args=(message,), daemon=True)
        self._thread.start()

    def _run(self, message: str) -> None:
        try:
            self._run_walkthrough(message)
        except Exception as exc:
            if self._thread is not threading.current_thread():
                return
            log.exception("Help session crashed")
            self.failed.emit(f"Helper walkthrough failed: {exc}")

    def _run_walkthrough(self, message: str) -> None:
        if self._aborted():
            return

        history = HistoryManager()
        outcome_note = (message or "").strip() or "Help me with what's on my screen."

        for _ in range(MAX_TURNS):
            if self._aborted():
                return

            self.chat_status.emit("Looking at your screen...")
            self._clear_overlays()
            try:
                capture = self._capture_provider()
            except Exception as exc:
                log.exception("Screenshot failed")
                self.failed.emit(f"Couldn't capture the screen: {exc}")
                return

            candidates = self._candidate_provider(capture)
            history.add_user_turn(text=outcome_note, screenshot=capture)

            try:
                decision = self._agent.plan_next_step(
                    history,
                    control_candidates=candidates,
                    capture=capture,
                )
            except Exception as exc:
                log.exception("plan_next_step failed")
                self.failed.emit(f"Helper couldn't decide a step: {exc}")
                return

            if self._aborted():
                return

            history.add_assistant_turn(decision.history_text)

            if decision.helper_action is not None:
                self._execute_helper_action(decision.helper_action)
                if self._sleep_with_cancel(POST_ACTION_SETTLE_SEC):
                    return
                if decision.kind == "done":
                    self._end_walkthrough(decision.message or "Walkthrough complete.")
                    return
                if decision.kind == "narrate" and decision.message:
                    self.chat_message.emit(decision.message)
                outcome_note = self._outcome_after_helper_action(decision.helper_action)
                self._clear_overlays()
                continue

            if decision.kind == "done":
                self._end_walkthrough(decision.message or "Walkthrough complete.")
                return

            if decision.kind == "narrate":
                self._clear_overlays()
                message_text = decision.message or "Take a look at the screen."
                self.chat_message.emit(message_text)
                self.chat_status.emit(message_text)
                wait_outcome = self._wait_for_progress(rect=None)
                if wait_outcome == "cancelled":
                    return
                outcome_note = self._outcome_after_narrate(wait_outcome)
                continue

            # decision.kind == "step"
            target = resolve_help_target(
                decision,
                capture,
                candidates,
                snapper=self._snapper,
                clip_to_capture=False,
            )
            if target.rejected_reason:
                self._emit_target_diagnostic(
                    build_target_diagnostic(
                        decision=decision,
                        capture=capture,
                        candidates=candidates,
                        target=target,
                        rejected_reason=target.rejected_reason,
                    )
                )
                log.info(
                    "Step downgraded to narrate (%s, model rect %dx%d normalized): %s",
                    target.rejected_reason,
                    decision.target_norm_width,
                    decision.target_norm_height,
                    decision.instruction,
                )
                self._clear_overlays()
                msg = (decision.instruction or "").strip() or "Take a look at the screen."
                self.chat_message.emit(msg)
                self.chat_status.emit(msg)
                wait_outcome = self._wait_for_progress(rect=None)
                if wait_outcome == "cancelled":
                    return
                outcome_note = self._outcome_after_downgrade(
                    decision,
                    target.rejected_reason,
                )
                continue

            log.info(
                "Help target resolved: source=%s target_id=%s confidence=%.2f text=%r rect=%s instruction=%r",
                target.source,
                target.target_id,
                target.confidence,
                target.matched_text,
                target.rect,
                decision.instruction,
            )
            final_rect = target.rect
            quality = evaluate_target_quality(
                capture=capture,
                rect=final_rect,
                source=target.source,
                confidence=target.confidence,
            )
            if not quality.accepted:
                self._emit_target_diagnostic(
                    build_target_diagnostic(
                        decision=decision,
                        capture=capture,
                        candidates=candidates,
                        target=target,
                        quality=quality,
                        rejected_reason=quality.reason,
                    )
                )
                log.info(
                    "Step downgraded by target quality gate (%s, visible=%.2f activity=%.3f): %s",
                    quality.reason,
                    quality.visible_fraction,
                    quality.visual_activity,
                    decision.instruction,
                )
                self._clear_overlays()
                msg = (decision.instruction or "").strip() or "Take a look at the screen."
                self.chat_message.emit(msg)
                self.chat_status.emit(msg)
                wait_outcome = self._wait_for_progress(rect=None)
                if wait_outcome == "cancelled":
                    return
                outcome_note = self._outcome_after_quality_rejection(
                    decision,
                    quality.reason,
                )
                continue
            display_target = clip_resolution_to_capture(target, capture)
            if display_target.rejected_reason:
                self._emit_target_diagnostic(
                    build_target_diagnostic(
                        decision=decision,
                        capture=capture,
                        candidates=candidates,
                        target=display_target,
                        quality=quality,
                        rejected_reason=display_target.rejected_reason,
                    )
                )
                self._clear_overlays()
                msg = (decision.instruction or "").strip() or "Take a look at the screen."
                self.chat_message.emit(msg)
                self.chat_status.emit(msg)
                wait_outcome = self._wait_for_progress(rect=None)
                if wait_outcome == "cancelled":
                    return
                outcome_note = self._outcome_after_quality_rejection(
                    decision,
                    display_target.rejected_reason,
                )
                continue
            final_rect = display_target.rect
            self._emit_target_diagnostic(
                build_target_diagnostic(
                    decision=decision,
                    capture=capture,
                    candidates=candidates,
                    target=display_target,
                    quality=quality,
                    overlay_rect=final_rect,
                )
            )
            self._show_step(decision.instruction, final_rect)
            wait_outcome = self._wait_for_progress(rect=final_rect)
            self._set_active_rect(None)
            if wait_outcome == "cancelled":
                return
            self._clear_overlays()
            if wait_outcome == "clicked_inside" and self._sleep_with_cancel(POST_CLICK_SETTLE_SEC):
                return
            outcome_note = self._outcome_after_step(decision, wait_outcome)

        self._clear_overlays()
        self.chat_status.emit("")
        self.step_skipped.emit("Stopping the walkthrough — too many steps.")
        self.finished.emit("Stopped after too many steps.")

    def _show_step(self, instruction: str, rect: tuple[int, int, int, int]) -> None:
        x, y, width, height = rect
        self.highlight_show.emit(int(x), int(y), int(width), int(height), instruction)
        self.chat_status.emit(instruction)
        self._set_active_rect(rect)

    def _wait_for_progress(self, rect: tuple[int, int, int, int] | None) -> str:
        self._click_inside_event.clear()
        self._check_now_event.clear()
        if rect is None:
            self._set_active_rect(None)
        deadline = time.monotonic() + IDLE_RECHECK_SEC
        while True:
            if self._cancelled.is_set():
                return "cancelled"
            if self._click_inside_event.is_set():
                return "clicked_inside"
            if self._check_now_event.is_set():
                self._check_now_event.clear()
                return "clicked_elsewhere"
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return "idle"
            time.sleep(min(remaining, 0.04))

    @staticmethod
    def _outcome_after_step(decision: "LiveHelpDecision", outcome: str) -> str:
        instruction = decision.instruction.strip().rstrip(".")
        expected = decision.expected_change.strip().rstrip(".")
        expected_line = (
            f' Expected visible change: "{expected}".'
            if expected
            else ""
        )
        if outcome == "clicked_inside":
            return (
                f'You suggested: "{instruction}". The user clicked the '
                "highlighted target."
                f"{expected_line} Look at the current screen and continue only "
                "from what is actually visible."
            )
        if outcome == "clicked_elsewhere":
            return (
                f'You suggested: "{instruction}". The user clicked somewhere '
                "else on the screen, not the highlighted target. Look at the "
                "current screen and either re-target or narrate to re-orient them."
            )
        return (
            f'You suggested: "{instruction}". The user has not clicked yet. '
            f"{expected_line} Look at the current screen — it may have changed on its own — "
            "and decide the next step."
        )

    @staticmethod
    def _outcome_after_narrate(outcome: str) -> str:
        if outcome in {"clicked_inside", "clicked_elsewhere"}:
            return "The user clicked. Continue from the current screen."
        return "Continue from the current screen."

    @staticmethod
    def _outcome_after_downgrade(
        decision: "LiveHelpDecision",
        reason: str = "target was rejected",
    ) -> str:
        instruction = decision.instruction.strip().rstrip(".")
        return (
            f'You suggested: "{instruction}". The target was not drawn because '
            f"{reason}. Emit a precise target around the actual clickable "
            "element only if you can identify it confidently — otherwise use narrate."
        )

    @staticmethod
    def _outcome_after_quality_rejection(
        decision: "LiveHelpDecision",
        reason: str,
    ) -> str:
        instruction = decision.instruction.strip().rstrip(".")
        return (
            f'You suggested: "{instruction}". The resolved target was not drawn '
            f"because {reason}. Re-check the screenshot and either emit a "
            "precise visible target with stronger evidence or use narrate."
        )

    @staticmethod
    def _outcome_after_helper_action(action: dict[str, Any]) -> str:
        name = str(action.get("name") or "").lower()
        if name == "launch_app":
            target = action.get("display_name") or action.get("command") or "the app"
            return f"Helper just launched {target}. Continue from the new screen."
        if name == "open_url":
            target = action.get("url") or "the URL"
            return f"Helper just opened {target}. Continue from the new screen."
        if name == "key":
            return (
                f"Helper just pressed {action.get('keys')}. "
                "Continue from the new screen."
            )
        if name == "scroll":
            direction = action.get("direction") or "down"
            return f"Helper just scrolled {direction}. Continue from the new screen."
        return "Helper just ran a setup action. Continue from the new screen."

    def _end_walkthrough(self, message: str) -> None:
        self._clear_overlays()
        self.chat_message.emit(message)
        self.chat_status.emit("")
        self.finished.emit(message)

    def _clear_overlays(self) -> None:
        self.ghost_clear.emit()
        self.highlight_clear.emit()
        self._set_active_rect(None)

    def _set_active_rect(self, rect: tuple[int, int, int, int] | None) -> None:
        with self._rect_lock:
            self._active_rect = rect

    def _emit_target_diagnostic(self, payload: dict[str, Any]) -> None:
        try:
            log.info("Help target diagnostic: %s", json.dumps(payload, sort_keys=True))
        except Exception:
            log.info("Help target diagnostic: %r", payload)
        self.target_diagnostic.emit(payload)

    def _execute_helper_action(self, action: dict[str, Any]) -> None:
        name = str(action.get("name") or "").lower()
        try:
            if name == "launch_app":
                command = str(action.get("command") or "").strip()
                if command:
                    self._launch(command)
            elif name == "open_url":
                url = str(action.get("url") or "").strip()
                if url:
                    self._launch(url)
            elif name == "key":
                keys = str(action.get("keys") or "").strip()
                if keys:
                    self._controller.key(
                        keys,
                        description="Walkthrough setup.",
                        force_execute=True,
                    )
            elif name == "scroll":
                direction = str(action.get("direction") or "down").lower()
                dy = -700 if direction == "down" else 700
                self._controller.scroll(dy, description="Walkthrough scroll.")
        except Exception:
            log.exception("Helper action failed: %s", action)

    @staticmethod
    def _launch(target: str) -> None:
        try:
            subprocess.Popen(
                ["cmd", "/c", "start", "", target],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        except Exception:
            log.exception("Launch failed: %s", target)

    def _sleep_with_cancel(self, seconds: float) -> bool:
        if self._cancelled.wait(seconds):
            return True
        return self._aborted()

    def _aborted(self) -> bool:
        if self._thread is not threading.current_thread():
            return True
        if self._cancelled.is_set():
            return True
        abort = getattr(self._controller, "abort_controller", None)
        if abort is not None and abort.is_aborted():
            self._cancelled.set()
            return True
        return False
