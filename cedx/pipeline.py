"""The governed 5-stage pipeline, run by the agent fleet.

Intake -> Normalize + Exception queue -> Assembly (Worker) -> Review (Verifier +
approval chain) -> Delivery + append-only audit.

Cross-cutting concerns handled here (not inside any single agent):
  * supersession (same id, higher version wins; older -> SUPERSEDED_VERSION),
  * batch-level robust-stat outlier detection,
  * idempotency (already-processed source versions are reused, never reprocessed),
  * building the audit bundle + exception queue + branded package.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from .amendment import compute_amendment
from .audit import build_audit, cost_summary, record_row
from .agents.orchestrator import OrchestratorAgent
from .agents.verifier import VerifierAgent
from .agents.worker import WorkerAgent
from .clock import LogicalClock
from .config import Config
from .contracts import ProcessedRecord, reason_class
from .delivery import write_package
from .detectors import detect_data_exception, outlier_ids
from .hashing import sha_bytes
from .intake import run_intake
from .llm import LLMClient
from .normalize import normalize_record
from .router import Router
from .store import Store


class Pipeline:
    def __init__(self, cfg: Config, llm: LLMClient | None = None,
                 worker=None, verifier=None):
        self.cfg = cfg
        self.amendment = compute_amendment(cfg.case_id)
        self.clock = LogicalClock(cfg.pipeline_now)
        self.llm = llm or LLMClient(cfg)
        self.router = Router(cfg, self.amendment)
        self.worker = worker or WorkerAgent(cfg, self.llm)
        self.verifier = verifier or VerifierAgent(cfg, self.llm)
        self.orchestrator = OrchestratorAgent(
            cfg, self.router, self.worker, self.verifier, self.amendment, self.clock
        )

    def roster(self) -> list[dict]:
        return [
            self.orchestrator.spec.to_roster_entry(),
            self.worker.spec.to_roster_entry(),
            self.verifier.spec.to_roster_entry(),
        ]

    def _seed_signature(self) -> str:
        parts = []
        for p in sorted(Path(self.cfg.seed_dir).rglob("*")):
            if p.is_file():
                parts.append(p.name + ":" + sha_bytes(p.read_bytes()))
        return sha_bytes("|".join(parts).encode("utf-8"))

    # -- per-record ----------------------------------------------------------
    def _process_one(self, rec, drift, flagged, store, log):
        svh = rec.source_version_hash
        if store.is_processed(svh):
            cached = store.get_processed(svh)
            return cached["row"], {"cost": cached["cost"], "latency": cached["latency"]}
        self.clock.rebase()  # per-record deterministic timestamps
        data_reason = detect_data_exception(rec, self.cfg.pipeline_now, flagged)
        pr, events = self.orchestrator.process(rec, data_reason, drift)
        row = record_row(pr)
        store.commit_record(
            svh, rec.id, pr.status,
            {"row": row, "cost": pr.cost_usd, "latency": pr.latency_ms}, events,
        )
        via = row["agent_trace"][-2]["model"] if len(row["agent_trace"]) >= 2 else "-"
        tag = pr.status.upper() if pr.status == "delivered" else f"EXC:{pr.reason_code}"
        log(f"  [fleet] {rec.id:<8} -> {tag} (model={via})")
        return row, {"cost": pr.cost_usd, "latency": pr.latency_ms}

    def _superseded_one(self, rec, store):
        svh = rec.source_version_hash
        if store.is_processed(svh):
            cached = store.get_processed(svh)
            return cached["row"], {"cost": 0.0, "latency": 0.0}
        self.clock.rebase()  # per-record deterministic timestamps
        pr = ProcessedRecord(
            record=rec, status="superseded", reason_code="SUPERSEDED_VERSION",
            spans=[{"agent": "orchestrator", "status": "routed"}], approval_trail=[],
        )
        events = [
            ("system", "record.received", self.clock.tick()),
            ("orchestrator", "superseded.by_newer_version", self.clock.tick()),
        ]
        row = record_row(pr)
        store.commit_record(svh, rec.id, "superseded",
                            {"row": row, "cost": 0.0, "latency": 0.0}, events)
        return row, {"cost": 0.0, "latency": 0.0}

    # -- run -----------------------------------------------------------------
    def run(self, quiet: bool = False) -> dict:
        def log(msg):
            if not quiet:
                print(msg, flush=True)

        self.cfg.out_dir.mkdir(parents=True, exist_ok=True)
        store = Store(self.cfg.db_path)
        store.ensure_seed(self._seed_signature())

        log(f"[intake] reading sources from {self.cfg.seed_dir}")
        raw = run_intake(self.cfg.seed_dir, store)
        log(f"[intake] persisted {len(raw)} source records")

        normalized = [normalize_record(r, self.cfg.schema_dir) for r in raw]

        # supersession: highest version per id wins
        by_id: dict[str, list] = {}
        for rec, drift in normalized:
            by_id.setdefault(rec.id, []).append((rec, drift))
        latest, superseded = {}, []
        for rid, items in by_id.items():
            items.sort(key=lambda x: x[0].version)
            *older, newest = items
            latest[rid] = newest
            superseded.extend(older)

        flagged = outlier_ids([rec for rec, _ in latest.values()], self.cfg.outlier_mad_cutoff)
        log(f"[orchestration] {len(latest)} active records, {len(superseded)} superseded, "
            f"{len(flagged)} outlier(s)")

        rows, meta = [], []
        for rec, _ in sorted(superseded, key=lambda x: (x[0].id, x[0].version)):
            r, m = self._superseded_one(rec, store)
            rows.append(r); meta.append(m)
        # env-gated crash injection for the crash-resume probe; never fires in normal runs.
        crash_after = int(os.environ.get("CEDX_CRASH_AFTER", "0") or "0")
        done = 0
        for rid in sorted(latest.keys()):
            rec, drift = latest[rid]
            r, m = self._process_one(rec, drift, flagged, store, log)
            rows.append(r); meta.append(m)
            done += 1
            if crash_after and done >= crash_after:
                store.close()
                raise SystemExit(137)  # simulate SIGKILL after atomically-committed work

        events = store.events()
        delivered_rows = [r for r in rows if r["status"] == "delivered"]
        oph = write_package(self.cfg.out_dir, self.cfg.case_id, delivered_rows)
        cost = cost_summary(meta, len(rows))

        audit = build_audit(
            self.cfg, self.amendment, self.roster(), rows, cost, events, oph, self.clock.now()
        )
        (self.cfg.out_dir / "audit.json").write_text(
            json.dumps(audit, indent=2), encoding="utf-8"
        )
        self._write_exception_queue(rows)
        store.close()

        log(f"[delivery] {len(delivered_rows)} delivered, "
            f"{sum(1 for r in rows if r['status'] == 'exception')} exceptions, "
            f"package hash {oph[:23]}...")
        log(f"[cost] total ${cost['total_usd']:.5f}, avg ${cost['avg_usd_per_record']:.6f}/record, "
            f"projected ${cost['projected_usd_per_10k']:.2f}/10k")
        return audit

    def _write_exception_queue(self, rows: list[dict]) -> None:
        eq = [
            {
                "id": r["id"],
                "reason_code": r["reason_code"],
                "reason_class": r["reason_class"],
                "source_format": r["source_format"],
                "detail": (r["approval_trail"][-1]["reason"] if r["approval_trail"] else None),
            }
            for r in rows
            if r["status"] == "exception"
        ]
        (self.cfg.out_dir / "exception_queue.json").write_text(
            json.dumps({"count": len(eq), "exceptions": eq}, indent=2), encoding="utf-8"
        )
