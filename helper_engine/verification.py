"""Evidence checks. A screen changing is never sufficient proof of success."""
from __future__ import annotations

from .types import Observation, Outcome, Predicate


__all__ = ["evaluate"]


def evaluate(predicates: tuple[Predicate, ...], obs: Observation) -> tuple[Outcome, str]:
    if not predicates:
        return Outcome.UNCERTAIN, "No deterministic postcondition; inspect current state."
    blob: str | None = None
    unknown = False
    for predicate in predicates:
        if predicate.kind == "text_contains" and blob is None:
            # One shared haystack per call: evaluate() runs on every wait-loop
            # poll, so rebuilding and casefolding this blob per predicate is
            # pure redundant work over identical observation data.
            blob = _text_blob(obs)
        result = _evaluate_one(predicate, obs, blob)
        if result is False:
            return Outcome.FAILED, f"Not yet satisfied: {predicate.kind}."
        if result is None:
            unknown = True
    if unknown:
        return Outcome.UNCERTAIN, "Required evidence is unavailable or ambiguous."
    return Outcome.ACHIEVED, "All declared postconditions were observed."


def _text_blob(obs: Observation) -> str:
    """Casefolded haystack for text_contains: visible text plus control names/values."""
    return "\n".join([obs.text, *(e.name for e in obs.elements),
                      *(e.value for e in obs.elements if e.value is not None and not e.password)]).casefold()


def _evaluate_one(p: Predicate, obs: Observation, blob: str | None) -> bool | None:
    if p.kind == "url_contains":
        return p.value in obs.url if obs.url else None
    if p.kind == "title_contains":
        return p.value.casefold() in obs.title.casefold()
    if p.kind == "text_contains":
        assert blob is not None  # built once in evaluate() before the first text_contains
        # Missing text in a truncated accessibility snapshot is not proof of absence.
        return True if (p.value or "").casefold() in blob else None
    matches = [e for e in obs.elements if e.visible and e.name.casefold() == (p.name or "").casefold()
               and (not p.role or e.role.casefold() == (p.role or "").casefold())]
    if p.kind == "exists":
        return True if matches else None
    if p.kind == "absent":
        # The adapters deliberately return bounded snapshots; absence needs visual verification.
        return False if matches else None
    if len(matches) != 1:
        return None
    element = matches[0]
    if p.kind == "value_equals":
        return element.value == p.value if element.value is not None and not element.password else None
    if p.kind == "focused":
        return element.focused
    if p.kind == "checked":
        return element.checked == (p.value == "true") if element.checked is not None else None
    return None
