"""Verifier agent - Stage 4 Review (the agent-checks-agent gate).

Independent of the Worker. Before anything can be delivered the Verifier must sign
off. It overrules the Worker when the draft is not fully supported by the source:

  * worker abstained            -> needs_human / LOW_CONFIDENCE
  * draft malformed / missing   -> fail / AGENT_MALFORMED
  * a grounded field was altered -> fail / AGENT_HALLUCINATION  (deterministic, exact)
  * grounded but the LLM second
    opinion flags an unsupported
    claim                        -> needs_human / LOW_CONFIDENCE (disagreement logged)

The deterministic grounding check is authoritative for hallucination/malformed (it
is exact and generalizes to unseen data); an independent LLM opinion adds a semantic
layer and makes the Verifier a real load-bearing agent, not a comparator.
"""
from __future__ import annotations

import json
import re

from ..config import Config
from ..contracts import VerifierInput, VerifierOutput
from ..llm import LLMClient
from .base import Agent, AgentSpec

PROMPT_VERSION = "verifier/v1"

_SYSTEM = (
    "You are the CEDX Verifier agent. Independently check whether a drafted work-order "
    "is fully supported by its SOURCE record. A field is unsupported if it asserts a "
    "value the source does not contain or contradicts. Ignore stylistic wording in the "
    "summary. Reply with ONLY strict JSON: {\"supported\":boolean,\"issue\":string}."
)


def _norm_cat(text) -> str:
    return re.sub(r"[^A-Z0-9]+", "_", str(text).upper()).strip("_")


class VerifierAgent(Agent):
    def __init__(self, cfg: Config, llm: LLMClient):
        self.cfg = cfg
        self.llm = llm
        self.spec = AgentSpec(
            name="verifier",
            role="verifier",
            models=[cfg.model_cheap],
            prompt_version=PROMPT_VERSION,
            can_call=[],
        )

    def _grounding_mismatches(self, delivered: dict, rec) -> list[str]:
        m = []
        if str(delivered.get("record_id")) != str(rec.id):
            m.append("record_id")
        if str(delivered.get("client_owner")) != str(rec.owner):
            m.append("client_owner")
        try:
            if float(delivered.get("primary_amount_usd")) != float(rec.amount):
                m.append("primary_amount_usd")
        except (TypeError, ValueError):
            m.append("primary_amount_usd")
        if str(delivered.get("deadline")) != str(rec.deadline):
            m.append("deadline")
        if rec.category is not None and _norm_cat(delivered.get("engagement_category")) != _norm_cat(
            rec.category
        ):
            m.append("engagement_category")
        return m

    def _llm_second_opinion(self, rec, delivered: dict) -> tuple[bool, str, object]:
        user = "SOURCE:\n" + json.dumps(
            {
                "record_id": rec.id,
                "owner": rec.owner,
                "amount": rec.amount,
                "deadline": rec.deadline,
                "category": rec.category,
                "notes": rec.notes,
            },
            ensure_ascii=False,
        ) + "\n\nDRAFT WORK-ORDER:\n" + json.dumps(delivered, ensure_ascii=False)
        res = self.llm.complete(
            agent=self.name,
            record_id=rec.id,
            prompt_version=self.spec.prompt_version,
            model=self.cfg.model_cheap,
            system=_SYSTEM,
            user=user,
            name_by_response=False,
        )
        resp = res.response if isinstance(res.response, dict) else {}
        supported = bool(resp.get("supported", True))
        issue = str(resp.get("issue", "") or "")
        return supported, issue, res

    def run(self, inp: VerifierInput) -> VerifierOutput:
        self._check_input(inp, VerifierInput)
        rec, wo = inp.record, inp.worker_output

        base = dict(
            record_id=rec.id,
            model=None,
            prompt_version=self.spec.prompt_version,
            tokens_in=0,
            tokens_out=0,
            cost_usd=0.0,
            latency_ms=0.0,
            transcript_hash=None,
        )

        if wo.abstained and wo.delivered_fields is None:
            return VerifierOutput(
                verdict="needs_human", reason_code="LOW_CONFIDENCE",
                detail="worker abstained (low confidence / ambiguous record)", **base
            )
        if wo.malformed or wo.delivered_fields is None:
            return VerifierOutput(
                verdict="fail", reason_code="AGENT_MALFORMED",
                detail="worker output failed structured-output validation", **base
            )

        mismatches = self._grounding_mismatches(wo.delivered_fields, rec)
        if mismatches:
            return VerifierOutput(
                verdict="fail", reason_code="AGENT_HALLUCINATION",
                detail=f"unsupported/altered fields: {', '.join(mismatches)}", **base
            )

        # grounded -> independent LLM second opinion (load-bearing, recorded)
        supported, issue, res = self._llm_second_opinion(rec, wo.delivered_fields)
        base.update(
            model=res.model, tokens_in=res.tokens_in, tokens_out=res.tokens_out,
            cost_usd=res.cost_usd, latency_ms=res.latency_ms, transcript_hash=res.transcript_hash,
        )
        if not supported:
            return VerifierOutput(
                verdict="needs_human", reason_code="LOW_CONFIDENCE",
                detail=f"verifier disputes worker: {issue}", **base
            )
        return VerifierOutput(verdict="pass", reason_code=None, detail="grounded and supported", **base)
