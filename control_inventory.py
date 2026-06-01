"""Collect and resolve clickable Windows UI Automation controls for Help mode."""
from __future__ import annotations

import logging
import ctypes
import os
import re
import time
from dataclasses import dataclass
from ctypes import wintypes
from collections.abc import Callable
from typing import Any

from screen import Capture
from help_intents import (
    TIGHT_ACTION_CONTROL_TYPES,
    control_type_matches_intent as _control_type_matches_intent,
    expand_token_aliases as _expand_token_aliases,
    instruction_control_intents as _instruction_control_intents,
    menu_segment_intent as _menu_segment_intent,
    tokenize_control as _tokenize_control,
    tokenize_instruction as _tokenize_instruction,
    tokens_from_text as _tokens_from_text,
)

log = logging.getLogger("helper.control_inventory")

DEFAULT_TIMEOUT_MS = 500
MAX_CANDIDATES = 80
MAX_BFS_DEPTH = 8
TEXT_MATCH_FLOOR = 0.55
TEXT_MATCH_GAP = 0.08
VISIBLE_TEXT_MATCH_BONUS = 0.08
AUTOMATION_ONLY_MATCH_PENALTY = 0.08
TARGET_ID_TEXT_FLOOR = 0.35
TARGET_ID_GEOMETRY_FLOOR = 0.72
TARGET_ID_FOREGROUND_CONFLICT_GAP = 0.35
CANDIDATE_SNAP_FLOOR = 0.50
CANDIDATE_SNAP_MARGIN_PX = 60
CONTAINING_ROW_SNAP_CAP = CANDIDATE_SNAP_FLOOR - TEXT_MATCH_GAP - 0.02
MIN_VISIBLE_FRACTION = 0.20
UNLABELED_COMPETITOR_MARGIN_PX = 96
FOREGROUND_RANK_BONUS = 0.10
FOREGROUND_SNAP_CONFLICT_GAP = 0.35
MIN_TOPMOST_SAMPLE_FRACTION = 0.50
DISMISS_DIALOG_CONTEXT_WORDS = frozenset({"dialog", "modal", "popup"})
DISMISS_WINDOW_CONTEXT_WORDS = frozenset({"browser", "page", "tab", "window"})
TASKBAR_WINDOW_WORDS = frozenset({"taskbar"})
TASKBAR_APP_STATE_WORDS = frozenset({"pinned", "running"})
TASKBAR_APP_STATE_CONTEXT_WORDS = frozenset(
    {"pinned", "running", "window", "windows"}
)
TASKBAR_APP_GENERIC_REQUEST_WORDS = frozenset(
    {
        "app",
        "apps",
        "application",
        "applications",
        "button",
        "icon",
        "icons",
        "program",
        "programs",
    }
)
TASKBAR_APP_STATUS_CONTEXT_WORDS = frozenset(
    {"and", "backed", "backup", "personal", "sync", "synced", "up"}
)
TASKBAR_START_BUTTON_ALLOWED_TOKENS = frozenset({"start", "windows"})
TASKBAR_WIDGET_STATUS_IDENTITY_WORDS = frozenset({"weather", "widgets"})
TASKBAR_NETWORK_STATUS_IDENTITY_WORDS = frozenset(
    {"internet", "network", "starlink", "wifi", "wireless"}
)
TASKBAR_VOLUME_STATUS_IDENTITY_WORDS = frozenset(
    {"audio", "realtek", "sound", "speaker", "speakers", "volume"}
)
TASKBAR_POWER_STATUS_IDENTITY_WORDS = frozenset({"battery", "power"})
TASKBAR_CLOCK_STATUS_IDENTITY_WORDS = frozenset({"clock", "time"})
TASKBAR_SEARCH_STATUS_IDENTITY_WORDS = frozenset({"find", "search"})
TASKBAR_ONEDRIVE_STATUS_IDENTITY_WORDS = frozenset({"onedrive"})
TASKBAR_FILE_ACTION_WORDS = frozenset(
    {
        "attach",
        "attachment",
        "browse",
        "choose",
        "document",
        "documents",
        "file",
        "files",
        "paperclip",
        "select",
        "upload",
    }
)
TASKBAR_PIN_ACTION_WORDS = frozenset(
    {"pin", "pinned", "pushpin", "thumbtack", "unpin"}
)
TASKBAR_GENERIC_FILE_IDENTITY_WORDS = frozenset({"file", "files"})
TASKBAR_WINDOWS_SEARCH_TOKENS = frozenset({"windows_search"})
BROWSER_PROFILE_WINDOW_WORDS = frozenset({"browser", "chrome", "edge"})
BROWSER_APP_IDENTITY_WORDS = frozenset({"browser", "chrome", "edge", "google"})
BROWSER_PROFILE_ACTION_CONTEXT_WORDS = frozenset({"edit", "pencil"})
BROWSER_PROFILE_LABEL_HINT_WORDS = frozenset({"all"})
BROWSER_PROFILE_TOKENS = frozenset({"account", "avatar", "person", "profile", "user"})
BROWSER_PROFILE_MAX_EDGE = 64
BROWSER_PROFILE_MAX_ASPECT = 1.75
BROWSER_ADDRESS_BAR_ROLE_WORDS = frozenset(
    {"address", "bar", "location", "omnibox", "search", "url"}
)
BROWSER_ADDRESS_BAR_REQUEST_WORDS = frozenset(
    {"address", "find", "location", "omnibox", "search", "url"}
)
BROWSER_ABOUT_BLANK_TARGET_WORDS = frozenset({"blank", "tab", "tabitem"})
BROWSER_TAB_AUTH_ACTION_WORDS = frozenset({"log", "login", "sign", "signin"})
BROWSER_MENU_BUTTON_TOKENS = frozenset(
    {"browser", "chrome", "menu", "more", "options", "preferences", "settings"}
)
BROWSER_MENU_CONTROL_INTENTS = frozenset({"button", "menuitem", "splitbutton"})
BROWSER_MENU_REQUEST_WORDS = frozenset({"menu", "more", "options", "overflow"})
BROWSER_MENU_SPECIFIC_CONTEXT_WORDS = frozenset(
    {
        "account",
        "all",
        "avatar",
        "bookmark",
        "bookmarks",
        "download",
        "downloads",
        "file",
        "history",
        "person",
        "profile",
        "tab",
        "tabs",
        "user",
    }
)
BROWSER_HIDDEN_BOOKMARKS_WORDS = frozenset({"bookmarks", "hidden"})
BROWSER_HIDDEN_BOOKMARKS_GENERIC_MENU_WORDS = frozenset(
    {"menu", "more", "options", "preferences", "settings"}
)
BROWSER_NEW_TAB_WORDS = frozenset({"new_tab"})
BROWSER_BOOKMARK_ACTION_WORDS = frozenset({"bookmark", "favorite", "star"})
BROWSER_GROUP_STATE_WORDS = frozenset({"closed", "collapsed", "expanded", "open"})
BROWSER_GROUP_GENERIC_WORDS = frozenset({"closed", "collapsed", "expanded", "group", "open"})
DISCLOSURE_EXPAND_ACTION_WORDS = frozenset({"expand"})
DISCLOSURE_COLLAPSE_ACTION_WORDS = frozenset({"collapse"})
BROWSER_EXTENSION_ACCESS_CONTEXT_WORDS = frozenset({"access", "site"})
BROWSER_EXTENSION_ACCESS_LABEL_STOPWORDS = frozenset(
    {
        "access",
        "button",
        "control",
        "extension",
        "has",
        "open",
        "site",
        "this",
        "to",
        "wants",
    }
)
BROWSER_EXTENSION_ACCESS_INSTRUCTION_STOPWORDS = frozenset(
    {
        "access",
        "allow",
        "button",
        "click",
        "control",
        "enable",
        "extension",
        "give",
        "grant",
        "has",
        "open",
        "request",
        "site",
        "this",
        "to",
        "wants",
    }
)
SITE_INFORMATION_REQUEST_WORDS = frozenset(
    {"about", "details", "info", "information", "lock", "padlock", "site_info_lock"}
)
GMAIL_TAB_SERVICE_RE = re.compile(
    r"(?:^|[\s\-\|\u2013\u2014])gmail(?:$|[\s\-\|\u2013\u2014])",
    re.IGNORECASE,
)
GMAIL_TAB_REQUEST_WORDS = frozenset({"email", "envelope", "gmail", "inbox", "mail"})
MAIL_TAB_EXPLICIT_WORDS = frozenset({"email", "inbox", "mail", "recibidos"})
BROWSER_TAB_MEMORY_USAGE_RE = re.compile(
    r"(?:\s*[\-\|\u2013\u2014]\s*)?memory\s+usage\s*[-:]\s*\d+\s*mb\b.*$",
    re.IGNORECASE,
)
BROWSER_TAB_OWNER_ACCOUNT_RE = re.compile(
    r"\s*[\-\|\u2013\u2014]\s*[^|\u2013\u2014-]*@[^|\u2013\u2014-]*['\u2019]s\s+account(?=\s*[\-\|\u2013\u2014]|$)",
    re.IGNORECASE,
)
SETTINGS_REQUEST_WORDS = frozenset({"options", "preferences", "settings"})
UNNAMED_BOOKMARK_GENERIC_ROUTE_WORDS = SETTINGS_REQUEST_WORDS | frozenset(
    {
        "account",
        "acct",
        "add",
        "all",
        "ap",
        "app",
        "application",
        "asset",
        "base",
        "campaign",
        "client",
        "cloud",
        "com",
        "create",
        "credentials",
        "dashboard",
        "default",
        "dev",
        "for",
        "home",
        "http",
        "https",
        "house",
        "id",
        "launcher",
        "latest",
        "manage",
        "mbs",
        "medium",
        "nav",
        "new",
        "owned",
        "overview",
        "page",
        "person",
        "platform",
        "plus",
        "profile",
        "project",
        "ref",
        "org",
        "organization",
        "source",
        "utm",
        "user",
        "workspace",
        "workspaces",
    }
)
UNNAMED_BOOKMARK_ACTION_WORDS = frozenset({"bookmark", "favorite", "star"})
UNNAMED_BOOKMARK_RE = re.compile(r"^\s*Unnamed bookmark for https?://", re.IGNORECASE)
UNNAMED_BOOKMARK_DESTINATION_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "ap",
        "app",
        "application",
        "account",
        "acct",
        "add",
        "all",
        "asset",
        "base",
        "bookmark",
        "browser",
        "campaign",
        "chrome",
        "click",
        "client",
        "cloud",
        "com",
        "create",
        "credentials",
        "dashboard",
        "default",
        "dev",
        "favorite",
        "favourite",
        "edge",
        "for",
        "go",
        "home",
        "http",
        "https",
        "house",
        "id",
        "in",
        "launch",
        "launcher",
        "latest",
        "manage",
        "mbs",
        "medium",
        "nav",
        "new",
        "owned",
        "open",
        "option",
        "options",
        "org",
        "organization",
        "overview",
        "page",
        "person",
        "platform",
        "preference",
        "preferences",
        "profile",
        "project",
        "ref",
        "setting",
        "settings",
        "source",
        "star",
        "show",
        "site",
        "tab",
        "the",
        "to",
        "utm",
        "user",
        "web",
        "website",
        "window",
        "workspace",
        "workspaces",
    }
)

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
ROW_LIKE_CONTROL_TYPES = frozenset({"listitem", "treeitem", "edit", "combobox"})
COMPOSITE_ACTION_CONTROL_TYPES = frozenset({"splitbutton"})
ForegroundHandleProvider = Callable[[], int | None]
TopmostHandleProvider = Callable[[int, int], int | None]


@dataclass(frozen=True)
class ControlCandidate:
    id: str
    text: str
    control_type: str
    rect: tuple[int, int, int, int]
    automation_id: str = ""
    window_title: str = ""
    depth: int = 0
    window_rank: int = 0

    @property
    def descriptor(self) -> str:
        parts = [self.text.strip(), self.automation_id.strip()]
        return " ".join(part for part in parts if part).strip()


@dataclass(frozen=True)
class TargetResolution:
    rect: tuple[int, int, int, int]
    confidence: float
    source: str
    matched_text: str = ""
    target_id: str = ""
    rejected_reason: str = ""


def collect_control_candidates(
    capture: Capture,
    *,
    desktop_factory: Any = None,
    foreground_handle_provider: ForegroundHandleProvider | None = None,
    topmost_handle_provider: TopmostHandleProvider | None = None,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
    limit: int = MAX_CANDIDATES,
) -> list[ControlCandidate]:
    """Return visible clickable/focusable controls intersecting ``capture``."""
    deadline = time.monotonic() + max(timeout_ms, 0) / 1000.0
    factory = desktop_factory or _default_desktop
    try:
        desktop = factory()
    except Exception as exc:
        log.debug("UIA Desktop unavailable for inventory: %s", exc)
        return []

    capture_rect = _capture_screen_rect(capture)
    raw: list[ControlCandidate] = []
    try:
        windows = list(desktop.windows(visible_only=True, enabled_only=True))
    except Exception as exc:
        log.debug("UIA windows() failed for inventory: %s", exc)
        return []

    foreground_index = _foreground_window_index(
        windows,
        _safe_foreground_handle(foreground_handle_provider or _foreground_window_handle),
    )
    topmost_provider = topmost_handle_provider
    if topmost_provider is None and desktop_factory is None:
        topmost_provider = _topmost_window_handle_at_point

    indexed_windows = list(enumerate(windows))
    indexed_windows.sort(
        key=lambda item: _candidate_window_rank(item[0], foreground_index),
    )

    for window_index, top in indexed_windows:
        if time.monotonic() >= deadline:
            break
        top_handle = _window_handle(top)
        if top_handle is not None and _is_own_process_window(top_handle):
            continue
        top_rect = _element_rect(top)
        if top_rect is None or not _intersects(top_rect, capture_rect):
            continue
        window_title = _control_text(top)
        queue: list[tuple[object, tuple[int, int, int, int], int]] = [(top, top_rect, 0)]
        while queue:
            if time.monotonic() >= deadline:
                break
            control, rect, depth = queue.pop(0)
            ctype = _control_type(control)
            if (
                ctype in CLICKABLE_CONTROL_TYPES
                and _is_enabled(control)
                and _is_visible(control)
                and _acceptable_bounds(rect, capture_rect, ctype)
                and _is_candidate_topmost(top_handle, rect, topmost_provider)
            ):
                raw.append(
                    ControlCandidate(
                        id=f"c{len(raw) + 1:03d}",
                        text=_control_text(control),
                        control_type=ctype,
                        rect=rect,
                        automation_id=_automation_id(control),
                        window_title=window_title,
                        depth=depth + window_index * 100,
                        window_rank=_candidate_window_rank(window_index, foreground_index),
                    )
                )
            if depth >= MAX_BFS_DEPTH:
                continue
            try:
                children = control.children()
            except Exception:
                continue
            for child in children:
                crect = _element_rect(child)
                if crect is None or not _intersects(crect, capture_rect):
                    continue
                queue.append((child, crect, depth + 1))

    deduped = _prune_dominated_candidates(_dedupe_candidates(raw))
    ranked = sorted(deduped, key=_candidate_sort_key)
    limited = ranked[: max(limit, 0)]
    return [
        ControlCandidate(
            id=f"c{index + 1:03d}",
            text=item.text,
            control_type=item.control_type,
            rect=item.rect,
            automation_id=item.automation_id,
            window_title=item.window_title,
            depth=item.depth,
            window_rank=item.window_rank,
        )
        for index, item in enumerate(limited)
    ]


def format_candidates_for_prompt(
    candidates: list[ControlCandidate],
    capture: Capture,
    *,
    limit: int = MAX_CANDIDATES,
) -> str:
    if not candidates:
        return (
            "Visible clickable controls: none available from Windows UI Automation. "
            "Use screenshot coordinates only if the target is clearly visible."
        )
    lines = [
        "Visible clickable controls. Visible text is shown separately from automation_id; "
        "prefer visible_text and do not treat automation_id as visible screen text when they conflict. "
        "Foreground-window controls are listed first when detected. "
        "These target IDs are valid only for this screenshot; ignore IDs from earlier turns. "
        "Prefer target_id over raw coordinates when the intended control is listed:",
    ]
    for candidate in candidates[:limit]:
        norm = _norm_rect(candidate.rect, capture)
        visible_text = candidate.text.strip() or "(none)"
        automation = (
            f' automation_id="{_clip(candidate.automation_id, 50)}"'
            if candidate.automation_id
            else ""
        )
        window = f' window="{_clip(candidate.window_title, 50)}"' if candidate.window_title else ""
        lines.append(
            f'- {candidate.id}: {candidate.control_type} '
            f'visible_text="{_clip(visible_text, 70)}"{automation}{window} '
            f"norm=({norm[0]},{norm[1]},{norm[2]},{norm[3]})"
        )
    return "\n".join(lines)


def _safe_foreground_handle(provider: ForegroundHandleProvider) -> int | None:
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
        user32 = ctypes.windll.user32
        hwnd = int(user32.GetForegroundWindow())
    except Exception:
        return None
    if not hwnd:
        return None
    if _is_own_process_window(hwnd):
        return None
    return hwnd


def _topmost_window_handle_at_point(x: int, y: int) -> int | None:
    try:
        point = wintypes.POINT(int(x), int(y))
        hwnd = int(ctypes.windll.user32.WindowFromPoint(point))
    except Exception:
        return None
    return hwnd or None


def _is_own_process_window(hwnd: int) -> bool:
    pid = wintypes.DWORD()
    try:
        ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    except Exception:
        return False
    return int(pid.value) == os.getpid()


def _is_candidate_topmost(
    top_handle: int | None,
    rect: tuple[int, int, int, int],
    topmost_handle_provider: TopmostHandleProvider | None,
) -> bool:
    if top_handle is None or topmost_handle_provider is None:
        return True
    expected_root = _root_window_handle(top_handle)
    if expected_root is None:
        return True
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


def _safe_topmost_handle(
    provider: TopmostHandleProvider,
    x: int,
    y: int,
) -> int | None:
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


def _root_window_handle(hwnd: int) -> int | None:
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


def _foreground_window_index(windows: list[object], foreground_handle: int | None) -> int | None:
    if foreground_handle is None:
        return None
    for index, window in enumerate(windows):
        if _window_handle(window) == foreground_handle:
            return index
    return None


def _window_handle(control: object) -> int | None:
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
        value = getattr(control.element_info, "handle", None)  # type: ignore[attr-defined]
    except Exception:
        return None
    if not value:
        return None
    try:
        return int(value)
    except Exception:
        return None


def _candidate_window_rank(window_index: int, foreground_index: int | None) -> int:
    if foreground_index is None:
        return 0
    if window_index == foreground_index:
        return 0
    return window_index + 1 if window_index < foreground_index else window_index


def resolve_candidate_target(
    *,
    target_id: str,
    instruction: str,
    candidates: list[ControlCandidate],
    model_rect: tuple[int, int, int, int] | None = None,
) -> TargetResolution | None:
    """Resolve a model decision to a candidate rect by ID or accessible text.

    The model sees both screenshots and UIA candidate IDs, but IDs are still
    model output and can be wrong. An exact ID is therefore accepted only when
    the candidate is semantically compatible with the instruction or agrees
    geometrically with the model rectangle. When evidence conflicts, returning a
    rejected TargetResolution lets Help mode narrate instead of drawing a
    confidently wrong rectangle.
    """
    if target_id:
        for candidate in candidates:
            if candidate.id == target_id:
                accepted, confidence, reason = _target_id_plausibility(
                    instruction=instruction,
                    candidate=candidate,
                    candidates=candidates,
                    model_rect=model_rect,
                )
                if not accepted:
                    return TargetResolution(
                        rect=candidate.rect,
                        confidence=confidence,
                        source="target_id",
                        matched_text=candidate.descriptor,
                        target_id=candidate.id,
                        rejected_reason=reason,
                    )
                return TargetResolution(
                    rect=candidate.rect,
                    confidence=confidence,
                    source="target_id",
                    matched_text=candidate.descriptor,
                    target_id=candidate.id,
                )
        return TargetResolution(
            rect=model_rect or (0, 0, 0, 0),
            confidence=0.0,
            source="target_id",
            target_id=target_id,
            rejected_reason="unknown target_id",
        )

    instruction_tokens = _tokenize_instruction(instruction)
    control_intents = _instruction_control_intents(instruction)
    dialog_dismiss = _single_dialog_dismiss_candidate(
        instruction=instruction,
        candidates=candidates,
        model_rect=model_rect,
    )
    if dialog_dismiss is not None:
        return dialog_dismiss
    ranked: list[tuple[float, ControlCandidate]] = []
    for candidate in candidates:
        if control_intents and not _candidate_matches_control_intent(candidate, control_intents):
            score = _context_text_match_score(
                instruction,
                instruction_tokens,
                candidate,
                candidates,
                model_rect,
            )
        else:
            score = _text_match_score(instruction, candidate, candidates, model_rect)
        score += _foreground_rank_bonus(candidate, candidates)
        if score > 0:
            if not _candidate_matches_control_intent(candidate, control_intents):
                contained = _single_contained_control_intent_candidate(
                    candidates=candidates,
                    model_rect=candidate.rect,
                    instruction=instruction,
                    instruction_tokens=instruction_tokens,
                    control_intents=control_intents,
                )
                if contained is None:
                    continue
                candidate = contained
            elif _menu_segment_intent(control_intents) and candidate.control_type == "splitbutton":
                if not _contains_tighter_same_intent_action(
                    selected=candidate,
                    candidates=candidates,
                    instruction_tokens=instruction_tokens,
                    control_intents=control_intents,
                ):
                    continue
                score = min(score, CONTAINING_ROW_SNAP_CAP)
            elif _contains_tighter_same_intent_action(
                selected=candidate,
                candidates=candidates,
                instruction_tokens=instruction_tokens,
                control_intents=control_intents,
            ):
                score = min(score, CONTAINING_ROW_SNAP_CAP)
            ranked.append((score, candidate))

    if not ranked:
        return None

    ranked.sort(key=lambda item: item[0], reverse=True)
    best_score, candidate = ranked[0]
    if best_score < TEXT_MATCH_FLOOR:
        return None

    runner_up = _first_distinct_ranked_candidate(ranked[1:], candidate)
    if runner_up is not None and best_score - runner_up[0] < TEXT_MATCH_GAP:
        return TargetResolution(
            rect=candidate.rect,
            confidence=best_score,
            source="text_match",
            matched_text=candidate.descriptor,
            target_id=candidate.id,
            rejected_reason="ambiguous text match",
        )

    return TargetResolution(
        rect=candidate.rect,
        confidence=best_score,
        source="text_match",
        matched_text=candidate.descriptor,
        target_id=candidate.id,
    )


def snap_candidate_target(
    *,
    instruction: str,
    candidates: list[ControlCandidate],
    model_rect: tuple[int, int, int, int],
    margin_px: int = CANDIDATE_SNAP_MARGIN_PX,
    confidence_floor: float = CANDIDATE_SNAP_FLOOR,
) -> TargetResolution | None:
    """Snap a model rectangle to the best already-collected UIA candidate.

    Help mode collects a candidate inventory before asking the model. Reusing
    that same snapshot avoids a second UIA enumeration producing a slightly
    different set of controls while the screen is changing.
    """
    search_rect = _expand_rect(model_rect, margin_px)
    ranked: list[tuple[float, ControlCandidate]] = []
    instruction_tokens = _tokenize_instruction(instruction)
    control_intents = _instruction_control_intents(instruction)
    for candidate in candidates:
        if not _intersects(candidate.rect, search_rect):
            continue
        score = _candidate_snap_score(
            candidate=candidate,
            candidates=candidates,
            instruction=instruction,
            instruction_tokens=instruction_tokens,
            control_intents=control_intents,
            model_rect=model_rect,
        )
        if score > 0:
            ranked.append((score, candidate))

    if not ranked:
        contained = _single_contained_control_intent_candidate(
            candidates=candidates,
            model_rect=model_rect,
            instruction=instruction,
            instruction_tokens=instruction_tokens,
            control_intents=control_intents,
        )
        if contained is not None:
            return TargetResolution(
                rect=contained.rect,
                confidence=confidence_floor,
                source="candidate_snap",
                matched_text=contained.descriptor,
                target_id=contained.id,
            )
        return None
    ranked.sort(key=lambda item: item[0], reverse=True)
    best_score, candidate = ranked[0]
    if best_score < confidence_floor:
        contained = _single_contained_control_intent_candidate(
            candidates=candidates,
            model_rect=model_rect,
            instruction=instruction,
            instruction_tokens=instruction_tokens,
            control_intents=control_intents,
        )
        if contained is not None:
            return TargetResolution(
                rect=contained.rect,
                confidence=confidence_floor,
                source="candidate_snap",
                matched_text=contained.descriptor,
                target_id=contained.id,
            )
        if _candidate_snap_semantic_mismatch(
            candidate=candidate,
            candidates=candidates,
            instruction=instruction,
            instruction_tokens=instruction_tokens,
            model_rect=model_rect,
        ):
            return TargetResolution(
                rect=candidate.rect,
                confidence=best_score,
                source="candidate_snap",
                matched_text=candidate.descriptor,
                target_id=candidate.id,
                rejected_reason="candidate semantic mismatch",
            )
        return None
    if _foreground_snap_conflict(
        ranked=ranked,
        instruction_tokens=instruction_tokens,
        confidence_floor=confidence_floor,
    ):
        return TargetResolution(
            rect=candidate.rect,
            confidence=best_score,
            source="candidate_snap",
            matched_text=candidate.descriptor,
            target_id=candidate.id,
            rejected_reason="ambiguous candidate snap",
        )
    runner_up = _first_distinct_ranked_candidate(ranked[1:], candidate)
    if runner_up is not None and best_score - runner_up[0] < TEXT_MATCH_GAP:
        return TargetResolution(
            rect=candidate.rect,
            confidence=best_score,
            source="candidate_snap",
            matched_text=candidate.descriptor,
            target_id=candidate.id,
            rejected_reason="ambiguous candidate snap",
        )
    return TargetResolution(
        rect=candidate.rect,
        confidence=best_score,
        source="candidate_snap",
        matched_text=candidate.descriptor,
        target_id=candidate.id,
    )


def _default_desktop():
    from pywinauto import Desktop

    return Desktop(backend="uia")


def _capture_screen_rect(capture: Capture) -> tuple[int, int, int, int]:
    width = int(capture.width / max(capture.scale, 0.001))
    height = int(capture.height / max(capture.scale, 0.001))
    return (capture.monitor_left, capture.monitor_top, width, height)


def _element_rect(control: object) -> tuple[int, int, int, int] | None:
    try:
        r = control.element_info.rectangle  # type: ignore[attr-defined]
    except Exception:
        try:
            r = control.rectangle()  # type: ignore[attr-defined]
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


def _control_type(control: object) -> str:
    try:
        return (control.element_info.control_type or "").strip().lower()  # type: ignore[attr-defined]
    except Exception:
        return ""


def _automation_id(control: object) -> str:
    try:
        return (getattr(control.element_info, "automation_id", "") or "").strip()  # type: ignore[attr-defined]
    except Exception:
        return ""


def _control_text(control: object) -> str:
    parts: list[str] = []
    try:
        text = (control.window_text() or "").strip()  # type: ignore[attr-defined]
        if text:
            parts.append(text)
    except Exception:
        pass
    try:
        info = control.element_info  # type: ignore[attr-defined]
        name = (getattr(info, "name", "") or "").strip()
        if name and name not in parts:
            parts.append(name)
    except Exception:
        pass
    return " | ".join(parts)


def _is_enabled(control: object) -> bool:
    try:
        return bool(control.is_enabled())  # type: ignore[attr-defined]
    except Exception:
        try:
            value = getattr(control.element_info, "enabled", None)  # type: ignore[attr-defined]
        except Exception:
            value = None
        return True if value is None else bool(value)


def _is_visible(control: object) -> bool:
    try:
        return bool(control.is_visible())  # type: ignore[attr-defined]
    except Exception:
        try:
            value = getattr(control.element_info, "visible", None)  # type: ignore[attr-defined]
        except Exception:
            value = None
        return True if value is None else bool(value)


def _acceptable_bounds(
    rect: tuple[int, int, int, int],
    capture_rect: tuple[int, int, int, int],
    ctype: str,
) -> bool:
    _, _, width, height = rect
    _, _, cap_width, cap_height = capture_rect
    if width < 4 or height < 4:
        return False
    visible = _intersection_rect(rect, capture_rect)
    if visible is None:
        return False
    visible_area = visible[2] * visible[3]
    area = width * height
    if visible_area / max(1, area) < MIN_VISIBLE_FRACTION:
        return False
    capture_area = max(1, cap_width * cap_height)
    if area > capture_area * 0.35:
        return False
    if ctype not in {"edit", "combobox", "listitem", "treeitem"}:
        if width > cap_width * 0.85 or height > cap_height * 0.85:
            return False
    return True


def _dedupe_candidates(candidates: list[ControlCandidate]) -> list[ControlCandidate]:
    seen: set[tuple[tuple[int, int, int, int], str, str]] = set()
    out: list[ControlCandidate] = []
    for candidate in candidates:
        key = _candidate_visual_key(candidate)
        if key in seen:
            continue
        seen.add(key)
        out.append(candidate)
    return out


def _candidate_visual_key(
    candidate: ControlCandidate,
) -> tuple[tuple[int, int, int, int], str, str]:
    return (candidate.rect, candidate.control_type, _candidate_semantic_key(candidate))


def _candidate_semantic_key(candidate: ControlCandidate) -> str:
    visible = _candidate_text_key(candidate.text)
    if visible:
        return f"text:{visible}"
    automation = _candidate_text_key(candidate.automation_id)
    if automation:
        return f"automation:{automation}"
    return ""


def _candidate_text_key(text: str) -> str:
    tokens = _tokens_from_text(text)
    if tokens:
        return " ".join(sorted(tokens))
    return (text or "").strip().lower()


def _same_visual_candidate(first: ControlCandidate, second: ControlCandidate) -> bool:
    return _candidate_visual_key(first) == _candidate_visual_key(second)


def _first_distinct_ranked_candidate(
    ranked: list[tuple[float, ControlCandidate]],
    selected: ControlCandidate,
) -> tuple[float, ControlCandidate] | None:
    for score, candidate in ranked:
        if _same_visual_candidate(candidate, selected):
            continue
        return (score, candidate)
    return None


def _prune_dominated_candidates(candidates: list[ControlCandidate]) -> list[ControlCandidate]:
    out: list[ControlCandidate] = []
    for candidate in candidates:
        if candidate.control_type in ROW_LIKE_CONTROL_TYPES:
            out.append(candidate)
            continue
        candidate_area = candidate.rect[2] * candidate.rect[3]
        candidate_visible_tokens = _candidate_visible_text_tokens(candidate)
        candidate_tokens = candidate_visible_tokens or _candidate_automation_tokens(candidate)
        dominated = False
        for other in candidates:
            if other is candidate:
                continue
            other_area = other.rect[2] * other.rect[3]
            if other_area >= candidate_area:
                continue
            if candidate_area < other_area * 1.8:
                continue
            if not _contains_rect(candidate.rect, other.rect):
                continue
            other_tokens = (
                _candidate_visible_text_tokens(other)
                if candidate_visible_tokens
                else _candidate_semantic_tokens(other)
            )
            if candidate_tokens and not (other_tokens and candidate_tokens & other_tokens):
                continue
            dominated = True
            break
        if not dominated:
            out.append(candidate)
    return out


def _candidate_sort_key(candidate: ControlCandidate) -> tuple[int, int, int, int, int, int]:
    text_penalty = 0 if candidate.text.strip() else 1 if candidate.automation_id.strip() else 2
    x, y, width, height = candidate.rect
    return (candidate.window_rank, text_penalty, y, x, width * height, candidate.depth)


def _text_match_score(
    instruction: str,
    candidate: ControlCandidate,
    candidates: list[ControlCandidate],
    model_rect: tuple[int, int, int, int] | None,
) -> float:
    instruction_tokens = _tokenize_instruction(instruction)
    control_intents = _instruction_control_intents(instruction)
    if not _candidate_matches_control_intent(candidate, control_intents):
        return 0.0
    if not instruction_tokens:
        return 0.0
    if _taskbar_start_button_action_mismatch(instruction_tokens, candidate):
        return 0.0
    if _taskbar_task_view_action_mismatch(instruction, instruction_tokens, candidate):
        return 0.0
    if _taskbar_app_state_action_mismatch(instruction_tokens, candidate):
        return 0.0
    if _browser_profile_identity_action_mismatch(instruction_tokens, candidate):
        return 0.0
    if _browser_menu_button_action_mismatch(instruction, candidate):
        return 0.0
    if _browser_address_bar_content_mismatch(
        instruction,
        instruction_tokens,
        candidate,
    ):
        return 0.0
    if _browser_about_blank_title_info_mismatch(
        instruction,
        instruction_tokens,
        candidate,
    ):
        return 0.0
    if _hidden_bookmarks_overflow_action_mismatch(instruction_tokens, candidate):
        return 0.0
    if _close_tab_action_mismatch(instruction, candidate, candidates):
        return 0.0
    if _browser_new_tab_bookmark_action_mismatch(instruction_tokens, candidate):
        return 0.0
    if _browser_extension_access_action_mismatch(
        instruction,
        instruction_tokens,
        candidate,
    ):
        return 0.0
    if _site_information_action_mismatch(instruction_tokens, candidate):
        return 0.0
    if _unnamed_bookmark_generic_route_mismatch(
        instruction,
        instruction_tokens,
        candidate,
    ):
        return 0.0
    if _browser_group_state_action_mismatch(instruction_tokens, candidate):
        return 0.0
    if _disclosure_state_action_mismatch(instruction_tokens, candidate):
        return 0.0
    if _mail_tab_account_reference_mismatch(instruction_tokens, candidate):
        return 0.0
    if _browser_tab_auth_action_mismatch(instruction_tokens, candidate):
        return 0.0
    visible_tokens = _candidate_visible_text_tokens(candidate)
    candidate_tokens = _candidate_semantic_tokens(candidate)
    if not candidate_tokens:
        return 0.0
    overlap = instruction_tokens & candidate_tokens
    if not overlap:
        return 0.0

    coverage = len(overlap) / max(1, len(instruction_tokens))
    density = len(overlap) / max(1, len(candidate_tokens))
    score = 0.70 * coverage + 0.18 * min(1.0, density * 3.0)
    window_tokens = _tokens_from_text(candidate.window_title)
    if window_tokens and instruction_tokens & window_tokens:
        score += 0.04
    if candidate.control_type in instruction_tokens:
        score += 0.03
    if visible_tokens:
        score += VISIBLE_TEXT_MATCH_BONUS
    elif candidate.automation_id.strip():
        score -= AUTOMATION_ONLY_MATCH_PENALTY
    if model_rect is not None:
        score += 0.05 * _proximity_score(candidate.rect, model_rect)
    return min(max(score, 0.0), 1.0)


def _context_text_match_score(
    instruction: str,
    instruction_tokens: set[str],
    candidate: ControlCandidate,
    candidates: list[ControlCandidate],
    model_rect: tuple[int, int, int, int] | None,
) -> float:
    if not instruction_tokens:
        return 0.0
    if _taskbar_start_button_action_mismatch(instruction_tokens, candidate):
        return 0.0
    if _taskbar_task_view_action_mismatch(instruction, instruction_tokens, candidate):
        return 0.0
    if _taskbar_app_state_action_mismatch(instruction_tokens, candidate):
        return 0.0
    if _browser_profile_identity_action_mismatch(instruction_tokens, candidate):
        return 0.0
    if _browser_menu_button_action_mismatch(instruction, candidate):
        return 0.0
    if _browser_address_bar_content_mismatch(
        instruction,
        instruction_tokens,
        candidate,
    ):
        return 0.0
    if _browser_about_blank_title_info_mismatch(
        instruction,
        instruction_tokens,
        candidate,
    ):
        return 0.0
    if _hidden_bookmarks_overflow_action_mismatch(instruction_tokens, candidate):
        return 0.0
    if _close_tab_action_mismatch(instruction, candidate, candidates):
        return 0.0
    if _browser_new_tab_bookmark_action_mismatch(instruction_tokens, candidate):
        return 0.0
    if _browser_extension_access_action_mismatch(
        instruction,
        instruction_tokens,
        candidate,
    ):
        return 0.0
    if _site_information_action_mismatch(instruction_tokens, candidate):
        return 0.0
    if _unnamed_bookmark_generic_route_mismatch(
        instruction,
        instruction_tokens,
        candidate,
    ):
        return 0.0
    if _browser_group_state_action_mismatch(instruction_tokens, candidate):
        return 0.0
    if _disclosure_state_action_mismatch(instruction_tokens, candidate):
        return 0.0
    if _mail_tab_account_reference_mismatch(instruction_tokens, candidate):
        return 0.0
    if _browser_tab_auth_action_mismatch(instruction_tokens, candidate):
        return 0.0
    visible_tokens = _candidate_visible_text_tokens(candidate)
    candidate_tokens = _candidate_semantic_tokens(candidate)
    if not candidate_tokens:
        return 0.0
    overlap = instruction_tokens & candidate_tokens
    if not overlap:
        return 0.0
    coverage = len(overlap) / max(1, len(instruction_tokens))
    density = len(overlap) / max(1, len(candidate_tokens))
    score = 0.70 * coverage + 0.18 * min(1.0, density * 3.0)
    if visible_tokens:
        score += VISIBLE_TEXT_MATCH_BONUS
    elif candidate.automation_id.strip():
        score -= AUTOMATION_ONLY_MATCH_PENALTY
    if model_rect is not None:
        score += 0.05 * _proximity_score(candidate.rect, model_rect)
    return min(max(score, 0.0), 1.0)


def _single_dialog_dismiss_candidate(
    *,
    instruction: str,
    candidates: list[ControlCandidate],
    model_rect: tuple[int, int, int, int] | None,
) -> TargetResolution | None:
    raw_tokens = _tokens_from_text(instruction)
    if raw_tokens & DISMISS_WINDOW_CONTEXT_WORDS and not raw_tokens & DISMISS_DIALOG_CONTEXT_WORDS:
        return None
    instruction_tokens = _tokenize_instruction(instruction)
    if not instruction_tokens or instruction_tokens - {"cancel", "close", "dismiss"}:
        return None
    dismiss_candidates: list[ControlCandidate] = []
    preferred: list[ControlCandidate] = []
    for candidate in candidates:
        if candidate.control_type not in {"button", "splitbutton", "menuitem"}:
            continue
        candidate_tokens = _candidate_visible_text_tokens(candidate)
        if not (candidate_tokens & {"cancel", "close", "dismiss", "x"}):
            continue
        dismiss_candidates.append(candidate)
        if (
            ("cancel" in raw_tokens and "cancel" in candidate_tokens)
            or ("close" in raw_tokens and candidate_tokens & {"close", "x"})
            or ("dismiss" in raw_tokens and "dismiss" in candidate_tokens)
        ):
            preferred.append(candidate)
    selected = preferred or dismiss_candidates
    if len(selected) != 1:
        if not selected:
            return None
        candidate = sorted(selected, key=_candidate_sort_key)[0]
        return TargetResolution(
            rect=candidate.rect,
            confidence=TEXT_MATCH_FLOOR,
            source="text_match",
            matched_text=candidate.descriptor,
            target_id=candidate.id,
            rejected_reason="ambiguous text match",
        )
    candidate = selected[0]
    confidence = TEXT_MATCH_FLOOR
    if model_rect is not None:
        confidence = min(1.0, confidence + 0.05 * _proximity_score(candidate.rect, model_rect))
    return TargetResolution(
        rect=candidate.rect,
        confidence=confidence,
        source="text_match",
        matched_text=candidate.descriptor,
        target_id=candidate.id,
    )


def _target_id_plausibility(
    *,
    instruction: str,
    candidate: ControlCandidate,
    candidates: list[ControlCandidate],
    model_rect: tuple[int, int, int, int] | None,
) -> tuple[bool, float, str]:
    instruction_tokens = _tokenize_instruction(instruction)
    control_intents = _instruction_control_intents(instruction)
    semantic_tokens = _candidate_semantic_tokens(candidate)
    text_score = _text_evidence_score(instruction_tokens, semantic_tokens)
    if _taskbar_start_button_action_mismatch(instruction_tokens, candidate):
        return (
            False,
            text_score,
            "target_id semantic mismatch",
        )
    if _taskbar_task_view_action_mismatch(instruction, instruction_tokens, candidate):
        return (
            False,
            text_score,
            "target_id semantic mismatch",
        )
    if _taskbar_app_state_action_mismatch(instruction_tokens, candidate):
        return (
            False,
            text_score,
            "target_id semantic mismatch",
        )
    if _browser_profile_identity_action_mismatch(instruction_tokens, candidate):
        return (
            False,
            text_score,
            "target_id semantic mismatch",
        )
    if _browser_menu_button_action_mismatch(instruction, candidate):
        return (
            False,
            text_score,
            "target_id semantic mismatch",
        )
    if _browser_address_bar_content_mismatch(
        instruction,
        instruction_tokens,
        candidate,
    ):
        return (
            False,
            text_score,
            "target_id semantic mismatch",
        )
    if _browser_about_blank_title_info_mismatch(
        instruction,
        instruction_tokens,
        candidate,
    ):
        return (
            False,
            text_score,
            "target_id semantic mismatch",
        )
    if _hidden_bookmarks_overflow_action_mismatch(instruction_tokens, candidate):
        return (
            False,
            text_score,
            "target_id semantic mismatch",
        )
    if _close_tab_action_mismatch(instruction, candidate, candidates):
        return (
            False,
            text_score,
            "target_id semantic mismatch",
        )
    if _browser_new_tab_bookmark_action_mismatch(instruction_tokens, candidate):
        return (
            False,
            text_score,
            "target_id semantic mismatch",
        )
    if _browser_extension_access_action_mismatch(
        instruction,
        instruction_tokens,
        candidate,
    ):
        return (
            False,
            text_score,
            "target_id semantic mismatch",
        )
    if _site_information_action_mismatch(instruction_tokens, candidate):
        return (
            False,
            text_score,
            "target_id semantic mismatch",
        )
    if _unnamed_bookmark_generic_route_mismatch(
        instruction,
        instruction_tokens,
        candidate,
    ):
        return (
            False,
            text_score,
            "target_id semantic mismatch",
        )
    if _browser_group_state_action_mismatch(instruction_tokens, candidate):
        return (
            False,
            text_score,
            "target_id semantic mismatch",
        )
    if _disclosure_state_action_mismatch(instruction_tokens, candidate):
        return (
            False,
            text_score,
            "target_id semantic mismatch",
        )
    if _mail_tab_account_reference_mismatch(instruction_tokens, candidate):
        return (
            False,
            text_score,
            "target_id semantic mismatch",
        )
    if _browser_tab_auth_action_mismatch(instruction_tokens, candidate):
        return (
            False,
            text_score,
            "target_id semantic mismatch",
        )
    geometry_score = (
        _geometry_agreement(candidate.rect, model_rect) if model_rect is not None else 0.0
    )
    if _contains_tighter_same_intent_action(
        selected=candidate,
        candidates=candidates,
        instruction_tokens=instruction_tokens,
        control_intents=control_intents,
    ):
        return (
            False,
            max(text_score, geometry_score),
            "target_id ambiguous",
        )
    if not _candidate_matches_control_intent(candidate, control_intents):
        return (
            False,
            max(text_score, geometry_score),
            "target_id control type mismatch",
        )
    if (
        _menu_segment_intent(control_intents)
        and candidate.control_type == "splitbutton"
        and not _contains_tighter_same_intent_action(
            selected=candidate,
            candidates=candidates,
            instruction_tokens=instruction_tokens,
            control_intents=control_intents,
        )
    ):
        return (
            False,
            max(text_score, geometry_score),
            "target_id ambiguous",
        )

    if not instruction_tokens:
        if _has_nearby_unlabeled_competitor(candidate, candidates):
            return (
                False,
                geometry_score,
                "target_id ambiguous unlabeled control",
            )
        if model_rect is not None and geometry_score >= TARGET_ID_GEOMETRY_FLOOR:
            return True, max(0.82, geometry_score), ""
        return False, geometry_score, "target_id lacks instruction evidence"

    if _has_visible_semantic_alternative(
        instruction=instruction,
        instruction_tokens=instruction_tokens,
        selected=candidate,
        candidates=candidates,
        control_intents=control_intents,
    ):
        return (
            False,
            max(text_score, geometry_score),
            "target_id ambiguous",
        )

    if text_score >= TARGET_ID_TEXT_FLOOR:
        ambiguous, _gap = _target_id_ambiguity(
            instruction=instruction,
            instruction_tokens=instruction_tokens,
            selected=candidate,
            candidates=candidates,
            model_rect=model_rect,
            control_intents=control_intents,
        )
        if ambiguous:
            return False, max(text_score, geometry_score), "target_id ambiguous"
        return True, max(0.86, text_score, geometry_score), ""

    if semantic_tokens:
        return (
            False,
            max(text_score, geometry_score),
            "target_id semantic mismatch",
        )

    if geometry_score >= TARGET_ID_GEOMETRY_FLOOR:
        if _has_semantic_alternative(
            instruction=instruction,
            instruction_tokens=instruction_tokens,
            selected=candidate,
            candidates=candidates,
            control_intents=control_intents,
        ):
            return (
                False,
                geometry_score,
                "target_id ambiguous",
            )
        if _has_nearby_unlabeled_competitor(candidate, candidates):
            return (
                False,
                geometry_score,
                "target_id ambiguous unlabeled control",
            )
        return True, max(0.78, geometry_score), ""

    return (
        False,
        geometry_score,
        "target_id lacks label and geometry agreement",
    )


def _candidate_identity_tokens(candidate: ControlCandidate) -> set[str]:
    text = " ".join(
        part
        for part in (_candidate_visible_semantic_text(candidate), candidate.automation_id)
        if part
    )
    return _expand_token_aliases(_tokens_from_text(text))


def _candidate_visible_text_tokens(candidate: ControlCandidate) -> set[str]:
    return _tokenize_control(_candidate_visible_semantic_text(candidate))


def _candidate_automation_tokens(candidate: ControlCandidate) -> set[str]:
    return _tokenize_control(candidate.automation_id)


def _candidate_semantic_tokens(candidate: ControlCandidate) -> set[str]:
    inferred_tokens = _candidate_inferred_semantic_tokens(candidate)
    visible_tokens = _candidate_visible_text_tokens(candidate)
    if visible_tokens:
        return visible_tokens | inferred_tokens
    automation_tokens = _candidate_automation_tokens(candidate)
    return automation_tokens | inferred_tokens


def _candidate_visible_semantic_text(candidate: ControlCandidate) -> str:
    text = candidate.text or ""
    if candidate.control_type == "tabitem":
        text = BROWSER_TAB_OWNER_ACCOUNT_RE.sub("", text)
        text = BROWSER_TAB_MEMORY_USAGE_RE.sub("", text)
    return text.strip()


def _candidate_inferred_semantic_tokens(candidate: ControlCandidate) -> set[str]:
    if _looks_like_browser_profile_button(candidate):
        return set(BROWSER_PROFILE_TOKENS)
    if _looks_like_browser_menu_button(candidate):
        return set(BROWSER_MENU_BUTTON_TOKENS)
    if _looks_like_taskbar_search_button(candidate):
        return set(TASKBAR_WINDOWS_SEARCH_TOKENS)
    return set()


def _looks_like_taskbar_search_button(candidate: ControlCandidate) -> bool:
    if candidate.control_type not in {"button", "splitbutton"}:
        return False
    window_tokens = _tokens_from_text(candidate.window_title)
    if not (window_tokens & TASKBAR_WINDOW_WORDS):
        return False
    text_tokens = _tokens_from_text(candidate.text)
    return "search" in text_tokens


def _looks_like_browser_profile_button(candidate: ControlCandidate) -> bool:
    if candidate.control_type not in {"button", "splitbutton"}:
        return False
    if _looks_like_unnamed_bookmark(candidate):
        return False
    width, height = candidate.rect[2], candidate.rect[3]
    if width <= 0 or height <= 0:
        return False
    if max(width, height) > BROWSER_PROFILE_MAX_EDGE:
        return False
    if max(width, height) / max(1, min(width, height)) > BROWSER_PROFILE_MAX_ASPECT:
        return False
    window_tokens = _tokens_from_text(candidate.window_title)
    if not (window_tokens & BROWSER_PROFILE_WINDOW_WORDS):
        return False
    text_tokens = _tokens_from_text(candidate.text)
    return bool(text_tokens & BROWSER_PROFILE_LABEL_HINT_WORDS)


def _browser_profile_identity_action_mismatch(
    instruction_tokens: set[str],
    candidate: ControlCandidate,
) -> bool:
    if not instruction_tokens & BROWSER_PROFILE_TOKENS:
        return False
    if instruction_tokens & BROWSER_PROFILE_ACTION_CONTEXT_WORDS:
        return False
    candidate_tokens = _candidate_semantic_tokens(candidate)
    if candidate_tokens & BROWSER_PROFILE_TOKENS:
        return False
    raw_candidate_tokens = _tokens_from_text(candidate.text)
    window_tokens = _tokens_from_text(candidate.window_title)
    if raw_candidate_tokens & BROWSER_APP_IDENTITY_WORDS:
        return True
    return bool(
        window_tokens & TASKBAR_WINDOW_WORDS
        and raw_candidate_tokens & BROWSER_APP_IDENTITY_WORDS
    )


def _browser_address_bar_content_mismatch(
    instruction: str,
    instruction_tokens: set[str],
    candidate: ControlCandidate,
) -> bool:
    if not _looks_like_browser_address_bar(candidate):
        return False
    candidate_tokens = _candidate_semantic_tokens(candidate)
    if not (instruction_tokens & candidate_tokens):
        return False
    return not _instruction_requests_browser_address_bar(instruction)


def _looks_like_browser_address_bar(candidate: ControlCandidate) -> bool:
    if candidate.control_type not in {"edit", "combobox"}:
        return False
    raw_tokens = _tokens_from_text(candidate.text)
    if {"address", "bar"} <= raw_tokens:
        return True
    window_tokens = _tokens_from_text(candidate.window_title)
    if window_tokens and not (window_tokens & BROWSER_PROFILE_WINDOW_WORDS):
        return False
    return bool(raw_tokens & (BROWSER_ADDRESS_BAR_ROLE_WORDS - {"bar", "search"}))


def _instruction_requests_browser_address_bar(instruction: str) -> bool:
    raw_tokens = _tokens_from_text(instruction)
    if raw_tokens & (BROWSER_ADDRESS_BAR_REQUEST_WORDS - {"find", "search"}):
        return True
    return "bar" in raw_tokens and bool(raw_tokens & {"find", "search"})


def _looks_like_browser_menu_button(candidate: ControlCandidate) -> bool:
    if candidate.control_type not in {"button", "splitbutton"}:
        return False
    window_tokens = _tokens_from_text(candidate.window_title)
    if window_tokens and not (window_tokens & BROWSER_PROFILE_WINDOW_WORDS):
        return False
    return _tokens_from_text(candidate.text) == {"chrome"}


def _browser_menu_button_action_mismatch(
    instruction: str,
    candidate: ControlCandidate,
) -> bool:
    raw_tokens = _tokens_from_text(instruction)
    if not (raw_tokens & BROWSER_MENU_REQUEST_WORDS):
        return False
    if raw_tokens & BROWSER_MENU_SPECIFIC_CONTEXT_WORDS:
        return False
    if not (
        raw_tokens
        & {"browser", "button", "chrome", "menu", "more", "options", "overflow"}
    ):
        return False
    window_tokens = _tokens_from_text(candidate.window_title)
    if not (window_tokens & BROWSER_PROFILE_WINDOW_WORDS):
        return False
    if _looks_like_browser_menu_button(candidate):
        return False
    return candidate.control_type in BROWSER_MENU_CONTROL_INTENTS


def _hidden_bookmarks_overflow_action_mismatch(
    instruction_tokens: set[str],
    candidate: ControlCandidate,
) -> bool:
    if not _looks_like_hidden_bookmarks_overflow_button(candidate):
        return False
    if "hidden" in instruction_tokens and not (
        "bookmarks" in instruction_tokens
        or instruction_tokens & BROWSER_HIDDEN_BOOKMARKS_GENERIC_MENU_WORDS
    ):
        return True
    if "hidden" in instruction_tokens:
        return False
    if "all" in instruction_tokens and "bookmarks" in instruction_tokens:
        return True
    if "bookmarks" in instruction_tokens:
        return False
    return bool(instruction_tokens & BROWSER_HIDDEN_BOOKMARKS_GENERIC_MENU_WORDS)


def _browser_new_tab_bookmark_action_mismatch(
    instruction_tokens: set[str],
    candidate: ControlCandidate,
) -> bool:
    if not (instruction_tokens & BROWSER_BOOKMARK_ACTION_WORDS):
        return False
    candidate_tokens = _candidate_semantic_tokens(candidate)
    if candidate_tokens & BROWSER_BOOKMARK_ACTION_WORDS:
        return False
    return bool(candidate_tokens & BROWSER_NEW_TAB_WORDS)


def _close_tab_action_mismatch(
    instruction: str,
    candidate: ControlCandidate,
    candidates: list[ControlCandidate],
) -> bool:
    raw_tokens = _tokens_from_text(instruction)
    if not (raw_tokens & {"close", "dismiss"} and raw_tokens & {"tab", "tabs", "tabitem"}):
        return False
    candidate_tokens = _candidate_semantic_tokens(candidate)
    if not (candidate_tokens & {"close", "dismiss", "x"}):
        return False
    return not _close_button_has_tab_context(candidate, candidates)


def _close_button_has_tab_context(
    candidate: ControlCandidate,
    candidates: list[ControlCandidate],
) -> bool:
    for other in candidates:
        if other.id == candidate.id or other.control_type != "tabitem":
            continue
        if _contains_rect(_expand_rect(other.rect, 8), candidate.rect):
            return True
        if _intersects(_expand_rect(other.rect, 8), candidate.rect):
            return True
    return False


def _looks_like_hidden_bookmarks_overflow_button(candidate: ControlCandidate) -> bool:
    if candidate.control_type not in {"button", "splitbutton"}:
        return False
    window_tokens = _tokens_from_text(candidate.window_title)
    if window_tokens and not (window_tokens & BROWSER_PROFILE_WINDOW_WORDS):
        return False
    text_tokens = _tokens_from_text(candidate.text)
    return bool(BROWSER_HIDDEN_BOOKMARKS_WORDS <= text_tokens and "menu" in text_tokens)


def _browser_about_blank_title_info_mismatch(
    instruction: str,
    instruction_tokens: set[str],
    candidate: ControlCandidate,
) -> bool:
    if not _looks_like_browser_about_blank_title(candidate):
        return False
    if not (instruction_tokens & SITE_INFORMATION_REQUEST_WORDS):
        return False
    raw_instruction_tokens = _tokens_from_text(instruction)
    return not bool(raw_instruction_tokens & BROWSER_ABOUT_BLANK_TARGET_WORDS)


def _looks_like_browser_about_blank_title(candidate: ControlCandidate) -> bool:
    if candidate.control_type != "tabitem":
        return False
    window_tokens = _tokens_from_text(candidate.window_title)
    if window_tokens and not (window_tokens & BROWSER_PROFILE_WINDOW_WORDS):
        return False
    return {"about", "blank"} <= _tokens_from_text(candidate.text)


def _browser_extension_access_action_mismatch(
    instruction: str,
    instruction_tokens: set[str],
    candidate: ControlCandidate,
) -> bool:
    if not _looks_like_browser_extension_access_button(candidate):
        return False
    if not (instruction_tokens & BROWSER_EXTENSION_ACCESS_CONTEXT_WORDS):
        return False
    if _instruction_names_browser_extension_access_target(instruction, candidate):
        return False
    return True


def _looks_like_browser_extension_access_button(candidate: ControlCandidate) -> bool:
    if candidate.control_type not in {"button", "splitbutton"}:
        return False
    window_tokens = _tokens_from_text(candidate.window_title)
    if not (window_tokens & BROWSER_PROFILE_WINDOW_WORDS):
        return False
    text_tokens = _tokens_from_text(candidate.text)
    return bool(BROWSER_EXTENSION_ACCESS_CONTEXT_WORDS <= text_tokens)


def _browser_extension_access_target_tokens(candidate: ControlCandidate) -> set[str]:
    tokens = _tokens_from_text(candidate.text)
    return {
        token
        for token in tokens - BROWSER_EXTENSION_ACCESS_LABEL_STOPWORDS
        if len(token) > 1 and not token.isdigit()
    }


def _instruction_names_browser_extension_access_target(
    instruction: str,
    candidate: ControlCandidate,
) -> bool:
    target_tokens = _browser_extension_access_target_tokens(candidate)
    if not target_tokens:
        return False
    raw_words = set(re.findall(r"[a-z0-9]+", (instruction or "").lower()))
    instruction_specific = {
        word
        for word in raw_words - BROWSER_EXTENSION_ACCESS_INSTRUCTION_STOPWORDS
        if len(word) > 1 and not word.isdigit()
    }
    if not instruction_specific:
        return False
    target_text = (candidate.text or "").lower()
    target_compact = re.sub(r"[^a-z0-9]+", "", target_text)
    for word in instruction_specific:
        if word in target_tokens:
            return True
        if len(word) >= 4 and word in target_compact:
            return True
    return False


def _site_information_action_mismatch(
    instruction_tokens: set[str],
    candidate: ControlCandidate,
) -> bool:
    if not _looks_like_site_information_button(candidate):
        return False
    if "site" not in instruction_tokens:
        return False
    return not bool(instruction_tokens & SITE_INFORMATION_REQUEST_WORDS)


def _looks_like_site_information_button(candidate: ControlCandidate) -> bool:
    if candidate.control_type not in {"button", "splitbutton"}:
        return False
    window_tokens = _tokens_from_text(candidate.window_title)
    if window_tokens and not (window_tokens & BROWSER_PROFILE_WINDOW_WORDS):
        return False
    return "site_info_lock" in _candidate_visible_text_tokens(candidate)


def _taskbar_app_state_action_mismatch(
    instruction_tokens: set[str],
    candidate: ControlCandidate,
) -> bool:
    if not instruction_tokens or not _candidate_is_taskbar_app_button(candidate):
        return False
    text_tokens = _tokens_from_text(candidate.text)
    if (
        instruction_tokens & TASKBAR_PIN_ACTION_WORDS
        and text_tokens & TASKBAR_APP_STATE_WORDS
    ):
        return True
    if _taskbar_status_label_action_mismatch(
        instruction_tokens,
        text_tokens,
        candidate,
    ):
        return True
    identity_tokens = _taskbar_app_identity_tokens(candidate)
    if identity_tokens and instruction_tokens & identity_tokens:
        return False
    if _taskbar_app_generic_state_request(instruction_tokens, text_tokens):
        return True
    if _taskbar_app_generic_status_request(instruction_tokens, text_tokens):
        return True
    if instruction_tokens & TASKBAR_FILE_ACTION_WORDS and text_tokens & {"file", "files"}:
        return True
    return False


def _taskbar_start_button_action_mismatch(
    instruction_tokens: set[str],
    candidate: ControlCandidate,
) -> bool:
    if not _looks_like_taskbar_start_button(candidate):
        return False
    if "start" not in instruction_tokens:
        return False
    return bool(instruction_tokens - TASKBAR_START_BUTTON_ALLOWED_TOKENS)


def _taskbar_task_view_action_mismatch(
    instruction: str,
    instruction_tokens: set[str],
    candidate: ControlCandidate,
) -> bool:
    if not _looks_like_taskbar_task_view_button(candidate):
        return False
    if not (instruction_tokens & {"task", "view"}):
        return False
    if _instruction_mentions_task_view(instruction):
        return False
    return True


def _instruction_mentions_task_view(instruction: str) -> bool:
    normalized = " ".join((instruction or "").lower().split())
    compact = re.sub(r"[^a-z0-9]+", "", normalized)
    return bool(re.search(r"\btask\s+view\b", normalized)) or "taskview" in compact


def _looks_like_taskbar_task_view_button(candidate: ControlCandidate) -> bool:
    if candidate.control_type not in {"button", "splitbutton"}:
        return False
    window_tokens = _tokens_from_text(candidate.window_title)
    if not (window_tokens & TASKBAR_WINDOW_WORDS):
        return False
    text_tokens = _tokens_from_text(candidate.text)
    automation_tokens = _tokens_from_text(candidate.automation_id)
    return {"task", "view"} <= (text_tokens | automation_tokens)


def _looks_like_taskbar_start_button(candidate: ControlCandidate) -> bool:
    if candidate.control_type not in {"button", "splitbutton"}:
        return False
    window_tokens = _tokens_from_text(candidate.window_title)
    if not (window_tokens & TASKBAR_WINDOW_WORDS):
        return False
    automation_tokens = _tokens_from_text(candidate.automation_id)
    text_tokens = _tokens_from_text(candidate.text)
    return "startbutton" in automation_tokens or (
        "start" in text_tokens and "button" in automation_tokens
    )


def _unnamed_bookmark_generic_route_mismatch(
    instruction: str,
    instruction_tokens: set[str],
    candidate: ControlCandidate,
) -> bool:
    if not _looks_like_unnamed_bookmark(candidate):
        return False
    if (
        instruction_tokens & UNNAMED_BOOKMARK_ACTION_WORDS
        and not _instruction_names_unnamed_bookmark_destination(instruction, candidate)
    ):
        return True
    candidate_tokens = _candidate_visible_text_tokens(candidate)
    overlap = instruction_tokens & candidate_tokens
    if not overlap:
        return False
    if not all(_is_unnamed_bookmark_generic_route_token(token) for token in overlap):
        return False
    if _instruction_names_unnamed_bookmark_destination(instruction, candidate):
        return False
    return True


def _looks_like_unnamed_bookmark(candidate: ControlCandidate) -> bool:
    return candidate.control_type in {"button", "splitbutton"} and bool(
        UNNAMED_BOOKMARK_RE.search(candidate.text or "")
    )


def _is_unnamed_bookmark_generic_route_token(token: str) -> bool:
    return token in UNNAMED_BOOKMARK_GENERIC_ROUTE_WORDS or (
        token.isdigit() and len(token) >= 5
    )


def _instruction_names_unnamed_bookmark_destination(
    instruction: str,
    candidate: ControlCandidate,
) -> bool:
    raw_words = set(re.findall(r"[a-z0-9]+", (instruction or "").lower()))
    destination_words = _tokens_from_text(candidate.text)
    destination_text = (candidate.text or "").lower()
    destination_compact = re.sub(r"[^a-z0-9]+", "", destination_text)
    for word in raw_words:
        if (
            len(word) <= 1
            or word.isdigit()
            or word in UNNAMED_BOOKMARK_DESTINATION_STOPWORDS
        ):
            continue
        if word in destination_words:
            return True
        if len(word) >= 4 and word in destination_compact:
            return True
        if word == "ai" and "openai" in destination_compact:
            return True
    return False


def _browser_group_state_action_mismatch(
    instruction_tokens: set[str],
    candidate: ControlCandidate,
) -> bool:
    if not _looks_like_browser_named_group_button(candidate):
        return False
    text_tokens = _tokens_from_text(candidate.text)
    identity_tokens = text_tokens - BROWSER_GROUP_GENERIC_WORDS
    if identity_tokens and instruction_tokens & identity_tokens:
        return False
    overlap = instruction_tokens & text_tokens
    return bool(overlap and overlap <= BROWSER_GROUP_GENERIC_WORDS)


def _disclosure_state_action_mismatch(
    instruction_tokens: set[str],
    candidate: ControlCandidate,
) -> bool:
    return _disclosure_action_tokens_mismatch(
        instruction_tokens,
        _candidate_semantic_tokens(candidate),
    )


def _disclosure_action_tokens_mismatch(
    instruction_tokens: set[str],
    candidate_tokens: set[str],
) -> bool:
    requested_expand = bool(instruction_tokens & DISCLOSURE_EXPAND_ACTION_WORDS)
    requested_collapse = bool(instruction_tokens & DISCLOSURE_COLLAPSE_ACTION_WORDS)
    if requested_expand == requested_collapse:
        return False

    candidate_expand = bool(candidate_tokens & DISCLOSURE_EXPAND_ACTION_WORDS)
    candidate_collapse = bool(candidate_tokens & DISCLOSURE_COLLAPSE_ACTION_WORDS)
    if candidate_expand == candidate_collapse:
        return False
    return requested_expand != candidate_expand


def _looks_like_browser_named_group_button(candidate: ControlCandidate) -> bool:
    if candidate.control_type not in {"button", "splitbutton"}:
        return False
    window_tokens = _tokens_from_text(candidate.window_title)
    if window_tokens and not (window_tokens & BROWSER_PROFILE_WINDOW_WORDS):
        return False
    text_tokens = _tokens_from_text(candidate.text)
    return "group" in text_tokens and bool(text_tokens & BROWSER_GROUP_STATE_WORDS)


def _candidate_is_taskbar_app_button(candidate: ControlCandidate) -> bool:
    window_tokens = _tokens_from_text(candidate.window_title)
    return (
        candidate.control_type in {"button", "splitbutton"}
        and bool(candidate.text.strip())
        and bool(window_tokens & TASKBAR_WINDOW_WORDS)
    )


def _taskbar_app_identity_tokens(candidate: ControlCandidate) -> set[str]:
    tokens = _tokens_from_text(candidate.text)
    tokens -= TASKBAR_APP_STATE_CONTEXT_WORDS
    tokens -= TASKBAR_APP_STATUS_CONTEXT_WORDS
    tokens -= TASKBAR_GENERIC_FILE_IDENTITY_WORDS
    return {token for token in tokens if not token.isdigit()}


def _taskbar_app_generic_state_request(
    instruction_tokens: set[str],
    text_tokens: set[str],
) -> bool:
    if not text_tokens & TASKBAR_APP_STATE_CONTEXT_WORDS:
        return False
    if not instruction_tokens & TASKBAR_APP_STATE_CONTEXT_WORDS:
        return False
    meaningful_tokens = {
        token for token in instruction_tokens if not token.isdigit()
    }
    return bool(
        meaningful_tokens
        and meaningful_tokens
        <= (TASKBAR_APP_STATE_CONTEXT_WORDS | TASKBAR_APP_GENERIC_REQUEST_WORDS)
    )


def _taskbar_app_generic_status_request(
    instruction_tokens: set[str],
    text_tokens: set[str],
) -> bool:
    if not text_tokens & TASKBAR_APP_STATUS_CONTEXT_WORDS:
        return False
    if not instruction_tokens & TASKBAR_APP_STATUS_CONTEXT_WORDS:
        return False
    meaningful_tokens = {
        token for token in instruction_tokens if not token.isdigit()
    }
    return bool(
        meaningful_tokens
        and meaningful_tokens
        <= (TASKBAR_APP_STATUS_CONTEXT_WORDS | TASKBAR_APP_GENERIC_REQUEST_WORDS)
    )


def _taskbar_status_label_action_mismatch(
    instruction_tokens: set[str],
    text_tokens: set[str],
    candidate: ControlCandidate,
) -> bool:
    identity_tokens = _taskbar_status_identity_tokens(text_tokens, candidate)
    if not identity_tokens:
        return False
    if instruction_tokens & identity_tokens:
        return False
    overlap = instruction_tokens & text_tokens
    return bool(overlap)


def _taskbar_status_identity_tokens(
    text_tokens: set[str],
    candidate: ControlCandidate,
) -> frozenset[str]:
    automation_id = (candidate.automation_id or "").strip().lower()
    if "widgets" in text_tokens or automation_id == "widgetsbutton":
        return TASKBAR_WIDGET_STATUS_IDENTITY_WORDS
    if text_tokens & {"internet", "network"}:
        return TASKBAR_NETWORK_STATUS_IDENTITY_WORDS
    if text_tokens & {"audio", "realtek", "speakers", "volume"}:
        return TASKBAR_VOLUME_STATUS_IDENTITY_WORDS
    if text_tokens & {"battery", "power"}:
        return TASKBAR_POWER_STATUS_IDENTITY_WORDS
    if "clock" in text_tokens:
        return TASKBAR_CLOCK_STATUS_IDENTITY_WORDS
    if "search" in text_tokens and automation_id == "searchgleambutton":
        return TASKBAR_SEARCH_STATUS_IDENTITY_WORDS
    if "onedrive" in text_tokens:
        return TASKBAR_ONEDRIVE_STATUS_IDENTITY_WORDS
    return frozenset()


def _target_id_ambiguity(
    *,
    instruction: str,
    instruction_tokens: set[str],
    selected: ControlCandidate,
    candidates: list[ControlCandidate],
    model_rect: tuple[int, int, int, int] | None,
    control_intents: set[str],
) -> tuple[bool, float]:
    selected_text = _text_evidence_score(
        instruction_tokens,
        _candidate_semantic_tokens(selected),
    )
    selected_geometry = (
        _geometry_agreement(selected.rect, model_rect) if model_rect is not None else 0.0
    )
    selected_score = selected_text + 0.30 * selected_geometry
    selected_score += _foreground_rank_bonus(selected, candidates)
    closest_gap = 1.0
    selected_has_gmail_tab_evidence = _has_explicit_gmail_tab_evidence(selected)
    for candidate in candidates:
        if candidate is selected or candidate.id == selected.id:
            continue
        if _same_visual_candidate(candidate, selected):
            continue
        if not _candidate_matches_control_intent(candidate, control_intents):
            continue
        if _taskbar_start_button_action_mismatch(instruction_tokens, candidate):
            continue
        if _taskbar_app_state_action_mismatch(instruction_tokens, candidate):
            continue
        if _browser_profile_identity_action_mismatch(instruction_tokens, candidate):
            continue
        if _browser_menu_button_action_mismatch(instruction, candidate):
            continue
        if _browser_address_bar_content_mismatch(
            instruction,
            instruction_tokens,
            candidate,
        ):
            continue
        if _browser_about_blank_title_info_mismatch(
            instruction,
            instruction_tokens,
            candidate,
        ):
            continue
        if _hidden_bookmarks_overflow_action_mismatch(instruction_tokens, candidate):
            continue
        if _close_tab_action_mismatch(instruction, candidate, candidates):
            continue
        if _browser_new_tab_bookmark_action_mismatch(instruction_tokens, candidate):
            continue
        if _browser_extension_access_action_mismatch(
            instruction,
            instruction_tokens,
            candidate,
        ):
            continue
        if _site_information_action_mismatch(instruction_tokens, candidate):
            continue
        if _unnamed_bookmark_generic_route_mismatch(
            instruction,
            instruction_tokens,
            candidate,
        ):
            continue
        if _browser_group_state_action_mismatch(instruction_tokens, candidate):
            continue
        if _disclosure_state_action_mismatch(instruction_tokens, candidate):
            continue
        if _mail_tab_account_reference_mismatch(instruction_tokens, candidate):
            continue
        candidate_tokens = _candidate_semantic_tokens(candidate)
        if _gmail_tab_selected_over_generic_mail_decoy(
            instruction_tokens=instruction_tokens,
            selected_has_gmail_tab_evidence=selected_has_gmail_tab_evidence,
            candidate=candidate,
            candidate_tokens=candidate_tokens,
        ):
            continue
        text_score = _text_evidence_score(instruction_tokens, candidate_tokens)
        if text_score < TARGET_ID_TEXT_FLOOR:
            continue
        geometry = (
            _geometry_agreement(candidate.rect, model_rect) if model_rect is not None else 0.0
        )
        score = text_score + 0.30 * geometry
        score += _foreground_rank_bonus(candidate, candidates)
        gap = selected_score - score
        closest_gap = min(closest_gap, gap)
        if (
            candidate.window_rank < selected.window_rank
            and gap < TARGET_ID_FOREGROUND_CONFLICT_GAP
        ):
            return True, gap
        if model_rect is None and gap < TEXT_MATCH_GAP:
            return True, gap
        if model_rect is not None and gap < TEXT_MATCH_GAP:
            return True, gap
    return False, closest_gap


def _gmail_tab_selected_over_generic_mail_decoy(
    *,
    instruction_tokens: set[str],
    selected_has_gmail_tab_evidence: bool,
    candidate: ControlCandidate,
    candidate_tokens: set[str],
) -> bool:
    if not selected_has_gmail_tab_evidence:
        return False
    if not (instruction_tokens & GMAIL_TAB_REQUEST_WORDS):
        return False
    if _has_explicit_gmail_tab_evidence(candidate):
        return False
    overlap = instruction_tokens & candidate_tokens
    return bool(overlap) and overlap <= GMAIL_TAB_REQUEST_WORDS


def _mail_tab_account_reference_mismatch(
    instruction_tokens: set[str],
    candidate: ControlCandidate,
) -> bool:
    if candidate.control_type != "tabitem":
        return False
    if not (instruction_tokens & GMAIL_TAB_REQUEST_WORDS):
        return False
    if _has_explicit_gmail_tab_evidence(candidate):
        return False
    raw_tokens = _tokens_from_text(candidate.text)
    if raw_tokens & MAIL_TAB_EXPLICIT_WORDS:
        return False
    candidate_tokens = _candidate_semantic_tokens(candidate)
    overlap = instruction_tokens & candidate_tokens
    return bool(overlap) and overlap <= GMAIL_TAB_REQUEST_WORDS


def _browser_tab_auth_action_mismatch(
    instruction_tokens: set[str],
    candidate: ControlCandidate,
) -> bool:
    if candidate.control_type != "tabitem":
        return False
    if not instruction_tokens or not (instruction_tokens & BROWSER_TAB_AUTH_ACTION_WORDS):
        return False
    return instruction_tokens <= BROWSER_TAB_AUTH_ACTION_WORDS


def _has_explicit_gmail_tab_evidence(candidate: ControlCandidate) -> bool:
    if candidate.control_type != "tabitem":
        return False
    text = candidate.text or ""
    tokens = _tokens_from_text(text)
    return "recibidos" in tokens or bool(GMAIL_TAB_SERVICE_RE.search(text))


def _foreground_rank_bonus(
    candidate: ControlCandidate,
    candidates: list[ControlCandidate],
) -> float:
    ranks = {item.window_rank for item in candidates}
    if len(ranks) < 2:
        return 0.0
    return FOREGROUND_RANK_BONUS if candidate.window_rank == min(ranks) else 0.0


def _has_semantic_alternative(
    *,
    instruction: str,
    instruction_tokens: set[str],
    selected: ControlCandidate,
    candidates: list[ControlCandidate],
    control_intents: set[str],
) -> bool:
    if not instruction_tokens:
        return False
    for candidate in candidates:
        if candidate.id == selected.id:
            continue
        if not _candidate_matches_control_intent(candidate, control_intents):
            continue
        if _taskbar_start_button_action_mismatch(instruction_tokens, candidate):
            continue
        if _taskbar_app_state_action_mismatch(instruction_tokens, candidate):
            continue
        if _browser_profile_identity_action_mismatch(instruction_tokens, candidate):
            continue
        if _browser_menu_button_action_mismatch(instruction, candidate):
            continue
        if _browser_address_bar_content_mismatch(
            instruction,
            instruction_tokens,
            candidate,
        ):
            continue
        if _browser_about_blank_title_info_mismatch(
            instruction,
            instruction_tokens,
            candidate,
        ):
            continue
        if _hidden_bookmarks_overflow_action_mismatch(instruction_tokens, candidate):
            continue
        if _close_tab_action_mismatch(instruction, candidate, candidates):
            continue
        if _browser_new_tab_bookmark_action_mismatch(instruction_tokens, candidate):
            continue
        if _browser_extension_access_action_mismatch(
            instruction,
            instruction_tokens,
            candidate,
        ):
            continue
        if _site_information_action_mismatch(instruction_tokens, candidate):
            continue
        if _unnamed_bookmark_generic_route_mismatch(
            instruction,
            instruction_tokens,
            candidate,
        ):
            continue
        if _browser_group_state_action_mismatch(instruction_tokens, candidate):
            continue
        if _disclosure_state_action_mismatch(instruction_tokens, candidate):
            continue
        if _mail_tab_account_reference_mismatch(instruction_tokens, candidate):
            continue
        score = _text_evidence_score(
            instruction_tokens,
            _candidate_semantic_tokens(candidate),
        )
        if score >= TARGET_ID_TEXT_FLOOR:
            return True
    return False


def _has_visible_semantic_alternative(
    *,
    instruction: str,
    instruction_tokens: set[str],
    selected: ControlCandidate,
    candidates: list[ControlCandidate],
    control_intents: set[str],
) -> bool:
    if not instruction_tokens or _candidate_visible_text_tokens(selected):
        return False
    for candidate in candidates:
        if candidate.id == selected.id:
            continue
        if not _candidate_matches_control_intent(candidate, control_intents):
            continue
        if _taskbar_start_button_action_mismatch(instruction_tokens, candidate):
            continue
        if _taskbar_app_state_action_mismatch(instruction_tokens, candidate):
            continue
        if _browser_profile_identity_action_mismatch(instruction_tokens, candidate):
            continue
        if _browser_menu_button_action_mismatch(instruction, candidate):
            continue
        if _browser_address_bar_content_mismatch(
            instruction,
            instruction_tokens,
            candidate,
        ):
            continue
        if _browser_about_blank_title_info_mismatch(
            instruction,
            instruction_tokens,
            candidate,
        ):
            continue
        if _hidden_bookmarks_overflow_action_mismatch(instruction_tokens, candidate):
            continue
        if _close_tab_action_mismatch(instruction, candidate, candidates):
            continue
        if _browser_new_tab_bookmark_action_mismatch(instruction_tokens, candidate):
            continue
        if _browser_extension_access_action_mismatch(
            instruction,
            instruction_tokens,
            candidate,
        ):
            continue
        if _site_information_action_mismatch(instruction_tokens, candidate):
            continue
        if _unnamed_bookmark_generic_route_mismatch(
            instruction,
            instruction_tokens,
            candidate,
        ):
            continue
        if _browser_group_state_action_mismatch(instruction_tokens, candidate):
            continue
        if _disclosure_state_action_mismatch(instruction_tokens, candidate):
            continue
        if _mail_tab_account_reference_mismatch(instruction_tokens, candidate):
            continue
        visible_tokens = _candidate_visible_text_tokens(candidate)
        if not visible_tokens:
            continue
        if _text_evidence_score(instruction_tokens, visible_tokens) >= TARGET_ID_TEXT_FLOOR:
            return True
    return False


def _candidate_snap_score(
    *,
    candidate: ControlCandidate,
    candidates: list[ControlCandidate],
    instruction: str,
    instruction_tokens: set[str],
    control_intents: set[str],
    model_rect: tuple[int, int, int, int],
) -> float:
    iou = _iou(candidate.rect, model_rect)
    proximity = _proximity_score(candidate.rect, model_rect)
    semantic_tokens = _candidate_semantic_tokens(candidate)
    text_score = _text_evidence_score(instruction_tokens, semantic_tokens)
    if _taskbar_start_button_action_mismatch(instruction_tokens, candidate):
        return min(0.41, 0.45 * iou + 0.30 * proximity)
    if _taskbar_task_view_action_mismatch(instruction, instruction_tokens, candidate):
        return min(0.41, 0.45 * iou + 0.30 * proximity)
    if _taskbar_app_state_action_mismatch(instruction_tokens, candidate):
        return 0.0
    if _browser_profile_identity_action_mismatch(instruction_tokens, candidate):
        return 0.0
    if _browser_menu_button_action_mismatch(instruction, candidate):
        return min(0.41, 0.45 * iou + 0.30 * proximity)
    if _browser_address_bar_content_mismatch(
        instruction,
        instruction_tokens,
        candidate,
    ):
        return 0.0
    if _browser_about_blank_title_info_mismatch(
        instruction,
        instruction_tokens,
        candidate,
    ):
        return 0.0
    if _hidden_bookmarks_overflow_action_mismatch(instruction_tokens, candidate):
        return 0.0
    if _close_tab_action_mismatch(instruction, candidate, candidates):
        return 0.0
    if _browser_new_tab_bookmark_action_mismatch(instruction_tokens, candidate):
        return 0.0
    if _browser_extension_access_action_mismatch(
        instruction,
        instruction_tokens,
        candidate,
    ):
        return 0.0
    if _site_information_action_mismatch(instruction_tokens, candidate):
        return 0.0
    if _unnamed_bookmark_generic_route_mismatch(
        instruction,
        instruction_tokens,
        candidate,
    ):
        return 0.0
    if _browser_group_state_action_mismatch(instruction_tokens, candidate):
        return 0.0
    if _disclosure_state_action_mismatch(instruction_tokens, candidate):
        return min(0.41, 0.45 * iou + 0.30 * proximity)
    if _mail_tab_account_reference_mismatch(instruction_tokens, candidate):
        return min(0.41, 0.45 * iou + 0.30 * proximity)
    if _browser_tab_auth_action_mismatch(instruction_tokens, candidate):
        return min(0.41, 0.45 * iou + 0.30 * proximity)
    if (
        control_intents
        and not _candidate_matches_control_intent(candidate, control_intents)
    ):
        return 0.0
    if not semantic_tokens and _has_nearby_unlabeled_competitor(candidate, candidates):
        return 0.0
    if _has_visible_semantic_alternative(
        instruction=instruction,
        instruction_tokens=instruction_tokens,
        selected=candidate,
        candidates=candidates,
        control_intents=control_intents,
    ):
        return 0.0
    if instruction_tokens and semantic_tokens and text_score <= 0:
        return min(0.41, 0.45 * iou + 0.30 * proximity)
    area_score = _area_fit_score(candidate.rect, model_rect)
    type_score = 1.0 if candidate.control_type in {"button", "menuitem", "tabitem", "hyperlink", "splitbutton"} else 0.7
    score = (
        0.34 * iou
        + 0.24 * proximity
        + 0.20 * text_score
        + 0.14 * area_score
        + 0.08 * type_score
    )
    final_score = score + _foreground_rank_bonus(candidate, candidates)
    if _menu_segment_intent(control_intents) and candidate.control_type == "splitbutton":
        if not _contains_tighter_same_intent_action(
            selected=candidate,
            candidates=candidates,
            instruction_tokens=instruction_tokens,
            control_intents=control_intents,
        ):
            return 0.0
        return min(final_score, CONTAINING_ROW_SNAP_CAP)
    if _contains_tighter_same_intent_action(
        selected=candidate,
        candidates=candidates,
        instruction_tokens=instruction_tokens,
        control_intents=control_intents,
    ):
        final_score = min(final_score, CONTAINING_ROW_SNAP_CAP)
    return min(1.0, final_score)


def _contains_tighter_same_intent_action(
    *,
    selected: ControlCandidate,
    candidates: list[ControlCandidate],
    instruction_tokens: set[str],
    control_intents: set[str],
) -> bool:
    if (
        selected.control_type not in ROW_LIKE_CONTROL_TYPES
        and selected.control_type not in COMPOSITE_ACTION_CONTROL_TYPES
    ):
        return False
    selected_area = selected.rect[2] * selected.rect[3]
    for candidate in candidates:
        if candidate.id == selected.id:
            continue
        if candidate.control_type not in TIGHT_ACTION_CONTROL_TYPES:
            continue
        candidate_area = candidate.rect[2] * candidate.rect[3]
        if selected_area < candidate_area * 1.8:
            continue
        if not _contains_rect(selected.rect, candidate.rect):
            continue
        if not instruction_tokens:
            if candidate.control_type in control_intents:
                return True
            if _candidate_matches_control_intent(selected, control_intents):
                return False
            if control_intents:
                return False
            return True
        if (
            control_intents
            and candidate.control_type in control_intents
            and not _candidate_matches_control_intent(selected, control_intents)
        ):
            if _taskbar_start_button_action_mismatch(instruction_tokens, candidate):
                continue
            if _taskbar_app_state_action_mismatch(instruction_tokens, candidate):
                continue
            if _disclosure_state_action_mismatch(instruction_tokens, candidate):
                continue
            if _mail_tab_account_reference_mismatch(instruction_tokens, candidate):
                continue
            candidate_tokens = _candidate_visible_text_tokens(candidate)
            if not candidate_tokens:
                return True
            if _text_evidence_score(instruction_tokens, candidate_tokens) >= TARGET_ID_TEXT_FLOOR:
                return True
        if _taskbar_start_button_action_mismatch(instruction_tokens, candidate):
            continue
        if _taskbar_app_state_action_mismatch(instruction_tokens, candidate):
            continue
        if _disclosure_state_action_mismatch(instruction_tokens, candidate):
            continue
        if _mail_tab_account_reference_mismatch(instruction_tokens, candidate):
            continue
        candidate_tokens = _candidate_visible_text_tokens(candidate)
        if not candidate_tokens:
            continue
        if _text_evidence_score(instruction_tokens, candidate_tokens) >= TARGET_ID_TEXT_FLOOR:
            return True
    return False


def _single_contained_control_intent_candidate(
    *,
    candidates: list[ControlCandidate],
    model_rect: tuple[int, int, int, int],
    instruction: str,
    instruction_tokens: set[str],
    control_intents: set[str],
) -> ControlCandidate | None:
    if not control_intents:
        return None
    bounds = _expand_rect(model_rect, 4)
    contained: list[ControlCandidate] = []
    for candidate in candidates:
        if _menu_segment_intent(control_intents) and candidate.control_type == "splitbutton":
            continue
        if not _candidate_matches_control_intent(candidate, control_intents):
            continue
        if not _contains_rect(bounds, candidate.rect):
            continue
        if _browser_menu_button_action_mismatch(instruction, candidate):
            continue
        if _close_tab_action_mismatch(instruction, candidate, candidates):
            continue
        if _taskbar_start_button_action_mismatch(instruction_tokens, candidate):
            continue
        if _taskbar_app_state_action_mismatch(instruction_tokens, candidate):
            continue
        if _disclosure_state_action_mismatch(instruction_tokens, candidate):
            continue
        if _mail_tab_account_reference_mismatch(instruction_tokens, candidate):
            continue
        if instruction_tokens and not _contained_control_intent_has_evidence(
            candidate=candidate,
            candidates=candidates,
            model_rect=model_rect,
            instruction_tokens=instruction_tokens,
        ):
            continue
        if any(_same_visual_candidate(candidate, existing) for existing in contained):
            continue
        contained.append(candidate)
        if len(contained) > 1:
            return None
    return contained[0] if contained else None


def _contained_control_intent_has_evidence(
    *,
    candidate: ControlCandidate,
    candidates: list[ControlCandidate],
    model_rect: tuple[int, int, int, int],
    instruction_tokens: set[str],
) -> bool:
    evidence_tokens = set(_candidate_semantic_tokens(candidate))
    for context in candidates:
        if context.id == candidate.id or _same_visual_candidate(context, candidate):
            continue
        if not _contains_rect(_expand_rect(context.rect, 4), candidate.rect):
            continue
        if _geometry_agreement(context.rect, model_rect) < TARGET_ID_GEOMETRY_FLOOR:
            continue
        evidence_tokens.update(_candidate_semantic_tokens(context))
        evidence_tokens.update(_expand_token_aliases(_tokens_from_text(context.window_title)))
    return _text_evidence_score(instruction_tokens, evidence_tokens) >= TARGET_ID_TEXT_FLOOR


def _candidate_matches_control_intent(
    candidate: ControlCandidate,
    control_intents: set[str],
) -> bool:
    if _control_type_matches_intent(candidate.control_type, control_intents):
        return True
    if (
        control_intents & BROWSER_MENU_CONTROL_INTENTS
        and (
            _looks_like_browser_menu_button(candidate)
            or _looks_like_hidden_bookmarks_overflow_button(candidate)
        )
    ):
        return True
    if _looks_like_taskbar_start_button(candidate) and control_intents & {
        "menuitem",
        "splitbutton",
    }:
        return True
    return False


def _foreground_snap_conflict(
    *,
    ranked: list[tuple[float, ControlCandidate]],
    instruction_tokens: set[str],
    confidence_floor: float,
) -> bool:
    best_score, best = ranked[0]
    min_rank = min(candidate.window_rank for _score, candidate in ranked)
    if best.window_rank == min_rank:
        return False

    foreground: tuple[float, ControlCandidate] | None = None
    for score, candidate in ranked:
        if candidate.window_rank != min_rank:
            continue
        if _same_visual_candidate(best, candidate):
            continue
        if score < confidence_floor:
            continue
        if not _same_snap_intent(best, candidate, instruction_tokens):
            continue
        if foreground is None or score > foreground[0]:
            foreground = (score, candidate)
    if foreground is None:
        return False
    return best_score - foreground[0] < FOREGROUND_SNAP_CONFLICT_GAP


def _same_snap_intent(
    first: ControlCandidate,
    second: ControlCandidate,
    instruction_tokens: set[str],
) -> bool:
    if not instruction_tokens:
        return True
    if _taskbar_start_button_action_mismatch(instruction_tokens, first):
        return False
    if _taskbar_start_button_action_mismatch(instruction_tokens, second):
        return False
    if _taskbar_app_state_action_mismatch(instruction_tokens, first):
        return False
    if _taskbar_app_state_action_mismatch(instruction_tokens, second):
        return False
    if _disclosure_state_action_mismatch(instruction_tokens, first):
        return False
    if _disclosure_state_action_mismatch(instruction_tokens, second):
        return False
    if _mail_tab_account_reference_mismatch(instruction_tokens, first):
        return False
    if _mail_tab_account_reference_mismatch(instruction_tokens, second):
        return False
    first_score = _text_evidence_score(
        instruction_tokens,
        _candidate_semantic_tokens(first),
    )
    second_score = _text_evidence_score(
        instruction_tokens,
        _candidate_semantic_tokens(second),
    )
    if first_score <= 0 and second_score <= 0:
        return True
    return second_score >= first_score - TEXT_MATCH_GAP


def _candidate_snap_semantic_mismatch(
    *,
    candidate: ControlCandidate,
    candidates: list[ControlCandidate],
    instruction: str,
    instruction_tokens: set[str],
    model_rect: tuple[int, int, int, int],
) -> bool:
    semantic_tokens = _candidate_semantic_tokens(candidate)
    if _browser_menu_button_action_mismatch(instruction, candidate):
        return True
    if not instruction_tokens or not semantic_tokens:
        return False
    if _taskbar_start_button_action_mismatch(instruction_tokens, candidate):
        return True
    if _taskbar_task_view_action_mismatch(instruction, instruction_tokens, candidate):
        return True
    if _taskbar_app_state_action_mismatch(instruction_tokens, candidate):
        return True
    if _browser_profile_identity_action_mismatch(instruction_tokens, candidate):
        return True
    if _browser_menu_button_action_mismatch(instruction, candidate):
        return True
    if _browser_address_bar_content_mismatch(
        instruction,
        instruction_tokens,
        candidate,
    ):
        return True
    if _browser_about_blank_title_info_mismatch(
        instruction,
        instruction_tokens,
        candidate,
    ):
        return True
    if _hidden_bookmarks_overflow_action_mismatch(instruction_tokens, candidate):
        return True
    if _close_tab_action_mismatch(instruction, candidate, candidates):
        return True
    if _browser_new_tab_bookmark_action_mismatch(instruction_tokens, candidate):
        return True
    if _browser_extension_access_action_mismatch(
        instruction,
        instruction_tokens,
        candidate,
    ):
        return True
    if _site_information_action_mismatch(instruction_tokens, candidate):
        return True
    if _unnamed_bookmark_generic_route_mismatch(
        instruction,
        instruction_tokens,
        candidate,
    ):
        return True
    if _browser_group_state_action_mismatch(instruction_tokens, candidate):
        return True
    if _disclosure_state_action_mismatch(instruction_tokens, candidate):
        return True
    if _mail_tab_account_reference_mismatch(instruction_tokens, candidate):
        return True
    if _browser_tab_auth_action_mismatch(instruction_tokens, candidate):
        return True
    if _text_evidence_score(instruction_tokens, semantic_tokens) > 0:
        return False
    return _geometry_agreement(candidate.rect, model_rect) >= TARGET_ID_GEOMETRY_FLOOR


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


def _geometry_agreement(
    candidate_rect: tuple[int, int, int, int],
    model_rect: tuple[int, int, int, int] | None,
) -> float:
    if model_rect is None:
        return 0.0
    iou = _iou(candidate_rect, model_rect)
    proximity = _proximity_score(candidate_rect, model_rect)
    contains = 1.0 if _center_inside(candidate_rect, _expand_rect(model_rect, 24)) else 0.0
    return min(1.0, 0.50 * iou + 0.35 * proximity + 0.15 * contains)


def _has_nearby_unlabeled_competitor(
    selected: ControlCandidate,
    candidates: list[ControlCandidate],
) -> bool:
    if _candidate_visible_text_tokens(selected):
        return False
    search_rect = _expand_rect(selected.rect, UNLABELED_COMPETITOR_MARGIN_PX)
    for candidate in candidates:
        if candidate.id == selected.id:
            continue
        if candidate.control_type != selected.control_type:
            continue
        if _candidate_visible_text_tokens(candidate):
            continue
        if _intersects(candidate.rect, search_rect):
            return True
    return False


def _proximity_score(
    a: tuple[int, int, int, int],
    b: tuple[int, int, int, int],
) -> float:
    ax, ay = _center(a)
    bx, by = _center(b)
    diagonal = max(1.0, (b[2] * b[2] + b[3] * b[3]) ** 0.5)
    distance = ((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5
    return max(0.0, 1.0 - min(1.0, distance / (diagonal * 4.0)))


def _area_fit_score(
    a: tuple[int, int, int, int],
    b: tuple[int, int, int, int],
) -> float:
    area_a = max(1, a[2] * a[3])
    area_b = max(1, b[2] * b[3])
    ratio = min(area_a, area_b) / max(area_a, area_b)
    return max(0.0, min(1.0, ratio))


def _iou(
    a: tuple[int, int, int, int],
    b: tuple[int, int, int, int],
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
    rect: tuple[int, int, int, int],
    margin: int,
) -> tuple[int, int, int, int]:
    x, y, width, height = rect
    return (x - margin, y - margin, width + margin * 2, height + margin * 2)


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


def _contains_rect(
    outer: tuple[int, int, int, int],
    inner: tuple[int, int, int, int],
) -> bool:
    ox, oy, ow, oh = outer
    ix, iy, iw, ih = inner
    return ox <= ix and oy <= iy and ox + ow >= ix + iw and oy + oh >= iy + ih


def _center_inside(
    rect: tuple[int, int, int, int],
    bounds: tuple[int, int, int, int],
) -> bool:
    cx, cy = _center(rect)
    bx, by, bw, bh = bounds
    return bx <= cx < bx + bw and by <= cy < by + bh


def _norm_rect(
    rect: tuple[int, int, int, int],
    capture: Capture,
) -> tuple[int, int, int, int]:
    capture_rect = _capture_screen_rect(capture)
    clipped = _intersection_rect(rect, capture_rect) or rect
    rect = clipped
    x, y, width, height = rect
    left = int((x - capture.monitor_left) * capture.scale / max(1, capture.width) * 1000)
    top = int((y - capture.monitor_top) * capture.scale / max(1, capture.height) * 1000)
    norm_width = int(width * capture.scale / max(1, capture.width) * 1000)
    norm_height = int(height * capture.scale / max(1, capture.height) * 1000)
    return (
        _clamp_norm(left),
        _clamp_norm(top),
        max(1, min(1000, norm_width)),
        max(1, min(1000, norm_height)),
    )


def _clamp_norm(value: int) -> int:
    return max(0, min(1000, int(value)))


def _intersects(
    a: tuple[int, int, int, int],
    b: tuple[int, int, int, int],
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
    x, y, width, height = rect
    return (x + width // 2, y + height // 2)


def _clip(value: str, limit: int) -> str:
    value = " ".join((value or "").split())
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 1)].rstrip() + "..."
