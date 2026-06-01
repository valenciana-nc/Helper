"""Shared natural-language intent parsing for Help-mode target resolution."""

from __future__ import annotations

import re

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

INPUT_CONTROL_TYPES = frozenset({"edit", "combobox", "spinner"})
EDIT_CONTROL_TYPES = frozenset({"edit"})
SLIDER_CONTROL_TYPES = frozenset({"slider"})
SPINNER_CONTROL_TYPES = frozenset({"spinner"})

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_CAMEL_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_SEPARATOR_RE = re.compile(r"[_\-.]+")
_WHITESPACE_RE = re.compile(r"\s+")

_PHRASE_TOKEN_ALIAS_PATTERNS = (
    (re.compile(r"\ba\s+(?:to\s+)?z\b"), {"ascending", "sort"}),
    (re.compile(r"\bz\s+(?:to\s+)?a\b"), {"descending", "sort"}),
    (re.compile(r"\b(?:ctrl|control)\s*\+?\s*shift\s*\+?\s*z\b"), {"redo"}),
    (re.compile(r"\b(?:ctrl|control)\s*\+?\s*z\b"), {"undo"}),
    (re.compile(r"\b(?:ctrl|control)\s*\+?\s*y\b"), {"redo"}),
    (re.compile(r"\bleft\s+arrow\b"), {"left_arrow"}),
    (re.compile(r"\bright\s+arrow\b"), {"right_arrow"}),
    (re.compile(r"\bcheck\s+mark\b"), {"checkmark"}),
    (re.compile(r"\bexternal\s+link\b"), {"external", "open_new"}),
    (
        re.compile(r"\bopen\s+(?:in\s+)?(?:a\s+)?new\s+tab\b"),
        {"external", "new_tab", "open_new"},
    ),
    (
        re.compile(r"\bopen\s+(?:in\s+)?(?:a\s+)?new\s+window\b"),
        {"external", "new_window", "open_new"},
    ),
    (re.compile(r"\bopen\s+in\s+new\b"), {"external", "open_new"}),
    (re.compile(r"\bzoom\s+in\b"), {"zoom_in"}),
    (re.compile(r"\bzoom\s+out\b"), {"zoom_out"}),
)
_CONTROL_PHRASE_TOKEN_ALIAS_PATTERNS = (
    (re.compile(r"\bnew\s+tab\b"), {"new_tab", "open_new"}),
    (re.compile(r"\bnew\s+window\b"), {"new_window", "open_new"}),
)
_AUTH_DIRECTION_TOKEN_REWRITES = (
    (
        re.compile(r"\b(?:log\s+in|login|sign\s+in|signin)\b"),
        {"in", "login", "signin"},
        {"log", "logout", "out", "sign", "signout"},
    ),
    (
        re.compile(r"\b(?:log\s+off|logoff|log\s+out|logout|sign\s+out|signout)\b"),
        {"logoff", "logout", "out", "signout"},
        {"in", "log", "login", "sign", "signin"},
    ),
)

_SYMBOL_TOKEN_ALIASES = {
    "?": {"help", "mark", "question"},
    "+": {"add", "create", "new", "plus", "zoom_in"},
    "-": {"minimize", "minus", "zoom_out"},
    "<": {"arrow", "back", "chevron", "left", "previous"},
    ">": {"arrow", "chevron", "forward", "next", "right"},
    "...": {"dot", "dots", "ellipsis", "menu", "more", "options"},
    "\u00d7": {"clear", "close", "dismiss", "x"},
    "\u2039": {"arrow", "back", "chevron", "left", "previous"},
    "\u203a": {"arrow", "chevron", "forward", "next", "right"},
    "\u2190": {"back", "left", "left_arrow", "previous"},
    "\u2192": {"forward", "next", "right", "right_arrow"},
    "\u2212": {"minimize", "minus", "zoom_out"},
    "\u2303": {"arrow", "caret", "chevron", "collapse", "disclosure"},
    "\u2304": {"arrow", "caret", "chevron", "collapse", "disclosure"},
    "\u24d8": {"about", "details", "info", "information"},
    "\u25a1": {"maximize", "square"},
    "\u25a2": {"maximize", "square"},
    "\u25b4": {"arrow", "caret", "chevron", "collapse", "disclosure"},
    "\u25b5": {"arrow", "caret", "chevron", "collapse", "disclosure"},
    "\u25b8": {"arrow", "caret", "chevron", "disclosure", "expand"},
    "\u25b9": {"arrow", "caret", "chevron", "disclosure", "expand"},
    "\u25be": {"arrow", "caret", "chevron", "collapse", "disclosure"},
    "\u25bf": {"arrow", "caret", "chevron", "collapse", "disclosure"},
    "\u2b1c": {"maximize", "square"},
    "\u2026": {"dot", "dots", "ellipsis", "menu", "more", "options"},
    "\u22ee": {"dot", "dots", "kebab", "menu", "more", "options"},
    "\u22ef": {"dot", "dots", "ellipsis", "menu", "more", "options"},
    "\u2699": {"cog", "gear", "options", "preferences", "settings"},
    "\u2139": {"about", "details", "info", "information"},
    "\u2197": {"external", "launch", "new_tab", "open_new"},
    "\u2715": {"clear", "close", "dismiss", "x"},
    "\u2716": {"clear", "close", "dismiss", "x"},
    "\u27f2": {"refresh", "reload"},
    "\u27f3": {"refresh", "reload"},
    "\u29c9": {"external", "launch", "new_tab", "open_new"},
    "\u2b08": {"external", "launch", "new_tab", "open_new"},
    "\u2605": {"bookmark", "favorite", "star"},
    "\u2606": {"bookmark", "favorite", "star"},
    "\u2661": {"favorite", "heart"},
    "\u2665": {"favorite", "heart"},
    "\u2705": {"apply", "checkmark", "complete", "confirm", "done", "finish", "ok"},
    "\u2713": {"apply", "checkmark", "complete", "confirm", "done", "finish", "ok"},
    "\u2714": {"apply", "checkmark", "complete", "confirm", "done", "finish", "ok"},
    "\u25b6": {"play"},
    "\u23f5": {"play"},
    "\u23f8": {"pause"},
    "\u23f9": {"stop"},
    "\u23fa": {"record"},
    "\u21b6": {"undo"},
    "\u21b7": {"redo"},
    "\u21ba": {"undo"},
    "\u21bb": {"redo"},
    "\u238c": {"undo"},
    "\u293a": {"undo"},
    "\u293b": {"redo"},
    "\u270e": {"edit", "pencil"},
    "\u270f": {"edit", "pencil"},
    "\u2702": {"cut", "scissors"},
    "\u2709": {"email", "envelope", "mail"},
    "\U0001f589": {"edit", "pencil"},
    "\U0001f464": {"account", "avatar", "person", "profile", "user"},
    "\U0001f465": {"account", "avatar", "people", "person", "profile", "user"},
    "\U0001f517": {"link", "share"},
    "\U0001f503": {"refresh", "reload"},
    "\U0001f504": {"refresh", "reload"},
    "\U0001f514": {"alerts", "bell", "notification", "notifications", "notify"},
    "\U0001f399": {"mic", "microphone"},
    "\U0001f3a4": {"mic", "microphone"},
    "\U0001f507": {"mute", "speaker", "sound", "volume"},
    "\U0001f508": {"speaker", "sound", "volume"},
    "\U0001f509": {"speaker", "sound", "volume"},
    "\U0001f50a": {"speaker", "sound", "volume"},
    "\U0001f3a5": {"camera", "video", "webcam"},
    "\U0001f4f7": {"camera", "video", "webcam"},
    "\U0001f4f9": {"camera", "video", "webcam"},
    "\U0001f6cd": {"bag", "basket", "cart"},
    "\U0001f6d2": {"bag", "basket", "cart"},
    "\U0001f441": {"eye", "visibility", "visible"},
    "\U0001f440": {"eye", "visibility", "visible"},
    "\U0001f510": {"key", "lock", "padlock", "secure", "security"},
    "\U0001f511": {"key", "secure", "security"},
    "\U0001f512": {"lock", "locked", "padlock"},
    "\U0001f513": {"lock", "padlock", "unlock", "unlocked"},
    "\u2302": {"home", "house"},
    "\U0001f3e0": {"home", "house"},
    "\U0001f4c5": {"calendar", "date"},
    "\U0001f4c6": {"calendar", "date"},
    "\U0001f5d3": {"calendar", "date"},
    "\U0001f551": {"clock", "time"},
    "\U0001f552": {"clock", "time"},
    "\U0001f5c3": {"archive", "cabinet", "filing"},
    "\U0001f5c4": {"archive", "cabinet", "filing"},
    "\U0001f5d1": {"bin", "delete", "remove", "trash", "wastebasket"},
    "\U0001f5d5": {"minimize", "minus"},
    "\U0001f5d6": {"maximize", "square"},
    "\U0001f5d7": {"overlap", "restore"},
    "\U0001f4cb": {"clipboard", "paste"},
    "\U0001f4cc": {"pin", "pinned", "pushpin", "thumbtack"},
    "\U0001f4e7": {"email", "envelope", "mail"},
    "\U0001f4e8": {"email", "envelope", "mail"},
    "\U0001f4e9": {"email", "envelope", "mail"},
    "\U0001f4ce": {"attach", "attachment", "file", "paperclip"},
    "\U0001f587": {"attach", "attachment", "file", "paperclip"},
    "\U0001f4c1": {"directory", "folder"},
    "\U0001f4c2": {"directory", "folder"},
    "\U0001f5c0": {"directory", "folder"},
    "\U0001f5c1": {"directory", "folder"},
    "\U0001f588": {"pin", "pinned", "pushpin", "thumbtack"},
    "\U0001f4be": {"disk", "floppy", "save"},
    "\u2399": {"print", "printer"},
    "\U0001f5a8": {"print", "printer"},
    "\U0001f6e1": {"secure", "security", "shield"},
    "\U0001f6c8": {"about", "details", "info", "information"},
    "\U0001f50d": {"find", "lens", "magnifier", "magnifying", "search"},
    "\U0001f50e": {"find", "lens", "magnifier", "magnifying", "search"},
}

_TOKEN_ALIASES = {
    "account": {"person", "profile", "user"},
    "add": {"create", "new", "plus"},
    "about": {"details", "info", "information"},
    "apply": {"confirm", "ok", "okay"},
    "address": {"location", "url"},
    "arrow": {"caret", "chevron", "collapse", "disclosure", "expand"},
    "avatar": {"account", "person", "profile", "user"},
    "b": {"bold"},
    "back": {"previous"},
    "attach": {"attachment", "browse", "choose", "file", "paperclip", "upload"},
    "attachment": {"attach", "browse", "choose", "file", "paperclip", "upload"},
    "bag": {"basket", "cart"},
    "alerts": {"bell", "notification", "notifications"},
    "basket": {"bag", "cart"},
    "bell": {"alerts", "notification", "notifications", "notify"},
    "bookmark": {"favorite", "star"},
    "bold": {"b"},
    "browse": {"choose", "file", "files", "select", "upload"},
    "cart": {"bag", "basket"},
    "calendar": {"date"},
    "cabinet": {"archive", "filing"},
    "caret": {"arrow", "chevron", "collapse", "disclosure", "expand"},
    "chevron": {"arrow", "caret", "collapse", "disclosure", "expand"},
    "choose": {"browse", "file", "select", "upload"},
    "checkmark": {"apply", "complete", "confirm", "done", "finish", "ok", "okay", "tick"},
    "clear": {"x"},
    "clone": {"copy", "duplicate"},
    "clock": {"time"},
    "cog": {"options", "preferences", "settings"},
    "camera": {"video", "webcam"},
    "collapse": {"arrow", "caret", "chevron", "disclosure"},
    "confirm": {"apply", "ok", "okay"},
    "continue": {"next", "proceed"},
    "copy": {"clone", "duplicate"},
    "complete": {"done", "finish"},
    "create": {"add", "new", "plus"},
    "cut": {"scissors"},
    "date": {"calendar"},
    "delete": {"bin", "remove", "trash", "wastebasket"},
    "details": {"about", "info", "information"},
    "dismiss": {"close"},
    "disclosure": {"arrow", "caret", "chevron", "collapse", "expand"},
    "directories": {"directory", "folder", "folders"},
    "directory": {"folder"},
    "document": {"file"},
    "documents": {"file", "files"},
    "download": {"export"},
    "done": {"complete", "finish"},
    "duplicate": {"clone", "copy"},
    "edit": {"pencil"},
    "email": {"envelope", "mail"},
    "envelope": {"email", "mail"},
    "external": {"launch", "new_tab", "new_window", "open_new"},
    "expand": {"arrow", "caret", "chevron", "disclosure"},
    "export": {"download"},
    "eye": {"visibility", "visible"},
    "favorite": {"bookmark", "star"},
    "file": {"attach", "attachment", "browse", "choose", "document", "select", "upload"},
    "files": {"attach", "attachment", "browse", "choose", "documents", "file", "select", "upload"},
    "filing": {"archive", "cabinet"},
    "find": {"search"},
    "finish": {"complete", "done"},
    "filter": {"funnel"},
    "floppy": {"save"},
    "folder": {"directory"},
    "folders": {"directories", "directory", "folder"},
    "forward": {"next"},
    "funnel": {"filter"},
    "gear": {"options", "preferences", "settings"},
    "home": {"house"},
    "house": {"home"},
    "i": {"italic"},
    "info": {"about", "details", "information"},
    "information": {"about", "details", "info"},
    "italic": {"i"},
    "italics": {"i", "italic"},
    "launch": {"external", "open_new"},
    "lens": {"find", "search"},
    "left_arrow": {"back", "previous"},
    "location": {"address", "url"},
    "lock": {"locked", "padlock", "unlock"},
    "locked": {"lock", "padlock"},
    "logoff": {"logout", "signout"},
    "log": {"login", "sign", "signin"},
    "login": {"log", "sign", "signin"},
    "logout": {"logoff", "signout"},
    "mail": {"email", "envelope"},
    "magnifier": {"find", "search"},
    "magnifying": {"find", "search"},
    "kebab": {"menu", "more", "options"},
    "menu": {"more", "options"},
    "meatballs": {"menu", "more", "options"},
    "mic": {"microphone"},
    "microphone": {"mic"},
    "minimize": {"minus"},
    "minus": {"minimize", "zoom_out"},
    "more": {"menu", "options"},
    "new": {"add", "create", "plus"},
    "new_tab": {"external", "open_new"},
    "new_window": {"external", "open_new"},
    "next": {"continue", "forward", "proceed"},
    "notification": {"alerts", "bell", "notifications", "notify"},
    "notifications": {"alerts", "bell", "notification", "notify"},
    "notify": {"bell", "notification", "notifications"},
    "ok": {"apply", "confirm", "okay"},
    "okay": {"apply", "confirm", "ok"},
    "omnibox": {"address", "search", "url"},
    "open_new": {"external", "new_tab", "new_window"},
    "options": {"preferences", "settings"},
    "overflow": {"menu", "more", "options"},
    "padlock": {"lock", "locked", "unlock", "unlocked"},
    "paperclip": {"attach", "attachment", "file", "upload"},
    "people": {"account", "person", "profile", "user"},
    "person": {"account", "avatar", "profile", "user"},
    "paste": {"clipboard"},
    "pencil": {"edit"},
    "pin": {"pinned", "pushpin", "thumbtack"},
    "pinned": {"pin", "pushpin", "thumbtack", "unpin"},
    "preferences": {"options", "settings"},
    "previous": {"back"},
    "plane": {"send"},
    "print": {"printer"},
    "printer": {"print"},
    "proceed": {"continue", "next"},
    "profile": {"account", "person", "user"},
    "pushpin": {"pin", "pinned", "thumbtack"},
    "refresh": {"reload"},
    "remove": {"delete"},
    "reload": {"refresh"},
    "right_arrow": {"forward", "next"},
    "restore": {"overlap"},
    "save": {"floppy"},
    "search": {"find"},
    "scissor": {"cut", "scissors"},
    "scissors": {"cut"},
    "secure": {"security", "shield"},
    "security": {"secure", "shield"},
    "select": {"browse", "choose", "file", "upload"},
    "send": {"plane", "submit"},
    "settings": {"options", "preferences"},
    "shield": {"secure", "security"},
    "sign": {"log", "login", "signin"},
    "signin": {"log", "login", "sign"},
    "signout": {"logoff", "logout"},
    "sound": {"speaker", "volume"},
    "speaker": {"sound", "volume"},
    "square": {"maximize"},
    "star": {"bookmark", "favorite"},
    "submit": {"send"},
    "tack": {"pin", "pinned", "pushpin", "thumbtack"},
    "thumbtack": {"pin", "pinned", "pushpin"},
    "u": {"underline"},
    "underlined": {"u", "underline"},
    "underline": {"u"},
    "import": {"upload"},
    "unlock": {"lock", "padlock", "unlocked"},
    "unlocked": {"lock", "padlock", "unlock"},
    "upload": {"attach", "attachment", "browse", "choose", "file", "import", "select"},
    "unpin": {"pin", "pinned", "pushpin", "thumbtack"},
    "user": {"account", "person", "profile"},
    "visibility": {"eye", "visible"},
    "visible": {"eye", "visibility"},
    "ellipsis": {"more", "options", "menu"},
    "close": {"dismiss"},
    "dot": {"more", "options", "menu"},
    "dots": {"more", "options", "menu"},
    "trash": {"bin", "delete", "remove", "wastebasket"},
    "time": {"clock"},
    "tick": {"checkmark"},
    "bin": {"delete", "remove"},
    "plus": {"add", "new", "create", "zoom_in"},
    "url": {"address", "location"},
    "video": {"camera", "webcam"},
    "volume": {"sound", "speaker"},
    "webcam": {"camera", "video"},
    "wastebasket": {"bin", "delete", "remove", "trash"},
    "x": {"clear", "close", "dismiss"},
    "zoomin": {"zoom_in"},
    "zoomout": {"zoom_out"},
}

_INSTRUCTION_STOPWORDS = frozenset(
    {
        "click",
        "tap",
        "press",
        "select",
        "choose",
        "pick",
        "adjust",
        "drag",
        "slide",
        "move",
        "spin",
        "open",
        "focus",
        "go",
        "the",
        "on",
        "in",
        "to",
        "a",
        "an",
        "and",
        "or",
        "of",
        "this",
        "that",
        "here",
        "there",
        "bar",
        "highlighted",
        "shown",
        "indicated",
        "selected",
        "control",
        "controls",
        "area",
        "spot",
        "place",
        "location",
        "your",
        "for",
        "now",
        "at",
        "is",
        "it",
        "be",
        "button",
        "icon",
        "link",
        "hyperlink",
        "split",
        "tab",
        "list",
        "tree",
        "menu",
        "item",
        "option",
        "choice",
        "choices",
        "header",
        "heading",
        "field",
        "input",
        "edit",
        "editable",
        "box",
        "text",
        "textbox",
        "textarea",
        "check",
        "checkbox",
        "toggle",
        "switch",
        "radio",
        "radiobutton",
        "splitbutton",
        "combo",
        "combobox",
        "dropdown",
        "picker",
        "selector",
        "slider",
        "spinner",
        "spinbox",
        "stepper",
        "drop",
        "down",
        "type",
        "enter",
        "into",
        "near",
        "beside",
        "nearby",
        "under",
        "above",
        "below",
        "top",
        "bottom",
        "left",
        "right",
        "upper",
        "lower",
        "middle",
        "center",
        "corner",
        "side",
        "row",
        "column",
        "listitem",
        "treeitem",
        "menuitem",
        "tabitem",
        "headeritem",
    }
)

_INPUT_INTENT_WORDS = frozenset({"field", "input", "text", "textbox", "textarea", "box"})
_ADDRESS_BAR_INTENT_WORDS = frozenset({"address", "url", "location", "omnibox"})
_SEARCH_BAR_INTENT_WORDS = frozenset({"filter", "find", "query", "search"})
_TEXT_ENTRY_ACTION_WORDS = frozenset({"enter", "type"})
_TEXT_ENTRY_BLOCKING_WORDS = frozenset(
    {
        "arrow",
        "button",
        "caret",
        "check",
        "checkbox",
        "chevron",
        "combo",
        "combobox",
        "down",
        "drop",
        "dropdown",
        "header",
        "headeritem",
        "heading",
        "hyperlink",
        "icon",
        "item",
        "key",
        "link",
        "list",
        "listitem",
        "menu",
        "menuitem",
        "option",
        "radio",
        "radiobutton",
        "shortcut",
        "slider",
        "spinbox",
        "spinner",
        "split",
        "splitbutton",
        "stepper",
        "switch",
        "tab",
        "tabitem",
        "toggle",
        "tree",
        "treeitem",
    }
)
_CHECKBOX_ACTION_WORDS = frozenset({"check", "tick", "uncheck", "untick"})
_CHECKBOX_STATE_ACTION_WORDS = frozenset({"disable", "enable"})
_CHECKBOX_ACTION_BLOCKING_WORDS = frozenset(
    {
        "button",
        "combo",
        "combobox",
        "down",
        "drop",
        "dropdown",
        "header",
        "headeritem",
        "heading",
        "hyperlink",
        "icon",
        "key",
        "link",
        "list",
        "listitem",
        "menu",
        "menuitem",
        "radio",
        "radiobutton",
        "shortcut",
        "slider",
        "spinbox",
        "spinner",
        "split",
        "splitbutton",
        "stepper",
        "tab",
        "tabitem",
        "tree",
        "treeitem",
    }
)
_BUTTON_INTENT_TYPES = frozenset({"button", "splitbutton"})
_EDIT_ACTION_INTENT_TYPES = frozenset({"button", "splitbutton", "hyperlink", "menuitem"})
_FORMAT_ACTION_INTENT_TYPES = frozenset({"button", "splitbutton", "menuitem"})
_HISTORY_ACTION_INTENT_TYPES = frozenset({"button", "splitbutton", "menuitem"})
_ZOOM_ACTION_INTENT_TYPES = frozenset({"button", "splitbutton", "menuitem"})
_EXTERNAL_LINK_ACTION_INTENT_TYPES = frozenset(
    {"button", "splitbutton", "hyperlink", "menuitem"}
)
_CONFIRM_ACTION_INTENT_TYPES = frozenset({"button", "splitbutton", "menuitem"})
_ICON_INTENT_TYPES = TIGHT_ACTION_CONTROL_TYPES
_MENU_INTENT_TYPES = frozenset({"menuitem", "splitbutton"})
_MENU_LAUNCHER_INTENT_TYPES = frozenset({"button", "splitbutton"})
_MENU_LAUNCHER_WORDS = frozenset(
    {"dot", "dots", "ellipsis", "kebab", "meatballs", "more", "overflow"}
)
_CONTEXTUAL_MENU_LAUNCHER_WORDS = frozenset(
    {
        "account",
        "avatar",
        "cog",
        "gear",
        "options",
        "preferences",
        "profile",
        "settings",
        "user",
    }
)
_DISCLOSURE_INTENT_TYPES = frozenset({"button", "splitbutton"})
_DISCLOSURE_INTENT_WORDS = frozenset(
    {"arrow", "caret", "chevron", "collapse", "disclosure", "expand", "expander"}
)
_DROPDOWN_INTENT_TYPES = frozenset({"combobox", "menuitem", "splitbutton"})
_PICKER_LAUNCHER_INTENT_TYPES = frozenset({"button", "splitbutton", "edit", "combobox"})
_PICKER_LAUNCHER_WORDS = frozenset({"chooser", "picker", "selector"})
_PICKER_LAUNCHER_CONTEXT_WORDS = frozenset(
    {
        "avatar",
        "calendar",
        "color",
        "colour",
        "date",
        "directory",
        "file",
        "folder",
        "image",
        "month",
        "photo",
        "picture",
        "time",
    }
)
_SELECTOR_INTENT_TYPES = frozenset({"combobox"})
_SELECTOR_INTENT_WORDS = frozenset({"picker", "selector"})
_SELECTOR_BLOCKING_WORDS = frozenset(
    {
        "button",
        "checkbox",
        "icon",
        "key",
        "link",
        "menu",
        "menuitem",
        "radio",
        "radiobutton",
        "shortcut",
        "split",
        "splitbutton",
    }
)
_OPTION_INTENT_TYPES = frozenset({"radiobutton", "listitem", "treeitem", "menuitem"})
_OPTION_INTENT_WORDS = frozenset({"choice", "choices", "option"})
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
_PASSWORD_VISIBILITY_CONTEXT_WORDS = frozenset({"passcode", "password"})
_PASSWORD_VISIBILITY_ACTION_WORDS = frozenset(
    {"conceal", "eye", "hide", "mask", "reveal", "show", "unmask", "visibility", "visible"}
)
_AUDIO_OUTPUT_ACTION_WORDS = frozenset(
    {
        "decrease",
        "down",
        "increase",
        "lower",
        "louder",
        "mute",
        "off",
        "on",
        "quieter",
        "raise",
        "sound",
        "speaker",
        "unmute",
        "up",
        "volume",
    }
)
_MEDIA_CONTROL_ACTION_WORDS = frozenset({"pause", "play", "record"})
_MEDIA_CONTROL_CONTEXT_WORDS = frozenset(
    {"audio", "clip", "media", "movie", "music", "playback", "song", "track", "video"}
)
_MEDIA_RESUME_WORDS = frozenset({"resume"})
_MEDIA_STOP_WORDS = frozenset({"stop"})
_FORMAT_ACTION_WORDS = frozenset({"bold", "italic", "italics", "underline", "underlined"})
_FORMAT_SINGLE_LETTER_WORDS = frozenset({"b", "i", "u"})
_FORMAT_SINGLE_LETTER_CONTEXT_WORDS = frozenset(
    {"button", "click", "format", "formatting", "icon", "press", "style", "tap", "toolbar"}
)
_FORMAT_ACTION_CONTEXT_WORDS = frozenset(
    {"content", "copy", "message", "paragraph", "selection", "text", "word", "words"}
)
_CLEAR_ACTION_WORDS = frozenset({"clear"})
_CLEAR_ACTION_CONTEXT_WORDS = frozenset(
    {"box", "field", "find", "input", "query", "search", "text", "textbox", "textarea"}
)
_DIALOG_DISMISS_ACTION_WORDS = frozenset({"cancel", "close", "dismiss"})
_DIALOG_DISMISS_CONTEXT_WORDS = frozenset({"dialog", "modal", "popup"})
_HISTORY_ACTION_WORDS = frozenset({"redo", "undo"})
_ZOOM_ACTION_WORDS = frozenset({"zoom_in", "zoom_out"})
_ZOOM_ACTION_CONTEXT_WORDS = frozenset({"in", "out", "zoom"})
_EXTERNAL_LINK_ACTION_WORDS = frozenset(
    {"external", "launch", "new_tab", "new_window", "open_new"}
)
_EXTERNAL_LINK_CONTEXT_WORDS = frozenset({"new", "tab", "window"})
_CONFIRM_ACTION_WORDS = frozenset({"apply", "checkmark", "confirm", "ok", "okay"})
_CONFIRM_ACTION_CONTEXT_WORDS = frozenset({"mark", "selection"})
_EDIT_ACTION_CONTEXT_WORDS = frozenset(
    {
        "account",
        "avatar",
        "button",
        "details",
        "entry",
        "icon",
        "item",
        "profile",
        "record",
        "row",
        "selection",
        "user",
    }
)


def tokenize_instruction(instruction: str) -> set[str]:
    tokens = tokens_from_text(instruction)
    filtered = {
        token for token in tokens if token not in _INSTRUCTION_STOPWORDS and len(token) > 1
    }
    if _password_visibility_requested(tokens):
        filtered.update({"eye", "visibility", "visible"})
    if _audio_output_control_requested(tokens):
        filtered.discard("audio")
        filtered.update({"speaker", "sound", "volume"})
    if _media_control_requested(tokens):
        filtered -= _MEDIA_CONTROL_CONTEXT_WORDS
        if tokens & _MEDIA_RESUME_WORDS:
            filtered.add("play")
    if _format_action_requested(tokens):
        filtered -= _FORMAT_ACTION_CONTEXT_WORDS
        filtered.update(tokens & _FORMAT_SINGLE_LETTER_WORDS)
    if _clear_action_requested(tokens):
        filtered -= _CLEAR_ACTION_CONTEXT_WORDS
    if _zoom_action_requested(tokens):
        filtered -= _ZOOM_ACTION_CONTEXT_WORDS
    if _external_link_action_requested(tokens):
        filtered -= _EXTERNAL_LINK_CONTEXT_WORDS
    if _confirm_action_requested(tokens):
        filtered -= _CONFIRM_ACTION_CONTEXT_WORDS
    dialog_dismiss_tokens = _dialog_dismiss_action_tokens(tokens)
    if dialog_dismiss_tokens:
        filtered.update(dialog_dismiss_tokens)
    if _edit_action_requested(tokens):
        filtered.update({"edit", "pencil"})
    if filtered & {"left_arrow", "right_arrow"}:
        filtered.discard("arrow")
    context_tokens = filtered & _CONTEXT_LOCATION_WORDS
    if context_tokens and (tokens & _DEICTIC_WORDS or filtered - context_tokens):
        filtered -= context_tokens
    return expand_token_aliases(filtered)


def instruction_control_intents(instruction: str) -> set[str]:
    raw_tokens = tokens_from_text(instruction)
    intents: set[str] = set()
    checkbox_requested = (
        "checkbox" in raw_tokens
        or ("check" in raw_tokens and "box" in raw_tokens)
        or _checkbox_action_requested(raw_tokens)
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
    picker_launcher_requested = _picker_launcher_requested(raw_tokens)
    selector_requested = _selector_requested(raw_tokens)
    menu_launcher_requested = _menu_launcher_requested(raw_tokens)
    contextual_menu_launcher_requested = _contextual_menu_launcher_requested(raw_tokens)
    disclosure_requested = _disclosure_requested(raw_tokens)
    password_visibility_requested = _password_visibility_requested(raw_tokens)
    format_action_requested = _format_action_requested(raw_tokens)
    clear_action_requested = _clear_action_requested(raw_tokens)
    history_action_requested = bool(raw_tokens & _HISTORY_ACTION_WORDS)
    zoom_action_requested = _zoom_action_requested(raw_tokens)
    external_link_action_requested = _external_link_action_requested(raw_tokens)
    confirm_action_requested = _confirm_action_requested(raw_tokens)
    edit_action_requested = _edit_action_requested(raw_tokens)
    split_button_requested = "splitbutton" in raw_tokens or (
        "split" in raw_tokens and "button" in raw_tokens
    )
    address_bar_requested = "omnibox" in raw_tokens or (
        "bar" in raw_tokens and bool(raw_tokens & _ADDRESS_BAR_INTENT_WORDS)
    )
    search_bar_requested = "bar" in raw_tokens and bool(raw_tokens & _SEARCH_BAR_INTENT_WORDS)
    input_requested = (
        bool(raw_tokens & _INPUT_INTENT_WORDS)
        or address_bar_requested
        or search_bar_requested
        or _text_entry_action_requested(raw_tokens)
    )
    if checkbox_requested or toggle_requested or switch_requested:
        intents.add("checkbox")
    if radio_requested:
        intents.add("radiobutton")
    if raw_tokens & {"edit", "editable"} and not edit_action_requested:
        intents.update(EDIT_CONTROL_TYPES)
    if (
        not checkbox_requested
        and input_requested
        and not password_visibility_requested
        and not format_action_requested
        and not clear_action_requested
    ):
        intents.update(INPUT_CONTROL_TYPES)
    if raw_tokens & {"combo", "combobox"}:
        intents.add("combobox")
    if dropdown_requested:
        intents.update(_DROPDOWN_INTENT_TYPES)
    if picker_launcher_requested:
        intents.update(_PICKER_LAUNCHER_INTENT_TYPES)
    if selector_requested:
        intents.update(_SELECTOR_INTENT_TYPES)
    if "slider" in raw_tokens:
        intents.update(SLIDER_CONTROL_TYPES)
    if raw_tokens & {"spinner", "spinbox", "stepper"} or (
        "spin" in raw_tokens and "box" in raw_tokens
    ):
        intents.update(SPINNER_CONTROL_TYPES)
    if split_button_requested:
        intents.add("splitbutton")
    if password_visibility_requested:
        intents.update(_BUTTON_INTENT_TYPES)
    if format_action_requested:
        intents.update(_FORMAT_ACTION_INTENT_TYPES)
    if clear_action_requested:
        intents.update(_BUTTON_INTENT_TYPES)
    if history_action_requested:
        intents.update(_HISTORY_ACTION_INTENT_TYPES)
    if zoom_action_requested:
        intents.update(_ZOOM_ACTION_INTENT_TYPES)
    if external_link_action_requested:
        intents.update(_EXTERNAL_LINK_ACTION_INTENT_TYPES)
    if confirm_action_requested:
        intents.update(_CONFIRM_ACTION_INTENT_TYPES)
    if edit_action_requested:
        intents.update(_EDIT_ACTION_INTENT_TYPES)
    if (
        not checkbox_requested
        and not radio_requested
        and not split_button_requested
        and not password_visibility_requested
        and not format_action_requested
        and not clear_action_requested
        and not zoom_action_requested
        and not external_link_action_requested
        and not confirm_action_requested
        and not edit_action_requested
        and "button" in raw_tokens
    ):
        intents.update(_BUTTON_INTENT_TYPES)
    if "icon" in raw_tokens:
        intents.update(_ICON_INTENT_TYPES)
    if disclosure_requested:
        intents.update(_DISCLOSURE_INTENT_TYPES)
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
    if raw_tokens & _OPTION_INTENT_WORDS:
        intents.update(_OPTION_INTENT_TYPES)
    if raw_tokens & {"header", "heading"}:
        intents.add("headeritem")
    if "headeritem" in raw_tokens:
        intents.add("headeritem")
    if "menu" in raw_tokens and not (
        menu_launcher_requested or contextual_menu_launcher_requested
    ):
        intents.update(_MENU_INTENT_TYPES)
    if menu_launcher_requested or contextual_menu_launcher_requested:
        intents.update(_MENU_LAUNCHER_INTENT_TYPES)
    if "menuitem" in raw_tokens:
        intents.add("menuitem")
    return intents


def control_type_matches_intent(control_type: str, control_intents: set[str]) -> bool:
    return not control_intents or control_type in control_intents


def menu_segment_intent(control_intents: set[str]) -> bool:
    return "menuitem" in control_intents


def tokenize_control(text: str) -> set[str]:
    return expand_token_aliases(tokens_from_text(text) | control_phrase_tokens(text))


def tokens_from_text(text: str) -> set[str]:
    value = text or ""
    spaced = _CAMEL_RE.sub(" ", value)
    spaced = _SEPARATOR_RE.sub(" ", spaced)
    tokens = set(_TOKEN_RE.findall(spaced.lower()))
    phrase_text = _WHITESPACE_RE.sub(" ", spaced.lower()).strip()
    for pattern, additions, removals in _AUTH_DIRECTION_TOKEN_REWRITES:
        if pattern.search(phrase_text):
            tokens -= removals
            tokens.update(additions)
    for pattern, aliases in _PHRASE_TOKEN_ALIAS_PATTERNS:
        if pattern.search(phrase_text):
            tokens.update(aliases)
    if tokens:
        return tokens
    compact = _WHITESPACE_RE.sub("", value)
    symbol_tokens: set[str] = set()
    for symbol, aliases in _SYMBOL_TOKEN_ALIASES.items():
        if symbol in compact:
            symbol_tokens.update(aliases)
    return symbol_tokens


def control_phrase_tokens(text: str) -> set[str]:
    value = text or ""
    spaced = _CAMEL_RE.sub(" ", value)
    spaced = _SEPARATOR_RE.sub(" ", spaced)
    phrase_text = _WHITESPACE_RE.sub(" ", spaced.lower()).strip()
    tokens: set[str] = set()
    for pattern, aliases in _CONTROL_PHRASE_TOKEN_ALIAS_PATTERNS:
        if pattern.search(phrase_text):
            tokens.update(aliases)
    return tokens


def expand_token_aliases(tokens: set[str]) -> set[str]:
    expanded = set(tokens)
    for token in tokens:
        expanded.update(_TOKEN_ALIASES.get(token, set()))
    return expanded


def _text_entry_action_requested(raw_tokens: set[str]) -> bool:
    if not (raw_tokens & _TEXT_ENTRY_ACTION_WORDS):
        return False
    return not bool(raw_tokens & _TEXT_ENTRY_BLOCKING_WORDS)


def _checkbox_action_requested(raw_tokens: set[str]) -> bool:
    if "checkmark" in raw_tokens:
        return False
    if raw_tokens & _CHECKBOX_ACTION_WORDS:
        if "check" in raw_tokens and "for" in raw_tokens and "box" not in raw_tokens:
            return False
        return not bool(raw_tokens & _CHECKBOX_ACTION_BLOCKING_WORDS)
    if raw_tokens & _CHECKBOX_STATE_ACTION_WORDS:
        return not bool(raw_tokens & _CHECKBOX_ACTION_BLOCKING_WORDS)
    if "turn" in raw_tokens and raw_tokens & {"off", "on"}:
        return not bool(raw_tokens & _CHECKBOX_ACTION_BLOCKING_WORDS)
    return False


def _password_visibility_requested(raw_tokens: set[str]) -> bool:
    if not (raw_tokens & _PASSWORD_VISIBILITY_CONTEXT_WORDS):
        return False
    return bool(raw_tokens & _PASSWORD_VISIBILITY_ACTION_WORDS)


def _audio_output_control_requested(raw_tokens: set[str]) -> bool:
    return "audio" in raw_tokens and bool(raw_tokens & _AUDIO_OUTPUT_ACTION_WORDS)


def _media_control_requested(raw_tokens: set[str]) -> bool:
    if raw_tokens & _MEDIA_CONTROL_ACTION_WORDS:
        return True
    if raw_tokens & _MEDIA_RESUME_WORDS:
        return bool(raw_tokens & _MEDIA_CONTROL_CONTEXT_WORDS)
    if raw_tokens & _MEDIA_STOP_WORDS:
        return bool(raw_tokens & _MEDIA_CONTROL_CONTEXT_WORDS)
    return False


def _format_action_requested(raw_tokens: set[str]) -> bool:
    if raw_tokens & _FORMAT_ACTION_WORDS:
        return True
    letter_tokens = raw_tokens & _FORMAT_SINGLE_LETTER_WORDS
    if len(letter_tokens) != 1:
        return False
    return bool(raw_tokens & _FORMAT_SINGLE_LETTER_CONTEXT_WORDS)


def _clear_action_requested(raw_tokens: set[str]) -> bool:
    return bool(raw_tokens & _CLEAR_ACTION_WORDS)


def _zoom_action_requested(raw_tokens: set[str]) -> bool:
    return bool(raw_tokens & _ZOOM_ACTION_WORDS)


def _external_link_action_requested(raw_tokens: set[str]) -> bool:
    return bool(raw_tokens & _EXTERNAL_LINK_ACTION_WORDS)


def _confirm_action_requested(raw_tokens: set[str]) -> bool:
    return bool(raw_tokens & _CONFIRM_ACTION_WORDS)


def _dialog_dismiss_action_tokens(raw_tokens: set[str]) -> set[str]:
    if not (raw_tokens & _DIALOG_DISMISS_CONTEXT_WORDS):
        return set()
    meaningful_tokens = raw_tokens - _DIALOG_DISMISS_CONTEXT_WORDS - _INSTRUCTION_STOPWORDS
    if not (meaningful_tokens & _DIALOG_DISMISS_ACTION_WORDS):
        return set()
    if meaningful_tokens - _DIALOG_DISMISS_ACTION_WORDS:
        return set()
    return {"cancel", "close", "dismiss"}


def _edit_action_requested(raw_tokens: set[str]) -> bool:
    return "edit" in raw_tokens and bool(raw_tokens & _EDIT_ACTION_CONTEXT_WORDS)


def _menu_launcher_requested(raw_tokens: set[str]) -> bool:
    if "menuitem" in raw_tokens or ("menu" in raw_tokens and "item" in raw_tokens):
        return False
    if not ("menu" in raw_tokens or "options" in raw_tokens):
        return False
    if raw_tokens & _MENU_LAUNCHER_WORDS:
        return True
    return "three" in raw_tokens and bool(raw_tokens & {"dot", "dots"})


def _contextual_menu_launcher_requested(raw_tokens: set[str]) -> bool:
    if "menuitem" in raw_tokens or ("menu" in raw_tokens and "item" in raw_tokens):
        return False
    has_dropdown_wording = "dropdown" in raw_tokens or (
        "drop" in raw_tokens and "down" in raw_tokens
    )
    if "menu" not in raw_tokens and not has_dropdown_wording:
        return False
    return bool(raw_tokens & _CONTEXTUAL_MENU_LAUNCHER_WORDS)


def _disclosure_requested(raw_tokens: set[str]) -> bool:
    return bool(raw_tokens & _DISCLOSURE_INTENT_WORDS)


def _selector_requested(raw_tokens: set[str]) -> bool:
    if _picker_launcher_requested(raw_tokens):
        return False
    return bool(raw_tokens & _SELECTOR_INTENT_WORDS) and not bool(
        raw_tokens & _SELECTOR_BLOCKING_WORDS
    )


def _picker_launcher_requested(raw_tokens: set[str]) -> bool:
    if not (raw_tokens & _PICKER_LAUNCHER_WORDS):
        return False
    return bool(raw_tokens & _PICKER_LAUNCHER_CONTEXT_WORDS)
