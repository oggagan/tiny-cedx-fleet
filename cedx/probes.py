"""Observability commands (trace, replay) and the uniform control probes.

Each probe is a self-contained assertion about one governance/reliability invariant
and returns a Unix exit code: 0 = the control held, non-zero = it did not. The
Makefile wires each target to one function here.
"""
from __future__ import annotations

import dataclasses
import json
import os
from pathlib import Path

from .agents.base import AgentSpec
from .amendment import compute_amendment
from .approval import ApprovalChain, ApprovalError
from .clock import LogicalClock
from .config import Config
from .contracts import WorkerOutput
from .intake import run_intake
from .normalize import normalize_record
from .pipeline import Pipeline
from .store import AppendOnlyViolation, Store


# ---- helpers ----------------------------------------------------------------
class _NullStore:
    def persist_record(self, *a, **k):  # intake only needs persist
        pass


def _normalized_records(cfg: Config) -> dict:
    raw = run_intake(cfg.seed_dir, _NullStore())
    out = {}
    for r in raw:
        rec, drift = normalize_record(r, cfg.schema_dir)
        out[rec.id] = (rec, drift)
    return out


def _load_or_run_audit(cfg: Config) -> dict:
    ap = cfg.out_dir / "audit.json"
    if not ap.exists():
        Pipeline(cfg).run(quiet=True)
    return json.loads(ap.read_text(encoding="utf-8"))


def _ok(msg: str) -> None:
    print(f"  OK  {msg}")


def _bad(msg: str) -> None:
    print(f" BAD  {msg}")


# ---- trace / replay ---------------------------------------------------------
def trace_record(cfg: Config, rid: str) -> int:
    audit = _load_or_run_audit(cfg)
    rec = next((r for r in audit["records"] if r["id"] == rid), None)
    if rec is None:
        print(f"record {rid} not found in audit")
        return 1
    print(f"=== agent decision path for {rid} ===")
    print(f"status={rec['status']} reason_code={rec['reason_code']} "
          f"reason_class={rec['reason_class']}")
    print(f"source_format={rec['source_format']} source_version_hash={rec['source_version_hash']}")
    print("\nagent_trace (one span per agent step):")
    for i, s in enumerate(rec["agent_trace"]):
        print(f"  [{i}] agent={s['agent']:<12} status={s.get('status'):<10} "
              f"verdict={s.get('verdict')} model={s.get('model')} "
              f"tok_in={s.get('tokens_in')} tok_out={s.get('tokens_out')} "
              f"cost=${s.get('cost_usd')} lat={s.get('latency_ms')}ms "
              f"retries={s.get('retries')} transcript={str(s.get('transcript_hash'))[:23]}")
    print("\napproval_trail:")
    for t in rec["approval_trail"]:
        print(f"  {t['state']:<18} by {t['actor']:<22} @ {t['ts']}  {t.get('reason') or ''}")
    if rec.get("delivered_fields"):
        print("\ndelivered_fields:", json.dumps(rec["delivered_fields"], indent=2))
    return 0


def replay_lineage(cfg: Config, rid: str) -> int:
    audit = _load_or_run_audit(cfg)
    rec = next((r for r in audit["records"] if r["id"] == rid), None)
    if rec is None:
        print(f"record {rid} not found in audit")
        return 1
    events = [e for e in audit["events"] if e.get("record_id") == rid]
    print(f"=== data lineage for {rid} (reconstructed from append-only log) ===")
    print(f"source_format={rec['source_format']}  source_version_hash={rec['source_version_hash']}")
    for e in events:
        print(f"  seq {e['seq']:>3}  {e['ts']}  {e['actor']:<22} {e['action']}")
    print(f"final: status={rec['status']} reason_code={rec['reason_code']}")
    if rec.get("transcript_hash"):
        print(f"load-bearing worker transcript: {rec['transcript_hash']}")
    if rec.get("delivered_fields_hash"):
        print(f"delivered_fields_hash: {rec['delivered_fields_hash']}")
    return 0


# ---- probe: approval (server-side refusal + amendment) ----------------------
def probe_approval(cfg: Config) -> int:
    amd = compute_amendment(cfg.case_id)
    clock = LogicalClock(cfg.pipeline_now)
    results = {}

    # 1) verified record, normal approval WITHHELD -> cannot deliver
    c1 = ApprovalChain(amd, clock)
    c1.auto_review(verifier_pass=True, amount=1000, grant_normal=False)
    refused1 = False
    try:
        c1.deliver()
    except ApprovalError:
        refused1 = True
    results["unapproved_refused"] = refused1 and not c1.can_deliver()

    # 2) high-value record (>= threshold), amendment second approval WITHHELD -> blocked
    c2 = ApprovalChain(amd, clock)
    c2.auto_review(verifier_pass=True, amount=amd.threshold + 1, grant_second=False)
    refused2 = False
    try:
        c2.deliver()
    except ApprovalError:
        refused2 = True
    results["amendment_enforced"] = refused2 and c2.state == "blocked"

    # 3) fully approved (incl. compliance second approval) -> delivers
    c3 = ApprovalChain(amd, clock)
    c3.auto_review(verifier_pass=True, amount=amd.threshold + 1)
    delivered3 = c3.can_deliver()
    c3.deliver()
    results["approved_delivers"] = delivered3 and c3.state == "delivered"

    (cfg.out_dir).mkdir(parents=True, exist_ok=True)
    (cfg.out_dir / "probe_approval.json").write_text(
        json.dumps({"amendment": amd.to_dict(), "trails": {
            "unapproved": c1.trail, "high_value_no_second": c2.trail, "fully_approved": c3.trail,
        }}, indent=2), encoding="utf-8")

    for k, v in results.items():
        (_ok if v else _bad)(k)
    passed = all(results.values())
    print("PASS" if passed else "FAIL", "- delivery refused server-side for non-approved items"
          f" (amendment: {amd.role} @ {amd.threshold})")
    return 0 if passed else 1


# ---- probe: agent failure (verifier catches bad worker) ---------------------
class _BadWorker:
    """Injected misbehaving worker used only by probes."""

    def __init__(self, cfg: Config, mode: str):
        self.mode = mode
        self.spec = AgentSpec("worker", "worker", [cfg.model_cheap, cfg.model_strong],
                              "worker/v1", [])

    @property
    def name(self):
        return "worker"

    def run(self, inp) -> WorkerOutput:
        rec = inp.record
        base = dict(record_id=rec.id, model=inp.model, prompt_version="worker/v1",
                    tokens_in=12, tokens_out=12, cost_usd=0.00002, latency_ms=4.0,
                    transcript_hash="sha256:" + "0" * 64, retries=0)
        if self.mode == "hallucinate":
            bad = {
                "record_id": rec.id, "client_owner": rec.owner,
                "engagement_category": "REPORT",
                "primary_amount_usd": float(rec.amount or 0) + 999999.0,  # fabricated value
                "deadline": rec.deadline, "currency": "USD", "summary": "fabricated figure",
            }
            return WorkerOutput(delivered_fields=bad, abstained=False, confidence=0.99,
                                malformed=False, **base)
        # malformed / loop: structurally unusable output
        return WorkerOutput(delivered_fields=None, abstained=False, confidence=0.99,
                            malformed=True, **base)


def _run_bad(cfg: Config, mode: str):
    from .agents.verifier import VerifierAgent
    from .agents.orchestrator import OrchestratorAgent
    from .router import Router

    amd = compute_amendment(cfg.case_id)
    clock = LogicalClock(cfg.pipeline_now)
    llm = None
    verifier = VerifierAgent(cfg, None)  # verifier will not call LLM on ungrounded/malformed
    worker = _BadWorker(cfg, mode)
    orch = OrchestratorAgent(cfg, Router(cfg, amd), worker, verifier, amd, clock)
    rec = _normalized_records(cfg)["REC-001"][0]
    pr, _events = orch.process(rec, None, False)
    return pr


def probe_agent_failure(cfg: Config) -> int:
    results = {}

    pr_h = _run_bad(cfg, "hallucinate")
    results["hallucination_caught"] = (
        pr_h.status == "exception" and pr_h.reason_code == "AGENT_HALLUCINATION"
    )

    pr_m = _run_bad(cfg, "malformed")
    results["malformed_caught"] = (
        pr_m.status == "exception" and pr_m.reason_code == "AGENT_MALFORMED"
    )

    # loop: starve the step budget so the orchestrator kills the run
    cfg_loop = dataclasses.replace(cfg, max_steps_per_record=1)
    pr_l = _run_bad(cfg_loop, "malformed")
    results["loop_killed"] = pr_l.status == "exception" and pr_l.reason_code == "AGENT_LOOP"

    for pr, label in ((pr_h, "hallucination"), (pr_m, "malformed"), (pr_l, "loop")):
        results[f"{label}_not_delivered"] = pr.status != "delivered"

    for k, v in results.items():
        (_ok if v else _bad)(f"{k}")
    passed = all(results.values())
    print("PASS" if passed else "FAIL",
          "- Verifier/Orchestrator caught and routed misbehaving worker output")
    return 0 if passed else 1


# ---- probe: budget ceiling --------------------------------------------------
def probe_budget(cfg: Config) -> int:
    # ensure the transcript for REC-001 exists (replay path)
    _load_or_run_audit(cfg)
    tiny = dataclasses.replace(cfg, max_cost_usd_per_record=1e-12)
    p = Pipeline(tiny)
    rec = _normalized_records(tiny)["REC-001"][0]
    pr, _ = p.orchestrator.process(rec, None, False)
    caught = pr.status == "exception" and pr.reason_code == "BUDGET_EXCEEDED"
    (_ok if caught else _bad)(
        f"REC-001 cost ${pr.cost_usd:.6f} > ceiling -> {pr.reason_code}, not delivered")
    passed = caught and pr.status != "delivered"
    print("PASS" if passed else "FAIL", "- per-record cost ceiling raised BUDGET_EXCEEDED and routed")
    return 0 if passed else 1


# ---- probe: append-only -----------------------------------------------------
def probe_append_only(cfg: Config) -> int:
    if not cfg.db_path.exists():
        Pipeline(cfg).run(quiet=True)
    store = Store(cfg.db_path)
    refused_update = False
    try:
        store.try_mutate_latest_event()
    except AppendOnlyViolation as e:
        refused_update = True
        _ok(f"UPDATE on events refused by engine: {e}")
    refused_delete = False
    try:
        store.conn.execute("DELETE FROM events WHERE seq=0")
        store.conn.commit()
    except Exception as e:  # trigger raises
        refused_delete = True
        _ok(f"DELETE on events refused by engine: {type(e).__name__}")
    store.close()
    if not refused_update:
        _bad("UPDATE was allowed")
    if not refused_delete:
        _bad("DELETE was allowed")
    passed = refused_update and refused_delete
    print("PASS" if passed else "FAIL", "- past audit entries are immutable (append-only)")
    return 0 if passed else 1


# ---- probe: idempotency -----------------------------------------------------
def probe_idempotency(cfg: Config) -> int:
    a1 = Pipeline(cfg).run(quiet=True)
    a2 = Pipeline(cfg).run(quiet=True)
    same_events = len(a1["events"]) == len(a2["events"])
    same_records = a1["records"] == a2["records"]
    identical = json.dumps(a1, sort_keys=True) == json.dumps(a2, sort_keys=True)
    (_ok if same_events else _bad)(f"event count stable: {len(a1['events'])} == {len(a2['events'])}")
    (_ok if same_records else _bad)("record set identical on re-run")
    (_ok if identical else _bad)("full audit bundle byte-identical on re-run")
    passed = same_events and same_records and identical
    print("PASS" if passed else "FAIL", "- re-running the pipeline produced no duplicates")
    return 0 if passed else 1


# ---- probe: crash resume (bonus) --------------------------------------------
def probe_crash(cfg: Config) -> int:
    # fresh state
    for p in (cfg.db_path, cfg.out_dir / "audit.json"):
        if p.exists():
            p.unlink()
    # 1) crash midway (records committed atomically survive)
    os.environ["CEDX_CRASH_AFTER"] = "6"
    crashed = False
    try:
        Pipeline(cfg).run(quiet=True)
    except SystemExit:
        crashed = True
    os.environ.pop("CEDX_CRASH_AFTER", None)
    (_ok if crashed else _bad)("pipeline crashed after committing 6 records")
    # 2) resume: re-run completes using persisted state
    audit = Pipeline(cfg).run(quiet=True)
    n = len(audit["records"])
    # compare to a clean full run
    for p in (cfg.db_path, cfg.out_dir / "audit.json"):
        if p.exists():
            p.unlink()
    clean = Pipeline(cfg).run(quiet=True)
    resumed_ok = json.dumps(audit["records"], sort_keys=True) == json.dumps(
        clean["records"], sort_keys=True)
    (_ok if resumed_ok else _bad)(f"resumed run == clean run ({n} records, no duplicates)")
    passed = crashed and resumed_ok
    print("PASS" if passed else "FAIL", "- pipeline resumes after a crash with no duplicates")
    return 0 if passed else 1
