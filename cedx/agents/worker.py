"""Worker agent - Stage 3 Assembly.

Drafts the branded work-order (delivered_fields) for one record via the LLM at the
model tier the router chose. It grounds the factual fields in the source and writes
one sentence of value-add summary. It abstains (rather than guessing) when the record
is ambiguous or underspecified, which surfaces as LOW_CONFIDENCE downstream.

It is a leaf agent: can_call is empty. It never decides delivery; that is the
Verifier's and the approval chain's job.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import jsonschema

from ..config import Config
from ..contracts import WorkerInput, WorkerOutput
from ..hashing import sha
from ..llm import LLMClient
from .base import Agent, AgentSpec

PROMPT_VERSION = "worker/v1"

_SYSTEM = (
    "You are the CEDX Assembly agent for a financial-services work-order pipeline. "
    "You convert one validated work-request into a branded work-order as STRICT JSON. "
    "Ground every factual field EXACTLY in the provided source record - copy record_id, "
    "client_owner (the owner), primary_amount_usd (the amount), and deadline verbatim; "
    "never invent or alter a value. engagement_category is the category in UPPER_SNAKE. "
    "currency is always \"USD\". summary is ONE professional sentence describing the work. "
    "If the record is ambiguous, self-contradictory, or missing the facts you need to be "
    "correct, DO NOT guess: set abstain=true and confidence below 0.5. "
    "Reply with ONLY this JSON object and no prose:\n"
    "{\"record_id\":str,\"client_owner\":str,\"engagement_category\":str,"
    "\"primary_amount_usd\":number,\"deadline\":str,\"currency\":\"USD\","
    "\"summary\":str,\"confidence\":number,\"abstain\":boolean}"
)

_DELIVERED_KEYS = [
    "record_id",
    "client_owner",
    "engagement_category",
    "primary_amount_usd",
    "deadline",
    "currency",
    "summary",
]


@lru_cache(maxsize=1)
def _output_schema(schema_dir_str: str) -> dict:
    return json.loads(
        (Path(schema_dir_str) / "output_schema.v1.json").read_text(encoding="utf-8")
    )


class WorkerAgent(Agent):
    def __init__(self, cfg: Config, llm: LLMClient):
        self.cfg = cfg
        self.llm = llm
        self.spec = AgentSpec(
            name="worker",
            role="worker",
            models=[cfg.model_cheap, cfg.model_strong],
            prompt_version=PROMPT_VERSION,
            can_call=[],
        )

    def _build_user(self, rec) -> str:
        source = {
            "record_id": rec.id,
            "owner": rec.owner,
            "amount": rec.amount,
            "deadline": rec.deadline,
            "category": rec.category,
            "notes": rec.notes,
        }
        return "Source work-request record:\n" + json.dumps(source, ensure_ascii=False)

    def _extract_delivered(self, resp: dict):
        try:
            delivered = {k: resp[k] for k in _DELIVERED_KEYS}
        except KeyError:
            return None
        try:
            delivered["primary_amount_usd"] = float(delivered["primary_amount_usd"])
        except (TypeError, ValueError):
            return None
        delivered["record_id"] = str(delivered["record_id"])
        delivered["client_owner"] = str(delivered["client_owner"])
        delivered["engagement_category"] = str(delivered["engagement_category"])
        delivered["deadline"] = str(delivered["deadline"])
        delivered["currency"] = "USD"
        delivered["summary"] = str(delivered["summary"])
        try:
            jsonschema.validate(delivered, _output_schema(str(self.cfg.schema_dir)))
        except jsonschema.ValidationError:
            return None
        return delivered

    def run(self, inp: WorkerInput) -> WorkerOutput:
        self._check_input(inp, WorkerInput)
        rec = inp.record
        res = self.llm.complete(
            agent=self.name,
            record_id=rec.id,
            prompt_version=self.spec.prompt_version,
            model=inp.model,
            system=_SYSTEM,
            user=self._build_user(rec),
        )
        resp = res.response if isinstance(res.response, dict) else {}
        abstain = bool(resp.get("abstain", False))
        try:
            confidence = float(resp.get("confidence", 0.0) or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0

        delivered = None
        malformed = False
        if not abstain and confidence >= 0.5:
            delivered = self._extract_delivered(resp)
            if delivered is None:
                malformed = True

        if delivered is not None:
            dfh = sha(delivered)
            self.llm.attach_delivery(res.transcript_hash, delivered, dfh)

        return WorkerOutput(
            record_id=rec.id,
            delivered_fields=delivered,
            abstained=(abstain or confidence < 0.5),
            confidence=confidence,
            model=res.model,
            prompt_version=res.prompt_version,
            tokens_in=res.tokens_in,
            tokens_out=res.tokens_out,
            cost_usd=res.cost_usd,
            latency_ms=res.latency_ms,
            transcript_hash=res.transcript_hash,
            retries=res.retries,
            malformed=malformed,
        )
