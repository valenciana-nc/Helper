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
TABLE_CONTEXT_MARGIN_PX = 240
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
FILTER_RESET_ACTION_WORDS = frozenset({"clear", "delete", "remove", "reset"})
FILTER_RESET_CONTEXT_WORDS = frozenset({"filter", "filters", "query", "search"})
FILTER_RESET_ALLOWED_CONTROL_WORDS = frozenset(
    {"clear", "delete", "remove", "reset", "x"}
)
FILTER_RESET_OBJECT_ONLY_WORDS = frozenset(
    {"apply", "filter", "filters", "funnel", "query", "result", "results", "search"}
)
SETTINGS_CONTEXT_WORDS = frozenset(
    {"option", "options", "preference", "preferences", "setting", "settings"}
)
SETTINGS_SPECIFIC_REQUEST_STOPWORDS = frozenset(
    {
        "app",
        "application",
        "button",
        "cog",
        "gear",
        "icon",
        "item",
        "left",
        "list",
        "listitem",
        "menu",
        "nav",
        "navigation",
        "option",
        "page",
        "pane",
        "panel",
        "rail",
        "right",
        "row",
        "sidebar",
        "screen",
        "tab",
        "tabitem",
        "table",
        "toolbar",
        "view",
    }
)
CLOSE_CONTEXT_TARGET_WORDS = frozenset(
    {
        "banner",
        "bar",
        "card",
        "column",
        "columns",
        "dialog",
        "dialogs",
        "drawer",
        "grid",
        "grids",
        "header",
        "headers",
        "heading",
        "headings",
        "list",
        "lists",
        "menu",
        "modal",
        "notification",
        "panel",
        "pane",
        "popover",
        "popup",
        "section",
        "sidebar",
        "table",
        "tables",
        "toast",
        "toolbar",
        "window",
        "windows",
    }
)
SURFACE_CONTEXT_CONTROL_TYPES = frozenset(
    {"datagrid", "grid", "group", "headeritem", "list", "menu", "pane", "table", "toolbar", "window"}
)
ROW_CONTEXT_CONTROL_TYPES = frozenset({"dataitem", "listitem", "row", "tableitem", "treeitem"})
TABLE_CELL_CONTROL_TYPES = frozenset({"cell", "datagridcell", "gridcell"})
TABLE_CELL_ROW_LABEL_CONTROL_TYPES = frozenset({"label", "statictext", "text"})
FIELD_LABEL_CONTEXT_CONTROL_TYPES = TABLE_CELL_ROW_LABEL_CONTROL_TYPES
BLANK_FIELD_LABEL_CONTROL_TYPES = frozenset({"edit", "combobox"})
OPTION_CONTEXT_CONTROL_TYPES = frozenset({"listitem", "menuitem", "option"})
OPTION_CONTEXT_PARENT_TYPES = frozenset({"group", "list", "menu", "menuitem", "pane"})
OPTION_PARENT_SURFACE_WORDS = frozenset(
    {"context", "drop", "down", "dropdown", "list", "menu", "picker", "selector"}
)
OPTION_PARENT_CONTEXT_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "below",
        "choice",
        "choices",
        "choose",
        "click",
        "entry",
        "entries",
        "for",
        "from",
        "in",
        "inside",
        "item",
        "items",
        "listitem",
        "menuitem",
        "of",
        "on",
        "open",
        "option",
        "options",
        "press",
        "select",
        "selected",
        "tap",
        "that",
        "the",
        "this",
        "to",
        "under",
        "value",
        "values",
        "within",
        "with",
    }
)
SURFACE_CONTEXT_TYPE_WORDS = {
    "datagrid": frozenset({"grid", "table"}),
    "grid": frozenset({"grid", "table"}),
    "group": frozenset({"group"}),
    "headeritem": frozenset({"column", "header", "heading"}),
    "list": frozenset({"list"}),
    "menu": frozenset({"menu"}),
    "pane": frozenset({"pane"}),
    "table": frozenset({"grid", "table"}),
    "toolbar": frozenset({"toolbar"}),
    "window": frozenset({"window"}),
}
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
OPEN_VIEW_REQUEST_WORDS = frozenset(
    {"display", "inspect", "open", "read", "review", "see", "show", "view"}
)
GENERIC_OBJECT_REQUEST_WORDS = frozenset(
    {
        "click",
        "find",
        "focus",
        "go",
        "hit",
        "look",
        "navigate",
        "press",
        "search",
        "tap",
        "visit",
    }
)
FIELD_ENTRY_ACTION_WORDS = frozenset({"enter", "fill", "input", "type"})
CONFIRM_ACTION_WORDS = frozenset(
    {"apply", "checkmark", "complete", "confirm", "done", "finish", "ok", "okay", "tick"}
)
CANCEL_ACTION_WORDS = frozenset({"cancel"})
CONFIRM_CANCEL_ACTION_WORDS = CONFIRM_ACTION_WORDS | CANCEL_ACTION_WORDS
ADD_ACTION_WORDS = frozenset({"add", "create", "new", "plus"})
REMOVE_ACTION_WORDS = frozenset({"bin", "delete", "remove", "trash", "wastebasket"})
PAY_ACTION_WORDS = frozenset({"checkout", "pay"})
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
GENERIC_OBJECT_REQUEST_STOPWORDS = ACTION_OBJECT_STOPWORDS | frozenset({"for", "on", "to"})
TABLE_CELL_CONTEXT_STOPWORDS = GENERIC_OBJECT_REQUEST_STOPWORDS | GENERIC_OBJECT_REQUEST_WORDS | frozenset(
    {
        "cell",
        "cells",
        "column",
        "columns",
        "data",
        "datagrid",
        "grid",
        "gridcell",
        "in",
        "of",
        "table",
        "the",
        "this",
        "with",
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
TRANSFER_ACTION_WORDS = FILE_EXPORT_ACTION_WORDS | FILE_IMPORT_ACTION_WORDS
CLIPBOARD_COPY_WORDS = frozenset({"copy"})
DUPLICATE_ACTION_WORDS = frozenset({"clone", "duplicate"})
SEARCH_ACTION_WORDS = frozenset({"find", "search"})
BARE_EXTENDED_ACTION_LABEL_WORDS = (
    CANCEL_ACTION_WORDS
    | REMOVE_ACTION_WORDS
    | FILE_OPEN_ACTION_WORDS
    | FILE_SAVE_ACTION_WORDS
    | FILE_EXPORT_ACTION_WORDS
    | EDIT_ACTION_WORDS
    | frozenset({"print"})
)
BARE_EXTENDED_ACTION_LABEL_HINT_WORDS = frozenset(
    {
        "alt",
        "cmd",
        "command",
        "control",
        "ctrl",
        "keyboard",
        "meta",
        "option",
        "shortcut",
        "shift",
        "win",
        "windows",
    }
)
CLIPBOARD_COPY_EXACT_CONTEXT_WORDS = frozenset(
    {"address", "link", "links", "selected", "selection", "text", "url", "urls"}
)
ACTION_OBJECT_ALIAS_CONTEXT_WORDS = FILE_IDENTITY_WORDS | frozenset(
    {
        "content",
        "message",
        "messages",
        "paragraph",
        "paragraphs",
        "selection",
        "selected",
        "text",
        "word",
        "words",
    }
)
BROWSER_TAB_WORDS = frozenset({"tab", "tabs", "tabitem"})
BROWSER_WINDOW_WORDS = frozenset({"window", "windows"})
CONTEXTUAL_NAV_ITEM_CONTAINER_WORDS = frozenset({"drawer", "nav", "navigation", "rail", "rails", "sidebar"})
GENERIC_VISIBILITY_SHOW_WORDS = frozenset({"show"})
GENERIC_VISIBILITY_HIDE_WORDS = frozenset({"hide"})
GENERIC_VISIBILITY_ACTION_WORDS = GENERIC_VISIBILITY_SHOW_WORDS | GENERIC_VISIBILITY_HIDE_WORDS
REVERSIBLE_ACTION_POLARITY_PAIRS = (
    (frozenset({"activate"}), frozenset({"deactivate"})),
    (frozenset({"archive"}), frozenset({"unarchive"})),
    (frozenset({"block"}), frozenset({"unblock"})),
    (frozenset({"connect"}), frozenset({"disconnect"})),
    (frozenset({"follow"}), frozenset({"unfollow"})),
    (frozenset({"join"}), frozenset({"leave"})),
    (frozenset({"like"}), frozenset({"unlike"})),
    (frozenset({"lock"}), frozenset({"unlock"})),
    (frozenset({"mute"}), frozenset({"unmute"})),
    (frozenset({"open"}), frozenset({"close"})),
    (frozenset({"restore"}), frozenset({"bin", "delete", "remove", "trash", "wastebasket"})),
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
    (frozenset({"activate"}), frozenset({"activated"})),
    (frozenset({"deactivate"}), frozenset({"deactivated"})),
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
    (frozenset({"activate", "deactivate"}), frozenset({"activated", "deactivated"})),
    (frozenset({"archive", "unarchive"}), frozenset({"archived", "unarchived"})),
    (frozenset({"select", "deselect"}), frozenset({"deselected", "selected", "unselected"})),
    (frozenset({"start", "stop"}), frozenset({"running", "started", "stopped"})),
    (frozenset({"subscribe", "unsubscribe"}), frozenset({"subscribed", "unsubscribed"})),
    (frozenset({"open", "close"}), frozenset({"closed", "opened"})),
    (
        frozenset({"accept", "allow", "approve", "decline", "deny", "reject"}),
        frozenset({"accepted", "allowed", "approved", "declined", "denied", "rejected"}),
    ),
    (frozenset({"mark", "read", "unread"}), frozenset({"read", "unread"})),
)
STATE_LABEL_TURN_ON_WORDS = frozenset({"checked", "enabled"})
STATE_LABEL_TURN_OFF_WORDS = frozenset({"disabled", "unchecked"})
STATE_ACTION_WORDS = frozenset(
    word for action_words, _state_words in STATE_LABEL_ACTION_GROUPS for word in action_words
)
PARTIAL_STATE_LABEL_CONTROL_TYPES = frozenset({"checkbox", "radiobutton", "slider"})
PARTIAL_STATE_LABEL_EXTRA_STOPWORDS = frozenset(
    {
        "and",
        "asterisk",
        "mandatory",
        "optional",
        "or",
        "required",
        "star",
    }
)
PARTIAL_STATE_LABEL_REQUEST_STOPWORDS = (
    ACTION_OBJECT_STOPWORDS
    | GENERIC_OBJECT_REQUEST_WORDS
    | STATE_ACTION_WORDS
    | CHECKBOX_ON_ACTION_WORDS
    | CHECKBOX_OFF_ACTION_WORDS
    | frozenset(
        {
            "a",
            "an",
            "box",
            "button",
            "check",
            "checkbox",
            "control",
            "for",
            "from",
            "in",
            "inside",
            "into",
            "of",
            "on",
            "option",
            "please",
            "radio",
            "radiobutton",
            "select",
            "slider",
            "state",
            "switch",
            "that",
            "the",
            "this",
            "to",
            "toggle",
            "under",
            "within",
        }
    )
)
PARTIAL_FIELD_LABEL_EXTRA_STOPWORDS = PARTIAL_STATE_LABEL_EXTRA_STOPWORDS | frozenset(
    {"address", "number", "value"}
)
PARTIAL_FIELD_LABEL_REQUEST_STOPWORDS = PARTIAL_STATE_LABEL_REQUEST_STOPWORDS | frozenset(
    {
        "address",
        "arrow",
        "bar",
        "box",
        "caret",
        "chevron",
        "combo",
        "combobox",
        "drop",
        "dropdown",
        "field",
        "fill",
        "form",
        "forms",
        "input",
        "into",
        "picker",
        "please",
        "selector",
        "text",
        "textarea",
        "type",
        "url",
        "your",
    }
)
SEARCH_RESULTS_LABEL_WORDS = frozenset({"result", "results"})
SORT_ASCENDING_WORDS = frozenset({"ascending"})
SORT_DESCENDING_WORDS = frozenset({"descending"})
SEARCH_FILTER_SEARCH_WORDS = frozenset({"find", "search"})
SEARCH_FILTER_FILTER_WORDS = frozenset({"filter", "funnel"})
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
    frozenset({"filter", "funnel"}),
    frozenset({"print", "printer"}),
    frozenset({"accept", "allow", "approve"}),
    frozenset({"decline", "deny", "reject"}),
    frozenset({"refresh", "reload"}),
    frozenset({"share"}),
    frozenset({"sort"}),
)
PIN_ACTION_WORDS = frozenset({"pin", "pushpin", "thumbtack", "unpin"})
SAME_ACTION_OBJECT_FAMILIES = EXCLUSIVE_ACTION_FAMILIES + (
    PIN_ACTION_WORDS,
    GENERIC_VISIBILITY_ACTION_WORDS,
    SEARCH_ACTION_WORDS,
    frozenset({"clear", "reset"}),
    frozenset({"overlap", "restore"}),
)
OPEN_VIEW_CANDIDATE_ACTION_FAMILIES = (
    CONFIRM_ACTION_WORDS,
    CANCEL_ACTION_WORDS,
    ADD_ACTION_WORDS - frozenset({"new", "plus"}),
    REMOVE_ACTION_WORDS,
    PAY_ACTION_WORDS,
    FILE_SAVE_ACTION_WORDS,
    FILE_EXPORT_ACTION_WORDS,
    FILE_PICKER_ACTION_WORDS | FILE_IMPORT_ACTION_WORDS,
    CLIPBOARD_COPY_WORDS | DUPLICATE_ACTION_WORDS,
    EDIT_ACTION_WORDS,
    frozenset({"archive", "cabinet", "filing"}),
    frozenset({"accept", "allow", "approve"}),
    frozenset({"clipboard", "paste"}),
    frozenset({"plane", "send", "submit"}),
    frozenset({"print", "printer"}),
    frozenset({"decline", "deny", "reject"}),
    frozenset({"share"}),
)
AUTOMATION_ONLY_ACTION_MATCH_WORDS = frozenset(
    word for action_words, _state_words in STATE_LABEL_ACTION_GROUPS for word in action_words
) | frozenset(word for family in EXCLUSIVE_ACTION_FAMILIES for word in family)
GENERIC_OBJECT_CANDIDATE_ACTION_FAMILIES = OPEN_VIEW_CANDIDATE_ACTION_FAMILIES
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
BROWSER_CHROME_APP_CONTEXT_WORDS = frozenset(
    {
        "app",
        "application",
        "card",
        "chart",
        "dashboard",
        "form",
        "grid",
        "in_app",
        "in_page",
        "list",
        "nav",
        "navigation",
        "pane",
        "panel",
        "report",
        "section",
        "sidebar",
        "table",
        "widget",
        "wizard",
    }
)
BROWSER_CHROME_EXPLICIT_CONTEXT_WORDS = frozenset(
    {"address", "browser", "brave", "chrome", "edge", "omnibox", "url"}
)
BROWSER_CHROME_TOOLBAR_AUTOMATION_IDS = frozenset(
    {
        "apps",
        "bookmarks",
        "browseressentials",
        "copilot",
        "downloads",
        "extensions",
        "games",
        "history",
        "home",
        "immersivereader",
        "mathsolver",
        "passwords",
        "performance",
        "readaloud",
        "readinglist",
        "reload",
        "searchtabs",
        "sidebar",
        "sidepanel",
        "splitscreen",
        "shopping",
        "tabactionsmenu",
        "tabsearchbutton",
        "translate",
        "verticaltabs",
        "wallet",
        "workspaces",
    }
)
BROWSER_CHROME_TOOLBAR_WORDS = frozenset(
    {
        "back",
        "aloud",
        "apps",
        "bookmarks",
        "collection",
        "collections",
        "copilot",
        "download",
        "downloads",
        "essentials",
        "extensions",
        "forward",
        "games",
        "history",
        "home",
        "house",
        "immersive",
        "math",
        "password",
        "passwords",
        "performance",
        "read",
        "reader",
        "reading",
        "reload",
        "refresh",
        "search",
        "sidebar",
        "split",
        "shopping",
        "solver",
        "tabs",
        "tab_search",
        "translate",
        "vertical",
        "wallet",
        "workspace",
        "workspaces",
    }
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
        "option",
        "tabitem",
        "hyperlink",
        "listitem",
        "dataitem",
        "row",
        "tableitem",
        "treeitem",
        "edit",
        "combobox",
        "checkbox",
        "radiobutton",
        "splitbutton",
        "spinner",
        "headeritem",
        "slider",
        "cell",
        "datagridcell",
        "gridcell",
    }
)
TIGHT_ACTION_SNAP_CONTROL_TYPES = frozenset(
    {"button", "hyperlink", "menuitem", "option", "splitbutton", "tabitem"}
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
    direct_card_request = _direct_card_target_request(instruction)
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
    surface_contexts: list[tuple[tuple[int, int, int, int], str, str]] = []
    row_contexts: list[tuple[tuple[int, int, int, int], str, str]] = []
    table_cell_surface_contexts: list[tuple[tuple[int, int, int, int], str, str]] = []
    table_cell_row_contexts: list[tuple[tuple[int, int, int, int], str, str]] = []
    state_option_contexts: list[tuple[tuple[int, int, int, int], str, str]] = []
    option_contexts: list[tuple[tuple[int, int, int, int], str, str]] = []
    option_parent_contexts: list[tuple[tuple[int, int, int, int], str, str]] = []
    field_label_contexts: list[tuple[tuple[int, int, int, int], str, int, int | None, str]] = []
    field_control_contexts: list[tuple[tuple[int, int, int, int], str, str, int, int | None, str]] = []
    snapped_field_label_contexts: list[tuple[tuple[int, int, int, int], str, str, int]] = []
    surface_scoped_action_rects: list[tuple[int, int, int, int]] = []
    foreground_handle = _safe_foreground_handle(
        foreground_handle_provider or _foreground_window_handle
    )
    topmost_provider = topmost_handle_provider
    if topmost_provider is None and desktop_factory is None:
        topmost_provider = _topmost_window_handle_at_point

    raw_candidates = list(
        _iter_candidates(
            desktop,
            search_rect,
            deadline,
            foreground_handle,
        )
    )
    context_search_rect = _expand_rect(model_rect, max(margin_px, TABLE_CONTEXT_MARGIN_PX))
    context_candidates = raw_candidates
    if context_search_rect != search_rect and time.monotonic() < deadline:
        context_candidates = raw_candidates + list(
            _iter_candidates(
                desktop,
                context_search_rect,
                deadline,
                foreground_handle,
            )
        )
    for (
        control,
        rect,
        _is_own_process,
        _window_rank,
        _foreground_known,
        label_top_handle,
        _control_handle,
        label_window_title,
    ) in context_candidates:
        ctype = _control_type(control)
        if not _is_enabled(control) or not _is_visible(control):
            continue
        if ctype in ROW_CONTEXT_CONTROL_TYPES or ctype in TABLE_CELL_ROW_LABEL_CONTROL_TYPES:
            table_cell_row_contexts.append((rect, _control_text(control), ctype))
        if ctype == "headeritem":
            table_cell_surface_contexts.append((rect, _control_text(control), ctype))
        if ctype in {"checkbox", "radiobutton"}:
            state_option_contexts.append((rect, _control_text(control), ctype))
        if ctype in OPTION_CONTEXT_CONTROL_TYPES:
            option_contexts.append((rect, _control_text(control), ctype))
        if ctype in OPTION_CONTEXT_PARENT_TYPES:
            option_parent_contexts.append((rect, _control_text(control), ctype))
        if ctype in FIELD_LABEL_CONTEXT_CONTROL_TYPES:
            label_text = _control_visible_text(control) or _control_text(control)
            if label_text:
                field_label_contexts.append(
                    (rect, label_text, _window_rank, label_top_handle, label_window_title)
                )
        if ctype in BLANK_FIELD_LABEL_CONTROL_TYPES:
            field_control_contexts.append(
                (
                    rect,
                    ctype,
                    _control_visible_text(control),
                    _window_rank,
                    label_top_handle,
                    label_window_title,
                )
            )

    for (
        rect,
        ctype,
        visible_text,
        window_rank,
        top_handle,
        window_title,
    ) in field_control_contexts:
        nearby_field_label_text = _nearby_field_label_text(
            rect=rect,
            ctype=ctype,
            visible_text=visible_text,
            window_rank=window_rank,
            top_handle=top_handle,
            window_title=window_title,
            field_label_contexts=field_label_contexts,
        )
        if nearby_field_label_text:
            snapped_field_label_contexts.append(
                (rect, nearby_field_label_text, ctype, window_rank)
            )

    for (
        control,
        rect,
        _is_own_process,
        _window_rank,
        _foreground_known,
        _top_handle,
        _control_handle,
        _window_title,
    ) in raw_candidates:
        ctype = _control_type(control)
        if not _is_enabled(control) or not _is_visible(control):
            continue
        if ctype in ROW_CONTEXT_CONTROL_TYPES:
            row_contexts.append((rect, _control_text(control), ctype))

    for (
        control,
        rect,
        is_own_process,
        window_rank,
        foreground_known,
        top_handle,
        control_handle,
        window_title,
    ) in raw_candidates:
        ctype = _control_type(control)
        if not _is_enabled(control) or not _is_visible(control):
            continue
        text = _control_text(control)
        visible_text = _control_visible_text(control)
        automation_id = _control_automation_id(control)
        if ctype in SURFACE_CONTEXT_CONTROL_TYPES:
            surface_contexts.append((rect, text, ctype))
        if ctype not in CLICKABLE_CONTROL_TYPES:
            continue
        nearby_field_label_text = _nearby_field_label_text(
            rect=rect,
            ctype=ctype,
            visible_text=visible_text,
            window_rank=window_rank,
            top_handle=top_handle,
            window_title=window_title,
            field_label_contexts=field_label_contexts,
        )
        semantic_text = visible_text or automation_id
        scoring_semantic_text = _field_label_scoring_semantic_text(
            semantic_text,
            nearby_field_label_text,
        )
        matched_text = _field_label_matched_text(text, nearby_field_label_text)
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
        taskbar_surface_context_mismatch = _taskbar_surface_context_mismatch(
            instruction,
            visible_text,
            automation_id,
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
        browser_chrome_app_context_mismatch = _browser_chrome_app_context_mismatch(
            instruction,
            visible_text,
            automation_id,
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
        object_only_action_context_mismatch = _object_only_action_context_mismatch(
            instruction,
            " ".join((visible_text or "", automation_id or "")),
        )
        surface_context_action_mismatch = _surface_context_action_mismatch(
            instruction,
            instruction_tokens,
            scoring_semantic_text,
            rect,
            surface_contexts,
        )
        row_context_action_mismatch = _row_context_action_mismatch(
            instruction,
            instruction_tokens,
            scoring_semantic_text,
            rect,
            row_contexts,
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
                scoring_semantic_text,
                ctype,
                window_title,
            )
        )
        site_information_action_mismatch = _site_information_action_mismatch(
            instruction_tokens,
            semantic_text,
            ctype,
        )
        bare_extended_label_action_mismatch = _bare_action_extended_label_mismatch(
            instruction,
            visible_text,
            ctype,
        )
        bare_search_filter_action_mismatch = _bare_search_filter_extended_label_mismatch(
            instruction,
            visible_text,
        )
        state_partial_label_mismatch = _state_control_partial_visible_label_mismatch(
            instruction,
            visible_text,
            ctype,
        )
        blank_field_label_mismatch = _blank_field_nearby_label_mismatch(
            instruction,
            ctype,
            visible_text,
            automation_id,
            nearby_field_label_text,
        )
        semantic_action_mismatch = (
            start_button_action_mismatch
            or task_view_action_mismatch
            or hidden_icons_action_mismatch
            or show_desktop_action_mismatch
            or taskbar_file_action_mismatch
            or taskbar_search_status_action_mismatch
            or taskbar_surface_context_mismatch
            or program_manager_action_mismatch
            or browser_profile_identity_action_mismatch
            or browser_profile_page_action_mismatch
            or browser_chrome_app_context_mismatch
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
            or object_only_action_context_mismatch
            or surface_context_action_mismatch
            or row_context_action_mismatch
            or clear_close_action_mismatch
            or close_context_action_mismatch
            or _specific_settings_context_mismatch(instruction, instruction_tokens, semantic_text)
            or unparsed_visible_text_action_mismatch
            or browser_about_blank_title_info_mismatch
            or site_information_action_mismatch
            or bare_extended_label_action_mismatch
            or bare_search_filter_action_mismatch
            or state_partial_label_mismatch
            or blank_field_label_mismatch
        )
        if not _is_candidate_topmost(top_handle, control_handle, rect, topmost_provider):
            if (
                occluded_result is None
                and _semantic_mismatch_targets_model_rect(rect, model_rect)
            ):
                occluded_result = SnapResult(
                    rect=rect,
                    confidence=0.0,
                    source="uia",
                    matched_text=matched_text,
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
                    matched_text=matched_text,
                    rejected_reason="own process target",
                )
            continue
        if _explicit_control_type_mismatch(
            instruction,
            ctype,
            visible_text,
            automation_id,
            control_intents,
        ) or _dropdown_launcher_option_mismatch(
            instruction,
            ctype,
            visible_text,
            automation_id,
        ):
            if (
                instruction_tokens
                and scoring_semantic_text
                and _semantic_mismatch_targets_model_rect(rect, model_rect)
            ):
                control_intent_contexts.append((rect, scoring_semantic_text))
            if (
                control_type_mismatch_result is None
                and _semantic_mismatch_targets_model_rect(rect, model_rect)
            ):
                control_type_mismatch_result = SnapResult(
                    rect=rect,
                    confidence=0.0,
                    source="uia",
                    matched_text=matched_text,
                    rejected_reason="control type mismatch",
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
                and scoring_semantic_text
                and _semantic_mismatch_targets_model_rect(rect, model_rect)
            ):
                control_intent_contexts.append((rect, scoring_semantic_text))
            if (
                control_type_mismatch_result is None
                and _semantic_mismatch_targets_model_rect(rect, model_rect)
            ):
                control_type_mismatch_result = SnapResult(
                    rect=rect,
                    confidence=0.0,
                    source="uia",
                    matched_text=matched_text,
                    rejected_reason="control type mismatch",
                )
            continue
        surface_scoped_action = _surface_scoped_action_match(
            instruction,
            instruction_tokens,
            scoring_semantic_text,
            rect,
            surface_contexts,
        )
        score = _score(
            rect=rect,
            semantic_text=scoring_semantic_text,
            ctype=ctype,
            model_rect=model_rect,
            model_center=model_center,
            instruction_tokens=instruction_tokens,
            diagonal=diagonal,
            semantic_action_mismatch=semantic_action_mismatch,
        )
        if surface_scoped_action:
            score = max(score, min(1.0, confidence_floor + 0.08))
            surface_scoped_action_rects.append(rect)
        if (
            semantic_action_mismatch
            and semantic_action_mismatch_result is None
            and _semantic_mismatch_targets_model_rect(rect, model_rect)
        ):
            semantic_action_mismatch_result = SnapResult(
                rect=rect,
                confidence=score,
                source="uia",
                matched_text=matched_text,
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
                    matched_text=matched_text,
                    rejected_reason="compound target ambiguous",
                )
        result = SnapResult(
            rect=rect,
            confidence=score,
            source="uia",
            matched_text=matched_text,
        )
        if (
            control_intents
            and _contains_rect(_expand_rect(model_rect, 4), rect)
            and not (ctype == "splitbutton" and _menu_segment_intent(control_intents))
            and not semantic_action_mismatch
            and not any(item.rect == result.rect for item, _text in contained_control_intent_results)
        ):
            contained_control_intent_results.append((result, scoring_semantic_text))
        ranked.append((score, result, scoring_semantic_text, ctype, window_rank))
        if (
            visible_text
            and _semantic_overlap(visible_text, instruction_tokens)
            and score > best_visible_score
        ):
            best_visible_score = score
            best_visible_result = result
        if score > best_score:
            best_score = score
            best_semantic_text = scoring_semantic_text
            best_ctype = ctype
            best_window_rank = window_rank
            best_is_automation_only = bool(
                not visible_text and automation_id and not nearby_field_label_text
            )
            best_result = result

    if (
        best_result is not None
        and best_is_automation_only
        and best_visible_result is not None
        and best_result.rect not in surface_scoped_action_rects
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

    if direct_card_request:
        card_result = _direct_card_target_result(
            ranked,
            instruction_tokens=instruction_tokens,
            confidence_floor=confidence_floor,
        )
        if card_result is not None:
            return card_result

    table_cell_result = _table_cell_context_snap_result(
        ranked,
        instruction=instruction,
        instruction_tokens=instruction_tokens,
        control_intents=control_intents,
        surface_contexts=table_cell_surface_contexts,
        row_contexts=table_cell_row_contexts,
        model_rect=model_rect,
        confidence_floor=confidence_floor,
    )
    if table_cell_result is not None:
        return table_cell_result

    option_context_result = _option_parent_context_snap_result(
        ranked,
        instruction=instruction,
        instruction_tokens=instruction_tokens,
        control_intents=control_intents,
        option_contexts=option_contexts,
        parent_contexts=option_parent_contexts,
        model_rect=model_rect,
        confidence_floor=confidence_floor,
    )
    if option_context_result is not None:
        return option_context_result

    if (
        best_result is not None
        and _neutral_same_label_state_option_snap_ambiguous(
            instruction,
            selected=best_result,
            selected_ctype=best_ctype,
            ranked=ranked,
            state_option_contexts=state_option_contexts,
        )
    ):
        return SnapResult(
            rect=best_result.rect,
            confidence=best_score,
            source="uia",
            matched_text=best_result.matched_text,
            rejected_reason="state option ambiguous",
        )

    if (
        best_result is not None
        and _duplicate_labelled_field_snap_ambiguous(
            instruction,
            selected=best_result,
            selected_ctype=best_ctype,
            snapped_field_label_contexts=snapped_field_label_contexts,
        )
    ):
        return SnapResult(
            rect=best_result.rect,
            confidence=best_score,
            source="uia",
            matched_text=best_result.matched_text,
            rejected_reason="fresh snap ambiguous",
        )

    if best_result is not None and best_score >= confidence_floor:
        contained_best = _tighter_contained_snap_result(
            ranked,
            best_result=best_result,
            best_semantic_text=best_semantic_text,
            best_ctype=best_ctype,
            instruction=instruction,
            instruction_tokens=instruction_tokens,
            control_intents=control_intents,
            confidence_floor=confidence_floor,
        )
        if contained_best is not None:
            return contained_best

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
            control_type_mismatch_result is not None
            and (
                best_result is None
                or _contains_rect(
                    _expand_rect(control_type_mismatch_result.rect, 4),
                    best_result.rect,
                )
            )
        ):
            return control_type_mismatch_result
        if len(contained_control_intent_results) > 1:
            return SnapResult(
                rect=model_rect,
                confidence=0.0,
                source="uia",
                rejected_reason="contained control ambiguous",
            )
        if own_process_result is not None:
            return own_process_result
        if occluded_result is not None:
            return occluded_result
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
        if control_type_mismatch_result is not None:
            return control_type_mismatch_result
        if semantic_action_mismatch_result is not None:
            return semantic_action_mismatch_result
        if compound_target_result is not None:
            return compound_target_result
        weak_model_candidates = _weak_model_fallback_candidates(
            ranked,
            model_rect,
            confidence_floor=confidence_floor,
        )
        if weak_model_candidates:
            weak_score, weak_result = max(weak_model_candidates, key=lambda item: item[0])
            reason = (
                "fresh snap ambiguous"
                if len(weak_model_candidates) > 1
                else "candidate evidence missing"
            )
            return SnapResult(
                rect=weak_result.rect,
                confidence=weak_score,
                source="uia",
                matched_text=weak_result.matched_text,
                rejected_reason=reason,
            )
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


def _weak_model_fallback_candidates(
    ranked: list[tuple[float, SnapResult, str, str, int]],
    model_rect: tuple[int, int, int, int],
    *,
    confidence_floor: float,
) -> list[tuple[float, SnapResult]]:
    weak: list[tuple[float, SnapResult]] = []
    for score, result, _semantic_text, _ctype, _window_rank in ranked:
        if score >= confidence_floor:
            continue
        if not _semantic_mismatch_targets_model_rect(result.rect, model_rect):
            continue
        weak.append((score, result))
    return weak


def _default_desktop():
    from pywinauto import Desktop

    return Desktop(backend="uia")


def _iter_candidates(desktop, search_rect, deadline, foreground_handle=None):
    """BFS visible top-level windows and their descendants, yielding
    ``(control, rect, is_own_process, window_rank, foreground_known,
    top_handle, control_handle, window_title)`` tuples whose rect intersects
    ``search_rect``. Pruned by ``deadline`` and ``_MAX_BFS_DEPTH``.
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
                control_handle = _window_handle(control)
                yield (
                    control,
                    rect,
                    is_own_process,
                    window_rank,
                    foreground_known,
                    top_handle,
                    control_handle,
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


def _is_candidate_topmost(
    top_handle: int | None,
    control_handle: int | None,
    rect,
    topmost_handle_provider,
) -> bool:
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
        if actual_root == expected_root and _topmost_handle_matches_candidate(
            actual,
            top_handle,
            control_handle,
        ):
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


def _topmost_handle_matches_candidate(
    actual_handle: int,
    top_handle: int,
    control_handle: int | None,
) -> bool:
    if control_handle is None:
        return actual_handle == top_handle
    if actual_handle in {top_handle, control_handle}:
        return True
    return _handles_are_related(control_handle, actual_handle)


def _handles_are_related(first: int, second: int) -> bool:
    try:
        user32 = ctypes.windll.user32
        return bool(user32.IsChild(int(first), int(second))) or bool(
            user32.IsChild(int(second), int(first))
        )
    except Exception:
        return False


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


def _nearby_field_label_text(
    *,
    rect: tuple[int, int, int, int],
    ctype: str,
    visible_text: str,
    window_rank: int,
    top_handle: int | None,
    window_title: str,
    field_label_contexts: list[tuple[tuple[int, int, int, int], str, int, int | None, str]],
) -> str:
    if ctype not in BLANK_FIELD_LABEL_CONTROL_TYPES:
        return ""
    best_score = 0.0
    best_text = ""
    field_area = max(1, rect[2] * rect[3])
    for (
        label_rect,
        label_text,
        label_window_rank,
        label_top_handle,
        label_window_title,
    ) in field_label_contexts:
        if label_window_rank != window_rank:
            continue
        if not _same_fresh_label_window(
            top_handle,
            window_title,
            label_top_handle,
            label_window_title,
        ):
            continue
        words = _literal_word_sequence(label_text)
        if not words or len(words) > 8:
            continue
        label_area = max(1, label_rect[2] * label_rect[3])
        if label_area > field_area * 4:
            continue
        score = _nearby_field_label_score(rect, label_rect)
        if score > best_score:
            best_score = score
            best_text = label_text
    if best_score < 0.5:
        return ""
    return best_text


def _same_fresh_label_window(
    top_handle: int | None,
    window_title: str,
    label_top_handle: int | None,
    label_window_title: str,
) -> bool:
    if top_handle is not None or label_top_handle is not None:
        return top_handle == label_top_handle
    if window_title and label_window_title:
        return window_title == label_window_title
    return False


def _nearby_field_label_score(
    field_rect: tuple[int, int, int, int],
    label_rect: tuple[int, int, int, int],
) -> float:
    field_left, field_top, field_width, field_height = field_rect
    field_right = field_left + field_width
    field_bottom = field_top + field_height
    label_left, label_top, label_width, label_height = label_rect
    label_right = label_left + label_width
    label_bottom = label_top + label_height

    y_overlap = max(0, min(field_bottom, label_bottom) - max(field_top, label_top))
    y_ratio = y_overlap / max(1, min(field_height, label_height))
    left_gap = field_left - label_right
    if y_ratio >= 0.45 and -8 <= left_gap <= 180 and label_left <= field_left:
        return 0.75 + 0.25 * (1.0 - min(1.0, max(0, left_gap) / 180.0))

    x_overlap = max(0, min(field_right, label_right) - max(field_left, label_left))
    x_ratio = x_overlap / max(1, min(field_width, label_width))
    above_gap = field_top - label_bottom
    label_center_x, _label_center_y = _center(label_rect)
    field_center_x, _field_center_y = _center(field_rect)
    center_aligned = abs(label_center_x - field_center_x) <= max(60, field_width * 0.45)
    if 0 <= above_gap <= 64 and (x_ratio >= 0.25 or center_aligned):
        return 0.65 + 0.20 * (1.0 - min(1.0, above_gap / 64.0))

    return 0.0


def _field_label_scoring_semantic_text(
    semantic_text: str,
    nearby_field_label_text: str,
) -> str:
    return " | ".join(
        part for part in (semantic_text, nearby_field_label_text) if part
    )


def _field_label_matched_text(text: str, nearby_field_label_text: str) -> str:
    parts: list[str] = []
    for part in (text, nearby_field_label_text):
        value = (part or "").strip()
        if value and value not in parts:
            parts.append(value)
    return " | ".join(parts)


def _blank_field_nearby_label_mismatch(
    instruction: str,
    ctype: str,
    visible_text: str,
    automation_id: str,
    nearby_field_label_text: str,
) -> bool:
    if ctype not in BLANK_FIELD_LABEL_CONTROL_TYPES:
        return False
    requested_words = _field_label_request_words(instruction, ctype)
    if not requested_words:
        return False
    own_visible_words = set(_literal_word_sequence(visible_text))
    if own_visible_words and requested_words & _object_token_variants(own_visible_words):
        return False
    label_words = set(_literal_word_sequence(nearby_field_label_text)) - PARTIAL_FIELD_LABEL_EXTRA_STOPWORDS
    if not label_words:
        return True
    label_variants = _object_token_variants(label_words) | _tokenize_control(
        " ".join(sorted(label_words))
    )
    requested_variants = _object_token_variants(requested_words) | _tokenize_control(
        " ".join(sorted(requested_words))
    )
    if not (requested_variants & label_variants):
        return True
    request_context = _field_label_request_context_words(instruction, requested_words)
    if len(requested_words) == 1:
        extra_label_words = label_words - requested_variants - request_context
        return bool(extra_label_words)
    return not requested_variants <= label_variants


def _field_label_request_words(instruction: str, control_type: str) -> set[str]:
    words = _literal_word_sequence(instruction)
    for index, _word in enumerate(words):
        role_prefix = _field_label_role_prefix_width(words, index, control_type)
        if role_prefix is None:
            continue
        trailing = _field_label_trailing_request_words(words, index + 1)
        if trailing:
            return trailing
        cursor = index - role_prefix
        label_words: list[str] = []
        while cursor >= 0:
            current = words[cursor]
            if current in PARTIAL_FIELD_LABEL_REQUEST_STOPWORDS:
                break
            label_words.append(current)
            cursor -= 1
        if label_words:
            return _object_token_variants(set(label_words))
    return set()


def _field_label_trailing_request_words(words: list[str], start_index: int) -> set[str]:
    cursor = start_index
    while cursor < len(words):
        marker = words[cursor]
        if marker not in {"called", "for", "labelled", "labeled", "named", "with"}:
            cursor += 1
            continue
        cursor += 1
        label_words: list[str] = []
        while cursor < len(words):
            current = words[cursor]
            if current in PARTIAL_FIELD_LABEL_REQUEST_STOPWORDS:
                break
            label_words.append(current)
            cursor += 1
        if label_words:
            return _object_token_variants(set(label_words))
    return set()


def _field_label_role_prefix_width(
    words: list[str],
    index: int,
    control_type: str,
) -> int | None:
    word = words[index]
    previous = words[index - 1] if index > 0 else ""
    if control_type == "edit":
        if word in {"textbox", "textarea", "field", "input"}:
            return 2 if word in {"field", "input"} and previous == "text" else 1
        if word == "box" and previous == "text":
            return 2
        return None
    if control_type == "combobox":
        if word in {"combo", "combobox", "dropdown", "picker", "selector"}:
            return 1
        if word == "down" and previous == "drop":
            return 2
        return None
    return None


def _field_label_request_context_words(
    instruction: str,
    requested_words: set[str],
) -> set[str]:
    context_words = set(_literal_word_sequence(instruction))
    context_words -= requested_words
    context_words -= PARTIAL_FIELD_LABEL_REQUEST_STOPWORDS
    context_words -= FIELD_ENTRY_ACTION_WORDS
    context_words -= OPEN_VIEW_REQUEST_WORDS
    context_words -= GENERIC_OBJECT_REQUEST_WORDS
    context_words -= {
        "a",
        "an",
        "below",
        "beneath",
        "for",
        "from",
        "in",
        "inside",
        "into",
        "of",
        "on",
        "the",
        "to",
        "under",
        "underneath",
        "within",
        "with",
    }
    return _object_token_variants(context_words)


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


def _taskbar_surface_context_mismatch(
    instruction: str,
    visible_text: str,
    automation_id: str,
    window_title: str,
) -> bool:
    raw_tokens = _tokens_from_text(instruction)
    is_taskbar = _looks_like_taskbar_surface(visible_text, automation_id, window_title)
    if "taskbar" in raw_tokens:
        return not is_taskbar
    if not is_taskbar:
        return False
    if _instruction_requests_local_app_content_surface(instruction, raw_tokens):
        return True
    return bool(raw_tokens & BROWSER_PAGE_TARGET_WORDS)


def _looks_like_taskbar_surface(
    visible_text: str,
    automation_id: str,
    window_title: str,
) -> bool:
    window_tokens = _tokens_from_text(window_title or "")
    automation_key = (automation_id or "").strip().lower()
    if window_tokens & TASKBAR_WINDOW_WORDS:
        return True
    if automation_key in {"searchgleambutton", "systemtrayicon", "widgetsbutton"}:
        return True
    return bool(_tokens_from_text(visible_text or "") & TASKBAR_APP_STATE_CONTEXT_WORDS)


def _instruction_requests_local_app_content_surface(
    instruction: str,
    raw_tokens: set[str],
) -> bool:
    local_surface_words = BROWSER_CHROME_APP_CONTEXT_WORDS - {"app", "application"}
    if raw_tokens & local_surface_words:
        return True
    text = (instruction or "").lower()
    return bool(
        re.search(r"\b(?:in|inside|on|within)\s+(?:the\s+)?app\b", text)
        or re.search(r"\b(?:in|inside|on|within)\s+(?:the\s+)?page\b", text)
        or re.search(r"\bin[-\s]?page\b", text)
    )


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


def _browser_chrome_app_context_mismatch(
    instruction: str,
    visible_text: str,
    automation_id: str,
    ctype: str,
    window_title: str,
    rect: tuple[int, int, int, int],
) -> bool:
    raw_tokens = _tokens_from_text(instruction)
    explicit_app_local = _instruction_has_explicit_app_local_context(instruction, raw_tokens)
    if raw_tokens & BROWSER_CHROME_EXPLICIT_CONTEXT_WORDS and not explicit_app_local:
        return False
    if not (explicit_app_local or _instruction_requests_app_local_surface(instruction, raw_tokens)):
        return False
    return _looks_like_browser_chrome_surface(visible_text, automation_id, ctype, window_title, rect)


def _instruction_has_explicit_app_local_context(
    instruction: str,
    raw_tokens: set[str],
) -> bool:
    surface_tokens = _object_token_variants(raw_tokens)
    if surface_tokens & {"app", "application", "in_app", "in_page"}:
        return True
    text = (instruction or "").lower()
    return bool(
        re.search(r"\bin\s+(?:the\s+)?app\b", text)
        or re.search(r"\b(?:in|inside|on|within)\s+(?:the\s+)?page\b", text)
        or re.search(r"\bin[-\s]?page\b", text)
    )


def _instruction_requests_app_local_surface(
    instruction: str,
    raw_tokens: set[str],
) -> bool:
    if raw_tokens & BROWSER_CHROME_APP_CONTEXT_WORDS:
        return True
    text = (instruction or "").lower()
    return bool(
        re.search(r"\bin\s+(?:the\s+)?app\b", text)
        or re.search(r"\bin[-\s]?page\b", text)
    )


def _looks_like_browser_chrome_surface(
    visible_text: str,
    automation_id: str,
    ctype: str,
    window_title: str,
    rect: tuple[int, int, int, int],
) -> bool:
    if _looks_like_os_chrome_surface(visible_text, automation_id, ctype, window_title, rect):
        return True
    if _looks_like_browser_site_information_chrome_button(
        visible_text,
        automation_id,
        ctype,
        window_title,
        rect,
    ):
        return True
    window_tokens = _tokens_from_text(window_title or "")
    if not (window_tokens & BROWSER_PROFILE_WINDOW_WORDS):
        return False
    if _looks_like_browser_toolbar_button(visible_text, automation_id, ctype, window_title, rect):
        return True
    if _looks_like_browser_menu_button(visible_text, automation_id, ctype, window_title, rect):
        return True
    if _looks_like_browser_new_tab_button(visible_text, automation_id, ctype, window_title):
        return True
    if _looks_like_browser_profile_chrome_button(visible_text, automation_id, ctype, window_title, rect):
        return True
    return ctype == "tabitem" and rect[1] <= 72


def _looks_like_os_chrome_surface(
    visible_text: str,
    automation_id: str,
    ctype: str,
    window_title: str,
    rect: tuple[int, int, int, int],
) -> bool:
    return _looks_like_taskbar_search_button(
        visible_text,
        automation_id,
        ctype,
        window_title,
    ) or _looks_like_window_titlebar_button(visible_text, automation_id, ctype, window_title, rect)


def _looks_like_taskbar_search_button(
    visible_text: str,
    automation_id: str,
    ctype: str,
    window_title: str,
) -> bool:
    if ctype not in {"button", "splitbutton"}:
        return False
    if not (_tokens_from_text(window_title or "") & TASKBAR_WINDOW_WORDS):
        return False
    if (automation_id or "").strip().lower() == "searchgleambutton":
        return True
    return "search" in _tokens_from_text(visible_text or "")


def _looks_like_window_titlebar_button(
    visible_text: str,
    automation_id: str,
    ctype: str,
    window_title: str,
    rect: tuple[int, int, int, int],
) -> bool:
    if ctype not in {"button", "splitbutton"}:
        return False
    raw_tokens = _tokens_from_text(" ".join((visible_text or "", automation_id or "")))
    if not (raw_tokens & {"close", "maximize", "minimize", "minimise", "restore"}):
        return False
    if rect[1] <= 44:
        return True
    compact_titlebar_shape = rect[2] <= 72 and rect[3] <= 48
    window_tokens = _tokens_from_text(window_title or "")
    return bool(compact_titlebar_shape and window_tokens & BROWSER_PROFILE_WINDOW_WORDS)


def _looks_like_browser_profile_chrome_button(
    visible_text: str,
    automation_id: str,
    ctype: str,
    window_title: str,
    rect: tuple[int, int, int, int],
) -> bool:
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
    raw_tokens = _tokens_from_text(" ".join((visible_text or "", automation_id or "")))
    return bool(raw_tokens & (BROWSER_PROFILE_TOKENS | BROWSER_PROFILE_LABEL_HINT_WORDS))


def _looks_like_browser_site_information_chrome_button(
    visible_text: str,
    automation_id: str,
    ctype: str,
    window_title: str,
    rect: tuple[int, int, int, int],
) -> bool:
    if ctype not in {"button", "splitbutton"}:
        return False
    raw_text = " ".join((visible_text or "", automation_id or "")).lower()
    raw_tokens = _tokens_from_text(raw_text)
    compact_chrome_shape = max(rect[2], rect[3]) <= 64
    if "site_info_lock" in raw_text and compact_chrome_shape:
        return True
    window_tokens = _tokens_from_text(window_title or "")
    if not (window_tokens & BROWSER_PROFILE_WINDOW_WORDS):
        return False
    if {"site", "info", "lock"} <= raw_tokens:
        return True
    if rect[1] > 72:
        return False
    control_tokens = _tokenize_control(_semantic_text(visible_text))
    return {"site", "information"} <= control_tokens or {"site", "info"} <= raw_tokens


def _looks_like_site_information_button(
    visible_text: str,
    ctype: str,
    window_title: str,
) -> bool:
    if ctype not in {"button", "splitbutton"}:
        return False
    window_tokens = _tokens_from_text(window_title or "")
    if window_tokens and not (window_tokens & BROWSER_PROFILE_WINDOW_WORDS):
        return False
    control_tokens = _tokenize_control(_semantic_text(visible_text))
    return "site_info_lock" in control_tokens or {"site", "information"} <= control_tokens


def _looks_like_browser_toolbar_button(
    visible_text: str,
    automation_id: str,
    ctype: str,
    window_title: str,
    rect: tuple[int, int, int, int],
) -> bool:
    if ctype not in {"button", "splitbutton"}:
        return False
    window_tokens = _tokens_from_text(window_title or "")
    if not (window_tokens & BROWSER_PROFILE_WINDOW_WORDS):
        return False
    automation_key = (automation_id or "").strip().lower()
    if automation_key.startswith("view_") or automation_key in BROWSER_CHROME_TOOLBAR_AUTOMATION_IDS:
        return True
    text_tokens = _tokens_from_text(visible_text or "")
    compact_toolbar_shape = max(rect[2], rect[3]) <= 56
    compact_text_toolbar_shape = rect[2] <= 96 and rect[3] <= 44 and rect[1] <= 72
    toolbar_words = text_tokens & BROWSER_CHROME_TOOLBAR_WORDS
    if compact_text_toolbar_shape and (
        {"tab", "actions", "menu"} <= text_tokens or {"vertical", "tabs"} <= text_tokens
    ):
        return True
    if toolbar_words and rect[1] > 144:
        return False
    return bool(toolbar_words and (compact_toolbar_shape or compact_text_toolbar_shape))


def _looks_like_browser_menu_button(
    visible_text: str,
    automation_id: str,
    ctype: str,
    window_title: str,
    rect: tuple[int, int, int, int],
) -> bool:
    if ctype not in {"button", "splitbutton"}:
        return False
    window_tokens = _tokens_from_text(window_title or "")
    if window_tokens and not (window_tokens & BROWSER_PROFILE_WINDOW_WORDS):
        return False
    control_tokens = _tokens_from_text(" ".join((visible_text or "", automation_id or "")))
    if control_tokens == {"chrome"}:
        return True
    compact_topbar_shape = rect[2] <= 96 and rect[3] <= 48 and rect[1] <= 72
    return bool(compact_topbar_shape and {"settings", "more"} <= control_tokens)


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
    if not _looks_like_browser_new_tab_button(visible_text, "", ctype, window_title):
        return False
    if not (instruction_tokens & BROWSER_NEW_TAB_RELATED_REQUEST_WORDS):
        return False
    if _instruction_mentions_tab_context(instruction):
        return False
    return True


def _looks_like_browser_new_tab_button(
    visible_text: str,
    automation_id: str,
    ctype: str,
    window_title: str,
) -> bool:
    if ctype not in {"button", "splitbutton"}:
        return False
    window_tokens = _tokenize_control(window_title or "")
    if window_tokens and not (window_tokens & BROWSER_PROFILE_WINDOW_WORDS):
        return False
    raw_control_tokens = _tokens_from_text(" ".join((visible_text or "", automation_id or "")))
    return {"new", "tab"} <= raw_control_tokens


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
    requested_on = turn_instruction == "on" or bool(instruction_tokens & CHECKBOX_ON_ACTION_WORDS)
    requested_off = turn_instruction == "off" or bool(instruction_tokens & CHECKBOX_OFF_ACTION_WORDS)
    if requested_on == requested_off:
        return False

    control_tokens = _tokens_from_text(control_text)
    control_on = turn_control == "on" or bool(control_tokens & CHECKBOX_ON_ACTION_WORDS)
    control_off = turn_control == "off" or bool(control_tokens & CHECKBOX_OFF_ACTION_WORDS)
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
        or _candidate_edit_action_context_mismatch(
            instruction,
            " ".join((visible_text or "", automation_id or "")),
        )
        or _candidate_action_for_open_view_mismatch(
            instruction,
            " ".join((visible_text or "", automation_id or "")),
        )
        or _candidate_action_for_generic_object_request_mismatch(
            instruction,
            " ".join((visible_text or "", automation_id or "")),
        )
        or _confirm_action_context_mismatch(instruction, visible_text, automation_id)
        or _filter_reset_action_mismatch(
            instruction,
            " ".join((visible_text or "", automation_id or "")),
        )
        or _sort_direction_action_mismatch(
            instruction,
            " ".join((visible_text or "", automation_id or "")),
        )
        or _search_filter_action_mismatch(
            instruction,
            " ".join((visible_text or "", automation_id or "")),
        )
        or _add_remove_action_mismatch(
            instruction,
            " ".join((visible_text or "", automation_id or "")),
        )
        or _generic_file_transfer_alias_mismatch(
            instruction,
            " ".join((visible_text or "", automation_id or "")),
        )
        or _clipboard_copy_context_mismatch(
            instruction,
            " ".join((visible_text or "", automation_id or "")),
        )
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
        or _check_in_out_action_mismatch(
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


def _candidate_edit_action_context_mismatch(instruction: str, candidate_text: str) -> bool:
    instruction_raw_tokens = _tokens_from_text(instruction)
    if not (instruction_raw_tokens & OPEN_VIEW_REQUEST_WORDS):
        return False
    if instruction_raw_tokens & EDIT_ACTION_WORDS:
        return False
    candidate_tokens = _tokenize_control(candidate_text) | _tokens_from_text(candidate_text)
    if not (candidate_tokens & EDIT_ACTION_WORDS):
        return False
    instruction_objects = _object_token_variants(
        _action_object_tokens(
            _tokenize_instruction(instruction) | instruction_raw_tokens,
            OPEN_VIEW_REQUEST_WORDS,
            ACTION_OBJECT_STOPWORDS,
        )
    )
    candidate_objects = _object_token_variants(
        _action_object_tokens(
            candidate_tokens,
            EDIT_ACTION_WORDS,
            ACTION_OBJECT_STOPWORDS,
        )
    )
    return bool(instruction_objects and candidate_objects and instruction_objects & candidate_objects)


def _candidate_action_for_open_view_mismatch(instruction: str, candidate_text: str) -> bool:
    instruction_raw_tokens = _tokens_from_text(instruction)
    if not (instruction_raw_tokens & OPEN_VIEW_REQUEST_WORDS):
        return False
    instruction_tokens = _tokenize_instruction(instruction) | instruction_raw_tokens
    candidate_raw_tokens = _tokens_from_text(candidate_text)
    if not candidate_raw_tokens:
        return False
    instruction_objects = _object_token_variants(
        _action_object_tokens(
            instruction_tokens,
            OPEN_VIEW_REQUEST_WORDS,
            ACTION_OBJECT_STOPWORDS,
        )
    )
    if not instruction_objects:
        return False
    for family in OPEN_VIEW_CANDIDATE_ACTION_FAMILIES:
        if not (candidate_raw_tokens & family):
            continue
        if instruction_raw_tokens & family:
            return False
        candidate_objects = _object_token_variants(
            _action_object_tokens(candidate_raw_tokens, family, ACTION_OBJECT_STOPWORDS)
        )
        if instruction_objects & candidate_objects:
            return True
    return False


def _candidate_action_for_generic_object_request_mismatch(
    instruction: str,
    candidate_text: str,
) -> bool:
    instruction_raw_tokens = _tokens_from_text(instruction)
    if not (instruction_raw_tokens & GENERIC_OBJECT_REQUEST_WORDS):
        return False
    instruction_tokens = _tokenize_instruction(instruction) | instruction_raw_tokens
    candidate_raw_tokens = _tokens_from_text(candidate_text)
    if not candidate_raw_tokens:
        return False
    instruction_objects = _object_token_variants(
        _action_object_tokens(
            instruction_tokens,
            GENERIC_OBJECT_REQUEST_WORDS,
            GENERIC_OBJECT_REQUEST_STOPWORDS,
        )
    )
    if not instruction_objects:
        return False
    for family in GENERIC_OBJECT_CANDIDATE_ACTION_FAMILIES:
        if not (candidate_raw_tokens & family):
            continue
        if instruction_raw_tokens & family:
            return False
        candidate_objects = _object_token_variants(
            _action_object_tokens(candidate_raw_tokens, family, ACTION_OBJECT_STOPWORDS)
        )
        if instruction_objects & candidate_objects:
            return True
    return False


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


def _table_cell_context_snap_result(
    ranked: list[tuple[float, SnapResult, str, str, int]],
    *,
    instruction: str,
    instruction_tokens: set[str],
    control_intents: set[str],
    surface_contexts: list[tuple[tuple[int, int, int, int], str, str]],
    row_contexts: list[tuple[tuple[int, int, int, int], str, str]],
    model_rect: tuple[int, int, int, int],
    confidence_floor: float,
) -> SnapResult | None:
    raw_tokens = _object_token_variants(_tokens_from_text(instruction))
    if not (
        control_intents & TABLE_CELL_CONTROL_TYPES
        or raw_tokens & {"cell", "cells", "column", "columns", "gridcell"}
    ):
        return None
    requested = raw_tokens - TABLE_CELL_CONTEXT_STOPWORDS
    if not requested:
        return None
    cells = [
        (score, result, semantic_text, ctype, window_rank)
        for score, result, semantic_text, ctype, window_rank in ranked
        if ctype in TABLE_CELL_CONTROL_TYPES
    ]
    if not cells:
        return None

    all_row_tokens: set[str] = set()
    all_column_tokens: set[str] = set()
    for _score, result, _semantic_text, _ctype, _window_rank in cells:
        all_row_tokens.update(
            _table_cell_row_context_tokens(
                result.rect,
                ranked=ranked,
                row_contexts=row_contexts,
            )
        )
        all_column_tokens.update(
            _table_cell_column_context_tokens(
                result.rect,
                surface_contexts=surface_contexts,
            )
        )
    requested_row = requested & all_row_tokens
    requested_column = requested & all_column_tokens
    requested_value = requested - all_row_tokens - all_column_tokens
    if not (requested_row or requested_column or requested_value):
        return None

    matches: list[tuple[float, SnapResult]] = []
    for score, result, semantic_text, _ctype, _window_rank in cells:
        row_tokens = _table_cell_row_context_tokens(
            result.rect,
            ranked=ranked,
            row_contexts=row_contexts,
        )
        if requested_row and not (row_tokens & requested_row):
            continue
        column_tokens = _table_cell_column_context_tokens(
            result.rect,
            surface_contexts=surface_contexts,
        )
        if requested_column and not (column_tokens & requested_column):
            continue
        value_tokens = _table_cell_value_tokens(semantic_text or result.matched_text)
        if requested_value and not (value_tokens & requested_value):
            continue
        if not any(existing.rect == result.rect for _existing_score, existing in matches):
            matches.append((score, result))

    if len(matches) == 1:
        score, result = matches[0]
        return SnapResult(
            rect=result.rect,
            confidence=max(confidence_floor, score),
            source=result.source,
            matched_text=result.matched_text,
        )
    if len(matches) > 1:
        score, result = max(matches, key=lambda item: item[0])
        return SnapResult(
            rect=result.rect,
            confidence=score,
            source=result.source,
            matched_text=result.matched_text,
            rejected_reason="fresh snap ambiguous",
        )
    if requested_row or requested_column:
        return SnapResult(
            rect=model_rect,
            confidence=0.0,
            source="uia",
            rejected_reason="fresh snap ambiguous",
        )
    return None


def _table_cell_value_tokens(text: str) -> set[str]:
    return _object_token_variants(
        (_tokenize_control(_semantic_text(text)) | _tokens_from_text(text))
        - TABLE_CELL_CONTEXT_STOPWORDS
        - TABLE_CELL_CONTROL_TYPES
    )


def _table_cell_column_context_tokens(
    rect: tuple[int, int, int, int],
    *,
    surface_contexts: list[tuple[tuple[int, int, int, int], str, str]],
) -> set[str]:
    tokens: set[str] = set()
    for context_rect, context_text, context_type in surface_contexts:
        if context_type != "headeritem":
            continue
        if not _header_surface_context_rect_matches(context_rect, rect):
            continue
        tokens.update(
            _object_token_variants(
                _tokenize_control(_semantic_text(context_text))
                | _tokens_from_text(context_text)
            )
        )
    return tokens - TABLE_CELL_CONTEXT_STOPWORDS


def _table_cell_row_context_tokens(
    rect: tuple[int, int, int, int],
    *,
    ranked: list[tuple[float, SnapResult, str, str, int]],
    row_contexts: list[tuple[tuple[int, int, int, int], str, str]],
) -> set[str]:
    tokens: set[str] = set()
    for context_rect, context_text, context_type in row_contexts:
        if context_type in TABLE_CELL_ROW_LABEL_CONTROL_TYPES:
            if not _same_row_left_label_rect_matches(context_rect, rect):
                continue
        elif not _row_context_rect_matches(context_rect, rect):
            continue
        tokens.update(
            _object_token_variants(
                _tokenize_control(_semantic_text(context_text))
                | _tokens_from_text(context_text)
            )
        )
    if tokens:
        return tokens - TABLE_CELL_CONTEXT_STOPWORDS
    for _score, result, semantic_text, ctype, _window_rank in ranked:
        if ctype not in TABLE_CELL_CONTROL_TYPES:
            continue
        if result.rect == rect:
            continue
        if not _same_row_left_label_rect_matches(result.rect, rect):
            continue
        tokens.update(
            _object_token_variants(
                _tokenize_control(_semantic_text(semantic_text or result.matched_text))
                | _tokens_from_text(semantic_text or result.matched_text)
            )
        )
    return tokens - TABLE_CELL_CONTEXT_STOPWORDS


def _option_parent_context_snap_result(
    ranked: list[tuple[float, SnapResult, str, str, int]],
    *,
    instruction: str,
    instruction_tokens: set[str],
    control_intents: set[str],
    option_contexts: list[tuple[tuple[int, int, int, int], str, str]],
    parent_contexts: list[tuple[tuple[int, int, int, int], str, str]],
    model_rect: tuple[int, int, int, int],
    confidence_floor: float,
) -> SnapResult | None:
    literal_tokens = _object_token_variants(_tokens_from_text(instruction))
    request_tokens = _object_token_variants(_tokens_from_text(instruction) | instruction_tokens)
    if not request_tokens:
        return None
    option_request = bool(
        literal_tokens
        & {
            "choice",
            "choices",
            "context",
            "drop",
            "down",
            "dropdown",
            "list",
            "menu",
            "menuitem",
            "option",
            "options",
            "picker",
            "selector",
        }
        or {"menu", "item"} <= literal_tokens
    )
    if not option_request:
        return None

    option_items = _option_context_snap_items(
        ranked,
        option_contexts=option_contexts,
        confidence_floor=confidence_floor,
    )
    if not option_items:
        return None

    label_matches: list[tuple[float, SnapResult, str, str]] = []
    for score, result, semantic_text, ctype in option_items:
        label_tokens = _option_context_label_tokens(semantic_text or result.matched_text)
        if label_tokens and request_tokens & label_tokens:
            label_matches.append((score, result, semantic_text, ctype))
    if not label_matches:
        return None

    scoped_matches: list[tuple[float, SnapResult, str, str]] = []
    has_parent_context = False
    for score, result, semantic_text, ctype in label_matches:
        label_tokens = _option_context_label_tokens(semantic_text or result.matched_text)
        parent_request = _option_parent_request_tokens(literal_tokens, label_tokens)
        if parent_request and _option_parent_request_has_context(
            parent_request,
            parent_contexts,
        ):
            has_parent_context = True
            if _option_parent_context_matches(result.rect, parent_request, parent_contexts):
                scoped_matches.append((score, result, semantic_text, ctype))

    if scoped_matches:
        return _option_context_match_result(
            scoped_matches,
            model_rect=model_rect,
            confidence_floor=confidence_floor,
        )

    preferred_type_matches = _option_context_preferred_type_matches(
        label_matches,
        literal_tokens,
    )
    if preferred_type_matches:
        return _option_context_match_result(
            preferred_type_matches,
            model_rect=model_rect,
            confidence_floor=confidence_floor,
        )

    if has_parent_context and _same_label_option_duplicate_exists(label_matches):
        return _option_context_ambiguous_result(
            label_matches,
            model_rect=model_rect,
            confidence_floor=confidence_floor,
        )

    explicit_type_matches = [
        item
        for item in label_matches
        if _option_context_explicit_type_match(
            literal_tokens,
            control_intents,
            item[3],
        )
    ]
    if explicit_type_matches:
        return _option_context_match_result(
            explicit_type_matches,
            model_rect=model_rect,
            confidence_floor=confidence_floor,
        )

    if _same_label_option_duplicate_exists(label_matches):
        return _option_context_ambiguous_result(
            label_matches,
            model_rect=model_rect,
            confidence_floor=confidence_floor,
        )
    return None


def _option_context_snap_items(
    ranked: list[tuple[float, SnapResult, str, str, int]],
    *,
    option_contexts: list[tuple[tuple[int, int, int, int], str, str]],
    confidence_floor: float,
) -> list[tuple[float, SnapResult, str, str]]:
    items: list[tuple[float, SnapResult, str, str]] = []
    seen: set[tuple[tuple[int, int, int, int], str]] = set()
    for score, result, semantic_text, ctype, _window_rank in ranked:
        if ctype not in OPTION_CONTEXT_CONTROL_TYPES:
            continue
        key = (result.rect, ctype)
        if key in seen:
            continue
        seen.add(key)
        items.append((score, result, semantic_text, ctype))
    for rect, text, ctype in option_contexts:
        if ctype not in OPTION_CONTEXT_CONTROL_TYPES:
            continue
        key = (rect, ctype)
        if key in seen:
            continue
        seen.add(key)
        items.append(
            (
                confidence_floor,
                SnapResult(
                    rect=rect,
                    confidence=confidence_floor,
                    source="uia",
                    matched_text=text,
                ),
                text,
                ctype,
            )
        )
    return items


def _option_context_preferred_type_matches(
    matches: list[tuple[float, SnapResult, str, str]],
    raw_tokens: set[str],
) -> list[tuple[float, SnapResult, str, str]]:
    if not (
        raw_tokens & {"choice", "choices", "option", "options"}
        or "menuitem" in raw_tokens
        or {"menu", "item"} <= raw_tokens
    ):
        return []
    exact_option = [item for item in matches if item[3] == "option"]
    if exact_option and raw_tokens & {"choice", "choices", "option", "options"}:
        return exact_option
    preferred_types: set[str] = set()
    if raw_tokens & {"context", "drop", "down", "dropdown", "menu", "picker", "selector"}:
        preferred_types.add("menuitem")
    if raw_tokens & {"list"} and not preferred_types:
        preferred_types.add("listitem")
    if not preferred_types:
        return []
    preferred = [item for item in matches if item[3] in preferred_types]
    return preferred if preferred else []


def _option_context_match_result(
    matches: list[tuple[float, SnapResult, str, str]],
    *,
    model_rect: tuple[int, int, int, int],
    confidence_floor: float,
) -> SnapResult:
    distinct: list[tuple[float, SnapResult]] = []
    for score, result, _semantic_text, _ctype in matches:
        if any(existing.rect == result.rect for _existing_score, existing in distinct):
            continue
        distinct.append((score, result))
    if len(distinct) == 1:
        score, result = distinct[0]
        return SnapResult(
            rect=result.rect,
            confidence=max(confidence_floor, score),
            source=result.source,
            matched_text=result.matched_text,
        )
    return _option_context_ambiguous_result(
        matches,
        model_rect=model_rect,
        confidence_floor=confidence_floor,
    )


def _option_context_ambiguous_result(
    matches: list[tuple[float, SnapResult, str, str]],
    *,
    model_rect: tuple[int, int, int, int],
    confidence_floor: float,
) -> SnapResult:
    if not matches:
        return SnapResult(
            rect=model_rect,
            confidence=0.0,
            source="uia",
            rejected_reason="fresh snap ambiguous",
        )
    score, result, _semantic_text, _ctype = max(
        matches,
        key=lambda item: (
            _iou(item[1].rect, model_rect),
            item[0],
        ),
    )
    return SnapResult(
        rect=result.rect,
        confidence=max(confidence_floor, score),
        source=result.source,
        matched_text=result.matched_text,
        rejected_reason="fresh snap ambiguous",
    )


def _same_label_option_duplicate_exists(
    matches: list[tuple[float, SnapResult, str, str]],
) -> bool:
    for index, (_score, result, semantic_text, ctype) in enumerate(matches):
        label_tokens = _option_context_label_tokens(semantic_text or result.matched_text)
        if not label_tokens:
            continue
        for _other_score, other_result, other_text, other_ctype in matches[index + 1 :]:
            if result.rect == other_result.rect and ctype == other_ctype:
                continue
            other_tokens = _option_context_label_tokens(other_text or other_result.matched_text)
            if other_tokens and label_tokens & other_tokens:
                return True
    return False


def _option_context_explicit_type_match(
    raw_tokens: set[str],
    control_intents: set[str],
    ctype: str,
) -> bool:
    option_words = raw_tokens & {"choice", "choices", "option", "options"}
    if ctype == "menuitem":
        return bool(
            option_words
            or "menuitem" in raw_tokens
            or {"menu", "item"} <= raw_tokens
            or raw_tokens & {"context", "menu"}
        )
    if ctype == "listitem":
        return bool(
            option_words
            or "listitem" in raw_tokens
            or {"list", "item"} <= raw_tokens
        )
    if ctype == "option":
        return bool(option_words)
    return False


def _option_parent_request_tokens(
    request_tokens: set[str],
    label_tokens: set[str],
) -> set[str]:
    tokens = set(request_tokens)
    tokens -= label_tokens
    tokens -= OPTION_PARENT_CONTEXT_STOPWORDS
    return {token for token in tokens if len(token) > 1 and not token.isdigit()}


def _option_parent_context_matches(
    rect: tuple[int, int, int, int],
    requested: set[str],
    parent_contexts: list[tuple[tuple[int, int, int, int], str, str]],
) -> bool:
    if not requested:
        return False
    requested_surfaces = requested & OPTION_PARENT_SURFACE_WORDS
    required_identity = requested - requested_surfaces
    for parent_rect, parent_text, parent_type in parent_contexts:
        if parent_type not in OPTION_CONTEXT_PARENT_TYPES:
            continue
        if not _option_parent_rect_matches(parent_rect, rect, parent_type):
            continue
        parent_tokens = _object_token_variants(
            _tokenize_control(_semantic_text(parent_text))
            | _tokens_from_text(parent_text)
            | {parent_type}
            | set(SURFACE_CONTEXT_TYPE_WORDS.get(parent_type, frozenset()))
        )
        if requested_surfaces and not (requested_surfaces & parent_tokens):
            continue
        if required_identity and not required_identity <= parent_tokens:
            continue
        if requested_surfaces or required_identity:
            return True
    return False


def _option_parent_request_has_context(
    requested: set[str],
    parent_contexts: list[tuple[tuple[int, int, int, int], str, str]],
) -> bool:
    if requested - OPTION_PARENT_SURFACE_WORDS:
        return True
    requested_surfaces = requested & OPTION_PARENT_SURFACE_WORDS
    if not requested_surfaces:
        return False
    for _parent_rect, parent_text, parent_type in parent_contexts:
        if parent_type not in {"group", "list", "menu", "pane"}:
            continue
        parent_tokens = _object_token_variants(
            _tokenize_control(_semantic_text(parent_text))
            | _tokens_from_text(parent_text)
            | {parent_type}
            | set(SURFACE_CONTEXT_TYPE_WORDS.get(parent_type, frozenset()))
        )
        if requested_surfaces & parent_tokens:
            return True
    return False


def _option_parent_rect_matches(
    parent_rect: tuple[int, int, int, int],
    child_rect: tuple[int, int, int, int],
    parent_type: str,
) -> bool:
    if parent_type == "menuitem":
        return _menu_path_parent_item_rect_matches(parent_rect, child_rect)
    return _contains_rect(_expand_rect(parent_rect, 4), child_rect)


def _menu_path_parent_item_rect_matches(
    parent_rect: tuple[int, int, int, int],
    child_rect: tuple[int, int, int, int],
) -> bool:
    parent_x, parent_y, parent_width, parent_height = parent_rect
    child_x, child_y, child_width, child_height = child_rect
    if min(parent_width, parent_height, child_width, child_height) <= 0:
        return False
    parent_right = parent_x + parent_width
    child_right = child_x + child_width
    horizontal_overlap = min(parent_right, child_right) - max(parent_x, child_x)
    if child_y >= parent_y + parent_height - 2:
        vertical_gap = child_y - (parent_y + parent_height)
        if vertical_gap <= max(72, parent_height * 3):
            left_aligned = abs(parent_x - child_x) <= max(18, parent_width * 0.35)
            if horizontal_overlap > 0 and left_aligned:
                return True
    parent_bottom = parent_y + parent_height
    child_bottom = child_y + child_height
    vertical_overlap = min(parent_bottom, child_bottom) - max(parent_y, child_y)
    if vertical_overlap < min(parent_height, child_height) * 0.45:
        return False
    horizontal_gap = child_x - parent_right
    if horizontal_gap < -4:
        return False
    return horizontal_gap <= max(96, parent_width * 0.75)


def _option_context_label_tokens(text: str) -> set[str]:
    return _object_token_variants(
        (_tokenize_control(_semantic_text(text)) | _tokens_from_text(text))
        - OPTION_PARENT_CONTEXT_STOPWORDS
        - OPTION_PARENT_SURFACE_WORDS
    )


def _row_context_rect_matches(
    row_rect: tuple[int, int, int, int],
    cell_rect: tuple[int, int, int, int],
) -> bool:
    if _contains_rect(_expand_rect(row_rect, 4), cell_rect):
        return True
    row_x, row_y, row_width, row_height = row_rect
    cell_x, cell_y, cell_width, cell_height = cell_rect
    if min(row_width, row_height, cell_width, cell_height) <= 0:
        return False
    row_right = row_x + row_width
    cell_right = cell_x + cell_width
    horizontal_overlap = min(row_right, cell_right) - max(row_x, cell_x)
    if horizontal_overlap < min(row_width, cell_width) * 0.45:
        return False
    cell_center_y = cell_y + cell_height / 2
    if row_y - 4 <= cell_center_y <= row_y + row_height + 4:
        return True
    vertical_overlap = min(row_y + row_height, cell_y + cell_height) - max(row_y, cell_y)
    return vertical_overlap >= min(row_height, cell_height) * 0.45


def _same_row_left_label_rect_matches(
    label_rect: tuple[int, int, int, int],
    cell_rect: tuple[int, int, int, int],
) -> bool:
    label_x, label_y, label_width, label_height = label_rect
    cell_x, cell_y, cell_width, cell_height = cell_rect
    if min(label_width, label_height, cell_width, cell_height) <= 0:
        return False
    label_right = label_x + label_width
    if label_right > cell_x + 4:
        return False
    vertical_overlap = min(label_y + label_height, cell_y + cell_height) - max(label_y, cell_y)
    if vertical_overlap < min(label_height, cell_height) * 0.45:
        return False
    horizontal_gap = max(0, cell_x - label_right)
    return horizontal_gap <= max(260, min(960, cell_width * 6))


def _neutral_same_label_state_option_snap_ambiguous(
    instruction: str,
    *,
    selected: SnapResult,
    selected_ctype: str,
    ranked: list[tuple[float, SnapResult, str, str, int]],
    state_option_contexts: list[tuple[tuple[int, int, int, int], str, str]] | None = None,
) -> bool:
    neutral_types = {"button", "checkbox", "radiobutton", "splitbutton"}
    if selected_ctype not in neutral_types:
        return False
    raw_tokens = _tokens_from_text(instruction)
    selected_tokens = _snap_state_option_label_tokens(selected.matched_text)
    if not selected_tokens:
        return False
    if selected_ctype in {"checkbox", "radiobutton"} and _same_type_state_option_duplicate_exists(
        selected=selected,
        selected_ctype=selected_ctype,
        selected_tokens=selected_tokens,
        ranked=ranked,
        state_option_contexts=state_option_contexts or [],
    ):
        return True
    if (
        raw_tokens & {
            "button",
            "buttons",
            "check",
            "checked",
            "checkbox",
            "radio",
            "radiobutton",
            "splitbutton",
            "switch",
            "toggle",
            "uncheck",
            "unchecked",
        }
        or {"check", "box"} <= raw_tokens
        or {"split", "button"} <= raw_tokens
    ):
        return False
    control_types = {selected_ctype}
    for _score, result, semantic_text, ctype, _window_rank in ranked:
        if ctype not in neutral_types:
            continue
        tokens = _snap_state_option_label_tokens(semantic_text or result.matched_text)
        if tokens and selected_tokens & tokens:
            control_types.add(ctype)
    return {"checkbox", "radiobutton"} <= control_types


def _same_type_state_option_duplicate_exists(
    *,
    selected: SnapResult,
    selected_ctype: str,
    selected_tokens: set[str],
    ranked: list[tuple[float, SnapResult, str, str, int]],
    state_option_contexts: list[tuple[tuple[int, int, int, int], str, str]],
) -> bool:
    for _score, result, semantic_text, ctype, _window_rank in ranked:
        if ctype != selected_ctype:
            continue
        if result.rect == selected.rect:
            continue
        tokens = _snap_state_option_label_tokens(semantic_text or result.matched_text)
        if tokens and selected_tokens & tokens:
            return True
    for rect, text, ctype in state_option_contexts:
        if ctype != selected_ctype:
            continue
        if rect == selected.rect:
            continue
        tokens = _snap_state_option_label_tokens(text)
        if tokens and selected_tokens & tokens:
            return True
    return False


def _duplicate_labelled_field_snap_ambiguous(
    instruction: str,
    *,
    selected: SnapResult,
    selected_ctype: str,
    snapped_field_label_contexts: list[tuple[tuple[int, int, int, int], str, str, int]],
) -> bool:
    if selected_ctype not in BLANK_FIELD_LABEL_CONTROL_TYPES:
        return False
    requested_words = _field_label_duplicate_request_words(instruction, selected_ctype)
    if not requested_words:
        return False
    requested_variants = _object_token_variants(requested_words) | _tokenize_control(
        " ".join(sorted(requested_words))
    )
    selected_label_words = _snap_field_direct_label_words(
        selected,
        selected_ctype,
        snapped_field_label_contexts,
    )
    if not selected_label_words:
        return False
    selected_variants = _object_token_variants(selected_label_words) | _tokenize_control(
        " ".join(sorted(selected_label_words))
    )
    if not requested_variants <= selected_variants:
        return False
    for rect, label_text, ctype, _window_rank in snapped_field_label_contexts:
        if ctype != selected_ctype or rect == selected.rect:
            continue
        label_words = set(_literal_word_sequence(label_text)) - PARTIAL_FIELD_LABEL_EXTRA_STOPWORDS
        if not label_words:
            continue
        label_variants = _object_token_variants(label_words) | _tokenize_control(
            " ".join(sorted(label_words))
        )
        if requested_variants <= label_variants:
            return True
    return False


def _field_label_duplicate_request_words(instruction: str, control_type: str) -> set[str]:
    requested_words = _field_label_request_words(instruction, control_type)
    if requested_words:
        return requested_words
    words = set(_literal_word_sequence(instruction))
    words -= PARTIAL_FIELD_LABEL_REQUEST_STOPWORDS
    words -= FIELD_ENTRY_ACTION_WORDS
    words -= OPEN_VIEW_REQUEST_WORDS
    words -= GENERIC_OBJECT_REQUEST_WORDS
    words -= {
        "a",
        "an",
        "below",
        "beneath",
        "for",
        "from",
        "in",
        "inside",
        "into",
        "of",
        "on",
        "the",
        "to",
        "under",
        "underneath",
        "within",
        "with",
    }
    return _object_token_variants(words)


def _snap_field_direct_label_words(
    selected: SnapResult,
    selected_ctype: str,
    snapped_field_label_contexts: list[tuple[tuple[int, int, int, int], str, str, int]],
) -> set[str]:
    best_words: set[str] = set()
    for rect, label_text, ctype, _window_rank in snapped_field_label_contexts:
        if ctype != selected_ctype or rect != selected.rect:
            continue
        words = set(_literal_word_sequence(label_text)) - PARTIAL_FIELD_LABEL_EXTRA_STOPWORDS
        if len(words) > len(best_words):
            best_words = words
    return best_words


def _snap_state_option_label_tokens(text: str) -> set[str]:
    return _object_token_variants(
        (_tokenize_control(text) | _tokens_from_text(text))
        - {
            "box",
            "button",
            "buttons",
            "check",
            "checkbox",
            "control",
            "option",
            "radio",
            "radiobutton",
            "split",
            "splitbutton",
            "switch",
            "toggle",
        }
    )


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


def _filter_reset_action_mismatch(instruction: str, candidate_text: str) -> bool:
    instruction_tokens = _tokens_from_text(instruction)
    if not (instruction_tokens & FILTER_RESET_ACTION_WORDS):
        return False
    if not (instruction_tokens & FILTER_RESET_CONTEXT_WORDS):
        return False
    candidate_tokens = _tokenize_control(candidate_text) | _tokens_from_text(candidate_text)
    if candidate_tokens & FILTER_RESET_ALLOWED_CONTROL_WORDS:
        return False
    return bool(candidate_tokens & FILTER_RESET_OBJECT_ONLY_WORDS)


def _sort_direction_action_mismatch(instruction: str, candidate_text: str) -> bool:
    instruction_tokens = _tokenize_instruction(instruction) | _tokens_from_text(instruction)
    candidate_tokens = _tokenize_control(candidate_text) | _tokens_from_text(candidate_text)
    requested_ascending = bool(instruction_tokens & SORT_ASCENDING_WORDS)
    requested_descending = bool(instruction_tokens & SORT_DESCENDING_WORDS)
    candidate_ascending = bool(candidate_tokens & SORT_ASCENDING_WORDS)
    candidate_descending = bool(candidate_tokens & SORT_DESCENDING_WORDS)
    if requested_ascending == requested_descending:
        return False
    if candidate_ascending == candidate_descending:
        return False
    return requested_ascending != candidate_ascending


def _search_filter_action_mismatch(instruction: str, candidate_text: str) -> bool:
    instruction_tokens = _tokenize_instruction(instruction) | _tokens_from_text(instruction)
    candidate_tokens = _tokenize_control(candidate_text) | _tokens_from_text(candidate_text)
    requested_search = bool(instruction_tokens & SEARCH_FILTER_SEARCH_WORDS)
    requested_filter = bool(instruction_tokens & SEARCH_FILTER_FILTER_WORDS)
    candidate_search = bool(candidate_tokens & SEARCH_FILTER_SEARCH_WORDS)
    candidate_filter = bool(candidate_tokens & SEARCH_FILTER_FILTER_WORDS)
    if requested_search and not requested_filter and candidate_filter and not candidate_search:
        return True
    return bool(requested_filter and not requested_search and candidate_search and not candidate_filter)


def _add_remove_action_mismatch(instruction: str, candidate_text: str) -> bool:
    instruction_tokens = _tokens_from_text(instruction)
    candidate_tokens = _tokens_from_text(candidate_text)
    requested_add = bool(instruction_tokens & ADD_ACTION_WORDS)
    requested_remove = bool(instruction_tokens & REMOVE_ACTION_WORDS)
    candidate_add = bool(candidate_tokens & ADD_ACTION_WORDS)
    candidate_remove = bool(candidate_tokens & REMOVE_ACTION_WORDS)
    return bool(
        (requested_add and not requested_remove and candidate_remove and not candidate_add)
        or (requested_remove and not requested_add and candidate_add and not candidate_remove)
    )


def _generic_file_transfer_alias_mismatch(instruction: str, candidate_text: str) -> bool:
    instruction_tokens = _tokens_from_text(instruction)
    if not (instruction_tokens & FILE_IDENTITY_WORDS):
        return False
    requested = instruction_tokens & TRANSFER_ACTION_WORDS
    if not requested:
        return False
    candidate_tokens = _tokens_from_text(candidate_text)
    candidate_transfer = candidate_tokens & TRANSFER_ACTION_WORDS
    if not candidate_transfer:
        return False
    return not bool(requested & candidate_tokens)


def _clipboard_copy_context_mismatch(instruction: str, candidate_text: str) -> bool:
    instruction_tokens = _tokens_from_text(instruction)
    protected_context = instruction_tokens & CLIPBOARD_COPY_EXACT_CONTEXT_WORDS
    if not protected_context:
        return False
    candidate_tokens = _tokens_from_text(candidate_text)
    if "copy" in instruction_tokens and candidate_tokens & DUPLICATE_ACTION_WORDS and "copy" not in candidate_tokens:
        return True
    if instruction_tokens & DUPLICATE_ACTION_WORDS and "copy" in candidate_tokens and not (
        candidate_tokens & DUPLICATE_ACTION_WORDS
    ):
        return True
    return False


def _object_only_action_context_mismatch(instruction: str, candidate_text: str) -> bool:
    instruction_raw_tokens = _tokens_from_text(instruction)
    if not instruction_raw_tokens:
        return False
    candidate_raw_tokens = _tokens_from_text(candidate_text)
    if not candidate_raw_tokens:
        return False
    for family in EXCLUSIVE_ACTION_FAMILIES:
        if not (instruction_raw_tokens & family):
            continue
        if candidate_raw_tokens & family:
            return False
        instruction_objects = _object_token_variants(
            _instruction_action_object_tokens(instruction, family)
        )
        if not instruction_objects:
            continue
        candidate_objects = _object_token_variants(candidate_raw_tokens & instruction_objects)
        if not candidate_objects:
            continue
        candidate_non_objects = (
            candidate_raw_tokens
            - instruction_objects
            - ACTION_OBJECT_STOPWORDS
            - FILE_IDENTITY_WORDS
        )
        if not candidate_non_objects:
            return True
    return False


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
    for family in SAME_ACTION_OBJECT_FAMILIES:
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
    for family in SAME_ACTION_OBJECT_FAMILIES:
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


def _check_in_out_action_mismatch(
    instruction: str,
    candidate_text: str,
) -> bool:
    instruction_kind = _check_in_out_action_kind(instruction)
    if not instruction_kind:
        return False
    control_kind = _check_in_out_action_kind(candidate_text)
    return bool(control_kind and control_kind != instruction_kind)


def _check_in_out_action_kind(text: str) -> str:
    words = _literal_word_sequence(text)
    for first, second in zip(words, words[1:]):
        if first == "check" and second in {"in", "out"}:
            return second
    return ""


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
    if not (raw_tokens & CONTEXTUAL_NAV_ITEM_CONTAINER_WORDS):
        return False
    window_tokens = _tokens_from_text(window_title or "")
    return bool(window_tokens & BROWSER_PROFILE_WINDOW_WORDS)


def _explicit_control_type_mismatch(
    instruction: str,
    ctype: str,
    visible_text: str,
    automation_id: str,
    control_intents: set[str],
) -> bool:
    requested_types = _explicit_strict_role_control_types(instruction)
    if not requested_types:
        return False
    if ctype in requested_types:
        return False
    if (
        requested_types == {"checkbox"}
        and ctype in {"button", "splitbutton"}
        and _control_matches_effective_intent(
            ctype,
            visible_text,
            automation_id,
            instruction,
            control_intents,
        )
    ):
        return False
    return True


def _explicit_strict_role_control_types(instruction: str) -> set[str]:
    raw_tokens = _tokens_from_text(instruction)
    requested: set[str] = set()
    if (
        raw_tokens & {"tab", "tabs", "tabitem"}
        and not raw_tokens & {"bookmark", "close", "dismiss", "favorite", "new", "search", "star"}
    ):
        requested.add("tabitem")
    literal_words = _literal_word_sequence(instruction)
    if raw_tokens & {"checkbox"} or {"check", "box"} <= raw_tokens:
        requested.add("checkbox")
    if literal_words and literal_words[-1] == "toggle":
        requested.add("checkbox")
    if "slider" in raw_tokens:
        requested.add("slider")
    if raw_tokens & {"hyperlink", "link"} and not raw_tokens & {
        "copy",
        "external",
        "new",
        "share",
        "tab",
        "window",
    }:
        requested.add("hyperlink")
    dropdown_requested = (
        raw_tokens & {"combo", "combobox", "dropdown", "picker", "selector"}
        or {"drop", "down"} <= raw_tokens
    )
    if dropdown_requested and raw_tokens & {"choice", "choices", "option", "options"}:
        requested.add("menuitem")
        requested.add("option")
    return requested


def _dropdown_launcher_option_mismatch(
    instruction: str,
    ctype: str,
    visible_text: str = "",
    automation_id: str = "",
) -> bool:
    if ctype not in {"menuitem", "option"}:
        return False
    raw_tokens = _tokens_from_text(instruction)
    dropdown_requested = (
        raw_tokens & {"combo", "combobox", "dropdown", "picker", "selector"}
        or {"drop", "down"} <= raw_tokens
    )
    if not dropdown_requested:
        return False
    control_tokens = _tokens_from_text(" ".join((visible_text or "", automation_id or "")))
    if control_tokens & {"dropdown", "menu"} or {"drop", "down"} <= control_tokens:
        return False
    return not bool(raw_tokens & {"choice", "choices", "option", "options"})


def _surface_scoped_action_match(
    instruction: str,
    instruction_tokens: set[str],
    semantic_text: str,
    rect: tuple[int, int, int, int],
    surface_contexts: list[tuple[tuple[int, int, int, int], str, str]],
) -> bool:
    if not surface_contexts:
        return False
    raw_tokens = _tokens_from_text(instruction)
    requested_surfaces = _object_token_variants(raw_tokens) & CLOSE_CONTEXT_TARGET_WORDS
    if not requested_surfaces:
        return False
    control_tokens = _tokenize_control(_semantic_text(semantic_text))
    action_tokens = instruction_tokens & control_tokens & AUTOMATION_ONLY_ACTION_MATCH_WORDS
    if not action_tokens:
        return False
    if not _surface_context_request_applies(instruction, action_tokens):
        return False
    requested_context = _object_token_variants(
        (raw_tokens | instruction_tokens)
        - action_tokens
        - ACTION_OBJECT_STOPWORDS
        - GENERIC_OBJECT_REQUEST_WORDS
        - {"a", "an", "for", "from", "in", "inside", "on", "the", "this", "that", "with", "within"}
    )
    required_identity = requested_context - CLOSE_CONTEXT_TARGET_WORDS
    for context_rect, context_text, context_type in surface_contexts:
        if context_type == "headeritem":
            if _rect_inside_surface_context_type(rect, surface_contexts, {"menu", "toolbar"}):
                continue
            if not _header_surface_context_rect_matches(context_rect, rect):
                continue
        elif not _contains_rect(_expand_rect(context_rect, 4), rect):
            continue
        context_tokens = _object_token_variants(
            _tokenize_control(_semantic_text(context_text))
            | _tokens_from_text(context_text)
            | {context_type}
            | set(SURFACE_CONTEXT_TYPE_WORDS.get(context_type, frozenset()))
        )
        if not (requested_surfaces & context_tokens):
            continue
        if required_identity and not required_identity <= context_tokens:
            continue
        return True
    return False


def _rect_inside_surface_context_type(
    rect: tuple[int, int, int, int],
    surface_contexts: list[tuple[tuple[int, int, int, int], str, str]],
    context_types: set[str],
) -> bool:
    return any(
        context_type in context_types
        and _contains_rect(_expand_rect(context_rect, 3), rect)
        for context_rect, _context_text, context_type in surface_contexts
    )


def _header_surface_context_rect_matches(
    header_rect: tuple[int, int, int, int],
    rect: tuple[int, int, int, int],
) -> bool:
    header_x, header_y, header_width, header_height = header_rect
    x, y, width, height = rect
    if min(header_width, header_height, width, height) <= 0:
        return False
    if header_y >= y:
        return False
    header_right = header_x + header_width
    right = x + width
    horizontal_overlap = min(header_right, right) - max(header_x, x)
    if horizontal_overlap <= 0:
        return False
    overlap_fraction = horizontal_overlap / max(1, min(header_width, width))
    if overlap_fraction < 0.45:
        return False
    vertical_gap = y - (header_y + header_height)
    return vertical_gap <= max(120, height * 5)


def _surface_context_action_mismatch(
    instruction: str,
    instruction_tokens: set[str],
    semantic_text: str,
    rect: tuple[int, int, int, int],
    surface_contexts: list[tuple[tuple[int, int, int, int], str, str]],
) -> bool:
    raw_tokens = _tokens_from_text(instruction)
    if not (_object_token_variants(raw_tokens) & CLOSE_CONTEXT_TARGET_WORDS):
        return False
    control_tokens = _tokenize_control(_semantic_text(semantic_text))
    action_tokens = instruction_tokens & control_tokens & AUTOMATION_ONLY_ACTION_MATCH_WORDS
    if not action_tokens:
        return False
    if not _surface_context_request_applies(instruction, action_tokens):
        return False
    return not _surface_scoped_action_match(
        instruction,
        instruction_tokens,
        semantic_text,
        rect,
        surface_contexts,
    )


def _row_context_action_mismatch(
    instruction: str,
    instruction_tokens: set[str],
    semantic_text: str,
    rect: tuple[int, int, int, int],
    surface_contexts: list[tuple[tuple[int, int, int, int], str, str]],
) -> bool:
    control_tokens = _tokenize_control(_semantic_text(semantic_text))
    action_tokens = instruction_tokens & control_tokens & AUTOMATION_ONLY_ACTION_MATCH_WORDS
    if not action_tokens:
        return False
    requested_objects = _object_token_variants(
        (_tokens_from_text(instruction) | instruction_tokens)
        - action_tokens
        - ACTION_OBJECT_STOPWORDS
        - GENERIC_OBJECT_REQUEST_WORDS
        - CLOSE_CONTEXT_TARGET_WORDS
        - frozenset({"a", "an", "for", "from", "in", "inside", "on", "the", "this", "that", "with", "within"})
    )
    if not requested_objects:
        return False
    containing_row_tokens: set[str] = set()
    matching_row_exists = False
    for context_rect, context_text, context_type in surface_contexts:
        if context_type not in ROW_CONTEXT_CONTROL_TYPES:
            continue
        context_tokens = _object_token_variants(
            _tokenize_control(_semantic_text(context_text)) | _tokens_from_text(context_text)
        )
        if context_tokens & requested_objects:
            matching_row_exists = True
        if _contains_rect(_expand_rect(context_rect, 4), rect):
            containing_row_tokens.update(context_tokens)
    if not containing_row_tokens:
        return False
    if containing_row_tokens & requested_objects:
        return False
    return matching_row_exists


def _surface_context_request_applies(
    instruction: str,
    action_tokens: set[str],
) -> bool:
    words = _literal_word_sequence(instruction)
    if set(words) & {"in", "inside", "on", "within"}:
        return True
    for index, word in enumerate(words):
        if not (_object_token_variants({word}) & CLOSE_CONTEXT_TARGET_WORDS):
            continue
        later_tokens = _object_token_variants(set(words[index + 1 :]))
        if later_tokens & action_tokens:
            return True
    return False


def _control_matches_effective_intent(
    ctype: str,
    visible_text: str,
    automation_id: str,
    instruction: str,
    control_intents: set[str],
) -> bool:
    if ctype in control_intents:
        return True
    if ctype in {"row", "tableitem"} and control_intents & {"dataitem", "listitem"}:
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


def _specific_settings_context_mismatch(
    instruction: str,
    instruction_tokens: set[str],
    candidate_text: str,
) -> bool:
    raw_tokens = _tokens_from_text(instruction)
    if not (raw_tokens & SETTINGS_CONTEXT_WORDS):
        return False
    requested = _object_token_variants(raw_tokens | set(instruction_tokens))
    if not (requested & SETTINGS_CONTEXT_WORDS):
        return False
    candidate_tokens = _object_token_variants(
        _tokenize_control(_semantic_text(candidate_text))
    )
    if not (candidate_tokens & SETTINGS_CONTEXT_WORDS):
        return False
    requested_specific = (
        requested
        - SETTINGS_CONTEXT_WORDS
        - OPEN_VIEW_REQUEST_WORDS
        - GENERIC_OBJECT_REQUEST_WORDS
        - BROWSER_PAGE_TARGET_WORDS
        - BROWSER_CHROME_APP_CONTEXT_WORDS
        - TASKBAR_WINDOW_WORDS
        - SETTINGS_SPECIFIC_REQUEST_STOPWORDS
        - SURFACE_CONTEXT_TYPE_WORDS.get("window", frozenset())
        - frozenset(
            {
                "a",
                "an",
                "for",
                "from",
                "in",
                "inside",
                "of",
                "on",
                "the",
                "to",
                "within",
            }
        )
    )
    if not requested_specific:
        return False
    candidate_specific = candidate_tokens - SETTINGS_CONTEXT_WORDS
    return not requested_specific <= candidate_specific


def _bare_action_extended_label_mismatch(
    instruction: str,
    candidate_text: str,
    ctype: str,
) -> bool:
    if ctype not in TIGHT_ACTION_SNAP_CONTROL_TYPES and ctype not in ROW_CONTEXT_CONTROL_TYPES:
        return False
    requested_sequence = _bare_action_request_sequence(instruction)
    if len(requested_sequence) != 1:
        return False
    candidate_sequence = tuple(_literal_word_sequence(candidate_text))
    if len(candidate_sequence) <= 1:
        return False
    requested_word = requested_sequence[0]
    if requested_word not in BARE_EXTENDED_ACTION_LABEL_WORDS:
        return False
    if requested_word not in candidate_sequence:
        return False
    candidate_tokens = set(candidate_sequence)
    requested_tokens = _literal_words_from_text(instruction)
    return bool(_bare_extended_label_extra_tokens(candidate_tokens, requested_tokens))


def _state_control_partial_visible_label_mismatch(
    instruction: str,
    visible_text: str,
    ctype: str,
) -> bool:
    if ctype not in PARTIAL_STATE_LABEL_CONTROL_TYPES:
        return False
    raw_tokens = _tokens_from_text(instruction)
    if raw_tokens & (
        CHECKBOX_ON_ACTION_WORDS
        | CHECKBOX_OFF_ACTION_WORDS
        | GENERIC_VISIBILITY_ACTION_WORDS
        | {"off", "on", "turn"}
    ):
        return False
    label_words = set(_literal_word_sequence(visible_text)) - PARTIAL_STATE_LABEL_EXTRA_STOPWORDS
    if len(label_words) <= 1:
        return False
    requested_words = _state_control_partial_label_request_words(instruction)
    if len(requested_words) != 1:
        return False
    requested_word = next(iter(requested_words))
    if requested_word not in label_words:
        return False
    return bool(label_words - requested_words)


def _state_control_partial_label_request_words(instruction: str) -> set[str]:
    return {
        word
        for word in _literal_word_sequence(instruction)
        if word not in PARTIAL_STATE_LABEL_REQUEST_STOPWORDS
    }


def _bare_search_filter_extended_label_mismatch(
    instruction: str,
    candidate_text: str,
) -> bool:
    requested_sequence = _bare_action_request_sequence(instruction)
    if len(requested_sequence) != 1 or requested_sequence[0] not in SEARCH_ACTION_WORDS:
        return False
    candidate_words = set(_literal_word_sequence(candidate_text))
    return requested_sequence[0] in candidate_words and bool(candidate_words & {"filter", "filters"})


def _bare_extended_label_extra_tokens(
    candidate_tokens: set[str],
    requested_tokens: set[str],
) -> set[str]:
    extra_tokens = candidate_tokens - requested_tokens - BARE_EXTENDED_ACTION_LABEL_HINT_WORDS
    if candidate_tokens & BARE_EXTENDED_ACTION_LABEL_HINT_WORDS:
        extra_tokens = {token for token in extra_tokens if len(token) > 1}
    return extra_tokens


def _bare_action_request_sequence(instruction: str) -> tuple[str, ...]:
    excluded_words = (
        OPEN_VIEW_REQUEST_WORDS
        | (GENERIC_OBJECT_REQUEST_WORDS - frozenset({"find", "go", "search"}))
        | frozenset(
            {
                "a",
                "an",
                "at",
                "button",
                "by",
                "control",
                "for",
                "from",
                "in",
                "inside",
                "of",
                "on",
                "now",
                "please",
                "the",
                "this",
                "that",
                "to",
                "with",
                "within",
            }
        )
    )
    requested_words = tuple(
        word for word in _literal_word_sequence(instruction) if word not in excluded_words
    )
    if requested_words:
        return requested_words
    fallback_words = [
        word
        for word in _literal_word_sequence(instruction)
        if word
        not in {
            "a",
            "an",
            "at",
            "button",
            "control",
            "hyperlink",
            "icon",
            "link",
            "menu",
            "menuitem",
            "the",
            "this",
            "that",
        }
    ]
    while fallback_words and fallback_words[0] in {
        "choose",
        "click",
        "focus",
        "hit",
        "press",
        "select",
        "tap",
        "use",
    }:
        fallback_words.pop(0)
    if len(fallback_words) == 1 and fallback_words[0] in OPEN_VIEW_REQUEST_WORDS:
        return (fallback_words[0],)
    return ()


def _literal_words_from_text(text: str) -> set[str]:
    return set(_literal_word_sequence(text))


def _literal_word_sequence(text: str) -> list[str]:
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", text or "")
    spaced = re.sub(r"[_\-.]+", " ", spaced)
    return re.findall(r"[a-z0-9]+", spaced.lower())


def _is_x_symbol_text(text: str) -> bool:
    return (text or "").strip().lower() in X_SYMBOL_TEXTS


def _semantic_mismatch_targets_model_rect(
    candidate_rect: tuple[int, int, int, int],
    model_rect: tuple[int, int, int, int],
) -> bool:
    if _iou(candidate_rect, model_rect) >= SEMANTIC_MISMATCH_IOU_FLOOR:
        return True
    if _center_inside(model_rect, candidate_rect):
        return True
    return _substantial_overlap_fraction(candidate_rect, model_rect) >= 0.35


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

    if ctype in {"button", "menuitem", "option", "tabitem", "hyperlink", "splitbutton"}:
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


def _direct_card_target_request(instruction: str) -> bool:
    text = (instruction or "").strip().lower()
    text = re.sub(r"[.!?]+$", "", text).strip()
    match = re.match(
        r"^(?:click|focus|highlight|open|press|select|show|tap)\s+(?:the\s+)?(.+?)$",
        text,
    )
    if not match:
        return False
    requested_object = match.group(1).strip()
    if re.search(r"\b(?:for|from|in|inside|on|within|with)\b", requested_object):
        return False
    return bool(_tokens_from_text(requested_object) & {"card", "cards", "tile", "tiles"})


def _direct_card_target_result(
    ranked: list[tuple[float, SnapResult, str, str, int]],
    *,
    instruction_tokens: set[str],
    confidence_floor: float,
) -> SnapResult | None:
    matches: list[tuple[float, SnapResult]] = []
    for score, result, semantic_text, ctype, _window_rank in ranked:
        if ctype not in {"listitem", "treeitem"}:
            continue
        candidate_tokens = _tokenize_control(_semantic_text(semantic_text))
        if not candidate_tokens or not (candidate_tokens & instruction_tokens):
            continue
        matches.append((score, result))
    if not matches:
        return None
    if len(matches) > 1:
        return SnapResult(
            rect=matches[0][1].rect,
            confidence=0.0,
            source=matches[0][1].source,
            matched_text=matches[0][1].matched_text,
            rejected_reason="card target ambiguous",
        )
    score, result = matches[0]
    return SnapResult(
        rect=result.rect,
        confidence=max(confidence_floor, score),
        source=result.source,
        matched_text=result.matched_text,
    )


def _tighter_contained_snap_result(
    ranked: list[tuple[float, SnapResult, str, str, int]],
    *,
    best_result: SnapResult,
    best_semantic_text: str,
    best_ctype: str,
    instruction: str,
    instruction_tokens: set[str],
    control_intents: set[str],
    confidence_floor: float,
) -> SnapResult | None:
    if _explicit_container_snap_request(instruction, best_ctype, control_intents):
        return None
    best_area = max(1, best_result.rect[2] * best_result.rect[3])
    matches: list[tuple[float, SnapResult]] = []
    for score, result, semantic_text, ctype, _window_rank in ranked:
        if result.rect == best_result.rect:
            continue
        candidate_area = max(1, result.rect[2] * result.rect[3])
        if best_area < candidate_area * 1.8:
            continue
        if not _contains_rect(_expand_rect(best_result.rect, 4), result.rect):
            continue
        if not _contained_snap_candidate_matches_request(
            best_semantic_text,
            best_ctype,
            semantic_text,
            ctype,
            instruction,
            instruction_tokens,
            control_intents,
        ):
            continue
        if not any(existing.rect == result.rect for _score, existing in matches):
            matches.append((score, result))
    if not matches:
        return None
    if len(matches) > 1:
        return SnapResult(
            rect=best_result.rect,
            confidence=0.0,
            source=best_result.source,
            matched_text=best_result.matched_text,
            rejected_reason="contained control ambiguous",
        )
    score, result = matches[0]
    return SnapResult(
        rect=result.rect,
        confidence=max(confidence_floor, score),
        source=result.source,
        matched_text=result.matched_text,
    )


def _explicit_container_snap_request(
    instruction: str,
    ctype: str,
    control_intents: set[str],
) -> bool:
    if ctype not in {
        "dataitem",
        "listitem",
        "row",
        "tableitem",
        "treeitem",
        "cell",
        "datagridcell",
        "gridcell",
    }:
        return False
    if ctype in control_intents:
        return True
    raw_tokens = _tokens_from_text(instruction)
    if ctype in ROW_CONTEXT_CONTROL_TYPES:
        return bool(
            raw_tokens
            & {
                "card",
                "dataitem",
                "item",
                "listitem",
                "record",
                "row",
                "table",
                "tableitem",
                "treeitem",
            }
        )
    return bool(raw_tokens & {"cell", "column", "gridcell"})


def _contained_snap_candidate_matches_request(
    best_semantic_text: str,
    best_ctype: str,
    semantic_text: str,
    ctype: str,
    instruction: str,
    instruction_tokens: set[str],
    control_intents: set[str],
) -> bool:
    request_tokens = instruction_tokens | _tokens_from_text(instruction)
    if control_intents and _control_matches_effective_intent(
        ctype,
        semantic_text,
        "",
        "",
        control_intents,
    ) and not _control_matches_effective_intent(
        best_ctype,
        best_semantic_text,
        "",
        "",
        control_intents,
    ):
        return True
    candidate_tokens = _tokenize_control(_semantic_text(semantic_text))
    context_tokens = _tokenize_control(_semantic_text(best_semantic_text))
    if (
        candidate_tokens
        and context_tokens
        and request_tokens & candidate_tokens
        and request_tokens & context_tokens
    ):
        return True
    return _same_snap_intent(
        best_semantic_text,
        best_ctype,
        semantic_text,
        ctype,
        instruction_tokens,
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


def _substantial_overlap_fraction(
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
    intersection = iw * ih
    if intersection <= 0:
        return 0.0
    smallest_area = max(1, min(abs(aw * ah), abs(bw * bh)))
    return intersection / smallest_area


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
