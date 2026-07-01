# Loom script (3-5 min, your voice)

Record your screen with the repo open and a terminal ready. Keep it tight; the goal is
to prove YOU understand and own the system.

## 0:00 - 0:30  Intro + scope
"This is my Tiny CEDX agent fleet for Investment Banking, Tier 1, CASE_ID CEDX-BEA205.
It takes messy engagement work-requests, catches every planted problem, and only delivers
work-orders that are grounded, verified, and approved. Three real agents, not one script.
Let me show it running."

## 0:30 - 1:15  One-command run + verify
Run: `docker compose up` (or `make demo`).
"One command, fully offline, replaying committed transcripts so no API key is needed.
Watch the per-record decisions: clean records deliver on the cheap model; the two
ambiguous ones escalate to the reasoner and abstain; STALE, MISSING_INPUT, OUTLIER, and
two injection attempts are quarantined."
Then `make verify` → point at `PASS`. "That is the grader's own gate, unmodified."

## 1:15 - 2:15  Agent topology + Verifier overrules Worker
Open `ARCHITECTURE.md` diagram, then `cedx/agents/`.
"Orchestrator owns the run and is the only agent that can call others. Worker drafts via
the model router. Verifier is independent and grounds every field against the source."
Run `make probe-agent-failure`.
"Here I inject a worker that hallucinates a value and one that returns malformed output.
The Verifier overrules it, the Orchestrator routes it, and neither is ever delivered.
A looping worker gets killed by the step budget as AGENT_LOOP."

## 2:15 - 3:00  A Class-A problem + injection + trace
Run `make trace ID=REC-013`. "Outlier caught by a robust median/MAD score, blocked before
we spend a cent on the model."
Run `make trace ID=REC-014`. "Injection in the notes, quarantined. Notes are data, never
commands."
Run `make trace ID=REC-001`. "A delivered record: worker span, verifier pass, approval
trail draft to approved to delivered, and the delivered fields."

## 3:00 - 3:40  Approval chain + amendment
Run `make probe-approval`.
"Delivery is refused server-side for anything not approved. My CASE_ID amendment adds a
second compliance approval for any amount at or above 47,000; without it, the record is
blocked, not delivered."

## 3:40 - 4:20  Audit, replay, cost
Open `out/audit.json`. "Append-only event log, per-agent traces with tokens and cost, and
every delivered field hashes back to a committed transcript."
Run `make replay ID=REC-016`. "Full data lineage from the log alone, including the schema
drift that was auto-mapped and logged."
"Cost is about $0.0005 per record, roughly $5 per ten-thousand a day; only the genuinely
hard records hit the expensive model."

## 4:20 - 5:00  Ownership close
"Record and replay produce a byte-identical audit; a crash mid-batch resumes to the same
result with no duplicates. Everything except the model calls is real code that runs every
time. I designed the topology, the thresholds, and the controls, and I am ready to extend
any of it live."
