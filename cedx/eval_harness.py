"""Agent eval harness: golden-case rule accuracy + an LLM-judge per agent.

Two independent signals, printed per agent:
  * rule accuracy - deterministic check of each agent's decisions against golden
    expectations (does the detector assign the right reason code, does the worker
    draft-vs-abstain correctly, does the verifier return the right verdict).
  * llm_judge - an independent LLM rates the QUALITY of each agent's behaviour on a
    sample of records (0..1). Judge calls are recorded/replayed like every other model
    call, so `make eval` runs offline and deterministically.
"""
from __future__ import annotations

import json
from pathlib import Path

from .config import Config
from .llm import LLMClient
from .pipeline import Pipeline

_JUDGE_PROMPT_VERSION = "judge/v1"
_JUDGE_SAMPLE = ["REC-001", "REC-013", "REC-015", "REC-016"]

_JUDGE_SYSTEM = (
    "You are an impartial QA judge for a multi-agent work-order pipeline. Given a source "
    "record and what the fleet did with it, rate each agent from 0.0 to 1.0 on whether it "
    "did its job correctly: worker (drafted faithfully or abstained appropriately), "
    "verifier (correct verdict), orchestrator (correct routing/exception). "
    "Reply ONLY strict JSON: {\"worker\":number,\"verifier\":number,\"orchestrator\":number,"
    "\"rationale\":string}."
)


def _load_audit(cfg: Config) -> dict:
    ap = cfg.out_dir / "audit.json"
    if not ap.exists():
        Pipeline(cfg).run(quiet=True)
    return json.loads(ap.read_text(encoding="utf-8"))


def _find_row(rows: list[dict], rid: str, status: str) -> dict | None:
    for r in rows:
        if r["id"] == rid and r["status"] == status:
            return r
    # fall back to id-only if status not matched (miss will be scored)
    return next((r for r in rows if r["id"] == rid), None)


def _rule_scores(audit: dict, golden: list[dict]) -> dict:
    rows = audit["records"]
    per = {"orchestrator": [0, 0], "worker": [0, 0], "verifier": [0, 0]}
    misses = []
    for g in golden:
        row = _find_row(rows, g["id"], g["expected_status"])
        ok = bool(row) and row["status"] == g["expected_status"] and \
            row["reason_code"] == g["expected_reason_code"]
        agent = g["agent"]
        per[agent][1] += 1
        if ok:
            per[agent][0] += 1
        else:
            misses.append((g["id"], g["expected_status"], g["expected_reason_code"],
                           (row or {}).get("status"), (row or {}).get("reason_code")))
    return {"per": per, "misses": misses}


def _judge(cfg: Config, audit: dict) -> dict:
    llm = LLMClient(cfg)
    rows = {r["id"]: r for r in audit["records"]}
    totals = {"worker": 0.0, "verifier": 0.0, "orchestrator": 0.0}
    n = 0
    for rid in _JUDGE_SAMPLE:
        row = rows.get(rid)
        if not row:
            continue
        trace = [
            {"agent": s["agent"], "status": s["status"], "verdict": s.get("verdict"),
             "model": s.get("model")}
            for s in row["agent_trace"]
        ]
        payload = {
            "record_id": rid,
            "status": row["status"],
            "reason_code": row["reason_code"],
            "agent_trace": trace,
            "delivered_fields": row.get("delivered_fields"),
        }
        res = llm.complete(
            agent="judge", record_id=rid, prompt_version=_JUDGE_PROMPT_VERSION,
            model=cfg.model_cheap, system=_JUDGE_SYSTEM,
            user=json.dumps(payload, ensure_ascii=False), name_by_response=False,
        )
        r = res.response if isinstance(res.response, dict) else {}
        for k in totals:
            try:
                totals[k] += float(r.get(k, 0.0))
            except (TypeError, ValueError):
                pass
        n += 1
    if n:
        for k in totals:
            totals[k] = round(totals[k] / n, 3)
    return {"scores": totals, "n": n}


def run_eval(cfg: Config) -> int:
    audit = _load_audit(cfg)
    golden = json.loads(
        (Path("golden") / "golden_cases.json").read_text(encoding="utf-8")
    )["cases"]

    rule = _rule_scores(audit, golden)
    judge = _judge(cfg, audit)

    print(f"=== agent eval ({len(golden)} golden cases, LLM-judge on {judge['n']} samples) ===")
    print(f"{'agent':<14}{'rule_accuracy':<16}{'llm_judge':<12}{'golden_n'}")
    all_perfect = True
    for agent in ("orchestrator", "worker", "verifier"):
        ok, total = rule["per"][agent]
        acc = (ok / total) if total else 1.0
        all_perfect = all_perfect and (ok == total)
        js = judge["scores"].get(agent, 0.0)
        print(f"{agent:<14}{f'{ok}/{total} ({acc:.0%})':<16}{js:<12}{total}")

    if rule["misses"]:
        print("\nmisses (id, exp_status, exp_reason -> got_status, got_reason):")
        for m in rule["misses"]:
            print("  ", m)

    judged_ok = judge["n"] > 0 and all(v >= 0.6 for v in judge["scores"].values())
    passed = all_perfect and judged_ok
    print("\n" + ("PASS" if passed else "FAIL"),
          "- rule accuracy and LLM-judge scores within thresholds")
    return 0 if passed else 1
