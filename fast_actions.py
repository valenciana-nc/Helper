from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import quote_plus


@dataclass(frozen=True)
class FastAction:
    name: str
    summary: str
    raw_args: dict[str, object] = field(default_factory=dict)
    keys: str | None = None


APP_ALIASES = {
    "chrome": ("chrome", "Chrome"),
    "google chrome": ("chrome", "Chrome"),
    "browser": ("", "the browser"),
    "web browser": ("", "the browser"),
    "edge": ("msedge", "Microsoft Edge"),
    "microsoft edge": ("msedge", "Microsoft Edge"),
    "notepad": ("notepad", "Notepad"),
    "calculator": ("calc", "Calculator"),
    "calc": ("calc", "Calculator"),
    "explorer": ("explorer", "File Explorer"),
    "file explorer": ("explorer", "File Explorer"),
    "settings": ("ms-settings:", "Settings"),
    "task manager": ("taskmgr", "Task Manager"),
    "terminal": ("wt", "Windows Terminal"),
    "windows terminal": ("wt", "Windows Terminal"),
    "powershell": ("powershell", "PowerShell"),
}

SITE_ALIASES = {
    "youtube": "https://www.youtube.com",
    "google": "https://www.google.com",
    "gmail": "https://mail.google.com",
    "chatgpt": "https://chatgpt.com",
    "openai": "https://openai.com",
    "github": "https://github.com",
    "reddit": "https://www.reddit.com",
    "x": "https://x.com",
    "twitter": "https://x.com",
    "maps": "https://www.google.com/maps",
}

HOTKEY_ALIASES = {
    "new tab": "ctrl+t",
    "close tab": "ctrl+w",
    "reopen tab": "ctrl+shift+t",
    "next tab": "ctrl+tab",
    "previous tab": "ctrl+shift+tab",
    "prev tab": "ctrl+shift+tab",
    "focus address bar": "ctrl+l",
    "address bar": "ctrl+l",
    "refresh": "ctrl+r",
    "reload": "ctrl+r",
    "copy": "ctrl+c",
    "paste": "ctrl+v",
    "cut": "ctrl+x",
    "select all": "ctrl+a",
    "undo": "ctrl+z",
    "redo": "ctrl+y",
    "save": "ctrl+s",
}

FAST_LAUNCH_PREFIXES = ("open", "launch", "start", "run")
POLITE_PREFIXES = ("please ", "can you ", "could you ", "will you ")
DESTRUCTIVE_CONTROL_WORDS = ("delete", "remove", "send", "buy", "purchase", "pay", "confirm")
GENERIC_CONTROL_LABELS = {
    "button",
    "the button",
    "link",
    "field",
    "box",
    "it",
    "that",
    "this",
    "there",
    "highlighted",
    "highlighted location",
}


def parse_fast_action(user_text: str) -> FastAction | None:
    text = _normalize(user_text)
    if not text:
        return None

    action = _parse_hotkey_alias(text)
    if action is not None:
        return action

    action = _parse_press_key(text)
    if action is not None:
        return action

    action = _parse_search(text)
    if action is not None:
        return action

    action = _parse_navigation(text)
    if action is not None:
        return action

    action = _parse_launch(text)
    if action is not None:
        return action

    action = _parse_click_control(text)
    if action is not None:
        return action

    return None


def _normalize(user_text: str) -> str:
    text = " ".join((user_text or "").strip().lower().split())
    for prefix in POLITE_PREFIXES:
        if text.startswith(prefix):
            text = text.removeprefix(prefix).strip()
    if text.endswith(" please"):
        text = text.removesuffix(" please").strip()
    return text


def _parse_launch(text: str) -> FastAction | None:
    for prefix in FAST_LAUNCH_PREFIXES:
        marker = prefix + " "
        if not text.startswith(marker):
            continue
        target = text.removeprefix(marker).strip().removeprefix("the ").strip()
        if target in SITE_ALIASES:
            return _navigate_action(SITE_ALIASES[target], f"Open {target.title()}.")
        if target in APP_ALIASES:
            command, display_name = APP_ALIASES[target]
            action_name = "open_web_browser" if not command else "launch_app"
            return FastAction(
                name=action_name,
                summary=f"Open {display_name}.",
                raw_args={
                    "app": target,
                    "command": command,
                    "display_name": display_name,
                    "fast_local": True,
                },
            )
        if _looks_like_url(target):
            return _navigate_action(_ensure_url(target), f"Open {target}.")
    return None


def _parse_navigation(text: str) -> FastAction | None:
    for prefix in ("go to ", "navigate to ", "open website ", "open site "):
        if text.startswith(prefix):
            target = text.removeprefix(prefix).strip()
            if target in SITE_ALIASES:
                return _navigate_action(SITE_ALIASES[target], f"Open {target.title()}.")
            if _looks_like_url(target):
                return _navigate_action(_ensure_url(target), f"Open {target}.")
    return None


def _parse_search(text: str) -> FastAction | None:
    match = re.match(r"search youtube for (.+)", text)
    if match:
        query = match.group(1).strip()
        return _navigate_action(
            f"https://www.youtube.com/results?search_query={quote_plus(query)}",
            f"Search YouTube for {query}.",
        )

    for prefix in ("search for ", "search ", "google "):
        if text.startswith(prefix):
            query = text.removeprefix(prefix).strip()
            if query:
                return _navigate_action(
                    f"https://www.google.com/search?q={quote_plus(query)}",
                    f"Search Google for {query}.",
                )

    return None


def _parse_hotkey_alias(text: str) -> FastAction | None:
    if text in HOTKEY_ALIASES:
        keys = HOTKEY_ALIASES[text]
        return _key_action(keys, f"Press {keys}.")
    return None


def _parse_press_key(text: str) -> FastAction | None:
    for prefix in ("press ", "hit "):
        if not text.startswith(prefix):
            continue
        keys = text.removeprefix(prefix).strip()
        keys = keys.replace("control", "ctrl").replace(" plus ", "+").replace(" ", "+")
        if not re.fullmatch(r"[a-z0-9+_-]+", keys):
            return None
        if keys in {"enter", "return"}:
            return None
        return _key_action(keys, f"Press {keys}.")
    return None


def _parse_click_control(text: str) -> FastAction | None:
    for prefix in ("click ", "press button ", "select "):
        if not text.startswith(prefix):
            continue
        label = text.removeprefix(prefix).strip()
        label = label.removeprefix("the ").strip()
        if (
            not label
            or label in GENERIC_CONTROL_LABELS
            or any(word in label for word in DESTRUCTIVE_CONTROL_WORDS)
        ):
            return None
        return FastAction(
            name="click_control",
            summary=f"Click {label}.",
            raw_args={"label": label, "fast_local": True},
        )
    return None


def _navigate_action(url: str, summary: str) -> FastAction:
    return FastAction(
        name="navigate",
        summary=summary,
        raw_args={"url": url, "fast_local": True},
    )


def _key_action(keys: str, summary: str) -> FastAction:
    return FastAction(
        name="key_combination",
        summary=summary,
        raw_args={"fast_local": True},
        keys=keys,
    )


def _looks_like_url(text: str) -> bool:
    return bool(
        text.startswith(("http://", "https://"))
        or re.fullmatch(r"[a-z0-9.-]+\.[a-z]{2,}(/.*)?", text)
    )


def _ensure_url(text: str) -> str:
    if text.startswith(("http://", "https://")):
        return text
    return "https://" + text
