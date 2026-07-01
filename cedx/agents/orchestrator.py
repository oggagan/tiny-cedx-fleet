"""Orchestrator agent - owns the run for one record.

It delegates: it holds NO assembly or verification business logic. Per record it
routes to a model tier, invokes the Worker, invokes the Verifier, enforces the
step + cost budgets, drives the approval chain, and decides delivered vs exception.
It is the only agent with a non-empty can_call.

Budgets it enforces:
  * step ceiling  -> exceeding it KILLS the run for that record  -> AGENT_LOOP
  * cost ceiling  -> exceeding it ROUTES the record              -> BUDGET_EXCEEDED
  * verifier fail on a cheap draft -> ONE escalation retry on the strong model, then
    route to a human if still bad (never deliver un-verified output).
"""
from __future__ import annotations

from ..amendment import Amendment
from ..approval import ApprovalChain
from ..clock import LogicalClock
from ..config import Config
from ..contracts import ProcessedRecord, VerifierInput, WorkerInput
from ..hashing import sha
from ..router import Router
from .base import Agent, AgentSpec

PROMPT_VERSION = "orchestrator/v1"


def _span(agent, status, verdict=None, model=None, pv=None, tin=0, tout=0,
          cost=0.0, lat=0.0, retries=0, transcript_hash=None) -> dict:
    return {
        "agent": agent, "status": status, "verdict": verdict, "model": model,
        "prompt_version": pv, "tokens_in": tin, "tokens_out": tout, "cost_usd": cost,
        "latency_ms": lat, "retries": retries, "transcript_hash": transcript_hash,
    }


_VERDICT_STATUS = {"pass": "ok", "fail": "overruled", "needs_human": "rejected"}


class OrchestratorAgent(Agent):
    def __init__(self, cfg: Config, router: Router, worker, verifier,
                 amendment: Amendment, clock: LogicalClock):
        self.cfg = cfg
        self.router = router
        self.worker = worker
        self.verifier = verifier
        self.amendment = amendment
        self.clock = clock
        self.spec = AgentSpec(
            name="orchestrator", role="orchestrator",
            models=[], prompt_version=PROMPT_VERSION,
            can_call=[worker.name, verifier.name],
        )

    # -- helpers -------------------------------------------------------------
    def _worker_span(self, wo, status):
        return _span("worker", status, model=wo.model, pv=wo.prompt_version,
                     tin=wo.tokens_in, tout=wo.tokens_out, cost=wo.cost_usd,
                     lat=wo.latency_ms, retries=wo.retries, transcript_hash=wo.transcript_hash)

    def _verifier_span(self, vo):
        return _span("verifier", _VERDICT_STATUS[vo.verdict], verdict=vo.verdict,
                     model=vo.model, pv=vo.prompt_version, tin=vo.tokens_in,
                     tout=vo.tokens_out, cost=vo.cost_usd, lat=vo.latency_ms,
                     transcript_hash=vo.transcript_hash)

    def _exception(self, rec, reason, spans, events, cost, lat, note):
        chain = ApprovalChain(self.amendment, self.clock)
        chain.open()
        chain.block(f"{note}: {reason}")
        events.extend(chain.events)
        return ProcessedRecord(
            record=rec, status="exception", reason_code=reason, spans=spans,
            approval_trail=chain.trail, cost_usd=cost, latency_ms=lat,
        )

    def _deliver(self, rec, wo, is_drift, spans, events, cost, lat):
        chain = ApprovalChain(self.amendment, self.clock)
        chain.auto_review(verifier_pass=True, amount=rec.amount)
        chain.deliver()
        events.extend(chain.events)
        delivered = wo.delivered_fields
        dfh = sha(delivered)
        spans.append(_span("orchestrator", "ok"))
        return ProcessedRecord(
            record=rec, status="delivered",
            reason_code=("SCHEMA_DRIFT" if is_drift else None),
            spans=spans, approval_trail=chain.trail,
            delivered_fields=delivered, delivered_fields_hash=dfh,
            transcript_hash=wo.transcript_hash, cost_usd=cost, latency_ms=lat,
        )

    # -- main ----------------------------------------------------------------
    def process(self, rec, data_reason, is_drift) -> tuple[ProcessedRecord, list]:
        spans: list[dict] = []
        events: list[tuple] = []

        def ev(actor, action):
            events.append((actor, action, self.clock.tick()))

        ev("system", "record.received")
        ev("orchestrator", "normalize.ok")

        # Class-A data exception: quarantine before spending any model budget.
        if data_reason is not None:
            spans.append(_span("orchestrator", "routed"))
            ev("orchestrator", f"route.exception:{data_reason}")
            return self._exception(rec, data_reason, spans, events, 0.0, 0.0, "data exception"), events

        steps = {"n": 0}

        def can_step() -> bool:
            if steps["n"] >= self.cfg.max_steps_per_record:
                return False
            steps["n"] += 1
            return True

        tier, model, route_reason = self.router.choose(rec)
        ev("orchestrator", f"route.{tier}:{route_reason}")

        cost = 0.0
        lat = 0.0

        # --- worker (assembly) ---
        can_step()
        wo = self.worker.run(WorkerInput(record=rec, model=model, tier=tier, reason=route_reason))
        cost += wo.cost_usd
        lat += wo.latency_ms
        spans.append(self._worker_span(wo, "ok" if wo.delivered_fields is not None else "abstained"))
        ev("worker", "assembly.draft" if wo.delivered_fields is not None else "assembly.abstain")

        # cost ceiling: never silently overspend
        if cost > self.cfg.max_cost_usd_per_record:
            spans.append(_span("orchestrator", "routed"))
            ev("orchestrator", "budget.exceeded")
            return self._exception(rec, "BUDGET_EXCEEDED", spans, events, cost, lat, "budget"), events

        # --- verifier ---
        if not can_step():
            spans.append(_span("orchestrator", "killed"))
            ev("orchestrator", "loop.killed")
            return self._exception(rec, "AGENT_LOOP", spans, events, cost, lat, "step budget"), events
        vo = self.verifier.run(VerifierInput(record=rec, worker_output=wo))
        cost += vo.cost_usd
        lat += vo.latency_ms
        spans.append(self._verifier_span(vo))
        ev("verifier", f"verify.{vo.verdict}")

        if vo.verdict == "pass":
            return self._deliver(rec, wo, is_drift, spans, events, cost, lat), events

        # --- escalate: verifier bounced a cheap draft -> retry once on strong ---
        if (vo.verdict == "fail" and vo.reason_code in {"AGENT_HALLUCINATION", "AGENT_MALFORMED"}
                and tier == "cheap"):
            ev("orchestrator", "escalate.retry_strong")
            if can_step():
                wo2 = self.worker.run(
                    WorkerInput(record=rec, model=self.cfg.model_strong, tier="strong",
                                reason="verifier_bounce_retry")
                )
                cost += wo2.cost_usd
                lat += wo2.latency_ms
                spans.append(self._worker_span(wo2, "retried"))
                ev("worker", "assembly.retry")
                if can_step():
                    vo2 = self.verifier.run(VerifierInput(record=rec, worker_output=wo2))
                    cost += vo2.cost_usd
                    lat += vo2.latency_ms
                    spans.append(self._verifier_span(vo2))
                    ev("verifier", f"verify.{vo2.verdict}")
                    if vo2.verdict == "pass":
                        return self._deliver(rec, wo2, is_drift, spans, events, cost, lat), events
                    vo = vo2

        reason = vo.reason_code or "UNVERIFIED_ANOMALY"
        ev("orchestrator", f"route.exception:{reason}")
        return self._exception(rec, reason, spans, events, cost, lat, "verifier overruled worker"), events
