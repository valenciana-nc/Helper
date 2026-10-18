"""Small integration contracts; the core has no Qt or Windows dependencies."""
from __future__ import annotations

from typing import Callable, Protocol

from .types import Action, Decision, Execution, Observation


class Driver(Protocol):
    def observe(self) -> Observation: ...
    def validate(self, action: Action, observation: Observation) -> None: ...
    def execute(self, action: Action, observation: Observation) -> Execution: ...
    def close(self) -> None: ...


class Model(Protocol):
    def decide(self, goal: str, observation: Observation, context: dict) -> Decision: ...
    def verify_goal(self, goal: str, observation: Observation, context: dict) -> tuple[bool, str]: ...


EventSink = Callable[[str, dict], None]
Confirmation = Callable[[Action, Observation, str], bool]
