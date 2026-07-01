"""Typed contracts exchanged between agents.

These dataclasses ARE the typed handoff contracts the task requires: each agent
declares the concrete input type it accepts and the output type it returns, and
the base Agent enforces the types at the boundary. Free-form dict passing is
deliberately avoided so the fleet is a set of separable, individually testable
units rather than one god-function.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

# ---- reason codes -----------------------------------------------------------

# Data-layer Class-A (blocking) codes.
CLASS_A = {
    "STALE",
    "MISSING_INPUT",
    "OUTLIER",
    "INJECTION_BLOCKED",
    "LOW_CONFIDENCE",
    "UNVERIFIED_ANOMALY",
}
# Agent-layer failure codes (also blocking, caught by the Verifier / Orchestrator).
AGENT_FAIL = {"AGENT_HALLUCINATION", "AGENT_LOOP", "AGENT_MALFORMED", "BUDGET_EXCEEDED"}
# Class-B: auto-resolved and logged, still delivered.
CLASS_B = {"SCHEMA_DRIFT", "SUPERSEDED_VERSION"}

BLOCKING = CLASS_A | AGENT_FAIL


def reason_class(code: Optional[str]) -> Optional[str]:
    if code in CLASS_A or code in AGENT_FAIL:
        return "A"
    if code in CLASS_B:
        return "B"
    return None


# ---- canonical record (post intake + normalize) -----------------------------


@dataclass
class NormalizedRecord:
    """One work-request after intake + declarative normalization."""

    id: str
    version: int
    owner: Optional[str]
    deadline: Optional[str]  # ISO date
    category: Optional[str]
    notes: str
    amount: Optional[float]  # the primary numeric field
    source_format: str  # feed | eml | pdf
    source_version_hash: str
    raw: dict = field(default_factory=dict)
    drift_fields: list[str] = field(default_factory=list)  # canonical fields recovered via SCHEMA_DRIFT

    def key(self) -> str:
        return f"{self.id}@v{self.version}"


# ---- Worker contract --------------------------------------------------------


@dataclass
class WorkerInput:
    record: NormalizedRecord
    model: str  # actual model name resolved by the router
    tier: str  # "cheap" | "strong"
    reason: str = ""  # why the orchestrator picked that tier


@dataclass
class WorkerOutput:
    record_id: str
    delivered_fields: Optional[dict]  # the branded structured output, or None on abstain
    abstained: bool
    confidence: float
    model: str
    prompt_version: str
    tokens_in: int
    tokens_out: int
    cost_usd: float
    latency_ms: float
    transcript_hash: Optional[str]  # sha of the raw LLM response (load-bearing anchor)
    retries: int = 0
    malformed: bool = False


# ---- Verifier contract ------------------------------------------------------


@dataclass
class VerifierInput:
    record: NormalizedRecord
    worker_output: WorkerOutput


@dataclass
class VerifierOutput:
    record_id: str
    verdict: str  # pass | fail | needs_human
    reason_code: Optional[str]  # AGENT_HALLUCINATION | AGENT_MALFORMED | LOW_CONFIDENCE | None
    detail: str
    model: str
    prompt_version: str
    tokens_in: int
    tokens_out: int
    cost_usd: float
    latency_ms: float
    transcript_hash: Optional[str] = None


# ---- Orchestrator result ----------------------------------------------------


@dataclass
class ProcessedRecord:
    """Everything the orchestrator produced for one record — the audit row source."""

    record: NormalizedRecord
    status: str  # delivered | exception | superseded
    reason_code: Optional[str]
    spans: list[dict] = field(default_factory=list)  # agent_trace spans
    approval_trail: list[dict] = field(default_factory=list)
    delivered_fields: Optional[dict] = None
    delivered_fields_hash: Optional[str] = None
    transcript_hash: Optional[str] = None
    cost_usd: float = 0.0
    latency_ms: float = 0.0
