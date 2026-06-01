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

_SYMBOL_TOKEN_ALIASES = {
    "?": {"help", "mark", "question"},
    "+": {"add", "create", "new", "plus"},
    "...": {"dot", "dots", "ellipsis", "menu", "more", "options"},
    "\u00d7": {"close", "dismiss", "x"},
    "\u2026": {"dot", "dots", "ellipsis", "menu", "more", "options"},
    "\u22ee": {"dot", "dots", "kebab", "menu", "more", "options"},
    "\u22ef": {"dot", "dots", "ellipsis", "menu", "more", "options"},
    "\u2699": {"cog", "gear", "options", "preferences", "settings"},
    "\u2715": {"close", "dismiss", "x"},
    "\u2716": {"close", "dismiss", "x"},
    "\u2605": {"bookmark", "favorite", "star"},
    "\u2606": {"bookmark", "favorite", "star"},
    "\u2661": {"favorite", "heart"},
    "\u2665": {"favorite", "heart"},
    "\U0001f50d": {"find", "lens", "magnifier", "magnifying", "search"},
    "\U0001f50e": {"find", "lens", "magnifier", "magnifying", "search"},
}

_TOKEN_ALIASES = {
    "account": {"profile", "user"},
    "address": {"location", "url"},
    "arrow": {"caret", "chevron", "collapse", "disclosure", "expand"},
    "avatar": {"account", "profile", "user"},
    "back": {"previous"},
    "attach": {"attachment", "browse", "choose", "file", "upload"},
    "attachment": {"attach", "browse", "choose", "file", "upload"},
    "bookmark": {"favorite", "star"},
    "browse": {"choose", "file", "files", "select", "upload"},
    "caret": {"arrow", "chevron", "collapse", "disclosure", "expand"},
    "chevron": {"arrow", "caret", "collapse", "disclosure", "expand"},
    "choose": {"browse", "file", "select", "upload"},
    "clone": {"copy", "duplicate"},
    "cog": {"options", "preferences", "settings"},
    "collapse": {"arrow", "caret", "chevron", "disclosure"},
    "confirm": {"ok", "okay"},
    "continue": {"next", "proceed"},
    "copy": {"clone", "duplicate"},
    "dismiss": {"close"},
    "disclosure": {"arrow", "caret", "chevron", "collapse", "expand"},
    "document": {"file"},
    "documents": {"file", "files"},
    "download": {"export"},
    "duplicate": {"clone", "copy"},
    "expand": {"arrow", "caret", "chevron", "disclosure"},
    "export": {"download"},
    "favorite": {"bookmark", "star"},
    "file": {"attach", "attachment", "browse", "choose", "document", "select", "upload"},
    "files": {"attach", "attachment", "browse", "choose", "documents", "file", "select", "upload"},
    "find": {"search"},
    "gear": {"options", "preferences", "settings"},
    "lens": {"find", "search"},
    "location": {"address", "url"},
    "log": {"login", "sign", "signin"},
    "login": {"log", "sign", "signin"},
    "magnifier": {"find", "search"},
    "magnifying": {"find", "search"},
    "kebab": {"menu", "more", "options"},
    "menu": {"more", "options"},
    "meatballs": {"menu", "more", "options"},
    "more": {"menu", "options"},
    "next": {"continue", "proceed"},
    "ok": {"confirm", "okay"},
    "okay": {"confirm", "ok"},
    "omnibox": {"address", "search", "url"},
    "options": {"preferences", "settings"},
    "overflow": {"menu", "more", "options"},
    "preferences": {"options", "settings"},
    "previous": {"back"},
    "proceed": {"continue", "next"},
    "profile": {"account", "user"},
    "refresh": {"reload"},
    "remove": {"delete"},
    "reload": {"refresh"},
    "search": {"find"},
    "select": {"browse", "choose", "file", "upload"},
    "settings": {"options", "preferences"},
    "sign": {"log", "login", "signin"},
    "signin": {"log", "login", "sign"},
    "star": {"bookmark", "favorite"},
    "import": {"upload"},
    "upload": {"attach", "attachment", "browse", "choose", "file", "import", "select"},
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


def tokenize_instruction(instruction: str) -> set[str]:
    tokens = tokens_from_text(instruction)
    filtered = {
        token for token in tokens if token not in _INSTRUCTION_STOPWORDS and len(token) > 1
    }
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
    if raw_tokens & {"edit", "editable"}:
        intents.update(EDIT_CONTROL_TYPES)
    if not checkbox_requested and input_requested:
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
    if (
        not checkbox_requested
        and not radio_requested
        and not split_button_requested
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
    return expand_token_aliases(tokens_from_text(text))


def tokens_from_text(text: str) -> set[str]:
    value = text or ""
    spaced = _CAMEL_RE.sub(" ", value)
    spaced = _SEPARATOR_RE.sub(" ", spaced)
    tokens = set(_TOKEN_RE.findall(spaced.lower()))
    if tokens:
        return tokens
    compact = _WHITESPACE_RE.sub("", value)
    symbol_tokens: set[str] = set()
    for symbol, aliases in _SYMBOL_TOKEN_ALIASES.items():
        if symbol in compact:
            symbol_tokens.update(aliases)
    return symbol_tokens


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
    if raw_tokens & _CHECKBOX_ACTION_WORDS:
        if "check" in raw_tokens and "for" in raw_tokens and "box" not in raw_tokens:
            return False
        return not bool(raw_tokens & _CHECKBOX_ACTION_BLOCKING_WORDS)
    if raw_tokens & _CHECKBOX_STATE_ACTION_WORDS:
        return not bool(raw_tokens & _CHECKBOX_ACTION_BLOCKING_WORDS)
    if "turn" in raw_tokens and raw_tokens & {"off", "on"}:
        return not bool(raw_tokens & _CHECKBOX_ACTION_BLOCKING_WORDS)
    return False


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
