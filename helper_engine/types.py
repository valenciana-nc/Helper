"""Immutable values shared by the model, runtime, and platform adapters."""
from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from enum import StrEnum
from functools import cached_property


class ProtocolError(ValueError):
    pass


class StaleObservation(RuntimeError):
    """Nothing was executed; the next decision needs a fresh observation."""


class Cancelled(RuntimeError):
    pass


__all__ = [
    "ProtocolError", "StaleObservation", "Cancelled",
    "Effect", "Outcome", "RunStatus",
    "Rect", "Element", "Observation",
    "Target", "Predicate", "Action", "Decision",
    "Execution", "Receipt", "Metrics", "RunReport",
]


class Effect(StrEnum):
    APPLIED = "applied"
    REJECTED = "rejected"
    UNKNOWN = "unknown"
    GUIDED = "guided"


class Outcome(StrEnum):
    ACHIEVED = "achieved"
    FAILED = "failed"
    UNCERTAIN = "uncertain"


class RunStatus(StrEnum):
    COMPLETED = "completed"
    ANSWERED = "answered"
    NEEDS_INPUT = "needs_input"
    FAILED = "failed"
    CANCELLED = "cancelled"
    LIMITED = "limited"


@dataclass(frozen=True)
class Rect:
    x: int
    y: int
    width: int
    height: int

    @property
    def center(self) -> tuple[int, int]:
        return self.x + self.width // 2, self.y + self.height // 2

    def as_list(self) -> list[int]:
        return [self.x, self.y, self.width, self.height]


@dataclass(frozen=True)
class Element:
    id: str
    name: str
    role: str
    rect: Rect
    value: str | None = None
    enabled: bool = True
    visible: bool = True
    focused: bool = False
    checked: bool | None = None
    password: bool = False
    capabilities: tuple[str, ...] = ()


@dataclass(frozen=True)
class Observation:
    id: str
    surface: str
    scope: str
    title: str
    png: bytes
    width: int
    height: int
    elements: tuple[Element, ...] = ()
    # Screenshot coordinates = (physical screen coordinates - origin) * scale.
    origin_x: int = 0
    origin_y: int = 0
    scale: float = 1.0
    url: str = ""
    text: str = ""
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.width < 1 or self.height < 1 or not math.isfinite(self.scale) or self.scale <= 0:
            raise ValueError("Invalid screenshot geometry")

    def element(self, element_id: str) -> Element | None:
        return next((e for e in self.elements if e.id == element_id), None)

    def screen_box(self, normalized: tuple[float, float, float, float]) -> Rect:
        x, y, w, h = normalized
        return Rect(
            round(self.origin_x + x * self.width / (1000 * self.scale)),
            round(self.origin_y + y * self.height / (1000 * self.scale)),
            max(1, round(w * self.width / (1000 * self.scale))),
            max(1, round(h * self.height / (1000 * self.scale))),
        )

    def image_box(self, rect: Rect) -> tuple[int, int, int, int]:
        x = round((rect.x - self.origin_x) * self.scale)
        y = round((rect.y - self.origin_y) * self.scale)
        right = round((rect.x + rect.width - self.origin_x) * self.scale)
        bottom = round((rect.y + rect.height - self.origin_y) * self.scale)
        # No intersection with the screenshot: return an empty box so callers
        # that check `right <= left` correctly treat it as invalid, instead of
        # a misleading 1-pixel sliver from clamping.
        if right <= 0 or bottom <= 0 or x >= self.width or y >= self.height:
            return (0, 0, 0, 0)
        return (
            max(0, min(self.width - 1, x)),
            max(0, min(self.height - 1, y)),
            max(0, min(self.width, right)),
            max(0, min(self.height, bottom)),
        )

    @cached_property
    def signature(self) -> str:
        # Memoized: a pure function of the frozen fields, but each access
        # costs a repr + sha256 over the whole element list. The runtime reads
        # before.signature on every wait-loop poll (up to ~257x per help-mode
        # step) even though that observation never changes, so recomputing it
        # every time is pure waste.
        # Include the full element state: a button becoming enabled, a field
        # gaining focus, or a checkbox toggling must all change the signature,
        # or the runtime's no-progress detection will miss the state change.
        semantics = [(e.id, e.name, e.role, e.value, e.enabled, e.visible,
                      e.focused, e.checked, e.password) for e in self.elements]
        return hashlib.sha256(repr((self.scope, self.url, semantics)).encode()).hexdigest()[:20]


@dataclass(frozen=True)
class Target:
    element_id: str = ""
    box: tuple[float, float, float, float] | None = None
    confidence: float = 1.0


@dataclass(frozen=True)
class Predicate:
    kind: str
    name: str = ""
    role: str = ""
    value: str = ""


@dataclass(frozen=True)
class Action:
    kind: str
    target: Target | None = None
    text: str = ""
    keys: tuple[str, ...] = ()
    amount: int = 0
    destination: tuple[float, float, float, float] | None = None
    expect: tuple[Predicate, ...] = ()
    requires_confirmation: bool = False
    description: str = ""

    def fingerprint(self, obs: Observation) -> str:
        el = obs.element(self.target.element_id) if self.target else None
        target = (el.name, el.role, el.rect) if el else self.target
        material = (obs.scope, self.kind, target, self.text, self.keys, self.amount, self.destination)
        return hashlib.sha256(repr(material).encode()).hexdigest()


@dataclass(frozen=True)
class Decision:
    kind: str
    observation_id: str
    message: str = ""
    action: Action | None = None
    previous_outcome: Outcome = Outcome.UNCERTAIN
    evidence: str = ""
    milestones: tuple[str, ...] = ()
    wait_ms: int = 200
    continue_task: bool = False


@dataclass(frozen=True)
class Execution:
    effect: Effect
    route: str
    detail: str = ""


@dataclass
class Receipt:
    id: str
    action: Action
    execution: Execution
    before_id: str
    after_id: str = ""
    outcome: Outcome = Outcome.UNCERTAIN
    evidence: str = ""


@dataclass
class Metrics:
    observations: int = 0
    model_calls: int = 0
    actions: int = 0
    verified_actions: int = 0
    stale_decisions: int = 0
    rejected_actions: int = 0
    clarification_reviews: int = 0
    screenshot_bytes: int = 0
    model_seconds: float = 0.0
    elapsed_seconds: float = 0.0
    routes: dict[str, int] = field(default_factory=dict)


@dataclass
class RunReport:
    goal: str
    status: RunStatus
    message: str
    receipts: list[Receipt] = field(default_factory=list)
    metrics: Metrics = field(default_factory=Metrics)

    @property
    def ok(self) -> bool:
        return self.status in {RunStatus.COMPLETED, RunStatus.ANSWERED}

    @property
    def done(self) -> list[str]:
        return [r.id for r in self.receipts if r.outcome == Outcome.ACHIEVED]
