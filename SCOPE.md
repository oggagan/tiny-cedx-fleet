# SCOPE - Build a Tiny CEDX Agent Fleet

- **Candidate name:** Gagandeep Singh
- **CASE_ID (assigned live):** CEDX-BEA205
- **Industry chosen (from cedxsystems.com/workflows):** Investment Banking (Boutique & Mid-Market)
- **Tier:** Tier 1 (highest revenue, fastest close)
- **Stack / language:** Python 3.11, standard library only for the fleet (urllib for the OpenAI-compatible LLM, sqlite3 for the append-only store, email for .eml intake); jsonschema + pypdf as the only third-party deps. LLM: DeepSeek (deepseek-chat cheap / deepseek-reasoner strong).

## Amendment (computed from CASE_ID)
```
H = sha256("CEDX-BEA205")   # e256d29a...
role R      = ["risk_officer","legal_counsel","compliance","finance_controller"][ int(H[0],16) % 4 ]
threshold T = 10000 + (int(H[1:3],16) % 50) * 1000
```
- **My role R:** compliance
- **My threshold T:** 47000

Any record whose normalized primary numeric field (amount) is >= 47,000 requires a
recorded `compliance` approval in addition to the normal operator approval before
delivery. Enforced by the approval state machine; proven by `make probe-approval`.

## What I will build (the 5 governed stages, as a >=3 agent fleet)
- [x] Sources/Intake - parse feed.json + inbox PDF/email into a persisted SQLite store
- [x] Orchestration - declarative normalize (versioned output schema + field map) + exception queue with every reason code
- [x] Assembly - Worker agent drafts branded output via a cheap/strong model router; abstains on ambiguity
- [x] Review - independent Verifier agent overrules the Worker; approval state machine + my CASE_ID amendment
- [x] Delivery - branded IB engagement work-orders + append-only audit with per-agent traces, cost, and replay

Agents: `orchestrator` (owns the run, budgets, routing) -> `worker` (assembly) + `verifier` (agent-checks-agent). Typed contracts, declared `can_call`.

## What I will deliberately NOT build (and why)
- No human UI for exception resolution beyond a CLI operator surface: the governance
  (state machine, server-side refusal, audit) is the graded substance; a rich UI would
  not change whether the controls hold.
- No real message bus / queue infra: at this scale in-process typed handoffs make the
  agent boundaries clearer and testable; the design notes where a queue slots in at 10k/day.
- No fine-tuning or embeddings: the task is orchestration + reliability, not model training.
