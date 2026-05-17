"""Snap model-emitted screen rectangles to actual UIA controls.

Vision-only models emit bounding boxes that drift by tens of pixels — sometimes
landing on empty space. We use Windows UI Automation to find the real control
underneath the model's guess and snap to its exact bounds. Falls back to the
model's rect cleanly if UIA is unavailable, the search times out, or no
candidate scores high enough.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass

log = logging.getLogger("helper.rect_snap")

DEFAULT_TIMEOUT_MS = 400
SEARCH_MARGIN_PX = 60
CONFIDENCE_FLOOR = 0.42

SCORE_WEIGHT_IOU = 0.40
SCORE_WEIGHT_PROXIMITY = 0.20
SCORE_WEIGHT_TEXT = 0.30
SCORE_WEIGHT_TYPE = 0.10

CLICKABLE_CONTROL_TYPES = frozenset(
    {
        "button",
        "menuitem",
        "tabitem",
        "hyperlink",
        "listitem",
        "treeitem",
        "edit",
        "combobox",
        "checkbox",
        "radiobutton",
        "splitbutton",
        "spinner",
        "headeritem",
    }
)

_INSTRUCTION_STOPWORDS = frozenset(
    {
        "click", "tap", "press", "select", "choose",
        "the", "on", "in", "to", "a", "an", "and", "or", "of",
        "this", "that", "your", "for", "now", "at", "is", "it", "be",
        "button", "icon", "link", "tab", "menu", "item", "field", "input",
        "open", "type", "enter", "into",
    }
)

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_MAX_BFS_DEPTH = 8


@dataclass(frozen=True)
class SnapResult:
    rect: tuple[int, int, int, int]
    confidence: float
    source: str  # "uia" | "model"
    matched_text: str = ""


def snap_to_control(
    model_rect: tuple[int, int, int, int],
    instruction: str,
    *,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
    margin_px: int = SEARCH_MARGIN_PX,
    confidence_floor: float = CONFIDENCE_FLOOR,
    desktop_factory=None,
) -> SnapResult:
    """Snap a model-emitted rect to the nearest matching UIA control.

    Returns ``SnapResult``; ``source="uia"`` if a candidate scored at or above
    ``confidence_floor``, otherwise ``source="model"`` with the original rect.
    The timeout caps the worst case: once elapsed we return the best so far
    (or the model rect if nothing was found yet).
    """
    deadline = time.monotonic() + max(timeout_ms, 0) / 1000.0
    factory = desktop_factory or _default_desktop
    try:
        desktop = factory()
    except Exception as exc:
        log.debug("UIA Desktop unavailable: %s", exc)
        return SnapResult(rect=model_rect, confidence=0.0, source="model")

    instruction_tokens = _tokenize_instruction(instruction)
    search_rect = _expand_rect(model_rect, margin_px)
    model_center = _center(model_rect)
    diagonal = max(1.0, _diagonal_of(search_rect))

    best_score = 0.0
    best_result: SnapResult | None = None

    for control, rect in _iter_candidates(desktop, search_rect, deadline):
        ctype = _control_type(control)
        if ctype not in CLICKABLE_CONTROL_TYPES:
            continue
        text = _control_text(control)
        score = _score(
            rect=rect,
            text=text,
            ctype=ctype,
            model_rect=model_rect,
            model_center=model_center,
            instruction_tokens=instruction_tokens,
            diagonal=diagonal,
        )
        if score > best_score:
            best_score = score
            best_result = SnapResult(
                rect=rect, confidence=score, source="uia", matched_text=text
            )

    if best_result is None or best_score < confidence_floor:
        log.debug(
            "Snap fallback: best=%.2f (floor=%.2f); using model rect",
            best_score,
            confidence_floor,
        )
        return SnapResult(
            rect=model_rect, confidence=best_score, source="model"
        )

    log.info(
        "Snap hit: %r score=%.2f rect=%s (model rect=%s)",
        best_result.matched_text,
        best_result.confidence,
        best_result.rect,
        model_rect,
    )
    return best_result


def _default_desktop():
    from pywinauto import Desktop

    return Desktop(backend="uia")


def _iter_candidates(desktop, search_rect, deadline):
    """BFS visible top-level windows and their descendants, yielding
    ``(control, rect)`` pairs whose rect intersects ``search_rect``. Pruned by
    ``deadline`` and ``_MAX_BFS_DEPTH``.
    """
    try:
        toplevels = list(desktop.windows(visible_only=True, enabled_only=True))
    except Exception as exc:
        log.debug("UIA windows() failed: %s", exc)
        return

    for top in toplevels:
        if time.monotonic() >= deadline:
            return
        top_rect = _element_rect(top)
        if top_rect is None or not _intersects(top_rect, search_rect):
            continue

        queue: list[tuple[object, tuple[int, int, int, int]]] = [(top, top_rect)]
        depth = 0
        while queue and depth < _MAX_BFS_DEPTH:
            if time.monotonic() >= deadline:
                return
            next_queue: list[tuple[object, tuple[int, int, int, int]]] = []
            for control, rect in queue:
                yield control, rect
                if time.monotonic() >= deadline:
                    return
                try:
                    children = control.children()
                except Exception:
                    continue
                for child in children:
                    crect = _element_rect(child)
                    if crect is None or not _intersects(crect, search_rect):
                        continue
                    next_queue.append((child, crect))
            queue = next_queue
            depth += 1


def _element_rect(control) -> tuple[int, int, int, int] | None:
    try:
        r = control.element_info.rectangle
    except Exception:
        try:
            r = control.rectangle()
        except Exception:
            return None
    try:
        left = int(r.left)
        top = int(r.top)
        width = int(r.right - r.left)
        height = int(r.bottom - r.top)
    except Exception:
        return None
    if width <= 0 or height <= 0:
        return None
    return (left, top, width, height)


def _control_type(control) -> str:
    try:
        return (control.element_info.control_type or "").strip().lower()
    except Exception:
        return ""


def _control_text(control) -> str:
    parts: list[str] = []
    try:
        text = (control.window_text() or "").strip()
        if text:
            parts.append(text)
    except Exception:
        pass
    try:
        info = control.element_info
        name = (getattr(info, "name", "") or "").strip()
        if name and name not in parts:
            parts.append(name)
        auto_id = (getattr(info, "automation_id", "") or "").strip()
        if auto_id and auto_id not in parts:
            parts.append(auto_id)
    except Exception:
        pass
    return " | ".join(parts)


def _tokenize_instruction(instruction: str) -> set[str]:
    tokens = set(_TOKEN_RE.findall((instruction or "").lower()))
    return {t for t in tokens if t not in _INSTRUCTION_STOPWORDS and len(t) > 1}


def _tokenize_control(text: str) -> set[str]:
    return set(_TOKEN_RE.findall((text or "").lower()))


def _score(
    *,
    rect: tuple[int, int, int, int],
    text: str,
    ctype: str,
    model_rect: tuple[int, int, int, int],
    model_center: tuple[int, int],
    instruction_tokens: set[str],
    diagonal: float,
) -> float:
    iou = _iou(rect, model_rect)
    cx, cy = _center(rect)
    mx, my = model_center
    distance = ((cx - mx) ** 2 + (cy - my) ** 2) ** 0.5
    proximity = max(0.0, 1.0 - min(1.0, distance / diagonal))

    control_tokens = _tokenize_control(text)
    if instruction_tokens and control_tokens:
        overlap = len(instruction_tokens & control_tokens)
        text_score = overlap / max(1, len(instruction_tokens))
    else:
        text_score = 0.0

    if ctype in {"button", "menuitem", "tabitem", "hyperlink", "splitbutton"}:
        type_score = 1.0
    elif ctype in {
        "listitem",
        "treeitem",
        "checkbox",
        "radiobutton",
        "edit",
        "combobox",
    }:
        type_score = 0.7
    else:
        type_score = 0.3

    return (
        SCORE_WEIGHT_IOU * iou
        + SCORE_WEIGHT_PROXIMITY * proximity
        + SCORE_WEIGHT_TEXT * text_score
        + SCORE_WEIGHT_TYPE * type_score
    )


def _iou(
    a: tuple[int, int, int, int], b: tuple[int, int, int, int]
) -> float:
    ax1, ay1, aw, ah = a
    bx1, by1, bw, bh = b
    ax2, ay2 = ax1 + aw, ay1 + ah
    bx2, by2 = bx1 + bw, by1 + bh
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw = max(0, ix2 - ix1)
    ih = max(0, iy2 - iy1)
    inter = iw * ih
    union = aw * ah + bw * bh - inter
    if union <= 0:
        return 0.0
    return inter / union


def _expand_rect(
    rect: tuple[int, int, int, int], margin: int
) -> tuple[int, int, int, int]:
    x, y, w, h = rect
    return (x - margin, y - margin, w + 2 * margin, h + 2 * margin)


def _intersects(
    a: tuple[int, int, int, int], b: tuple[int, int, int, int]
) -> bool:
    ax1, ay1, aw, ah = a
    bx1, by1, bw, bh = b
    return (
        ax1 < bx1 + bw
        and ax1 + aw > bx1
        and ay1 < by1 + bh
        and ay1 + ah > by1
    )


def _center(rect: tuple[int, int, int, int]) -> tuple[int, int]:
    x, y, w, h = rect
    return (x + w // 2, y + h // 2)


def _diagonal_of(rect: tuple[int, int, int, int]) -> float:
    _, _, w, h = rect
    return (w * w + h * h) ** 0.5
