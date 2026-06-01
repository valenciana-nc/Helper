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
FOREGROUND_RANK_BONUS = 0.10
FOREGROUND_SNAP_CONFLICT_GAP = 0.35
MIN_TOPMOST_SAMPLE_FRACTION = 0.50

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
        "slider",
    }
)

_INSTRUCTION_STOPWORDS = frozenset(
    {
        "click", "tap", "press", "select", "choose", "adjust", "drag",
        "slide", "move", "spin", "focus", "go",
        "the", "on", "in", "to", "a", "an", "and", "or", "of",
        "this", "that", "your", "for", "now", "at", "is", "it", "be",
        "here", "there", "bar", "highlighted", "shown", "indicated", "selected",
        "control", "controls",
        "area", "spot", "place", "location",
        "button", "icon", "link", "hyperlink", "split", "tab", "list", "tree", "menu", "item", "option",
        "header", "heading", "field", "input", "edit", "editable",
        "box", "text", "textbox", "textarea", "check", "checkbox",
        "toggle", "switch",
        "radio", "radiobutton", "splitbutton", "combo", "combobox", "dropdown",
        "slider", "spinner", "spinbox", "stepper",
        "drop", "down", "arrow", "caret", "chevron",
        "open", "type", "enter", "into",
        "near", "beside", "nearby", "under", "above", "below",
        "top", "bottom", "left", "right", "upper", "lower",
        "middle", "center", "corner", "side", "row", "column",
        "listitem", "treeitem", "menuitem", "tabitem", "headeritem",
    }
)

INPUT_CONTROL_TYPES = frozenset({"edit", "combobox", "spinner"})
EDIT_CONTROL_TYPES = frozenset({"edit"})
SLIDER_CONTROL_TYPES = frozenset({"slider"})
SPINNER_CONTROL_TYPES = frozenset({"spinner"})
TIGHT_ACTION_CONTROL_TYPES = frozenset(
    {
        "button",
        "menuitem",
        "tabitem",
        "hyperlink",
        "checkbox",
        "radiobutton",
        "splitbutton",
    }
)
_INPUT_INTENT_WORDS = frozenset({"field", "input", "text", "textbox", "textarea", "box"})
_ADDRESS_BAR_INTENT_WORDS = frozenset({"address", "url", "location", "omnibox"})
_BUTTON_INTENT_TYPES = frozenset({"button", "splitbutton"})
_ICON_INTENT_TYPES = TIGHT_ACTION_CONTROL_TYPES
_MENU_INTENT_TYPES = frozenset({"menuitem", "splitbutton"})
_DROPDOWN_INTENT_TYPES = frozenset({"combobox", "menuitem", "splitbutton"})
_OPTION_INTENT_TYPES = frozenset({"radiobutton", "listitem", "treeitem", "menuitem"})
_LIST_ITEM_INTENT_TYPES = frozenset({"listitem"})
_TREE_ITEM_INTENT_TYPES = frozenset({"treeitem"})
_NAV_ITEM_INTENT_TYPES = frozenset(
    {"button", "hyperlink", "listitem", "treeitem", "menuitem", "tabitem"}
)
_CONTEXT_LOCATION_WORDS = frozenset(
    {
        "card",
        "dialog",
        "drawer",
        "form",
        "grid",
        "modal",
        "page",
        "pane",
        "panel",
        "section",
        "table",
        "nav",
        "navigation",
        "popup",
        "sidebar",
        "toolbar",
        "view",
        "window",
    }
)
_DEICTIC_WORDS = frozenset({"this", "that", "here", "there", "shown", "indicated", "selected"})
_SWITCH_ACTION_CONTEXT_WORDS = frozenset(
    {
        "account",
        "app",
        "application",
        "branch",
        "context",
        "organization",
        "org",
        "profile",
        "project",
        "tab",
        "team",
        "user",
        "view",
        "window",
        "workspace",
    }
)
_TOGGLE_ACTION_CONTEXT_WORDS = _SWITCH_ACTION_CONTEXT_WORDS | frozenset(
    {
        "drawer",
        "menu",
        "nav",
        "navigation",
        "panel",
        "section",
        "sidebar",
        "toolbar",
    }
)

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_CAMEL_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_SEPARATOR_RE = re.compile(r"[_\-.]+")
_TOKEN_ALIASES = {
    "account": {"profile", "user"},
    "address": {"location", "url"},
    "avatar": {"account", "profile", "user"},
    "cog": {"options", "preferences", "settings"},
    "dismiss": {"close"},
    "find": {"search"},
    "gear": {"options", "preferences", "settings"},
    "lens": {"find", "search"},
    "location": {"address", "url"},
    "magnifier": {"find", "search"},
    "magnifying": {"find", "search"},
    "menu": {"more", "options"},
    "more": {"menu", "options"},
    "omnibox": {"address", "search", "url"},
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
    "url": {"address", "location"},
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
    foreground_handle_provider=None,
    topmost_handle_provider=None,
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
    control_intents = _instruction_control_intents(instruction)
    search_rect = _expand_rect(model_rect, margin_px)
    model_center = _center(model_rect)
    diagonal = max(1.0, _diagonal_of(search_rect))

    best_score = 0.0
    best_result: SnapResult | None = None
    best_semantic_text = ""
    best_ctype = ""
    best_window_rank = 0
    best_is_automation_only = False
    best_visible_score = 0.0
    best_visible_result: SnapResult | None = None
    ranked: list[tuple[float, SnapResult, str, str, int]] = []
    own_process_result: SnapResult | None = None
    occluded_result: SnapResult | None = None
    control_type_mismatch_result: SnapResult | None = None
    compound_target_result: SnapResult | None = None
    contained_control_intent_results: list[tuple[SnapResult, str]] = []
    control_intent_contexts: list[tuple[tuple[int, int, int, int], str]] = []
    foreground_handle = _safe_foreground_handle(
        foreground_handle_provider or _foreground_window_handle
    )
    topmost_provider = topmost_handle_provider
    if topmost_provider is None and desktop_factory is None:
        topmost_provider = _topmost_window_handle_at_point

    for (
        control,
        rect,
        is_own_process,
        window_rank,
        foreground_known,
        top_handle,
    ) in _iter_candidates(
        desktop,
        search_rect,
        deadline,
        foreground_handle,
    ):
        ctype = _control_type(control)
        if ctype not in CLICKABLE_CONTROL_TYPES:
            continue
        if not _is_enabled(control) or not _is_visible(control):
            continue
        text = _control_text(control)
        visible_text = _control_visible_text(control)
        automation_id = _control_automation_id(control)
        semantic_text = visible_text or automation_id
        if not _is_candidate_topmost(top_handle, rect, topmost_provider):
            if (
                occluded_result is None
                and _semantic_mismatch_targets_model_rect(rect, model_rect)
            ):
                occluded_result = SnapResult(
                    rect=rect,
                    confidence=0.0,
                    source="uia",
                    matched_text=text,
                    rejected_reason="occluded target",
                )
            continue
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
        if control_intents and ctype not in control_intents:
            if (
                instruction_tokens
                and semantic_text
                and _semantic_mismatch_targets_model_rect(rect, model_rect)
            ):
                control_intent_contexts.append((rect, semantic_text))
            if (
                control_type_mismatch_result is None
                and _semantic_mismatch_targets_model_rect(rect, model_rect)
            ):
                control_type_mismatch_result = SnapResult(
                    rect=rect,
                    confidence=0.0,
                    source="uia",
                    matched_text=text,
                    rejected_reason="control type mismatch",
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
        if foreground_known and window_rank == 0:
            score = min(1.0, score + FOREGROUND_RANK_BONUS)
        if ctype == "splitbutton" and _menu_segment_intent(control_intents):
            score = min(score, SEMANTIC_MISMATCH_CAP)
            if (
                compound_target_result is None
                and _semantic_mismatch_targets_model_rect(rect, model_rect)
            ):
                compound_target_result = SnapResult(
                    rect=rect,
                    confidence=score,
                    source="uia",
                    matched_text=text,
                    rejected_reason="compound target ambiguous",
                )
        result = SnapResult(
            rect=rect,
            confidence=score,
            source="uia",
            matched_text=text,
        )
        if (
            control_intents
            and _contains_rect(_expand_rect(model_rect, 4), rect)
            and not (ctype == "splitbutton" and _menu_segment_intent(control_intents))
            and not any(item.rect == result.rect for item, _text in contained_control_intent_results)
        ):
            contained_control_intent_results.append((result, semantic_text))
        ranked.append((score, result, semantic_text, ctype, window_rank))
        if (
            visible_text
            and _semantic_overlap(visible_text, instruction_tokens)
            and score > best_visible_score
        ):
            best_visible_score = score
            best_visible_result = result
        if score > best_score:
            best_score = score
            best_semantic_text = semantic_text
            best_ctype = ctype
            best_window_rank = window_rank
            best_is_automation_only = bool(not visible_text and automation_id)
            best_result = result

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

    foreground_conflict = _foreground_snap_conflict(
        ranked=ranked,
        best_result=best_result,
        best_semantic_text=best_semantic_text,
        best_ctype=best_ctype,
        best_window_rank=best_window_rank,
        instruction_tokens=instruction_tokens,
        confidence_floor=confidence_floor,
    )
    if foreground_conflict is not None:
        return foreground_conflict

    if best_result is None or best_score < confidence_floor:
        contained_result = _single_contained_control_intent_result(
            contained_control_intent_results,
            confidence_floor=confidence_floor,
            instruction_tokens=instruction_tokens,
            contexts=control_intent_contexts,
        )
        if contained_result is not None:
            return contained_result
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
        if occluded_result is not None:
            return occluded_result
        if control_type_mismatch_result is not None:
            return control_type_mismatch_result
        if compound_target_result is not None:
            return compound_target_result
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


def _iter_candidates(desktop, search_rect, deadline, foreground_handle=None):
    """BFS visible top-level windows and their descendants, yielding
    ``(control, rect, is_own_process, window_rank, foreground_known,
    top_handle)`` tuples whose rect intersects ``search_rect``. Pruned by
    ``deadline`` and ``_MAX_BFS_DEPTH``.
    """
    try:
        toplevels = list(desktop.windows(visible_only=True, enabled_only=True))
    except Exception as exc:
        log.debug("UIA windows() failed: %s", exc)
        return

    foreground_index = _foreground_window_index(toplevels, foreground_handle)
    foreground_known = foreground_index is not None
    indexed_toplevels = list(enumerate(toplevels))
    indexed_toplevels.sort(
        key=lambda item: _candidate_window_rank(item[0], foreground_index)
    )

    for window_index, top in indexed_toplevels:
        if time.monotonic() >= deadline:
            return
        top_rect = _element_rect(top)
        if top_rect is None or not _intersects(top_rect, search_rect):
            continue

        top_handle = _window_handle(top)
        window_rank = _candidate_window_rank(window_index, foreground_index)
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
                yield control, rect, is_own_process, window_rank, foreground_known, top_handle
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


def _safe_foreground_handle(provider) -> int | None:
    try:
        handle = provider()
    except Exception:
        return None
    if not handle:
        return None
    try:
        return int(handle)
    except Exception:
        return None


def _foreground_window_handle() -> int | None:
    try:
        return int(ctypes.windll.user32.GetForegroundWindow())
    except Exception:
        return None


def _topmost_window_handle_at_point(x: int, y: int) -> int | None:
    try:
        point = wintypes.POINT(int(x), int(y))
        hwnd = int(ctypes.windll.user32.WindowFromPoint(point))
    except Exception:
        return None
    return hwnd or None


def _foreground_window_index(windows: list[object], foreground_handle: int | None) -> int | None:
    if foreground_handle is None:
        return None
    for index, window in enumerate(windows):
        if _window_handle(window) == foreground_handle:
            return index
    return None


def _candidate_window_rank(window_index: int, foreground_index: int | None) -> int:
    if foreground_index is None:
        return 0
    if window_index == foreground_index:
        return 0
    return window_index + 1 if window_index < foreground_index else window_index


def _is_candidate_topmost(top_handle: int | None, rect, topmost_handle_provider) -> bool:
    if top_handle is None or topmost_handle_provider is None:
        return True
    expected_root = _root_window_handle(top_handle)
    matches = 0
    checked = 0
    center_checked = False
    center_matched = False
    for index, (x, y) in enumerate(_sample_points(rect)):
        actual = _safe_topmost_handle(topmost_handle_provider, x, y)
        if actual is None:
            continue
        checked += 1
        actual_root = _root_window_handle(actual)
        if actual_root == expected_root:
            matches += 1
            if index == 0:
                center_matched = True
        if index == 0:
            center_checked = True
    if checked == 0:
        return True
    if center_checked and not center_matched:
        return False
    return (matches / checked) >= MIN_TOPMOST_SAMPLE_FRACTION


def _safe_topmost_handle(provider, x: int, y: int) -> int | None:
    try:
        handle = provider(x, y)
    except Exception:
        return None
    if not handle:
        return None
    try:
        return int(handle)
    except Exception:
        return None


def _root_window_handle(hwnd: int) -> int:
    try:
        root = int(ctypes.windll.user32.GetAncestor(int(hwnd), 2))
    except Exception:
        root = 0
    return root or int(hwnd)


def _sample_points(rect: tuple[int, int, int, int]) -> tuple[tuple[int, int], ...]:
    x, y, width, height = rect
    right = x + max(1, width) - 1
    bottom = y + max(1, height) - 1
    return (
        (x + max(1, width) // 2, y + max(1, height) // 2),
        (x + min(6, max(0, width - 1)), y + min(6, max(0, height - 1))),
        (right - min(6, max(0, width - 1)), bottom - min(6, max(0, height - 1))),
    )


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
    tokens = _tokens_from_text(instruction)
    filtered = {t for t in tokens if t not in _INSTRUCTION_STOPWORDS and len(t) > 1}
    context_tokens = filtered & _CONTEXT_LOCATION_WORDS
    if context_tokens and (tokens & _DEICTIC_WORDS or filtered - context_tokens):
        filtered -= context_tokens
    return _expand_token_aliases(filtered)


def _instruction_control_intents(instruction: str) -> set[str]:
    raw_tokens = _tokens_from_text(instruction)
    intents: set[str] = set()
    checkbox_requested = "checkbox" in raw_tokens or (
        "check" in raw_tokens and "box" in raw_tokens
    )
    toggle_requested = (
        "toggle" in raw_tokens
        and not (raw_tokens & _TOGGLE_ACTION_CONTEXT_WORDS)
    )
    switch_requested = (
        "switch" in raw_tokens
        and not (raw_tokens & _SWITCH_ACTION_CONTEXT_WORDS)
    )
    radio_requested = "radiobutton" in raw_tokens or "radio" in raw_tokens
    dropdown_requested = "dropdown" in raw_tokens or (
        "drop" in raw_tokens and "down" in raw_tokens
    )
    split_button_requested = "splitbutton" in raw_tokens or (
        "split" in raw_tokens and "button" in raw_tokens
    )
    address_bar_requested = "omnibox" in raw_tokens or (
        "bar" in raw_tokens and bool(raw_tokens & _ADDRESS_BAR_INTENT_WORDS)
    )
    input_requested = bool(raw_tokens & _INPUT_INTENT_WORDS) or address_bar_requested
    if checkbox_requested or toggle_requested or switch_requested:
        intents.add("checkbox")
    if radio_requested:
        intents.add("radiobutton")
    if raw_tokens & {"edit", "editable"}:
        intents.update(EDIT_CONTROL_TYPES)
    if not checkbox_requested and input_requested:
        intents.update(INPUT_CONTROL_TYPES)
    if raw_tokens & {"combo", "combobox"}:
        intents.add("combobox")
    if dropdown_requested:
        intents.update(_DROPDOWN_INTENT_TYPES)
    if "slider" in raw_tokens:
        intents.update(SLIDER_CONTROL_TYPES)
    if raw_tokens & {"spinner", "spinbox", "stepper"} or (
        "spin" in raw_tokens and "box" in raw_tokens
    ):
        intents.update(SPINNER_CONTROL_TYPES)
    if split_button_requested:
        intents.add("splitbutton")
    if (
        not checkbox_requested
        and not radio_requested
        and not split_button_requested
        and "button" in raw_tokens
    ):
        intents.update(_BUTTON_INTENT_TYPES)
    if "icon" in raw_tokens:
        intents.update(_ICON_INTENT_TYPES)
    if raw_tokens & {"link", "hyperlink"}:
        intents.add("hyperlink")
    if "tab" in raw_tokens:
        intents.add("tabitem")
    if "tabitem" in raw_tokens:
        intents.add("tabitem")
    if "listitem" in raw_tokens or ("list" in raw_tokens and "item" in raw_tokens):
        intents.update(_LIST_ITEM_INTENT_TYPES)
    if "treeitem" in raw_tokens or ("tree" in raw_tokens and "item" in raw_tokens):
        intents.update(_TREE_ITEM_INTENT_TYPES)
    if "item" in raw_tokens and raw_tokens & {"drawer", "nav", "navigation", "sidebar"}:
        intents.update(_NAV_ITEM_INTENT_TYPES)
    if "option" in raw_tokens:
        intents.update(_OPTION_INTENT_TYPES)
    if raw_tokens & {"header", "heading"}:
        intents.add("headeritem")
    if "headeritem" in raw_tokens:
        intents.add("headeritem")
    if "menu" in raw_tokens:
        intents.update(_MENU_INTENT_TYPES)
    if "menuitem" in raw_tokens:
        intents.add("menuitem")
    return intents


def _menu_segment_intent(control_intents: set[str]) -> bool:
    return "menuitem" in control_intents


def _tokenize_control(text: str) -> set[str]:
    return _expand_token_aliases(_tokens_from_text(text))


def _tokens_from_text(text: str) -> set[str]:
    spaced = _CAMEL_RE.sub(" ", text or "")
    spaced = _SEPARATOR_RE.sub(" ", spaced)
    return set(_TOKEN_RE.findall(spaced.lower()))


def _semantic_mismatch(text: str, instruction_tokens: set[str]) -> bool:
    control_tokens = _tokenize_control(text)
    return bool(instruction_tokens and control_tokens and not (instruction_tokens & control_tokens))


def _semantic_overlap(text: str, instruction_tokens: set[str]) -> bool:
    return bool(instruction_tokens and (_tokenize_control(text) & instruction_tokens))


def _semantic_score(text: str, instruction_tokens: set[str]) -> float:
    control_tokens = _tokenize_control(text)
    if not instruction_tokens or not control_tokens:
        return 0.0
    return len(instruction_tokens & control_tokens) / max(1, len(instruction_tokens))


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
        "slider",
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


def _foreground_snap_conflict(
    *,
    ranked: list[tuple[float, SnapResult, str, str, int]],
    best_result: SnapResult | None,
    best_semantic_text: str,
    best_ctype: str,
    best_window_rank: int,
    instruction_tokens: set[str],
    confidence_floor: float,
) -> SnapResult | None:
    if best_result is None or best_window_rank == 0:
        return None

    foreground: tuple[float, SnapResult] | None = None
    for score, result, semantic_text, ctype, window_rank in ranked:
        if window_rank != 0:
            continue
        if score < confidence_floor:
            continue
        if not _same_snap_intent(
            best_semantic_text,
            best_ctype,
            semantic_text,
            ctype,
            instruction_tokens,
        ):
            continue
        if foreground is None or score > foreground[0]:
            foreground = (score, result)
    if foreground is None:
        return None
    if best_result.confidence - foreground[0] >= FOREGROUND_SNAP_CONFLICT_GAP:
        return None
    return SnapResult(
        rect=best_result.rect,
        confidence=best_result.confidence,
        source="uia",
        matched_text=best_result.matched_text,
        rejected_reason="foreground target ambiguous",
    )


def _same_snap_intent(
    first_text: str,
    first_ctype: str,
    second_text: str,
    second_ctype: str,
    instruction_tokens: set[str],
) -> bool:
    if not instruction_tokens:
        return first_ctype == second_ctype
    first_score = _semantic_score(first_text, instruction_tokens)
    second_score = _semantic_score(second_text, instruction_tokens)
    if first_score <= 0 and second_score <= 0:
        return first_ctype == second_ctype
    return second_score >= first_score - 0.08


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


def _contains_rect(
    outer: tuple[int, int, int, int],
    inner: tuple[int, int, int, int],
) -> bool:
    ox, oy, ow, oh = outer
    ix, iy, iw, ih = inner
    return ox <= ix and oy <= iy and ox + ow >= ix + iw and oy + oh >= iy + ih


def _single_contained_control_intent_result(
    results: list[tuple[SnapResult, str]],
    *,
    confidence_floor: float,
    instruction_tokens: set[str],
    contexts: list[tuple[tuple[int, int, int, int], str]],
) -> SnapResult | None:
    eligible: list[SnapResult] = []
    for result, semantic_text in results:
        if instruction_tokens and not _contained_control_intent_result_has_evidence(
            rect=result.rect,
            semantic_text=semantic_text,
            contexts=contexts,
            instruction_tokens=instruction_tokens,
        ):
            continue
        eligible.append(result)
    if len(eligible) != 1:
        return None
    result = eligible[0]
    return SnapResult(
        rect=result.rect,
        confidence=max(confidence_floor, result.confidence),
        source=result.source,
        matched_text=result.matched_text,
    )


def _contained_control_intent_result_has_evidence(
    *,
    rect: tuple[int, int, int, int],
    semantic_text: str,
    contexts: list[tuple[tuple[int, int, int, int], str]],
    instruction_tokens: set[str],
) -> bool:
    evidence_tokens = _tokenize_control(semantic_text)
    for context_rect, context_text in contexts:
        if _contains_rect(_expand_rect(context_rect, 4), rect):
            evidence_tokens.update(_tokenize_control(context_text))
    return _text_evidence_score(instruction_tokens, evidence_tokens) >= 0.35


def _text_evidence_score(
    instruction_tokens: set[str],
    candidate_tokens: set[str],
) -> float:
    if not instruction_tokens or not candidate_tokens:
        return 0.0
    overlap = instruction_tokens & candidate_tokens
    if not overlap:
        return 0.0
    coverage = len(overlap) / max(1, len(instruction_tokens))
    density = len(overlap) / max(1, len(candidate_tokens))
    return min(1.0, 0.75 * coverage + 0.25 * min(1.0, density * 3.0))


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
