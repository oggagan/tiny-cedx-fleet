# ARCHITECTURE - Tiny CEDX Agent Fleet

Industry: **Investment Banking (Boutique & Mid-Market), Tier 1**. The fleet ingests
client-engagement work-requests, governs them through five stages, and delivers branded
engagement work-orders only when they are grounded, verified, and approved.

## Agent topology

```
                         ┌───────────────────────────────────────────────┐
                         │             ORCHESTRATOR  (role: orchestrator) │
   source records ──────▶│  owns the run per record: routes, enforces     │
   (feed / eml / pdf)    │  step+cost budgets, drives approval, decides    │
                         │  delivered vs exception. can_call:[worker,      │
                         │  verifier]. No assembly/verification logic here.│
                         └───────┬───────────────────────────────┬────────┘
                                 │ WorkerInput (typed)            │ VerifierInput (typed)
                                 ▼                                ▼
                    ┌────────────────────────┐        ┌────────────────────────────┐
                    │  WORKER (role: worker)  │        │  VERIFIER (role: verifier)  │
                    │  Assembly. Drafts the   │        │  Independent. Grounds the   │
                    │  branded work-order via │        │  draft against the source; │
                    │  the model ROUTER       │───────▶│  can OVERRULE the worker.   │
                    │  (cheap→strong). Abstains│  draft │  Deterministic grounding + │
                    │  on ambiguity.          │        │  independent LLM opinion.   │
                    │  can_call:[]            │        │  can_call:[]                │
                    └────────────────────────┘        └────────────────────────────┘
                                 │ WorkerOutput                   │ VerifierOutput
                                 └───────────────┬────────────────┘
                                                 ▼
                              Approval state machine + CASE_ID amendment
                                                 ▼
                              Delivery: branded package + append-only audit
```

Three agents, each a separable, individually testable unit with its own module,
prompt version, and a declared `can_call` list. The Orchestrator is the only agent
allowed to call others. This is enforced structurally: the Worker and Verifier are
leaf agents that receive a typed input and return a typed output, nothing else.

## Typed handoff contracts (`cedx/contracts.py`)

| Producer | Contract | Consumer |
|---|---|---|
| Orchestrator | `WorkerInput{record, model, tier, reason}` | Worker |
| Worker | `WorkerOutput{delivered_fields, abstained, confidence, model, cost, transcript_hash, ...}` | Orchestrator, Verifier |
| Orchestrator | `VerifierInput{record, worker_output}` | Verifier |
| Verifier | `VerifierOutput{verdict, reason_code, detail, ...}` | Orchestrator |
| Orchestrator | `ProcessedRecord{status, reason_code, spans, approval_trail, delivered_fields, ...}` | audit/delivery |

The base `Agent` (`cedx/agents/base.py`) type-checks its input contract at the boundary,
so a wrong handoff is a loud error, not silent string-passing.

## The five governed stages (under the fleet)

1. **Intake** (`intake.py`) - parse `feed.json`, `.eml`, and `.pdf`, preserving original
   field names, persist each to SQLite. No in-memory-only arrays.
2. **Orchestration** (`normalize.py` + `detectors.py`) - map to the versioned output
   schema (`schema/output_schema.v1.json`) via the declarative `schema/field_map.json`;
   the exception queue assigns each planted problem its reason code + class. Class-A blocks;
   Class-B (SCHEMA_DRIFT, SUPERSEDED_VERSION) auto-resolves and continues.
3. **Assembly** (`agents/worker.py` + `router.py`) - the Worker drafts the branded
   work-order at the routed model tier, enforces structured output with repair, abstains
   on ambiguity (LOW_CONFIDENCE).
4. **Review** (`agents/verifier.py` + `approval.py`) - the Verifier independently grounds
   the draft and can overrule the Worker; the approval state machine
   (`draft→in_review→approved→delivered`, plus `changes_requested`/`blocked`) refuses
   delivery of anything not `approved`, and the CASE_ID amendment adds a second
   `compliance` approval for amount >= 47,000.
5. **Delivery + Audit** (`delivery.py` + `audit.py`) - branded work-orders + a hashed
   package manifest, and the append-only `out/audit.json` with per-record `agent_trace`,
   `cost`, `approval_trail`, and the event log.

## Where the Verifier overrules the Worker

`agents/verifier.py` returns a verdict the Orchestrator obeys:

- worker abstained → `needs_human` / `LOW_CONFIDENCE` (route, do not guess)
- draft malformed → `fail` / `AGENT_MALFORMED`
- a grounded field (id, owner, amount, deadline, category) was altered → `fail` /
  `AGENT_HALLUCINATION` (deterministic, exact, generalizes to unseen data)
- grounded but the independent LLM opinion disputes it → `needs_human` (disagreement logged)

On a `fail` for a cheap draft, the Orchestrator escalates once to the strong model and
re-verifies before routing to a human. Nothing unverified is ever delivered.

## Budgets, routing, observability

- **Model router** (`router.py`): cheap `deepseek-chat` by default; escalate to
  `deepseek-reasoner` only for uncertainty markers or amount >= threshold, or a verifier
  bounce. On the dev seed: 30 cheap spans, 2 strong (the two genuinely ambiguous records).
- **Budgets** (`agents/orchestrator.py`): a per-record step ceiling (exceed → `AGENT_LOOP`,
  killed) and cost ceiling (exceed → `BUDGET_EXCEEDED`, routed). Never a silent overspend.
- **Traces**: every record carries an ordered `agent_trace` (agent, model, tokens, cost,
  latency, retries, status, verdict, transcript_hash). `make trace ID=<id>` reconstructs
  the full decision path from the log alone; `make replay ID=<id>` reconstructs data lineage.

## Determinism and provenance

The LLM boundary (`llm.py`) records each model call to `transcripts/<response_hash>.json`
(worker, load-bearing) or by request key (verifier), tagged with the calling agent. The
default offline path replays them deterministically - record and replay produce a
byte-identical audit. A per-record logical clock makes the audit order-independent, so a
crash-resumed run equals a clean run. Every delivered field hashes back to a committed,
worker-tagged transcript, which is exactly what `verify_audit.py` checks.
