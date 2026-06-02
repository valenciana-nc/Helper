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
BACKGROUND_TRANSIENT_POSITION_WORDS = frozenset({"behind", "under", "underneath"})
DISMISS_WINDOW_CONTEXT_WORDS = frozenset({"browser", "page", "tab", "window"})
CLEAR_CLOSE_WORDS = frozenset({"cancel", "close", "dismiss"})
CLEAR_CONTEXT_CONTROL_TYPES = frozenset({"combobox", "edit"})
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
AUDIO_OUTPUT_MUTE_WORDS = frozenset({"mute"})
AUDIO_OUTPUT_UNMUTE_WORDS = frozenset({"unmute"})
SPINNER_INCREMENT_WORDS = frozenset({"increase", "increment", "raise", "up"})
SPINNER_DECREMENT_WORDS = frozenset({"decrease", "decrement", "down", "lower"})
CARDINAL_DIRECTION_ACTION_PAIRS = (
    (frozenset({"up"}), frozenset({"down"})),
    (frozenset({"left"}), frozenset({"right"})),
)
CARDINAL_DIRECTION_ACTION_WORDS = frozenset().union(*(
    words for pair in CARDINAL_DIRECTION_ACTION_PAIRS for words in pair
))
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
CONFIRM_ACTION_WORDS = frozenset(
    {"apply", "checkmark", "complete", "confirm", "done", "finish", "ok", "okay", "tick"}
)
CANCEL_ACTION_WORDS = frozenset({"cancel"})
CONFIRM_CANCEL_ACTION_WORDS = CONFIRM_ACTION_WORDS | CANCEL_ACTION_WORDS
ADD_ACTION_WORDS = frozenset({"add", "create", "new", "plus"})
REMOVE_ACTION_WORDS = frozenset({"bin", "delete", "remove", "trash", "wastebasket"})
PAY_ACTION_WORDS = frozenset({"checkout", "pay"})
CART_ACTION_WORDS = frozenset({"bag", "basket", "cart"})
SIDE_EFFECT_ACTION_FAMILIES = (
    frozenset({"activate", "deactivate"}),
    frozenset({"connect", "disconnect"}),
    frozenset({"deploy", "publish", "release"}),
    frozenset({"disable", "enable"}),
    frozenset({"escalate", "resolve"}),
    frozenset({"install", "uninstall", "update"}),
    frozenset({"lock", "unlock"}),
    frozenset({"move", "rename"}),
    frozenset({"refresh", "reload", "sync"}),
    frozenset({"restore"}),
    frozenset({"start", "stop"}),
    frozenset({"subscribe", "unsubscribe"}),
)
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
NEUTRAL_ACTION_DESTINATION_WORDS = frozenset(
    {
        "about",
        "dashboard",
        "dashboards",
        "detail",
        "details",
        "info",
        "information",
        "menu",
        "menus",
        "option",
        "options",
        "overview",
        "overviews",
        "page",
        "pages",
        "panel",
        "panels",
        "preferences",
        "profile",
        "profiles",
        "properties",
        "property",
        "settings",
        "summaries",
        "summary",
    }
)
GENERIC_OBJECT_REQUEST_STOPWORDS = ACTION_OBJECT_STOPWORDS | frozenset({"for", "on", "to"})
ROW_CONTEXT_OBJECT_STOPWORDS = ACTION_OBJECT_STOPWORDS | frozenset(
    {"by", "for", "from", "in", "of", "on", "with"}
)
CONTEXTUAL_DUPLICATE_CONTAINER_WORDS = frozenset(
    {
        "alert",
        "alerts",
        "area",
        "banner",
        "banners",
        "box",
        "card",
        "column",
        "columns",
        "dialog",
        "dialogs",
        "drawer",
        "drawers",
        "footer",
        "form",
        "grid",
        "group",
        "header",
        "list",
        "listitem",
        "menu",
        "menus",
        "modal",
        "modals",
        "notification",
        "notifications",
        "panel",
        "pane",
        "popover",
        "popovers",
        "prompt",
        "prompts",
        "popup",
        "popups",
        "row",
        "rows",
        "section",
        "sidebar",
        "sidebars",
        "snackbar",
        "snackbars",
        "table",
        "toast",
        "toasts",
        "toolbar",
        "toolbars",
        "warning",
        "warnings",
        "window",
        "windows",
    }
)
ROW_ACTION_CONTAINER_WORDS = CONTEXTUAL_DUPLICATE_CONTAINER_WORDS | frozenset(
    {
        "entries",
        "entry",
        "account",
        "accounts",
        "customer",
        "customers",
        "invoice",
        "invoices",
        "item",
        "items",
        "listitem",
        "project",
        "projects",
        "record",
        "records",
        "report",
        "reports",
        "result",
        "results",
        "treeitem",
        "user",
        "users",
    }
)
CONTEXTUAL_DUPLICATE_STOPWORDS = ACTION_OBJECT_STOPWORDS | CONTEXTUAL_DUPLICATE_CONTAINER_WORDS | frozenset(
    {
        "by",
        "click",
        "for",
        "from",
        "inside",
        "in",
        "of",
        "on",
        "press",
        "tap",
        "use",
        "within",
        "with",
    }
)
CONTEXTUAL_DUPLICATE_GENERIC_CONTEXT_WORDS = frozenset(
    {"field", "fields", "invoice", "invoices", "request", "requests"}
)
CONTEXTUAL_DUPLICATE_POSITION_WORDS = frozenset(
    {
        "1",
        "1st",
        "2",
        "2nd",
        "3",
        "3rd",
        "4",
        "4th",
        "5",
        "5th",
        "6",
        "6th",
        "7",
        "7th",
        "8",
        "8th",
        "9",
        "9th",
        "10",
        "10th",
        "bottom",
        "eighth",
        "fifth",
        "first",
        "fourth",
        "last",
        "left",
        "lower",
        "ninth",
        "right",
        "second",
        "seventh",
        "sixth",
        "tenth",
        "third",
        "top",
        "upper",
    }
)
FOREGROUND_CONTEXT_WORDS = frozenset({"active", "foreground", "front"})
CONTEXTUAL_DUPLICATE_ORDINAL_WORDS = (
    frozenset({"1", "1st", "first"}),
    frozenset({"2", "2nd", "second"}),
    frozenset({"3", "3rd", "third"}),
    frozenset({"4", "4th", "fourth"}),
    frozenset({"5", "5th", "fifth"}),
    frozenset({"6", "6th", "sixth"}),
    frozenset({"7", "7th", "seventh"}),
    frozenset({"8", "8th", "eighth"}),
    frozenset({"9", "9th", "ninth"}),
    frozenset({"10", "10th", "tenth"}),
)
FILE_IDENTITY_WORDS = frozenset({"document", "documents", "file", "files"})
FOLDER_IDENTITY_WORDS = frozenset({"directories", "directory", "folder", "folders"})
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
CLIPBOARD_COPY_EXACT_CONTEXT_WORDS = frozenset(
    {"address", "link", "links", "selected", "selection", "text", "url", "urls"}
)
CLIPBOARD_TEXT_ENTRY_TARGET_WORDS = frozenset(
    {
        "address",
        "bar",
        "box",
        "chat",
        "comment",
        "comments",
        "field",
        "filter",
        "find",
        "input",
        "location",
        "message",
        "messages",
        "omnibox",
        "query",
        "reply",
        "search",
        "textbox",
        "textarea",
        "url",
    }
)
FIELD_ENTRY_ACTION_WORDS = frozenset({"enter", "fill", "input", "type"})
TEXT_ENTRY_FIELD_WORDS = frozenset(
    {"box", "field", "fields", "input", "inputs", "text", "textbox", "textarea"}
)
TEXT_ENTRY_LABEL_BOUNDARY_WORDS = (
    OPEN_VIEW_REQUEST_WORDS
    | GENERIC_OBJECT_REQUEST_WORDS
    | FIELD_ENTRY_ACTION_WORDS
    | TEXT_ENTRY_FIELD_WORDS
    | frozenset(
        {
            "a",
            "an",
            "at",
            "current",
            "for",
            "in",
            "into",
            "of",
            "on",
            "that",
            "the",
            "this",
            "to",
            "with",
        }
    )
)
NAMED_CONTROL_LABEL_TYPES = frozenset(
    {"checkbox", "combobox", "edit", "radiobutton", "slider", "spinner"}
)
NAMED_CONTROL_ROLE_WORDS = TEXT_ENTRY_FIELD_WORDS | frozenset(
    {
        "checkbox",
        "combo",
        "combobox",
        "down",
        "dropdown",
        "radio",
        "radiobutton",
        "select",
        "selector",
        "slider",
        "spinbox",
        "spinner",
        "stepper",
        "switch",
        "toggle",
    }
)
NAMED_CONTROL_LABEL_BOUNDARY_WORDS = (
    OPEN_VIEW_REQUEST_WORDS
    | GENERIC_OBJECT_REQUEST_WORDS
    | FIELD_ENTRY_ACTION_WORDS
    | CHECKBOX_ON_ACTION_WORDS
    | CHECKBOX_OFF_ACTION_WORDS
    | NAMED_CONTROL_ROLE_WORDS
    | CONTEXTUAL_DUPLICATE_CONTAINER_WORDS
    | CONTEXTUAL_DUPLICATE_POSITION_WORDS
    | frozenset(
        {
            "a",
            "an",
            "at",
            "current",
            "for",
            "from",
            "in",
            "inside",
            "into",
            "of",
            "on",
            "that",
            "the",
            "this",
            "to",
            "within",
            "with",
        }
    )
)
NAMED_CONTROL_TRAILING_LABEL_MARKERS = frozenset(
    {"called", "for", "label", "labeled", "labelled", "named"}
)
NAMED_CONTROL_LABEL_STOPWORDS = NAMED_CONTROL_ROLE_WORDS | frozenset(
    {
        "button",
        "control",
        "option",
    }
)
RECORD_TARGET_WORDS = frozenset(
    {
        "account",
        "accounts",
        "card",
        "cards",
        "case",
        "cases",
        "client",
        "clients",
        "company",
        "companies",
        "contact",
        "contacts",
        "contract",
        "contracts",
        "customer",
        "customers",
        "dashboard",
        "dashboards",
        "deal",
        "deals",
        "detail",
        "details",
        "invoice",
        "invoices",
        "issue",
        "issues",
        "lead",
        "leads",
        "opportunities",
        "opportunity",
        "order",
        "orders",
        "organization",
        "organizations",
        "overview",
        "overviews",
        "page",
        "pages",
        "partner",
        "partners",
        "profile",
        "profiles",
        "project",
        "projects",
        "quote",
        "quotes",
        "record",
        "records",
        "report",
        "reports",
        "request",
        "requests",
        "screen",
        "screens",
        "subscription",
        "subscriptions",
        "summary",
        "summaries",
        "supplier",
        "suppliers",
        "task",
        "tasks",
        "team",
        "teams",
        "ticket",
        "tickets",
        "tile",
        "tiles",
        "user",
        "users",
        "vendor",
        "vendors",
        "view",
        "views",
        "workspace",
        "workspaces",
    }
)
RECORD_TARGET_TRAILING_CONTEXT_WORDS = frozenset({"tab", "tabs", "tabitem"})
RECORD_TARGET_LABEL_BOUNDARY_WORDS = (
    OPEN_VIEW_REQUEST_WORDS
    | GENERIC_OBJECT_REQUEST_WORDS
    | FIELD_ENTRY_ACTION_WORDS
    | RECORD_TARGET_WORDS
    | frozenset(
        {
            "a",
            "an",
            "at",
            "current",
            "for",
            "from",
            "in",
            "inside",
            "of",
            "on",
            "that",
            "the",
            "this",
            "to",
            "within",
            "with",
        }
    )
)
ACCESS_PERMISSION_ACTION_WORDS = frozenset({"grant", "revoke"})
LITERAL_STOPWORD_NAME_TOKENS = frozenset(
    {
        "area",
        "bottom",
        "box",
        "button",
        "control",
        "drop",
        "field",
        "header",
        "icon",
        "input",
        "item",
        "link",
        "list",
        "menu",
        "option",
        "radio",
        "slider",
        "spinner",
        "switch",
        "tab",
        "text",
        "toggle",
        "top",
    }
)
GENERIC_LITERAL_STOPWORD_NAME_TOKENS = frozenset({"item"})
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
        "route",
        "section",
        "sidebar",
        "table",
        "widget",
        "wizard",
        "workspace",
    }
)
BROWSER_CHROME_EXPLICIT_CONTEXT_WORDS = frozenset(
    {"address", "browser", "brave", "chrome", "edge", "omnibox", "url"}
)
BROWSER_CHROME_NAVIGATION_CONTEXT_WORDS = frozenset(
    {"browser", "brave", "chrome", "edge", "toolbar"}
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
BROWSER_CHROME_TOOLBAR_ACTION_AUTOMATION_IDS = frozenset(
    {"copy", "print", "save", "share"}
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
        "copy",
        "download",
        "downloads",
        "essentials",
        "extensions",
        "favorite",
        "favorites",
        "find",
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
        "print",
        "read",
        "reader",
        "reading",
        "reload",
        "refresh",
        "search",
        "save",
        "share",
        "shopping",
        "sidebar",
        "split",
        "solver",
        "star",
        "tab_search",
        "tabs",
        "translate",
        "vertical",
        "wallet",
        "workspace",
        "workspaces",
    }
)
BROWSER_CHROME_FAVORITE_TOOLBAR_WORDS = frozenset({"favorite", "favorites", "star"})
CONTEXTUAL_NAV_ITEM_CONTAINER_WORDS = frozenset(
    {"drawer", "nav", "navigation", "rail", "rails", "sidebar"}
)
GENERIC_VISIBILITY_SHOW_WORDS = frozenset({"show"})
GENERIC_VISIBILITY_HIDE_WORDS = frozenset({"hide"})
GENERIC_VISIBILITY_ACTION_WORDS = GENERIC_VISIBILITY_SHOW_WORDS | GENERIC_VISIBILITY_HIDE_WORDS
LOCK_ACTION_WORDS = frozenset({"lock", "unlock"})
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
    (frozenset({"activate"}), frozenset({"active", "activated"})),
    (frozenset({"deactivate"}), frozenset({"deactivated", "inactive"})),
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
    (frozenset({"filter"}), frozenset({"filtered"})),
    (frozenset({"fix"}), frozenset({"fixed"})),
    (frozenset({"install", "update"}), frozenset({"installed", "updated"})),
    (frozenset({"invite"}), frozenset({"invited"})),
    (frozenset({"paste"}), frozenset({"pasted"})),
    (frozenset({"print"}), frozenset({"printed"})),
    (frozenset({"save"}), frozenset({"autosaved", "saved"})),
    (frozenset({"search"}), frozenset({"searched"})),
    (frozenset({"send", "submit"}), frozenset({"delivered", "sent", "submitted"})),
    (frozenset({"share"}), frozenset({"shared"})),
    (frozenset({"sort"}), frozenset({"sorted"})),
    (frozenset({"sync"}), frozenset({"synced"})),
    (frozenset({"resolve"}), frozenset({"resolved"})),
    (frozenset({"mute", "unmute"}), frozenset({"muted", "unmuted"})),
    (frozenset({"show", "hide"}), frozenset({"hidden", "shown", "visible"})),
    (frozenset({"expand", "collapse"}), frozenset({"collapsed", "expanded"})),
    (frozenset({"lock", "unlock"}), frozenset({"locked", "unlocked"})),
    (frozenset({"connect", "disconnect"}), frozenset({"connected", "disconnected"})),
    (frozenset({"activate", "deactivate"}), frozenset({"active", "activated", "deactivated", "inactive"})),
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
STATE_ACTION_WORDS = frozenset(
    word for action_words, _state_words in STATE_LABEL_ACTION_GROUPS for word in action_words
)
SAME_FORM_STATE_ACTION_WORDS = frozenset({"reset"})
STATE_LABEL_TURN_ON_WORDS = frozenset({"checked", "enabled"})
STATE_LABEL_TURN_OFF_WORDS = frozenset({"disabled", "unchecked"})
SEARCH_ACTION_WORDS = frozenset({"find", "search"})
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
ROW_CONTEXT_GENERIC_WORDS = WINDOW_CONTEXT_OBJECT_WORDS | frozenset(
    {
        "item",
        "items",
        "list",
        "order",
        "orders",
        "record",
        "records",
        "row",
        "rows",
        "table",
    }
)
CONTEXTUAL_DUPLICATE_SURFACE_WORDS = frozenset(
    {
        "alert",
        "alerts",
        "banner",
        "banners",
        "card",
        "column",
        "dialog",
        "dialogs",
        "drawer",
        "footer",
        "form",
        "grid",
        "group",
        "header",
        "menu",
        "modal",
        "modals",
        "notification",
        "notifications",
        "panel",
        "pane",
        "popover",
        "popovers",
        "prompt",
        "prompts",
        "popup",
        "popups",
        "section",
        "snackbar",
        "snackbars",
        "sidebar",
        "table",
        "toast",
        "toasts",
        "toolbar",
        "warning",
        "warnings",
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
    frozenset({"publish", "release"}),
    frozenset({"refresh", "reload"}),
    frozenset({"share"}),
    frozenset({"sort"}),
)
AUTOMATION_ONLY_ACTION_MATCH_WORDS = frozenset(
    word for action_words, _state_words in STATE_LABEL_ACTION_GROUPS for word in action_words
) | frozenset(word for family in EXCLUSIVE_ACTION_FAMILIES for word in family)
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
    *SIDE_EFFECT_ACTION_FAMILIES,
    frozenset({"share"}),
)
GENERIC_OBJECT_CANDIDATE_ACTION_FAMILIES = OPEN_VIEW_CANDIDATE_ACTION_FAMILIES
AMBIGUOUS_EXACT_ACTION_ALIAS_FAMILIES = (frozenset({"print", "printer"}),)
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
TASKBAR_STATUS_SETTINGS_REQUEST_WORDS = frozenset({"options", "preferences", "settings"})
TASKBAR_POWER_STATUS_IDENTITY_WORDS = frozenset({"battery", "power"})
TASKBAR_CLOCK_STATUS_IDENTITY_WORDS = frozenset({"clock", "date", "time"})
TASKBAR_SEARCH_STATUS_IDENTITY_WORDS = frozenset({"find", "search"})
TASKBAR_NOTIFICATION_STATUS_IDENTITY_WORDS = frozenset({"notification", "notifications"})
TASKBAR_SEARCH_STATUS_SEPARATOR_ALIAS_WORDS = frozenset(
    {"minimize", "minus", "zoom_out"}
)
TASKBAR_ONEDRIVE_STATUS_IDENTITY_WORDS = frozenset({"onedrive"})
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
SAME_ACTION_OBJECT_FAMILIES = EXCLUSIVE_ACTION_FAMILIES + (
    TASKBAR_PIN_ACTION_WORDS,
    GENERIC_VISIBILITY_ACTION_WORDS,
    SEARCH_ACTION_WORDS,
    frozenset({"clear", "reset"}),
    frozenset({"overlap", "restore"}),
)
TASKBAR_GENERIC_FILE_IDENTITY_WORDS = frozenset({"file", "files"})
TASKBAR_WINDOWS_SEARCH_TOKENS = frozenset({"windows_search"})
BROWSER_PROFILE_WINDOW_WORDS = frozenset({"brave", "browser", "chrome", "edge"})
BROWSER_APP_IDENTITY_WORDS = frozenset({"brave", "browser", "chrome", "edge", "google"})
BROWSER_PROFILE_ACTION_CONTEXT_WORDS = frozenset({"edit", "pencil"})
BROWSER_PROFILE_LABEL_HINT_WORDS = frozenset({"all"})
BROWSER_PROFILE_TOKENS = frozenset({"account", "avatar", "person", "profile", "user"})
BROWSER_PAGE_TARGET_WORDS = frozenset({"page", "route", "webpage"})
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
BROWSER_SIGN_IN_ACTION_WORDS = frozenset({"in", "login", "signin"})
BROWSER_SIGN_OUT_ACTION_WORDS = frozenset({"logoff", "logout", "out", "signout"})
BROWSER_TAB_GENERIC_SECTION_WORDS = frozenset(
    {
        "download",
        "downloads",
        "extension",
        "extensions",
        "history",
        "home",
        "house",
        "options",
        "overview",
        "preferences",
        "settings",
    }
)
BROWSER_MENU_BUTTON_TOKENS = frozenset(
    {"browser", "chrome", "menu", "more", "options", "preferences", "settings"}
)
BROWSER_MENU_CONTROL_INTENTS = frozenset({"button", "menuitem", "splitbutton"})
BROWSER_MENU_REQUEST_WORDS = frozenset({"menu", "more", "options", "overflow"})
BROWSER_MENU_SETTINGS_REQUEST_WORDS = frozenset({"preferences", "settings"})
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
BROWSER_NEW_TAB_GENERIC_WORDS = frozenset({"add", "create", "new", "plus"})
BROWSER_NEW_TAB_RELATED_REQUEST_WORDS = (
    BROWSER_NEW_TAB_GENERIC_WORDS
    | BROWSER_NEW_TAB_WORDS
    | frozenset({"external", "new_window", "open_new"})
)
BROWSER_BOOKMARK_ACTION_WORDS = frozenset({"bookmark", "favorite", "star"})
BROWSER_BOOKMARK_TAB_CONTEXT_WORDS = frozenset(
    {"page", "pages", "tab", "tabs", "webpage", "website"}
)
BROWSER_BOOKMARK_ITEM_CONTEXT_WORDS = frozenset(
    {"article", "card", "item", "items", "listing", "post", "product", "record", "row"}
)
BROWSER_GROUP_STATE_WORDS = frozenset({"closed", "collapsed", "expanded", "open"})
BROWSER_GROUP_GENERIC_WORDS = frozenset({"closed", "collapsed", "expanded", "group", "open"})
DISCLOSURE_EXPAND_ACTION_WORDS = frozenset({"expand"})
DISCLOSURE_COLLAPSE_ACTION_WORDS = frozenset({"collapse"})
COMBOBOX_DROPDOWN_ARROW_REQUEST_WORDS = frozenset({"arrow", "caret", "chevron", "down", "dropdown"})
COMBOBOX_DROPDOWN_ARROW_CONTROL_WORDS = frozenset(
    {"down", "dropdown", "expand", "more", "open", "show"}
)
PIN_STATE_NEUTRAL_WORDS = frozenset({"pinned", "pushpin", "thumbtack"})
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
    r"(?:\s*[\-\|\u2013\u2014]\s*)?memory\s+usage\s*[-:]\s*\d+(?:\.\d+)?\s*mb\b.*$",
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
        "directories",
        "directory",
        "for",
        "folder",
        "folders",
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
        "unnamed",
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
        "directories",
        "directory",
        "favorite",
        "favourite",
        "edge",
        "for",
        "folder",
        "folders",
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
        "unnamed",
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
        "cell",
        "menuitem",
        "tabitem",
        "hyperlink",
        "listitem",
        "dataitem",
        "datagridcell",
        "gridcell",
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
CELL_CONTROL_TYPES = frozenset({"cell", "datagridcell", "gridcell"})
NON_ACTIONABLE_CONTROL_TYPES = frozenset({"label", "statictext", "text"})
NEARBY_ROW_LABEL_CONTROL_TYPES = NON_ACTIONABLE_CONTROL_TYPES | frozenset(
    {"cell", "datagridcell", "gridcell", "rowheader"}
)
LABELLED_FIELD_CONTROL_TYPES = frozenset({"combobox", "edit", "spinner"})
OPTION_ROLE_CONTROL_TYPES = frozenset({"checkbox", "listitem", "menuitem", "radiobutton", "treeitem"})
ROW_LIKE_CONTROL_TYPES = frozenset({"listitem", "dataitem", "treeitem", "edit", "combobox"})
ROW_CONTEXT_CONTROL_TYPES = frozenset({"listitem", "dataitem", "treeitem"})
SURFACE_CONTEXT_CONTROL_TYPES = frozenset({"group", "headeritem", "menu", "pane", "toolbar", "window"})
SURFACE_CONTEXT_TYPE_WORDS = {
    "group": frozenset({"group"}),
    "headeritem": frozenset({"column", "header", "heading"}),
    "menu": frozenset({"menu"}),
    "pane": frozenset({"pane"}),
    "toolbar": frozenset({"toolbar"}),
    "window": frozenset({"window"}),
}
DIRECT_SURFACE_CONTAINER_ALIASES = {
    "group": frozenset({"group"}),
    "headeritem": frozenset({"column", "header", "heading"}),
    "menu": frozenset({"menu"}),
    "pane": frozenset(
        {
            "card",
            "dashboard",
            "details",
            "drawer",
            "footer",
            "form",
            "nav",
            "navigation",
            "overview",
            "page",
            "pane",
            "panel",
            "profile",
            "rail",
            "route",
            "section",
            "sidebar",
            "screen",
            "summary",
            "tile",
            "view",
            "workspace",
        }
    ),
    "toolbar": frozenset({"toolbar"}),
    "window": frozenset(
        {
            "alert",
            "banner",
            "dialog",
            "footer",
            "form",
            "modal",
            "notification",
            "popover",
            "popup",
            "prompt",
            "snackbar",
            "toast",
            "warning",
            "window",
        }
    ),
}
UNNAMED_FOREGROUND_TRANSIENT_SURFACE_WORDS = frozenset(
    {
        "alert",
        "alerts",
        "banner",
        "banners",
        "dialog",
        "dialogs",
        "modal",
        "modals",
        "notification",
        "popover",
        "popovers",
        "prompt",
        "prompts",
        "popup",
        "popups",
        "snackbar",
        "snackbars",
        "toast",
        "toasts",
        "warning",
        "warnings",
    }
)
CONTAINED_CONTROL_REQUEST_WORDS = frozenset(
    {
        "arrow",
        "bar",
        "button",
        "checkbox",
        "combo",
        "combobox",
        "dropdown",
        "edit",
        "field",
        "headeritem",
        "hyperlink",
        "icon",
        "input",
        "item",
        "link",
        "menuitem",
        "option",
        "radio",
        "radiobutton",
        "slider",
        "spinner",
        "splitbutton",
        "switch",
        "tab",
        "tabitem",
        "cell",
        "datagridcell",
        "gridcell",
        "textbox",
        "toggle",
    }
)
POSITIONAL_DUPLICATE_REQUEST_WORDS = CONTAINED_CONTROL_REQUEST_WORDS | frozenset(
    {"buttons", "controls", "entries", "entry", "icons", "result", "results"}
)
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
        matches_row_action = (
            _instruction_requests_contained_row_action(instruction)
            and bool(control_intents & ROW_CONTEXT_CONTROL_TYPES)
            and _contained_row_action_candidate_matches(candidate, instruction_tokens, instruction)
        )
        matches_named_contextual_duplicate = _candidate_satisfies_named_contextual_duplicate_request(
            instruction,
            candidate,
            candidates,
        )
        matches_control_intent = _candidate_matches_control_intent(
            candidate,
            control_intents,
            instruction=instruction,
        ) or _contextual_action_candidate_matches_surface_request(
            instruction,
            instruction_tokens,
            candidate,
            candidates,
        ) or matches_row_action or matches_named_contextual_duplicate or (
            _spinner_stepper_button_match(instruction, candidate, candidates)
        )
        if control_intents and not matches_control_intent:
            score = _context_text_match_score(
                instruction,
                instruction_tokens,
                candidate,
                candidates,
                model_rect,
            )
        else:
            score = _text_match_score(instruction, candidate, candidates, model_rect)
        score += _foreground_rank_bonus(candidate, candidates, model_rect=model_rect)
        if score > 0:
            if not matches_control_intent:
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
                    instruction=instruction,
                    instruction_tokens=instruction_tokens,
                    control_intents=control_intents,
                ):
                    continue
                score = min(score, CONTAINING_ROW_SNAP_CAP)
            elif _contains_tighter_same_intent_action(
                selected=candidate,
                candidates=candidates,
                instruction=instruction,
                instruction_tokens=instruction_tokens,
                control_intents=control_intents,
            ):
                score = min(score, CONTAINING_ROW_SNAP_CAP)
            ranked.append((score, candidate))

    if not ranked:
        single_strict_text_entry = _single_strict_text_entry_candidate(
            instruction,
            candidates,
        )
        if single_strict_text_entry is not None:
            return TargetResolution(
                rect=single_strict_text_entry.rect,
                confidence=TEXT_MATCH_FLOOR,
                source="text_match",
                matched_text=single_strict_text_entry.descriptor,
                target_id=single_strict_text_entry.id,
            )
        return None

    ranked.sort(key=lambda item: item[0], reverse=True)
    best_score, candidate = ranked[0]
    if best_score < TEXT_MATCH_FLOOR:
        return None

    runner_up = _first_distinct_ranked_candidate(ranked[1:], candidate)
    if runner_up is not None and best_score - runner_up[0] < TEXT_MATCH_GAP:
        if _exact_visible_label_matches_request(instruction, candidate) and not (
            _exact_visible_label_matches_request(instruction, runner_up[1])
        ):
            return TargetResolution(
                rect=candidate.rect,
                confidence=best_score,
                source="text_match",
                matched_text=candidate.descriptor,
                target_id=candidate.id,
            )
        if _row_scoped_action_target_matches_context(
            instruction,
            candidate,
            candidates,
        ) and not _row_scoped_action_target_matches_context(
            instruction,
            runner_up[1],
            candidates,
        ):
            return TargetResolution(
                rect=candidate.rect,
                confidence=best_score,
                source="text_match",
                matched_text=candidate.descriptor,
                target_id=candidate.id,
            )
        if _candidate_satisfies_named_contextual_duplicate_request(
            instruction,
            candidate,
            candidates,
        ) and not _candidate_satisfies_named_contextual_duplicate_request(
            instruction,
            runner_up[1],
            candidates,
        ):
            return TargetResolution(
                rect=candidate.rect,
                confidence=best_score,
                source="text_match",
                matched_text=candidate.descriptor,
                target_id=candidate.id,
            )
        if _contextual_control_intent_text_match_ambiguous(
            instruction=instruction,
            instruction_tokens=instruction_tokens,
            selected=candidate,
            runner_up=runner_up[1],
            candidates=candidates,
            control_intents=control_intents,
        ):
            return None
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


def _contextual_control_intent_text_match_ambiguous(
    *,
    instruction: str,
    instruction_tokens: set[str],
    selected: ControlCandidate,
    runner_up: ControlCandidate,
    candidates: list[ControlCandidate],
    control_intents: set[str],
) -> bool:
    if not control_intents:
        return False
    for candidate in (selected, runner_up):
        if not _candidate_matches_control_intent(
            candidate,
            control_intents,
            instruction=instruction,
        ):
            return False
        if not _candidate_satisfies_contextual_duplicate_request(
            instruction,
            candidate,
            candidates,
        ):
            return False
    selected_tokens = _candidate_visible_text_tokens(selected)
    runner_tokens = _candidate_visible_text_tokens(runner_up)
    if instruction_tokens & (selected_tokens | runner_tokens):
        return False
    return True


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
            rejected_reason = (
                "candidate semantic mismatch"
                if _candidate_snap_semantic_mismatch(
                    candidate=contained,
                    candidates=candidates,
                    instruction=instruction,
                    instruction_tokens=instruction_tokens,
                    model_rect=model_rect,
                )
                else ""
            )
            return TargetResolution(
                rect=contained.rect,
                confidence=confidence_floor,
                source="candidate_snap",
                matched_text=contained.descriptor,
                target_id=contained.id,
                rejected_reason=rejected_reason,
            )
        return None
    ranked.sort(key=lambda item: item[0], reverse=True)
    best_score, candidate = ranked[0]
    geometry_conflict = _exact_action_geometry_conflict(
        ranked=ranked,
        selected=candidate,
        selected_score=best_score,
        confidence_floor=confidence_floor,
        instruction=instruction,
        candidates=candidates,
        control_intents=control_intents,
        model_rect=model_rect,
    )
    if geometry_conflict is not None:
        conflict_score, conflict_candidate = geometry_conflict
        return TargetResolution(
            rect=conflict_candidate.rect,
            confidence=conflict_score,
            source="candidate_snap",
            matched_text=conflict_candidate.descriptor,
            target_id=conflict_candidate.id,
            rejected_reason="candidate semantic mismatch",
        )
    if best_score < confidence_floor:
        contained = _single_contained_control_intent_candidate(
            candidates=candidates,
            model_rect=model_rect,
            instruction=instruction,
            instruction_tokens=instruction_tokens,
            control_intents=control_intents,
        )
        if contained is not None:
            rejected_reason = (
                "candidate semantic mismatch"
                if _candidate_snap_semantic_mismatch(
                    candidate=contained,
                    candidates=candidates,
                    instruction=instruction,
                    instruction_tokens=instruction_tokens,
                    model_rect=model_rect,
                )
                else ""
            )
            return TargetResolution(
                rect=contained.rect,
                confidence=confidence_floor,
                source="candidate_snap",
                matched_text=contained.descriptor,
                target_id=contained.id,
                rejected_reason=rejected_reason,
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
        min_rank = min(item.window_rank for _score, item in ranked)
        if not (candidate.window_rank == min_rank and runner_up[1].window_rank > min_rank):
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


def _same_visual_and_context_candidate(first: ControlCandidate, second: ControlCandidate) -> bool:
    if not _same_visual_candidate(first, second):
        return False
    return _candidate_context_identity_key(first) == _candidate_context_identity_key(second)


def _candidate_context_identity_key(candidate: ControlCandidate) -> tuple[str, int]:
    return ((candidate.window_title or "").strip().lower(), candidate.window_rank)


def _first_distinct_ranked_candidate(
    ranked: list[tuple[float, ControlCandidate]],
    selected: ControlCandidate,
) -> tuple[float, ControlCandidate] | None:
    for score, candidate in ranked:
        if _same_visual_and_context_candidate(candidate, selected):
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
    visible_tokens = _candidate_visible_text_tokens(candidate)
    literal_match_tokens = _literal_stopword_name_match_tokens(instruction, visible_tokens)
    exact_visible_label_match = _exact_visible_label_matches_request(instruction, candidate)
    matches_dropdown_item = _dropdown_item_request_matches_candidate(
        instruction,
        candidate,
        candidates,
    )
    matches_spinner_stepper_button = _spinner_stepper_button_match(
        instruction,
        candidate,
        candidates,
    )
    matches_audio_mute_action = _audio_output_mute_action_match(instruction, candidate)
    matches_site_information_action = _site_information_request_matches_candidate(
        instruction,
        candidate,
    )
    matches_record_target = (
        _record_target_candidate_matches_request(instruction, candidate)
        and not _instruction_requests_contained_row_action(instruction)
    )
    direct_surface_requested, direct_surface_label_tokens = _direct_surface_container_request_parts(
        instruction
    )
    matches_direct_surface_container = bool(
        direct_surface_requested
        and (direct_surface_label_tokens or model_rect is None)
        and _direct_surface_container_candidate_matches_request(
            instruction,
            candidate,
            candidates,
        )
    )
    if candidate.control_type in NON_ACTIONABLE_CONTROL_TYPES:
        return 0.0
    if _cell_target_request_mismatch(instruction, candidate):
        return 0.0
    if _tab_context_candidate_mismatch(instruction, candidate, candidates):
        return 0.0
    if _named_control_label_missing(instruction, candidate, candidates):
        return 0.0
    if _record_target_alternative_mismatch(instruction, candidate, candidates):
        return 0.0
    if _access_permission_action_mismatch(instruction, candidate, candidates):
        return 0.0
    if model_rect is not None and _container_only_request_blocks_contained_candidate(
        instruction=instruction,
        instruction_tokens=instruction_tokens,
        control_intents=control_intents,
        candidate=candidate,
        candidates=candidates,
        model_rect=model_rect,
    ):
        return 0.0
    matches_row_action = (
        _instruction_requests_contained_row_action(instruction)
        and bool(control_intents & ROW_CONTEXT_CONTROL_TYPES)
        and _contained_row_action_candidate_matches(candidate, instruction_tokens, instruction)
    )
    matches_named_contextual_duplicate = _candidate_satisfies_named_contextual_duplicate_request(
        instruction,
        candidate,
        candidates,
    )
    matches_contextual_surface_action = _contextual_action_candidate_matches_surface_request(
        instruction,
        instruction_tokens,
        candidate,
        candidates,
    ) and not _candidate_matches_control_intent(
        candidate,
        control_intents,
        instruction=instruction,
    )
    matches_contextual_control_intent = bool(control_intents) and _candidate_matches_control_intent(
        candidate,
        control_intents,
        instruction=instruction,
    ) and _candidate_satisfies_contextual_duplicate_request(
        instruction,
        candidate,
        candidates,
    )
    if (
        not matches_row_action
        and not matches_named_contextual_duplicate
        and not _candidate_matches_control_intent(
            candidate,
            control_intents,
            instruction=instruction,
        )
        and not matches_contextual_surface_action
        and not matches_spinner_stepper_button
        and not _cardinal_direction_request_matches_candidate(instruction, candidate)
        and not literal_match_tokens
        and not exact_visible_label_match
        and not matches_site_information_action
        and not matches_dropdown_item
        and not matches_record_target
        and not matches_direct_surface_container
    ):
        return 0.0
    if _named_dropdown_alternative_mismatch(instruction, candidate, candidates):
        return 0.0
    if _named_dropdown_request_matches_candidate(instruction, candidate, candidates):
        score = TEXT_MATCH_FLOOR + 0.08
        if model_rect is not None:
            score += 0.05 * _proximity_score(candidate.rect, model_rect)
        return min(1.0, score)
    if _combobox_dropdown_arrow_match(instruction, candidate, candidates):
        score = TEXT_MATCH_FLOOR
        if model_rect is not None:
            score = min(1.0, score + 0.05 * _proximity_score(candidate.rect, model_rect))
        return score
    if matches_dropdown_item:
        score = TEXT_MATCH_FLOOR + 0.08
        if model_rect is not None:
            score += 0.05 * _proximity_score(candidate.rect, model_rect)
        return min(1.0, score)
    if matches_record_target:
        score = TEXT_MATCH_FLOOR + 0.08
        if visible_tokens:
            score += VISIBLE_TEXT_MATCH_BONUS
        if model_rect is not None:
            score += 0.05 * _proximity_score(candidate.rect, model_rect)
        return min(1.0, score)
    if matches_direct_surface_container:
        score = TEXT_MATCH_FLOOR + 0.06
        if visible_tokens:
            score += VISIBLE_TEXT_MATCH_BONUS
        if model_rect is not None:
            score += 0.05 * _proximity_score(candidate.rect, model_rect)
        return min(1.0, score)
    if matches_spinner_stepper_button:
        score = TEXT_MATCH_FLOOR + 0.10
        if visible_tokens:
            score += VISIBLE_TEXT_MATCH_BONUS
        if model_rect is not None:
            score += 0.05 * _proximity_score(candidate.rect, model_rect)
        return min(1.0, score)
    if matches_audio_mute_action:
        score = TEXT_MATCH_FLOOR + 0.08
        if visible_tokens:
            score += VISIBLE_TEXT_MATCH_BONUS
        if model_rect is not None:
            score += 0.05 * _proximity_score(candidate.rect, model_rect)
        return min(1.0, score)
    if matches_site_information_action:
        score = TEXT_MATCH_FLOOR + 0.08
        if visible_tokens:
            score += VISIBLE_TEXT_MATCH_BONUS
        if model_rect is not None:
            score += 0.05 * _proximity_score(candidate.rect, model_rect)
        return min(1.0, score)
    if _contains_tighter_row_action_candidate(
        selected=candidate,
        candidates=candidates,
        instruction=instruction,
        instruction_tokens=instruction_tokens,
        control_intents=control_intents,
    ):
        return 0.0
    if _surface_context_contains_tighter_action(
        selected=candidate,
        candidates=candidates,
        instruction=instruction,
        instruction_tokens=instruction_tokens,
        control_intents=control_intents,
    ):
        return 0.0
    if (
        not instruction_tokens
        and not literal_match_tokens
        and not exact_visible_label_match
        and not _cardinal_direction_request_matches_candidate(instruction, candidate)
    ):
        return 0.0
    if _taskbar_start_button_action_mismatch(instruction_tokens, candidate):
        return 0.0
    if _taskbar_start_button_generic_menu_mismatch(instruction, candidate):
        return 0.0
    if _taskbar_task_view_action_mismatch(instruction, instruction_tokens, candidate):
        return 0.0
    if _taskbar_hidden_icons_action_mismatch(instruction_tokens, candidate):
        return 0.0
    if _taskbar_show_desktop_action_mismatch(instruction_tokens, candidate):
        return 0.0
    if _program_manager_desktop_item_action_mismatch(instruction_tokens, candidate):
        return 0.0
    if _taskbar_app_state_action_mismatch(instruction_tokens, candidate):
        return 0.0
    if _taskbar_surface_context_mismatch(instruction, candidate):
        return 0.0
    if _browser_profile_identity_action_mismatch(instruction_tokens, candidate):
        return 0.0
    if _browser_profile_page_action_mismatch(instruction, candidate):
        return 0.0
    if _browser_chrome_app_context_mismatch(instruction, candidate):
        return 0.0
    if _background_transient_surface_target_mismatch(instruction, candidate, candidates):
        return 0.0
    if _browser_menu_button_action_mismatch(instruction, candidate):
        return 0.0
    if _browser_navigation_chrome_action_mismatch(instruction, candidate):
        return 0.0
    if _browser_toolbar_chrome_action_mismatch(instruction, candidate):
        return 0.0
    if _browser_address_bar_content_mismatch(
        instruction,
        instruction_tokens,
        candidate,
    ):
        return 0.0
    if _browser_address_bar_alternative_mismatch(
        instruction,
        instruction_tokens,
        candidate,
        candidates,
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
    if _clear_close_action_mismatch(instruction, instruction_tokens, candidate, candidates):
        return 0.0
    if _close_context_action_mismatch(instruction, candidate, candidates):
        return 0.0
    if _window_close_alternative_mismatch(instruction, candidate, candidates):
        return 0.0
    if _close_tab_action_mismatch(instruction, candidate, candidates):
        return 0.0
    if _browser_new_tab_bookmark_action_mismatch(instruction_tokens, candidate):
        return 0.0
    if _browser_new_tab_action_mismatch(instruction, instruction_tokens, candidate):
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
    if _pin_state_action_mismatch(instruction, candidate):
        return 0.0
    if _password_visibility_state_action_mismatch(instruction, candidate):
        return 0.0
    if _audio_output_polarity_action_mismatch(instruction, candidate, candidates):
        return 0.0
    if _history_action_mismatch(instruction, candidate):
        return 0.0
    if _explicit_checkbox_like_control_type_mismatch(
        instruction,
        candidate,
        control_intents,
    ):
        return 0.0
    if _checkbox_state_action_mismatch(instruction, candidate):
        return 0.0
    if _navigation_media_transport_action_mismatch(instruction, candidate):
        return 0.0
    if _calendar_exact_alternative_mismatch(instruction, candidate, candidates):
        return 0.0
    if _navigation_backup_action_mismatch(instruction, candidate):
        return 0.0
    if _unresolved_contextual_duplicate_mismatch(instruction, candidate, candidates):
        return 0.0
    if _implicit_container_context_duplicate_mismatch(instruction, candidate, candidates):
        return 0.0
    if _explicit_transient_surface_alternative_mismatch(instruction, candidate, candidates):
        return 0.0
    if _contained_row_action_context_mismatch(instruction, candidate, candidates):
        return 0.0
    if _positional_action_duplicate_mismatch(
        instruction,
        instruction_tokens,
        candidate,
        candidates,
    ):
        return 0.0
    if _contextual_surface_action_alternative_mismatch(
        instruction,
        instruction_tokens,
        candidate,
        candidates,
    ):
        return 0.0
    if _prepositional_context_action_alternative_mismatch(
        instruction,
        instruction_tokens,
        candidate,
        candidates,
    ):
        return 0.0
    if _delimited_context_only_target_alternative_mismatch(
        instruction,
        candidate,
        candidates,
        control_intents,
    ):
        return 0.0
    if _prepositional_context_only_target_alternative_mismatch(
        instruction,
        instruction_tokens,
        candidate,
        candidates,
        control_intents,
    ):
        return 0.0
    if _reversible_action_exact_alternative_mismatch(
        instruction,
        candidate,
        candidates,
        control_intents,
    ):
        return 0.0
    if _explicit_action_context_mismatch_without_contextual_evidence(
        instruction,
        candidate,
        candidates,
    ):
        return 0.0
    if _state_action_object_only_alternative_mismatch(instruction, candidate, candidates):
        return 0.0
    if _object_only_action_context_mismatch(instruction, candidate):
        return 0.0
    if _action_object_alias_context_requested(
        instruction
    ) and _exact_action_word_alternative_mismatch(
        instruction,
        candidate,
        candidates,
        None,
    ) and not _ambiguous_exact_action_alias_alternative(instruction, candidate, candidates):
        return 0.0
    if _exclusive_action_family_mismatch(instruction, candidate.descriptor):
        return 0.0
    if _mail_tab_account_reference_mismatch(instruction_tokens, candidate):
        return 0.0
    if _browser_tab_auth_action_mismatch(instruction_tokens, candidate):
        return 0.0
    if _browser_tab_generic_section_mismatch(instruction, instruction_tokens, candidate):
        return 0.0
    if _browser_tab_contextual_item_mismatch(instruction, candidate):
        return 0.0
    if _dropdown_item_request_launcher_mismatch(instruction, candidate, candidates):
        return 0.0
    if _dropdown_item_request_menuitem_mismatch(instruction, candidate, candidates):
        return 0.0
    if _combobox_dropdown_arrow_control_mismatch(instruction, candidate, candidates):
        return 0.0
    if _dropdown_option_launcher_mismatch(instruction, candidate, candidates):
        return 0.0
    if _literal_stopword_name_alternative_mismatch(instruction, candidate, candidates):
        return 0.0
    if _exact_visible_label_alternative_mismatch(
        instruction,
        candidate,
        candidates,
        control_intents,
    ):
        return 0.0
    if _ambiguous_exact_literal_alias_alternative(
        instruction,
        candidate,
        candidates,
        control_intents,
    ) or _exact_literal_alias_peer_alternative(
        instruction,
        candidate,
        candidates,
        control_intents,
    ):
        score = TEXT_MATCH_FLOOR + 0.08
        if visible_tokens:
            score += VISIBLE_TEXT_MATCH_BONUS
        if model_rect is not None:
            score += 0.05 * _proximity_score(candidate.rect, model_rect)
        return min(max(score, 0.0), 1.0)
    if _explicit_combobox_alternative_mismatch(instruction, candidate, candidates):
        return 0.0
    if _explicit_spinner_alternative_mismatch(instruction, candidate, candidates):
        return 0.0
    if _spinner_stepper_parent_mismatch(instruction, candidate, candidates):
        return 0.0
    if _explicit_slider_alternative_mismatch(instruction, candidate, candidates):
        return 0.0
    if _explicit_pane_alternative_mismatch(instruction, candidate, candidates):
        return 0.0
    if _explicit_surface_container_alternative_mismatch(instruction, candidate, candidates):
        return 0.0
    if _explicit_item_alternative_mismatch(instruction, candidate, candidates):
        return 0.0
    if _explicit_generic_item_control_type_mismatch(instruction, candidate):
        return 0.0
    if _explicit_field_alternative_mismatch(instruction, candidate, candidates):
        return 0.0
    if _explicit_option_alternative_mismatch(instruction, candidate, candidates):
        return 0.0
    if _explicit_checkbox_like_alternative_mismatch(instruction, candidate, candidates):
        return 0.0
    if _explicit_subtype_alternative_mismatch(instruction, candidate, candidates):
        return 0.0
    if _surface_context_contains_tighter_action(
        selected=candidate,
        candidates=candidates,
        instruction=instruction,
        instruction_tokens=instruction_tokens,
        control_intents=_instruction_control_intents(instruction),
    ):
        return 0.0
    if _has_visible_semantic_alternative(
        instruction=instruction,
        instruction_tokens=instruction_tokens,
        selected=candidate,
        candidates=candidates,
        control_intents=control_intents,
    ):
        return 0.0
    if exact_visible_label_match:
        score = TEXT_MATCH_FLOOR + 0.08
        if visible_tokens:
            score += VISIBLE_TEXT_MATCH_BONUS
        if model_rect is not None:
            score += 0.05 * _proximity_score(candidate.rect, model_rect)
        return min(max(score, 0.0), 1.0)
    if _delimited_context_target_match(instruction, candidate, candidates):
        score = TEXT_MATCH_FLOOR + 0.08
        if visible_tokens:
            score += VISIBLE_TEXT_MATCH_BONUS
        if model_rect is not None:
            score += 0.05 * _proximity_score(candidate.rect, model_rect)
        return min(max(score, 0.0), 1.0)
    label_tokens = _named_control_candidate_label_tokens(candidate, candidates)
    candidate_tokens = _candidate_semantic_tokens(candidate) | label_tokens
    if not candidate_tokens:
        return 0.0
    if matches_named_contextual_duplicate:
        score = TEXT_MATCH_FLOOR + 0.06
        if visible_tokens:
            score += VISIBLE_TEXT_MATCH_BONUS
        if model_rect is not None:
            score += 0.05 * _proximity_score(candidate.rect, model_rect)
        return min(max(score, 0.0), 1.0)
    if matches_contextual_control_intent:
        score = TEXT_MATCH_FLOOR + 0.06
        if visible_tokens:
            score += VISIBLE_TEXT_MATCH_BONUS
        if model_rect is not None:
            score += 0.05 * _proximity_score(candidate.rect, model_rect)
        return min(max(score, 0.0), 1.0)
    if matches_contextual_surface_action:
        score = TEXT_MATCH_FLOOR + 0.06
        if visible_tokens:
            score += VISIBLE_TEXT_MATCH_BONUS
        if model_rect is not None:
            score += 0.05 * _proximity_score(candidate.rect, model_rect)
        return min(max(score, 0.0), 1.0)
    if _candidate_satisfies_tab_context_action_request(instruction, candidate, candidates):
        score = TEXT_MATCH_FLOOR + 0.04
        if visible_tokens:
            score += VISIBLE_TEXT_MATCH_BONUS
        if model_rect is not None:
            score += 0.05 * _proximity_score(candidate.rect, model_rect)
        return min(max(score, 0.0), 1.0)
    if _candidate_satisfies_positional_action_duplicate_request(
        instruction,
        instruction_tokens,
        candidate,
        candidates,
    ):
        score = TEXT_MATCH_FLOOR + 0.04
        if visible_tokens:
            score += VISIBLE_TEXT_MATCH_BONUS
        if model_rect is not None:
            score += 0.05 * _proximity_score(candidate.rect, model_rect)
        return min(max(score, 0.0), 1.0)
    match_tokens = _candidate_match_instruction_tokens(
        instruction,
        instruction_tokens,
        candidate,
    )
    overlap = match_tokens & candidate_tokens
    if not overlap:
        return 0.0

    coverage = len(overlap) / max(1, len(match_tokens))
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
        if _automation_only_exact_action_match(candidate, overlap):
            score = max(score, TEXT_MATCH_FLOOR + 0.02)
    if label_tokens and overlap & label_tokens:
        score = max(score, TEXT_MATCH_FLOOR + 0.03)
    if _action_object_alias_context_requested(instruction) and _exact_visible_action_word_match(
        instruction,
        candidate,
    ):
        score = max(score, TEXT_MATCH_FLOOR + 0.04)
    if _candidate_satisfies_contextual_duplicate_request(
        instruction,
        candidate,
        candidates,
    ):
        score = max(score, TEXT_MATCH_FLOOR + 0.04)
    if _row_scoped_action_target_matches_context(instruction, candidate, candidates):
        score = max(score, TEXT_MATCH_FLOOR + 0.04)
    if _candidate_satisfies_positional_action_duplicate_request(
        instruction,
        instruction_tokens,
        candidate,
        candidates,
    ):
        score = max(score, TEXT_MATCH_FLOOR + 0.04)
    if model_rect is not None:
        score += 0.05 * _proximity_score(candidate.rect, model_rect)
        if _same_label_duplicate_has_stronger_geometry(
            candidate,
            candidates,
            model_rect,
        ) and not _candidate_satisfies_contextual_duplicate_request(
            instruction,
            candidate,
            candidates,
        ):
            score = min(score, TEXT_MATCH_FLOOR - 0.01)
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
    if candidate.control_type in NON_ACTIONABLE_CONTROL_TYPES:
        return 0.0
    if _cell_target_request_mismatch(instruction, candidate):
        return 0.0
    if _tab_context_candidate_mismatch(instruction, candidate, candidates):
        return 0.0
    if _named_control_label_missing(instruction, candidate, candidates):
        return 0.0
    if _record_target_alternative_mismatch(instruction, candidate, candidates):
        return 0.0
    if _access_permission_action_mismatch(instruction, candidate, candidates):
        return 0.0
    if _taskbar_start_button_action_mismatch(instruction_tokens, candidate):
        return 0.0
    if _taskbar_start_button_generic_menu_mismatch(instruction, candidate):
        return 0.0
    if _taskbar_task_view_action_mismatch(instruction, instruction_tokens, candidate):
        return 0.0
    if _taskbar_hidden_icons_action_mismatch(instruction_tokens, candidate):
        return 0.0
    if _taskbar_show_desktop_action_mismatch(instruction_tokens, candidate):
        return 0.0
    if _program_manager_desktop_item_action_mismatch(instruction_tokens, candidate):
        return 0.0
    if _taskbar_app_state_action_mismatch(instruction_tokens, candidate):
        return 0.0
    if _taskbar_surface_context_mismatch(instruction, candidate):
        return 0.0
    if _browser_profile_identity_action_mismatch(instruction_tokens, candidate):
        return 0.0
    if _browser_profile_page_action_mismatch(instruction, candidate):
        return 0.0
    if _browser_chrome_app_context_mismatch(instruction, candidate):
        return 0.0
    if _background_transient_surface_target_mismatch(instruction, candidate, candidates):
        return 0.0
    if _browser_menu_button_action_mismatch(instruction, candidate):
        return 0.0
    if _browser_navigation_chrome_action_mismatch(instruction, candidate):
        return 0.0
    if _browser_toolbar_chrome_action_mismatch(instruction, candidate):
        return 0.0
    if _browser_address_bar_content_mismatch(
        instruction,
        instruction_tokens,
        candidate,
    ):
        return 0.0
    if _browser_address_bar_alternative_mismatch(
        instruction,
        instruction_tokens,
        candidate,
        candidates,
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
    if _clear_close_action_mismatch(instruction, instruction_tokens, candidate, candidates):
        return 0.0
    if _close_context_action_mismatch(instruction, candidate, candidates):
        return 0.0
    if _window_close_alternative_mismatch(instruction, candidate, candidates):
        return 0.0
    if _close_tab_action_mismatch(instruction, candidate, candidates):
        return 0.0
    if _browser_new_tab_bookmark_action_mismatch(instruction_tokens, candidate):
        return 0.0
    if _browser_new_tab_action_mismatch(instruction, instruction_tokens, candidate):
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
    if _pin_state_action_mismatch(instruction, candidate):
        return 0.0
    if _password_visibility_state_action_mismatch(instruction, candidate):
        return 0.0
    if _audio_output_polarity_action_mismatch(instruction, candidate, candidates):
        return 0.0
    if _history_action_mismatch(instruction, candidate):
        return 0.0
    if _explicit_checkbox_like_control_type_mismatch(
        instruction,
        candidate,
        _instruction_control_intents(instruction),
    ):
        return 0.0
    if _checkbox_state_action_mismatch(instruction, candidate):
        return 0.0
    if _navigation_media_transport_action_mismatch(instruction, candidate):
        return 0.0
    if _calendar_exact_alternative_mismatch(instruction, candidate, candidates):
        return 0.0
    if _navigation_backup_action_mismatch(instruction, candidate):
        return 0.0
    if _unresolved_contextual_duplicate_mismatch(instruction, candidate, candidates):
        return 0.0
    if _implicit_container_context_duplicate_mismatch(instruction, candidate, candidates):
        return 0.0
    if _explicit_transient_surface_alternative_mismatch(instruction, candidate, candidates):
        return 0.0
    if _contained_row_action_context_mismatch(instruction, candidate, candidates):
        return 0.0
    if _positional_action_duplicate_mismatch(
        instruction,
        instruction_tokens,
        candidate,
        candidates,
    ):
        return 0.0
    if _contextual_surface_action_alternative_mismatch(
        instruction,
        instruction_tokens,
        candidate,
        candidates,
    ):
        return 0.0
    if _prepositional_context_action_alternative_mismatch(
        instruction,
        instruction_tokens,
        candidate,
        candidates,
    ):
        return 0.0
    if _explicit_action_context_mismatch_without_contextual_evidence(
        instruction,
        candidate,
        candidates,
    ):
        return 0.0
    if _object_only_action_context_mismatch(instruction, candidate):
        return 0.0
    if _action_object_alias_context_requested(
        instruction
    ) and _exact_action_word_alternative_mismatch(
        instruction,
        candidate,
        candidates,
        None,
    ) and not _ambiguous_exact_action_alias_alternative(instruction, candidate, candidates):
        return 0.0
    if _exclusive_action_family_mismatch(instruction, candidate.descriptor):
        return 0.0
    if _mail_tab_account_reference_mismatch(instruction_tokens, candidate):
        return 0.0
    if _browser_tab_auth_action_mismatch(instruction_tokens, candidate):
        return 0.0
    if _browser_tab_generic_section_mismatch(instruction, instruction_tokens, candidate):
        return 0.0
    if _browser_tab_contextual_item_mismatch(instruction, candidate):
        return 0.0
    if _dropdown_item_request_launcher_mismatch(instruction, candidate, candidates):
        return 0.0
    if _dropdown_item_request_menuitem_mismatch(instruction, candidate, candidates):
        return 0.0
    if _dropdown_option_launcher_mismatch(instruction, candidate, candidates):
        return 0.0
    if _literal_stopword_name_alternative_mismatch(instruction, candidate, candidates):
        return 0.0
    if _explicit_combobox_alternative_mismatch(instruction, candidate, candidates):
        return 0.0
    if _explicit_spinner_alternative_mismatch(instruction, candidate, candidates):
        return 0.0
    if _spinner_stepper_parent_mismatch(instruction, candidate, candidates):
        return 0.0
    if _explicit_slider_alternative_mismatch(instruction, candidate, candidates):
        return 0.0
    if _explicit_pane_alternative_mismatch(instruction, candidate, candidates):
        return 0.0
    if _explicit_surface_container_alternative_mismatch(instruction, candidate, candidates):
        return 0.0
    if _explicit_item_alternative_mismatch(instruction, candidate, candidates):
        return 0.0
    if _explicit_generic_item_control_type_mismatch(instruction, candidate):
        return 0.0
    if _explicit_field_alternative_mismatch(instruction, candidate, candidates):
        return 0.0
    if _explicit_option_alternative_mismatch(instruction, candidate, candidates):
        return 0.0
    if _explicit_checkbox_like_alternative_mismatch(instruction, candidate, candidates):
        return 0.0
    if _explicit_subtype_alternative_mismatch(instruction, candidate, candidates):
        return 0.0
    visible_tokens = _candidate_visible_text_tokens(candidate)
    label_tokens = _named_control_candidate_label_tokens(candidate, candidates)
    candidate_tokens = _candidate_semantic_tokens(candidate) | label_tokens
    if not candidate_tokens:
        return 0.0
    if _candidate_satisfies_positional_action_duplicate_request(
        instruction,
        instruction_tokens,
        candidate,
        candidates,
    ):
        score = TEXT_MATCH_FLOOR + 0.04
        if visible_tokens:
            score += VISIBLE_TEXT_MATCH_BONUS
        if model_rect is not None:
            score += 0.05 * _proximity_score(candidate.rect, model_rect)
        return min(max(score, 0.0), 1.0)
    match_tokens = _candidate_match_instruction_tokens(
        instruction,
        instruction_tokens,
        candidate,
    )
    overlap = match_tokens & candidate_tokens
    if not overlap:
        return 0.0
    coverage = len(overlap) / max(1, len(match_tokens))
    density = len(overlap) / max(1, len(candidate_tokens))
    score = 0.70 * coverage + 0.18 * min(1.0, density * 3.0)
    if visible_tokens:
        score += VISIBLE_TEXT_MATCH_BONUS
    elif candidate.automation_id.strip():
        score -= AUTOMATION_ONLY_MATCH_PENALTY
        if _automation_only_exact_action_match(candidate, overlap):
            score = max(score, TEXT_MATCH_FLOOR + 0.02)
    if label_tokens and overlap & label_tokens:
        score = max(score, TEXT_MATCH_FLOOR + 0.03)
    if _action_object_alias_context_requested(instruction) and _exact_visible_action_word_match(
        instruction,
        candidate,
    ):
        score = max(score, TEXT_MATCH_FLOOR + 0.04)
    if _candidate_satisfies_contextual_duplicate_request(
        instruction,
        candidate,
        candidates,
    ):
        score = max(score, TEXT_MATCH_FLOOR + 0.04)
    if _row_scoped_action_target_matches_context(instruction, candidate, candidates):
        score = max(score, TEXT_MATCH_FLOOR + 0.04)
    if _candidate_satisfies_positional_action_duplicate_request(
        instruction,
        instruction_tokens,
        candidate,
        candidates,
    ):
        score = max(score, TEXT_MATCH_FLOOR + 0.04)
    if model_rect is not None:
        score += 0.05 * _proximity_score(candidate.rect, model_rect)
        if _same_label_duplicate_has_stronger_geometry(
            candidate,
            candidates,
            model_rect,
        ) and not _candidate_satisfies_contextual_duplicate_request(
            instruction,
            candidate,
            candidates,
        ):
            score = min(score, TEXT_MATCH_FLOOR - 0.01)
    return min(max(score, 0.0), 1.0)


def _single_dialog_dismiss_candidate(
    *,
    instruction: str,
    candidates: list[ControlCandidate],
    model_rect: tuple[int, int, int, int] | None,
) -> TargetResolution | None:
    raw_tokens = _tokens_from_text(instruction)
    window_close_requested = "window" in raw_tokens and not (
        raw_tokens & (DISMISS_WINDOW_CONTEXT_WORDS - {"window"})
    )
    if (
        raw_tokens & DISMISS_WINDOW_CONTEXT_WORDS
        and not raw_tokens & DISMISS_DIALOG_CONTEXT_WORDS
        and not window_close_requested
    ):
        return None
    close_context_tokens = raw_tokens & CLOSE_CONTEXT_TARGET_WORDS
    if close_context_tokens and not (close_context_tokens & DISMISS_DIALOG_CONTEXT_WORDS):
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
    if window_close_requested:
        selected = _window_close_preferred_candidates(selected)
    if window_close_requested:
        contextual_selected = []
    else:
        contextual_selected = [
            candidate
            for candidate in selected
            if _candidate_satisfies_contextual_duplicate_request(
                instruction,
                candidate,
                candidates,
            )
        ]
    if contextual_selected:
        selected = contextual_selected
    elif close_context_tokens & DISMISS_DIALOG_CONTEXT_WORDS:
        return None
    if len(selected) != 1:
        if not selected:
            return None
        exact_label_candidates = [
            candidate
            for candidate in selected
            if _literal_words_from_text(candidate.text)
            == _exact_visible_label_request_words(instruction)
        ]
        if len(exact_label_candidates) == 1:
            candidate = exact_label_candidates[0]
            return TargetResolution(
                rect=candidate.rect,
                confidence=TEXT_MATCH_FLOOR,
                source="text_match",
                matched_text=candidate.descriptor,
                target_id=candidate.id,
            )
        if window_close_requested:
            sort_key = (
                _window_close_candidate_sort_key
                if any(_looks_like_window_titlebar_button(candidate) for candidate in selected)
                else _candidate_sort_key
            )
            candidate = sorted(selected, key=sort_key)[0]
            return TargetResolution(
                rect=candidate.rect,
                confidence=TEXT_MATCH_FLOOR,
                source="text_match",
                matched_text=candidate.descriptor,
                target_id=candidate.id,
            )
        if model_rect is not None:
            ranked_by_geometry = sorted(
                (
                    (_geometry_agreement(candidate.rect, model_rect), candidate)
                    for candidate in selected
                ),
                key=lambda item: (-item[0], _candidate_sort_key(item[1])),
            )
            best_score, best_candidate = ranked_by_geometry[0]
            runner_up_score = 0.0
            for score, other in ranked_by_geometry[1:]:
                if _same_visual_candidate(other, best_candidate):
                    continue
                runner_up_score = score
                break
            if (
                best_score >= TARGET_ID_GEOMETRY_FLOOR
                and best_score - runner_up_score >= TEXT_MATCH_GAP
            ):
                return TargetResolution(
                    rect=best_candidate.rect,
                    confidence=min(1.0, TEXT_MATCH_FLOOR + 0.05 * best_score),
                    source="text_match",
                    matched_text=best_candidate.descriptor,
                    target_id=best_candidate.id,
                )
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


def _window_close_preferred_candidates(
    candidates: list[ControlCandidate],
) -> list[ControlCandidate]:
    if not candidates:
        return []
    titlebar = [candidate for candidate in candidates if _looks_like_window_titlebar_button(candidate)]
    return titlebar or candidates


def _window_close_candidate_sort_key(candidate: ControlCandidate) -> tuple[int, int, int, int, int, int, int]:
    x, y, width, height = candidate.rect
    return (
        candidate.window_rank,
        0 if _looks_like_window_titlebar_button(candidate) else 1,
        -(x + width),
        y,
        -width,
        height,
        candidate.depth,
    )


def _window_close_alternative_mismatch(
    instruction: str,
    candidate: ControlCandidate,
    candidates: list[ControlCandidate],
) -> bool:
    raw_tokens = _tokens_from_text(instruction)
    if not ({"close", "window"} <= raw_tokens):
        return False
    if raw_tokens & {"browser", "page", "tab", "toolbar"}:
        return False
    if not _looks_like_close_or_x_button(candidate):
        return False
    close_candidates = [
        item
        for item in candidates
        if item.id != candidate.id
        and not _same_visual_candidate(item, candidate)
        and _looks_like_close_or_x_button(item)
    ]
    if not close_candidates:
        return False
    candidates_with_selected = close_candidates + [candidate]
    if not any(_looks_like_window_titlebar_button(item) for item in candidates_with_selected):
        return False
    selected = sorted(
        _window_close_preferred_candidates(candidates_with_selected),
        key=_window_close_candidate_sort_key,
    )[0]
    return selected.id != candidate.id


def _target_id_plausibility(
    *,
    instruction: str,
    candidate: ControlCandidate,
    candidates: list[ControlCandidate],
    model_rect: tuple[int, int, int, int] | None,
) -> tuple[bool, float, str]:
    instruction_tokens = _tokenize_instruction(instruction)
    control_intents = _instruction_control_intents(instruction)
    semantic_tokens = _candidate_semantic_tokens_with_field_label(candidate, candidates)
    text_score = _text_evidence_score(instruction_tokens, semantic_tokens)
    if candidate.control_type in NON_ACTIONABLE_CONTROL_TYPES:
        return (
            False,
            text_score,
            "target_id control type mismatch",
        )
    if _cell_target_request_mismatch(instruction, candidate):
        return (
            False,
            text_score,
            "target_id control type mismatch",
        )
    if _tab_context_candidate_mismatch(instruction, candidate, candidates):
        return (
            False,
            text_score,
            "target_id semantic mismatch",
        )
    if _explicit_text_field_control_type_mismatch(instruction, candidate):
        return (
            False,
            text_score,
            "target_id control type mismatch",
        )
    if _named_control_label_missing(instruction, candidate, candidates):
        return (
            False,
            text_score,
            "target_id semantic mismatch",
        )
    if _record_target_alternative_mismatch(instruction, candidate, candidates):
        return (
            False,
            text_score,
            "target_id semantic mismatch",
        )
    if _access_permission_action_mismatch(instruction, candidate, candidates):
        return (
            False,
            text_score,
            "target_id semantic mismatch",
        )
    if _explicit_combobox_alternative_mismatch(instruction, candidate, candidates):
        return (
            False,
            text_score,
            "target_id control type mismatch",
        )
    if _explicit_spinner_alternative_mismatch(instruction, candidate, candidates):
        return (
            False,
            text_score,
            "target_id control type mismatch",
        )
    if _spinner_stepper_parent_mismatch(instruction, candidate, candidates):
        return (
            False,
            text_score,
            "target_id ambiguous",
        )
    if _explicit_slider_alternative_mismatch(instruction, candidate, candidates):
        return (
            False,
            text_score,
            "target_id control type mismatch",
        )
    if _explicit_pane_alternative_mismatch(instruction, candidate, candidates):
        return (
            False,
            text_score,
            "target_id control type mismatch",
        )
    if _explicit_surface_container_alternative_mismatch(instruction, candidate, candidates):
        return (
            False,
            text_score,
            "target_id control type mismatch",
        )
    if _explicit_item_alternative_mismatch(instruction, candidate, candidates):
        return (
            False,
            text_score,
            "target_id control type mismatch",
        )
    if _explicit_generic_item_control_type_mismatch(instruction, candidate):
        return (
            False,
            text_score,
            "target_id control type mismatch",
        )
    if _explicit_field_alternative_mismatch(instruction, candidate, candidates):
        return (
            False,
            text_score,
            "target_id control type mismatch",
        )
    if _explicit_option_alternative_mismatch(instruction, candidate, candidates):
        return (
            False,
            text_score,
            "target_id control type mismatch",
        )
    if _explicit_checkbox_like_alternative_mismatch(instruction, candidate, candidates):
        return (
            False,
            text_score,
            "target_id control type mismatch",
        )
    if _explicit_subtype_alternative_mismatch(instruction, candidate, candidates):
        return (
            False,
            text_score,
            "target_id control type mismatch",
        )
    if _taskbar_start_button_action_mismatch(instruction_tokens, candidate):
        return (
            False,
            text_score,
            "target_id semantic mismatch",
        )
    if _taskbar_start_button_generic_menu_mismatch(instruction, candidate):
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
    if _taskbar_hidden_icons_action_mismatch(instruction_tokens, candidate):
        return (
            False,
            text_score,
            "target_id semantic mismatch",
        )
    if _taskbar_show_desktop_action_mismatch(instruction_tokens, candidate):
        return (
            False,
            text_score,
            "target_id semantic mismatch",
        )
    if _program_manager_desktop_item_action_mismatch(instruction_tokens, candidate):
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
    if _taskbar_surface_context_mismatch(instruction, candidate):
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
    if _browser_profile_page_action_mismatch(instruction, candidate):
        return (
            False,
            text_score,
            "target_id semantic mismatch",
        )
    if _browser_chrome_app_context_mismatch(instruction, candidate):
        return (
            False,
            text_score,
            "target_id semantic mismatch",
        )
    if _background_transient_surface_target_mismatch(instruction, candidate, candidates):
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
    if _browser_navigation_chrome_action_mismatch(instruction, candidate):
        return (
            False,
            text_score,
            "target_id semantic mismatch",
        )
    if _browser_toolbar_chrome_action_mismatch(instruction, candidate):
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
    if _browser_address_bar_alternative_mismatch(
        instruction,
        instruction_tokens,
        candidate,
        candidates,
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
    if _clear_close_action_mismatch(instruction, instruction_tokens, candidate, candidates):
        return (
            False,
            text_score,
            "target_id semantic mismatch",
        )
    if _close_context_action_mismatch(instruction, candidate, candidates):
        return (
            False,
            text_score,
            "target_id semantic mismatch",
        )
    if _window_close_alternative_mismatch(instruction, candidate, candidates):
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
    if _browser_new_tab_action_mismatch(instruction, instruction_tokens, candidate):
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
    if _pin_state_action_mismatch(instruction, candidate):
        return (
            False,
            text_score,
            "target_id semantic mismatch",
        )
    if _password_visibility_state_action_mismatch(instruction, candidate):
        return (
            False,
            text_score,
            "target_id semantic mismatch",
        )
    if _audio_output_polarity_action_mismatch(instruction, candidate, candidates):
        return (
            False,
            text_score,
            "target_id semantic mismatch",
        )
    if _history_action_mismatch(instruction, candidate):
        return (
            False,
            text_score,
            "target_id semantic mismatch",
        )
    if _explicit_checkbox_like_control_type_mismatch(
        instruction,
        candidate,
        control_intents,
    ):
        return (
            False,
            text_score,
            "target_id control type mismatch",
        )
    if _checkbox_state_action_mismatch(instruction, candidate):
        return (
            False,
            text_score,
            "target_id semantic mismatch",
        )
    if _navigation_media_transport_action_mismatch(instruction, candidate):
        return (
            False,
            text_score,
            "target_id semantic mismatch",
        )
    if _calendar_exact_alternative_mismatch(instruction, candidate, candidates):
        return (
            False,
            text_score,
            "target_id semantic mismatch",
        )
    if _navigation_backup_action_mismatch(instruction, candidate):
        return (
            False,
            text_score,
            "target_id semantic mismatch",
        )
    if _unresolved_contextual_duplicate_mismatch(instruction, candidate, candidates):
        return (
            False,
            text_score,
            "target_id ambiguous",
        )
    if _implicit_container_context_duplicate_mismatch(instruction, candidate, candidates):
        return (
            False,
            text_score,
            "target_id semantic mismatch",
        )
    if _explicit_transient_surface_alternative_mismatch(instruction, candidate, candidates):
        return (
            False,
            text_score,
            "target_id semantic mismatch",
        )
    if _generic_control_group_target_ambiguous(
        instruction=instruction,
        instruction_tokens=instruction_tokens,
        candidate=candidate,
        candidates=candidates,
        model_rect=model_rect,
        control_intents=control_intents,
    ):
        return (
            False,
            text_score,
            "target_id ambiguous",
        )
    if _generic_pane_context_duplicate_ambiguous(instruction, candidate, candidates):
        return (
            False,
            text_score,
            "target_id ambiguous",
        )
    if _positional_action_duplicate_mismatch(
        instruction,
        instruction_tokens,
        candidate,
        candidates,
    ):
        return (
            False,
            text_score,
            "target_id semantic mismatch",
        )
    if _contextual_surface_action_alternative_mismatch(
        instruction,
        instruction_tokens,
        candidate,
        candidates,
    ):
        return (
            False,
            text_score,
            "target_id semantic mismatch",
        )
    if _prepositional_context_action_alternative_mismatch(
        instruction,
        instruction_tokens,
        candidate,
        candidates,
    ):
        return (
            False,
            text_score,
            "target_id semantic mismatch",
        )
    if _delimited_context_only_target_alternative_mismatch(
        instruction,
        candidate,
        candidates,
        control_intents,
    ):
        return (
            False,
            text_score,
            "target_id semantic mismatch",
        )
    if _prepositional_context_only_target_alternative_mismatch(
        instruction,
        instruction_tokens,
        candidate,
        candidates,
        control_intents,
    ):
        return (
            False,
            text_score,
            "target_id semantic mismatch",
        )
    if _reversible_action_exact_alternative_mismatch(
        instruction,
        candidate,
        candidates,
        control_intents,
    ):
        return (
            False,
            text_score,
            "target_id semantic mismatch",
        )
    if _contained_row_action_context_mismatch(instruction, candidate, candidates):
        return (
            False,
            text_score,
            "target_id semantic mismatch",
        )
    if _explicit_action_context_mismatch_without_contextual_evidence(
        instruction,
        candidate,
        candidates,
    ):
        return (
            False,
            text_score,
            "target_id semantic mismatch",
        )
    if _object_only_action_context_mismatch(instruction, candidate):
        return (
            False,
            text_score,
            "target_id semantic mismatch",
        )
    if _ambiguous_exact_literal_alias_alternative(
        instruction,
        candidate,
        candidates,
        control_intents,
    ):
        return (
            False,
            text_score,
            "target_id ambiguous",
        )
    if _exact_visible_label_alternative_mismatch(
        instruction,
        candidate,
        candidates,
        control_intents,
    ):
        return (
            False,
            text_score,
            "target_id ambiguous",
        )
    if _action_object_alias_context_requested(
        instruction
    ) and _exact_action_word_alternative_mismatch(
        instruction,
        candidate,
        candidates,
        control_intents,
    ):
        reason = (
            "target_id ambiguous"
            if _ambiguous_exact_action_alias_alternative(
                instruction,
                candidate,
                candidates,
                control_intents,
            )
            else "target_id semantic mismatch"
        )
        return (
            False,
            text_score,
            reason,
        )
    if _exclusive_action_family_mismatch(instruction, candidate.descriptor):
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
    if _browser_tab_generic_section_mismatch(instruction, instruction_tokens, candidate):
        return (
            False,
            text_score,
            "target_id semantic mismatch",
        )
    if _browser_tab_contextual_item_mismatch(instruction, candidate):
        return (
            False,
            text_score,
            "target_id semantic mismatch",
        )
    if _dropdown_item_request_launcher_mismatch(instruction, candidate, candidates):
        return (
            False,
            text_score,
            "target_id control type mismatch",
        )
    if _dropdown_item_request_menuitem_mismatch(instruction, candidate, candidates):
        return (
            False,
            text_score,
            "target_id semantic mismatch",
        )
    if _combobox_dropdown_arrow_control_mismatch(instruction, candidate, candidates):
        return (
            False,
            text_score,
            "target_id control type mismatch",
        )
    if _named_dropdown_alternative_mismatch(instruction, candidate, candidates):
        return (
            False,
            text_score,
            "target_id semantic mismatch",
        )
    if _dropdown_option_launcher_mismatch(instruction, candidate, candidates):
        return (
            False,
            text_score,
            "target_id control type mismatch",
        )
    if _literal_stopword_name_alternative_mismatch(instruction, candidate, candidates):
        return (
            False,
            text_score,
            "target_id semantic mismatch",
        )
    if instruction_tokens and not semantic_tokens and _has_unparsed_alnum_text(candidate.text):
        return (
            False,
            text_score,
            "target_id semantic mismatch",
        )
    geometry_score = (
        _geometry_agreement(candidate.rect, model_rect) if model_rect is not None else 0.0
    )
    if _contextual_action_candidate_matches_surface_request(
        instruction,
        instruction_tokens,
        candidate,
        candidates,
    ):
        return True, max(0.86, text_score, geometry_score), ""
    if _candidate_satisfies_positional_action_duplicate_request(
        instruction,
        instruction_tokens,
        candidate,
        candidates,
    ):
        return True, max(0.86, text_score, geometry_score), ""
    if model_rect is not None and _same_label_duplicate_has_stronger_geometry(
        candidate,
        candidates,
        model_rect,
    ) and not _candidate_satisfies_contextual_duplicate_request(
        instruction,
        candidate,
        candidates,
    ):
        return False, max(text_score, geometry_score), "target_id ambiguous"
    if _combobox_dropdown_arrow_match(instruction, candidate, candidates):
        return True, max(0.86, text_score, geometry_score), ""
    if _contains_tighter_same_intent_action(
        selected=candidate,
        candidates=candidates,
        instruction=instruction,
        instruction_tokens=instruction_tokens,
        control_intents=control_intents,
    ):
        return (
            False,
            max(text_score, geometry_score),
            "target_id ambiguous",
        )
    if _contains_tighter_row_action_candidate(
        selected=candidate,
        candidates=candidates,
        instruction=instruction,
        instruction_tokens=instruction_tokens,
        control_intents=control_intents,
    ):
        return (
            False,
            max(text_score, geometry_score),
            "target_id ambiguous",
        )
    if _row_scoped_action_target_matches_context(instruction, candidate, candidates):
        return True, max(0.86, text_score, geometry_score), ""
    if (
        not _candidate_matches_control_intent(
            candidate,
            control_intents,
            instruction=instruction,
        )
        and not _spinner_stepper_button_match(instruction, candidate, candidates)
        and not _dropdown_item_request_matches_candidate(instruction, candidate, candidates)
        and not _literal_stopword_name_match_tokens(
            instruction,
            _candidate_visible_text_tokens(candidate),
        )
    ):
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
            instruction=instruction,
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

    if (
        candidate.control_type in SURFACE_CONTEXT_CONTROL_TYPES
        and text_score >= TARGET_ID_TEXT_FLOOR
        and _explicit_surface_container_target_request(
            instruction,
            control_intents,
            candidate.control_type,
        )
    ):
        return True, max(0.86, text_score, geometry_score), ""

    if _has_visible_semantic_alternative(
        instruction=instruction,
        instruction_tokens=instruction_tokens,
        selected=candidate,
        candidates=candidates,
        control_intents=control_intents,
    ) and not _candidate_satisfies_contextual_duplicate_request(
        instruction,
        candidate,
        candidates,
    ) and not _candidate_satisfies_positional_action_duplicate_request(
        instruction,
        instruction_tokens,
        candidate,
        candidates,
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


def _candidate_semantic_tokens_with_field_label(
    candidate: ControlCandidate,
    candidates: list[ControlCandidate],
) -> set[str]:
    return _candidate_semantic_tokens(candidate) | _named_control_candidate_label_tokens(
        candidate,
        candidates,
    )


def _nearby_field_label_tokens(
    candidate: ControlCandidate,
    candidates: list[ControlCandidate],
) -> set[str]:
    if candidate.control_type not in LABELLED_FIELD_CONTROL_TYPES:
        return set()
    if _candidate_visible_text_tokens(candidate) or candidate.automation_id.strip():
        return set()
    return _nearby_field_context_label_tokens(candidate, candidates)


def _nearby_field_context_label_tokens(
    candidate: ControlCandidate,
    candidates: list[ControlCandidate],
) -> set[str]:
    if candidate.control_type not in LABELLED_FIELD_CONTROL_TYPES:
        return set()
    best_score = 0.0
    best_tokens: set[str] = set()
    field_area = max(1, candidate.rect[2] * candidate.rect[3])
    for label in candidates:
        if label.id == candidate.id:
            continue
        if label.control_type not in NON_ACTIONABLE_CONTROL_TYPES:
            continue
        tokens = _candidate_visible_text_tokens(label)
        if not tokens or len(tokens) > 8:
            continue
        label_area = max(1, label.rect[2] * label.rect[3])
        if label_area > field_area * 4:
            continue
        score = _nearby_field_label_score(candidate.rect, label.rect)
        if score > best_score:
            best_score = score
            best_tokens = tokens
    if best_score < 0.5:
        return set()
    return best_tokens


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


def _candidate_visible_semantic_text(candidate: ControlCandidate) -> str:
    text = candidate.text or ""
    if candidate.control_type == "tabitem":
        text = BROWSER_TAB_OWNER_ACCOUNT_RE.sub("", text)
        text = BROWSER_TAB_MEMORY_USAGE_RE.sub("", text)
    return text.strip()


def _has_unparsed_alnum_text(text: str) -> bool:
    value = (text or "").strip()
    return bool(value and not _tokens_from_text(value) and any(ch.isalnum() for ch in value))


def _candidate_inferred_semantic_tokens(candidate: ControlCandidate) -> set[str]:
    if _looks_like_browser_profile_button(candidate):
        return set(BROWSER_PROFILE_TOKENS)
    if _looks_like_browser_menu_button(candidate):
        return set(BROWSER_MENU_BUTTON_TOKENS)
    if _looks_like_taskbar_search_button(candidate):
        return set(TASKBAR_WINDOWS_SEARCH_TOKENS)
    if _looks_like_taskbar_clock_status(candidate):
        return set(TASKBAR_CLOCK_STATUS_IDENTITY_WORDS)
    return set()


def _looks_like_taskbar_search_button(candidate: ControlCandidate) -> bool:
    if candidate.control_type not in {"button", "splitbutton"}:
        return False
    window_tokens = _tokens_from_text(candidate.window_title)
    if not (window_tokens & TASKBAR_WINDOW_WORDS):
        return False
    if (candidate.automation_id or "").strip().lower() == "searchgleambutton":
        return True
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


def _looks_like_browser_profile_chrome_button(candidate: ControlCandidate) -> bool:
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
    raw_tokens = _tokens_from_text(" ".join((candidate.text, candidate.automation_id)))
    return bool(raw_tokens & (BROWSER_PROFILE_TOKENS | BROWSER_PROFILE_LABEL_HINT_WORDS))


def _browser_profile_identity_action_mismatch(
    instruction_tokens: set[str],
    candidate: ControlCandidate,
) -> bool:
    if not instruction_tokens & BROWSER_PROFILE_TOKENS:
        return False
    if instruction_tokens & BROWSER_PROFILE_ACTION_CONTEXT_WORDS:
        return False
    if instruction_tokens & BROWSER_PROFILE_WINDOW_WORDS:
        return not _looks_like_browser_profile_chrome_button(candidate)
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


def _browser_profile_page_action_mismatch(
    instruction: str,
    candidate: ControlCandidate,
) -> bool:
    instruction_tokens = _tokens_from_text(instruction)
    if not (instruction_tokens & BROWSER_PROFILE_TOKENS):
        return False
    if not (instruction_tokens & BROWSER_PAGE_TARGET_WORDS):
        return False
    return _looks_like_browser_profile_chrome_button(candidate)


def _browser_address_bar_content_mismatch(
    instruction: str,
    instruction_tokens: set[str],
    candidate: ControlCandidate,
) -> bool:
    if _instruction_requests_page_address_field(instruction) and _looks_like_strong_browser_address_bar(
        candidate
    ):
        return True
    if _instruction_requests_page_address_field(instruction):
        return False
    if not _looks_like_browser_address_bar(candidate):
        return False
    candidate_tokens = _candidate_semantic_tokens(candidate)
    if not (instruction_tokens & candidate_tokens):
        return False
    return not _instruction_requests_browser_address_bar(instruction)


def _looks_like_browser_address_bar(candidate: ControlCandidate) -> bool:
    if candidate.control_type not in {"edit", "combobox"}:
        return False
    raw_tokens = _tokens_from_text(" ".join((candidate.text, candidate.automation_id)))
    if {"address", "bar"} <= raw_tokens:
        return True
    window_tokens = _tokens_from_text(candidate.window_title)
    if window_tokens and not (window_tokens & BROWSER_PROFILE_WINDOW_WORDS):
        return False
    return bool(raw_tokens & (BROWSER_ADDRESS_BAR_ROLE_WORDS - {"bar", "search"}))


def _looks_like_strong_browser_address_bar(candidate: ControlCandidate) -> bool:
    if not _looks_like_browser_address_bar(candidate):
        return False
    raw_tokens = _tokens_from_text(" ".join((candidate.text, candidate.automation_id)))
    return (
        {"address", "bar"} <= raw_tokens
        or {"url", "bar"} <= raw_tokens
        or "omnibox" in raw_tokens
        or {"address", "search"} <= raw_tokens
    )


def _instruction_requests_browser_address_bar(instruction: str) -> bool:
    raw_tokens = _tokens_from_text(instruction)
    if _instruction_requests_page_address_field(instruction):
        return False
    if raw_tokens & (BROWSER_ADDRESS_BAR_REQUEST_WORDS - {"find", "search"}):
        return True
    return "bar" in raw_tokens and bool(raw_tokens & {"find", "search"})


def _instruction_requests_page_address_field(instruction: str) -> bool:
    raw_tokens = _tokens_from_text(instruction)
    if not (raw_tokens & BROWSER_PAGE_TARGET_WORDS):
        return False
    if raw_tokens & BROWSER_APP_IDENTITY_WORDS:
        return False
    return bool(raw_tokens & (BROWSER_ADDRESS_BAR_ROLE_WORDS - {"bar", "search"}))


def _browser_address_bar_alternative_mismatch(
    instruction: str,
    instruction_tokens: set[str],
    candidate: ControlCandidate,
    candidates: list[ControlCandidate],
) -> bool:
    if not _instruction_requests_browser_address_bar(instruction):
        return False
    if _looks_like_strong_browser_address_bar(candidate):
        return False
    candidate_tokens = _candidate_semantic_tokens(candidate)
    if not (instruction_tokens & candidate_tokens & BROWSER_ADDRESS_BAR_ROLE_WORDS):
        return False
    return any(
        other.id != candidate.id and _looks_like_strong_browser_address_bar(other)
        for other in candidates
    )


def _looks_like_browser_menu_button(candidate: ControlCandidate) -> bool:
    if candidate.control_type not in {"button", "splitbutton"}:
        return False
    window_tokens = _tokens_from_text(candidate.window_title)
    if window_tokens and not (window_tokens & BROWSER_PROFILE_WINDOW_WORDS):
        return False
    text_tokens = _tokens_from_text(candidate.text)
    if text_tokens == {"chrome"}:
        return True
    compact_topbar_shape = candidate.rect[2] <= 96 and candidate.rect[3] <= 48
    return bool(
        compact_topbar_shape
        and candidate.rect[1] <= 72
        and {"settings", "more"} <= text_tokens
    )


def _looks_like_browser_toolbar_button(candidate: ControlCandidate) -> bool:
    if candidate.control_type not in {"button", "splitbutton"}:
        return False
    window_tokens = _tokens_from_text(candidate.window_title)
    if not (window_tokens & BROWSER_PROFILE_WINDOW_WORDS):
        return False
    automation_id = (candidate.automation_id or "").strip().lower()
    if automation_id.startswith("view_") or automation_id in BROWSER_CHROME_TOOLBAR_AUTOMATION_IDS:
        return True
    text_tokens = _tokens_from_text(candidate.text)
    compact_toolbar_shape = max(candidate.rect[2], candidate.rect[3]) <= 56
    compact_text_toolbar_shape = candidate.rect[2] <= 96 and candidate.rect[3] <= 44
    if (
        automation_id in BROWSER_CHROME_TOOLBAR_ACTION_AUTOMATION_IDS
        and (compact_toolbar_shape or compact_text_toolbar_shape)
        and candidate.rect[1] <= 72
    ):
        return True
    toolbar_words = text_tokens & BROWSER_CHROME_TOOLBAR_WORDS
    compact_topbar = (compact_toolbar_shape or compact_text_toolbar_shape) and candidate.rect[1] <= 72
    if compact_topbar and ({"tab", "actions", "menu"} <= text_tokens or {"vertical", "tabs"} <= text_tokens):
        return True
    if toolbar_words and candidate.rect[1] > 144:
        return False
    if "find" in toolbar_words and candidate.rect[1] > 72:
        return False
    if toolbar_words & BROWSER_CHROME_TOOLBAR_ACTION_AUTOMATION_IDS and candidate.rect[1] > 72:
        return False
    if toolbar_words & BROWSER_CHROME_FAVORITE_TOOLBAR_WORDS and candidate.rect[1] > 72:
        return False
    return bool(
        toolbar_words
        and (compact_toolbar_shape or compact_text_toolbar_shape)
    )


def _browser_navigation_chrome_action_mismatch(
    instruction: str,
    candidate: ControlCandidate,
) -> bool:
    raw_tokens = _tokens_from_text(instruction)
    if not (raw_tokens & BROWSER_CHROME_NAVIGATION_CONTEXT_WORDS):
        return False
    if not (raw_tokens & NAVIGATION_DIRECTION_WORDS):
        return False
    candidate_tokens = _candidate_semantic_tokens(candidate)
    if not (candidate_tokens & NAVIGATION_DIRECTION_WORDS):
        return False
    return not _looks_like_browser_toolbar_button(candidate)


def _browser_toolbar_chrome_action_mismatch(
    instruction: str,
    candidate: ControlCandidate,
) -> bool:
    raw_tokens = _tokens_from_text(instruction)
    if _instruction_has_explicit_app_local_context(instruction, raw_tokens):
        return False
    if not (raw_tokens & BROWSER_CHROME_NAVIGATION_CONTEXT_WORDS):
        return False
    toolbar_words = raw_tokens & BROWSER_CHROME_TOOLBAR_WORDS
    if not toolbar_words:
        return False
    explicit_browser_context = bool(
        raw_tokens & (BROWSER_CHROME_NAVIGATION_CONTEXT_WORDS - {"toolbar"})
    )
    if (
        not explicit_browser_context
        and toolbar_words <= BROWSER_CHROME_TOOLBAR_ACTION_AUTOMATION_IDS
    ):
        return False
    candidate_tokens = _candidate_semantic_tokens(candidate)
    if not (candidate_tokens & BROWSER_CHROME_TOOLBAR_WORDS):
        return False
    return not _looks_like_browser_toolbar_button(candidate)


def _browser_chrome_app_context_mismatch(
    instruction: str,
    candidate: ControlCandidate,
) -> bool:
    raw_tokens = _tokens_from_text(instruction)
    explicit_app_local = _instruction_has_explicit_app_local_context(instruction, raw_tokens)
    if raw_tokens & BROWSER_CHROME_EXPLICIT_CONTEXT_WORDS and not explicit_app_local:
        return False
    if not (explicit_app_local or _instruction_requests_app_local_surface(instruction, raw_tokens)):
        return False
    return _looks_like_browser_chrome_surface(candidate)


def _background_transient_surface_target_mismatch(
    instruction: str,
    candidate: ControlCandidate,
    candidates: list[ControlCandidate],
) -> bool:
    raw_tokens = _tokens_from_text(instruction)
    if not (raw_tokens & BACKGROUND_TRANSIENT_POSITION_WORDS):
        return False
    if not (raw_tokens & (UNNAMED_FOREGROUND_TRANSIENT_SURFACE_WORDS | DISMISS_DIALOG_CONTEXT_WORDS)):
        return False
    if not candidates:
        return False
    foreground_rank = min(item.window_rank for item in candidates)
    if candidate.window_rank <= foreground_rank:
        return False
    return any(
        item.window_rank == foreground_rank
        and (
            item.control_type in SURFACE_CONTEXT_CONTROL_TYPES
            or _candidate_semantic_tokens(item) & UNNAMED_FOREGROUND_TRANSIENT_SURFACE_WORDS
            or _surface_context_type_tokens(item.control_type) & UNNAMED_FOREGROUND_TRANSIENT_SURFACE_WORDS
        )
        for item in candidates
    )


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
    surface_tokens = _object_token_variants(raw_tokens)
    if surface_tokens & BROWSER_CHROME_APP_CONTEXT_WORDS:
        return True
    if _instruction_requests_named_page_local_surface(raw_tokens):
        return True
    if raw_tokens & FOLDER_IDENTITY_WORDS:
        return True
    if raw_tokens & ACTION_OBJECT_ALIAS_CONTEXT_WORDS:
        return True
    text = (instruction or "").lower()
    return bool(
        re.search(r"\bin\s+(?:the\s+)?app\b", text)
        or re.search(r"\b(?:in|inside|on|within)\s+(?:the\s+)?page\b", text)
        or re.search(r"\bin[-\s]?page\b", text)
    )


def _instruction_requests_named_page_local_surface(raw_tokens: set[str]) -> bool:
    if not (raw_tokens & BROWSER_PAGE_TARGET_WORDS):
        return False
    if raw_tokens & BROWSER_CHROME_EXPLICIT_CONTEXT_WORDS:
        return False
    if not (raw_tokens & (OPEN_VIEW_REQUEST_WORDS | GENERIC_OBJECT_REQUEST_WORDS)):
        return False
    object_tokens = _object_token_variants(
        raw_tokens
        - BROWSER_PAGE_TARGET_WORDS
        - OPEN_VIEW_REQUEST_WORDS
        - GENERIC_OBJECT_REQUEST_WORDS
        - ACTION_OBJECT_STOPWORDS
    )
    return bool(object_tokens)


def _looks_like_browser_chrome_surface(candidate: ControlCandidate) -> bool:
    if _looks_like_os_chrome_surface(candidate):
        return True
    if _looks_like_browser_site_information_chrome_button(candidate):
        return True
    window_tokens = _tokens_from_text(candidate.window_title)
    if not (window_tokens & BROWSER_PROFILE_WINDOW_WORDS):
        return False
    if _looks_like_browser_toolbar_button(candidate) or _looks_like_browser_menu_button(candidate):
        return True
    if _looks_like_browser_new_tab_button(candidate):
        return True
    if _looks_like_browser_profile_chrome_button(candidate):
        return True
    return candidate.control_type == "tabitem" and candidate.rect[1] <= 72


def _looks_like_os_chrome_surface(candidate: ControlCandidate) -> bool:
    return _looks_like_taskbar_search_button(candidate) or _looks_like_window_titlebar_button(candidate)


def _looks_like_window_titlebar_button(candidate: ControlCandidate) -> bool:
    if candidate.control_type not in {"button", "splitbutton"}:
        return False
    raw_tokens = _tokens_from_text(" ".join((candidate.text, candidate.automation_id)))
    if not (raw_tokens & {"close", "maximize", "minimize", "minimise", "restore"}):
        return False
    if candidate.rect[1] <= 44:
        return True
    compact_titlebar_shape = candidate.rect[2] <= 72 and candidate.rect[3] <= 48
    window_tokens = _tokens_from_text(candidate.window_title)
    return bool(compact_titlebar_shape and window_tokens & BROWSER_PROFILE_WINDOW_WORDS)


def _looks_like_browser_site_information_chrome_button(candidate: ControlCandidate) -> bool:
    if candidate.control_type not in {"button", "splitbutton"}:
        return False
    raw_text = " ".join((candidate.text or "", candidate.automation_id or "")).lower()
    raw_tokens = _tokens_from_text(raw_text)
    compact_chrome_shape = max(candidate.rect[2], candidate.rect[3]) <= 64
    if "site_info_lock" in raw_text and compact_chrome_shape:
        return True
    window_tokens = _tokens_from_text(candidate.window_title)
    if not (window_tokens & BROWSER_PROFILE_WINDOW_WORDS):
        return False
    if {"site", "info", "lock"} <= raw_tokens:
        return True
    if candidate.rect[1] > 72:
        return False
    visible_tokens = _candidate_visible_text_tokens(candidate)
    return {"site", "information"} <= visible_tokens or {"site", "info"} <= raw_tokens


def _browser_menu_button_action_mismatch(
    instruction: str,
    candidate: ControlCandidate,
) -> bool:
    raw_tokens = _tokens_from_text(instruction)
    if _looks_like_browser_menu_button(candidate):
        return bool(
            raw_tokens & BROWSER_MENU_SETTINGS_REQUEST_WORDS
            and not (raw_tokens & BROWSER_MENU_REQUEST_WORDS)
        )
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
    return _looks_like_browser_toolbar_button(candidate)


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


def _clear_close_action_mismatch(
    instruction: str,
    instruction_tokens: set[str],
    candidate: ControlCandidate,
    candidates: list[ControlCandidate],
) -> bool:
    instruction_words = _literal_words_from_text(instruction)
    if "clear" not in instruction_words and "clear" not in instruction_tokens:
        return False
    if not _looks_like_close_or_x_button(candidate):
        return False
    if _candidate_has_literal_clear_evidence(candidate):
        return False
    return not _has_clear_field_context(candidate, candidates, instruction_words)


def _close_context_action_mismatch(
    instruction: str,
    candidate: ControlCandidate,
    candidates: list[ControlCandidate] | None = None,
) -> bool:
    instruction_words = _literal_words_from_text(instruction)
    if not (instruction_words & CLEAR_CLOSE_WORDS):
        return False
    requested_context = instruction_words & CLOSE_CONTEXT_TARGET_WORDS
    if not requested_context:
        return False
    if not _looks_like_close_or_x_button(candidate):
        return False
    if candidates is not None and _candidate_satisfies_contextual_duplicate_request(
        instruction,
        candidate,
        candidates,
    ):
        return False
    control_words = _literal_words_from_text(candidate.descriptor)
    return not bool(control_words & requested_context)


def _looks_like_close_or_x_button(candidate: ControlCandidate) -> bool:
    if candidate.control_type not in {"button", "splitbutton"}:
        return False
    literal_tokens = _literal_words_from_text(candidate.descriptor)
    if literal_tokens & CLEAR_CLOSE_WORDS:
        return True
    return _is_x_symbol_text(candidate.text) or _is_x_symbol_text(candidate.automation_id)


def _candidate_has_literal_clear_evidence(candidate: ControlCandidate) -> bool:
    return "clear" in _literal_words_from_text(candidate.descriptor)


def _has_clear_field_context(
    candidate: ControlCandidate,
    candidates: list[ControlCandidate],
    instruction_words: set[str],
) -> bool:
    requested_context = instruction_words & CLEAR_CONTEXT_WORDS
    for context in candidates:
        if context.id == candidate.id or _same_visual_candidate(context, candidate):
            continue
        if not (
            context.control_type in CLEAR_CONTEXT_CONTROL_TYPES
            or _candidate_context_tokens(context) & CLEAR_CONTEXT_WORDS
        ):
            continue
        context_tokens = _candidate_context_tokens(context)
        if requested_context and not (context_tokens & requested_context):
            continue
        expanded = _expand_rect(context.rect, 10)
        if _contains_rect(expanded, candidate.rect) or _center_inside(candidate.rect, expanded):
            return True
    return False


def _candidate_context_tokens(candidate: ControlCandidate) -> set[str]:
    return (
        _candidate_semantic_tokens(candidate)
        | _literal_words_from_text(candidate.descriptor)
        | _literal_words_from_text(candidate.window_title)
    )


def _literal_words_from_text(text: str) -> set[str]:
    return set(_literal_word_sequence(text))


def _literal_word_sequence(text: str) -> list[str]:
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", text or "")
    spaced = re.sub(r"[_\-.]+", " ", spaced)
    return re.findall(r"[a-z0-9]+", spaced.lower())


def _is_x_symbol_text(text: str) -> bool:
    return (text or "").strip().lower() in X_SYMBOL_TEXTS


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


def _browser_tab_bookmark_action_mismatch(
    instruction: str,
    instruction_tokens: set[str],
    candidate: ControlCandidate,
) -> bool:
    if candidate.control_type not in {"button", "splitbutton"}:
        return False
    if not (instruction_tokens & BROWSER_BOOKMARK_ACTION_WORDS):
        return False
    window_tokens = _tokens_from_text(candidate.window_title)
    if window_tokens and not (window_tokens & BROWSER_PROFILE_WINDOW_WORDS):
        return False

    raw_candidate_tokens = _tokens_from_text(candidate.descriptor)
    if not (raw_candidate_tokens & BROWSER_BOOKMARK_ACTION_WORDS):
        return False
    if not (raw_candidate_tokens & BROWSER_BOOKMARK_TAB_CONTEXT_WORDS):
        return False

    raw_instruction_tokens = _tokens_from_text(instruction)
    if raw_instruction_tokens & BROWSER_BOOKMARK_TAB_CONTEXT_WORDS:
        return False
    if "add" in raw_instruction_tokens and "bookmark" in raw_instruction_tokens:
        return False
    if raw_instruction_tokens & BROWSER_BOOKMARK_ITEM_CONTEXT_WORDS:
        return True
    return bool(raw_instruction_tokens & {"favorite", "star"} and raw_instruction_tokens & {"this", "that"})


def _browser_new_tab_action_mismatch(
    instruction: str,
    instruction_tokens: set[str],
    candidate: ControlCandidate,
) -> bool:
    if not _looks_like_browser_new_tab_button(candidate):
        return False
    if not (instruction_tokens & BROWSER_NEW_TAB_RELATED_REQUEST_WORDS):
        return False
    if _instruction_mentions_tab_context(instruction):
        return False
    return True


def _looks_like_browser_new_tab_button(candidate: ControlCandidate) -> bool:
    if candidate.control_type not in {"button", "splitbutton"}:
        return False
    window_tokens = _tokens_from_text(candidate.window_title)
    if window_tokens and not (window_tokens & BROWSER_PROFILE_WINDOW_WORDS):
        return False
    raw_text_tokens = _tokens_from_text(candidate.text)
    automation_tokens = _tokens_from_text(candidate.automation_id)
    return {"new", "tab"} <= raw_text_tokens or {"new", "tab"} <= automation_tokens


def _close_tab_action_mismatch(
    instruction: str,
    candidate: ControlCandidate,
    candidates: list[ControlCandidate],
) -> bool:
    raw_tokens = _tokens_from_text(instruction)
    close_tab_requested = bool(raw_tokens & {"close", "dismiss"} and raw_tokens & {"tab", "tabs", "tabitem"})
    close_page_requested = bool(
        raw_tokens & {"close", "dismiss"}
        and raw_tokens & BROWSER_PAGE_TARGET_WORDS
        and _tokens_from_text(candidate.window_title) & BROWSER_PROFILE_WINDOW_WORDS
    )
    if not (close_tab_requested or close_page_requested):
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


def _combobox_dropdown_arrow_match(
    instruction: str,
    candidate: ControlCandidate,
    candidates: list[ControlCandidate],
) -> bool:
    raw_tokens = _tokens_from_text(instruction)
    if raw_tokens & {"choice", "choices", "option", "options"}:
        return False
    if not (
        raw_tokens & COMBOBOX_DROPDOWN_ARROW_REQUEST_WORDS
        or {"drop", "down"} <= raw_tokens
    ):
        return False
    if candidate.control_type == "combobox":
        if _dropdown_item_request_launcher_mismatch(instruction, candidate, candidates):
            return False
        return not _has_combobox_dropdown_arrow_button(candidate, candidates)
    if candidate.control_type not in {"button", "splitbutton"}:
        return False
    control_tokens = _tokens_from_text(candidate.descriptor)
    if control_tokens and not (control_tokens & COMBOBOX_DROPDOWN_ARROW_CONTROL_WORDS):
        return False
    return _has_adjacent_combobox(candidate, candidates)


def _combobox_dropdown_arrow_control_mismatch(
    instruction: str,
    candidate: ControlCandidate,
    candidates: list[ControlCandidate],
) -> bool:
    if candidate.control_type != "combobox":
        return False
    raw_tokens = _tokens_from_text(instruction)
    if not (raw_tokens & {"arrow", "caret", "chevron"}):
        return False
    return _has_combobox_dropdown_arrow_button(candidate, candidates)


def _dropdown_option_launcher_mismatch(
    instruction: str,
    candidate: ControlCandidate,
    candidates: list[ControlCandidate],
) -> bool:
    raw_tokens = _tokens_from_text(instruction)
    dropdown_requested = (
        "dropdown" in raw_tokens
        or "combobox" in raw_tokens
        or "combo" in raw_tokens
        or {"drop", "down"} <= raw_tokens
    )
    if not dropdown_requested:
        return False
    if raw_tokens & {"menuitem", "option", "options"}:
        return False
    if candidate.control_type != "menuitem":
        return False
    if _dropdown_item_request_matches_candidate(instruction, candidate, candidates):
        return False
    if _menuitem_is_splitbutton_dropdown_segment(candidate, candidates):
        return False
    return _has_dropdown_launcher_candidate(candidate, candidates)


def _named_dropdown_request_matches_candidate(
    instruction: str,
    candidate: ControlCandidate,
    candidates: list[ControlCandidate],
) -> bool:
    if candidate.control_type != "combobox":
        return False
    if _tokens_from_text(instruction) & {"choice", "choices", "option", "options"}:
        return False
    if _dropdown_item_request_tokens(instruction):
        return False
    if _tokens_from_text(instruction) & {"arrow", "caret", "chevron"}:
        return False
    if not _dropdown_launcher_requested(instruction):
        return False
    requested = _named_dropdown_request_tokens(instruction)
    if not requested:
        return False
    identity = (
        _candidate_visible_text_tokens(candidate)
        | _tokens_from_text(candidate.text)
        | _field_alternative_label_tokens(candidate, candidates)
    )
    return bool(requested & identity)


def _named_dropdown_alternative_mismatch(
    instruction: str,
    candidate: ControlCandidate,
    candidates: list[ControlCandidate],
) -> bool:
    if candidate.control_type != "combobox":
        return False
    if _tokens_from_text(instruction) & {"choice", "choices", "option", "options"}:
        return False
    if _dropdown_item_request_tokens(instruction):
        return False
    if _tokens_from_text(instruction) & {"arrow", "caret", "chevron"}:
        return False
    if not _dropdown_launcher_requested(instruction):
        return False
    requested = _named_dropdown_request_tokens(instruction)
    if not requested:
        return False
    candidate_identity = (
        _candidate_visible_text_tokens(candidate)
        | _tokens_from_text(candidate.text)
        | _field_alternative_label_tokens(candidate, candidates)
    )
    if requested & candidate_identity:
        return False
    for other in candidates:
        if other.id == candidate.id or _same_visual_candidate(other, candidate):
            continue
        if other.control_type != "combobox":
            continue
        other_identity = (
            _candidate_visible_text_tokens(other)
            | _tokens_from_text(other.text)
            | _field_alternative_label_tokens(other, candidates)
        )
        if requested & other_identity:
            return True
    return False


def _named_dropdown_request_tokens(instruction: str) -> set[str]:
    raw_tokens = _tokens_from_text(instruction)
    return _tokenize_instruction(instruction) | (
        raw_tokens - OPEN_VIEW_REQUEST_WORDS - CONTAINED_CONTROL_REQUEST_WORDS
    )


def _dropdown_item_request_tokens(instruction: str) -> set[str]:
    raw_tokens = _tokens_from_text(instruction)
    launcher_context = _dropdown_item_launcher_context_tokens(instruction)
    if not _dropdown_launcher_requested(instruction) and not ("menu" in raw_tokens and launcher_context):
        return set()
    if not (raw_tokens & {"from", "in", "inside", "within"}):
        return set()
    return _object_token_variants(
        (_tokenize_instruction(instruction) | raw_tokens)
        - OPEN_VIEW_REQUEST_WORDS
        - GENERIC_OBJECT_REQUEST_WORDS
        - CONTAINED_CONTROL_REQUEST_WORDS
        - launcher_context
        - {"a", "an", "from", "in", "inside", "the", "within"}
    )


def _dropdown_item_launcher_context_tokens(instruction: str) -> set[str]:
    text = (instruction or "").lower()
    matches = re.findall(
        r"\b(?:from|in|inside|within)\s+(?:the\s+)?([a-z0-9][a-z0-9\s_.-]{0,60}?)\s+"
        r"(?:drop\s+down|dropdown|combobox|combo|menu|picker|selector)\b",
        text,
    )
    if not matches:
        return set()
    tokens: set[str] = set()
    for match in matches:
        tokens.update(_tokens_from_text(match))
    return _object_token_variants(
        tokens
        - OPEN_VIEW_REQUEST_WORDS
        - GENERIC_OBJECT_REQUEST_WORDS
        - CONTAINED_CONTROL_REQUEST_WORDS
        - {"a", "an", "from", "inside", "in", "the", "within"}
    )


def _dropdown_item_request_matches_candidate(
    instruction: str,
    candidate: ControlCandidate,
    candidates: list[ControlCandidate] | None = None,
) -> bool:
    if candidate.control_type != "menuitem":
        return False
    requested = _dropdown_item_request_tokens(instruction)
    if not requested:
        return False
    candidate_tokens = _candidate_semantic_tokens(candidate) | _tokens_from_text(candidate.text)
    if not (requested & candidate_tokens):
        return False
    launcher_context = _dropdown_item_launcher_context_tokens(instruction)
    if not launcher_context:
        return True
    return bool(
        candidates
        and _menuitem_matches_requested_dropdown_launcher(
            candidate,
            candidates,
            launcher_context,
        )
    )


def _menuitem_matches_requested_dropdown_launcher(
    candidate: ControlCandidate,
    candidates: list[ControlCandidate],
    launcher_context: set[str],
) -> bool:
    for launcher in candidates:
        if launcher.id == candidate.id or _same_visual_candidate(launcher, candidate):
            continue
        if launcher.control_type not in {"button", "combobox", "edit", "splitbutton"}:
            continue
        launcher_tokens = (
            _candidate_semantic_tokens(launcher)
            | _tokens_from_text(launcher.text)
            | _tokens_from_text(launcher.automation_id)
        )
        if not (launcher_context & launcher_tokens):
            continue
        if _menuitem_is_below_aligned_launcher(candidate, launcher):
            return True
    return False


def _dropdown_item_request_menuitem_mismatch(
    instruction: str,
    candidate: ControlCandidate,
    candidates: list[ControlCandidate],
) -> bool:
    if candidate.control_type != "menuitem":
        return False
    requested = _dropdown_item_request_tokens(instruction)
    if not requested:
        return False
    candidate_tokens = _candidate_semantic_tokens(candidate) | _tokens_from_text(candidate.text)
    if not (requested & candidate_tokens):
        return False
    if _dropdown_item_request_matches_candidate(instruction, candidate, candidates):
        return False
    for other in candidates:
        if other.id == candidate.id or _same_visual_candidate(other, candidate):
            continue
        if _dropdown_item_request_matches_candidate(instruction, other, candidates):
            return True
    return False


def _dropdown_item_request_launcher_mismatch(
    instruction: str,
    candidate: ControlCandidate,
    candidates: list[ControlCandidate],
) -> bool:
    if candidate.control_type not in {"button", "combobox", "edit", "splitbutton"}:
        return False
    requested = _dropdown_item_request_tokens(instruction)
    if not requested:
        return False
    for other in candidates:
        if other.id == candidate.id or _same_visual_candidate(other, candidate):
            continue
        if not _dropdown_item_request_matches_candidate(instruction, other, candidates):
            continue
        if _has_dropdown_launcher_candidate(other, candidates):
            return True
        if _menuitem_is_below_aligned_launcher(other, candidate):
            return True
    return False


def _dropdown_launcher_requested(instruction: str) -> bool:
    raw_tokens = _tokens_from_text(instruction)
    return (
        "dropdown" in raw_tokens
        or "combobox" in raw_tokens
        or "combo" in raw_tokens
        or "picker" in raw_tokens
        or "selector" in raw_tokens
        or {"drop", "down"} <= raw_tokens
    )


def _has_dropdown_launcher_candidate(
    candidate: ControlCandidate,
    candidates: list[ControlCandidate],
) -> bool:
    candidate_tokens = _candidate_visible_text_tokens(candidate)
    for launcher in candidates:
        if launcher.id == candidate.id or _same_visual_candidate(launcher, candidate):
            continue
        if launcher.control_type not in {"button", "combobox", "edit", "splitbutton"}:
            continue
        launcher_tokens = _candidate_visible_text_tokens(launcher)
        same_label_launcher = (
            candidate_tokens
            and launcher_tokens
            and _text_evidence_score(candidate_tokens, launcher_tokens) >= TARGET_ID_TEXT_FLOOR
        )
        if same_label_launcher:
            return True
        if launcher.control_type in {"combobox", "edit"} and _menuitem_is_below_aligned_launcher(candidate, launcher):
            return True
    return False


def _menuitem_is_below_aligned_launcher(
    candidate: ControlCandidate,
    launcher: ControlCandidate,
) -> bool:
    item_left, item_top, item_width, _item_height = candidate.rect
    item_right = item_left + item_width
    launcher_left, launcher_top, launcher_width, launcher_height = launcher.rect
    launcher_right = launcher_left + launcher_width
    horizontal_overlap = max(0, min(item_right, launcher_right) - max(item_left, launcher_left))
    overlap_ratio = horizontal_overlap / max(1, min(item_width, launcher_width))
    vertical_gap = item_top - (launcher_top + launcher_height)
    return overlap_ratio >= 0.55 and -8 <= vertical_gap <= 320


def _menuitem_is_splitbutton_dropdown_segment(
    candidate: ControlCandidate,
    candidates: list[ControlCandidate],
) -> bool:
    if candidate.control_type != "menuitem":
        return False
    candidate_tokens = _candidate_visible_text_tokens(candidate)
    item_left, item_top, item_width, item_height = candidate.rect
    item_right = item_left + item_width
    for launcher in candidates:
        if launcher.id == candidate.id or _same_visual_candidate(launcher, candidate):
            continue
        if launcher.control_type != "splitbutton":
            continue
        launcher_tokens = _candidate_visible_text_tokens(launcher)
        if (
            candidate_tokens
            and launcher_tokens
            and _text_evidence_score(candidate_tokens, launcher_tokens) < TARGET_ID_TEXT_FLOOR
        ):
            continue
        launcher_left, launcher_top, launcher_width, launcher_height = launcher.rect
        launcher_right = launcher_left + launcher_width
        same_row = abs(item_top - launcher_top) <= 4 and abs(item_height - launcher_height) <= 8
        right_segment = (
            item_left >= launcher_left + launcher_width * 0.52
            and item_right <= launcher_right + 4
            and item_width <= max(56, launcher_width * 0.36)
        )
        if same_row and right_segment:
            return True
    return False


def _explicit_text_field_control_type_mismatch(
    instruction: str,
    candidate: ControlCandidate,
) -> bool:
    if _strict_text_entry_requested(instruction):
        return candidate.control_type != "edit"
    if candidate.control_type != "spinner":
        return False
    raw_tokens = _tokens_from_text(instruction)
    if raw_tokens & {"spinner", "stepper"}:
        return False
    return False


def _strict_text_entry_requested(instruction: str) -> bool:
    raw_tokens = _tokens_from_text(instruction)
    return (
        "textbox" in raw_tokens
        or "textarea" in raw_tokens
        or {"text", "box"} <= raw_tokens
        or {"text", "field"} <= raw_tokens
        or {"text", "input"} <= raw_tokens
    )


def _single_strict_text_entry_candidate(
    instruction: str,
    candidates: list[ControlCandidate],
) -> ControlCandidate | None:
    if not _strict_text_entry_requested(instruction):
        return None
    selected: ControlCandidate | None = None
    for candidate in candidates:
        if candidate.control_type != "edit":
            continue
        if _browser_chrome_app_context_mismatch(instruction, candidate):
            continue
        if _browser_address_bar_content_mismatch(
            instruction,
            _tokenize_instruction(instruction),
            candidate,
        ):
            continue
        if _named_control_label_missing(instruction, candidate, candidates):
            continue
        if selected is not None and not _same_visual_candidate(selected, candidate):
            return None
        selected = candidate
    return selected


def _named_control_label_missing(
    instruction: str,
    candidate: ControlCandidate,
    candidates: list[ControlCandidate],
) -> bool:
    if candidate.control_type not in NAMED_CONTROL_LABEL_TYPES:
        return False
    requested_label = _named_control_requested_label_tokens(instruction, candidate.control_type)
    if not requested_label:
        return False
    evidence = _named_control_candidate_label_tokens(candidate, candidates)
    return not bool(requested_label & evidence)


def _strict_text_entry_requested_label_tokens(instruction: str) -> set[str]:
    return _named_control_requested_label_tokens(instruction, "edit")


def _named_control_requested_label_tokens(instruction: str, control_type: str) -> set[str]:
    words = _literal_word_sequence(instruction)
    for index, word in enumerate(words):
        role_prefix = _named_control_role_prefix_width(words, index, control_type)
        if role_prefix is None:
            continue
        trailing = _named_control_trailing_label_tokens(words, index + 1)
        if trailing:
            return trailing
        cursor = index - role_prefix
        label_words: list[str] = []
        while cursor >= 0:
            current = words[cursor]
            if current in NAMED_CONTROL_LABEL_BOUNDARY_WORDS:
                break
            label_words.append(current)
            cursor -= 1
        if label_words:
            return _object_token_variants(set(label_words))
    return set()


def _named_control_trailing_label_tokens(words: list[str], start_index: int) -> set[str]:
    cursor = start_index
    while cursor < len(words):
        marker = words[cursor]
        if marker not in NAMED_CONTROL_TRAILING_LABEL_MARKERS:
            cursor += 1
            continue
        cursor += 1
        label_words: list[str] = []
        while cursor < len(words):
            current = words[cursor]
            if current in NAMED_CONTROL_LABEL_BOUNDARY_WORDS:
                break
            label_words.append(current)
            cursor += 1
        if label_words:
            return _object_token_variants(set(label_words))
    return set()


def _named_control_role_prefix_width(
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
        if word in {"combo", "combobox", "dropdown", "selector"}:
            return 1
        if word == "down" and previous == "drop":
            return 2
        return None
    if control_type == "spinner":
        if word in {"spinbox", "spinner", "stepper"}:
            return 1
        if word == "box" and previous == "spin":
            return 2
        return None
    if control_type == "slider":
        return 1 if word == "slider" else None
    if control_type == "checkbox":
        if word in {"checkbox", "switch", "toggle"}:
            return 1
        if word == "box" and previous == "check":
            return 2
        return None
    if control_type == "radiobutton":
        if word in {"radio", "radiobutton"}:
            return 1
        if word == "button" and previous == "radio":
            return 2
        return None
    return None


def _named_control_candidate_label_tokens(
    candidate: ControlCandidate,
    candidates: list[ControlCandidate],
) -> set[str]:
    if candidate.control_type not in NAMED_CONTROL_LABEL_TYPES:
        return set()
    tokens = (
        _field_alternative_label_tokens(candidate, candidates)
        | _nearby_unlabeled_control_label_tokens(candidate, candidates)
    )
    return _object_token_variants(tokens) - NAMED_CONTROL_LABEL_STOPWORDS


def _nearby_unlabeled_control_label_tokens(
    candidate: ControlCandidate,
    candidates: list[ControlCandidate],
) -> set[str]:
    if candidate.control_type not in NAMED_CONTROL_LABEL_TYPES:
        return set()
    if _candidate_visible_text_tokens(candidate) or candidate.automation_id.strip():
        return set()
    best_score = 0.0
    best_tokens: set[str] = set()
    control_area = max(1, candidate.rect[2] * candidate.rect[3])
    for label in candidates:
        if label.id == candidate.id:
            continue
        if label.control_type not in NON_ACTIONABLE_CONTROL_TYPES:
            continue
        tokens = _candidate_visible_text_tokens(label)
        if not tokens or len(tokens) > 8:
            continue
        label_area = max(1, label.rect[2] * label.rect[3])
        if label_area > control_area * 8:
            continue
        score = (
            _nearby_option_label_score(candidate.rect, label.rect)
            if candidate.control_type in {"checkbox", "radiobutton"}
            else _nearby_field_label_score(candidate.rect, label.rect)
        )
        if score > best_score:
            best_score = score
            best_tokens = tokens
    if best_score < 0.5:
        return set()
    return best_tokens


def _nearby_option_label_score(
    option_rect: tuple[int, int, int, int],
    label_rect: tuple[int, int, int, int],
) -> float:
    option_left, option_top, option_width, option_height = option_rect
    option_right = option_left + option_width
    option_bottom = option_top + option_height
    label_left, label_top, label_width, label_height = label_rect
    label_right = label_left + label_width
    label_bottom = label_top + label_height

    y_overlap = max(0, min(option_bottom, label_bottom) - max(option_top, label_top))
    y_ratio = y_overlap / max(1, min(option_height, label_height))
    right_gap = label_left - option_right
    if y_ratio >= 0.45 and -4 <= right_gap <= 240 and label_right >= option_right:
        return 0.75 + 0.25 * (1.0 - min(1.0, max(0, right_gap) / 240.0))
    return _nearby_field_label_score(option_rect, label_rect)


def _explicit_field_alternative_mismatch(
    instruction: str,
    candidate: ControlCandidate,
    candidates: list[ControlCandidate],
) -> bool:
    raw_tokens = _tokens_from_text(instruction)
    if not (raw_tokens & {"field", "fields", "input", "inputs"}):
        return False
    if raw_tokens & {"button", "buttons"}:
        return False
    if candidate.control_type in LABELLED_FIELD_CONTROL_TYPES:
        return False
    candidate_label_tokens = _field_alternative_label_tokens(candidate, candidates)
    if not candidate_label_tokens:
        return False
    for other in candidates:
        if other.id == candidate.id or _same_visual_candidate(other, candidate):
            continue
        if other.control_type not in LABELLED_FIELD_CONTROL_TYPES:
            continue
        field_label_tokens = _field_alternative_label_tokens(other, candidates)
        if field_label_tokens and candidate_label_tokens & field_label_tokens:
            return True
    return False


def _explicit_combobox_alternative_mismatch(
    instruction: str,
    candidate: ControlCandidate,
    candidates: list[ControlCandidate],
) -> bool:
    raw_tokens = _tokens_from_text(instruction)
    explicit_combobox = (
        "combobox" in raw_tokens
        or "combo" in raw_tokens
        or "dropdown" in raw_tokens
        or {"drop", "down"} <= raw_tokens
    )
    if not explicit_combobox:
        return False
    if candidate.control_type == "combobox":
        return False
    if raw_tokens & {"button", "buttons", "launcher"}:
        return False
    if candidate.control_type not in (
        LABELLED_FIELD_CONTROL_TYPES
        | OPTION_ROLE_CONTROL_TYPES
        | {"button", "splitbutton"}
    ):
        return False
    candidate_label_tokens = _field_alternative_label_tokens(candidate, candidates)
    if not candidate_label_tokens:
        return False
    for other in candidates:
        if other.id == candidate.id or _same_visual_candidate(other, candidate):
            continue
        if other.control_type != "combobox":
            continue
        combo_label_tokens = _field_alternative_label_tokens(other, candidates)
        if combo_label_tokens and candidate_label_tokens & combo_label_tokens:
            return True
    return False


def _explicit_spinner_alternative_mismatch(
    instruction: str,
    candidate: ControlCandidate,
    candidates: list[ControlCandidate],
) -> bool:
    raw_tokens = _tokens_from_text(instruction)
    explicit_spinner = (
        "spinner" in raw_tokens
        or "spinbox" in raw_tokens
        or "stepper" in raw_tokens
        or {"spin", "box"} <= raw_tokens
    )
    if not explicit_spinner:
        return False
    if candidate.control_type == "spinner":
        return False
    if candidate.control_type not in LABELLED_FIELD_CONTROL_TYPES:
        return False
    candidate_label_tokens = _field_alternative_label_tokens(candidate, candidates)
    if not candidate_label_tokens:
        return False
    for other in candidates:
        if other.id == candidate.id or _same_visual_candidate(other, candidate):
            continue
        if other.control_type != "spinner":
            continue
        spinner_label_tokens = _field_alternative_label_tokens(other, candidates)
        if spinner_label_tokens and candidate_label_tokens & spinner_label_tokens:
            return True
    return False


def _explicit_slider_alternative_mismatch(
    instruction: str,
    candidate: ControlCandidate,
    candidates: list[ControlCandidate],
) -> bool:
    raw_tokens = _tokens_from_text(instruction)
    if "slider" not in raw_tokens:
        return False
    if candidate.control_type == "slider":
        return False
    candidate_label_tokens = _field_alternative_label_tokens(candidate, candidates)
    if not candidate_label_tokens:
        return False
    for other in candidates:
        if other.id == candidate.id or _same_visual_candidate(other, candidate):
            continue
        if other.control_type != "slider":
            continue
        slider_label_tokens = _field_alternative_label_tokens(other, candidates)
        if slider_label_tokens and candidate_label_tokens & slider_label_tokens:
            return True
    return False


def _explicit_pane_alternative_mismatch(
    instruction: str,
    candidate: ControlCandidate,
    candidates: list[ControlCandidate],
) -> bool:
    raw_tokens = _tokens_from_text(instruction)
    if not (raw_tokens & {"pane", "panel"}):
        return False
    if candidate.control_type == "pane":
        return False
    candidate_label_tokens = _field_alternative_label_tokens(candidate, candidates)
    if not candidate_label_tokens:
        return False
    for other in candidates:
        if other.id == candidate.id or _same_visual_candidate(other, candidate):
            continue
        if other.control_type != "pane":
            continue
        pane_label_tokens = _field_alternative_label_tokens(other, candidates)
        if pane_label_tokens and candidate_label_tokens & pane_label_tokens:
            return True
    return False


def _explicit_item_alternative_mismatch(
    instruction: str,
    candidate: ControlCandidate,
    candidates: list[ControlCandidate],
) -> bool:
    raw_tokens = _tokens_from_text(instruction)
    if not (raw_tokens & {"item", "items"}):
        return False
    item_control_types = ROW_CONTEXT_CONTROL_TYPES | {"menuitem", "tabitem"}
    if candidate.control_type in item_control_types:
        return False
    if candidate.control_type not in (SURFACE_CONTEXT_CONTROL_TYPES | NON_ACTIONABLE_CONTROL_TYPES):
        return False
    candidate_label_tokens = _field_alternative_label_tokens(candidate, candidates)
    if not candidate_label_tokens:
        return False
    for other in candidates:
        if other.id == candidate.id or _same_visual_candidate(other, candidate):
            continue
        if other.control_type not in item_control_types:
            continue
        item_label_tokens = _field_alternative_label_tokens(other, candidates)
        if item_label_tokens and candidate_label_tokens & item_label_tokens:
            return True
    return False


def _explicit_generic_item_control_type_mismatch(
    instruction: str,
    candidate: ControlCandidate,
) -> bool:
    raw_tokens = _tokens_from_text(instruction)
    if not (raw_tokens & {"item", "items"}):
        return False
    if _literal_stopword_name_request_tokens(instruction) & {"item", "items"}:
        return False
    if raw_tokens & STATE_ACTION_WORDS and _exact_visible_label_matches_request(instruction, candidate):
        return False
    if raw_tokens & {"for", "from", "in", "inside", "on", "within", "with"}:
        return False
    action_words = (
        set().union(*EXCLUSIVE_ACTION_FAMILIES)
        | ADD_ACTION_WORDS
        | CONFIRM_CANCEL_ACTION_WORDS
        | TASKBAR_PIN_ACTION_WORDS
        | BROWSER_BOOKMARK_ACTION_WORDS
        | UNNAMED_BOOKMARK_ACTION_WORDS
        | OPEN_VIEW_REQUEST_WORDS
        | {"launch"}
    )
    if raw_tokens & action_words:
        return False
    if raw_tokens & {
        "data",
        "drawer",
        "grid",
        "list",
        "menu",
        "nav",
        "navigation",
        "option",
        "sidebar",
        "tab",
        "table",
        "tree",
    }:
        return False
    item_control_types = ROW_CONTEXT_CONTROL_TYPES | {"menuitem", "tabitem"}
    return candidate.control_type not in item_control_types


def _record_target_alternative_mismatch(
    instruction: str,
    candidate: ControlCandidate,
    candidates: list[ControlCandidate],
) -> bool:
    requested_label = _record_target_requested_label_tokens(instruction)
    if not requested_label:
        return False
    candidate_label = _record_target_candidate_label_tokens(candidate)
    if not (candidate_label & requested_label):
        return False
    candidate_priority = _record_target_control_priority(
        candidate,
        instruction=instruction,
        candidates=candidates,
        requested_label=requested_label,
    )
    return any(
        other.id != candidate.id
        and not _same_visual_candidate(other, candidate)
        and _record_target_control_priority(
            other,
            instruction=instruction,
            candidates=candidates,
            requested_label=requested_label,
        )
        < candidate_priority
        and bool(_record_target_candidate_label_tokens(other) & requested_label)
        for other in candidates
    )


def _record_target_candidate_matches_request(
    instruction: str,
    candidate: ControlCandidate,
) -> bool:
    if candidate.control_type not in ROW_CONTEXT_CONTROL_TYPES:
        return False
    requested_label = _record_target_requested_label_tokens(instruction)
    if not requested_label:
        return False
    candidate_label = _record_target_candidate_label_tokens(candidate)
    return bool(candidate_label & requested_label)


def _record_target_requested_label_tokens(instruction: str) -> set[str]:
    words = _literal_word_sequence(instruction)
    for index, word in enumerate(words):
        if word not in RECORD_TARGET_WORDS:
            continue
        if any(
            next_word in RECORD_TARGET_TRAILING_CONTEXT_WORDS
            for next_word in words[index + 1 : index + 3]
        ):
            continue
        cursor = index - 1
        label_words: list[str] = []
        while cursor >= 0:
            current = words[cursor]
            if current in RECORD_TARGET_LABEL_BOUNDARY_WORDS:
                break
            label_words.append(current)
            cursor -= 1
        if label_words:
            return _object_token_variants(set(label_words))
    return set()


def _record_target_candidate_label_tokens(candidate: ControlCandidate) -> set[str]:
    return _object_token_variants(
        _candidate_visible_text_tokens(candidate)
        | _tokens_from_text(candidate.text)
        | _tokens_from_text(candidate.automation_id)
    ) - RECORD_TARGET_WORDS


def _record_target_control_priority(
    candidate: ControlCandidate,
    *,
    instruction: str = "",
    candidates: list[ControlCandidate] | None = None,
    requested_label: set[str] | None = None,
) -> int:
    if instruction and _direct_surface_container_candidate_matches_request(
        instruction,
        candidate,
        candidates or [],
    ):
        return 0
    if candidate.control_type == "dataitem":
        return 0
    if candidate.control_type == "listitem":
        if _record_target_listitem_is_content_card(
            candidate,
            candidates or [],
            requested_label or set(),
        ):
            return 1
        return 2
    if candidate.control_type == "treeitem":
        return 1
    return 3


def _record_target_listitem_is_content_card(
    candidate: ControlCandidate,
    candidates: list[ControlCandidate],
    requested_label: set[str],
) -> bool:
    if candidate.control_type != "listitem" or not requested_label:
        return False
    candidate_label = _record_target_candidate_label_tokens(candidate)
    if not (candidate_label & requested_label):
        return False
    x, _y, width, height = candidate.rect
    area = max(0, width) * max(0, height)
    if area <= 0:
        return False
    for other in candidates:
        if other.id == candidate.id or _same_visual_candidate(other, candidate):
            continue
        if other.control_type != "listitem":
            continue
        other_label = _record_target_candidate_label_tokens(other)
        if not (other_label & requested_label):
            continue
        other_x, _other_y, other_width, other_height = other.rect
        other_area = max(0, other_width) * max(0, other_height)
        if other_area <= 0:
            continue
        is_larger_card = area >= other_area * 1.8 and (
            width >= other_width * 1.35
            or height >= other_height * 1.35
            or x >= other_x + max(24, other_width // 2)
        )
        if is_larger_card:
            return True
    return False


def _access_permission_action_mismatch(
    instruction: str,
    candidate: ControlCandidate,
    candidates: list[ControlCandidate],
) -> bool:
    instruction_tokens = _tokens_from_text(instruction)
    requested_actions = instruction_tokens & ACCESS_PERMISSION_ACTION_WORDS
    if not requested_actions or "access" not in instruction_tokens:
        return False
    candidate_tokens = _tokens_from_text(candidate.descriptor)
    if "access" not in candidate_tokens:
        return False
    if candidate_tokens & requested_actions:
        return False
    if _browser_extension_access_action_match(instruction, instruction_tokens, candidate):
        return False
    return any(
        other.id != candidate.id
        and not _same_visual_candidate(other, candidate)
        and "access" in _tokens_from_text(other.descriptor)
        and bool(_tokens_from_text(other.descriptor) & requested_actions)
        for other in candidates
    )


def _cell_target_request_mismatch(instruction: str, candidate: ControlCandidate) -> bool:
    if candidate.control_type not in CELL_CONTROL_TYPES:
        return False
    raw_tokens = _tokens_from_text(instruction)
    return not bool(raw_tokens & {"cell", "datagridcell", "gridcell"})


def _field_alternative_label_tokens(
    candidate: ControlCandidate,
    candidates: list[ControlCandidate],
) -> set[str]:
    tokens = (
        _candidate_visible_text_tokens(candidate)
        | _tokens_from_text(candidate.automation_id)
        | _nearby_field_label_tokens(candidate, candidates)
    )
    return _object_token_variants(tokens) - {
        "box",
        "combo",
        "combobox",
        "dropdown",
        "edit",
        "field",
        "fields",
        "input",
        "inputs",
        "item",
        "menuitem",
        "option",
        "radio",
        "radiobutton",
        "spinner",
        "treeitem",
    }


def _subtype_alternative_label_tokens(
    candidate: ControlCandidate,
    candidates: list[ControlCandidate],
) -> set[str]:
    return _field_alternative_label_tokens(candidate, candidates) - {
        "button",
        "buttons",
        "cell",
        "cells",
        "checkbox",
        "data",
        "datagridcell",
        "grid",
        "gridcell",
        "split",
        "splitbutton",
        "table",
    }


def _same_label_candidate_has_type(
    candidate: ControlCandidate,
    candidates: list[ControlCandidate],
    control_types: frozenset[str] | set[str],
) -> bool:
    candidate_tokens = _subtype_alternative_label_tokens(candidate, candidates)
    if not candidate_tokens:
        return False
    for other in candidates:
        if other.id == candidate.id or _same_visual_candidate(other, candidate):
            continue
        if other.control_type not in control_types:
            continue
        other_tokens = _subtype_alternative_label_tokens(other, candidates)
        if other_tokens and candidate_tokens & other_tokens:
            return True
    return False


def _same_label_option_control_types(
    candidate: ControlCandidate,
    candidates: list[ControlCandidate],
) -> set[str]:
    candidate_tokens = _subtype_alternative_label_tokens(candidate, candidates)
    if not candidate_tokens:
        return set()
    control_types = {candidate.control_type} if candidate.control_type in OPTION_ROLE_CONTROL_TYPES else set()
    for other in candidates:
        if other.id == candidate.id or _same_visual_candidate(other, candidate):
            continue
        if other.control_type not in OPTION_ROLE_CONTROL_TYPES:
            continue
        other_tokens = _subtype_alternative_label_tokens(other, candidates)
        if other_tokens and candidate_tokens & other_tokens:
            control_types.add(other.control_type)
    return control_types


def _explicit_option_alternative_mismatch(
    instruction: str,
    candidate: ControlCandidate,
    candidates: list[ControlCandidate],
) -> bool:
    raw_tokens = _tokens_from_text(instruction)
    if not (raw_tokens & {"choice", "choices", "option"}):
        return False
    if candidate.control_type != "combobox":
        return False
    candidate_label_tokens = _field_alternative_label_tokens(candidate, candidates)
    if not candidate_label_tokens:
        return False
    for other in candidates:
        if other.id == candidate.id or _same_visual_candidate(other, candidate):
            continue
        if other.control_type not in OPTION_ROLE_CONTROL_TYPES:
            continue
        option_label_tokens = _field_alternative_label_tokens(other, candidates)
        if option_label_tokens and candidate_label_tokens & option_label_tokens:
            return True
    return False


def _explicit_subtype_alternative_mismatch(
    instruction: str,
    candidate: ControlCandidate,
    candidates: list[ControlCandidate],
) -> bool:
    return (
        _explicit_role_control_type_mismatch(instruction, candidate, candidates)
        or _explicit_dropdown_option_role_mismatch(instruction, candidate, candidates)
        or _explicit_plain_button_subtype_alternative_mismatch(instruction, candidate, candidates)
        or _explicit_plain_field_subtype_alternative_mismatch(instruction, candidate, candidates)
        or _explicit_bare_option_role_alternative_mismatch(instruction, candidate, candidates)
        or _explicit_cell_subtype_alternative_mismatch(instruction, candidate, candidates)
    )


def _explicit_role_control_type_mismatch(
    instruction: str,
    candidate: ControlCandidate,
    candidates: list[ControlCandidate],
) -> bool:
    requested_types = _explicit_strict_role_control_types(instruction)
    if not requested_types:
        return False
    if candidate.control_type in requested_types:
        return False
    if _app_local_row_item_matches_exact_text_intent(instruction, candidate, requested_types):
        return False
    if _exact_visible_label_matches_request(instruction, candidate):
        return False
    candidate_tokens = _subtype_alternative_label_tokens(candidate, candidates)
    if _same_label_candidate_has_type(candidate, candidates, requested_types):
        return True
    return bool(candidate_tokens and candidate_tokens & _object_token_variants(_tokens_from_text(instruction)))


def _explicit_strict_role_control_types(instruction: str) -> set[str]:
    raw_tokens = _tokens_from_text(instruction)
    requested: set[str] = set()
    if (
        raw_tokens & {"tab", "tabs", "tabitem"}
        and not _tab_context_tokens(instruction)
        and not raw_tokens & {"bookmark", "close", "dismiss", "favorite", "new", "search", "star"}
    ):
        requested.add("tabitem")
    if raw_tokens & {"checkbox", "toggle"} or {"check", "box"} <= raw_tokens:
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
    return requested


def _explicit_dropdown_option_role_mismatch(
    instruction: str,
    candidate: ControlCandidate,
    candidates: list[ControlCandidate],
) -> bool:
    raw_tokens = _tokens_from_text(instruction)
    dropdown_requested = "dropdown" in raw_tokens or {"drop", "down"} <= raw_tokens
    option_requested = bool(raw_tokens & {"choice", "choices", "option", "options"})
    if not (dropdown_requested and option_requested):
        return False
    candidate_tokens = _subtype_alternative_label_tokens(candidate, candidates)
    if not candidate_tokens:
        return False
    if candidate.control_type in {"button", "combobox", "splitbutton"}:
        return bool(candidate_tokens & _object_token_variants(raw_tokens))
    if candidate.control_type != "menuitem":
        return _same_label_candidate_has_type(candidate, candidates, {"menuitem"})
    return False


def _explicit_plain_button_subtype_alternative_mismatch(
    instruction: str,
    candidate: ControlCandidate,
    candidates: list[ControlCandidate],
) -> bool:
    raw_tokens = _tokens_from_text(instruction)
    if not (raw_tokens & {"button", "buttons"}):
        return False
    if "splitbutton" in raw_tokens or "split" in raw_tokens:
        return False
    if candidate.control_type != "splitbutton":
        return False
    return _same_label_candidate_has_type(candidate, candidates, {"button"})


def _explicit_plain_field_subtype_alternative_mismatch(
    instruction: str,
    candidate: ControlCandidate,
    candidates: list[ControlCandidate],
) -> bool:
    raw_tokens = _tokens_from_text(instruction)
    if not (raw_tokens & {"field", "fields", "input", "inputs"}):
        return False
    if raw_tokens & {"button", "buttons"}:
        return False
    if raw_tokens & {
        "address",
        "calendar",
        "combo",
        "combobox",
        "date",
        "dropdown",
        "find",
        "picker",
        "search",
        "selector",
        "spinbox",
        "spinner",
        "stepper",
        "time",
    }:
        return False
    if {"drop", "down"} <= raw_tokens or {"spin", "box"} <= raw_tokens:
        return False
    if candidate.control_type == "edit":
        return False
    if candidate.control_type not in LABELLED_FIELD_CONTROL_TYPES:
        return False
    return _same_label_candidate_has_type(candidate, candidates, {"edit"})


def _explicit_bare_option_role_alternative_mismatch(
    instruction: str,
    candidate: ControlCandidate,
    candidates: list[ControlCandidate],
) -> bool:
    raw_tokens = _tokens_from_text(instruction)
    if not (raw_tokens & {"choice", "choices", "option", "options"}):
        return False
    if raw_tokens & {
        "checkbox",
        "combo",
        "combobox",
        "dropdown",
        "list",
        "listitem",
        "menu",
        "menuitem",
        "radio",
        "radiobutton",
        "tree",
        "treeitem",
    }:
        return False
    if raw_tokens & (CHECKBOX_ON_ACTION_WORDS | CHECKBOX_OFF_ACTION_WORDS):
        return False
    if {"check", "box"} <= raw_tokens or {"drop", "down"} <= raw_tokens:
        return False
    if candidate.control_type in OPTION_ROLE_CONTROL_TYPES:
        return len(_same_label_option_control_types(candidate, candidates)) > 1
    return _same_label_candidate_has_type(candidate, candidates, OPTION_ROLE_CONTROL_TYPES)


def _explicit_cell_subtype_alternative_mismatch(
    instruction: str,
    candidate: ControlCandidate,
    candidates: list[ControlCandidate],
) -> bool:
    requested_type = _explicit_cell_subtype_request(instruction)
    if requested_type is None:
        return False
    if candidate.control_type == requested_type:
        return False
    if candidate.control_type not in CELL_CONTROL_TYPES:
        return False
    return _same_label_candidate_has_type(candidate, candidates, {requested_type})


def _explicit_cell_subtype_request(instruction: str) -> str | None:
    raw_tokens = _tokens_from_text(instruction)
    cell_requested = bool(raw_tokens & {"cell", "cells"})
    if "datagridcell" in raw_tokens or (cell_requested and {"data", "grid"} <= raw_tokens):
        return "datagridcell"
    if "gridcell" in raw_tokens or (cell_requested and "grid" in raw_tokens):
        return "gridcell"
    if cell_requested:
        return "cell"
    return None


def _explicit_checkbox_like_alternative_mismatch(
    instruction: str,
    candidate: ControlCandidate,
    candidates: list[ControlCandidate],
) -> bool:
    raw_tokens = _tokens_from_text(instruction)
    explicit_checkbox_like = (
        "checkbox" in raw_tokens
        or {"check", "box"} <= raw_tokens
        or bool(raw_tokens & {"switch", "toggle"})
    )
    if not explicit_checkbox_like:
        return False
    if raw_tokens & {"button", "buttons"}:
        return False
    if candidate.control_type == "checkbox":
        return False
    if candidate.control_type not in TIGHT_ACTION_CONTROL_TYPES:
        return False
    candidate_tokens = _checkbox_like_alternative_label_tokens(candidate)
    if not candidate_tokens:
        return False
    for other in candidates:
        if other.id == candidate.id or _same_visual_candidate(other, candidate):
            continue
        if other.control_type != "checkbox":
            continue
        other_tokens = _checkbox_like_alternative_label_tokens(other)
        if other_tokens and candidate_tokens & other_tokens:
            return True
    return False


def _checkbox_like_alternative_label_tokens(candidate: ControlCandidate) -> set[str]:
    tokens = (
        _candidate_visible_text_tokens(candidate)
        | _tokens_from_text(candidate.text)
        | _tokens_from_text(candidate.automation_id)
    )
    return _object_token_variants(tokens) - {
        "box",
        "button",
        "buttons",
        "check",
        "checkbox",
        "switch",
        "toggle",
    }


def _spinner_stepper_button_match(
    instruction: str,
    candidate: ControlCandidate,
    candidates: list[ControlCandidate],
) -> bool:
    if candidate.control_type not in {"button", "splitbutton"}:
        return False
    raw_tokens = _tokens_from_text(instruction)
    if not (raw_tokens & {"spinner", "spinbox", "stepper"}):
        return False
    requested_up = bool(raw_tokens & SPINNER_INCREMENT_WORDS)
    requested_down = bool(raw_tokens & SPINNER_DECREMENT_WORDS)
    if requested_up == requested_down:
        return False
    control_tokens = _tokens_from_text(candidate.descriptor)
    control_up = bool(control_tokens & SPINNER_INCREMENT_WORDS)
    control_down = bool(control_tokens & SPINNER_DECREMENT_WORDS)
    if control_up == control_down:
        return False
    if requested_up != control_up:
        return False
    return _has_adjacent_spinner(candidate, candidates)


def _spinner_stepper_parent_mismatch(
    instruction: str,
    candidate: ControlCandidate,
    candidates: list[ControlCandidate],
) -> bool:
    if candidate.control_type != "spinner":
        return False
    raw_tokens = _tokens_from_text(instruction)
    if not (raw_tokens & {"spinner", "spinbox", "stepper"}):
        return False
    requested_up = bool(raw_tokens & SPINNER_INCREMENT_WORDS)
    requested_down = bool(raw_tokens & SPINNER_DECREMENT_WORDS)
    if requested_up == requested_down:
        return False
    return any(
        other.id != candidate.id
        and _spinner_stepper_button_match(instruction, other, candidates)
        and _is_adjacent_spinner_button(other, candidate)
        for other in candidates
    )


def _has_adjacent_spinner(
    candidate: ControlCandidate,
    candidates: list[ControlCandidate],
) -> bool:
    for context in candidates:
        if context.id == candidate.id or context.control_type != "spinner":
            continue
        if _is_adjacent_spinner_button(candidate, context):
            return True
    return False


def _is_adjacent_spinner_button(
    button: ControlCandidate,
    spinner: ControlCandidate,
) -> bool:
    if _contains_rect(_expand_rect(spinner.rect, 6), button.rect):
        return True
    return _intersects(_expand_rect(spinner.rect, 8), button.rect)


def _has_combobox_dropdown_arrow_button(
    combobox: ControlCandidate,
    candidates: list[ControlCandidate],
) -> bool:
    return any(
        candidate.id != combobox.id
        and candidate.control_type in {"button", "splitbutton"}
        and (
            not _tokens_from_text(candidate.descriptor)
            or bool(_tokens_from_text(candidate.descriptor) & COMBOBOX_DROPDOWN_ARROW_CONTROL_WORDS)
        )
        and _has_adjacent_combobox(candidate, [combobox])
        for candidate in candidates
    )


def _has_adjacent_combobox(
    candidate: ControlCandidate,
    candidates: list[ControlCandidate],
) -> bool:
    for context in candidates:
        if context.id == candidate.id or context.control_type != "combobox":
            continue
        if _contains_rect(_expand_rect(context.rect, 4), candidate.rect):
            return True
        if _intersects(_expand_rect(context.rect, 6), candidate.rect):
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
    raw_tokens = _tokens_from_text(instruction)
    if not (
        instruction_tokens & BROWSER_EXTENSION_ACCESS_CONTEXT_WORDS
        or raw_tokens & BROWSER_EXTENSION_ACCESS_LABEL_STOPWORDS
    ):
        return False
    if _instruction_names_browser_extension_access_target(instruction, candidate):
        return False
    return True


def _browser_extension_access_action_match(
    instruction: str,
    instruction_tokens: set[str],
    candidate: ControlCandidate,
) -> bool:
    if not _looks_like_browser_extension_access_button(candidate):
        return False
    raw_tokens = _tokens_from_text(instruction)
    if not (
        instruction_tokens & BROWSER_EXTENSION_ACCESS_CONTEXT_WORDS
        or raw_tokens & BROWSER_EXTENSION_ACCESS_LABEL_STOPWORDS
    ):
        return False
    return _instruction_names_browser_extension_access_target(instruction, candidate)


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
    if instruction_tokens & SITE_INFORMATION_REQUEST_WORDS:
        return False
    return bool(instruction_tokens & {"site", "view"})


def _site_information_request_matches_candidate(
    instruction: str,
    candidate: ControlCandidate,
) -> bool:
    raw_tokens = _tokens_from_text(instruction)
    if not ("site" in raw_tokens and raw_tokens & {"info", "information"}):
        return False
    candidate_tokens = _candidate_semantic_tokens(candidate) | _tokens_from_text(candidate.descriptor)
    if (
        candidate_tokens & {"lock", "locked", "padlock", "unlock"}
        and _instruction_requests_local_app_content_surface(instruction, raw_tokens)
    ):
        return False
    if "site_info_lock" in candidate_tokens:
        return True
    return bool("site" in candidate_tokens and candidate_tokens & {"info", "information"})


def _looks_like_site_information_button(candidate: ControlCandidate) -> bool:
    if candidate.control_type not in {"button", "splitbutton"}:
        return False
    window_tokens = _tokens_from_text(candidate.window_title)
    if window_tokens and not (window_tokens & BROWSER_PROFILE_WINDOW_WORDS):
        return False
    return "site_info_lock" in _candidate_visible_text_tokens(candidate)


def _taskbar_surface_context_mismatch(
    instruction: str,
    candidate: ControlCandidate,
) -> bool:
    raw_tokens = _tokens_from_text(instruction)
    is_taskbar = _candidate_is_taskbar_surface(candidate)
    if "taskbar" in raw_tokens:
        return not is_taskbar
    if _instruction_requests_local_app_content_surface(instruction, raw_tokens):
        return is_taskbar
    if raw_tokens & BROWSER_PAGE_TARGET_WORDS:
        return is_taskbar
    return False


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


def _candidate_is_taskbar_surface(candidate: ControlCandidate) -> bool:
    window_tokens = _tokens_from_text(candidate.window_title)
    automation_id = (candidate.automation_id or "").strip().lower()
    return bool(window_tokens & TASKBAR_WINDOW_WORDS) or automation_id in {
        "searchgleambutton",
        "systemtrayicon",
        "widgetsbutton",
    }


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


def _taskbar_search_status_action_mismatch(
    instruction_tokens: set[str],
    candidate: ControlCandidate,
) -> bool:
    if candidate.control_type not in {"button", "splitbutton"}:
        return False
    if (candidate.automation_id or "").strip().lower() != "searchgleambutton":
        return False
    if not (_tokens_from_text(candidate.window_title) & TASKBAR_WINDOW_WORDS):
        return False
    if instruction_tokens & TASKBAR_SEARCH_STATUS_IDENTITY_WORDS:
        return False
    overlap = instruction_tokens & _tokenize_control(candidate.text)
    return bool(overlap & TASKBAR_SEARCH_STATUS_SEPARATOR_ALIAS_WORDS)


def _taskbar_start_button_action_mismatch(
    instruction_tokens: set[str],
    candidate: ControlCandidate,
) -> bool:
    if not _looks_like_taskbar_start_button(candidate):
        return False
    if "start" not in instruction_tokens:
        return False
    return bool(instruction_tokens - TASKBAR_START_BUTTON_ALLOWED_TOKENS)


def _taskbar_start_button_generic_menu_mismatch(
    instruction: str,
    candidate: ControlCandidate,
) -> bool:
    if not _looks_like_taskbar_start_button(candidate):
        return False
    raw_tokens = _tokens_from_text(instruction)
    if "menu" not in raw_tokens:
        return False
    return not (raw_tokens & {"start", "taskbar", "windows"})


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


def _taskbar_hidden_icons_action_mismatch(
    instruction_tokens: set[str],
    candidate: ControlCandidate,
) -> bool:
    if not _looks_like_taskbar_hidden_icons_button(candidate):
        return False
    if instruction_tokens & TASKBAR_HIDDEN_ICONS_REQUEST_WORDS:
        return False
    if {"hidden", "icons"} <= instruction_tokens:
        return False
    return bool(instruction_tokens & {"hidden", "icons"})


def _taskbar_show_desktop_action_mismatch(
    instruction_tokens: set[str],
    candidate: ControlCandidate,
) -> bool:
    if not _looks_like_taskbar_show_desktop_button(candidate):
        return False
    if "desktop" not in instruction_tokens:
        return False
    return not bool(instruction_tokens & TASKBAR_SHOW_DESKTOP_REQUEST_WORDS)


def _looks_like_taskbar_hidden_icons_button(candidate: ControlCandidate) -> bool:
    if candidate.control_type not in {"button", "splitbutton"}:
        return False
    window_tokens = _tokens_from_text(candidate.window_title)
    if not (window_tokens & TASKBAR_WINDOW_WORDS):
        return False
    text_tokens = _tokens_from_text(candidate.text)
    return {"hidden", "icons"} <= text_tokens


def _looks_like_taskbar_show_desktop_button(candidate: ControlCandidate) -> bool:
    if candidate.control_type not in {"button", "splitbutton"}:
        return False
    window_tokens = _tokens_from_text(candidate.window_title)
    if not (window_tokens & TASKBAR_WINDOW_WORDS):
        return False
    text_tokens = _candidate_visible_text_tokens(candidate)
    return "show_desktop" in text_tokens or {"show", "desktop"} <= text_tokens


def _program_manager_desktop_item_action_mismatch(
    instruction_tokens: set[str],
    candidate: ControlCandidate,
) -> bool:
    if not _looks_like_program_manager_desktop_item(candidate):
        return False
    text_tokens = _candidate_visible_text_tokens(candidate)
    raw_text_tokens = _tokens_from_text(candidate.text)
    if "desktop" in text_tokens and "desktop" in instruction_tokens:
        distinctive_tokens = text_tokens - {"desktop"}
        if not instruction_tokens & distinctive_tokens:
            return True
    if _looks_like_program_manager_spotlight_picture_item(candidate):
        if instruction_tokens & PROGRAM_MANAGER_ABOUT_WORDS:
            return not bool(instruction_tokens & PROGRAM_MANAGER_SPOTLIGHT_REQUEST_WORDS)
    if "new" in raw_text_tokens and instruction_tokens & PROGRAM_MANAGER_NEW_ACTION_WORDS:
        distinctive_tokens = (
            text_tokens
            - PROGRAM_MANAGER_NEW_ACTION_WORDS
            - {token for token in text_tokens if token.isdigit()}
        )
        if not instruction_tokens & distinctive_tokens:
            return True
    if instruction_tokens & PROGRAM_MANAGER_GENERIC_NAME_WORDS & text_tokens:
        distinctive_tokens = (
            text_tokens
            - PROGRAM_MANAGER_GENERIC_NAME_WORDS
            - {token for token in text_tokens if token.isdigit()}
        )
        if distinctive_tokens and not instruction_tokens & distinctive_tokens:
            return True
    return False


def _looks_like_program_manager_desktop_item(candidate: ControlCandidate) -> bool:
    if candidate.control_type not in {"listitem", "treeitem"}:
        return False
    return PROGRAM_MANAGER_WINDOW_WORDS <= _tokens_from_text(candidate.window_title)


def _looks_like_program_manager_spotlight_picture_item(
    candidate: ControlCandidate,
) -> bool:
    text_tokens = _tokens_from_text(candidate.text)
    return {"learn", "about", "picture"} <= text_tokens


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


def _pin_state_action_mismatch(
    instruction: str,
    candidate: ControlCandidate,
) -> bool:
    instruction_tokens = _tokens_from_text(instruction)
    requested_unpin = "unpin" in instruction_tokens
    requested_pin = "pin" in instruction_tokens and not requested_unpin
    if requested_unpin == requested_pin:
        return False

    control_tokens = _tokens_from_text(candidate.text) | _tokens_from_text(
        candidate.automation_id
    )
    if requested_unpin:
        return "pin" in control_tokens and not (control_tokens & PIN_STATE_NEUTRAL_WORDS)
    return "unpin" in control_tokens


def _password_visibility_state_action_mismatch(
    instruction: str,
    candidate: ControlCandidate,
) -> bool:
    instruction_tokens = _literal_words_from_text(instruction)
    if not (instruction_tokens & PASSWORD_VISIBILITY_CONTEXT_WORDS):
        return False
    requested_show = bool(instruction_tokens & PASSWORD_VISIBILITY_SHOW_WORDS)
    requested_hide = bool(instruction_tokens & PASSWORD_VISIBILITY_HIDE_WORDS)
    if requested_show == requested_hide:
        return False

    control_tokens = _literal_words_from_text(candidate.descriptor)
    control_show = bool(control_tokens & PASSWORD_VISIBILITY_SHOW_WORDS)
    control_hide = bool(control_tokens & PASSWORD_VISIBILITY_HIDE_WORDS)
    if control_show == control_hide:
        return False
    return requested_show != control_show


def _audio_output_polarity_action_mismatch(
    instruction: str,
    candidate: ControlCandidate,
    candidates: list[ControlCandidate] | None = None,
) -> bool:
    instruction_tokens = _literal_words_from_text(instruction)
    if not (instruction_tokens & AUDIO_OUTPUT_CONTEXT_WORDS):
        return False
    requested_up = bool(instruction_tokens & AUDIO_OUTPUT_UP_WORDS)
    requested_down = bool(instruction_tokens & AUDIO_OUTPUT_DOWN_WORDS)

    control_tokens = _literal_words_from_text(candidate.descriptor)
    if requested_up != requested_down:
        if not (control_tokens & AUDIO_OUTPUT_CONTEXT_WORDS):
            return False
        control_up = bool(control_tokens & AUDIO_OUTPUT_UP_WORDS)
        control_down = bool(control_tokens & AUDIO_OUTPUT_DOWN_WORDS)
        if control_up == control_down:
            return False
        return requested_up != control_up

    requested_mute = bool(instruction_tokens & AUDIO_OUTPUT_MUTE_WORDS)
    requested_unmute = bool(instruction_tokens & AUDIO_OUTPUT_UNMUTE_WORDS)
    if requested_mute == requested_unmute:
        return False
    control_mute = bool(control_tokens & AUDIO_OUTPUT_MUTE_WORDS)
    control_unmute = bool(control_tokens & AUDIO_OUTPUT_UNMUTE_WORDS)
    if control_mute != control_unmute:
        return requested_mute != control_mute
    if not (control_tokens & AUDIO_OUTPUT_CONTEXT_WORDS):
        return False
    return _has_requested_audio_mute_action_candidate(
        instruction_tokens,
        candidate,
        candidates,
    )


def _audio_output_mute_action_match(
    instruction: str,
    candidate: ControlCandidate,
) -> bool:
    instruction_tokens = _literal_words_from_text(instruction)
    if not (instruction_tokens & AUDIO_OUTPUT_CONTEXT_WORDS):
        return False
    requested_mute = bool(instruction_tokens & AUDIO_OUTPUT_MUTE_WORDS)
    requested_unmute = bool(instruction_tokens & AUDIO_OUTPUT_UNMUTE_WORDS)
    if requested_mute == requested_unmute:
        return False
    control_tokens = _literal_words_from_text(candidate.descriptor)
    control_mute = bool(control_tokens & AUDIO_OUTPUT_MUTE_WORDS)
    control_unmute = bool(control_tokens & AUDIO_OUTPUT_UNMUTE_WORDS)
    if control_mute == control_unmute:
        return False
    return requested_mute == control_mute


def _has_requested_audio_mute_action_candidate(
    instruction_tokens: set[str],
    selected: ControlCandidate,
    candidates: list[ControlCandidate] | None,
) -> bool:
    if not candidates:
        return False
    requested_words = (
        AUDIO_OUTPUT_UNMUTE_WORDS
        if instruction_tokens & AUDIO_OUTPUT_UNMUTE_WORDS
        else AUDIO_OUTPUT_MUTE_WORDS
    )
    opposite_words = (
        AUDIO_OUTPUT_MUTE_WORDS
        if requested_words == AUDIO_OUTPUT_UNMUTE_WORDS
        else AUDIO_OUTPUT_UNMUTE_WORDS
    )
    for other in candidates:
        if other.id == selected.id or _same_visual_candidate(other, selected):
            continue
        control_tokens = _literal_words_from_text(other.descriptor)
        if not (control_tokens & requested_words):
            continue
        if control_tokens & opposite_words:
            continue
        return True
    return False


def _history_action_mismatch(instruction: str, candidate: ControlCandidate) -> bool:
    instruction_tokens = _tokens_from_text(instruction)
    requested_undo = bool(instruction_tokens & HISTORY_UNDO_WORDS)
    requested_redo = bool(instruction_tokens & HISTORY_REDO_WORDS)
    if requested_undo == requested_redo:
        return False

    control_tokens = _tokens_from_text(candidate.descriptor)
    control_undo = bool(control_tokens & HISTORY_UNDO_WORDS)
    control_redo = bool(control_tokens & HISTORY_REDO_WORDS)
    if control_undo == control_redo:
        return False
    return requested_undo != control_undo


def _checkbox_state_action_mismatch(
    instruction: str,
    candidate: ControlCandidate,
) -> bool:
    turn_instruction = _turn_on_off_action_kind(instruction)
    turn_control = _turn_on_off_action_kind(candidate.descriptor)
    if turn_instruction and turn_control:
        return turn_instruction != turn_control

    instruction_tokens = _tokens_from_text(instruction)
    requested_on = turn_instruction == "on" or bool(instruction_tokens & CHECKBOX_ON_ACTION_WORDS)
    requested_off = turn_instruction == "off" or bool(instruction_tokens & CHECKBOX_OFF_ACTION_WORDS)
    if requested_on == requested_off:
        return False

    control_tokens = _tokens_from_text(candidate.descriptor)
    control_on = turn_control == "on" or bool(control_tokens & CHECKBOX_ON_ACTION_WORDS)
    control_off = turn_control == "off" or bool(control_tokens & CHECKBOX_OFF_ACTION_WORDS)
    if control_on == control_off:
        return False
    return requested_on != control_on


def _explicit_checkbox_like_control_type_mismatch(
    instruction: str,
    candidate: ControlCandidate,
    control_intents: set[str],
) -> bool:
    if "checkbox" not in control_intents:
        return False
    if candidate.control_type == "checkbox":
        return False
    if candidate.control_type not in TIGHT_ACTION_CONTROL_TYPES:
        return False
    raw_tokens = _tokens_from_text(instruction)
    explicit_checkbox = (
        "checkbox" in raw_tokens
        or {"check", "box"} <= raw_tokens
        or bool(raw_tokens & (CHECKBOX_ON_ACTION_WORDS | CHECKBOX_OFF_ACTION_WORDS))
    )
    if explicit_checkbox and _state_action_button_matches_checkbox_intent(
        instruction,
        candidate,
        control_intents,
    ):
        return False
    explicit_toggle_or_switch = bool(raw_tokens & {"switch", "toggle"}) and not bool(
        raw_tokens & {"button", "buttons"}
    )
    return explicit_checkbox or explicit_toggle_or_switch


def _turn_on_off_action_kind(text: str) -> str:
    has_on = bool(TURN_ON_RE.search(text or ""))
    has_off = bool(TURN_OFF_RE.search(text or ""))
    if has_on == has_off:
        return ""
    return "on" if has_on else "off"


def _navigation_media_transport_action_mismatch(
    instruction: str,
    candidate: ControlCandidate,
) -> bool:
    instruction_tokens = _tokens_from_text(instruction)
    if not (instruction_tokens & NAVIGATION_DIRECTION_WORDS):
        return False
    if instruction_tokens & MEDIA_TRANSPORT_CONTEXT_WORDS:
        return False

    control_tokens = _tokens_from_text(candidate.descriptor)
    if not (control_tokens & NAVIGATION_DIRECTION_WORDS):
        return False
    control_context_tokens = control_tokens | _tokens_from_text(candidate.window_title)
    if instruction_tokens & BROWSER_PAGE_TARGET_WORDS:
        return bool(control_context_tokens & MEDIA_TRANSPORT_CONTEXT_WORDS)
    return bool(control_tokens & MEDIA_TRANSPORT_CONTEXT_WORDS)


def _calendar_exact_alternative_mismatch(
    instruction: str,
    candidate: ControlCandidate,
    candidates: list[ControlCandidate],
) -> bool:
    raw_tokens = _tokens_from_text(instruction)
    if "calendar" not in raw_tokens:
        return False
    candidate_tokens = _tokens_from_text(candidate.text) | _tokens_from_text(
        candidate.automation_id
    )
    if "calendar" in candidate_tokens:
        return False
    if "date" not in candidate_tokens:
        return False
    for other in candidates:
        if other.id == candidate.id or _same_visual_candidate(other, candidate):
            continue
        other_tokens = _tokens_from_text(other.text) | _tokens_from_text(other.automation_id)
        if "calendar" in other_tokens:
            return True
    return False


def _navigation_backup_action_mismatch(
    instruction: str,
    candidate: ControlCandidate,
) -> bool:
    instruction_tokens = _tokens_from_text(instruction)
    if not (instruction_tokens & NAVIGATION_BACK_WORDS):
        return False
    if instruction_tokens & BACKUP_ACTION_WORDS:
        return False
    control_tokens = _tokens_from_text(candidate.descriptor)
    return bool("back" in control_tokens and control_tokens & BACKUP_ACTION_WORDS)


def _explicit_action_context_mismatch(
    instruction: str,
    candidate: ControlCandidate,
) -> bool:
    instruction_tokens = _tokenize_instruction(instruction)
    return (
        _open_destination_context_mismatch(instruction, candidate)
        or _edit_action_context_mismatch(instruction, candidate)
        or _candidate_edit_action_context_mismatch(instruction, candidate.descriptor)
        or _candidate_action_for_open_view_mismatch(instruction, candidate.descriptor)
        or _candidate_action_for_generic_object_request_mismatch(
            instruction,
            candidate.descriptor,
        )
        or _confirm_action_context_mismatch(instruction, candidate)
        or _filter_reset_action_mismatch(instruction, candidate)
        or _cardinal_direction_action_mismatch(instruction, candidate.descriptor)
        or _sort_direction_action_mismatch(instruction, candidate.descriptor)
        or _search_filter_action_mismatch(instruction, candidate.descriptor)
        or _add_remove_action_mismatch(instruction, candidate.descriptor)
        or _generic_file_transfer_alias_mismatch(instruction, candidate.descriptor)
        or _clipboard_copy_context_mismatch(instruction, candidate.descriptor)
        or _file_action_context_mismatch(instruction, candidate.descriptor)
        or _same_action_family_object_mismatch(instruction, candidate.descriptor)
        or _same_action_family_window_context_mismatch(instruction, candidate)
        or _generic_visibility_polarity_action_mismatch(instruction, candidate.descriptor)
        or _check_in_out_action_mismatch(instruction, candidate.descriptor)
        or _reversible_action_polarity_mismatch(instruction, candidate.descriptor)
        or _state_label_action_mismatch(instruction, candidate.descriptor)
        or _search_results_label_action_mismatch(instruction, candidate.descriptor)
        or _new_tab_window_action_mismatch(instruction, candidate.descriptor)
        or _browser_tab_bookmark_action_mismatch(instruction, instruction_tokens, candidate)
        or _browser_tab_contextual_item_mismatch(instruction, candidate)
    )


def _explicit_action_context_mismatch_without_contextual_evidence(
    instruction: str,
    candidate: ControlCandidate,
    candidates: list[ControlCandidate],
) -> bool:
    if not _explicit_action_context_mismatch(instruction, candidate):
        return False
    if _state_label_action_mismatch(instruction, candidate.descriptor):
        return True
    if _same_action_family_object_mismatch(instruction, candidate.text):
        return True
    return not _candidate_satisfies_contextual_duplicate_request(
        instruction,
        candidate,
        candidates,
    )


def _edit_action_context_mismatch(instruction: str, candidate: ControlCandidate) -> bool:
    if candidate.control_type in {"combobox", "edit"}:
        return False
    instruction_tokens = _tokens_from_text(instruction)
    if not (instruction_tokens & EDIT_ACTION_WORDS):
        return False
    control_tokens = _tokens_from_text(candidate.descriptor)
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
    if "site_info_lock" in instruction_raw_tokens and "site_info_lock" in candidate_raw_tokens:
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
    if "site_info_lock" in instruction_raw_tokens and "site_info_lock" in candidate_raw_tokens:
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


def _open_destination_context_mismatch(
    instruction: str,
    candidate: ControlCandidate,
) -> bool:
    instruction_raw_tokens = _tokens_from_text(instruction)
    if not (instruction_raw_tokens & OPEN_VIEW_REQUEST_WORDS):
        return False
    requested_destinations = instruction_raw_tokens & (
        NEUTRAL_ACTION_DESTINATION_WORDS
        - frozenset({"menu", "menus", "page", "pages", "panel", "panels"})
    )
    if not requested_destinations:
        return False
    requested_destination_variants = _object_token_variants(
        _expand_token_aliases(set(requested_destinations))
    )
    candidate_tokens = (
        _candidate_semantic_tokens(candidate)
        | _tokens_from_text(candidate.descriptor)
        | _expand_token_aliases(_tokens_from_text(candidate.descriptor))
    )
    if _object_token_variants(candidate_tokens) & requested_destination_variants:
        return False
    instruction_tokens = _tokenize_instruction(instruction) | instruction_raw_tokens
    instruction_objects = _object_token_variants(
        instruction_tokens
        - requested_destination_variants
        - OPEN_VIEW_REQUEST_WORDS
        - GENERIC_OBJECT_REQUEST_WORDS
        - ACTION_OBJECT_STOPWORDS
        - GENERIC_OBJECT_REQUEST_STOPWORDS
    )
    if not instruction_objects:
        return False
    candidate_variants = _object_token_variants(candidate_tokens)
    if not (candidate_variants & instruction_objects):
        return False
    if requested_destinations & SETTINGS_REQUEST_WORDS and candidate_tokens & {"configure", "manage"}:
        return False
    return True


def _filter_reset_action_mismatch(
    instruction: str,
    candidate: ControlCandidate,
) -> bool:
    instruction_tokens = _tokens_from_text(instruction)
    if not (instruction_tokens & FILTER_RESET_ACTION_WORDS):
        return False
    if not (instruction_tokens & FILTER_RESET_CONTEXT_WORDS):
        return False
    candidate_tokens = _candidate_semantic_tokens(candidate) | _tokens_from_text(
        candidate.descriptor
    )
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


def _cardinal_direction_action_mismatch(instruction: str, candidate_text: str) -> bool:
    instruction_tokens = _tokens_from_text(instruction)
    candidate_tokens = _tokenize_control(candidate_text) | _tokens_from_text(candidate_text)
    for positive_words, negative_words in CARDINAL_DIRECTION_ACTION_PAIRS:
        requested_positive = bool(instruction_tokens & positive_words)
        requested_negative = bool(instruction_tokens & negative_words)
        if requested_positive == requested_negative:
            continue
        candidate_positive = bool(candidate_tokens & positive_words)
        candidate_negative = bool(candidate_tokens & negative_words)
        if candidate_positive == candidate_negative:
            continue
        if requested_positive != candidate_positive:
            return True
    return False


def _cardinal_direction_request_matches_candidate(
    instruction: str,
    candidate: ControlCandidate,
) -> bool:
    if candidate.control_type not in TIGHT_ACTION_CONTROL_TYPES:
        return False
    requested = _tokens_from_text(instruction) & CARDINAL_DIRECTION_ACTION_WORDS
    if not requested:
        return False
    visible = _candidate_visible_text_tokens(candidate)
    return bool(requested & visible & CARDINAL_DIRECTION_ACTION_WORDS)


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


def _clipboard_text_entry_target_request(instruction: str) -> bool:
    instruction_raw_tokens = _tokens_from_text(instruction)
    if "paste" not in instruction_raw_tokens:
        return False
    if not (instruction_raw_tokens & CLIPBOARD_TEXT_ENTRY_TARGET_WORDS):
        return False
    if instruction_raw_tokens & {"selected", "selection"} and not (
        instruction_raw_tokens & {"box", "field", "input", "textbox", "textarea"}
    ):
        return False
    return bool(
        instruction_raw_tokens & {"in", "into", "to"}
        or instruction_raw_tokens & CLIPBOARD_TEXT_ENTRY_TARGET_WORDS
    )


def _confirm_action_context_mismatch(
    instruction: str,
    candidate: ControlCandidate,
) -> bool:
    instruction_kind = _confirm_cancel_action_kind(_tokens_from_text(instruction))
    if not instruction_kind:
        return False

    control_tokens = _tokens_from_text(candidate.descriptor)
    control_kind = _confirm_cancel_action_kind(control_tokens)
    if not control_kind:
        return False
    if instruction_kind != control_kind:
        return True
    return _same_action_object_mismatch(
        _tokenize_instruction(instruction),
        _candidate_semantic_tokens(candidate),
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
    tokens.update(raw_tokens & ACTION_OBJECT_ALIAS_CONTEXT_WORDS)
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


def _object_only_action_context_mismatch(
    instruction: str,
    candidate: ControlCandidate,
) -> bool:
    instruction_raw_tokens = _tokens_from_text(instruction)
    if not instruction_raw_tokens:
        return False
    if (
        _clipboard_text_entry_target_request(instruction)
        and candidate.control_type in {"combobox", "edit", "spinner"}
    ):
        return False
    if candidate.control_type == "checkbox" and (
        instruction_raw_tokens & {"checkbox", "switch", "toggle"}
        or {"check", "box"} <= instruction_raw_tokens
    ):
        return False
    candidate_raw_tokens = _tokens_from_text(candidate.descriptor)
    if not candidate_raw_tokens:
        return False
    for family in EXCLUSIVE_ACTION_FAMILIES:
        if not (instruction_raw_tokens & family):
            continue
        if (
            family & (FILE_PICKER_ACTION_WORDS | FILE_IMPORT_ACTION_WORDS)
            and not (instruction_raw_tokens & ACTION_OBJECT_ALIAS_CONTEXT_WORDS)
        ):
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
        if _browser_extension_access_action_match(instruction, instruction_raw_tokens, candidate):
            return False
        candidate_non_objects = (
            candidate_raw_tokens
            - instruction_objects
            - ACTION_OBJECT_STOPWORDS
            - FILE_IDENTITY_WORDS
        )
        if not candidate_non_objects:
            return True
        if not (candidate_raw_tokens & set().union(*EXCLUSIVE_ACTION_FAMILIES)):
            return True
        if not (candidate_non_objects - NEUTRAL_ACTION_DESTINATION_WORDS):
            return True
    return False


def _action_object_alias_context_requested(instruction: str) -> bool:
    return bool(_tokens_from_text(instruction) & ACTION_OBJECT_ALIAS_CONTEXT_WORDS)


def _exact_visible_action_word_match(
    instruction: str,
    candidate: ControlCandidate,
) -> bool:
    instruction_words = _literal_words_from_text(instruction)
    candidate_words = _literal_words_from_text(candidate.descriptor)
    if not instruction_words or not candidate_words:
        return False
    return any(instruction_words & candidate_words & family for family in EXCLUSIVE_ACTION_FAMILIES)


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
    candidate: ControlCandidate,
) -> bool:
    if not candidate.window_title.strip():
        return False
    instruction_raw_tokens = _tokens_from_text(instruction)
    control_raw_tokens = _tokens_from_text(candidate.descriptor)
    if not instruction_raw_tokens or not control_raw_tokens:
        return False
    context_tokens = _tokenize_control(candidate.window_title)
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
            _tokenize_control(candidate.descriptor),
            family,
            ACTION_OBJECT_STOPWORDS,
        )
        if control_objects:
            continue
        if instruction_objects & context_objects:
            return False
        return True
    return False


def _contained_row_action_context_mismatch(
    instruction: str,
    candidate: ControlCandidate,
    candidates: list[ControlCandidate],
) -> bool:
    if candidate.control_type in ROW_CONTEXT_CONTROL_TYPES:
        return False
    instruction_raw_tokens = _tokens_from_text(instruction)
    control_raw_tokens = _tokens_from_text(candidate.descriptor)
    if not instruction_raw_tokens or not control_raw_tokens:
        return False
    for family in EXCLUSIVE_ACTION_FAMILIES:
        if not (instruction_raw_tokens & family and control_raw_tokens & family):
            continue
        instruction_objects = _object_token_variants(
            _instruction_action_object_tokens(instruction, family)
        )
        if not instruction_objects:
            continue
        control_objects = _object_token_variants(
            _action_object_tokens(
                _tokenize_control(candidate.descriptor),
                family,
                ACTION_OBJECT_STOPWORDS,
            )
        )
        if control_objects:
            continue
        row_objects = _contained_row_context_objects(candidate, candidates)
        if not row_objects:
            continue
        distinctive_objects = instruction_objects - ROW_CONTEXT_GENERIC_WORDS
        if distinctive_objects:
            if distinctive_objects <= row_objects:
                return False
            if _duplicate_row_action_has_context_objects(
                candidate,
                candidates,
                instruction,
                _tokenize_instruction(instruction),
                distinctive_objects,
            ):
                return True
            if distinctive_objects & row_objects:
                return False
            return True
        if instruction_objects <= row_objects:
            return False
        if _duplicate_row_action_has_context_objects(
            candidate,
            candidates,
            instruction,
            _tokenize_instruction(instruction),
            instruction_objects,
        ):
            return True
        if instruction_objects & row_objects:
            return False
        return True
    if _contained_row_action_candidate_matches(
        candidate,
        _tokenize_instruction(instruction),
        instruction,
    ):
        instruction_objects = _instruction_row_context_objects(
            instruction,
            candidate,
            candidates,
        )
        if not instruction_objects:
            return False
        row_objects = _contained_row_context_objects(candidate, candidates)
        if not row_objects:
            missing_row_context_requested = instruction_raw_tokens & {
                "entries",
                "entry",
                "item",
                "items",
                "listitem",
                "record",
                "records",
                "result",
                "results",
                "row",
                "rows",
                "treeitem",
            }
            return bool(missing_row_context_requested) and _has_duplicate_tight_action(
                candidate,
                candidates,
            )
        if instruction_objects <= row_objects:
            return False
        if _duplicate_row_action_has_context_objects(
            candidate,
            candidates,
            instruction,
            _tokenize_instruction(instruction),
            instruction_objects,
        ):
            return True
        if instruction_objects & row_objects:
            return False
        return True
    return False


def _duplicate_row_action_has_context_objects(
    candidate: ControlCandidate,
    candidates: list[ControlCandidate],
    instruction: str,
    instruction_tokens: set[str],
    requested_objects: set[str],
) -> bool:
    if not requested_objects:
        return False
    for other in candidates:
        if other.id == candidate.id or _same_visual_candidate(other, candidate):
            continue
        if other.control_type not in TIGHT_ACTION_CONTROL_TYPES:
            continue
        if not _contained_row_action_candidate_matches(other, instruction_tokens, instruction):
            continue
        if requested_objects <= _contained_row_context_objects(other, candidates):
            return True
    return False


def _instruction_row_context_objects(
    instruction: str,
    candidate: ControlCandidate,
    candidates: list[ControlCandidate],
) -> set[str]:
    explicit_objects = _instruction_contained_row_context_objects(instruction, candidate)
    if explicit_objects:
        return explicit_objects
    if not _has_duplicate_contained_row_action(candidate, candidates):
        return set()
    return _instruction_prepositional_row_context_objects(instruction, candidate)


def _instruction_contained_row_context_objects(
    instruction: str,
    candidate: ControlCandidate,
) -> set[str]:
    words = _literal_word_sequence(instruction)
    if not words:
        return set()
    candidate_words = (
        _literal_words_from_text(candidate.descriptor)
        | _candidate_visible_text_tokens(candidate)
    )
    ignored_words = (
        candidate_words
        | CONTEXTUAL_DUPLICATE_STOPWORDS
        | ROW_CONTEXT_GENERIC_WORDS
        | REVERSIBLE_ACTION_POLARITY_WORDS
        | CLEAR_CONTEXT_WORDS
    )
    context_words: set[str] = set()
    boundary_words = frozenset(
        {
            "a",
            "an",
            "by",
            "for",
            "from",
            "in",
            "inside",
            "of",
            "on",
            "the",
            "this",
            "that",
            "to",
            "with",
            "within",
        }
    )
    position_only_words = CONTEXTUAL_DUPLICATE_POSITION_WORDS - frozenset(
        {"1", "2", "3", "4", "5"}
    )
    for index, word in enumerate(words):
        if word not in ROW_ACTION_CONTAINER_WORDS:
            continue
        before: list[str] = []
        cursor = index - 1
        while cursor >= 0:
            token = words[cursor]
            if token in boundary_words:
                break
            if token not in ROW_ACTION_CONTAINER_WORDS:
                before.append(token)
            cursor -= 1
        before.reverse()

        after: list[str] = []
        cursor = index + 1
        if cursor < len(words) and words[cursor] in boundary_words:
            cursor += 1
        while cursor < len(words):
            token = words[cursor]
            if token in boundary_words or token in ROW_ACTION_CONTAINER_WORDS:
                break
            after.append(token)
            cursor += 1

        span_words = before or after
        if not span_words:
            continue
        filtered = {token for token in span_words if token not in ignored_words}
        if not filtered:
            continue
        if filtered <= position_only_words:
            continue
        context_words.update(filtered)
    return _object_token_variants(context_words)


def _instruction_prepositional_row_context_objects(
    instruction: str,
    candidate: ControlCandidate,
) -> set[str]:
    words = _literal_word_sequence(instruction)
    if not words:
        return set()
    candidate_words = (
        _literal_words_from_text(candidate.descriptor)
        | _candidate_visible_text_tokens(candidate)
    )
    ignored_words = (
        candidate_words
        | CONTEXTUAL_DUPLICATE_STOPWORDS
        | ROW_CONTEXT_GENERIC_WORDS
        | REVERSIBLE_ACTION_POLARITY_WORDS
        | CLEAR_CONTEXT_WORDS
    )
    boundary_words = frozenset(
        {
            "a",
            "an",
            "by",
            "for",
            "from",
            "in",
            "inside",
            "of",
            "on",
            "the",
            "this",
            "that",
            "to",
            "with",
            "within",
        }
    )
    context_words: set[str] = set()
    for index, word in enumerate(words):
        if word not in {"for", "from", "in", "inside", "on", "with", "within"}:
            continue
        span_words: list[str] = []
        cursor = index + 1
        while cursor < len(words):
            token = words[cursor]
            if token in boundary_words or token in ROW_ACTION_CONTAINER_WORDS:
                break
            span_words.append(token)
            cursor += 1
        filtered = {token for token in span_words if token not in ignored_words}
        if filtered:
            context_words.update(filtered)
    return _object_token_variants(context_words)


def _has_duplicate_contained_row_action(
    candidate: ControlCandidate,
    candidates: list[ControlCandidate],
) -> bool:
    if candidate.control_type not in TIGHT_ACTION_CONTROL_TYPES:
        return False
    candidate_tokens = _candidate_visible_text_tokens(candidate) or _candidate_automation_tokens(candidate)
    candidate_tokens -= CONTAINED_CONTROL_REQUEST_WORDS
    if not candidate_tokens:
        return False
    if not _contained_row_context_objects(candidate, candidates):
        return False
    for other in candidates:
        if other.id == candidate.id or _same_visual_candidate(other, candidate):
            continue
        if other.control_type not in TIGHT_ACTION_CONTROL_TYPES:
            continue
        other_tokens = _candidate_visible_text_tokens(other) or _candidate_automation_tokens(other)
        other_tokens -= CONTAINED_CONTROL_REQUEST_WORDS
        if not (candidate_tokens & other_tokens):
            continue
        if _contained_row_context_objects(other, candidates):
            return True
    return False


def _has_duplicate_tight_action(
    candidate: ControlCandidate,
    candidates: list[ControlCandidate],
) -> bool:
    if candidate.control_type not in TIGHT_ACTION_CONTROL_TYPES:
        return False
    duplicate_key = _contextual_duplicate_key(candidate)
    if duplicate_key:
        return any(
            other.id != candidate.id
            and not _same_visual_and_context_candidate(other, candidate)
            and other.control_type == candidate.control_type
            and _contextual_duplicate_key(other) == duplicate_key
            for other in candidates
        )
    candidate_tokens = _candidate_visible_text_tokens(candidate) or _candidate_automation_tokens(candidate)
    candidate_tokens -= CONTAINED_CONTROL_REQUEST_WORDS
    if not candidate_tokens:
        return False
    for other in candidates:
        if other.id == candidate.id or _same_visual_candidate(other, candidate):
            continue
        if other.control_type not in TIGHT_ACTION_CONTROL_TYPES:
            continue
        other_tokens = _candidate_visible_text_tokens(other) or _candidate_automation_tokens(other)
        other_tokens -= CONTAINED_CONTROL_REQUEST_WORDS
        if candidate_tokens & other_tokens:
            return True
    return False


def _contained_row_context_objects(
    candidate: ControlCandidate,
    candidates: list[ControlCandidate],
) -> set[str]:
    containers = [
        item
        for item in candidates
        if item.id != candidate.id
        and item.control_type in ROW_CONTEXT_CONTROL_TYPES
        and item.descriptor
        and _row_action_context_rect_matches(item, candidate)
    ]
    containers.sort(
        key=lambda item: (
            0 if _contains_rect(_expand_rect(item.rect, 2), candidate.rect) else 1,
            item.rect[2] * item.rect[3],
        )
    )
    for container in containers:
        container_tokens = (
            _candidate_semantic_tokens(container)
            | _tokens_from_text(container.descriptor)
        )
        container_tokens.update(
            _contained_row_line_label_tokens(candidate, candidates, container)
        )
        objects = _object_token_variants(
            {
                token
                for token in container_tokens - ROW_CONTEXT_OBJECT_STOPWORDS
                if token and token not in {"click", "press", "tap"}
            }
        )
        if objects:
            return objects
    return set()


def _contained_row_line_label_tokens(
    candidate: ControlCandidate,
    candidates: list[ControlCandidate],
    container: ControlCandidate,
) -> set[str]:
    tokens: set[str] = set()
    for label in candidates:
        if label.id == candidate.id or _same_visual_candidate(label, candidate):
            continue
        if label.id == container.id or _same_visual_candidate(label, container):
            continue
        if label.control_type in CLICKABLE_CONTROL_TYPES and not _clickable_context_label_candidate(
            label,
        ):
            continue
        if label.control_type not in NEARBY_ROW_LABEL_CONTROL_TYPES and not (
            label.control_type in CLICKABLE_CONTROL_TYPES
            and _clickable_context_label_candidate(label)
        ):
            continue
        if not _row_line_label_rect_matches(label, candidate, container):
            continue
        tokens.update(_candidate_semantic_tokens(label))
        tokens.update(_tokens_from_text(label.descriptor))
        tokens.update(_surface_context_type_tokens(label.control_type))
    return tokens


def _row_line_label_rect_matches(
    label: ControlCandidate,
    action: ControlCandidate,
    row: ControlCandidate,
) -> bool:
    if action.control_type not in TIGHT_ACTION_CONTROL_TYPES:
        return False
    if row.control_type not in ROW_CONTEXT_CONTROL_TYPES:
        return False
    if not _contains_rect(_expand_rect(row.rect, 2), action.rect):
        return False
    if not _contains_rect(_expand_rect(row.rect, 2), label.rect):
        return False
    _label_x, label_y, label_width, label_height = label.rect
    _action_x, action_y, action_width, action_height = action.rect
    if min(label_width, label_height, action_width, action_height) <= 0:
        return False
    label_center_y = label_y + label_height / 2
    action_center_y = action_y + action_height / 2
    if abs(label_center_y - action_center_y) > max(8.0, min(label_height, action_height) * 0.75):
        return False
    vertical_overlap = min(label_y + label_height, action_y + action_height) - max(
        label_y,
        action_y,
    )
    return vertical_overlap >= min(label_height, action_height) * 0.35


def _same_containing_row_line_label_rect_matches(
    label: ControlCandidate,
    action: ControlCandidate,
    candidates: list[ControlCandidate],
) -> bool:
    return any(
        row.id != label.id
        and row.id != action.id
        and row.control_type in ROW_CONTEXT_CONTROL_TYPES
        and _row_line_label_rect_matches(label, action, row)
        for row in candidates
    )


def _implicit_container_context_duplicate_mismatch(
    instruction: str,
    candidate: ControlCandidate,
    candidates: list[ControlCandidate],
) -> bool:
    if candidate.control_type not in TIGHT_ACTION_CONTROL_TYPES:
        return False
    duplicate_key = _contextual_duplicate_key(candidate)
    if not duplicate_key:
        return False
    requested_context = _implicit_container_context_request_tokens(instruction, candidate)
    if not requested_context:
        return False
    duplicates = [
        item
        for item in candidates
        if item.control_type == candidate.control_type
        and _contextual_duplicate_key(item) == duplicate_key
        and not (item.id != candidate.id and _same_visual_and_context_candidate(item, candidate))
    ]
    if len(duplicates) < 2:
        return False
    candidate_evidence = _implicit_container_context_evidence_tokens(candidate, candidates)
    if _contextual_duplicate_request_matches_evidence(requested_context, candidate_evidence):
        return False
    return any(
        other.id != candidate.id
        and _contextual_duplicate_request_matches_evidence(
            requested_context,
            _implicit_container_context_evidence_tokens(other, candidates),
        )
        for other in duplicates
    )


def _implicit_container_context_request_tokens(
    instruction: str,
    candidate: ControlCandidate,
) -> set[str]:
    raw_tokens = _tokens_from_text(instruction)
    instruction_tokens = _tokenize_instruction(instruction)
    candidate_tokens = _candidate_semantic_tokens(candidate)
    matched_action_tokens = (raw_tokens | instruction_tokens) & candidate_tokens
    if not matched_action_tokens:
        return set()
    request_tokens = _object_token_variants(
        (raw_tokens | instruction_tokens)
        - candidate_tokens
        - CONTEXTUAL_DUPLICATE_STOPWORDS
        - ROW_CONTEXT_OBJECT_STOPWORDS
    )
    for family in EXCLUSIVE_ACTION_FAMILIES:
        if matched_action_tokens & family:
            request_tokens -= family
    if candidate.control_type in LABELLED_FIELD_CONTROL_TYPES:
        request_tokens -= FIELD_ENTRY_ACTION_WORDS
    return request_tokens


def _implicit_container_context_evidence_tokens(
    candidate: ControlCandidate,
    candidates: list[ControlCandidate],
) -> set[str]:
    tokens: set[str] = set()
    for context in candidates:
        if context.id == candidate.id or _same_visual_candidate(context, candidate):
            continue
        if context.control_type in ROW_CONTEXT_CONTROL_TYPES:
            if not _row_action_context_rect_matches(context, candidate):
                continue
        elif context.control_type in SURFACE_CONTEXT_CONTROL_TYPES:
            if not _contains_rect(_expand_rect(context.rect, 4), candidate.rect):
                continue
        elif context.control_type in NEARBY_ROW_LABEL_CONTROL_TYPES:
            if not (
                _nearby_row_label_rect_matches(context, candidate)
                or _same_containing_row_line_label_rect_matches(context, candidate, candidates)
            ):
                continue
        else:
            continue
        tokens.update(_candidate_semantic_tokens(context))
        tokens.update(_tokens_from_text(context.descriptor))
        tokens.update(_tokens_from_text(context.control_type))
        tokens.update(_surface_context_type_tokens(context.control_type))
        tokens.update(_tokenize_control(context.window_title))
    return _object_token_variants(
        {
            token
            for token in tokens - ROW_CONTEXT_OBJECT_STOPWORDS
            if token and token not in {"click", "press", "tap"}
        }
    )


def _row_action_context_rect_matches(
    row: ControlCandidate,
    action: ControlCandidate,
) -> bool:
    if _contains_rect(_expand_rect(row.rect, 2), action.rect):
        return True
    if row.control_type not in ROW_CONTEXT_CONTROL_TYPES:
        return False
    if action.control_type not in TIGHT_ACTION_CONTROL_TYPES:
        return False
    row_x, row_y, row_width, row_height = row.rect
    action_x, action_y, action_width, action_height = action.rect
    if min(row_width, row_height, action_width, action_height) <= 0:
        return False
    if _tokens_from_text(row.descriptor) & {"card", "cards"} and action_x + action_width <= row_x:
        return False
    _action_center_x, action_center_y = _center(action.rect)
    row_top = row_y - 4
    row_bottom = row_y + row_height + 4
    if not (row_top <= action_center_y <= row_bottom):
        return False
    vertical_overlap = min(row_y + row_height, action_y + action_height) - max(row_y, action_y)
    if vertical_overlap < min(row_height, action_height) * 0.45:
        return False
    row_left = row_x
    row_right = row_x + row_width
    action_left = action_x
    action_right = action_x + action_width
    horizontal_gap = max(row_left - action_right, action_left - row_right, 0)
    max_gap = max(24, min(360, max(row_height * 6, row_width * 0.50)))
    return horizontal_gap <= max_gap


def _unresolved_contextual_duplicate_mismatch(
    instruction: str,
    candidate: ControlCandidate,
    candidates: list[ControlCandidate],
) -> bool:
    requested_context = _contextual_duplicate_request_tokens(
        instruction,
        candidate,
        candidates,
    )
    if not requested_context:
        return False
    duplicate_key = _contextual_duplicate_key(candidate)
    if not duplicate_key:
        return False
    has_duplicate = any(
        other.id != candidate.id
        and not _same_visual_and_context_candidate(other, candidate)
        and other.control_type == candidate.control_type
        and _contextual_duplicate_key(other) == duplicate_key
        for other in candidates
    )
    if not has_duplicate:
        return False
    evidence_tokens = _contextual_duplicate_evidence_tokens(candidate, candidates)
    return not _contextual_duplicate_request_matches_evidence(
        requested_context,
        evidence_tokens,
    )


def _generic_pane_context_duplicate_ambiguous(
    instruction: str,
    candidate: ControlCandidate,
    candidates: list[ControlCandidate],
) -> bool:
    if candidate.control_type not in TIGHT_ACTION_CONTROL_TYPES:
        return False
    raw_tokens = _tokens_from_text(instruction)
    if "pane" not in raw_tokens or "panel" in raw_tokens:
        return False
    if not (raw_tokens & {"in", "inside", "on", "within"}):
        return False
    requested_context = _contextual_duplicate_request_tokens(
        instruction,
        candidate,
        candidates,
    )
    if requested_context != {"pane"}:
        return False
    duplicate_key = _contextual_duplicate_key(candidate)
    if not duplicate_key:
        return False
    if not _candidate_satisfies_contextual_duplicate_request(
        instruction,
        candidate,
        candidates,
    ):
        return False
    return any(
        other.id != candidate.id
        and not _same_visual_and_context_candidate(other, candidate)
        and other.control_type == candidate.control_type
        and _contextual_duplicate_key(other) == duplicate_key
        and _contextual_duplicate_request_matches_evidence(
            requested_context,
            _contextual_duplicate_evidence_tokens(other, candidates),
        )
        for other in candidates
    )


def _generic_control_group_target_ambiguous(
    *,
    instruction: str,
    instruction_tokens: set[str],
    candidate: ControlCandidate,
    candidates: list[ControlCandidate],
    model_rect: tuple[int, int, int, int] | None,
    control_intents: set[str],
) -> bool:
    if model_rect is None or not control_intents:
        return False
    if not _candidate_matches_control_intent(
        candidate,
        control_intents,
        instruction=instruction,
    ):
        return False
    candidate_identity = (
        _candidate_visible_text_tokens(candidate)
        | _tokens_from_text(candidate.text)
        | _tokens_from_text(candidate.automation_id)
    )
    candidate_identity -= CONTAINED_CONTROL_REQUEST_WORDS
    candidate_identity -= control_intents
    if candidate_identity and instruction_tokens & candidate_identity:
        return False
    bounds = _expand_rect(model_rect, 4)
    matches: list[ControlCandidate] = []
    for other in candidates:
        if not _contains_rect(bounds, other.rect):
            continue
        if not _candidate_matches_control_intent(
            other,
            control_intents,
            instruction=instruction,
        ):
            continue
        if other.id != candidate.id and _same_visual_candidate(other, candidate):
            continue
        if other.control_type in NON_ACTIONABLE_CONTROL_TYPES:
            continue
        matches.append(other)
    if len(matches) < 2:
        return False
    distinct_ids = {item.id for item in matches}
    return candidate.id in distinct_ids


def _candidate_satisfies_contextual_duplicate_request(
    instruction: str,
    candidate: ControlCandidate,
    candidates: list[ControlCandidate],
) -> bool:
    requested_context = _contextual_duplicate_request_tokens(
        instruction,
        candidate,
        candidates,
    )
    if not requested_context:
        return False
    evidence_tokens = _contextual_duplicate_evidence_tokens(candidate, candidates)
    return _contextual_duplicate_request_matches_evidence(
        requested_context,
        evidence_tokens,
    )


def _candidate_satisfies_named_contextual_duplicate_request(
    instruction: str,
    candidate: ControlCandidate,
    candidates: list[ControlCandidate],
) -> bool:
    if candidate.control_type not in TIGHT_ACTION_CONTROL_TYPES:
        return False
    duplicate_key = _contextual_duplicate_key(candidate)
    if not duplicate_key:
        return False
    has_duplicate = any(
        other.id != candidate.id
        and not _same_visual_and_context_candidate(other, candidate)
        and other.control_type == candidate.control_type
        and _contextual_duplicate_key(other) == duplicate_key
        for other in candidates
    )
    if not has_duplicate:
        return False
    identity_tokens = _candidate_visible_text_tokens(candidate) or _candidate_automation_tokens(
        candidate
    )
    action_family_tokens = set().union(*EXCLUSIVE_ACTION_FAMILIES)
    identity_tokens -= CONTAINED_CONTROL_REQUEST_WORDS - action_family_tokens
    if not identity_tokens:
        return False
    requested_tokens = _object_token_variants(
        _tokens_from_text(instruction) | _tokenize_instruction(instruction)
    )
    if not (requested_tokens & identity_tokens):
        return False
    return _candidate_satisfies_contextual_duplicate_request(
        instruction,
        candidate,
        candidates,
    )


def _contextual_action_candidate_matches_surface_request(
    instruction: str,
    instruction_tokens: set[str],
    candidate: ControlCandidate,
    candidates: list[ControlCandidate],
) -> bool:
    if candidate.control_type not in TIGHT_ACTION_CONTROL_TYPES:
        return False
    if not _contextual_action_tokens(instruction_tokens, candidate):
        return False
    return _candidate_satisfies_contextual_duplicate_request(
        instruction,
        candidate,
        candidates,
    )


def _contextual_surface_action_alternative_mismatch(
    instruction: str,
    instruction_tokens: set[str],
    candidate: ControlCandidate,
    candidates: list[ControlCandidate],
) -> bool:
    if candidate.control_type not in TIGHT_ACTION_CONTROL_TYPES:
        return False
    raw_tokens = _tokens_from_text(instruction)
    if not (raw_tokens & {"in", "inside", "on", "within"}):
        return False
    if not (_object_token_variants(raw_tokens) & CONTEXTUAL_DUPLICATE_SURFACE_WORDS):
        return False
    requested_context = _contextual_duplicate_request_tokens(
        instruction,
        candidate,
        candidates,
    )
    if not requested_context:
        return False
    if _candidate_satisfies_contextual_duplicate_request(
        instruction,
        candidate,
        candidates,
    ):
        return False
    action_tokens = _contextual_action_tokens(instruction_tokens, candidate)
    if not action_tokens:
        return False
    for other in candidates:
        if other.id == candidate.id or _same_visual_candidate(other, candidate):
            continue
        if other.control_type not in TIGHT_ACTION_CONTROL_TYPES:
            continue
        other_action_tokens = _contextual_action_tokens(instruction_tokens, other)
        if not (action_tokens & other_action_tokens):
            continue
        if _candidate_satisfies_contextual_duplicate_request(
            instruction,
            other,
            candidates,
        ):
            return True
    return False


def _prepositional_context_action_alternative_mismatch(
    instruction: str,
    instruction_tokens: set[str],
    candidate: ControlCandidate,
    candidates: list[ControlCandidate],
) -> bool:
    if candidate.control_type not in CLICKABLE_CONTROL_TYPES:
        return False
    target_tokens, context_tokens = _prepositional_context_action_tokens(instruction)
    if not target_tokens or not context_tokens:
        return False
    candidate_tokens = _candidate_semantic_tokens(candidate) | _tokens_from_text(
        candidate.descriptor
    )
    if candidate_tokens & target_tokens:
        return False
    if not (candidate_tokens & context_tokens):
        if _text_evidence_score(instruction_tokens, candidate_tokens) < TARGET_ID_TEXT_FLOOR:
            return False
    for other in candidates:
        if other.id == candidate.id or _same_visual_candidate(other, candidate):
            continue
        if other.control_type not in CLICKABLE_CONTROL_TYPES:
            continue
        other_tokens = _candidate_semantic_tokens(other) | _tokens_from_text(other.descriptor)
        if not (other_tokens & target_tokens):
            continue
        if _prepositional_context_tokens_match_candidate(context_tokens, other, candidates):
            return True
    return False


def _prepositional_context_only_target_alternative_mismatch(
    instruction: str,
    instruction_tokens: set[str],
    candidate: ControlCandidate,
    candidates: list[ControlCandidate],
    control_intents: set[str] | None = None,
) -> bool:
    if candidate.control_type not in CLICKABLE_CONTROL_TYPES:
        return False
    target_tokens, context_tokens = _prepositional_context_action_tokens(instruction)
    if not target_tokens or not context_tokens:
        return False
    candidate_tokens = _candidate_semantic_tokens(candidate) | _tokens_from_text(
        candidate.descriptor
    )
    candidate_variants = _object_token_variants(candidate_tokens)
    if candidate_variants & target_tokens:
        return False
    if not (candidate_variants & context_tokens):
        return False
    non_context_instruction = _object_token_variants(instruction_tokens) - context_tokens
    if candidate_variants & non_context_instruction:
        return False
    for other in candidates:
        if other.id == candidate.id or _same_visual_candidate(other, candidate):
            continue
        if control_intents and not _candidate_matches_control_intent(
            other,
            control_intents,
            instruction=instruction,
        ):
            continue
        other_tokens = _object_token_variants(
            _candidate_semantic_tokens(other) | _tokens_from_text(other.descriptor)
        )
        if other_tokens & target_tokens:
            return True
    return False


def _delimited_context_only_target_alternative_mismatch(
    instruction: str,
    candidate: ControlCandidate,
    candidates: list[ControlCandidate],
    control_intents: set[str] | None = None,
) -> bool:
    if candidate.control_type not in CLICKABLE_CONTROL_TYPES:
        return False
    target_tokens, context_tokens = _delimited_context_action_tokens(instruction)
    if not target_tokens or not context_tokens:
        return False
    candidate_variants = _object_token_variants(
        _candidate_semantic_tokens(candidate) | _tokens_from_text(candidate.descriptor)
    )
    if candidate_variants & target_tokens:
        return False
    if not (candidate_variants & context_tokens):
        return False
    for other in candidates:
        if other.id == candidate.id or _same_visual_candidate(other, candidate):
            continue
        if control_intents is not None and not _candidate_matches_control_intent(
            other,
            control_intents,
            instruction=instruction,
        ):
            continue
        other_tokens = _object_token_variants(
            _candidate_semantic_tokens(other) | _tokens_from_text(other.descriptor)
        )
        if other_tokens & target_tokens:
            return True
    return False


def _delimited_context_target_match(
    instruction: str,
    candidate: ControlCandidate,
    candidates: list[ControlCandidate],
) -> bool:
    target_tokens, context_tokens = _delimited_context_action_tokens(instruction)
    if not target_tokens:
        return False
    candidate_tokens = _object_token_variants(
        _candidate_semantic_tokens(candidate) | _tokens_from_text(candidate.descriptor)
    )
    if not (candidate_tokens & target_tokens):
        return False
    if context_tokens:
        return _prepositional_context_tokens_match_candidate(
            context_tokens,
            candidate,
            candidates,
        )
    return True


def _delimited_context_action_tokens(instruction: str) -> tuple[set[str], set[str]]:
    parts = re.split(
        r"\s*[:\u2013\u2014]\s*|\s+-\s+|(?<=[A-Za-z0-9])-(?=[A-Z][A-Za-z0-9]*\s)",
        instruction or "",
        maxsplit=1,
    )
    if len(parts) != 2:
        return set(), set()
    target_words = _literal_word_sequence(parts[0])
    context_words = _literal_word_sequence(parts[1])
    while len(target_words) > 1 and target_words[0] in {
        "activate",
        "choose",
        "click",
        "enter",
        "fill",
        "focus",
        "hit",
        "input",
        "open",
        "press",
        "select",
        "tap",
        "type",
        "use",
    }:
        target_words.pop(0)
    while target_words and target_words[0] in {"a", "an", "the", "this", "that"}:
        target_words.pop(0)
    while context_words and context_words[0] in {"a", "an", "the", "this", "that"}:
        context_words.pop(0)
    target_tokens = _object_token_variants(set(target_words)) - (
        CONTAINED_CONTROL_REQUEST_WORDS
        | CONTEXTUAL_DUPLICATE_CONTAINER_WORDS
        | CONTEXTUAL_DUPLICATE_STOPWORDS
    )
    context_tokens = _object_token_variants(set(context_words)) - {
        "a",
        "an",
        "the",
        "this",
        "that",
    }
    context_tokens -= CONTAINED_CONTROL_REQUEST_WORDS
    return target_tokens, context_tokens


def _prepositional_context_action_tokens(instruction: str) -> tuple[set[str], set[str]]:
    words = _literal_word_sequence(instruction)
    if not words:
        return set(), set()
    for index, word in enumerate(words):
        if word not in {"for", "in", "inside", "on", "within"}:
            continue
        target_words = list(words[:index])
        context_words = list(words[index + 1 :])
        while context_words and context_words[0] in {"a", "an", "the", "this", "that"}:
            context_words.pop(0)
        if not context_words:
            continue
        while len(target_words) > 1 and target_words[0] in {
            "activate",
            "choose",
            "click",
            "enter",
            "fill",
            "focus",
            "hit",
            "input",
            "open",
            "press",
            "select",
            "tap",
            "type",
            "use",
        }:
            target_words.pop(0)
        while target_words and target_words[0] in {"a", "an", "the", "this", "that"}:
            target_words.pop(0)
        target_tokens = _object_token_variants(set(target_words)) - (
            CONTAINED_CONTROL_REQUEST_WORDS
            | CONTEXTUAL_DUPLICATE_CONTAINER_WORDS
            | CONTEXTUAL_DUPLICATE_STOPWORDS
        )
        context_tokens = _object_token_variants(set(context_words)) - {
            "a",
            "an",
            "the",
            "this",
            "that",
        }
        context_tokens -= CONTAINED_CONTROL_REQUEST_WORDS
        if target_tokens and context_tokens:
            return target_tokens, context_tokens
    return set(), set()


def _prepositional_context_tokens_match_candidate(
    context_tokens: set[str],
    candidate: ControlCandidate,
    candidates: list[ControlCandidate],
) -> bool:
    evidence_tokens = _contextual_duplicate_evidence_tokens(candidate, candidates)
    if _contextual_duplicate_request_matches_evidence(context_tokens, evidence_tokens):
        return True
    return bool(context_tokens <= evidence_tokens)


def _explicit_transient_surface_alternative_mismatch(
    instruction: str,
    candidate: ControlCandidate,
    candidates: list[ControlCandidate],
) -> bool:
    raw_tokens = _tokens_from_text(instruction)
    if not (raw_tokens & UNNAMED_FOREGROUND_TRANSIENT_SURFACE_WORDS):
        return False
    if candidate.control_type not in TIGHT_ACTION_CONTROL_TYPES:
        return False
    candidate_action_tokens = (
        raw_tokens | _tokenize_instruction(instruction)
    ) & _candidate_semantic_tokens(candidate)
    if not candidate_action_tokens:
        return False
    duplicate_key = _contextual_duplicate_key(candidate)
    if not duplicate_key:
        return False
    if _candidate_has_foreground_unnamed_transient_surface_evidence(candidate, candidates):
        return False
    return any(
        other.id != candidate.id
        and not _same_visual_and_context_candidate(other, candidate)
        and other.control_type == candidate.control_type
        and _contextual_duplicate_key(other) == duplicate_key
        and _candidate_has_foreground_unnamed_transient_surface_evidence(other, candidates)
        for other in candidates
    )


def _contextual_action_tokens(
    instruction_tokens: set[str],
    candidate: ControlCandidate,
) -> set[str]:
    if not instruction_tokens:
        return set()
    return instruction_tokens & _candidate_semantic_tokens(candidate)


def _contextual_duplicate_request_matches_evidence(
    requested_context: set[str],
    evidence_tokens: set[str],
) -> bool:
    requested_positions = requested_context & CONTEXTUAL_DUPLICATE_POSITION_WORDS
    if requested_positions and not (requested_positions <= evidence_tokens):
        return False
    requested_surfaces = _contextual_surface_token_variants(requested_context)
    if requested_surfaces and not requested_positions:
        if not _contextual_surface_tokens_match(requested_context, evidence_tokens):
            return False
    required_identity = requested_context - requested_positions - CONTEXTUAL_DUPLICATE_SURFACE_WORDS
    required_identity -= CONTEXTUAL_DUPLICATE_GENERIC_CONTEXT_WORDS
    if required_identity:
        return required_identity <= evidence_tokens
    if requested_surfaces:
        return _contextual_surface_tokens_match(requested_context, evidence_tokens)
    return bool(requested_context & evidence_tokens)


def _contextual_surface_token_variants(tokens: set[str]) -> set[str]:
    return _object_token_variants(tokens) & CONTEXTUAL_DUPLICATE_SURFACE_WORDS


def _contextual_surface_tokens_match(
    requested_tokens: set[str],
    evidence_tokens: set[str],
) -> bool:
    requested = _contextual_surface_token_variants(requested_tokens)
    evidence = _contextual_surface_token_variants(evidence_tokens)
    if requested & evidence:
        return True
    if requested & {"dialog", "modal"} and evidence & {"dialog", "modal"}:
        return True
    return False


def _candidate_pool_has_contextual_surface_evidence(
    surface_tokens: set[str],
    candidates: list[ControlCandidate] | None,
) -> bool:
    if not surface_tokens or not candidates:
        return False
    for item in candidates:
        evidence_tokens = (
            _candidate_semantic_tokens(item)
            | _tokens_from_text(item.descriptor)
            | _tokens_from_text(item.control_type)
            | _surface_context_type_tokens(item.control_type)
        )
        if _contextual_surface_tokens_match(surface_tokens, evidence_tokens):
            return True
    return False


def _contextual_duplicate_request_tokens(
    instruction: str,
    candidate: ControlCandidate,
    candidates: list[ControlCandidate] | None = None,
) -> set[str]:
    raw_tokens = _tokens_from_text(instruction)
    raw_token_variants = _object_token_variants(raw_tokens)
    instruction_tokens = _tokenize_instruction(instruction)
    candidate_tokens = _candidate_semantic_tokens(candidate)
    surface_tokens = raw_token_variants & CONTEXTUAL_DUPLICATE_SURFACE_WORDS
    matched_action_tokens = (instruction_tokens | raw_tokens) & candidate_tokens
    named_page_context = _named_page_context_request_tokens(instruction)
    if named_page_context and matched_action_tokens:
        return named_page_context
    spatial_context = _spatial_contextual_duplicate_request_tokens(instruction)
    if spatial_context and matched_action_tokens:
        return spatial_context
    variant_only_surfaces = surface_tokens - (raw_tokens & CONTEXTUAL_DUPLICATE_SURFACE_WORDS)
    if variant_only_surfaces and not _candidate_pool_has_contextual_surface_evidence(
        variant_only_surfaces,
        candidates,
    ):
        surface_tokens -= variant_only_surfaces
    if not (raw_token_variants & CONTEXTUAL_DUPLICATE_CONTAINER_WORDS):
        if _positional_action_request_tokens(instruction):
            return set()
        return _implicit_contextual_duplicate_request_tokens(
            instruction,
            instruction_tokens,
            raw_tokens,
            candidate,
            candidate_tokens,
            candidates,
        )
    request_tokens = _object_token_variants(
        (instruction_tokens | raw_tokens | surface_tokens)
        - candidate_tokens
        - (CONTEXTUAL_DUPLICATE_STOPWORDS - CONTEXTUAL_DUPLICATE_SURFACE_WORDS)
    )
    if matched_action_tokens & CLEAR_CLOSE_WORDS and raw_tokens & {
        "modal",
        "notification",
        "popover",
        "popup",
        "toast",
    }:
        request_tokens -= CLEAR_CLOSE_WORDS
    for family in EXCLUSIVE_ACTION_FAMILIES:
        if matched_action_tokens & family:
            request_tokens -= family
    return request_tokens


def _named_page_context_request_tokens(instruction: str) -> set[str]:
    text = instruction.lower()
    match = re.search(
        r"\b(?:in|inside|on|within)\s+(?:the\s+)?([a-z0-9][a-z0-9 _/&.-]{0,80}?)\s+"
        r"(?:page|pages|route|routes|webpage|webpages)\b",
        text,
    )
    if not match:
        return set()
    name_tokens = _tokens_from_text(match.group(1))
    name_tokens -= ACTION_OBJECT_STOPWORDS
    name_tokens -= BROWSER_PAGE_TARGET_WORDS
    name_tokens -= {"the"}
    return _object_token_variants(name_tokens)


def _implicit_contextual_duplicate_request_tokens(
    instruction: str,
    instruction_tokens: set[str],
    raw_tokens: set[str],
    candidate: ControlCandidate,
    candidate_tokens: set[str],
    candidates: list[ControlCandidate] | None,
) -> set[str]:
    if not candidates:
        return set()
    duplicate_key = _contextual_duplicate_key(candidate)
    if not duplicate_key:
        return set()
    matched_action_tokens = (instruction_tokens | raw_tokens) & candidate_tokens
    if not matched_action_tokens:
        return set()
    request_tokens = _object_token_variants(
        (instruction_tokens | raw_tokens)
        - candidate_tokens
        - CONTEXTUAL_DUPLICATE_STOPWORDS
    )
    spatial_context = _spatial_contextual_duplicate_request_tokens(instruction)
    if spatial_context:
        request_tokens = (
            request_tokens - {"beside", "near", "next", "to"}
        ) | spatial_context
    for family in EXCLUSIVE_ACTION_FAMILIES:
        if matched_action_tokens & family:
            request_tokens -= family
    if candidate.control_type in LABELLED_FIELD_CONTROL_TYPES:
        request_tokens -= FIELD_ENTRY_ACTION_WORDS
    if not request_tokens:
        return set()
    positional_request = bool(request_tokens & CONTEXTUAL_DUPLICATE_POSITION_WORDS)
    for other in candidates:
        if other.id != candidate.id and _same_visual_and_context_candidate(other, candidate):
            continue
        if other.control_type != candidate.control_type:
            continue
        if _contextual_duplicate_key(other) != duplicate_key:
            continue
        if positional_request or other.control_type in LABELLED_FIELD_CONTROL_TYPES:
            evidence_tokens = _contextual_duplicate_evidence_tokens(other, candidates)
        else:
            evidence_tokens = _object_token_variants(
                _contextual_duplicate_aligned_header_tokens(other, candidates)
                | _contextual_duplicate_nearby_label_tokens(other, candidates)
            )
        if _contextual_duplicate_request_matches_evidence(
            request_tokens,
            evidence_tokens,
        ):
            return request_tokens
    return set()


def _spatial_contextual_duplicate_request_tokens(instruction: str) -> set[str]:
    matches = re.findall(
        r"\b(?:beside|near|next\s+to)\s+(?:the\s+)?"
        r"([a-z0-9][a-z0-9 _/&.-]{0,80}?)"
        r"(?:\s+(?:card|entry|item|listitem|record|result|row|treeitem))?"
        r"(?=\s*(?:[.!?,;:]|$))",
        instruction.lower(),
    )
    tokens: set[str] = set()
    for match in matches:
        tokens.update(
            _tokens_from_text(match)
            - ACTION_OBJECT_STOPWORDS
            - ROW_ACTION_CONTAINER_WORDS
            - {"the"}
        )
    return _object_token_variants(tokens)


def _contextual_duplicate_key(candidate: ControlCandidate) -> str:
    visible = _candidate_text_key(candidate.text)
    if visible:
        return f"{candidate.control_type}:text:{visible}"
    automation = _candidate_text_key(candidate.automation_id)
    if automation:
        return f"{candidate.control_type}:automation:{automation}"
    return ""


def _contextual_duplicate_evidence_tokens(
    candidate: ControlCandidate,
    candidates: list[ControlCandidate],
) -> set[str]:
    tokens = set(_candidate_semantic_tokens(candidate))
    if _looks_like_browser_toolbar_button(candidate):
        tokens.update({"browser", "chrome", "toolbar"})
    tokens.update(_surface_context_type_tokens(candidate.control_type))
    tokens.update(_distinct_window_title_tokens(candidate, candidates))
    tokens.update(_contextual_duplicate_position_tokens(candidate, candidates))
    tokens.update(_contextual_duplicate_aligned_header_tokens(candidate, candidates))
    tokens.update(_contextual_duplicate_nearby_label_tokens(candidate, candidates))
    tokens.update(_nearby_field_context_label_tokens(candidate, candidates))
    if _candidate_has_foreground_unnamed_transient_surface_evidence(candidate, candidates):
        tokens.update(_expand_token_aliases(set(UNNAMED_FOREGROUND_TRANSIENT_SURFACE_WORDS)))
    if _candidate_has_rank_modal_evidence(candidate, candidates):
        tokens.add("modal")
    if _candidate_has_foreground_rank_evidence(candidate, candidates):
        tokens.update(FOREGROUND_CONTEXT_WORDS)
    for context in candidates:
        if context.id == candidate.id or _same_visual_candidate(context, candidate):
            continue
        if not context.descriptor:
            continue
        if context.control_type in CLICKABLE_CONTROL_TYPES and not _clickable_context_label_candidate(
            context,
        ):
            continue
        if not (
            _contains_rect(_expand_rect(context.rect, 4), candidate.rect)
            or _row_action_context_rect_matches(context, candidate)
            or _nearby_row_label_rect_matches(context, candidate)
            or _nearby_context_label_rect_matches(context, candidate)
            or _same_containing_row_line_label_rect_matches(context, candidate, candidates)
        ):
            continue
        tokens.update(_candidate_semantic_tokens(context))
        tokens.update(_tokens_from_text(context.descriptor))
        tokens.update(_tokens_from_text(context.control_type))
        tokens.update(_surface_context_type_tokens(context.control_type))
        if context.control_type == "menu":
            tokens.add("context")
        if context.control_type in ROW_CONTEXT_CONTROL_TYPES:
            tokens.add("card")
    return _object_token_variants(tokens)


def _distinct_window_title_tokens(
    candidate: ControlCandidate,
    candidates: list[ControlCandidate],
) -> set[str]:
    title_tokens = _tokenize_control(candidate.window_title)
    if not title_tokens:
        return set()
    duplicate_key = _contextual_duplicate_key(candidate)
    if not duplicate_key:
        return title_tokens
    peer_tokens: set[str] = set()
    for other in candidates:
        if other.id == candidate.id:
            continue
        if other.control_type != candidate.control_type:
            continue
        if _contextual_duplicate_key(other) != duplicate_key:
            continue
        peer_tokens.update(_tokenize_control(other.window_title))
    return title_tokens - peer_tokens


def _candidate_has_rank_modal_evidence(
    candidate: ControlCandidate,
    candidates: list[ControlCandidate],
) -> bool:
    if _has_explicit_modal_surface_candidate(candidates):
        return False
    return any(candidate.window_rank > other.window_rank for other in candidates)


def _candidate_has_foreground_unnamed_transient_surface_evidence(
    candidate: ControlCandidate,
    candidates: list[ControlCandidate],
) -> bool:
    if candidate.control_type not in TIGHT_ACTION_CONTROL_TYPES:
        return False
    ranks = {item.window_rank for item in candidates}
    if len(ranks) <= 1 or candidate.window_rank != min(ranks):
        return False
    duplicate_key = _contextual_duplicate_key(candidate)
    if not duplicate_key:
        return False
    has_duplicate_elsewhere = any(
        other.id != candidate.id
        and not _same_visual_candidate(other, candidate)
        and other.control_type == candidate.control_type
        and _contextual_duplicate_key(other) == duplicate_key
        for other in candidates
    )
    if not has_duplicate_elsewhere:
        return False
    for surface in candidates:
        if surface.id == candidate.id or _same_visual_candidate(surface, candidate):
            continue
        if surface.control_type != "window":
            continue
        if surface.window_rank != candidate.window_rank:
            continue
        if not _contains_rect(_expand_rect(surface.rect, 4), candidate.rect):
            continue
        identity_tokens = (
            _candidate_semantic_tokens(surface)
            | _tokens_from_text(surface.descriptor)
            | _tokenize_control(surface.window_title)
        )
        allowed_identity = UNNAMED_FOREGROUND_TRANSIENT_SURFACE_WORDS | {
            "unnamed",
            "untitled",
            "window",
        }
        if identity_tokens - allowed_identity:
            continue
        return True
    return False


def _candidate_has_foreground_rank_evidence(
    candidate: ControlCandidate,
    candidates: list[ControlCandidate],
) -> bool:
    ranks = {item.window_rank for item in candidates}
    if len(ranks) <= 1:
        return False
    return candidate.window_rank == min(ranks)


def _has_explicit_modal_surface_candidate(candidates: list[ControlCandidate]) -> bool:
    for candidate in candidates:
        tokens = (
            _candidate_semantic_tokens(candidate)
            | _tokens_from_text(candidate.descriptor)
            | _tokens_from_text(candidate.control_type)
            | _surface_context_type_tokens(candidate.control_type)
            | _tokenize_control(candidate.window_title)
        )
        if tokens & {"dialog", "modal"}:
            return True
    return False


def _surface_context_type_tokens(control_type: str) -> set[str]:
    tokens = set(_tokens_from_text(control_type))
    tokens.update(SURFACE_CONTEXT_TYPE_WORDS.get(control_type, frozenset()))
    return tokens


def _contextual_duplicate_aligned_header_tokens(
    candidate: ControlCandidate,
    candidates: list[ControlCandidate],
) -> set[str]:
    cx, _cy = _center(candidate.rect)
    tokens: set[str] = set()
    for header in candidates:
        if header.id == candidate.id or header.control_type != "headeritem":
            continue
        if header.rect[1] > candidate.rect[1]:
            continue
        header_left, _header_top, header_width, _header_height = header.rect
        if not (header_left - 2 <= cx <= header_left + header_width + 2):
            continue
        tokens.update(_candidate_semantic_tokens(header))
        tokens.update(_tokens_from_text(header.descriptor))
        tokens.update(_surface_context_type_tokens(header.control_type))
    return tokens


def _contextual_duplicate_nearby_label_tokens(
    candidate: ControlCandidate,
    candidates: list[ControlCandidate],
) -> set[str]:
    if candidate.control_type not in TIGHT_ACTION_CONTROL_TYPES:
        return set()
    tokens: set[str] = set()
    for label in candidates:
        if label.id == candidate.id:
            continue
        if label.control_type in CLICKABLE_CONTROL_TYPES and not _clickable_context_label_candidate(
            label,
        ):
            continue
        if label.control_type not in NEARBY_ROW_LABEL_CONTROL_TYPES and not (
            label.control_type in CLICKABLE_CONTROL_TYPES
            and _nearby_context_label_rect_matches(label, candidate)
        ):
            continue
        if not (
            _nearby_row_label_rect_matches(label, candidate)
            or _nearby_context_label_rect_matches(label, candidate)
            or _same_containing_row_line_label_rect_matches(label, candidate, candidates)
        ):
            continue
        tokens.update(_candidate_semantic_tokens(label))
        tokens.update(_tokens_from_text(label.descriptor))
    return tokens


def _clickable_context_label_candidate(candidate: ControlCandidate) -> bool:
    tokens = _candidate_semantic_tokens(candidate) | _tokens_from_text(candidate.descriptor)
    action_tokens = (
        set().union(*EXCLUSIVE_ACTION_FAMILIES)
        | REVERSIBLE_ACTION_POLARITY_WORDS
        | OPEN_VIEW_REQUEST_WORDS
        | CONFIRM_CANCEL_ACTION_WORDS
        | ADD_ACTION_WORDS
        | REMOVE_ACTION_WORDS
        | PAY_ACTION_WORDS
        | TASKBAR_PIN_ACTION_WORDS
        | SEARCH_ACTION_WORDS
        | BROWSER_SIGN_IN_ACTION_WORDS
        | BROWSER_SIGN_OUT_ACTION_WORDS
    )
    return not bool(tokens & action_tokens)


def _nearby_row_label_rect_matches(
    label: ControlCandidate,
    action: ControlCandidate,
) -> bool:
    if action.control_type not in TIGHT_ACTION_CONTROL_TYPES:
        return False
    label_x, label_y, label_width, label_height = label.rect
    action_x, action_y, action_width, action_height = action.rect
    if min(label_width, label_height, action_width, action_height) <= 0:
        return False
    label_right = label_x + label_width
    action_right = action_x + action_width
    label_center_y = label_y + label_height / 2
    action_center_y = action_y + action_height / 2
    if abs(label_center_y - action_center_y) > max(8.0, min(label_height, action_height) * 0.55):
        return False
    vertical_overlap = min(label_y + label_height, action_y + action_height) - max(
        label_y,
        action_y,
    )
    if vertical_overlap < min(label_height, action_height) * 0.45:
        return False
    horizontal_gap = max(action_x - label_right, label_x - action_right, 0)
    max_gap = max(48, min(220, max(label_height, action_height) * 6))
    return horizontal_gap <= max_gap


def _nearby_context_label_rect_matches(
    label: ControlCandidate,
    action: ControlCandidate,
) -> bool:
    if action.control_type not in TIGHT_ACTION_CONTROL_TYPES:
        return False
    if _same_visual_candidate(label, action):
        return False
    if _nearby_row_label_rect_matches(label, action):
        return True
    label_x, label_y, label_width, label_height = label.rect
    action_x, action_y, action_width, action_height = action.rect
    if min(label_width, label_height, action_width, action_height) <= 0:
        return False
    label_bottom = label_y + label_height
    if label_bottom > action_y:
        return False
    vertical_gap = action_y - label_bottom
    if vertical_gap > max(72, action_height * 2.5):
        return False
    label_right = label_x + label_width
    action_right = action_x + action_width
    horizontal_overlap = min(label_right, action_right) - max(label_x, action_x)
    min_width = min(label_width, action_width)
    left_aligned = abs(label_x - action_x) <= max(16, min_width * 0.30)
    center_aligned = abs((label_x + label_right) / 2 - (action_x + action_right) / 2) <= max(
        24,
        min_width * 0.45,
    )
    return horizontal_overlap >= min_width * 0.30 or left_aligned or center_aligned


def _contextual_duplicate_position_tokens(
    candidate: ControlCandidate,
    candidates: list[ControlCandidate],
) -> set[str]:
    duplicate_key = _contextual_duplicate_key(candidate)
    if not duplicate_key:
        return set()
    duplicates = [
        item
        for item in candidates
        if not _same_visual_candidate(item, candidate)
        and item.control_type == candidate.control_type
        and _contextual_duplicate_key(item) == duplicate_key
    ]
    duplicates.append(candidate)
    distinct: dict[str, ControlCandidate] = {}
    for item in duplicates:
        distinct.setdefault(item.id, item)
    ordered = sorted(
        distinct.values(),
        key=lambda item: (
            item.window_rank,
            item.rect[1],
            item.rect[0],
            item.depth,
            item.id,
        ),
    )
    if len(ordered) < 2:
        return set()
    tokens: set[str] = set()
    try:
        index = next(index for index, item in enumerate(ordered) if item.id == candidate.id)
    except StopIteration:
        return set()
    if index < len(CONTEXTUAL_DUPLICATE_ORDINAL_WORDS):
        tokens.update(CONTEXTUAL_DUPLICATE_ORDINAL_WORDS[index])
    if index == len(ordered) - 1:
        tokens.add("last")

    centers = [(item, _center(item.rect)) for item in ordered]
    xs = [center[0] for _item, center in centers]
    ys = [center[1] for _item, center in centers]
    cx, cy = _center(candidate.rect)
    if max(xs) - min(xs) >= max(8, candidate.rect[2] // 4):
        if cx == min(xs):
            tokens.add("left")
        if cx == max(xs):
            tokens.add("right")
    if max(ys) - min(ys) >= max(8, candidate.rect[3] // 4):
        if cy == min(ys):
            tokens.update({"top", "upper"})
        if cy == max(ys):
            tokens.update({"bottom", "lower"})
    return tokens


def _positional_action_duplicate_mismatch(
    instruction: str,
    instruction_tokens: set[str],
    candidate: ControlCandidate,
    candidates: list[ControlCandidate],
) -> bool:
    requested_positions = _positional_action_request_tokens_for_candidate(
        instruction,
        instruction_tokens,
        candidate,
        candidates,
    )
    if not requested_positions:
        return False
    request_tokens = instruction_tokens | _tokens_from_text(instruction)
    if not _positional_action_duplicate_action_tokens(request_tokens, candidate):
        return False
    position_tokens = _positional_action_duplicate_position_tokens(
        candidate,
        candidates,
        request_tokens,
    )
    if not position_tokens:
        return False
    return not (requested_positions <= position_tokens)


def _candidate_satisfies_positional_action_duplicate_request(
    instruction: str,
    instruction_tokens: set[str],
    candidate: ControlCandidate,
    candidates: list[ControlCandidate],
) -> bool:
    requested_positions = _positional_action_request_tokens_for_candidate(
        instruction,
        instruction_tokens,
        candidate,
        candidates,
    )
    if not requested_positions:
        return False
    request_tokens = instruction_tokens | _tokens_from_text(instruction)
    position_tokens = _positional_action_duplicate_position_tokens(
        candidate,
        candidates,
        request_tokens,
    )
    if not position_tokens:
        return False
    return requested_positions <= position_tokens


def _positional_action_request_tokens(instruction: str) -> set[str]:
    raw_tokens = _tokens_from_text(instruction)
    if not (raw_tokens & POSITIONAL_DUPLICATE_REQUEST_WORDS):
        return set()
    requested = set(raw_tokens & CONTEXTUAL_DUPLICATE_POSITION_WORDS)
    if raw_tokens & {"arrow", "caret", "chevron"}:
        requested -= {"left", "right"}
    return requested


def _positional_action_request_tokens_for_candidate(
    instruction: str,
    instruction_tokens: set[str],
    candidate: ControlCandidate,
    candidates: list[ControlCandidate],
) -> set[str]:
    requested = _positional_action_request_tokens(instruction)
    if requested:
        return requested
    raw_tokens = _tokens_from_text(instruction)
    requested = set(raw_tokens & CONTEXTUAL_DUPLICATE_POSITION_WORDS)
    if raw_tokens & {"arrow", "caret", "chevron"}:
        requested -= {"left", "right"}
    if not requested:
        return set()
    request_tokens = instruction_tokens | raw_tokens
    if not _positional_action_duplicate_action_tokens(request_tokens, candidate):
        return set()
    if len(_positional_action_duplicate_candidates(candidate, candidates, request_tokens)) < 2:
        return set()
    return requested


def _positional_action_duplicate_position_tokens(
    candidate: ControlCandidate,
    candidates: list[ControlCandidate],
    instruction_tokens: set[str],
) -> set[str]:
    duplicates = _positional_action_duplicate_candidates(
        candidate,
        candidates,
        instruction_tokens,
    )
    if len(duplicates) < 2:
        return set()
    ordered = sorted(
        duplicates,
        key=lambda item: (
            item.window_rank,
            item.rect[1],
            item.rect[0],
            item.depth,
            item.id,
        ),
    )
    try:
        index = next(index for index, item in enumerate(ordered) if item.id == candidate.id)
    except StopIteration:
        return set()
    tokens: set[str] = set()
    if index < len(CONTEXTUAL_DUPLICATE_ORDINAL_WORDS):
        tokens.update(CONTEXTUAL_DUPLICATE_ORDINAL_WORDS[index])
    if index == len(ordered) - 1:
        tokens.add("last")

    centers = [(item, _center(item.rect)) for item in ordered]
    xs = [center[0] for _item, center in centers]
    ys = [center[1] for _item, center in centers]
    cx, cy = _center(candidate.rect)
    if max(xs) - min(xs) >= max(8, candidate.rect[2] // 4):
        if cx == min(xs):
            tokens.add("left")
        if cx == max(xs):
            tokens.add("right")
    if max(ys) - min(ys) >= max(8, candidate.rect[3] // 4):
        if cy == min(ys):
            tokens.update({"top", "upper"})
        if cy == max(ys):
            tokens.update({"bottom", "lower"})
    return tokens


def _positional_action_duplicate_candidates(
    candidate: ControlCandidate,
    candidates: list[ControlCandidate],
    instruction_tokens: set[str],
) -> list[ControlCandidate]:
    if candidate.control_type not in CLICKABLE_CONTROL_TYPES:
        return []
    action_tokens = _positional_action_duplicate_action_tokens(instruction_tokens, candidate)
    if not action_tokens:
        return []
    duplicates: list[ControlCandidate] = []
    for item in candidates:
        if item.control_type != candidate.control_type:
            continue
        if item.id != candidate.id and _same_visual_candidate(item, candidate):
            continue
        item_action_tokens = _positional_action_duplicate_action_tokens(instruction_tokens, item)
        if action_tokens & item_action_tokens:
            duplicates.append(item)
    distinct: dict[str, ControlCandidate] = {}
    for item in duplicates:
        distinct.setdefault(item.id, item)
    return list(distinct.values())


def _positional_action_duplicate_action_tokens(
    instruction_tokens: set[str],
    candidate: ControlCandidate,
) -> set[str]:
    semantic_overlap = (
        instruction_tokens
        & _candidate_semantic_tokens(candidate)
        - CONTEXTUAL_DUPLICATE_POSITION_WORDS
    )
    if semantic_overlap:
        return semantic_overlap
    return (
        instruction_tokens
        & _positional_duplicate_control_tokens(candidate)
        - CONTEXTUAL_DUPLICATE_POSITION_WORDS
    )


def _positional_duplicate_control_tokens(candidate: ControlCandidate) -> set[str]:
    tokens = set(_tokenize_control(candidate.control_type))
    if candidate.control_type == "button":
        tokens.update({"button", "control", "icon"})
    elif candidate.control_type == "splitbutton":
        tokens.update({"button", "control", "splitbutton"})
    elif candidate.control_type == "edit":
        tokens.update({"box", "edit", "field", "input", "textbox"})
    elif candidate.control_type == "combobox":
        tokens.update({"box", "combo", "combobox", "dropdown", "field", "input"})
    elif candidate.control_type == "spinner":
        tokens.update({"field", "input", "spinner"})
    elif candidate.control_type == "listitem":
        tokens.update({"entry", "item", "listitem", "result", "row"})
    elif candidate.control_type == "dataitem":
        tokens.update({"dataitem", "entry", "grid", "item", "result", "row", "table"})
    elif candidate.control_type == "treeitem":
        tokens.update({"entry", "item", "treeitem", "row"})
    elif candidate.control_type == "radiobutton":
        tokens.update({"option", "radio", "radiobutton"})
    elif candidate.control_type == "checkbox":
        tokens.update({"checkbox", "option"})
    elif candidate.control_type == "hyperlink":
        tokens.update({"hyperlink", "link"})
    elif candidate.control_type == "menuitem":
        tokens.update({"item", "menuitem", "option"})
    elif candidate.control_type == "tabitem":
        tokens.update({"tab", "tabitem"})
    elif candidate.control_type == "headeritem":
        tokens.update({"column", "header", "headeritem", "heading"})
    elif candidate.control_type in CELL_CONTROL_TYPES:
        tokens.update({"cell", "datagridcell", "gridcell"})
    elif candidate.control_type == "slider":
        tokens.update({"slider"})
    return tokens


def _exact_action_word_alternative_mismatch(
    instruction: str,
    candidate: ControlCandidate,
    candidates: list[ControlCandidate],
    control_intents: set[str] | None = None,
) -> bool:
    instruction_words = _literal_words_from_text(instruction)
    if not instruction_words:
        return False
    candidate_semantic = _candidate_semantic_tokens(candidate)
    candidate_words = _literal_words_from_text(candidate.descriptor)
    for family in EXCLUSIVE_ACTION_FAMILIES:
        exact_words = instruction_words & family
        if not exact_words or not (candidate_semantic & family):
            continue
        if candidate_words & exact_words:
            return False
        for other in candidates:
            if other.id == candidate.id or _same_visual_candidate(other, candidate):
                continue
            if control_intents is not None and not _candidate_matches_control_intent(
                other,
                control_intents,
                instruction=instruction,
            ):
                continue
            if not (_candidate_semantic_tokens(other) & family):
                continue
            if _literal_words_from_text(other.descriptor) & exact_words:
                return True
    return False


def _exact_visible_label_alternative_mismatch(
    instruction: str,
    candidate: ControlCandidate,
    candidates: list[ControlCandidate],
    control_intents: set[str] | None = None,
) -> bool:
    if candidate.control_type not in TIGHT_ACTION_CONTROL_TYPES:
        return False
    requested_words = _exact_visible_label_request_words(instruction)
    if not requested_words:
        return False
    candidate_words = _literal_words_from_text(candidate.text)
    if not requested_words or not candidate_words:
        return False
    if candidate_words == requested_words:
        return False
    if not requested_words < candidate_words:
        return False
    for other in candidates:
        if other.id == candidate.id or _same_visual_candidate(other, candidate):
            continue
        if control_intents is not None and not _candidate_matches_control_intent(
            other,
            control_intents,
            instruction=instruction,
        ):
            continue
        if _literal_words_from_text(other.text) == requested_words:
            return True
    return False


def _exact_visible_label_matches_request(
    instruction: str,
    candidate: ControlCandidate,
) -> bool:
    if candidate.control_type not in TIGHT_ACTION_CONTROL_TYPES:
        return False
    requested_words = _exact_visible_label_request_words(instruction)
    if not requested_words:
        return False
    return _literal_words_from_text(candidate.text) == requested_words


def _exact_visible_label_request_words(instruction: str) -> set[str]:
    words = _literal_words_from_text(instruction)
    requested_words = words - (
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
                "the",
                "this",
                "that",
                "to",
                "with",
                "within",
            }
        )
    )
    if requested_words:
        return requested_words
    return _single_word_visible_action_label(instruction)


def _single_word_visible_action_label(instruction: str) -> set[str]:
    words = [
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
    while words and words[0] in {
        "choose",
        "click",
        "focus",
        "hit",
        "press",
        "select",
        "tap",
        "use",
    }:
        words.pop(0)
    if len(words) == 1 and words[0] in OPEN_VIEW_REQUEST_WORDS:
        return {words[0]}
    return set()


def _ambiguous_exact_action_alias_alternative(
    instruction: str,
    candidate: ControlCandidate,
    candidates: list[ControlCandidate],
    control_intents: set[str] | None = None,
) -> bool:
    instruction_words = _literal_words_from_text(instruction)
    if not instruction_words:
        return False
    candidate_words = _literal_words_from_text(candidate.descriptor)
    candidate_semantic = _candidate_semantic_tokens(candidate)
    for family in AMBIGUOUS_EXACT_ACTION_ALIAS_FAMILIES:
        exact_words = instruction_words & family
        if not exact_words or not (candidate_semantic & family):
            continue
        if candidate_words & exact_words:
            return False
        if not (candidate_words & family):
            continue
        for other in candidates:
            if other.id == candidate.id or _same_visual_candidate(other, candidate):
                continue
            if control_intents is not None and not _candidate_matches_control_intent(
                other,
                control_intents,
                instruction=instruction,
            ):
                continue
            if _literal_words_from_text(other.descriptor) & exact_words:
                return True
    return False


def _ambiguous_exact_literal_alias_alternative(
    instruction: str,
    candidate: ControlCandidate,
    candidates: list[ControlCandidate],
    control_intents: set[str] | None = None,
) -> bool:
    instruction_words = _literal_words_from_text(instruction)
    if not instruction_words:
        return False
    candidate_semantic = _candidate_semantic_tokens(candidate)
    candidate_words = _literal_words_from_text(candidate.descriptor)
    for family in EXCLUSIVE_ACTION_FAMILIES + (
        BROWSER_BOOKMARK_ACTION_WORDS,
        ADD_ACTION_WORDS,
        BROWSER_SIGN_IN_ACTION_WORDS,
        BROWSER_SIGN_OUT_ACTION_WORDS,
        CART_ACTION_WORDS,
        CLEAR_CLOSE_WORDS,
        CONFIRM_ACTION_WORDS,
        LOCK_ACTION_WORDS,
        SEARCH_ACTION_WORDS,
    ):
        exact_words = instruction_words & family
        if not exact_words or not (candidate_semantic & family):
            continue
        if candidate_words & exact_words:
            return False
        for other in candidates:
            if other.id == candidate.id or _same_visual_candidate(other, candidate):
                continue
            if control_intents is not None and not _candidate_matches_control_intent(
                other,
                control_intents,
                instruction=instruction,
            ):
                continue
            if not (_candidate_semantic_tokens(other) & family):
                continue
            if _literal_words_from_text(other.descriptor) & exact_words:
                return True
    return False


def _exact_literal_alias_peer_alternative(
    instruction: str,
    candidate: ControlCandidate,
    candidates: list[ControlCandidate],
    control_intents: set[str] | None = None,
) -> bool:
    instruction_words = _literal_words_from_text(instruction)
    candidate_words = _literal_words_from_text(candidate.descriptor)
    if not instruction_words or not candidate_words:
        return False
    if not (instruction_words & candidate_words):
        return False
    for other in candidates:
        if other.id == candidate.id or _same_visual_candidate(other, candidate):
            continue
        if control_intents is not None and not _candidate_matches_control_intent(
            other,
            control_intents,
            instruction=instruction,
        ):
            continue
        if _ambiguous_exact_literal_alias_alternative(
            instruction,
            other,
            candidates,
            control_intents,
        ):
            return True
    return False


def _exact_action_geometry_conflict(
    *,
    ranked: list[tuple[float, ControlCandidate]],
    selected: ControlCandidate,
    selected_score: float,
    confidence_floor: float,
    instruction: str,
    candidates: list[ControlCandidate],
    control_intents: set[str],
    model_rect: tuple[int, int, int, int],
) -> tuple[float, ControlCandidate] | None:
    if selected_score >= confidence_floor + TEXT_MATCH_GAP:
        return None
    selected_geometry = _geometry_agreement(selected.rect, model_rect)
    best_conflict: tuple[float, ControlCandidate] | None = None
    for score, candidate in ranked:
        if _same_visual_candidate(candidate, selected):
            continue
        if not _exact_action_word_alternative_mismatch(
            instruction,
            candidate,
            candidates,
            control_intents,
        ):
            continue
        candidate_geometry = _geometry_agreement(candidate.rect, model_rect)
        if candidate_geometry < TARGET_ID_GEOMETRY_FLOOR:
            continue
        if selected_geometry >= candidate_geometry - TEXT_MATCH_GAP:
            continue
        if best_conflict is None or candidate_geometry > _geometry_agreement(
            best_conflict[1].rect,
            model_rect,
        ):
            best_conflict = (score, candidate)
    return best_conflict


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


def _reversible_action_exact_alternative_mismatch(
    instruction: str,
    candidate: ControlCandidate,
    candidates: list[ControlCandidate],
    control_intents: set[str] | None = None,
) -> bool:
    if not _reversible_action_polarity_mismatch(instruction, candidate.descriptor):
        return False
    instruction_kind = _reversible_action_polarity_kind(_tokens_from_text(instruction))
    if not instruction_kind:
        return False
    family_index, instruction_side = instruction_kind.split(":", 1)
    positive_words, negative_words = REVERSIBLE_ACTION_POLARITY_PAIRS[int(family_index)]
    requested_words = positive_words if instruction_side == "positive" else negative_words
    if _literal_words_from_text(candidate.descriptor) & requested_words:
        return False
    instruction_objects = _object_token_variants(
        _action_object_tokens(
            _tokens_from_text(instruction),
            REVERSIBLE_ACTION_POLARITY_WORDS,
            ACTION_OBJECT_STOPWORDS,
        )
    )
    for other in candidates:
        if other.id == candidate.id or _same_visual_candidate(other, candidate):
            continue
        if control_intents is not None and not _candidate_matches_control_intent(
            other,
            control_intents,
            instruction=instruction,
        ):
            continue
        other_words = _literal_words_from_text(other.descriptor)
        if not (other_words & requested_words):
            continue
        if not instruction_objects:
            return True
        other_objects = _object_token_variants(
            _action_object_tokens(
                _tokens_from_text(other.descriptor),
                REVERSIBLE_ACTION_POLARITY_WORDS,
                ACTION_OBJECT_STOPWORDS,
            )
        )
        if instruction_objects & other_objects:
            return True
    return False


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
    if _same_form_state_label_action_mismatch(instruction, candidate_text):
        return True
    if control_tokens == _exact_visible_label_request_words(instruction):
        return False
    if _state_label_is_target_identity(instruction_tokens, control_tokens):
        return False

    turn_kind = _turn_on_off_action_kind(instruction)
    if turn_kind and control_tokens & (STATE_LABEL_TURN_ON_WORDS | STATE_LABEL_TURN_OFF_WORDS):
        return True

    for action_words, state_words in STATE_LABEL_ACTION_GROUPS:
        if instruction_tokens & action_words and control_tokens & state_words:
            return True
    return False


def _same_form_state_label_action_mismatch(
    instruction: str,
    candidate_text: str,
) -> bool:
    instruction_words = _literal_word_sequence(instruction)
    control_words = _literal_word_sequence(candidate_text)
    if len(instruction_words) < 2 or len(control_words) < 2:
        return False
    requested_action = instruction_words[0]
    if requested_action not in SAME_FORM_STATE_ACTION_WORDS:
        return False
    return control_words[-1] == requested_action and control_words[0] != requested_action


def _state_action_object_only_alternative_mismatch(
    instruction: str,
    candidate: ControlCandidate,
    candidates: list[ControlCandidate],
) -> bool:
    raw_tokens = _tokens_from_text(instruction)
    if _literal_words_from_text(candidate.text) == _exact_visible_label_request_words(instruction):
        return False
    if candidate.control_type == "checkbox" and (
        raw_tokens & {"checkbox", "switch", "toggle"} or {"check", "box"} <= raw_tokens
    ):
        return False
    candidate_tokens = _candidate_semantic_tokens(candidate) | _tokens_from_text(candidate.descriptor)
    turn_kind = _turn_on_off_action_kind(instruction)
    if turn_kind and candidate.control_type == "checkbox":
        if _turn_on_off_action_kind(candidate.descriptor):
            return False
        instruction_objects = _object_token_variants(
            _action_object_tokens(
                raw_tokens,
                frozenset({"off", "on", "turn"}),
                ACTION_OBJECT_STOPWORDS,
            )
        )
        if instruction_objects and _object_token_variants(candidate_tokens) & instruction_objects:
            for other in candidates:
                if other.id == candidate.id or _same_visual_candidate(other, candidate):
                    continue
                if other.control_type not in TIGHT_ACTION_CONTROL_TYPES:
                    continue
                if _turn_on_off_action_kind(other.descriptor) != turn_kind:
                    continue
                other_tokens = _candidate_semantic_tokens(other) | _tokens_from_text(
                    other.descriptor
                )
                other_objects = _object_token_variants(
                    _action_object_tokens(
                        other_tokens,
                        frozenset({"off", "on", "turn"}),
                        ACTION_OBJECT_STOPWORDS,
                    )
                )
                if other_objects & instruction_objects:
                    return True
    for action_words, _state_words in STATE_LABEL_ACTION_GROUPS:
        requested_actions = raw_tokens & action_words
        if not requested_actions:
            continue
        if candidate_tokens & action_words:
            return False
        instruction_objects = _object_token_variants(
            _action_object_tokens(raw_tokens, action_words, ACTION_OBJECT_STOPWORDS)
        )
        if not instruction_objects:
            continue
        candidate_objects = _object_token_variants(candidate_tokens) & instruction_objects
        if not candidate_objects:
            continue
        for other in candidates:
            if other.id == candidate.id or _same_visual_candidate(other, candidate):
                continue
            if other.control_type not in TIGHT_ACTION_CONTROL_TYPES:
                continue
            other_tokens = _candidate_semantic_tokens(other) | _tokens_from_text(other.descriptor)
            if not (other_tokens & requested_actions):
                continue
            other_objects = _object_token_variants(other_tokens) & instruction_objects
            if other_objects:
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
    for action_words, state_words in STATE_LABEL_ACTION_GROUPS:
        if not (instruction_tokens & action_words):
            continue
        if not (instruction_tokens & control_tokens & state_words):
            continue
        identity_stopwords = CONFIRM_OBJECT_STOPWORDS | action_words | state_words
        instruction_objects = _object_token_variants(instruction_tokens - identity_stopwords)
        control_objects = _object_token_variants(control_tokens - identity_stopwords)
        if instruction_objects and control_objects and instruction_objects & control_objects:
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
    if _taskbar_status_settings_request_mismatch(instruction_tokens, text_tokens, candidate):
        return True
    if instruction_tokens & identity_tokens:
        return False
    overlap = instruction_tokens & text_tokens
    return bool(overlap)


def _taskbar_status_settings_request_mismatch(
    instruction_tokens: set[str],
    text_tokens: set[str],
    candidate: ControlCandidate,
) -> bool:
    if not (instruction_tokens & TASKBAR_STATUS_SETTINGS_REQUEST_WORDS):
        return False
    if text_tokens & TASKBAR_STATUS_SETTINGS_REQUEST_WORDS:
        return False
    automation_id = (candidate.automation_id or "").strip().lower()
    window_tokens = _tokens_from_text(candidate.window_title)
    if automation_id != "systemtrayicon" and not (window_tokens & TASKBAR_WINDOW_WORDS):
        return False
    return True


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
    if "clock" in text_tokens or _looks_like_taskbar_clock_status(candidate):
        return TASKBAR_CLOCK_STATUS_IDENTITY_WORDS
    if automation_id == "searchgleambutton":
        return TASKBAR_SEARCH_STATUS_IDENTITY_WORDS
    if text_tokens & TASKBAR_NOTIFICATION_STATUS_IDENTITY_WORDS:
        return TASKBAR_NOTIFICATION_STATUS_IDENTITY_WORDS
    if "onedrive" in text_tokens:
        return TASKBAR_ONEDRIVE_STATUS_IDENTITY_WORDS
    return frozenset()


def _looks_like_taskbar_clock_status(candidate: ControlCandidate) -> bool:
    if candidate.control_type not in {"button", "splitbutton"}:
        return False
    if (candidate.automation_id or "").strip().lower() != "systemtrayicon":
        return False
    if not (_tokens_from_text(candidate.window_title) & TASKBAR_WINDOW_WORDS):
        return False
    text = candidate.text or ""
    return bool(re.search(r"\b\d{1,2}:\d{2}(?::\d{2})?\b", text))


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
        _candidate_semantic_tokens_with_field_label(selected, candidates),
    )
    selected_geometry = (
        _geometry_agreement(selected.rect, model_rect) if model_rect is not None else 0.0
    )
    selected_score = selected_text + 0.30 * selected_geometry
    selected_score += _foreground_rank_bonus(selected, candidates, model_rect=model_rect)
    closest_gap = 1.0
    selected_has_gmail_tab_evidence = _has_explicit_gmail_tab_evidence(selected)
    for candidate in candidates:
        if candidate is selected or candidate.id == selected.id:
            continue
        if _same_visual_candidate(candidate, selected):
            continue
        if not _candidate_matches_control_intent(
            candidate,
            control_intents,
            instruction=instruction,
        ):
            continue
        if _taskbar_start_button_action_mismatch(instruction_tokens, candidate):
            continue
        if _taskbar_start_button_generic_menu_mismatch(instruction, candidate):
            continue
        if _taskbar_app_state_action_mismatch(instruction_tokens, candidate):
            continue
        if _taskbar_surface_context_mismatch(instruction, candidate):
            continue
        if _browser_profile_identity_action_mismatch(instruction_tokens, candidate):
            continue
        if _browser_profile_page_action_mismatch(instruction, candidate):
            continue
        if _browser_chrome_app_context_mismatch(instruction, candidate):
            continue
        if _browser_menu_button_action_mismatch(instruction, candidate):
            continue
        if _browser_navigation_chrome_action_mismatch(instruction, candidate):
            continue
        if _browser_toolbar_chrome_action_mismatch(instruction, candidate):
            continue
        if _browser_address_bar_content_mismatch(
            instruction,
            instruction_tokens,
            candidate,
        ):
            continue
        if _browser_address_bar_alternative_mismatch(
            instruction,
            instruction_tokens,
            candidate,
            candidates,
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
        if _clear_close_action_mismatch(instruction, instruction_tokens, candidate, candidates):
            continue
        if _close_context_action_mismatch(instruction, candidate, candidates):
            continue
        if _close_tab_action_mismatch(instruction, candidate, candidates):
            continue
        if _browser_new_tab_bookmark_action_mismatch(instruction_tokens, candidate):
            continue
        if _browser_new_tab_action_mismatch(instruction, instruction_tokens, candidate):
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
        if _pin_state_action_mismatch(instruction, candidate):
            continue
        if _password_visibility_state_action_mismatch(instruction, candidate):
            continue
        if _audio_output_polarity_action_mismatch(instruction, candidate, candidates):
            continue
        if _history_action_mismatch(instruction, candidate):
            continue
        if _checkbox_state_action_mismatch(instruction, candidate):
            continue
        if _navigation_media_transport_action_mismatch(instruction, candidate):
            continue
        if _navigation_backup_action_mismatch(instruction, candidate):
            continue
        if _unresolved_contextual_duplicate_mismatch(instruction, candidate, candidates):
            continue
        if _positional_action_duplicate_mismatch(
            instruction,
            instruction_tokens,
            candidate,
            candidates,
        ):
            continue
        if _contextual_surface_action_alternative_mismatch(
            instruction,
            instruction_tokens,
            candidate,
            candidates,
        ):
            continue
        if _contained_row_action_context_mismatch(instruction, candidate, candidates):
            continue
        if _explicit_action_context_mismatch_without_contextual_evidence(
            instruction,
            candidate,
            candidates,
        ):
            continue
        if _object_only_action_context_mismatch(instruction, candidate):
            continue
        if _exclusive_action_family_mismatch(instruction, candidate.descriptor):
            continue
        if _mail_tab_account_reference_mismatch(instruction_tokens, candidate):
            continue
        candidate_tokens = _candidate_semantic_tokens_with_field_label(candidate, candidates)
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
        score += _foreground_rank_bonus(candidate, candidates, model_rect=model_rect)
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


def _browser_tab_generic_section_mismatch(
    instruction: str,
    instruction_tokens: set[str],
    candidate: ControlCandidate,
) -> bool:
    if candidate.control_type != "tabitem":
        return False
    if not instruction_tokens or not (instruction_tokens & BROWSER_TAB_GENERIC_SECTION_WORDS):
        return False
    if _instruction_mentions_tab_context(instruction):
        return False
    allowed_context = (
        BROWSER_TAB_GENERIC_SECTION_WORDS
        | BROWSER_APP_IDENTITY_WORDS
        | OPEN_VIEW_REQUEST_WORDS
    )
    return instruction_tokens <= allowed_context


def _browser_tab_contextual_item_mismatch(
    instruction: str,
    candidate: ControlCandidate,
) -> bool:
    if candidate.control_type != "tabitem":
        return False
    raw_tokens = _tokens_from_text(instruction)
    if raw_tokens & BROWSER_TAB_WORDS:
        return False
    if "item" not in raw_tokens:
        return False
    if not (raw_tokens & CONTEXTUAL_NAV_ITEM_CONTAINER_WORDS):
        return False
    window_tokens = _tokens_from_text(candidate.window_title)
    return bool(window_tokens & BROWSER_PROFILE_WINDOW_WORDS)


def _tab_context_candidate_mismatch(
    instruction: str,
    candidate: ControlCandidate,
    candidates: list[ControlCandidate],
) -> bool:
    context_tokens = _tab_context_tokens(instruction)
    if not context_tokens:
        return False
    action_tokens = _tab_context_action_tokens(instruction, context_tokens)
    if not action_tokens:
        return False
    if candidate.control_type == "tabitem":
        return any(
            other.id != candidate.id
            and not _same_visual_candidate(other, candidate)
            and _tab_context_action_candidate_matches(other, action_tokens)
            and _tab_context_candidate_matches_context(other, context_tokens, candidates)
            for other in candidates
        )
    if not _tab_context_action_candidate_matches(candidate, action_tokens):
        return False
    if _tab_context_candidate_matches_context(candidate, context_tokens, candidates):
        return False
    return any(
        other.id != candidate.id
        and not _same_visual_candidate(other, candidate)
        and _tab_context_action_candidate_matches(other, action_tokens)
        and _tab_context_candidate_matches_context(other, context_tokens, candidates)
        for other in candidates
    )


def _tab_context_tokens(instruction: str) -> set[str]:
    text = (instruction or "").lower()
    matches = re.findall(
        r"\b(?:in|inside|on|within)\s+(?:the\s+)?([a-z0-9][a-z0-9\s_.-]{0,60}?)\s+"
        r"(?:tab|tabs|tabitem)\b",
        text,
    )
    tokens: set[str] = set()
    for match in matches:
        tokens.update(_tokens_from_text(match))
    return _object_token_variants(
        tokens
        - OPEN_VIEW_REQUEST_WORDS
        - GENERIC_OBJECT_REQUEST_WORDS
        - CONTAINED_CONTROL_REQUEST_WORDS
        - {"a", "an", "in", "inside", "on", "the", "within"}
    )


def _tab_context_action_tokens(instruction: str, context_tokens: set[str]) -> set[str]:
    raw_tokens = _tokens_from_text(instruction)
    return _object_token_variants(
        (_tokenize_instruction(instruction) | raw_tokens)
        - context_tokens
        - OPEN_VIEW_REQUEST_WORDS
        - GENERIC_OBJECT_REQUEST_WORDS
        - CONTAINED_CONTROL_REQUEST_WORDS
        - {"a", "an", "in", "inside", "on", "the", "within"}
    )


def _tab_context_action_candidate_matches(
    candidate: ControlCandidate,
    action_tokens: set[str],
) -> bool:
    if candidate.control_type == "tabitem":
        return False
    candidate_tokens = _candidate_semantic_tokens(candidate) | _tokens_from_text(candidate.descriptor)
    return bool(action_tokens & _object_token_variants(candidate_tokens))


def _tab_context_candidate_matches_context(
    candidate: ControlCandidate,
    context_tokens: set[str],
    candidates: list[ControlCandidate],
) -> bool:
    evidence_tokens = (
        _tokenize_control(candidate.window_title)
        | _contextual_duplicate_evidence_tokens(candidate, candidates)
    )
    return _contextual_duplicate_request_matches_evidence(
        context_tokens,
        _object_token_variants(evidence_tokens),
    )


def _candidate_satisfies_tab_context_action_request(
    instruction: str,
    candidate: ControlCandidate,
    candidates: list[ControlCandidate],
) -> bool:
    context_tokens = _tab_context_tokens(instruction)
    if not context_tokens:
        return False
    action_tokens = _tab_context_action_tokens(instruction, context_tokens)
    if not action_tokens:
        return False
    return _tab_context_action_candidate_matches(
        candidate,
        action_tokens,
    ) and _tab_context_candidate_matches_context(candidate, context_tokens, candidates)


def _instruction_mentions_tab_context(instruction: str) -> bool:
    return bool(re.search(r"\b(?:tab|tabs|tabitem)\b", (instruction or "").lower()))


def _has_explicit_gmail_tab_evidence(candidate: ControlCandidate) -> bool:
    if candidate.control_type != "tabitem":
        return False
    text = candidate.text or ""
    tokens = _tokens_from_text(text)
    return "recibidos" in tokens or bool(GMAIL_TAB_SERVICE_RE.search(text))


def _foreground_rank_bonus(
    candidate: ControlCandidate,
    candidates: list[ControlCandidate],
    *,
    model_rect: tuple[int, int, int, int] | None = None,
    suppress_for_stronger_geometry: bool = True,
) -> float:
    ranks = {item.window_rank for item in candidates}
    if len(ranks) < 2:
        return 0.0
    if suppress_for_stronger_geometry and model_rect is not None and _same_label_duplicate_has_stronger_geometry(
        candidate,
        candidates,
        model_rect,
    ):
        return 0.0
    return FOREGROUND_RANK_BONUS if candidate.window_rank == min(ranks) else 0.0


def _same_label_duplicate_has_stronger_geometry(
    candidate: ControlCandidate,
    candidates: list[ControlCandidate],
    model_rect: tuple[int, int, int, int],
) -> bool:
    candidate_key = _candidate_semantic_key(candidate)
    if not candidate_key:
        return False
    candidate_geometry = _geometry_agreement(candidate.rect, model_rect)
    candidate_center_inside = _center_inside(candidate.rect, _expand_rect(model_rect, 8))
    for other in candidates:
        if other.id == candidate.id or _same_visual_candidate(other, candidate):
            continue
        if other.window_rank == candidate.window_rank:
            continue
        if other.control_type != candidate.control_type:
            continue
        if _candidate_semantic_key(other) != candidate_key:
            continue
        other_geometry = _geometry_agreement(other.rect, model_rect)
        other_center_inside = _center_inside(other.rect, _expand_rect(model_rect, 8))
        if other_center_inside and not candidate_center_inside:
            return True
        if (
            other_geometry >= TARGET_ID_GEOMETRY_FLOOR
            and other_geometry > candidate_geometry + TEXT_MATCH_GAP
        ):
            return True
    return False


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
        if control_intents and not _candidate_matches_control_intent(
            candidate,
            control_intents,
            instruction=instruction,
        ):
            continue
        if _taskbar_start_button_action_mismatch(instruction_tokens, candidate):
            continue
        if _taskbar_start_button_generic_menu_mismatch(instruction, candidate):
            continue
        if _taskbar_app_state_action_mismatch(instruction_tokens, candidate):
            continue
        if _taskbar_surface_context_mismatch(instruction, candidate):
            continue
        if _browser_profile_identity_action_mismatch(instruction_tokens, candidate):
            continue
        if _browser_profile_page_action_mismatch(instruction, candidate):
            continue
        if _browser_chrome_app_context_mismatch(instruction, candidate):
            continue
        if _browser_menu_button_action_mismatch(instruction, candidate):
            continue
        if _browser_navigation_chrome_action_mismatch(instruction, candidate):
            continue
        if _browser_toolbar_chrome_action_mismatch(instruction, candidate):
            continue
        if _browser_address_bar_content_mismatch(
            instruction,
            instruction_tokens,
            candidate,
        ):
            continue
        if _browser_address_bar_alternative_mismatch(
            instruction,
            instruction_tokens,
            candidate,
            candidates,
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
        if _clear_close_action_mismatch(instruction, instruction_tokens, candidate, candidates):
            continue
        if _close_context_action_mismatch(instruction, candidate, candidates):
            continue
        if _close_tab_action_mismatch(instruction, candidate, candidates):
            continue
        if _browser_new_tab_bookmark_action_mismatch(instruction_tokens, candidate):
            continue
        if _browser_new_tab_action_mismatch(instruction, instruction_tokens, candidate):
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
        if _pin_state_action_mismatch(instruction, candidate):
            continue
        if _password_visibility_state_action_mismatch(instruction, candidate):
            continue
        if _audio_output_polarity_action_mismatch(instruction, candidate, candidates):
            continue
        if _history_action_mismatch(instruction, candidate):
            continue
        if _checkbox_state_action_mismatch(instruction, candidate):
            continue
        if _navigation_media_transport_action_mismatch(instruction, candidate):
            continue
        if _navigation_backup_action_mismatch(instruction, candidate):
            continue
        if _unresolved_contextual_duplicate_mismatch(instruction, candidate, candidates):
            continue
        if _contained_row_action_context_mismatch(instruction, candidate, candidates):
            continue
        if _explicit_action_context_mismatch_without_contextual_evidence(
            instruction,
            candidate,
            candidates,
        ):
            continue
        if _object_only_action_context_mismatch(instruction, candidate):
            continue
        if _exclusive_action_family_mismatch(instruction, candidate.descriptor):
            continue
        if _mail_tab_account_reference_mismatch(instruction_tokens, candidate):
            continue
        score = _text_evidence_score(
            instruction_tokens,
            _candidate_semantic_tokens_with_field_label(candidate, candidates),
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
    if _row_scoped_action_target_matches_context(instruction, selected, candidates):
        return False
    if _candidate_satisfies_named_contextual_duplicate_request(
        instruction,
        selected,
        candidates,
    ):
        return False
    selected_evidence_tokens = _candidate_semantic_tokens_with_field_label(
        selected,
        candidates,
    )
    allow_cross_role_visible_alternative = (
        not _candidate_visible_text_tokens(selected)
        and not (instruction_tokens & selected_evidence_tokens)
        and bool(instruction_tokens - CONTAINED_CONTROL_REQUEST_WORDS)
    )
    for candidate in candidates:
        if candidate.id == selected.id:
            continue
        matches_requested_role = _candidate_matches_control_intent(
            candidate,
            control_intents,
            instruction=instruction,
        )
        if not matches_requested_role and not (
            allow_cross_role_visible_alternative
            and candidate.control_type in CLICKABLE_CONTROL_TYPES
        ):
            continue
        if _taskbar_start_button_action_mismatch(instruction_tokens, candidate):
            continue
        if _taskbar_start_button_generic_menu_mismatch(instruction, candidate):
            continue
        if _taskbar_app_state_action_mismatch(instruction_tokens, candidate):
            continue
        if _taskbar_surface_context_mismatch(instruction, candidate):
            continue
        if _browser_profile_identity_action_mismatch(instruction_tokens, candidate):
            continue
        if _browser_profile_page_action_mismatch(instruction, candidate):
            continue
        if _browser_chrome_app_context_mismatch(instruction, candidate):
            continue
        if _browser_menu_button_action_mismatch(instruction, candidate):
            continue
        if _browser_navigation_chrome_action_mismatch(instruction, candidate):
            continue
        if _browser_toolbar_chrome_action_mismatch(instruction, candidate):
            continue
        if _browser_address_bar_content_mismatch(
            instruction,
            instruction_tokens,
            candidate,
        ):
            continue
        if _browser_address_bar_alternative_mismatch(
            instruction,
            instruction_tokens,
            candidate,
            candidates,
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
        if _clear_close_action_mismatch(instruction, instruction_tokens, candidate, candidates):
            continue
        if _close_context_action_mismatch(instruction, candidate, candidates):
            continue
        if _close_tab_action_mismatch(instruction, candidate, candidates):
            continue
        if _browser_new_tab_bookmark_action_mismatch(instruction_tokens, candidate):
            continue
        if _browser_new_tab_action_mismatch(instruction, instruction_tokens, candidate):
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
        if _pin_state_action_mismatch(instruction, candidate):
            continue
        if _password_visibility_state_action_mismatch(instruction, candidate):
            continue
        if _audio_output_polarity_action_mismatch(instruction, candidate, candidates):
            continue
        if _history_action_mismatch(instruction, candidate):
            continue
        if _checkbox_state_action_mismatch(instruction, candidate):
            continue
        if _navigation_media_transport_action_mismatch(instruction, candidate):
            continue
        if _navigation_backup_action_mismatch(instruction, candidate):
            continue
        if _unresolved_contextual_duplicate_mismatch(instruction, candidate, candidates):
            continue
        if _contained_row_action_context_mismatch(instruction, candidate, candidates):
            continue
        if _explicit_action_context_mismatch_without_contextual_evidence(
            instruction,
            candidate,
            candidates,
        ):
            continue
        if _object_only_action_context_mismatch(instruction, candidate):
            continue
        if _exclusive_action_family_mismatch(instruction, candidate.descriptor):
            continue
        if _mail_tab_account_reference_mismatch(instruction_tokens, candidate):
            continue
        visible_tokens = _candidate_visible_text_tokens(candidate)
        label_tokens = _nearby_field_label_tokens(candidate, candidates)
        alternative_tokens = visible_tokens | label_tokens
        if not alternative_tokens:
            continue
        if _text_evidence_score(instruction_tokens, alternative_tokens) >= TARGET_ID_TEXT_FLOOR:
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
    semantic_tokens = _candidate_semantic_tokens(candidate) | _named_control_candidate_label_tokens(
        candidate,
        candidates,
    )
    exact_visible_label_match = _exact_visible_label_matches_request(instruction, candidate)
    text_score = _text_evidence_score(instruction_tokens, semantic_tokens)
    if candidate.control_type in NON_ACTIONABLE_CONTROL_TYPES:
        return 0.0
    if _cell_target_request_mismatch(instruction, candidate):
        return 0.0
    if _tab_context_candidate_mismatch(instruction, candidate, candidates):
        return 0.0
    if _explicit_text_field_control_type_mismatch(instruction, candidate):
        return 0.0
    if _named_control_label_missing(instruction, candidate, candidates):
        return 0.0
    if _record_target_alternative_mismatch(instruction, candidate, candidates):
        return 0.0
    if _access_permission_action_mismatch(instruction, candidate, candidates):
        return 0.0
    if _explicit_combobox_alternative_mismatch(instruction, candidate, candidates):
        return 0.0
    if _explicit_spinner_alternative_mismatch(instruction, candidate, candidates):
        return 0.0
    if _spinner_stepper_parent_mismatch(instruction, candidate, candidates):
        return 0.0
    if _explicit_slider_alternative_mismatch(instruction, candidate, candidates):
        return 0.0
    if _explicit_pane_alternative_mismatch(instruction, candidate, candidates):
        return 0.0
    if _explicit_surface_container_alternative_mismatch(instruction, candidate, candidates):
        return 0.0
    if _explicit_item_alternative_mismatch(instruction, candidate, candidates):
        return 0.0
    if _explicit_generic_item_control_type_mismatch(instruction, candidate):
        return 0.0
    if _explicit_field_alternative_mismatch(instruction, candidate, candidates):
        return 0.0
    if _explicit_option_alternative_mismatch(instruction, candidate, candidates):
        return 0.0
    if _explicit_checkbox_like_alternative_mismatch(instruction, candidate, candidates):
        return 0.0
    if _explicit_subtype_alternative_mismatch(instruction, candidate, candidates):
        return 0.0
    if _surface_context_contains_tighter_action(
        selected=candidate,
        candidates=candidates,
        instruction=instruction,
        instruction_tokens=instruction_tokens,
        control_intents=control_intents,
    ):
        return 0.0
    if _taskbar_start_button_action_mismatch(instruction_tokens, candidate):
        return min(0.41, 0.45 * iou + 0.30 * proximity)
    if _taskbar_start_button_generic_menu_mismatch(instruction, candidate):
        return min(0.41, 0.45 * iou + 0.30 * proximity)
    if _taskbar_task_view_action_mismatch(instruction, instruction_tokens, candidate):
        return min(0.41, 0.45 * iou + 0.30 * proximity)
    if _taskbar_hidden_icons_action_mismatch(instruction_tokens, candidate):
        return min(0.41, 0.45 * iou + 0.30 * proximity)
    if _taskbar_show_desktop_action_mismatch(instruction_tokens, candidate):
        return min(0.41, 0.45 * iou + 0.30 * proximity)
    if _program_manager_desktop_item_action_mismatch(instruction_tokens, candidate):
        return min(0.41, 0.45 * iou + 0.30 * proximity)
    if _taskbar_search_status_action_mismatch(instruction_tokens, candidate):
        return min(0.41, 0.45 * iou + 0.30 * proximity)
    if _taskbar_app_state_action_mismatch(instruction_tokens, candidate):
        return 0.0
    if _taskbar_surface_context_mismatch(instruction, candidate):
        return 0.0
    if _browser_profile_identity_action_mismatch(instruction_tokens, candidate):
        return 0.0
    if _browser_profile_page_action_mismatch(instruction, candidate):
        return min(0.41, 0.45 * iou + 0.30 * proximity)
    if _browser_chrome_app_context_mismatch(instruction, candidate):
        return min(0.41, 0.45 * iou + 0.30 * proximity)
    if _background_transient_surface_target_mismatch(instruction, candidate, candidates):
        return min(0.41, 0.45 * iou + 0.30 * proximity)
    if _browser_menu_button_action_mismatch(instruction, candidate):
        return min(0.41, 0.45 * iou + 0.30 * proximity)
    if _browser_navigation_chrome_action_mismatch(instruction, candidate):
        return 0.0
    if _browser_toolbar_chrome_action_mismatch(instruction, candidate):
        return 0.0
    if _browser_address_bar_content_mismatch(
        instruction,
        instruction_tokens,
        candidate,
    ):
        return 0.0
    if _browser_address_bar_alternative_mismatch(
        instruction,
        instruction_tokens,
        candidate,
        candidates,
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
    if _clear_close_action_mismatch(instruction, instruction_tokens, candidate, candidates):
        return 0.0
    if _close_context_action_mismatch(instruction, candidate, candidates):
        return min(0.41, 0.45 * iou + 0.30 * proximity)
    if _window_close_alternative_mismatch(instruction, candidate, candidates):
        return min(0.41, 0.45 * iou + 0.30 * proximity)
    if _close_tab_action_mismatch(instruction, candidate, candidates):
        return 0.0
    if _browser_new_tab_bookmark_action_mismatch(instruction_tokens, candidate):
        return 0.0
    if _browser_new_tab_action_mismatch(instruction, instruction_tokens, candidate):
        return min(0.41, 0.45 * iou + 0.30 * proximity)
    if _browser_extension_access_action_mismatch(
        instruction,
        instruction_tokens,
        candidate,
    ):
        return min(0.41, 0.45 * iou + 0.30 * proximity)
    if _site_information_action_mismatch(instruction_tokens, candidate):
        return min(0.41, 0.45 * iou + 0.30 * proximity)
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
    if _pin_state_action_mismatch(instruction, candidate):
        return min(0.41, 0.45 * iou + 0.30 * proximity)
    if _password_visibility_state_action_mismatch(instruction, candidate):
        return min(0.41, 0.45 * iou + 0.30 * proximity)
    if _audio_output_polarity_action_mismatch(instruction, candidate, candidates):
        return min(0.41, 0.45 * iou + 0.30 * proximity)
    if _history_action_mismatch(instruction, candidate):
        return min(0.41, 0.45 * iou + 0.30 * proximity)
    if _explicit_checkbox_like_control_type_mismatch(
        instruction,
        candidate,
        control_intents,
    ):
        return 0.0
    if _checkbox_state_action_mismatch(instruction, candidate):
        return min(0.41, 0.45 * iou + 0.30 * proximity)
    if _navigation_media_transport_action_mismatch(instruction, candidate):
        return min(0.41, 0.45 * iou + 0.30 * proximity)
    if _calendar_exact_alternative_mismatch(instruction, candidate, candidates):
        return 0.0
    if _navigation_backup_action_mismatch(instruction, candidate):
        return min(0.41, 0.45 * iou + 0.30 * proximity)
    if _unresolved_contextual_duplicate_mismatch(instruction, candidate, candidates):
        return 0.0
    if _implicit_container_context_duplicate_mismatch(instruction, candidate, candidates):
        return 0.0
    if _explicit_transient_surface_alternative_mismatch(instruction, candidate, candidates):
        return 0.0
    if _generic_pane_context_duplicate_ambiguous(instruction, candidate, candidates):
        return 0.0
    if _positional_action_duplicate_mismatch(
        instruction,
        instruction_tokens,
        candidate,
        candidates,
    ):
        return 0.0
    positional_duplicate_satisfied = _candidate_satisfies_positional_action_duplicate_request(
        instruction,
        instruction_tokens,
        candidate,
        candidates,
    )
    named_contextual_duplicate_satisfied = _candidate_satisfies_named_contextual_duplicate_request(
        instruction,
        candidate,
        candidates,
    )
    if _contextual_surface_action_alternative_mismatch(
        instruction,
        instruction_tokens,
        candidate,
        candidates,
    ):
        return 0.0
    if _prepositional_context_action_alternative_mismatch(
        instruction,
        instruction_tokens,
        candidate,
        candidates,
    ):
        return 0.0
    if _delimited_context_only_target_alternative_mismatch(
        instruction,
        candidate,
        candidates,
        control_intents,
    ):
        return 0.0
    if _prepositional_context_only_target_alternative_mismatch(
        instruction,
        instruction_tokens,
        candidate,
        candidates,
        control_intents,
    ):
        return 0.0
    if _reversible_action_exact_alternative_mismatch(
        instruction,
        candidate,
        candidates,
        control_intents,
    ):
        return 0.0
    if _contained_row_action_context_mismatch(instruction, candidate, candidates):
        return min(0.41, 0.45 * iou + 0.30 * proximity)
    if _exact_action_word_alternative_mismatch(instruction, candidate, candidates, control_intents):
        return min(0.41, 0.45 * iou + 0.30 * proximity)
    if _explicit_action_context_mismatch_without_contextual_evidence(
        instruction,
        candidate,
        candidates,
    ):
        return min(0.41, 0.45 * iou + 0.30 * proximity)
    if _object_only_action_context_mismatch(instruction, candidate):
        return 0.0
    if _ambiguous_exact_literal_alias_alternative(
        instruction,
        candidate,
        candidates,
        control_intents,
    ):
        return 0.0
    if _exact_visible_label_alternative_mismatch(
        instruction,
        candidate,
        candidates,
        control_intents,
    ):
        return 0.0
    if _exclusive_action_family_mismatch(instruction, candidate.descriptor):
        return min(0.41, 0.45 * iou + 0.30 * proximity)
    if _mail_tab_account_reference_mismatch(instruction_tokens, candidate):
        return min(0.41, 0.45 * iou + 0.30 * proximity)
    if _browser_tab_auth_action_mismatch(instruction_tokens, candidate):
        return min(0.41, 0.45 * iou + 0.30 * proximity)
    if _browser_tab_generic_section_mismatch(instruction, instruction_tokens, candidate):
        return min(0.41, 0.45 * iou + 0.30 * proximity)
    if _browser_tab_contextual_item_mismatch(instruction, candidate):
        return min(0.41, 0.45 * iou + 0.30 * proximity)
    if _dropdown_item_request_launcher_mismatch(instruction, candidate, candidates):
        return 0.0
    if _dropdown_item_request_menuitem_mismatch(instruction, candidate, candidates):
        return 0.0
    if _combobox_dropdown_arrow_control_mismatch(instruction, candidate, candidates):
        return 0.0
    if _named_dropdown_alternative_mismatch(instruction, candidate, candidates):
        return 0.0
    if _dropdown_option_launcher_mismatch(instruction, candidate, candidates):
        return 0.0
    if _literal_stopword_name_alternative_mismatch(instruction, candidate, candidates):
        return 0.0
    if _combobox_dropdown_arrow_match(instruction, candidate, candidates):
        return min(1.0, 0.45 * iou + 0.30 * proximity + 0.20)
    if exact_visible_label_match and _contains_tighter_same_intent_action(
        selected=candidate,
        candidates=candidates,
        instruction=instruction,
        instruction_tokens=instruction_tokens,
        control_intents=control_intents,
    ):
        return CONTAINING_ROW_SNAP_CAP
    if exact_visible_label_match:
        return min(
            1.0,
            max(
                CANDIDATE_SNAP_FLOOR + TEXT_MATCH_GAP,
                0.45 * iou + 0.30 * proximity + 0.20,
            )
            + _foreground_rank_bonus(
                candidate,
                candidates,
                model_rect=model_rect,
                suppress_for_stronger_geometry=False,
            ),
        )
    if instruction_tokens and not semantic_tokens and _has_unparsed_alnum_text(candidate.text):
        return min(0.41, 0.45 * iou + 0.30 * proximity)
    if (
        control_intents
        and not _candidate_matches_control_intent(
            candidate,
            control_intents,
            instruction=instruction,
        )
        and not _spinner_stepper_button_match(instruction, candidate, candidates)
        and not _dropdown_item_request_matches_candidate(instruction, candidate, candidates)
        and not named_contextual_duplicate_satisfied
        and not _contextual_action_candidate_matches_surface_request(
            instruction,
            instruction_tokens,
            candidate,
            candidates,
        )
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
    if (
        instruction_tokens
        and semantic_tokens
        and text_score <= 0
        and not positional_duplicate_satisfied
        and not named_contextual_duplicate_satisfied
    ):
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
    final_score = score + _foreground_rank_bonus(
        candidate,
        candidates,
        model_rect=model_rect,
        suppress_for_stronger_geometry=False,
    )
    if _row_scoped_action_target_matches_context(instruction, candidate, candidates):
        final_score = max(final_score, CANDIDATE_SNAP_FLOOR + TEXT_MATCH_GAP)
    if positional_duplicate_satisfied:
        final_score = max(final_score, CANDIDATE_SNAP_FLOOR + TEXT_MATCH_GAP)
    if named_contextual_duplicate_satisfied:
        final_score = max(final_score, CANDIDATE_SNAP_FLOOR + TEXT_MATCH_GAP)
    if _spinner_stepper_button_match(instruction, candidate, candidates):
        final_score = max(final_score, CANDIDATE_SNAP_FLOOR + TEXT_MATCH_GAP)
    if _audio_output_mute_action_match(instruction, candidate):
        final_score = max(final_score, CANDIDATE_SNAP_FLOOR + TEXT_MATCH_GAP)
    if _menu_segment_intent(control_intents) and candidate.control_type == "splitbutton":
        if not _contains_tighter_same_intent_action(
            selected=candidate,
            candidates=candidates,
            instruction=instruction,
            instruction_tokens=instruction_tokens,
            control_intents=control_intents,
        ):
            return 0.0
        return min(final_score, CONTAINING_ROW_SNAP_CAP)
    if _contains_tighter_same_intent_action(
        selected=candidate,
        candidates=candidates,
        instruction=instruction,
        instruction_tokens=instruction_tokens,
        control_intents=control_intents,
    ):
        final_score = min(final_score, CONTAINING_ROW_SNAP_CAP)
    if _contains_tighter_row_action_candidate(
        selected=candidate,
        candidates=candidates,
        instruction=instruction,
        instruction_tokens=instruction_tokens,
        control_intents=control_intents,
    ):
        final_score = min(final_score, CONTAINING_ROW_SNAP_CAP)
    return min(1.0, final_score)


def _contains_tighter_same_intent_action(
    *,
    selected: ControlCandidate,
    candidates: list[ControlCandidate],
    instruction: str,
    instruction_tokens: set[str],
    control_intents: set[str],
) -> bool:
    if (
        selected.control_type not in ROW_LIKE_CONTROL_TYPES
        and selected.control_type not in TIGHT_ACTION_CONTROL_TYPES
        and selected.control_type not in COMPOSITE_ACTION_CONTROL_TYPES
        and selected.control_type not in SURFACE_CONTEXT_CONTROL_TYPES
        and not _instruction_requests_contained_surface_action(instruction, selected)
    ):
        return False
    if selected.control_type in ROW_CONTEXT_CONTROL_TYPES and _explicit_container_target_request(
        instruction,
        control_intents,
        selected.control_type,
    ):
        return False
    if selected.control_type in SURFACE_CONTEXT_CONTROL_TYPES and _explicit_surface_container_target_request(
        instruction,
        control_intents,
        selected.control_type,
    ):
        return False
    if _direct_surface_container_candidate_matches_request(instruction, selected, candidates):
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
        if (
            selected.control_type in LABELLED_FIELD_CONTROL_TYPES
            and _candidate_matches_control_intent(
                selected,
                control_intents,
                instruction=instruction,
            )
            and not _candidate_matches_control_intent(
                candidate,
                control_intents,
                instruction=instruction,
            )
        ):
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
            if _taskbar_start_button_generic_menu_mismatch(instruction, candidate):
                continue
            if _taskbar_task_view_action_mismatch(instruction, instruction_tokens, candidate):
                continue
            if _taskbar_hidden_icons_action_mismatch(instruction_tokens, candidate):
                continue
            if _taskbar_show_desktop_action_mismatch(instruction_tokens, candidate):
                continue
            if _taskbar_app_state_action_mismatch(instruction_tokens, candidate):
                continue
            if _taskbar_surface_context_mismatch(instruction, candidate):
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
        if _taskbar_start_button_generic_menu_mismatch(instruction, candidate):
            continue
        if _taskbar_task_view_action_mismatch(instruction, instruction_tokens, candidate):
            continue
        if _taskbar_hidden_icons_action_mismatch(instruction_tokens, candidate):
            continue
        if _taskbar_show_desktop_action_mismatch(instruction_tokens, candidate):
            continue
        if _taskbar_app_state_action_mismatch(instruction_tokens, candidate):
            continue
        if _taskbar_surface_context_mismatch(instruction, candidate):
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


def _surface_context_contains_tighter_action(
    *,
    selected: ControlCandidate,
    candidates: list[ControlCandidate],
    instruction: str,
    instruction_tokens: set[str],
    control_intents: set[str],
) -> bool:
    if not _instruction_requests_contained_surface_action(instruction, selected):
        return False
    raw_tokens = _tokens_from_text(instruction)
    requested_surfaces = _expand_token_aliases(
        _object_token_variants(raw_tokens & CONTEXTUAL_DUPLICATE_SURFACE_WORDS)
    )
    requested_action_tokens = _object_token_variants(
        (raw_tokens | instruction_tokens)
        - requested_surfaces
        - CONTEXTUAL_DUPLICATE_STOPWORDS
    )
    if not requested_action_tokens:
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
        candidate_tokens = _candidate_semantic_tokens(candidate) | _tokens_from_text(
            candidate.descriptor
        )
        if not (requested_action_tokens & candidate_tokens):
            continue
        if _browser_profile_page_action_mismatch(instruction, candidate):
            continue
        if _browser_chrome_app_context_mismatch(instruction, candidate):
            continue
        if _browser_menu_button_action_mismatch(instruction, candidate):
            continue
        if _browser_navigation_chrome_action_mismatch(instruction, candidate):
            continue
        if _browser_toolbar_chrome_action_mismatch(instruction, candidate):
            continue
        if _taskbar_surface_context_mismatch(instruction, candidate):
            continue
        return True
    return False


def _instruction_requests_contained_surface_action(
    instruction: str,
    selected: ControlCandidate,
) -> bool:
    if selected.control_type not in SURFACE_CONTEXT_CONTROL_TYPES:
        return False
    raw_tokens = _tokens_from_text(instruction)
    if not (raw_tokens & {"in", "inside", "on", "within"}):
        return False
    requested_surfaces = raw_tokens & CONTEXTUAL_DUPLICATE_SURFACE_WORDS
    if not requested_surfaces:
        return False
    selected_tokens = (
        _candidate_semantic_tokens(selected)
        | _tokens_from_text(selected.descriptor)
        | _tokens_from_text(selected.control_type)
        | _surface_context_type_tokens(selected.control_type)
    )
    return bool(_object_token_variants(requested_surfaces) & _object_token_variants(selected_tokens))


def _contains_tighter_row_action_candidate(
    *,
    selected: ControlCandidate,
    candidates: list[ControlCandidate],
    instruction: str,
    instruction_tokens: set[str],
    control_intents: set[str],
) -> bool:
    if selected.control_type not in ROW_CONTEXT_CONTROL_TYPES:
        return False
    if _explicit_container_target_request(
        instruction,
        control_intents,
        selected.control_type,
    ) and not _instruction_requests_contained_row_action(instruction):
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
        if not _row_action_context_rect_matches(selected, candidate):
            continue
        if _contained_row_action_candidate_matches(candidate, instruction_tokens, instruction):
            return True
    return False


def _contained_row_action_candidate_matches(
    candidate: ControlCandidate,
    instruction_tokens: set[str],
    instruction: str = "",
) -> bool:
    if candidate.control_type not in TIGHT_ACTION_CONTROL_TYPES:
        return False
    visible_tokens = _candidate_visible_text_tokens(candidate)
    candidate_tokens = set(visible_tokens)
    if not candidate_tokens:
        candidate_tokens = _candidate_automation_tokens(candidate)
        candidate_tokens -= CONTAINED_CONTROL_REQUEST_WORDS
    if not candidate_tokens:
        return False
    action_tokens = set(instruction_tokens)
    if instruction:
        action_tokens.update(_tokens_from_text(instruction) & candidate_tokens)
    return bool(action_tokens & candidate_tokens)


def _candidate_match_instruction_tokens(
    instruction: str,
    instruction_tokens: set[str],
    candidate: ControlCandidate,
) -> set[str]:
    tokens = set(instruction_tokens)
    visible_tokens = _candidate_visible_text_tokens(candidate)
    if visible_tokens:
        tokens.update(_tokens_from_text(instruction) & visible_tokens & CARDINAL_DIRECTION_ACTION_WORDS)
        tokens.update(_literal_stopword_name_match_tokens(instruction, visible_tokens))
    if (
        _instruction_requests_contained_row_action(instruction)
        and candidate.control_type in TIGHT_ACTION_CONTROL_TYPES
    ):
        if visible_tokens:
            tokens.update(_tokens_from_text(instruction) & visible_tokens)
    return tokens


def _automation_only_exact_action_match(
    candidate: ControlCandidate,
    overlap: set[str],
) -> bool:
    if _candidate_visible_text_tokens(candidate):
        return False
    if not candidate.automation_id.strip():
        return False
    return bool(overlap & AUTOMATION_ONLY_ACTION_MATCH_WORDS)


def _literal_stopword_name_match_tokens(
    instruction: str,
    visible_tokens: set[str],
) -> set[str]:
    requested = _literal_stopword_name_request_tokens(instruction)
    return requested & visible_tokens


def _literal_stopword_name_request_tokens(instruction: str) -> set[str]:
    raw_tokens = _tokens_from_text(instruction)
    matches = raw_tokens & LITERAL_STOPWORD_NAME_TOKENS
    if "drop" in matches and ({"drop", "down"} <= raw_tokens or "dropdown" in raw_tokens):
        matches.remove("drop")
    if not matches:
        return set()
    if _instruction_has_explicit_app_local_context(instruction, raw_tokens):
        return matches
    generic_fillers = (
        OPEN_VIEW_REQUEST_WORDS
        | GENERIC_OBJECT_REQUEST_WORDS
        | frozenset({"a", "an", "the", "use"})
    )
    remaining = raw_tokens - generic_fillers
    if len(remaining) == 1 and remaining <= GENERIC_LITERAL_STOPWORD_NAME_TOKENS:
        return remaining
    return set()


def _literal_stopword_name_alternative_mismatch(
    instruction: str,
    candidate: ControlCandidate,
    candidates: list[ControlCandidate],
) -> bool:
    requested = _literal_stopword_name_request_tokens(instruction)
    if not requested:
        return False
    candidate_tokens = (
        _candidate_visible_text_tokens(candidate)
        | _tokens_from_text(candidate.text)
        | _tokens_from_text(candidate.automation_id)
    )
    if requested & candidate_tokens:
        return False
    for other in candidates:
        if other.id == candidate.id or _same_visual_candidate(other, candidate):
            continue
        if other.control_type in NON_ACTIONABLE_CONTROL_TYPES:
            continue
        other_tokens = (
            _candidate_visible_text_tokens(other)
            | _tokens_from_text(other.text)
            | _tokens_from_text(other.automation_id)
        )
        if requested & other_tokens:
            return True
    return False


def _row_scoped_action_target_matches_context(
    instruction: str,
    candidate: ControlCandidate,
    candidates: list[ControlCandidate],
) -> bool:
    if not _instruction_requests_contained_row_action(instruction):
        return False
    instruction_tokens = _tokenize_instruction(instruction)
    if not _contained_row_action_candidate_matches(candidate, instruction_tokens, instruction):
        return False
    return not _contained_row_action_context_mismatch(instruction, candidate, candidates)


def _instruction_requests_contained_row_action(instruction: str) -> bool:
    raw_tokens = _tokens_from_text(instruction)
    if not (raw_tokens & ROW_ACTION_CONTAINER_WORDS):
        return False
    return bool(raw_tokens & {"for", "in", "inside", "on", "within"})


def _explicit_container_target_request(
    instruction: str,
    control_intents: set[str],
    selected_control_type: str,
) -> bool:
    if control_intents and selected_control_type not in control_intents:
        return False
    raw_tokens = _tokens_from_text(instruction)
    return bool(
        raw_tokens & CONTEXTUAL_DUPLICATE_CONTAINER_WORDS
        or {"list", "item"} <= raw_tokens
        or {"tree", "item"} <= raw_tokens
    )


def _explicit_surface_container_target_request(
    instruction: str,
    control_intents: set[str],
    selected_control_type: str,
) -> bool:
    if selected_control_type not in SURFACE_CONTEXT_CONTROL_TYPES:
        return False
    if control_intents and selected_control_type not in control_intents:
        return False
    raw_tokens = _tokens_from_text(instruction)
    requested_tokens = _object_token_variants(raw_tokens)
    surface_tokens = _object_token_variants(_surface_context_type_tokens(selected_control_type))
    return bool(requested_tokens & surface_tokens)


def _direct_surface_container_request_parts(instruction: str) -> tuple[set[str], set[str]]:
    text = (instruction or "").strip().lower()
    text = re.sub(r"[.!?]+$", "", text).strip()
    match = re.match(
        r"^(?:click|focus|highlight|open|press|select|show|tap)\s+(?:the\s+)?(.+?)$",
        text,
    )
    if not match:
        return set(), set()
    requested_object = match.group(1).strip()
    if re.search(r"\b(?:for|from|in|inside|on|within|with)\b", requested_object):
        return set(), set()
    surface_phrases: tuple[tuple[str, set[str]], ...] = (
        ("notification", {"notification"}),
        ("notifications", {"notification"}),
        ("column header", {"column", "header"}),
        ("column heading", {"column", "heading"}),
        ("column", {"column"}),
        ("side bar", {"sidebar"}),
        ("sidebar", {"sidebar"}),
        ("navigation", {"navigation"}),
        ("nav", {"nav"}),
        ("left rail", {"rail"}),
        ("rail", {"rail"}),
        ("card", {"card"}),
        ("cards", {"card"}),
        ("tile", {"tile"}),
        ("tiles", {"tile"}),
        ("dashboard", {"dashboard"}),
        ("dashboards", {"dashboard"}),
        ("details", {"details"}),
        ("detail", {"details"}),
        ("overview", {"overview"}),
        ("overviews", {"overview"}),
        ("page", {"page"}),
        ("pages", {"page"}),
        ("profile", {"profile"}),
        ("profiles", {"profile"}),
        ("route", {"route"}),
        ("routes", {"route"}),
        ("screen", {"screen"}),
        ("screens", {"screen"}),
        ("summary", {"summary"}),
        ("summaries", {"summary"}),
        ("view", {"view"}),
        ("views", {"view"}),
        ("workspace", {"workspace"}),
        ("workspaces", {"workspace"}),
        ("section", {"section"}),
        ("drawer", {"drawer"}),
        ("panel", {"panel"}),
        ("pane", {"pane"}),
        ("dialog", {"dialog"}),
        ("dialogs", {"dialog"}),
        ("modal", {"modal"}),
        ("modals", {"modal"}),
        ("popup", {"popup"}),
        ("popups", {"popup"}),
        ("popover", {"popover"}),
        ("popovers", {"popover"}),
        ("toast", {"toast"}),
        ("toasts", {"toast"}),
        ("snackbar", {"snackbar"}),
        ("snackbars", {"snackbar"}),
        ("alert", {"alert"}),
        ("alerts", {"alert"}),
        ("banner", {"banner"}),
        ("banners", {"banner"}),
        ("prompt", {"prompt"}),
        ("prompts", {"prompt"}),
        ("warning", {"warning"}),
        ("warnings", {"warning"}),
        ("form", {"form"}),
        ("forms", {"form"}),
        ("footer", {"footer"}),
        ("footers", {"footer"}),
        ("group", {"group"}),
        ("toolbar", {"toolbar"}),
        ("menu", {"menu"}),
        ("window", {"window"}),
        ("header", {"header"}),
        ("heading", {"heading"}),
    )
    label_required_phrases = frozenset(
        {
            "dashboard",
            "dashboards",
            "overview",
            "overviews",
            "profile",
            "profiles",
            "summary",
            "summaries",
            "workspace",
            "workspaces",
        }
    )
    for phrase, surface_tokens in surface_phrases:
        if requested_object == phrase:
            if phrase in label_required_phrases:
                return set(), set()
            return _object_token_variants(surface_tokens), set()
        suffix = f" {phrase}"
        if not requested_object.endswith(suffix):
            continue
        label = requested_object[: -len(suffix)].strip()
        label_tokens = _object_token_variants(
            _tokens_from_text(label) - ACTION_OBJECT_STOPWORDS - {"the"}
        )
        if phrase in label_required_phrases and not label_tokens:
            return set(), set()
        return _object_token_variants(surface_tokens), label_tokens
    return set(), set()


def _direct_surface_container_type_tokens(control_type: str) -> set[str]:
    return _object_token_variants(DIRECT_SURFACE_CONTAINER_ALIASES.get(control_type, frozenset()))


def _direct_surface_container_candidate_matches_request(
    instruction: str,
    candidate: ControlCandidate,
    candidates: list[ControlCandidate],
) -> bool:
    if candidate.control_type not in SURFACE_CONTEXT_CONTROL_TYPES:
        return False
    requested_surface, label_tokens = _direct_surface_container_request_parts(instruction)
    if not requested_surface:
        return False
    candidate_surface_tokens = _direct_surface_container_type_tokens(candidate.control_type)
    candidate_surface_tokens |= _object_token_variants(
        _tokens_from_text(candidate.descriptor)
    ) & requested_surface
    if not (requested_surface & candidate_surface_tokens):
        return False
    if not label_tokens:
        return True
    candidate_tokens = _field_alternative_label_tokens(candidate, candidates)
    candidate_tokens |= _tokens_from_text(candidate.descriptor)
    candidate_tokens = _object_token_variants(candidate_tokens)
    return bool(label_tokens & candidate_tokens)


def _explicit_surface_container_alternative_mismatch(
    instruction: str,
    candidate: ControlCandidate,
    candidates: list[ControlCandidate],
) -> bool:
    requested_surface, label_tokens = _direct_surface_container_request_parts(instruction)
    if not requested_surface:
        return False
    if _direct_surface_container_candidate_matches_request(instruction, candidate, candidates):
        return False
    candidate_tokens = _field_alternative_label_tokens(candidate, candidates)
    candidate_tokens |= _tokens_from_text(candidate.descriptor)
    candidate_tokens = _object_token_variants(candidate_tokens)
    for other in candidates:
        if other.id == candidate.id or _same_visual_candidate(other, candidate):
            continue
        if not _direct_surface_container_candidate_matches_request(instruction, other, candidates):
            continue
        other_tokens = _field_alternative_label_tokens(other, candidates)
        other_tokens |= _tokens_from_text(other.descriptor)
        other_tokens = _object_token_variants(other_tokens)
        if label_tokens and not (label_tokens & other_tokens):
            continue
        if label_tokens and not (label_tokens & candidate_tokens):
            continue
        if candidate_tokens & other_tokens:
            return True
        if _contains_rect(_expand_rect(other.rect, 4), candidate.rect):
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
    contained_actions: list[ControlCandidate] = []
    contained: list[ControlCandidate] = []
    for candidate in candidates:
        if _menu_segment_intent(control_intents) and candidate.control_type == "splitbutton":
            continue
        matches_intent = _candidate_matches_control_intent(
            candidate,
            control_intents,
            instruction=instruction,
        )
        matches_row_action = (
            _instruction_requests_contained_row_action(instruction)
            and bool(control_intents & ROW_CONTEXT_CONTROL_TYPES)
            and _contained_row_action_candidate_matches(candidate, instruction_tokens, instruction)
        )
        if not matches_intent and not matches_row_action:
            continue
        if not _contains_rect(bounds, candidate.rect):
            continue
        if _container_only_request_blocks_contained_candidate(
            instruction=instruction,
            instruction_tokens=instruction_tokens,
            control_intents=control_intents,
            candidate=candidate,
            candidates=candidates,
            model_rect=model_rect,
        ):
            continue
        if _browser_profile_page_action_mismatch(instruction, candidate):
            continue
        if _browser_chrome_app_context_mismatch(instruction, candidate):
            continue
        if _browser_menu_button_action_mismatch(instruction, candidate):
            continue
        if _browser_navigation_chrome_action_mismatch(instruction, candidate):
            continue
        if _browser_toolbar_chrome_action_mismatch(instruction, candidate):
            continue
        if _clear_close_action_mismatch(instruction, instruction_tokens, candidate, candidates):
            continue
        if _close_context_action_mismatch(instruction, candidate, candidates):
            continue
        if _close_tab_action_mismatch(instruction, candidate, candidates):
            continue
        if _browser_new_tab_bookmark_action_mismatch(instruction_tokens, candidate):
            continue
        if _browser_new_tab_action_mismatch(instruction, instruction_tokens, candidate):
            continue
        if _browser_extension_access_action_mismatch(
            instruction,
            instruction_tokens,
            candidate,
        ):
            continue
        if _taskbar_start_button_action_mismatch(instruction_tokens, candidate):
            continue
        if _taskbar_start_button_generic_menu_mismatch(instruction, candidate):
            continue
        if _taskbar_task_view_action_mismatch(instruction, instruction_tokens, candidate):
            continue
        if _taskbar_hidden_icons_action_mismatch(instruction_tokens, candidate):
            continue
        if _taskbar_show_desktop_action_mismatch(instruction_tokens, candidate):
            continue
        if _taskbar_app_state_action_mismatch(instruction_tokens, candidate):
            continue
        if _taskbar_surface_context_mismatch(instruction, candidate):
            continue
        if _disclosure_state_action_mismatch(instruction_tokens, candidate):
            continue
        if _pin_state_action_mismatch(instruction, candidate):
            continue
        if _password_visibility_state_action_mismatch(instruction, candidate):
            continue
        if _audio_output_polarity_action_mismatch(instruction, candidate, candidates):
            continue
        if _history_action_mismatch(instruction, candidate):
            continue
        if _checkbox_state_action_mismatch(instruction, candidate):
            continue
        if _navigation_media_transport_action_mismatch(instruction, candidate):
            continue
        if _navigation_backup_action_mismatch(instruction, candidate):
            continue
        if _unresolved_contextual_duplicate_mismatch(instruction, candidate, candidates):
            continue
        if _contained_row_action_context_mismatch(instruction, candidate, candidates):
            continue
        if _explicit_action_context_mismatch_without_contextual_evidence(
            instruction,
            candidate,
            candidates,
        ):
            continue
        if _object_only_action_context_mismatch(instruction, candidate):
            continue
        if _exclusive_action_family_mismatch(instruction, candidate.descriptor):
            continue
        if _mail_tab_account_reference_mismatch(instruction_tokens, candidate):
            continue
        if _combobox_dropdown_arrow_control_mismatch(instruction, candidate, candidates):
            continue
        if _dropdown_option_launcher_mismatch(instruction, candidate, candidates):
            continue
        if instruction_tokens and not _contained_control_intent_has_evidence(
            candidate=candidate,
            candidates=candidates,
            model_rect=model_rect,
            instruction=instruction,
            instruction_tokens=instruction_tokens,
        ):
            continue
        target_bucket = contained_actions if matches_row_action and not matches_intent else contained
        if any(_same_visual_candidate(candidate, existing) for existing in target_bucket):
            continue
        target_bucket.append(candidate)
        if len(target_bucket) > 1:
            return None
    if contained_actions:
        return contained_actions[0]
    return contained[0] if contained else None


def _container_only_request_blocks_contained_candidate(
    *,
    instruction: str,
    instruction_tokens: set[str],
    control_intents: set[str],
    candidate: ControlCandidate,
    candidates: list[ControlCandidate],
    model_rect: tuple[int, int, int, int],
) -> bool:
    raw_tokens = _tokens_from_text(instruction)
    if not (raw_tokens & CONTEXTUAL_DUPLICATE_CONTAINER_WORDS):
        return False
    if raw_tokens & {"for", "in", "inside", "on", "within"}:
        return False
    if raw_tokens & CONTAINED_CONTROL_REQUEST_WORDS:
        return False
    if not _candidate_matches_control_intent(
        candidate,
        control_intents,
        instruction=instruction,
    ):
        return False
    candidate_tokens = _candidate_semantic_tokens(candidate)
    if instruction_tokens & candidate_tokens:
        return False
    for context in candidates:
        if context.id == candidate.id or _same_visual_candidate(context, candidate):
            continue
        if not _contains_rect(_expand_rect(context.rect, 4), candidate.rect):
            continue
        if _geometry_agreement(context.rect, model_rect) < TARGET_ID_GEOMETRY_FLOOR:
            continue
        context_tokens = (
            _candidate_semantic_tokens(context)
            | _tokens_from_text(context.descriptor)
            | _surface_context_type_tokens(context.control_type)
        )
        if _text_evidence_score(instruction_tokens, _object_token_variants(context_tokens)) >= TARGET_ID_TEXT_FLOOR:
            return True
    return False


def _contained_control_intent_has_evidence(
    *,
    candidate: ControlCandidate,
    candidates: list[ControlCandidate],
    model_rect: tuple[int, int, int, int],
    instruction: str,
    instruction_tokens: set[str],
) -> bool:
    candidate_tokens = set(_candidate_semantic_tokens(candidate))
    context_tokens: set[str] = set()
    for context in candidates:
        if context.id == candidate.id or _same_visual_candidate(context, candidate):
            continue
        if not _contains_rect(_expand_rect(context.rect, 4), candidate.rect):
            continue
        if _geometry_agreement(context.rect, model_rect) < TARGET_ID_GEOMETRY_FLOOR:
            continue
        context_tokens.update(_candidate_semantic_tokens(context))
        context_tokens.update(_expand_token_aliases(_tokens_from_text(context.window_title)))
    evidence_tokens = candidate_tokens | context_tokens
    if _text_evidence_score(instruction_tokens, evidence_tokens) < TARGET_ID_TEXT_FLOOR:
        return False
    if _candidate_visible_text_tokens(candidate) and not (instruction_tokens & candidate_tokens):
        if _direct_contained_control_label_conflict(
            instruction,
            instruction_tokens,
            candidate,
        ):
            return False
        if not instruction_tokens <= context_tokens:
            return False
    return True


def _direct_contained_control_label_conflict(
    instruction: str,
    instruction_tokens: set[str],
    candidate: ControlCandidate,
) -> bool:
    visible_tokens = _candidate_visible_text_tokens(candidate)
    if not visible_tokens:
        return False
    raw_tokens = _tokens_from_text(instruction)
    if raw_tokens & {"for", "from", "in", "inside", "on", "with", "within"}:
        return False
    requested_tokens = _object_token_variants(
        (raw_tokens | instruction_tokens)
        - ACTION_OBJECT_STOPWORDS
        - CONTAINED_CONTROL_REQUEST_WORDS
        - CONTEXTUAL_DUPLICATE_CONTAINER_WORDS
        - ROW_CONTEXT_GENERIC_WORDS
    )
    if not requested_tokens:
        return False
    if requested_tokens & (_candidate_semantic_tokens(candidate) | visible_tokens):
        return False
    return True


def _candidate_matches_control_intent(
    candidate: ControlCandidate,
    control_intents: set[str],
    *,
    instruction: str = "",
) -> bool:
    if _explicit_text_field_control_type_mismatch(instruction, candidate):
        return False
    if _control_type_matches_intent(candidate.control_type, control_intents):
        return True
    if _state_action_button_matches_checkbox_intent(
        instruction,
        candidate,
        control_intents,
    ):
        return True
    if _app_local_row_item_matches_exact_text_intent(
        instruction,
        candidate,
        control_intents,
    ):
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


def _app_local_row_item_matches_exact_text_intent(
    instruction: str,
    candidate: ControlCandidate,
    control_intents: set[str],
) -> bool:
    if not control_intents or candidate.control_type not in ROW_CONTEXT_CONTROL_TYPES:
        return False
    raw_tokens = _tokens_from_text(instruction)
    if not _instruction_has_explicit_app_local_context(instruction, raw_tokens):
        return False
    visible_tokens = _tokens_from_text(candidate.text) | _tokens_from_text(candidate.automation_id)
    if not visible_tokens or not (visible_tokens <= raw_tokens):
        return False
    if _literal_stopword_name_match_tokens(instruction, visible_tokens):
        return True
    return (
        _text_evidence_score(
            _tokenize_instruction(instruction),
            _candidate_semantic_tokens(candidate),
        )
        >= TARGET_ID_TEXT_FLOOR
    )


def _state_action_button_matches_checkbox_intent(
    instruction: str,
    candidate: ControlCandidate,
    control_intents: set[str],
) -> bool:
    if "checkbox" not in control_intents:
        return False
    if candidate.control_type not in {"button", "splitbutton"}:
        return False

    instruction_tokens = _tokens_from_text(instruction)
    if (
        "checkbox" in instruction_tokens
        or {"check", "box"} <= instruction_tokens
        or bool(instruction_tokens & {"switch", "toggle"})
    ):
        return False
    turn_instruction = _turn_on_off_action_kind(instruction)
    requested_on = turn_instruction == "on" or bool(instruction_tokens & CHECKBOX_ON_ACTION_WORDS)
    requested_off = turn_instruction == "off" or bool(instruction_tokens & CHECKBOX_OFF_ACTION_WORDS)
    if requested_on == requested_off:
        return False

    control_tokens = _tokens_from_text(candidate.descriptor)
    turn_control = _turn_on_off_action_kind(candidate.descriptor)
    control_on = turn_control == "on" or bool(control_tokens & CHECKBOX_ON_ACTION_WORDS)
    control_off = turn_control == "off" or bool(control_tokens & CHECKBOX_OFF_ACTION_WORDS)
    if control_on == control_off:
        return False
    if requested_on and not control_on:
        return False
    if requested_off and not control_off:
        return False

    instruction_semantic = _tokenize_instruction(instruction) - (
        CHECKBOX_ON_ACTION_WORDS | CHECKBOX_OFF_ACTION_WORDS | {"off", "on", "turn"}
    )
    candidate_semantic = _candidate_semantic_tokens(candidate) - (
        CHECKBOX_ON_ACTION_WORDS | CHECKBOX_OFF_ACTION_WORDS | {"off", "on", "turn"}
    )
    return bool(instruction_semantic & candidate_semantic)


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
    if _taskbar_start_button_generic_menu_mismatch(instruction, first):
        return False
    if _taskbar_start_button_generic_menu_mismatch(instruction, second):
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
    semantic_tokens = _candidate_semantic_tokens(candidate) | _named_control_candidate_label_tokens(
        candidate,
        candidates,
    )
    if _browser_menu_button_action_mismatch(instruction, candidate):
        return True
    if _cell_target_request_mismatch(instruction, candidate):
        return True
    if _tab_context_candidate_mismatch(instruction, candidate, candidates):
        return True
    if _explicit_text_field_control_type_mismatch(instruction, candidate):
        return True
    if _named_control_label_missing(instruction, candidate, candidates):
        return True
    if _record_target_alternative_mismatch(instruction, candidate, candidates):
        return True
    if _access_permission_action_mismatch(instruction, candidate, candidates):
        return True
    if _explicit_combobox_alternative_mismatch(instruction, candidate, candidates):
        return True
    if _explicit_spinner_alternative_mismatch(instruction, candidate, candidates):
        return True
    if _spinner_stepper_parent_mismatch(instruction, candidate, candidates):
        return True
    if _explicit_slider_alternative_mismatch(instruction, candidate, candidates):
        return True
    if _explicit_pane_alternative_mismatch(instruction, candidate, candidates):
        return True
    if _explicit_surface_container_alternative_mismatch(instruction, candidate, candidates):
        return True
    if _explicit_item_alternative_mismatch(instruction, candidate, candidates):
        return True
    if _explicit_generic_item_control_type_mismatch(instruction, candidate):
        return True
    if _explicit_field_alternative_mismatch(instruction, candidate, candidates):
        return True
    if _explicit_option_alternative_mismatch(instruction, candidate, candidates):
        return True
    if _explicit_checkbox_like_alternative_mismatch(instruction, candidate, candidates):
        return True
    if _explicit_subtype_alternative_mismatch(instruction, candidate, candidates):
        return True
    if _positional_action_duplicate_mismatch(
        instruction,
        instruction_tokens,
        candidate,
        candidates,
    ):
        return True
    if _contextual_surface_action_alternative_mismatch(
        instruction,
        instruction_tokens,
        candidate,
        candidates,
    ):
        return True
    if instruction_tokens and not semantic_tokens and _has_unparsed_alnum_text(candidate.text):
        return True
    if not instruction_tokens or not semantic_tokens:
        return False
    if _taskbar_start_button_action_mismatch(instruction_tokens, candidate):
        return True
    if _taskbar_start_button_generic_menu_mismatch(instruction, candidate):
        return True
    if _taskbar_task_view_action_mismatch(instruction, instruction_tokens, candidate):
        return True
    if _taskbar_hidden_icons_action_mismatch(instruction_tokens, candidate):
        return True
    if _taskbar_show_desktop_action_mismatch(instruction_tokens, candidate):
        return True
    if _program_manager_desktop_item_action_mismatch(instruction_tokens, candidate):
        return True
    if _taskbar_app_state_action_mismatch(instruction_tokens, candidate):
        return True
    if _taskbar_surface_context_mismatch(instruction, candidate):
        return True
    if _browser_profile_identity_action_mismatch(instruction_tokens, candidate):
        return True
    if _browser_profile_page_action_mismatch(instruction, candidate):
        return True
    if _browser_chrome_app_context_mismatch(instruction, candidate):
        return True
    if _background_transient_surface_target_mismatch(instruction, candidate, candidates):
        return True
    if _browser_menu_button_action_mismatch(instruction, candidate):
        return True
    if _browser_navigation_chrome_action_mismatch(instruction, candidate):
        return True
    if _browser_toolbar_chrome_action_mismatch(instruction, candidate):
        return True
    if _browser_address_bar_content_mismatch(
        instruction,
        instruction_tokens,
        candidate,
    ):
        return True
    if _browser_address_bar_alternative_mismatch(
        instruction,
        instruction_tokens,
        candidate,
        candidates,
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
    if _clear_close_action_mismatch(instruction, instruction_tokens, candidate, candidates):
        return True
    if _close_context_action_mismatch(instruction, candidate, candidates):
        return True
    if _window_close_alternative_mismatch(instruction, candidate, candidates):
        return True
    if _close_tab_action_mismatch(instruction, candidate, candidates):
        return True
    if _browser_new_tab_bookmark_action_mismatch(instruction_tokens, candidate):
        return True
    if _browser_new_tab_action_mismatch(instruction, instruction_tokens, candidate):
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
    if _pin_state_action_mismatch(instruction, candidate):
        return True
    if _password_visibility_state_action_mismatch(instruction, candidate):
        return True
    if _audio_output_polarity_action_mismatch(instruction, candidate, candidates):
        return True
    if _history_action_mismatch(instruction, candidate):
        return True
    if _explicit_checkbox_like_control_type_mismatch(
        instruction,
        candidate,
        _instruction_control_intents(instruction),
    ):
        return True
    if _checkbox_state_action_mismatch(instruction, candidate):
        return True
    if _navigation_media_transport_action_mismatch(instruction, candidate):
        return True
    if _calendar_exact_alternative_mismatch(instruction, candidate, candidates):
        return True
    if _navigation_backup_action_mismatch(instruction, candidate):
        return True
    if _unresolved_contextual_duplicate_mismatch(instruction, candidate, candidates):
        return True
    if _implicit_container_context_duplicate_mismatch(instruction, candidate, candidates):
        return True
    if _explicit_transient_surface_alternative_mismatch(instruction, candidate, candidates):
        return True
    if _prepositional_context_action_alternative_mismatch(
        instruction,
        instruction_tokens,
        candidate,
        candidates,
    ):
        return True
    if _delimited_context_only_target_alternative_mismatch(
        instruction,
        candidate,
        candidates,
        _instruction_control_intents(instruction),
    ):
        return True
    if _prepositional_context_only_target_alternative_mismatch(
        instruction,
        instruction_tokens,
        candidate,
        candidates,
        _instruction_control_intents(instruction),
    ):
        return True
    if _reversible_action_exact_alternative_mismatch(
        instruction,
        candidate,
        candidates,
        _instruction_control_intents(instruction),
    ):
        return True
    if _contained_row_action_context_mismatch(instruction, candidate, candidates):
        return True
    if _exact_action_word_alternative_mismatch(instruction, candidate, candidates):
        return True
    if _explicit_action_context_mismatch_without_contextual_evidence(
        instruction,
        candidate,
        candidates,
    ):
        return True
    if _object_only_action_context_mismatch(instruction, candidate):
        return True
    if _exclusive_action_family_mismatch(instruction, candidate.descriptor):
        return True
    if _mail_tab_account_reference_mismatch(instruction_tokens, candidate):
        return True
    if _browser_tab_auth_action_mismatch(instruction_tokens, candidate):
        return True
    if _browser_tab_generic_section_mismatch(instruction, instruction_tokens, candidate):
        return True
    if _browser_tab_contextual_item_mismatch(instruction, candidate):
        return True
    if _dropdown_item_request_launcher_mismatch(instruction, candidate, candidates):
        return True
    if _dropdown_item_request_menuitem_mismatch(instruction, candidate, candidates):
        return True
    if _literal_stopword_name_alternative_mismatch(instruction, candidate, candidates):
        return True
    if _exact_visible_label_alternative_mismatch(
        instruction,
        candidate,
        candidates,
        _instruction_control_intents(instruction),
    ):
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
