"""Snap model-emitted screen rectangles to actual UIA controls.

Vision-only models emit bounding boxes that drift by tens of pixels — sometimes
landing on empty space. We use Windows UI Automation to find the real control
underneath the model's guess and snap to its exact bounds. Falls back to the
model's rect cleanly if UIA is unavailable, the search times out, or no
candidate scores high enough.
"""
from __future__ import annotations

import ctypes
import logging
import os
import re
import time
from ctypes import wintypes
from dataclasses import dataclass

log = logging.getLogger("helper.rect_snap")

DEFAULT_TIMEOUT_MS = 400
SEARCH_MARGIN_PX = 60
CONFIDENCE_FLOOR = 0.42
SEMANTIC_MISMATCH_CAP = 0.41
SEMANTIC_MISMATCH_IOU_FLOOR = 0.65

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
        "near", "beside", "nearby", "under", "above", "below",
        "top", "bottom", "left", "right", "upper", "lower",
        "middle", "center", "corner", "side",
    }
)

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_TOKEN_ALIASES = {
    "account": {"profile", "user"},
    "avatar": {"account", "profile", "user"},
    "cog": {"options", "preferences", "settings"},
    "dismiss": {"close"},
    "find": {"search"},
    "gear": {"options", "preferences", "settings"},
    "lens": {"find", "search"},
    "magnifier": {"find", "search"},
    "magnifying": {"find", "search"},
    "menu": {"more", "options"},
    "more": {"menu", "options"},
    "options": {"preferences", "settings"},
    "preferences": {"options", "settings"},
    "profile": {"account", "user"},
    "remove": {"delete"},
    "search": {"find"},
    "settings": {"options", "preferences"},
    "user": {"account", "profile"},
    "ellipsis": {"more", "options", "menu"},
    "close": {"dismiss"},
    "dot": {"more", "options", "menu"},
    "dots": {"more", "options", "menu"},
    "trash": {"delete", "remove"},
    "bin": {"delete", "remove"},
    "plus": {"add", "new", "create"},
}
_MAX_BFS_DEPTH = 8


@dataclass(frozen=True)
class SnapResult:
    rect: tuple[int, int, int, int]
    confidence: float
    source: str  # "uia" | "model"
    matched_text: str = ""
    rejected_reason: str = ""


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
    best_semantic_text = ""
    best_is_automation_only = False
    best_visible_score = 0.0
    best_visible_result: SnapResult | None = None
    own_process_result: SnapResult | None = None

    for control, rect, is_own_process in _iter_candidates(desktop, search_rect, deadline):
        ctype = _control_type(control)
        if ctype not in CLICKABLE_CONTROL_TYPES:
            continue
        if not _is_enabled(control) or not _is_visible(control):
            continue
        text = _control_text(control)
        visible_text = _control_visible_text(control)
        automation_id = _control_automation_id(control)
        semantic_text = visible_text or automation_id
        if is_own_process:
            if (
                own_process_result is None
                and _semantic_mismatch_targets_model_rect(rect, model_rect)
            ):
                own_process_result = SnapResult(
                    rect=rect,
                    confidence=0.0,
                    source="uia",
                    matched_text=text,
                    rejected_reason="own process target",
                )
            continue
        score = _score(
            rect=rect,
            semantic_text=semantic_text,
            ctype=ctype,
            model_rect=model_rect,
            model_center=model_center,
            instruction_tokens=instruction_tokens,
            diagonal=diagonal,
        )
        if (
            visible_text
            and _semantic_overlap(visible_text, instruction_tokens)
            and score > best_visible_score
        ):
            best_visible_score = score
            best_visible_result = SnapResult(
                rect=rect,
                confidence=score,
                source="uia",
                matched_text=text,
            )
        if score > best_score:
            best_score = score
            best_semantic_text = semantic_text
            best_is_automation_only = bool(not visible_text and automation_id)
            best_result = SnapResult(
                rect=rect, confidence=score, source="uia", matched_text=text
            )

    if (
        best_result is not None
        and best_is_automation_only
        and best_visible_result is not None
    ):
        if best_visible_score >= confidence_floor:
            return best_visible_result
        return SnapResult(
            rect=best_result.rect,
            confidence=best_score,
            source="uia",
            matched_text=best_result.matched_text,
            rejected_reason="automation-only target ambiguous",
        )

    if best_result is None or best_score < confidence_floor:
        if (
            best_result is not None
            and _semantic_mismatch(
                best_semantic_text,
                instruction_tokens,
            )
            and _semantic_mismatch_targets_model_rect(best_result.rect, model_rect)
        ):
            return SnapResult(
                rect=best_result.rect,
                confidence=best_score,
                source="uia",
                matched_text=best_result.matched_text,
                rejected_reason="candidate semantic mismatch",
            )
        if own_process_result is not None:
            return own_process_result
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
    ``(control, rect, is_own_process)`` tuples whose rect intersects
    ``search_rect``. Pruned by ``deadline`` and ``_MAX_BFS_DEPTH``.
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

        top_handle = _window_handle(top)
        is_own_process = (
            top_handle is not None and _is_own_process_window(top_handle)
        )
        queue: list[tuple[object, tuple[int, int, int, int]]] = [(top, top_rect)]
        depth = 0
        while queue and depth < _MAX_BFS_DEPTH:
            if time.monotonic() >= deadline:
                return
            next_queue: list[tuple[object, tuple[int, int, int, int]]] = []
            for control, rect in queue:
                yield control, rect, is_own_process
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


def _window_handle(control) -> int | None:
    for attr in ("handle", "hwnd"):
        try:
            value = getattr(control, attr)
        except Exception:
            continue
        if value:
            try:
                return int(value)
            except Exception:
                pass
    try:
        value = getattr(control.element_info, "handle", None)
    except Exception:
        return None
    if not value:
        return None
    try:
        return int(value)
    except Exception:
        return None


def _is_own_process_window(hwnd: int) -> bool:
    pid = wintypes.DWORD()
    try:
        ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    except Exception:
        return False
    return int(pid.value) == os.getpid()


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


def _control_visible_text(control) -> str:
    parts: list[str] = []
    try:
        text = (control.window_text() or "").strip()
        if text:
            parts.append(text)
    except Exception:
        pass
    try:
        name = (getattr(control.element_info, "name", "") or "").strip()
        if name and name not in parts:
            parts.append(name)
    except Exception:
        pass
    return " | ".join(parts)


def _control_automation_id(control) -> str:
    try:
        return (getattr(control.element_info, "automation_id", "") or "").strip()
    except Exception:
        return ""


def _is_enabled(control) -> bool:
    try:
        return bool(control.is_enabled())
    except Exception:
        try:
            value = getattr(control.element_info, "enabled", None)
        except Exception:
            value = None
        return True if value is None else bool(value)


def _is_visible(control) -> bool:
    try:
        return bool(control.is_visible())
    except Exception:
        try:
            value = getattr(control.element_info, "visible", None)
        except Exception:
            value = None
        return True if value is None else bool(value)


def _tokenize_instruction(instruction: str) -> set[str]:
    tokens = set(_TOKEN_RE.findall((instruction or "").lower()))
    filtered = {t for t in tokens if t not in _INSTRUCTION_STOPWORDS and len(t) > 1}
    return _expand_token_aliases(filtered)


def _tokenize_control(text: str) -> set[str]:
    return _expand_token_aliases(set(_TOKEN_RE.findall((text or "").lower())))


def _semantic_mismatch(text: str, instruction_tokens: set[str]) -> bool:
    control_tokens = _tokenize_control(text)
    return bool(instruction_tokens and control_tokens and not (instruction_tokens & control_tokens))


def _semantic_overlap(text: str, instruction_tokens: set[str]) -> bool:
    return bool(instruction_tokens and (_tokenize_control(text) & instruction_tokens))


def _semantic_mismatch_targets_model_rect(
    candidate_rect: tuple[int, int, int, int],
    model_rect: tuple[int, int, int, int],
) -> bool:
    if _iou(candidate_rect, model_rect) >= SEMANTIC_MISMATCH_IOU_FLOOR:
        return True
    return _center_inside(model_rect, candidate_rect)


def _expand_token_aliases(tokens: set[str]) -> set[str]:
    expanded = set(tokens)
    for token in tokens:
        expanded.update(_TOKEN_ALIASES.get(token, set()))
    return expanded


def _score(
    *,
    rect: tuple[int, int, int, int],
    semantic_text: str,
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

    control_tokens = _tokenize_control(semantic_text)
    overlap = instruction_tokens & control_tokens
    if instruction_tokens and control_tokens:
        text_score = len(overlap) / max(1, len(instruction_tokens))
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

    score = (
        SCORE_WEIGHT_IOU * iou
        + SCORE_WEIGHT_PROXIMITY * proximity
        + SCORE_WEIGHT_TEXT * text_score
        + SCORE_WEIGHT_TYPE * type_score
    )
    if instruction_tokens and control_tokens and not overlap:
        return min(score, SEMANTIC_MISMATCH_CAP)
    return score


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


def _center_inside(
    rect: tuple[int, int, int, int],
    bounds: tuple[int, int, int, int],
) -> bool:
    cx, cy = _center(rect)
    bx, by, bw, bh = bounds
    return bx <= cx < bx + bw and by <= cy < by + bh


def _center(rect: tuple[int, int, int, int]) -> tuple[int, int]:
    x, y, w, h = rect
    return (x + w // 2, y + h // 2)


def _diagonal_of(rect: tuple[int, int, int, int]) -> float:
    _, _, w, h = rect
    return (w * w + h * h) ** 0.5
