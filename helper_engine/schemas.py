"""Model output contracts. Strict provider schemas supplement, never replace, local validation."""
from __future__ import annotations

from copy import deepcopy


def _object(properties: dict) -> dict:
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


def _text(limit: int) -> dict:
    return {"type": "string", "maxLength": limit}


def _nullable(schema: dict) -> dict:
    return {"anyOf": [schema, {"type": "null"}]}


BOX = {"type": "array", "items": {"type": "number", "minimum": 0, "maximum": 1000},
       "minItems": 4, "maxItems": 4}
TARGET = {"anyOf": [
    _object({"element_id": {"type": "string", "minLength": 1, "maxLength": 200}}),
    _object({"box": BOX, "confidence": {"type": "number", "minimum": .85, "maximum": 1}}),
]}
PREDICATE = _object({
    "kind": {"type": "string", "enum": ["exists", "absent", "value_equals", "focused", "checked",
                                        "text_contains", "url_contains", "title_contains"]},
    "name": _text(500), "role": _text(80), "value": _text(4000),
})
ACTION = _object({
    "kind": {"type": "string", "enum": ["click", "double_click", "right_click", "fill", "press", "scroll", "drag", "open"]},
    "target": _nullable(TARGET), "text": _text(12000),
    "keys": {"type": "array", "items": {"type": "string", "minLength": 1}, "maxItems": 5},
    "amount": {"type": "integer", "minimum": -2400, "maximum": 2400},
    "destination": _nullable(BOX),
    "expect": {"type": "array", "items": PREDICATE, "maxItems": 8},
    "requires_confirmation": {"type": "boolean"}, "description": _text(500),
})
DECISION = _object({
    "kind": {"type": "string", "enum": ["act", "observe", "finish", "answer", "ask"]},
    "observation_id": {"type": "string", "minLength": 1, "maxLength": 200},
    "message": _text(2000), "action": _nullable(ACTION),
    "previous_outcome": {"type": "string", "enum": ["achieved", "failed", "uncertain"]},
    "evidence": _text(2000),
    "milestones": {"type": "array", "items": _text(300), "maxItems": 12},
    "wait_ms": {"type": "integer", "minimum": 0, "maximum": 5000},
    "continue_task": {"type": "boolean"},
})
VERIFICATION = _object({
    "satisfied": {"type": "boolean"}, "evidence": _text(2000),
    "missing": {"type": "array", "items": _text(500), "maxItems": 20},
})


def decision_schema(observation) -> dict:
    schema = deepcopy(DECISION)
    schema["properties"]["observation_id"]["enum"] = [observation.id]
    marks = [element.id for element in observation.elements[:100] if element.enabled and element.visible]
    if marks:
        action = schema["properties"]["action"]["anyOf"][0]
        target = action["properties"]["target"]["anyOf"][0]
        target["anyOf"][0]["properties"]["element_id"]["enum"] = marks
    return schema
