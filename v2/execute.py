"""Executor: closed-loop subgoal execution.

do_subgoal() runs: ground → actuate → re-observe → verify → retry.
The planner is only re-invoked after max_attempts local retries fail.
Element patterns (UIA Invoke/SetValue) are preferred: no cursor movement,
DPI-proof. Synthetic input is the fallback for canvas/custom controls.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import Protocol, TYPE_CHECKING

from .ground import Target, center, find_by_text, is_typable, resolve_mark
from .observe import crop_png
from .plan import LlmPort, Subgoal
from .verify import verify_subgoal
from .world import Element

if TYPE_CHECKING:
    from .world import WorldModel

log = logging.getLogger("helper.v2.execute")


class Actuator(Protocol):
    def click_element(self, el: Element) -> bool: ...
    def set_value(self, el: Element, text: str) -> bool: ...
    def click_at(self, x: int, y: int) -> None: ...
    def type_text(self, text: str) -> None: ...
    def press_key(self, key: str) -> None: ...


@dataclass
class ActionResult:
    ok: bool
    detail: str
    attempts: int


def do_subgoal(
    subgoal: Subgoal,
    world: WorldModel,
    actuator: Actuator,
    llm: LlmPort,
    *,
    marks: list | None = None,
    max_attempts: int = 3,
    verify_wait_s: float = 0.3,
    should_abort=None,
) -> ActionResult:
    """Execute one subgoal with verify-and-retry. Never raises for grounding
    or actuation failures — returns ActionResult(ok=False) instead."""
    aborted = should_abort or (lambda: False)
    last_detail = "not attempted"
    for attempt in range(1, max_attempts + 1):
        if aborted():
            return ActionResult(ok=False, detail="aborted by user",
                                attempts=attempt)
        frame = world.current_frame()
        target = _ground(subgoal, frame, world, marks)
        if target is None:
            last_detail = "could not ground target"
            log.info("subgoal %s attempt %d: no target", subgoal.id, attempt)
            _settle(world, verify_wait_s)
            continue

        before_crop = _crop_frame(frame, target, crop_png)
        try:
            _actuate(subgoal, target, world, actuator)
        except Exception as exc:
            # Actuator failure (e.g. synthetic input unavailable headless)
            # fails the attempt, never the run: the docstring contract is
            # that do_subgoal never raises for actuation failures.
            log.warning("subgoal %s attempt %d: actuation raised: %r",
                        subgoal.id, attempt, exc)
            last_detail = f"actuation failed on attempt {attempt}: {exc}"
            _settle(world, verify_wait_s)
            continue
        after_frame = _settle(world, verify_wait_s)
        after_crop = _crop_frame(after_frame, target, crop_png)

        try:
            ok = verify_subgoal(subgoal, before_crop, after_crop, llm)
        except Exception as exc:  # verifier failure ≠ subgoal failure proof
            log.warning("verifier raised: %r", exc)
            ok = False
        if ok:
            return ActionResult(ok=True, detail="verified", attempts=attempt)
        last_detail = f"verification failed on attempt {attempt}"
        log.info("subgoal %s attempt %d: not verified, retrying", subgoal.id, attempt)

    return ActionResult(ok=False, detail=last_detail, attempts=max_attempts)


def _ground(
    subgoal: Subgoal, frame, world: WorldModel, marks: list | None
) -> Target | None:
    hint = (subgoal.target_hint or "").strip()
    # "mark 12" → resolve via world (stable element id, fresh rect).
    m = re.fullmatch(r"mark\s+(\d+)", hint, re.I)
    if m and marks:
        t = resolve_mark(marks, int(m.group(1)), world)
        if t is not None:
            return t
    # Fall back to UIA name search on the hint or description.
    for text in (hint, subgoal.description):
        t = find_by_text(world, _strip_verbs(text))
        if t is not None:
            return t
    return None


def _strip_verbs(text: str) -> str:
    """'Click the "Save" button' → 'Save'. Best-effort target noun extraction."""
    m = re.search(r'"([^"]+)"', text)
    if m:
        return m.group(1)
    m = re.search(
        r"\b(?:click|press|open|select|choose|tap)\s+(?:the\s+)?(.+?)(?:\s+button|\s+link|\s+menu)?$",
        text,
        re.I,
    )
    if m:
        return m.group(1).strip()
    return text


def _actuate(
    subgoal: Subgoal, target: Target, world: WorldModel, actuator: Actuator
) -> None:
    desc = subgoal.description.lower()
    el = world.element(target.element_id) if target.element_id else None

    # Text entry into a typable element: SetValue pattern, no keystrokes.
    if el is not None and is_typable(el):
        text = _extract_text(subgoal.description)
        if text and actuator.set_value(el, text):
            return
        # Fall through to click-then-type on pattern failure.

    if el is not None and ("click" in desc or "press" in desc or "open" in desc
                           or "select" in desc or "tap" in desc):
        if actuator.click_element(el):
            return
        # Pattern unsupported → synthetic fallback at rect center.

    if "type" in desc or "enter" in desc or "write" in desc:
        text = _extract_text(subgoal.description)
        if el is not None:
            actuator.click_element(el)  # focus; ignore result
        else:
            x, y = center(target.rect)
            actuator.click_at(x, y)
        if text:
            actuator.type_text(text)
        return

    if any(k in desc for k in ("key", "hotkey", "shortcut", "press esc")):
        key = _extract_key(subgoal.description)
        actuator.press_key(key)
        return

    # Default: click.
    if el is not None and actuator.click_element(el):
        return
    x, y = center(target.rect)
    actuator.click_at(x, y)


def _extract_text(description: str) -> str | None:
    m = re.search(r'"([^"]+)"', description)
    return m.group(1) if m else None


def _extract_key(description: str) -> str:
    m = re.search(r"\b(esc(?:ape)?|enter|tab|space|delete|backspace|f\d{1,2})\b",
                  description, re.I)
    return (m.group(1) if m else "enter").lower()


def _crop_frame(frame, target: Target, crop_png) -> bytes:
    x, y, w, h = target.rect
    pad = max(w, h) // 2 + 20
    try:
        return crop_png(frame.png, (x, y, w, h), pad=pad)
    except Exception:
        return frame.png  # worst case: verify on the full frame


def _settle(world: WorldModel, wait_s: float):
    """Give the UI a beat, then return the freshest frame."""
    changed = world.wait_for_change(wait_s)
    time.sleep(min(0.15, wait_s))
    return changed or world.current_frame()
