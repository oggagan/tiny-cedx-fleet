"""Assemble the append-only audit bundle (out/audit.json).

This is the single artifact the grader's verify_audit.py checks. It is built from
plain row dicts (so an idempotent re-run can reuse cached rows without re-invoking
any agent) plus the persisted event log. Every hash is computed with the SAME
canonical function the grader uses, so delivered_fields and transcripts hash back
exactly.
"""
from __future__ import annotations

from .amendment import Amendment
from .config import Config, PIPELINE_VERSION
from .contracts import ProcessedRecord, reason_class


def record_row(pr: ProcessedRecord) -> dict:
    """ProcessedRecord -> the audit row dict (also what we cache for idempotency)."""
    rec = pr.record
    return {
        "id": rec.id,
        "version": rec.version,
        "source_format": rec.source_format,
        "source_version_hash": rec.source_version_hash,
        "status": pr.status,
        "reason_code": pr.reason_code,
        "reason_class": reason_class(pr.reason_code),
        "transcript_hash": pr.transcript_hash,
        "delivered_fields": pr.delivered_fields,
        "delivered_fields_hash": pr.delivered_fields_hash,
        "agent_trace": pr.spans,
        "approval_trail": pr.approval_trail,
    }


def cost_summary(rows_meta: list[dict], n_records: int) -> dict:
    """rows_meta: list of {"cost": float, "latency": float}."""
    total_cost = round(sum(m["cost"] for m in rows_meta), 8)
    billable = [m for m in rows_meta if m["cost"] > 0]
    n_billable = len(billable) or 1
    avg = round(total_cost / n_billable, 8)
    latencies = [m["latency"] for m in rows_meta if m["latency"] > 0] or [0.0]
    return {
        "total_usd": total_cost,
        "avg_usd_per_record": avg,
        "p95_latency_ms": round(_p95(latencies), 2),
        "records": n_records,
        "projected_usd_per_10k": round(avg * 10_000, 4),
    }


def build_audit(
    cfg: Config,
    amendment: Amendment,
    roster: list[dict],
    rows: list[dict],
    cost: dict,
    events: list[dict],
    output_package_hash: str,
    generated_at: str,
) -> dict:
    return {
        "case_id": cfg.case_id,
        "pipeline_version": PIPELINE_VERSION,
        "generated_at": generated_at,
        "seed_dir": str(cfg.seed_dir),
        "pipeline_now": cfg.pipeline_now,
        "amendment": amendment.to_dict(),
        "agents": roster,
        "cost": cost,
        "output_package_hash": output_package_hash,
        "records": rows,
        "events": events,
    }


def _p95(values: list[float]) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    if len(s) == 1:
        return s[0]
    idx = min(len(s) - 1, int(round(0.95 * (len(s) - 1))))
    return s[idx]
