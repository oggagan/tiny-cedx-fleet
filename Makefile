# Uniform probe interface. Graders invoke these targets identically on every repo.
# PY lets you point at a venv locally (make demo PY=.venv/bin/python); in Docker the
# image's python3 already has the deps, so the default is correct.
PY ?= python3
SEED_DIR ?= seed
export SEED_DIR

.PHONY: demo verify trace eval replay probe-approval probe-agent-failure probe-budget \
        probe-append-only probe-idempotency probe-crash probes clean

# Full multi-agent pipeline, offline replay, on $(SEED_DIR). Writes out/package/,
# out/audit.json (agents roster + per-record agent_trace + cost), out/exception_queue.json.
demo:
	$(PY) -m cedx.cli demo

# Run the PROVIDED gate on your audit bundle. Do not modify verify_audit.py.
verify:
	$(PY) verify_audit.py --audit out/audit.json --transcripts transcripts --schema audit.schema.json

# Print one record's FULL agent decision path from the log alone.
trace:
	$(PY) -m cedx.cli trace --id $(ID)

# Agent eval harness: >=10 golden cases + an LLM-judge per agent. Prints per-agent scores.
eval:
	$(PY) -m cedx.cli eval

# Reconstruct one delivered output's DATA lineage from the append-only log alone.
replay:
	$(PY) -m cedx.cli replay --id $(ID)

probe-approval:
	$(PY) -m cedx.cli probe approval

probe-agent-failure:
	$(PY) -m cedx.cli probe agent-failure

probe-budget:
	$(PY) -m cedx.cli probe budget

probe-append-only:
	$(PY) -m cedx.cli probe append-only

probe-idempotency:
	$(PY) -m cedx.cli probe idempotency

# Bundle the latest audit into the static observability dashboard (webui/).
dashboard:
	$(PY) -m cedx.cli dashboard

# BONUS. Resumes from the last committed record after a simulated SIGKILL.
probe-crash:
	$(PY) -m cedx.cli probe crash

# Convenience: run every control probe in sequence (stops on first failure).
probes: probe-approval probe-agent-failure probe-budget probe-append-only probe-idempotency probe-crash
	@echo "all probes passed"

clean:
	rm -rf out
