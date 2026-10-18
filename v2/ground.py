"""Grounding: resolve a planner's intent to a concrete action target.

The model never emits coordinates. It returns mark numbers (from observe.py)
or text hints; this module turns those into Targets with screen-px rects,
preferring exact UIA elements over vision fallbacks.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, TYPE_CHECKING

from .world import Element, normalized_control_type

if TYPE_CHECKING:
    from .world import WorldModel


@dataclass(frozen=True)
class Target:
    element_id: str | None
    rect: tuple[int, int, int, int]  # screen px: x, y, w, h
    source: str  # "mark" | "uia-direct" | "vision-fallback"
    confidence: float


# Control types we consider directly actuable via UIA patterns.
_ACTIONABLE_TYPES = {
    "button",
    "hyperlink",
    "link",
    "menuitem",
    "checkbox",
    "radiobutton",
    "combobox",
    "tabitem",
    "listitem",
    "treeitem",
}

# Control types that accept text.
_TYPABLE_TYPES = {
    "edit",
    "textbox",
    "document",
    "searchbox",
}


def resolve_mark(
    marks: list[Any], n: int, world: WorldModel
) -> Target | None:
    """Turn a 1-based mark number into a Target via the world model."""
    for mark in marks:
        if mark.n == n:
            el = world.element(mark.element_id)
            if el is None:
                return None
            return Target(
                element_id=el.id,
                rect=el.rect,
                source="mark",
                confidence=1.0 if el.visible and el.enabled else 0.5,
            )
    return None


def find_by_text(world: WorldModel, text: str) -> Target | None:
    """Find the best visible, enabled element whose name contains `text`.

    Case-insensitive substring match, preferring exact matches, then
    actionable types, then smallest rect (most specific).
    """
    needle = (text or "").strip().lower()
    if not needle:
        return None
    frame = world.current_frame()
    candidates: list[tuple[int, int, Element]] = []
    for el in frame.elements.values():
        if not el.visible or not el.enabled:
            continue
        name = (el.name or "").lower()
        if needle not in name:
            continue
        exact = 0 if name == needle else 1
        actionable = (
            0
            if normalized_control_type(el.control_type)
            in (_ACTIONABLE_TYPES | _TYPABLE_TYPES)
            else 1
        )
        area = el.rect[2] * el.rect[3]
        candidates.append((exact, actionable, area, el))
    if not candidates:
        return None
    candidates.sort(key=lambda c: (c[0], c[1], c[2]))
    best = candidates[0][3]
    return Target(
        element_id=best.id,
        rect=best.rect,
        source="uia-direct",
        confidence=0.9 if candidates[0][0] == 0 else 0.7,
    )


# Signature: (region_png_bytes, instruction) -> (x, y, w, h) in region px, or None.
VisionGrounder = Callable[[bytes, str], tuple[int, int, int, int] | None]


def vision_fallback(
    region_png: bytes,
    instruction: str,
    grounder: VisionGrounder | None,
    region_origin: tuple[int, int] = (0, 0),
) -> Target | None:
    """Last-resort grounding via a learned vision model.

    `grounder` is pluggable (OS-Atlas/UGround class). Returns None when no
    grounder is configured — the executor then fails the subgoal cleanly
    instead of guessing coordinates.
    """
    if grounder is None:
        return None
    try:
        box = grounder(region_png, instruction)
    except Exception:
        return None
    if not box:
        return None
    x, y, w, h = (int(v) for v in box)
    if w <= 0 or h <= 0:
        return None
    ox, oy = region_origin
    return Target(
        element_id=None,
        rect=(ox + x, oy + y, w, h),
        source="vision-fallback",
        confidence=0.6,
    )


def center(rect: tuple[int, int, int, int]) -> tuple[int, int]:
    x, y, w, h = rect
    return (x + w // 2, y + h // 2)


def is_typable(el: Element) -> bool:
    return normalized_control_type(el.control_type) in _TYPABLE_TYPES
