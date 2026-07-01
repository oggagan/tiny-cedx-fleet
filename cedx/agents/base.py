"""Base agent: a named, role-typed unit with a declared call surface.

Every agent in the fleet subclasses this. The important properties for the "real
fleet, not a god-function" requirement:
  * name + role + prompt_version identify the agent in the roster and in traces.
  * can_call is the declared, typed list of agents this agent is permitted to invoke;
    the orchestrator is the only one with a non-empty can_call.
  * run() takes exactly one typed input contract and returns exactly one typed output
    contract. Passing anything else is a programming error, surfaced immediately.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class AgentSpec:
    name: str
    role: str  # orchestrator | worker | verifier | router | operator | other
    models: list[str]
    prompt_version: str
    can_call: list[str]

    def to_roster_entry(self) -> dict:
        return {
            "name": self.name,
            "role": self.role,
            "models": self.models,
            "prompt_version": self.prompt_version,
            "can_call": self.can_call,
        }


class Agent:
    spec: AgentSpec

    @property
    def name(self) -> str:
        return self.spec.name

    def _check_input(self, value, contract) -> None:
        if not isinstance(value, contract):
            raise TypeError(
                f"{self.spec.name} expected {contract.__name__}, got {type(value).__name__}"
            )
