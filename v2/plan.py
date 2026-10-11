"""Planner: turn a goal into a subgoal DAG with one LLM call.

The planner sees the annotated screenshot (set-of-marks) plus the element
name list, and returns an ordered list of subgoals, each with explicit
success criteria the verifier can check later.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Protocol


class LlmPort(Protocol):
    def chat(
        self,
        messages: list[dict],
        *,
        system: str | None = None,
        max_tokens: int = 1024,
    ) -> str: ...


@dataclass
class Subgoal:
    id: str
    description: str       # e.g. 'Click the "Save" button'
    success_criteria: str  # e.g. 'Save dialog is visible'
    target_hint: str | None = None  # e.g. 'button named Save' / 'mark 12'
    done: bool = False


@dataclass
class Plan:
    goal: str
    subgoals: list[Subgoal] = field(default_factory=list)

    @property
    def done(self) -> bool:
        return bool(self.subgoals) and all(s.done for s in self.subgoals)


PLANNER_SYSTEM = """You are a planner for a desktop computer-use agent. Decompose the user's goal into a short ordered list of concrete subgoals.

You see an annotated screenshot where interactive controls have numbered badges (marks). Prefer referring to targets by mark number (e.g. "mark 12") or by exact visible name.

Rules:
- 1 to 8 subgoals. Each subgoal is ONE atomic interaction (one click, one text entry, one keypress).
- Every subgoal needs "success_criteria": a directly observable on-screen fact the verifier can check (e.g. "Save dialog is visible", "text 'hello' appears in the document").
- If the goal is already complete, return an empty subgoals list.
- Reply with ONLY a JSON array, no prose. Each item: {"description": ..., "success_criteria": ..., "target_hint": ... (optional)}.
"""


def make_plan(
    goal: str,
    llm: LlmPort,
    annotated_png: bytes,
    element_names: list[str],
    *,
    max_tokens: int = 1024,
) -> Plan:
    """Plan once. Single LLM call; raises PlanError on unparseable output."""
    import base64

    names = "\n".join(f"- {n}" for n in element_names[:80])
    user_text = (
        f"Goal: {goal}\n\nVisible controls:\n{names}\n\n"
        "Decompose into subgoals (JSON array only)."
    )
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": user_text},
                {
                    "type": "image_url",
                    "image_url": {
                        "url": "data:image/png;base64,"
                        + base64.b64encode(annotated_png).decode("ascii")
                    },
                },
            ],
        }
    ]
    raw = llm.chat(messages, system=PLANNER_SYSTEM, max_tokens=max_tokens)
    items = _parse_json_array(raw)
    subgoals = [
        Subgoal(
            id=f"sg{i + 1}",
            description=str(it.get("description", "")).strip(),
            success_criteria=str(it.get("success_criteria", "")).strip(),
            target_hint=(str(it.get("target_hint")).strip() or None)
            if it.get("target_hint")
            else None,
        )
        for i, it in enumerate(items)
        if str(it.get("description", "")).strip()
    ]
    return Plan(goal=goal, subgoals=subgoals)


def _parse_json_array(raw: str) -> list[dict]:
    text = raw.strip()
    # Tolerate code fences.
    m = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", text, re.S)
    if m:
        text = m.group(1)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise PlanError(f"planner returned unparseable JSON: {exc}") from exc
    if not isinstance(data, list):
        raise PlanError("planner JSON was not an array")
    return [it for it in data if isinstance(it, dict)]


class PlanError(Exception):
    pass
