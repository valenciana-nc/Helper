"""One policy boundary for all action routes, including native UI patterns."""
from __future__ import annotations

import re
from urllib.parse import urlsplit

from .types import Action, Observation


COMMIT_WORDS = re.compile(r"\b(send|submit|purchase|buy|pay|delete|remove|erase|uninstall|publish|transfer|shutdown|install|format)\b", re.I)
TERMINALS = re.compile(r"\b(powershell|terminal|cmd\.exe|command prompt|bash|wsl)\b", re.I)


__all__ = ["COMMIT_WORDS", "TERMINALS", "confirmation_reason"]


def confirmation_reason(action: Action, obs: Observation) -> str:
    if action.requires_confirmation:
        return "This action requests confirmation."
    target = obs.element(action.target.element_id) if action.target and action.target.element_id else None
    if target is None and action.kind == "press":
        focused = [element for element in obs.elements if element.focused]
        target = focused[0] if len(focused) == 1 else None
    if target and target.password:
        return "This field contains credentials."
    # Filling a normal field prepares a draft/query; it does not itself click a commit button.
    if action.kind == "fill":
        return ""
    labels = " ".join([action.description, target.name if target else ""])
    commits = {match.group().casefold() for match in COMMIT_WORDS.finditer(labels)}
    search_control = target and target.name.strip().casefold() in {
        "search", "search youtube", "search google", "submit search", "search button", "find",
    }
    if commits and not (commits == {"submit"} and search_control):
        return "This action may send, publish, spend, install, or delete."
    if action.kind == "press":
        keys = {key.casefold() for key in action.keys}
        if keys & {"enter", "return", "delete", "del"} and TERMINALS.search(obs.title):
            return "This key would commit input in a terminal."
        if keys & {"delete", "del"}:
            return "This key may delete the selected item."
    if action.kind == "open":
        try:
            parsed = urlsplit(action.text)
        except ValueError:
            # Unparseable, not risky: the driver rejects it without side effects.
            return ""
        if parsed.scheme and parsed.scheme.casefold() not in {"http", "https", "ms-settings"}:
            return "This launch target uses a non-web protocol."
    return ""
