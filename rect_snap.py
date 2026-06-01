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

from help_intents import (
    instruction_control_intents as _instruction_control_intents,
    menu_segment_intent as _menu_segment_intent,
    tokenize_control as _tokenize_control,
    tokenize_instruction as _tokenize_instruction,
    tokens_from_text as _tokens_from_text,
)

log = logging.getLogger("helper.rect_snap")

DEFAULT_TIMEOUT_MS = 400
SEARCH_MARGIN_PX = 60
CONFIDENCE_FLOOR = 0.42
SEMANTIC_MISMATCH_CAP = 0.41
SEMANTIC_MISMATCH_IOU_FLOOR = 0.65
FOREGROUND_RANK_BONUS = 0.10
FOREGROUND_SNAP_CONFLICT_GAP = 0.35
MIN_TOPMOST_SAMPLE_FRACTION = 0.50
CLEAR_CLOSE_WORDS = frozenset({"cancel", "close", "dismiss"})
CLEAR_CONTEXT_WORDS = frozenset(
    {"field", "filter", "find", "input", "query", "search", "text", "textbox"}
)
CLOSE_CONTEXT_TARGET_WORDS = frozenset(
    {
        "banner",
        "bar",
        "card",
        "drawer",
        "menu",
        "modal",
        "notification",
        "panel",
        "pane",
        "popover",
        "popup",
        "section",
        "sidebar",
        "toast",
        "toolbar",
    }
)
X_SYMBOL_TEXTS = frozenset({"x", "\u00d7", "\u2715", "\u2716"})
PASSWORD_VISIBILITY_CONTEXT_WORDS = frozenset({"passcode", "password"})
PASSWORD_VISIBILITY_SHOW_WORDS = frozenset({"reveal", "show", "unmask"})
PASSWORD_VISIBILITY_HIDE_WORDS = frozenset({"conceal", "hide", "mask"})
AUDIO_OUTPUT_CONTEXT_WORDS = frozenset({"audio", "sound", "speaker", "speakers", "volume"})
AUDIO_OUTPUT_UP_WORDS = frozenset({"increase", "louder", "raise", "up"})
AUDIO_OUTPUT_DOWN_WORDS = frozenset({"decrease", "down", "lower", "quieter"})
HISTORY_UNDO_WORDS = frozenset({"undo"})
HISTORY_REDO_WORDS = frozenset({"redo"})
CHECKBOX_ON_ACTION_WORDS = frozenset({"check", "enable", "tick"})
CHECKBOX_OFF_ACTION_WORDS = frozenset({"disable", "uncheck", "untick"})
NAVIGATION_DIRECTION_WORDS = frozenset({"back", "forward", "next", "previous"})
NAVIGATION_BACK_WORDS = frozenset({"back", "previous"})
BACKUP_ACTION_WORDS = frozenset({"backup", "sync", "synced", "up"})
MEDIA_TRANSPORT_CONTEXT_WORDS = frozenset(
    {"audio", "clip", "media", "movie", "music", "playback", "song", "track", "video"}
)
EDIT_ACTION_WORDS = frozenset({"edit", "pencil"})
CONFIRM_ACTION_WORDS = frozenset(
    {"apply", "checkmark", "complete", "confirm", "done", "finish", "ok", "okay", "tick"}
)
CANCEL_ACTION_WORDS = frozenset({"cancel"})
CONFIRM_CANCEL_ACTION_WORDS = CONFIRM_ACTION_WORDS | CANCEL_ACTION_WORDS
CONFIRM_OBJECT_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "button",
        "check",
        "control",
        "mark",
        "selected",
        "selection",
        "the",
        "this",
        "that",
    }
)
ACTION_OBJECT_STOPWORDS = CONFIRM_OBJECT_STOPWORDS | frozenset(
    {
        "current",
        "data",
        "document",
        "documents",
        "entry",
        "entries",
        "file",
        "files",
        "icon",
        "item",
        "items",
        "paper",
        "record",
        "records",
        "row",
        "rows",
        "select",
    }
)
FILE_IDENTITY_WORDS = frozenset({"document", "documents", "file", "files"})
FILE_OPEN_ACTION_WORDS = frozenset({"open"})
FILE_SAVE_ACTION_WORDS = frozenset({"disk", "floppy", "save"})
FILE_EXPORT_ACTION_WORDS = frozenset({"download", "export"})
FILE_PICKER_ACTION_WORDS = frozenset(
    {"attach", "attachment", "browse", "choose", "paperclip", "picker", "select", "upload"}
)
FILE_IMPORT_ACTION_WORDS = frozenset({"import", "upload"})
BROWSER_TAB_WORDS = frozenset({"tab", "tabs", "tabitem"})
BROWSER_WINDOW_WORDS = frozenset({"window", "windows"})
CONTEXTUAL_NAV_ITEM_CONTAINER_WORDS = frozenset({"drawer", "nav", "navigation", "sidebar"})
GENERIC_VISIBILITY_SHOW_WORDS = frozenset({"show"})
GENERIC_VISIBILITY_HIDE_WORDS = frozenset({"hide"})
GENERIC_VISIBILITY_ACTION_WORDS = GENERIC_VISIBILITY_SHOW_WORDS | GENERIC_VISIBILITY_HIDE_WORDS
REVERSIBLE_ACTION_POLARITY_PAIRS = (
    (frozenset({"archive"}), frozenset({"unarchive"})),
    (frozenset({"connect"}), frozenset({"disconnect"})),
    (frozenset({"lock"}), frozenset({"unlock"})),
    (frozenset({"mute"}), frozenset({"unmute"})),
    (frozenset({"open"}), frozenset({"close"})),
    (frozenset({"select"}), frozenset({"deselect"})),
    (frozenset({"start"}), frozenset({"stop"})),
    (frozenset({"subscribe"}), frozenset({"unsubscribe"})),
)
REVERSIBLE_ACTION_POLARITY_WORDS = frozenset(
    word for pair in REVERSIBLE_ACTION_POLARITY_PAIRS for words in pair for word in words
)
TURN_ON_RE = re.compile(r"\bturn\s+on\b", re.IGNORECASE)
TURN_OFF_RE = re.compile(r"\bturn\s+off\b", re.IGNORECASE)
STATE_LABEL_ACTION_FAMILIES = (
    (frozenset({"add"}), frozenset({"added"})),
    (frozenset({"enable", "check", "tick"}), frozenset({"checked", "enabled"})),
    (frozenset({"disable", "uncheck", "untick"}), frozenset({"disabled", "unchecked"})),
    (frozenset({"apply"}), frozenset({"applied"})),
    (frozenset({"attach"}), frozenset({"attached"})),
    (frozenset({"cancel"}), frozenset({"canceled", "cancelled"})),
    (frozenset({"confirm"}), frozenset({"confirmed"})),
    (frozenset({"complete"}), frozenset({"completed"})),
    (frozenset({"create"}), frozenset({"created"})),
    (frozenset({"delete", "remove"}), frozenset({"deleted", "removed"})),
    (frozenset({"download", "export"}), frozenset({"downloaded", "exported"})),
    (frozenset({"dismiss"}), frozenset({"dismissed"})),
    (frozenset({"finish"}), frozenset({"finished"})),
    (frozenset({"fix"}), frozenset({"fixed"})),
    (frozenset({"import", "upload"}), frozenset({"imported", "uploaded"})),
    (frozenset({"install"}), frozenset({"installed"})),
    (frozenset({"invite"}), frozenset({"invited"})),
    (frozenset({"mute"}), frozenset({"muted"})),
    (frozenset({"unmute"}), frozenset({"unmuted"})),
    (frozenset({"save"}), frozenset({"saved"})),
    (frozenset({"send", "submit"}), frozenset({"delivered", "sent", "submitted"})),
    (frozenset({"share"}), frozenset({"shared"})),
    (frozenset({"resolve"}), frozenset({"resolved"})),
    (frozenset({"show"}), frozenset({"shown", "visible"})),
    (frozenset({"hide"}), frozenset({"hidden"})),
    (frozenset({"update"}), frozenset({"updated"})),
    (frozenset({"expand"}), frozenset({"expanded"})),
    (frozenset({"collapse"}), frozenset({"collapsed"})),
    (frozenset({"lock"}), frozenset({"locked"})),
    (frozenset({"unlock"}), frozenset({"unlocked"})),
    (frozenset({"connect"}), frozenset({"connected"})),
    (frozenset({"disconnect"}), frozenset({"disconnected"})),
    (frozenset({"archive"}), frozenset({"archived"})),
    (frozenset({"unarchive"}), frozenset({"unarchived"})),
    (frozenset({"select"}), frozenset({"selected"})),
    (frozenset({"deselect"}), frozenset({"deselected", "unselected"})),
    (frozenset({"start"}), frozenset({"running", "started"})),
    (frozenset({"stop"}), frozenset({"stopped"})),
    (frozenset({"subscribe"}), frozenset({"subscribed"})),
    (frozenset({"unsubscribe"}), frozenset({"unsubscribed"})),
    (frozenset({"open"}), frozenset({"opened"})),
    (frozenset({"close"}), frozenset({"closed"})),
)
STATE_LABEL_ACTION_GROUPS = (
    (
        frozenset({"check", "disable", "enable", "tick", "uncheck", "untick"}),
        frozenset({"checked", "disabled", "enabled", "unchecked"}),
    ),
    (
        frozenset({"apply", "complete", "confirm", "done", "finish", "ok", "okay"}),
        frozenset({"applied", "completed", "confirmed", "finished", "status"}),
    ),
    (frozenset({"add", "create"}), frozenset({"added", "created"})),
    (frozenset({"attach", "import", "upload"}), frozenset({"attached", "imported", "uploaded"})),
    (frozenset({"cancel"}), frozenset({"canceled", "cancelled"})),
    (frozenset({"delete", "remove"}), frozenset({"deleted", "removed"})),
    (frozenset({"dismiss"}), frozenset({"dismissed"})),
    (frozenset({"download", "export"}), frozenset({"downloaded", "exported"})),
    (frozenset({"fix"}), frozenset({"fixed"})),
    (frozenset({"install", "update"}), frozenset({"installed", "updated"})),
    (frozenset({"invite"}), frozenset({"invited"})),
    (frozenset({"save"}), frozenset({"saved"})),
    (frozenset({"send", "submit"}), frozenset({"delivered", "sent", "submitted"})),
    (frozenset({"share"}), frozenset({"shared"})),
    (frozenset({"resolve"}), frozenset({"resolved"})),
    (frozenset({"mute", "unmute"}), frozenset({"muted", "unmuted"})),
    (frozenset({"show", "hide"}), frozenset({"hidden", "shown", "visible"})),
    (frozenset({"expand", "collapse"}), frozenset({"collapsed", "expanded"})),
    (frozenset({"lock", "unlock"}), frozenset({"locked", "unlocked"})),
    (frozenset({"connect", "disconnect"}), frozenset({"connected", "disconnected"})),
    (frozenset({"archive", "unarchive"}), frozenset({"archived", "unarchived"})),
    (frozenset({"select", "deselect"}), frozenset({"deselected", "selected", "unselected"})),
    (frozenset({"start", "stop"}), frozenset({"running", "started", "stopped"})),
    (frozenset({"subscribe", "unsubscribe"}), frozenset({"subscribed", "unsubscribed"})),
    (frozenset({"open", "close"}), frozenset({"closed", "opened"})),
    (frozenset({"approve", "reject"}), frozenset({"approved", "rejected"})),
    (frozenset({"mark", "read", "unread"}), frozenset({"read", "unread"})),
)
STATE_LABEL_TURN_ON_WORDS = frozenset({"checked", "enabled"})
STATE_LABEL_TURN_OFF_WORDS = frozenset({"disabled", "unchecked"})
SEARCH_ACTION_WORDS = frozenset({"find", "search"})
SEARCH_RESULTS_LABEL_WORDS = frozenset({"result", "results"})
WINDOW_CONTEXT_OBJECT_WORDS = frozenset(
    {
        "account",
        "accounts",
        "address",
        "addresses",
        "chat",
        "chats",
        "coupon",
        "coupons",
        "email",
        "emails",
        "image",
        "images",
        "inbox",
        "invoice",
        "invoices",
        "mail",
        "message",
        "messages",
        "notification",
        "notifications",
        "photo",
        "photos",
        "profile",
        "profiles",
        "project",
        "projects",
        "report",
        "reports",
        "settings",
        "user",
        "users",
    }
)
ACTION_CONTEXT_OBJECT_WORDS = WINDOW_CONTEXT_OBJECT_WORDS | frozenset(
    {
        "card",
        "cards",
        "dialog",
        "dialogs",
        "drawer",
        "drawers",
        "form",
        "forms",
        "grid",
        "grids",
        "modal",
        "modals",
        "page",
        "pages",
        "pane",
        "panes",
        "panel",
        "panels",
        "section",
        "sections",
        "sidebar",
        "sidebars",
        "table",
        "tables",
        "toolbar",
        "toolbars",
        "view",
        "views",
        "window",
        "windows",
    }
)
EXCLUSIVE_ACTION_FAMILIES = (
    frozenset({"plane", "send", "submit"}),
    frozenset({"bin", "delete", "remove", "trash", "wastebasket"}),
    frozenset({"disk", "floppy", "save"}),
    frozenset({"archive", "cabinet", "filing"}),
    frozenset({"download", "export"}),
    frozenset({"attach", "attachment", "browse", "choose", "import", "paperclip", "upload"}),
    frozenset({"clone", "copy", "duplicate"}),
    frozenset({"clipboard", "paste"}),
    frozenset({"edit", "pencil"}),
    frozenset({"print", "printer"}),
    frozenset({"share"}),
)
DISCLOSURE_EXPAND_ACTION_WORDS = frozenset({"expand"})
DISCLOSURE_COLLAPSE_ACTION_WORDS = frozenset({"collapse"})
PIN_STATE_NEUTRAL_WORDS = frozenset({"pinned", "pushpin", "thumbtack"})
START_BUTTON_ALLOWED_TOKENS = frozenset({"start", "windows"})
TASKBAR_WINDOW_WORDS = frozenset({"taskbar"})
TASKBAR_APP_STATE_CONTEXT_WORDS = frozenset(
    {"pinned", "running", "window", "windows"}
)
TASKBAR_SEARCH_STATUS_IDENTITY_WORDS = frozenset({"find", "search"})
TASKBAR_SEARCH_STATUS_SEPARATOR_ALIAS_WORDS = frozenset(
    {"minimize", "minus", "zoom_out"}
)
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
TASKBAR_GENERIC_FILE_IDENTITY_WORDS = frozenset({"file", "files"})
BROWSER_APP_IDENTITY_WORDS = frozenset({"brave", "browser", "chrome", "edge", "google"})
BROWSER_PROFILE_ACTION_CONTEXT_WORDS = frozenset({"edit", "pencil"})
BROWSER_PROFILE_LABEL_HINT_WORDS = frozenset({"all"})
BROWSER_PROFILE_TOKENS = frozenset({"account", "avatar", "person", "profile", "user"})
BROWSER_PAGE_TARGET_WORDS = frozenset({"page", "webpage"})
BROWSER_PROFILE_MAX_EDGE = 64
BROWSER_PROFILE_MAX_ASPECT = 1.75
BROWSER_ADDRESS_BAR_ROLE_WORDS = frozenset(
    {"address", "bar", "location", "omnibox", "search", "url"}
)
BROWSER_ADDRESS_BAR_REQUEST_WORDS = frozenset(
    {"address", "find", "location", "omnibox", "search", "url"}
)
BROWSER_TAB_AUTH_ACTION_WORDS = frozenset({"log", "login", "sign", "signin"})
BROWSER_TAB_GENERIC_SECTION_WORDS = frozenset(
    {"download", "downloads", "home", "house", "options", "overview", "preferences", "settings"}
)
SITE_INFORMATION_REQUEST_WORDS = frozenset(
    {"about", "details", "info", "information", "lock", "padlock", "site_info_lock"}
)
BROWSER_ABOUT_BLANK_TARGET_WORDS = frozenset({"blank", "tab", "tabitem"})
TASKBAR_HIDDEN_ICONS_REQUEST_WORDS = frozenset(
    {"notification_area", "system_tray", "tray"}
)
TASKBAR_SHOW_DESKTOP_REQUEST_WORDS = frozenset({"show_desktop"})
PROGRAM_MANAGER_WINDOW_WORDS = frozenset({"manager", "program"})
PROGRAM_MANAGER_SPOTLIGHT_REQUEST_WORDS = frozenset(
    {"background", "image", "learn", "photo", "picture", "spotlight", "wallpaper"}
)
PROGRAM_MANAGER_ABOUT_WORDS = frozenset({"about", "details", "info", "information"})
PROGRAM_MANAGER_NEW_ACTION_WORDS = frozenset({"add", "create", "new", "plus"})
PROGRAM_MANAGER_GENERIC_NAME_WORDS = frozenset(
    {
        "ai",
        "app",
        "apps",
        "application",
        "applications",
        "dev",
        "installer",
        "launcher",
        "main",
        "source",
        "system",
    }
)
BROWSER_PROFILE_WINDOW_WORDS = frozenset({"brave", "browser", "chrome", "edge"})
BROWSER_NEW_TAB_WORDS = frozenset({"new_tab"})
BROWSER_NEW_TAB_GENERIC_WORDS = frozenset({"add", "create", "new", "plus"})
BROWSER_NEW_TAB_RELATED_REQUEST_WORDS = (
    BROWSER_NEW_TAB_GENERIC_WORDS
    | BROWSER_NEW_TAB_WORDS
    | frozenset({"external", "new_window", "open_new"})
)
BROWSER_EXTENSION_ACCESS_CONTEXT_WORDS = frozenset({"access", "site"})
BROWSER_EXTENSION_ACCESS_LABEL_STOPWORDS = frozenset(
    {"access", "button", "control", "extension", "has", "open", "site", "this", "to", "wants"}
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
BROWSER_BOOKMARK_ACTION_WORDS = frozenset({"bookmark", "favorite", "star"})
BROWSER_BOOKMARK_TAB_CONTEXT_WORDS = frozenset(
    {"page", "pages", "tab", "tabs", "webpage", "website"}
)
BROWSER_BOOKMARK_ITEM_CONTEXT_WORDS = frozenset(
    {"article", "card", "item", "items", "listing", "post", "product", "record", "row"}
)
BROWSER_TAB_MEMORY_USAGE_RE = re.compile(
    r"(?:\s*[\-\|\u2013\u2014]\s*)?memory\s+usage\s*[-:]\s*\d+(?:\.\d+)?\s*mb\b.*$",
    re.IGNORECASE,
)
BROWSER_TAB_OWNER_ACCOUNT_RE = re.compile(
    r"\s*[\-\|\u2013\u2014]\s*[^|\u2013\u2014-]*@[^|\u2013\u2014-]*['\u2019]s\s+account(?=\s*[\-\|\u2013\u2014]|$)",
    re.IGNORECASE,
)

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
    semantic_action_mismatch_result: SnapResult | None = None
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
        window_title,
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
        start_button_action_mismatch = _start_button_action_mismatch(
            instruction_tokens,
            visible_text,
            automation_id,
        )
        task_view_action_mismatch = _task_view_action_mismatch(
            instruction,
            instruction_tokens,
            visible_text,
            automation_id,
        )
        hidden_icons_action_mismatch = _hidden_icons_action_mismatch(
            instruction_tokens,
            visible_text,
        )
        show_desktop_action_mismatch = _show_desktop_action_mismatch(
            instruction_tokens,
            visible_text,
        )
        taskbar_file_action_mismatch = _taskbar_file_action_mismatch(
            instruction_tokens,
            visible_text,
            ctype,
            window_title,
        )
        taskbar_search_status_action_mismatch = _taskbar_search_status_action_mismatch(
            instruction_tokens,
            visible_text,
            automation_id,
            ctype,
            window_title,
        )
        program_manager_action_mismatch = _program_manager_desktop_item_action_mismatch(
            instruction_tokens,
            visible_text,
            ctype,
            window_title,
        )
        browser_profile_identity_action_mismatch = (
            _browser_profile_identity_action_mismatch(
                instruction_tokens,
                visible_text,
                ctype,
                window_title,
            )
        )
        browser_profile_page_action_mismatch = _browser_profile_page_action_mismatch(
            instruction,
            visible_text,
            ctype,
            window_title,
            rect,
        )
        browser_address_bar_content_mismatch = _browser_address_bar_content_mismatch(
            instruction,
            instruction_tokens,
            semantic_text,
            ctype,
            window_title,
        )
        browser_new_tab_action_mismatch = _browser_new_tab_action_mismatch(
            instruction,
            instruction_tokens,
            visible_text,
            ctype,
            window_title,
        )
        browser_extension_access_action_mismatch = (
            _browser_extension_access_action_mismatch(
                instruction,
                instruction_tokens,
                semantic_text,
                ctype,
                window_title,
            )
        )
        browser_tab_auth_action_mismatch = _browser_tab_auth_action_mismatch(
            instruction_tokens,
            ctype,
        )
        browser_tab_generic_section_mismatch = _browser_tab_generic_section_mismatch(
            instruction,
            instruction_tokens,
            ctype,
        )
        pin_state_action_mismatch = _pin_state_action_mismatch(
            instruction,
            visible_text,
            automation_id,
        )
        password_visibility_state_action_mismatch = (
            _password_visibility_state_action_mismatch(
                instruction,
                visible_text,
                automation_id,
            )
        )
        audio_output_polarity_action_mismatch = (
            _audio_output_polarity_action_mismatch(
                instruction,
                visible_text,
                automation_id,
            )
        )
        history_action_mismatch = _history_action_mismatch(
            instruction,
            visible_text,
            automation_id,
        )
        checkbox_state_action_mismatch = _checkbox_state_action_mismatch(
            instruction,
            visible_text,
            automation_id,
        )
        navigation_media_transport_action_mismatch = (
            _navigation_media_transport_action_mismatch(
                instruction,
                visible_text,
                automation_id,
            )
        )
        navigation_backup_action_mismatch = _navigation_backup_action_mismatch(
            instruction,
            visible_text,
            automation_id,
        )
        explicit_action_context_mismatch = _explicit_action_context_mismatch(
            instruction,
            visible_text,
            automation_id,
            ctype,
            window_title,
        )
        exclusive_action_family_mismatch = _exclusive_action_family_mismatch(
            instruction,
            " ".join((visible_text or "", automation_id or "")),
        )
        clear_close_action_mismatch = _clear_close_action_mismatch(
            instruction,
            instruction_tokens,
            visible_text,
            automation_id,
            rect,
            control_intent_contexts,
        )
        close_context_action_mismatch = _close_context_action_mismatch(
            instruction,
            visible_text,
            automation_id,
        )
        unparsed_visible_text_action_mismatch = bool(
            instruction_tokens
            and not _tokenize_control(semantic_text)
            and _has_unparsed_alnum_text(visible_text)
        )
        browser_about_blank_title_info_mismatch = (
            _browser_about_blank_title_info_mismatch(
                instruction,
                instruction_tokens,
                semantic_text,
                ctype,
                window_title,
            )
        )
        site_information_action_mismatch = _site_information_action_mismatch(
            instruction_tokens,
            semantic_text,
            ctype,
        )
        semantic_action_mismatch = (
            start_button_action_mismatch
            or task_view_action_mismatch
            or hidden_icons_action_mismatch
            or show_desktop_action_mismatch
            or taskbar_file_action_mismatch
            or taskbar_search_status_action_mismatch
            or program_manager_action_mismatch
            or browser_profile_identity_action_mismatch
            or browser_profile_page_action_mismatch
            or browser_address_bar_content_mismatch
            or browser_new_tab_action_mismatch
            or browser_extension_access_action_mismatch
            or browser_tab_auth_action_mismatch
            or browser_tab_generic_section_mismatch
            or pin_state_action_mismatch
            or password_visibility_state_action_mismatch
            or audio_output_polarity_action_mismatch
            or history_action_mismatch
            or checkbox_state_action_mismatch
            or navigation_media_transport_action_mismatch
            or navigation_backup_action_mismatch
            or explicit_action_context_mismatch
            or exclusive_action_family_mismatch
            or clear_close_action_mismatch
            or close_context_action_mismatch
            or unparsed_visible_text_action_mismatch
            or browser_about_blank_title_info_mismatch
            or site_information_action_mismatch
        )
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
        if control_intents and not _control_matches_effective_intent(
            ctype,
            visible_text,
            automation_id,
            instruction,
            control_intents,
        ):
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
            semantic_action_mismatch=semantic_action_mismatch,
        )
        if (
            semantic_action_mismatch
            and semantic_action_mismatch_result is None
            and _semantic_mismatch_targets_model_rect(rect, model_rect)
        ):
            semantic_action_mismatch_result = SnapResult(
                rect=rect,
                confidence=score,
                source="uia",
                matched_text=text,
                rejected_reason="candidate semantic mismatch",
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
            and not semantic_action_mismatch
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
        if semantic_action_mismatch_result is not None:
            return semantic_action_mismatch_result
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
                yield (
                    control,
                    rect,
                    is_own_process,
                    window_rank,
                    foreground_known,
                    top_handle,
                    _control_visible_text(top),
                )
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


def _semantic_mismatch(text: str, instruction_tokens: set[str]) -> bool:
    control_tokens = _tokenize_control(_semantic_text(text))
    if _disclosure_action_tokens_mismatch(instruction_tokens, control_tokens):
        return True
    return bool(instruction_tokens and control_tokens and not (instruction_tokens & control_tokens))


def _semantic_overlap(text: str, instruction_tokens: set[str]) -> bool:
    control_tokens = _tokenize_control(_semantic_text(text))
    if _disclosure_action_tokens_mismatch(instruction_tokens, control_tokens):
        return False
    return bool(instruction_tokens and (control_tokens & instruction_tokens))


def _semantic_score(text: str, instruction_tokens: set[str]) -> float:
    control_tokens = _tokenize_control(_semantic_text(text))
    if not instruction_tokens or not control_tokens:
        return 0.0
    if _disclosure_action_tokens_mismatch(instruction_tokens, control_tokens):
        return 0.0
    return len(instruction_tokens & control_tokens) / max(1, len(instruction_tokens))


def _semantic_text(text: str) -> str:
    text = BROWSER_TAB_OWNER_ACCOUNT_RE.sub("", text or "")
    return BROWSER_TAB_MEMORY_USAGE_RE.sub("", text).strip()


def _has_unparsed_alnum_text(text: str) -> bool:
    value = (text or "").strip()
    return bool(value and not _tokens_from_text(value) and any(ch.isalnum() for ch in value))


def _start_button_action_mismatch(
    instruction_tokens: set[str],
    visible_text: str,
    automation_id: str,
) -> bool:
    if "start" not in instruction_tokens:
        return False
    control_tokens = _tokenize_control(" ".join((visible_text or "", automation_id or "")))
    if "startbutton" not in control_tokens and not (
        "start" in control_tokens and "button" in control_tokens
    ):
        return False
    return bool(instruction_tokens - START_BUTTON_ALLOWED_TOKENS)


def _task_view_action_mismatch(
    instruction: str,
    instruction_tokens: set[str],
    visible_text: str,
    automation_id: str,
) -> bool:
    if not (instruction_tokens & {"task", "view"}):
        return False
    control_tokens = _tokenize_control(" ".join((visible_text or "", automation_id or "")))
    if not {"task", "view"} <= control_tokens:
        return False
    return not _instruction_mentions_task_view(instruction)


def _hidden_icons_action_mismatch(
    instruction_tokens: set[str],
    visible_text: str,
) -> bool:
    control_tokens = _tokenize_control(visible_text or "")
    if not {"hidden", "icons"} <= control_tokens:
        return False
    if instruction_tokens & TASKBAR_HIDDEN_ICONS_REQUEST_WORDS:
        return False
    if {"hidden", "icons"} <= instruction_tokens:
        return False
    return bool(instruction_tokens & {"hidden", "icons"})


def _show_desktop_action_mismatch(
    instruction_tokens: set[str],
    visible_text: str,
) -> bool:
    if "desktop" not in instruction_tokens:
        return False
    control_tokens = _tokenize_control(visible_text or "")
    if "show_desktop" not in control_tokens and not {"show", "desktop"} <= control_tokens:
        return False
    return not bool(instruction_tokens & TASKBAR_SHOW_DESKTOP_REQUEST_WORDS)


def _program_manager_desktop_item_action_mismatch(
    instruction_tokens: set[str],
    visible_text: str,
    ctype: str,
    window_title: str,
) -> bool:
    if ctype not in {"listitem", "treeitem"}:
        return False
    if PROGRAM_MANAGER_WINDOW_WORDS - _tokenize_control(window_title or ""):
        return False
    control_tokens = _tokenize_control(visible_text or "")
    raw_control_tokens = _tokens_from_text(visible_text or "")
    if "desktop" in control_tokens and "desktop" in instruction_tokens:
        distinctive_tokens = control_tokens - {"desktop"}
        if not instruction_tokens & distinctive_tokens:
            return True
    if {"learn", "about", "picture"} <= raw_control_tokens:
        if instruction_tokens & PROGRAM_MANAGER_ABOUT_WORDS:
            return not bool(instruction_tokens & PROGRAM_MANAGER_SPOTLIGHT_REQUEST_WORDS)
    if "new" in raw_control_tokens and instruction_tokens & PROGRAM_MANAGER_NEW_ACTION_WORDS:
        distinctive_tokens = (
            control_tokens
            - PROGRAM_MANAGER_NEW_ACTION_WORDS
            - {token for token in control_tokens if token.isdigit()}
        )
        if not instruction_tokens & distinctive_tokens:
            return True
    if instruction_tokens & PROGRAM_MANAGER_GENERIC_NAME_WORDS & control_tokens:
        distinctive_tokens = (
            control_tokens
            - PROGRAM_MANAGER_GENERIC_NAME_WORDS
            - {token for token in control_tokens if token.isdigit()}
        )
        if distinctive_tokens and not instruction_tokens & distinctive_tokens:
            return True
    return False


def _taskbar_file_action_mismatch(
    instruction_tokens: set[str],
    visible_text: str,
    ctype: str,
    window_title: str,
) -> bool:
    if ctype not in {"button", "splitbutton"}:
        return False
    window_tokens = _tokens_from_text(window_title or "")
    if not (window_tokens & TASKBAR_WINDOW_WORDS):
        return False
    if not (instruction_tokens & TASKBAR_FILE_ACTION_WORDS):
        return False
    raw_control_tokens = _tokens_from_text(visible_text or "")
    if not (raw_control_tokens & TASKBAR_GENERIC_FILE_IDENTITY_WORDS):
        return False
    distinctive_tokens = (
        raw_control_tokens
        - TASKBAR_GENERIC_FILE_IDENTITY_WORDS
        - TASKBAR_APP_STATE_CONTEXT_WORDS
    )
    if distinctive_tokens and instruction_tokens & distinctive_tokens:
        return False
    return True


def _taskbar_search_status_action_mismatch(
    instruction_tokens: set[str],
    visible_text: str,
    automation_id: str,
    ctype: str,
    window_title: str,
) -> bool:
    if ctype not in {"button", "splitbutton"}:
        return False
    if (automation_id or "").strip().lower() != "searchgleambutton":
        return False
    if not (_tokens_from_text(window_title or "") & TASKBAR_WINDOW_WORDS):
        return False
    if instruction_tokens & TASKBAR_SEARCH_STATUS_IDENTITY_WORDS:
        return False
    overlap = instruction_tokens & _tokenize_control(visible_text or "")
    return bool(overlap & TASKBAR_SEARCH_STATUS_SEPARATOR_ALIAS_WORDS)


def _browser_profile_identity_action_mismatch(
    instruction_tokens: set[str],
    visible_text: str,
    ctype: str,
    window_title: str,
) -> bool:
    if not instruction_tokens & BROWSER_PROFILE_TOKENS:
        return False
    if instruction_tokens & BROWSER_PROFILE_ACTION_CONTEXT_WORDS:
        return False
    control_tokens = _tokenize_control(visible_text or "")
    raw_control_tokens = _tokens_from_text(visible_text or "")
    window_tokens = _tokens_from_text(window_title or "")
    if control_tokens & BROWSER_PROFILE_TOKENS:
        return False
    if (
        ctype in {"button", "splitbutton"}
        and raw_control_tokens & BROWSER_PROFILE_LABEL_HINT_WORDS
        and window_tokens & BROWSER_PROFILE_WINDOW_WORDS
    ):
        return False
    if raw_control_tokens & BROWSER_APP_IDENTITY_WORDS:
        return True
    return bool(
        window_tokens & TASKBAR_WINDOW_WORDS
        and raw_control_tokens & BROWSER_APP_IDENTITY_WORDS
    )


def _browser_profile_page_action_mismatch(
    instruction: str,
    visible_text: str,
    ctype: str,
    window_title: str,
    rect: tuple[int, int, int, int],
) -> bool:
    instruction_tokens = _tokens_from_text(instruction)
    if not (instruction_tokens & BROWSER_PROFILE_TOKENS):
        return False
    if not (instruction_tokens & BROWSER_PAGE_TARGET_WORDS):
        return False
    if ctype not in {"button", "splitbutton"}:
        return False
    width, height = rect[2], rect[3]
    if width <= 0 or height <= 0:
        return False
    if max(width, height) > BROWSER_PROFILE_MAX_EDGE:
        return False
    if max(width, height) / max(1, min(width, height)) > BROWSER_PROFILE_MAX_ASPECT:
        return False
    window_tokens = _tokens_from_text(window_title or "")
    if not (window_tokens & BROWSER_PROFILE_WINDOW_WORDS):
        return False
    raw_control_tokens = _tokens_from_text(visible_text or "")
    return bool(raw_control_tokens & (BROWSER_PROFILE_TOKENS | BROWSER_PROFILE_LABEL_HINT_WORDS))


def _browser_address_bar_content_mismatch(
    instruction: str,
    instruction_tokens: set[str],
    semantic_text: str,
    ctype: str,
    window_title: str,
) -> bool:
    if not _looks_like_browser_address_bar(semantic_text, ctype, window_title):
        return False
    control_tokens = _tokenize_control(_semantic_text(semantic_text))
    if not (instruction_tokens & control_tokens):
        return False
    return not _instruction_requests_browser_address_bar(instruction)


def _looks_like_browser_address_bar(
    semantic_text: str,
    ctype: str,
    window_title: str,
) -> bool:
    if ctype not in {"edit", "combobox"}:
        return False
    raw_tokens = _tokens_from_text(semantic_text or "")
    if {"address", "bar"} <= raw_tokens:
        return True
    window_tokens = _tokens_from_text(window_title or "")
    if window_tokens and not (window_tokens & BROWSER_PROFILE_WINDOW_WORDS):
        return False
    return bool(raw_tokens & (BROWSER_ADDRESS_BAR_ROLE_WORDS - {"bar", "search"}))


def _instruction_requests_browser_address_bar(instruction: str) -> bool:
    raw_tokens = _tokens_from_text(instruction)
    if raw_tokens & (BROWSER_ADDRESS_BAR_REQUEST_WORDS - {"find", "search"}):
        return True
    return "bar" in raw_tokens and bool(raw_tokens & {"find", "search"})


def _browser_new_tab_action_mismatch(
    instruction: str,
    instruction_tokens: set[str],
    visible_text: str,
    ctype: str,
    window_title: str,
) -> bool:
    if ctype not in {"button", "splitbutton"}:
        return False
    if _tokenize_control(window_title or "") and not (
        _tokenize_control(window_title or "") & BROWSER_PROFILE_WINDOW_WORDS
    ):
        return False
    raw_control_tokens = _tokens_from_text(visible_text or "")
    if not {"new", "tab"} <= raw_control_tokens:
        return False
    if not (instruction_tokens & BROWSER_NEW_TAB_RELATED_REQUEST_WORDS):
        return False
    if _instruction_mentions_tab_context(instruction):
        return False
    return True


def _browser_extension_access_action_mismatch(
    instruction: str,
    instruction_tokens: set[str],
    semantic_text: str,
    ctype: str,
    window_title: str,
) -> bool:
    if ctype not in {"button", "splitbutton"}:
        return False
    window_tokens = _tokenize_control(window_title or "")
    if not (window_tokens & BROWSER_PROFILE_WINDOW_WORDS):
        return False
    control_tokens = _tokens_from_text(semantic_text or "")
    if not (BROWSER_EXTENSION_ACCESS_CONTEXT_WORDS <= control_tokens):
        return False
    raw_tokens = _tokens_from_text(instruction)
    if not (
        instruction_tokens & BROWSER_EXTENSION_ACCESS_CONTEXT_WORDS
        or raw_tokens & BROWSER_EXTENSION_ACCESS_LABEL_STOPWORDS
    ):
        return False
    if _instruction_names_browser_extension_access_target(instruction, semantic_text):
        return False
    return True


def _instruction_names_browser_extension_access_target(
    instruction: str,
    semantic_text: str,
) -> bool:
    target_tokens = {
        token
        for token in _tokens_from_text(semantic_text or "")
        - BROWSER_EXTENSION_ACCESS_LABEL_STOPWORDS
        if len(token) > 1 and not token.isdigit()
    }
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
    target_compact = re.sub(r"[^a-z0-9]+", "", (semantic_text or "").lower())
    for word in instruction_specific:
        if word in target_tokens:
            return True
        if len(word) >= 4 and word in target_compact:
            return True
    return False


def _instruction_mentions_task_view(instruction: str) -> bool:
    normalized = " ".join((instruction or "").lower().split())
    compact = re.sub(r"[^a-z0-9]+", "", normalized)
    return bool(re.search(r"\btask\s+view\b", normalized)) or "taskview" in compact


def _browser_tab_auth_action_mismatch(
    instruction_tokens: set[str],
    ctype: str,
) -> bool:
    if ctype != "tabitem":
        return False
    if not instruction_tokens or not (instruction_tokens & BROWSER_TAB_AUTH_ACTION_WORDS):
        return False
    return instruction_tokens <= BROWSER_TAB_AUTH_ACTION_WORDS


def _browser_tab_generic_section_mismatch(
    instruction: str,
    instruction_tokens: set[str],
    ctype: str,
) -> bool:
    if ctype != "tabitem":
        return False
    if not instruction_tokens or not (instruction_tokens & BROWSER_TAB_GENERIC_SECTION_WORDS):
        return False
    if _instruction_mentions_tab_context(instruction):
        return False
    return instruction_tokens <= BROWSER_TAB_GENERIC_SECTION_WORDS


def _instruction_mentions_tab_context(instruction: str) -> bool:
    return bool(re.search(r"\b(?:tab|tabs|tabitem)\b", (instruction or "").lower()))


def _browser_about_blank_title_info_mismatch(
    instruction: str,
    instruction_tokens: set[str],
    semantic_text: str,
    ctype: str,
    window_title: str,
) -> bool:
    if ctype != "tabitem":
        return False
    window_tokens = _tokens_from_text(window_title or "")
    if window_tokens and not (window_tokens & BROWSER_PROFILE_WINDOW_WORDS):
        return False
    if not (instruction_tokens & SITE_INFORMATION_REQUEST_WORDS):
        return False
    if not ({"about", "blank"} <= _tokens_from_text(semantic_text or "")):
        return False
    raw_instruction_tokens = _tokens_from_text(instruction)
    return not bool(raw_instruction_tokens & BROWSER_ABOUT_BLANK_TARGET_WORDS)


def _site_information_action_mismatch(
    instruction_tokens: set[str],
    semantic_text: str,
    ctype: str,
) -> bool:
    if ctype not in {"button", "splitbutton"}:
        return False
    control_tokens = _tokenize_control(_semantic_text(semantic_text))
    if not {"site", "information"} <= control_tokens:
        return False
    if instruction_tokens & SITE_INFORMATION_REQUEST_WORDS:
        return False
    return bool(instruction_tokens & {"site", "view"})


def _disclosure_action_tokens_mismatch(
    instruction_tokens: set[str],
    control_tokens: set[str],
) -> bool:
    requested_expand = bool(instruction_tokens & DISCLOSURE_EXPAND_ACTION_WORDS)
    requested_collapse = bool(instruction_tokens & DISCLOSURE_COLLAPSE_ACTION_WORDS)
    if requested_expand == requested_collapse:
        return False

    control_expand = bool(control_tokens & DISCLOSURE_EXPAND_ACTION_WORDS)
    control_collapse = bool(control_tokens & DISCLOSURE_COLLAPSE_ACTION_WORDS)
    if control_expand == control_collapse:
        return False
    return requested_expand != control_expand


def _pin_state_action_mismatch(
    instruction: str,
    visible_text: str,
    automation_id: str,
) -> bool:
    instruction_tokens = _tokens_from_text(instruction)
    requested_unpin = "unpin" in instruction_tokens
    requested_pin = "pin" in instruction_tokens and not requested_unpin
    if requested_unpin == requested_pin:
        return False

    control_tokens = _tokens_from_text(visible_text or "") | _tokens_from_text(
        automation_id or ""
    )
    if requested_unpin:
        return "pin" in control_tokens and not (control_tokens & PIN_STATE_NEUTRAL_WORDS)
    return "unpin" in control_tokens


def _password_visibility_state_action_mismatch(
    instruction: str,
    visible_text: str,
    automation_id: str,
) -> bool:
    instruction_tokens = _literal_words_from_text(instruction)
    if not (instruction_tokens & PASSWORD_VISIBILITY_CONTEXT_WORDS):
        return False
    requested_show = bool(instruction_tokens & PASSWORD_VISIBILITY_SHOW_WORDS)
    requested_hide = bool(instruction_tokens & PASSWORD_VISIBILITY_HIDE_WORDS)
    if requested_show == requested_hide:
        return False

    control_tokens = _literal_words_from_text(" ".join((visible_text or "", automation_id or "")))
    control_show = bool(control_tokens & PASSWORD_VISIBILITY_SHOW_WORDS)
    control_hide = bool(control_tokens & PASSWORD_VISIBILITY_HIDE_WORDS)
    if control_show == control_hide:
        return False
    return requested_show != control_show


def _audio_output_polarity_action_mismatch(
    instruction: str,
    visible_text: str,
    automation_id: str,
) -> bool:
    instruction_tokens = _literal_words_from_text(instruction)
    if not (instruction_tokens & AUDIO_OUTPUT_CONTEXT_WORDS):
        return False
    requested_up = bool(instruction_tokens & AUDIO_OUTPUT_UP_WORDS)
    requested_down = bool(instruction_tokens & AUDIO_OUTPUT_DOWN_WORDS)
    if requested_up == requested_down:
        return False

    control_tokens = _literal_words_from_text(
        " ".join((visible_text or "", automation_id or ""))
    )
    if not (control_tokens & AUDIO_OUTPUT_CONTEXT_WORDS):
        return False
    control_up = bool(control_tokens & AUDIO_OUTPUT_UP_WORDS)
    control_down = bool(control_tokens & AUDIO_OUTPUT_DOWN_WORDS)
    if control_up == control_down:
        return False
    return requested_up != control_up


def _history_action_mismatch(
    instruction: str,
    visible_text: str,
    automation_id: str,
) -> bool:
    instruction_tokens = _tokens_from_text(instruction)
    requested_undo = bool(instruction_tokens & HISTORY_UNDO_WORDS)
    requested_redo = bool(instruction_tokens & HISTORY_REDO_WORDS)
    if requested_undo == requested_redo:
        return False

    control_tokens = _tokens_from_text(" ".join((visible_text or "", automation_id or "")))
    control_undo = bool(control_tokens & HISTORY_UNDO_WORDS)
    control_redo = bool(control_tokens & HISTORY_REDO_WORDS)
    if control_undo == control_redo:
        return False
    return requested_undo != control_undo


def _checkbox_state_action_mismatch(
    instruction: str,
    visible_text: str,
    automation_id: str,
) -> bool:
    control_text = " ".join((visible_text or "", automation_id or ""))
    turn_instruction = _turn_on_off_action_kind(instruction)
    turn_control = _turn_on_off_action_kind(control_text)
    if turn_instruction and turn_control:
        return turn_instruction != turn_control

    instruction_tokens = _tokens_from_text(instruction)
    requested_on = bool(instruction_tokens & CHECKBOX_ON_ACTION_WORDS)
    requested_off = bool(instruction_tokens & CHECKBOX_OFF_ACTION_WORDS)
    if requested_on == requested_off:
        return False

    control_tokens = _tokens_from_text(control_text)
    control_on = bool(control_tokens & CHECKBOX_ON_ACTION_WORDS)
    control_off = bool(control_tokens & CHECKBOX_OFF_ACTION_WORDS)
    if control_on == control_off:
        return False
    return requested_on != control_on


def _turn_on_off_action_kind(text: str) -> str:
    has_on = bool(TURN_ON_RE.search(text or ""))
    has_off = bool(TURN_OFF_RE.search(text or ""))
    if has_on == has_off:
        return ""
    return "on" if has_on else "off"


def _navigation_media_transport_action_mismatch(
    instruction: str,
    visible_text: str,
    automation_id: str,
) -> bool:
    instruction_tokens = _tokens_from_text(instruction)
    if not (instruction_tokens & NAVIGATION_DIRECTION_WORDS):
        return False
    if instruction_tokens & MEDIA_TRANSPORT_CONTEXT_WORDS:
        return False

    control_tokens = _tokens_from_text(" ".join((visible_text or "", automation_id or "")))
    if not (control_tokens & NAVIGATION_DIRECTION_WORDS):
        return False
    return bool(control_tokens & MEDIA_TRANSPORT_CONTEXT_WORDS)


def _navigation_backup_action_mismatch(
    instruction: str,
    visible_text: str,
    automation_id: str,
) -> bool:
    instruction_tokens = _tokens_from_text(instruction)
    if not (instruction_tokens & NAVIGATION_BACK_WORDS):
        return False
    if instruction_tokens & BACKUP_ACTION_WORDS:
        return False
    control_tokens = _tokens_from_text(" ".join((visible_text or "", automation_id or "")))
    return bool("back" in control_tokens and control_tokens & BACKUP_ACTION_WORDS)


def _explicit_action_context_mismatch(
    instruction: str,
    visible_text: str,
    automation_id: str,
    ctype: str,
    window_title: str,
) -> bool:
    return (
        _edit_action_context_mismatch(instruction, visible_text, automation_id, ctype)
        or _confirm_action_context_mismatch(instruction, visible_text, automation_id)
        or _file_action_context_mismatch(
            instruction,
            " ".join((visible_text or "", automation_id or "")),
        )
        or _same_action_family_object_mismatch(
            instruction,
            " ".join((visible_text or "", automation_id or "")),
        )
        or _same_action_family_window_context_mismatch(
            instruction,
            " ".join((visible_text or "", automation_id or "")),
            window_title,
        )
        or _generic_visibility_polarity_action_mismatch(
            instruction,
            " ".join((visible_text or "", automation_id or "")),
        )
        or _reversible_action_polarity_mismatch(
            instruction,
            " ".join((visible_text or "", automation_id or "")),
        )
        or _state_label_action_mismatch(
            instruction,
            " ".join((visible_text or "", automation_id or "")),
        )
        or _search_results_label_action_mismatch(
            instruction,
            " ".join((visible_text or "", automation_id or "")),
        )
        or _new_tab_window_action_mismatch(
            instruction,
            " ".join((visible_text or "", automation_id or "")),
        )
        or _browser_tab_bookmark_action_mismatch(
            instruction,
            visible_text,
            automation_id,
            ctype,
            window_title,
        )
        or _browser_tab_contextual_item_mismatch(
            instruction,
            ctype,
            window_title,
        )
    )


def _edit_action_context_mismatch(
    instruction: str,
    visible_text: str,
    automation_id: str,
    ctype: str,
) -> bool:
    if ctype in {"combobox", "edit"}:
        return False
    instruction_tokens = _tokens_from_text(instruction)
    if not (instruction_tokens & EDIT_ACTION_WORDS):
        return False
    control_tokens = _tokens_from_text(" ".join((visible_text or "", automation_id or "")))
    if not control_tokens:
        return False
    return not bool(control_tokens & EDIT_ACTION_WORDS)


def _confirm_action_context_mismatch(
    instruction: str,
    visible_text: str,
    automation_id: str,
) -> bool:
    instruction_kind = _confirm_cancel_action_kind(_tokens_from_text(instruction))
    if not instruction_kind:
        return False

    control_text = " ".join((visible_text or "", automation_id or ""))
    control_tokens = _tokens_from_text(control_text)
    control_kind = _confirm_cancel_action_kind(control_tokens)
    if not control_kind:
        return False
    if instruction_kind != control_kind:
        return True
    return _same_action_object_mismatch(
        _tokenize_instruction(instruction),
        _tokenize_control(_semantic_text(control_text)),
        CONFIRM_CANCEL_ACTION_WORDS,
        CONFIRM_OBJECT_STOPWORDS,
    )


def _confirm_cancel_action_kind(tokens: set[str]) -> str:
    requested_confirm = bool(tokens & CONFIRM_ACTION_WORDS)
    requested_cancel = bool(tokens & CANCEL_ACTION_WORDS)
    if requested_confirm == requested_cancel:
        return ""
    return "confirm" if requested_confirm else "cancel"


def _same_action_object_mismatch(
    instruction_tokens: set[str],
    control_tokens: set[str],
    action_tokens: frozenset[str],
    stopwords: frozenset[str],
) -> bool:
    instruction_objects = _action_object_tokens(instruction_tokens, action_tokens, stopwords)
    control_objects = _action_object_tokens(control_tokens, action_tokens, stopwords)
    if not instruction_objects or not control_objects:
        return False
    return not bool(
        _object_token_variants(instruction_objects)
        & _object_token_variants(control_objects)
    )


def _object_token_variants(tokens: set[str]) -> set[str]:
    variants = set(tokens)
    for token in tokens:
        if len(token) < 4:
            continue
        if token.endswith("ies") and len(token) > 4:
            variants.add(f"{token[:-3]}y")
        if token.endswith("es") and len(token) > 4:
            variants.add(token[:-2])
        if token.endswith("s") and not token.endswith("ss"):
            variants.add(token[:-1])
    return variants


def _action_object_tokens(
    tokens: set[str],
    action_tokens: frozenset[str],
    stopwords: frozenset[str],
) -> set[str]:
    return {
        token
        for token in tokens - action_tokens - stopwords
        if len(token) > 1 and not token.isdigit()
    }


def _instruction_action_object_tokens(
    instruction: str,
    action_tokens: frozenset[str],
) -> set[str]:
    tokens = set(_tokenize_instruction(instruction))
    raw_tokens = _tokens_from_text(instruction)
    tokens.update(raw_tokens & ACTION_CONTEXT_OBJECT_WORDS)
    objects = _action_object_tokens(tokens, action_tokens, ACTION_OBJECT_STOPWORDS)
    objects.update(_file_identity_object_tokens(raw_tokens))
    if action_tokens & (FILE_PICKER_ACTION_WORDS | FILE_IMPORT_ACTION_WORDS):
        objects -= {"add", "create", "new", "plus"}
    return objects


def _file_identity_object_tokens(tokens: set[str]) -> set[str]:
    return set(FILE_IDENTITY_WORDS) if tokens & FILE_IDENTITY_WORDS else set()


def _file_action_context_mismatch(instruction: str, candidate_text: str) -> bool:
    instruction_kind = _file_action_kind(_tokens_from_text(instruction), is_instruction=True)
    if not instruction_kind:
        return False
    control_kind = _file_action_kind(_tokens_from_text(candidate_text), is_instruction=False)
    return bool(control_kind and instruction_kind != control_kind)


def _file_action_kind(tokens: set[str], *, is_instruction: bool) -> str:
    fileish = bool(tokens & FILE_IDENTITY_WORDS)
    pickerish = bool(tokens & (FILE_PICKER_ACTION_WORDS | FILE_IMPORT_ACTION_WORDS))
    if pickerish and (fileish or not is_instruction):
        return "picker"
    if not fileish:
        return ""
    if tokens & FILE_SAVE_ACTION_WORDS:
        return "save"
    if tokens & FILE_EXPORT_ACTION_WORDS:
        return "export"
    if tokens & FILE_OPEN_ACTION_WORDS:
        return "open"
    return ""


def _same_action_family_object_mismatch(instruction: str, candidate_text: str) -> bool:
    instruction_raw_tokens = _tokens_from_text(instruction)
    control_raw_tokens = _tokens_from_text(candidate_text)
    if not instruction_raw_tokens or not control_raw_tokens:
        return False
    for family in EXCLUSIVE_ACTION_FAMILIES:
        if not (instruction_raw_tokens & family and control_raw_tokens & family):
            continue
        instruction_objects = _object_token_variants(
            _instruction_action_object_tokens(instruction, family)
        )
        control_objects = _object_token_variants(
            _action_object_tokens(
                _tokenize_control(candidate_text),
                family,
                ACTION_OBJECT_STOPWORDS,
            )
            | _file_identity_object_tokens(control_raw_tokens)
        )
        if instruction_objects and control_objects and not (instruction_objects & control_objects):
            return True
    return False


def _same_action_family_window_context_mismatch(
    instruction: str,
    candidate_text: str,
    window_title: str,
) -> bool:
    if not (window_title or "").strip():
        return False
    instruction_raw_tokens = _tokens_from_text(instruction)
    control_raw_tokens = _tokens_from_text(candidate_text)
    if not instruction_raw_tokens or not control_raw_tokens:
        return False
    context_tokens = _tokenize_control(window_title or "")
    context_objects = _object_token_variants(
        _action_object_tokens(
            context_tokens,
            frozenset(),
            ACTION_OBJECT_STOPWORDS,
        )
    ) & WINDOW_CONTEXT_OBJECT_WORDS
    if not context_objects:
        return False
    for family in EXCLUSIVE_ACTION_FAMILIES:
        if not (instruction_raw_tokens & family and control_raw_tokens & family):
            continue
        instruction_objects = _object_token_variants(
            _instruction_action_object_tokens(instruction, family)
        )
        if not instruction_objects:
            continue
        control_objects = _action_object_tokens(
            _tokenize_control(candidate_text),
            family,
            ACTION_OBJECT_STOPWORDS,
        )
        if control_objects:
            continue
        if instruction_objects & context_objects:
            return False
        return True
    return False


def _generic_visibility_polarity_action_mismatch(
    instruction: str,
    candidate_text: str,
) -> bool:
    instruction_tokens = _tokens_from_text(instruction)
    requested_show = bool(instruction_tokens & GENERIC_VISIBILITY_SHOW_WORDS)
    requested_hide = bool(instruction_tokens & GENERIC_VISIBILITY_HIDE_WORDS)
    if requested_show == requested_hide:
        return False

    control_tokens = _tokens_from_text(candidate_text)
    control_show = bool(control_tokens & GENERIC_VISIBILITY_SHOW_WORDS)
    control_hide = bool(control_tokens & GENERIC_VISIBILITY_HIDE_WORDS)
    if control_show == control_hide:
        return False
    if requested_show == control_show:
        return False

    instruction_objects = _action_object_tokens(
        _tokenize_instruction(instruction),
        GENERIC_VISIBILITY_ACTION_WORDS,
        ACTION_OBJECT_STOPWORDS,
    )
    control_objects = _action_object_tokens(
        _tokenize_control(candidate_text),
        GENERIC_VISIBILITY_ACTION_WORDS,
        ACTION_OBJECT_STOPWORDS,
    )
    return bool(instruction_objects or control_objects)


def _reversible_action_polarity_mismatch(
    instruction: str,
    candidate_text: str,
) -> bool:
    instruction_kind = _reversible_action_polarity_kind(_tokens_from_text(instruction))
    if not instruction_kind:
        return False
    control_kind = _reversible_action_polarity_kind(_tokens_from_text(candidate_text))
    if not control_kind:
        return False
    instruction_family, instruction_side = instruction_kind.split(":", 1)
    control_family, control_side = control_kind.split(":", 1)
    if instruction_family != control_family or instruction_side == control_side:
        return False
    instruction_objects = _object_token_variants(
        _action_object_tokens(
            _tokens_from_text(instruction),
            REVERSIBLE_ACTION_POLARITY_WORDS,
            ACTION_OBJECT_STOPWORDS,
        )
    )
    control_objects = _object_token_variants(
        _action_object_tokens(
            _tokens_from_text(candidate_text),
            REVERSIBLE_ACTION_POLARITY_WORDS,
            ACTION_OBJECT_STOPWORDS,
        )
    )
    if instruction_objects and control_objects:
        return True
    return not instruction_objects and not control_objects


def _reversible_action_polarity_kind(tokens: set[str]) -> str:
    for index, (positive_words, negative_words) in enumerate(REVERSIBLE_ACTION_POLARITY_PAIRS):
        requested_positive = bool(tokens & positive_words)
        requested_negative = bool(tokens & negative_words)
        if requested_positive == requested_negative:
            continue
        side = "positive" if requested_positive else "negative"
        return f"{index}:{side}"
    return ""


def _state_label_action_mismatch(
    instruction: str,
    candidate_text: str,
) -> bool:
    control_tokens = _literal_words_from_text(candidate_text)
    if not control_tokens:
        return False

    instruction_tokens = _literal_words_from_text(instruction)
    if _state_label_is_target_identity(instruction_tokens, control_tokens):
        return False

    turn_kind = _turn_on_off_action_kind(instruction)
    if turn_kind and control_tokens & (STATE_LABEL_TURN_ON_WORDS | STATE_LABEL_TURN_OFF_WORDS):
        return True

    for action_words, state_words in STATE_LABEL_ACTION_GROUPS:
        if instruction_tokens & action_words and control_tokens & state_words:
            return True
    return False


def _search_results_label_action_mismatch(
    instruction: str,
    candidate_text: str,
) -> bool:
    instruction_tokens = _literal_words_from_text(instruction)
    if not (instruction_tokens & SEARCH_ACTION_WORDS):
        return False
    if instruction_tokens & SEARCH_RESULTS_LABEL_WORDS:
        return False
    control_tokens = _literal_words_from_text(candidate_text)
    return bool(control_tokens & SEARCH_ACTION_WORDS and control_tokens & SEARCH_RESULTS_LABEL_WORDS)


def _state_label_is_target_identity(
    instruction_tokens: set[str],
    control_tokens: set[str],
) -> bool:
    if {"hidden", "icons"} <= control_tokens and {"hidden", "icons"} <= instruction_tokens:
        return True
    if {"hidden", "bookmarks"} <= control_tokens and {"hidden", "bookmarks"} <= instruction_tokens:
        return True
    if "group" in control_tokens and control_tokens & BROWSER_GROUP_STATE_WORDS:
        identity_tokens = control_tokens - BROWSER_GROUP_STATE_WORDS - {"group", "tab", "tabs"}
        return "group" in instruction_tokens or bool(identity_tokens & instruction_tokens)
    return False


def _new_tab_window_action_mismatch(instruction: str, candidate_text: str) -> bool:
    instruction_kind = _literal_new_tab_window_kind(_literal_words_from_text(instruction))
    if not instruction_kind:
        return False
    control_kind = _literal_new_tab_window_kind(_literal_words_from_text(candidate_text))
    return bool(control_kind and instruction_kind != control_kind)


def _literal_new_tab_window_kind(words: set[str]) -> str:
    if "new" not in words:
        return ""
    has_tab = bool(words & BROWSER_TAB_WORDS)
    has_window = bool(words & BROWSER_WINDOW_WORDS)
    if has_tab == has_window:
        return ""
    return "tab" if has_tab else "window"


def _browser_tab_bookmark_action_mismatch(
    instruction: str,
    visible_text: str,
    automation_id: str,
    ctype: str,
    window_title: str,
) -> bool:
    if ctype not in {"button", "splitbutton"}:
        return False
    instruction_tokens = _tokenize_instruction(instruction)
    if not (instruction_tokens & BROWSER_BOOKMARK_ACTION_WORDS):
        return False
    window_tokens = _tokens_from_text(window_title or "")
    if window_tokens and not (window_tokens & BROWSER_PROFILE_WINDOW_WORDS):
        return False

    control_text = " ".join((visible_text or "", automation_id or ""))
    raw_control_tokens = _tokens_from_text(control_text)
    if not (raw_control_tokens & BROWSER_BOOKMARK_ACTION_WORDS):
        return False
    if not (raw_control_tokens & BROWSER_BOOKMARK_TAB_CONTEXT_WORDS):
        return False

    raw_instruction_tokens = _tokens_from_text(instruction)
    if raw_instruction_tokens & BROWSER_BOOKMARK_TAB_CONTEXT_WORDS:
        return False
    if "add" in raw_instruction_tokens and "bookmark" in raw_instruction_tokens:
        return False
    if raw_instruction_tokens & BROWSER_BOOKMARK_ITEM_CONTEXT_WORDS:
        return True
    return bool(raw_instruction_tokens & {"favorite", "star"} and raw_instruction_tokens & {"this", "that"})


def _browser_tab_contextual_item_mismatch(
    instruction: str,
    ctype: str,
    window_title: str,
) -> bool:
    if ctype != "tabitem":
        return False
    raw_tokens = _tokens_from_text(instruction)
    if raw_tokens & BROWSER_TAB_WORDS:
        return False
    if "item" not in raw_tokens:
        return False
    if not (raw_tokens & CONTEXTUAL_NAV_ITEM_CONTAINER_WORDS):
        return False
    window_tokens = _tokens_from_text(window_title or "")
    return bool(window_tokens & BROWSER_PROFILE_WINDOW_WORDS)


def _control_matches_effective_intent(
    ctype: str,
    visible_text: str,
    automation_id: str,
    instruction: str,
    control_intents: set[str],
) -> bool:
    if ctype in control_intents:
        return True
    if "checkbox" not in control_intents or ctype not in {"button", "splitbutton"}:
        return False

    instruction_tokens = _tokens_from_text(instruction)
    requested_on = bool(instruction_tokens & CHECKBOX_ON_ACTION_WORDS)
    requested_off = bool(instruction_tokens & CHECKBOX_OFF_ACTION_WORDS)
    if requested_on == requested_off:
        return False

    control_text = " ".join((visible_text or "", automation_id or ""))
    control_tokens = _tokens_from_text(control_text)
    if requested_on and not (control_tokens & CHECKBOX_ON_ACTION_WORDS):
        return False
    if requested_off and not (control_tokens & CHECKBOX_OFF_ACTION_WORDS):
        return False

    instruction_semantic = _tokenize_instruction(instruction) - (
        CHECKBOX_ON_ACTION_WORDS | CHECKBOX_OFF_ACTION_WORDS
    )
    control_semantic = _tokenize_control(_semantic_text(control_text)) - (
        CHECKBOX_ON_ACTION_WORDS | CHECKBOX_OFF_ACTION_WORDS
    )
    return bool(instruction_semantic & control_semantic)


def _exclusive_action_family_mismatch(instruction: str, candidate_text: str) -> bool:
    requested_families = _exclusive_action_family_indexes(_tokens_from_text(instruction))
    if not requested_families:
        return False
    candidate_families = _exclusive_action_family_indexes(_tokens_from_text(candidate_text))
    return bool(candidate_families and requested_families.isdisjoint(candidate_families))


def _exclusive_action_family_indexes(tokens: set[str]) -> set[int]:
    return {
        index
        for index, family in enumerate(EXCLUSIVE_ACTION_FAMILIES)
        if tokens & family
    }


def _clear_close_action_mismatch(
    instruction: str,
    instruction_tokens: set[str],
    visible_text: str,
    automation_id: str,
    rect: tuple[int, int, int, int],
    contexts: list[tuple[tuple[int, int, int, int], str]],
) -> bool:
    instruction_words = _literal_words_from_text(instruction)
    if "clear" not in instruction_words and "clear" not in instruction_tokens:
        return False
    if not _looks_like_close_or_x_control(visible_text, automation_id):
        return False
    if "clear" in _literal_words_from_text(" ".join((visible_text or "", automation_id or ""))):
        return False
    return not _has_clear_field_context(rect, contexts, instruction_words)


def _looks_like_close_or_x_control(visible_text: str, automation_id: str) -> bool:
    literal_tokens = _literal_words_from_text(" ".join((visible_text or "", automation_id or "")))
    if literal_tokens & CLEAR_CLOSE_WORDS:
        return True
    return _is_x_symbol_text(visible_text) or _is_x_symbol_text(automation_id)


def _has_clear_field_context(
    rect: tuple[int, int, int, int],
    contexts: list[tuple[tuple[int, int, int, int], str]],
    instruction_words: set[str],
) -> bool:
    requested_context = instruction_words & CLEAR_CONTEXT_WORDS
    for context_rect, context_text in contexts:
        context_tokens = _tokenize_control(_semantic_text(context_text)) | _literal_words_from_text(
            context_text
        )
        if not (context_tokens & CLEAR_CONTEXT_WORDS):
            continue
        if requested_context and not (context_tokens & requested_context):
            continue
        expanded = _expand_rect(context_rect, 10)
        if _contains_rect(expanded, rect) or _center_inside(rect, expanded):
            return True
    return False


def _close_context_action_mismatch(
    instruction: str,
    visible_text: str,
    automation_id: str,
) -> bool:
    instruction_words = _literal_words_from_text(instruction)
    if not (instruction_words & CLEAR_CLOSE_WORDS):
        return False
    requested_context = instruction_words & CLOSE_CONTEXT_TARGET_WORDS
    if not requested_context:
        return False
    if not _looks_like_close_or_x_control(visible_text, automation_id):
        return False
    control_words = _literal_words_from_text(" ".join((visible_text or "", automation_id or "")))
    return not bool(control_words & requested_context)


def _literal_words_from_text(text: str) -> set[str]:
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", text or "")
    spaced = re.sub(r"[_\-.]+", " ", spaced)
    return set(re.findall(r"[a-z0-9]+", spaced.lower()))


def _is_x_symbol_text(text: str) -> bool:
    return (text or "").strip().lower() in X_SYMBOL_TEXTS


def _semantic_mismatch_targets_model_rect(
    candidate_rect: tuple[int, int, int, int],
    model_rect: tuple[int, int, int, int],
) -> bool:
    if _iou(candidate_rect, model_rect) >= SEMANTIC_MISMATCH_IOU_FLOOR:
        return True
    return _center_inside(model_rect, candidate_rect)


def _score(
    *,
    rect: tuple[int, int, int, int],
    semantic_text: str,
    ctype: str,
    model_rect: tuple[int, int, int, int],
    model_center: tuple[int, int],
    instruction_tokens: set[str],
    diagonal: float,
    semantic_action_mismatch: bool = False,
) -> float:
    iou = _iou(rect, model_rect)
    cx, cy = _center(rect)
    mx, my = model_center
    distance = ((cx - mx) ** 2 + (cy - my) ** 2) ** 0.5
    proximity = max(0.0, 1.0 - min(1.0, distance / diagonal))

    control_tokens = _tokenize_control(_semantic_text(semantic_text))
    overlap = instruction_tokens & control_tokens
    disclosure_mismatch = _disclosure_action_tokens_mismatch(
        instruction_tokens,
        control_tokens,
    )
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
    if (
        semantic_action_mismatch
        or disclosure_mismatch
        or (instruction_tokens and control_tokens and not overlap)
    ):
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
    if _disclosure_action_tokens_mismatch(
        instruction_tokens,
        _tokenize_control(_semantic_text(first_text)),
    ):
        return False
    if _disclosure_action_tokens_mismatch(
        instruction_tokens,
        _tokenize_control(_semantic_text(second_text)),
    ):
        return False
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
        if _disclosure_action_tokens_mismatch(
            instruction_tokens,
            _tokenize_control(_semantic_text(semantic_text)),
        ):
            continue
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
    candidate_tokens = _tokenize_control(_semantic_text(semantic_text))
    context_tokens: set[str] = set()
    for context_rect, context_text in contexts:
        if _contains_rect(_expand_rect(context_rect, 4), rect):
            context_tokens.update(_tokenize_control(_semantic_text(context_text)))
    evidence_tokens = candidate_tokens | context_tokens
    if _text_evidence_score(instruction_tokens, evidence_tokens) < 0.35:
        return False
    if (
        candidate_tokens
        and not (instruction_tokens & candidate_tokens)
        and not instruction_tokens <= context_tokens
    ):
        return False
    return True


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
