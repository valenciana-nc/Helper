"""One policy boundary for all action routes, including native UI patterns."""
from __future__ import annotations

import re
from urllib.parse import urlsplit

from .types import Action, Observation


COMMIT_WORDS = re.compile(r"\b(send|submit|purchase|buy|pay|delete|erase|publish|transfer|shutdown|install)\b", re.I)
TERMINALS = re.compile(r"\b(powershell|terminal|cmd\.exe|command prompt|bash|wsl)\b", re.I)


def confirmation_reason(action: Action, obs: Observation) -> str:
    if action.requires_confirmation:
        return "This action requests confirmation."
    target = obs.element(action.target.element_id) if action.target and action.target.element_id else None
    if target and target.password:
        return "This field contains credentials."
    labels = " ".join([action.description, target.name if target else ""])
    if COMMIT_WORDS.search(labels):
        return "This action may send, publish, spend, install, or delete."
    if action.kind == "press":
        keys = {key.casefold() for key in action.keys}
        if keys & {"enter", "return", "delete", "del"} and TERMINALS.search(obs.title):
            return "This key would commit input in a terminal."
        if keys & {"delete", "del"}:
            return "This key may delete the selected item."
    if action.kind == "open":
        parsed = urlsplit(action.text)
        if parsed.scheme and parsed.scheme.casefold() not in {"http", "https", "ms-settings"}:
            return "This launch target uses a non-web protocol."
    return ""
