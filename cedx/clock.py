"""A deterministic logical clock.

Wall-clock time would make the audit bundle (and therefore its idempotency) differ
run to run. Instead every timestamp is derived from PIPELINE_NOW plus a monotonic
counter, so two runs on the same seed produce byte-identical audits. This is what
lets probe-idempotency assert "no dupes" by simple equality.
"""
from __future__ import annotations


class LogicalClock:
    def __init__(self, base_date: str):
        self.base = base_date
        self._n = 0

    def rebase(self) -> None:
        """Reset the per-record counter. The pipeline calls this before each record so
        a record's timestamp sequence depends ONLY on that record's own events, not on
        how many records ran before it. That makes the audit order-independent: a
        crash-resumed run and a clean run produce byte-identical records."""
        self._n = 0

    def tick(self) -> str:
        s = self._n
        self._n += 1
        return f"{self.base}T00:{(s // 60) % 60:02d}:{s % 60:02d}Z"

    def now(self) -> str:
        # a stable, non-advancing stamp for one-off fields like generated_at
        return f"{self.base}T00:00:00Z"
