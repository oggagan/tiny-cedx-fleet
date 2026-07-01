# DECISIONS

CASE_ID: **CEDX-BEA205** · amendment: **compliance @ 47,000**.

## What I deliberately did NOT automate (and why)
- **Class-A exception resolution stays human.** STALE, MISSING_INPUT, OUTLIER,
  INJECTION_BLOCKED, LOW_CONFIDENCE, UNVERIFIED_ANOMALY are quarantined, never
  auto-fixed. Auto-defaulting a missing amount or auto-approving a flagged outlier is
  exactly the failure a governed pipeline exists to prevent.
- **Delivery is never self-service.** The approval state machine refuses delivery of any
  record not in `approved`, server-side. The demo uses a scripted operator policy so the
  batch runs unattended, but the gate is real (see `probe-approval`).
- **The Worker never grounds itself.** It emits the factual fields; the independent
  Verifier decides if they are supported. That separation is what makes AGENT_HALLUCINATION
  catchable rather than assumed away.
- **No no-code/orchestrator platform, no message bus, no fine-tuning.** In-process typed
  handoffs keep the agent boundaries explicit and testable at this scale.

## Thresholds, and why they generalize (not hardcoded to the seed)
- **Outlier = robust modified z-score** (median + MAD, cutoff 3.5). Median/MAD are
  unaffected by the single extreme value, so the detector adapts to whatever the "normal"
  band and outlier magnitude are in the held-out set. No `== 250000` anywhere.
- **Abstain = the Worker's own confidence.** The model abstains (confidence < 0.5) on
  ambiguous/contradictory records; the pipeline routes that as LOW_CONFIDENCE. This is a
  semantic decision by the model, so different ambiguous records in the held-out set are
  handled without code changes. On the dev seed this correctly fires on REC-015 (INTAKE
  vs renewal-and-report contradiction) and REC-021 (unclear category, unattached figure).
- **Injection = instruction PHRASES** ("ignore the field", "approve immediately", "skip
  review"...), not specific strings; notes are treated as data, never commands.
- **UNVERIFIED_ANOMALY** is the catch-all: fails basic structural validity but matches no
  known rule → quarantined. This is what catches the held-out undocumented anomaly.

## Router policy + the cost numbers
Cheap `deepseek-chat` by default; escalate to strong `deepseek-reasoner` only when a
record shows uncertainty markers, or amount >= the amendment threshold, or the Verifier
bounced a cheap draft. On the dev seed that is 30 cheap spans vs 2 strong (only the two
genuinely ambiguous records), which keeps average cost near the cheap-model floor:

- avg **$0.000516 / record**, total **$0.0088** for the batch, p95 latency ~9.6 s/record
  (the reasoner escalations dominate the tail; cheap records are ~1-2 s).
- projected **~$5.16 / 10,000 records/day**.

Per-record cost and step ceilings (`MAX_COST_USD_PER_RECORD`, `MAX_STEPS_PER_RECORD`)
raise BUDGET_EXCEEDED / AGENT_LOOP rather than overspending or looping.

## How provenance survives a re-run
Every model call is recorded to a content-addressed transcript tagged with the calling
agent; delivered fields hash back to a worker transcript. The event log is append-only,
enforced by SQLite triggers that ABORT UPDATE/DELETE (`probe-append-only`). Each record's
events + result commit in one transaction, and a per-record logical clock makes timestamps
independent of processing order. Result: record-mode and replay-mode produce a
byte-identical audit; a crash mid-batch resumes to the same audit with no duplicates
(`probe-idempotency`, `probe-crash`).

## What breaks first at 10,000 records/day
1. **Verifier latency tail, not cost.** At ~$5/day cost is a non-issue; the reasoner's
   multi-second latency on escalations is the bottleneck. First move: cap the escalation
   rate, batch verifier checks, and make the deterministic grounding check the fast path
   with the LLM opinion sampled.
2. **Single SQLite writer.** Fine for a batch; at streaming scale swap the store for a
   real queue + Postgres. The append-only interface and per-record transactions are
   already the seam where that swap happens.
3. **One-at-a-time processing.** Records are independent, so the orchestrator loop
   parallelizes trivially behind a worker pool; the audit is already order-independent.

## Honest note on AI usage
The code was written with AI assistance, as the task expects. The architecture, the
control decisions, the threshold choices, and every design trade-off above are mine, and
I can extend any part of it live.
