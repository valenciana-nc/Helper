"""Validate model output as data. No action is inferred from natural language."""
from __future__ import annotations

import json
import math

from .types import Action, Decision, Outcome, Predicate, ProtocolError, Target


ACTION_KINDS = frozenset({"click", "double_click", "right_click", "fill", "press", "scroll", "drag", "open"})
PREDICATES = frozenset({
    "exists", "absent", "value_equals", "text_contains", "url_contains", "title_contains", "focused", "checked",
})


def json_object(raw: str) -> dict:
    text = raw.strip()
    fence = chr(96) * 3
    if text.startswith(fence):
        lines = text.splitlines()
        if len(lines) < 3 or lines[-1].strip() != fence:
            raise ProtocolError("Unclosed JSON fence")
        text = "\n".join(lines[1:-1])
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ProtocolError(f"Duplicate JSON key: {key}")
            result[key] = value
        return result
    try:
        data = json.loads(text, object_pairs_hook=unique)
    except (ValueError, TypeError) as exc:
        raise ProtocolError("Response must be one JSON object") from exc
    if not isinstance(data, dict):
        raise ProtocolError("Response must be an object")
    return data


def _fields(data: dict, allowed: set[str]) -> None:
    extra = data.keys() - allowed
    if extra:
        raise ProtocolError(f"Unknown fields: {', '.join(sorted(extra))}")


def _text(data: dict, key: str, *, limit: int = 12000, required: bool = False) -> str:
    value = data.get(key, "")
    if not isinstance(value, str) or len(value) > limit:
        raise ProtocolError(f"{key} must be text of at most {limit} characters")
    if required and not value.strip():
        raise ProtocolError(f"{key} is required")
    return value


def _box(value) -> tuple[float, float, float, float]:
    if not isinstance(value, list) or len(value) != 4:
        raise ProtocolError("box must be [x,y,width,height], normalized to 0..1000")
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in value):
        raise ProtocolError("box values must be finite numbers")
    x, y, w, h = value
    if x < 0 or y < 0 or w <= 0 or h <= 0 or x + w > 1000 or y + h > 1000:
        raise ProtocolError("box is outside the screenshot")
    return tuple(float(v) for v in value)


def parse_predicate(data) -> Predicate:
    if not isinstance(data, dict):
        raise ProtocolError("expect entries must be objects")
    _fields(data, {"kind", "name", "role", "value"})
    kind = _text(data, "kind", required=True)
    if kind not in PREDICATES:
        raise ProtocolError(f"Unknown predicate: {kind}")
    pred = Predicate(kind, _text(data, "name", limit=500), _text(data, "role", limit=80),
                     _text(data, "value", limit=4000))
    if kind in {"exists", "absent", "value_equals", "focused", "checked"} and not pred.name:
        raise ProtocolError(f"{kind} requires an exact element name")
    if kind in {"text_contains", "url_contains", "title_contains"} and not pred.value:
        raise ProtocolError(f"{kind} requires a nonempty value")
    if kind == "checked" and pred.value not in {"true", "false"}:
        raise ProtocolError("checked value must be 'true' or 'false'")
    return pred


def parse_action(data) -> Action:
    if not isinstance(data, dict):
        raise ProtocolError("act requires one action object")
    _fields(data, {"kind", "target", "text", "keys", "amount", "destination",
                   "expect", "requires_confirmation", "description"})
    kind = _text(data, "kind", required=True)
    if kind not in ACTION_KINDS:
        raise ProtocolError(f"Unsupported action: {kind}")
    target = None
    if "target" in data:
        raw = data["target"]
        if not isinstance(raw, dict):
            raise ProtocolError("target must be an object")
        _fields(raw, {"element_id", "box", "confidence"})
        eid = _text(raw, "element_id", limit=200)
        box = _box(raw["box"]) if "box" in raw else None
        if bool(eid) == (box is not None):
            raise ProtocolError("target needs exactly one of element_id or box")
        confidence = raw.get("confidence", 1.0 if eid else 0)
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
            raise ProtocolError("confidence must be a number")
        if not math.isfinite(confidence) or not 0 <= confidence <= 1:
            raise ProtocolError("confidence must be in 0..1")
        if box is not None and confidence < 0.85:
            raise ProtocolError("Visual targets require confidence >= 0.85; observe or ask instead")
        target = Target(eid, box, float(confidence))
    if kind in {"click", "double_click", "right_click", "fill", "drag"} and target is None:
        raise ProtocolError(f"{kind} requires a current target")
    text = _text(data, "text")
    if kind == "open" and not text:
        raise ProtocolError("open requires a URL or app name")
    keys = data.get("keys", [])
    if not isinstance(keys, list) or len(keys) > 5 or any(not isinstance(k, str) or not k for k in keys):
        raise ProtocolError("keys must contain at most five nonempty key names")
    if kind == "press" and not keys:
        raise ProtocolError("press requires keys")
    amount = data.get("amount", 0)
    if isinstance(amount, bool) or not isinstance(amount, int) or abs(amount) > 2400:
        raise ProtocolError("amount must be an integer between -2400 and 2400")
    if kind == "scroll" and amount == 0:
        raise ProtocolError("scroll requires a nonzero amount")
    destination = _box(data["destination"]) if "destination" in data else None
    if kind == "drag" and destination is None:
        raise ProtocolError("drag requires a destination box")
    expected = data.get("expect", [])
    if not isinstance(expected, list) or len(expected) > 8:
        raise ProtocolError("expect must be an array of at most eight predicates")
    confirmation = data.get("requires_confirmation", False)
    if not isinstance(confirmation, bool):
        raise ProtocolError("requires_confirmation must be boolean")
    if text and kind not in {"fill", "open"}:
        raise ProtocolError(f"{kind} does not accept text")
    if keys and kind != "press":
        raise ProtocolError(f"{kind} does not accept keys")
    return Action(kind, target, text, tuple(keys), amount, destination,
                  tuple(parse_predicate(p) for p in expected), confirmation,
                  _text(data, "description", limit=500))


def parse_decision(raw: str) -> Decision:
    data = json_object(raw)
    _fields(data, {"kind", "observation_id", "message", "action", "previous_outcome",
                   "evidence", "milestones", "wait_ms"})
    kind = _text(data, "kind", required=True)
    if kind not in {"act", "observe", "finish", "answer", "ask"}:
        raise ProtocolError("Unknown decision kind")
    oid = _text(data, "observation_id", limit=200, required=True)
    action = parse_action(data.get("action")) if kind == "act" else None
    if kind != "act" and "action" in data:
        raise ProtocolError("Only act may contain an action")
    if kind in {"answer", "ask"} and not _text(data, "message").strip():
        raise ProtocolError(f"{kind} requires a nonempty message")
    try:
        previous = Outcome(data.get("previous_outcome", "uncertain"))
    except ValueError as exc:
        raise ProtocolError("Invalid previous_outcome") from exc
    milestones = data.get("milestones", [])
    if (not isinstance(milestones, list) or len(milestones) > 12
            or any(not isinstance(m, str) or len(m) > 300 for m in milestones)):
        raise ProtocolError("milestones must be at most twelve short strings")
    wait_ms = data.get("wait_ms", 200)
    if isinstance(wait_ms, bool) or not isinstance(wait_ms, int) or not 0 <= wait_ms <= 5000:
        raise ProtocolError("wait_ms must be in 0..5000")
    return Decision(kind, oid, _text(data, "message", limit=2000), action, previous,
                    _text(data, "evidence", limit=2000), tuple(milestones), wait_ms)
