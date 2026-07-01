"""Stage 4 - the approval state machine + CASE_ID amendment.

An explicit, enforced state machine gates delivery. Nothing is delivered unless it
reaches `approved`, and the transition rules are checked in code, so `probe-approval`
can prove a non-approved item is refused server-side.

The amendment (maker-checker) adds a SECOND approval by role R for any record whose
amount is >= threshold T: without that recorded second approval the record is blocked,
not delivered.

Transitions:
    draft -> in_review -> {changes_requested -> in_review | approved | blocked}
    in_review -> in_review        (additional approver, e.g. amendment checker)
    approved -> delivered
    blocked  -> (terminal)
"""
from __future__ import annotations

from typing import Optional

from .amendment import Amendment
from .clock import LogicalClock

_ALLOWED = {
    None: {"draft"},
    "draft": {"in_review", "blocked"},
    "in_review": {"in_review", "changes_requested", "approved", "blocked"},
    "changes_requested": {"in_review", "blocked"},
    "approved": {"delivered"},
    "delivered": set(),
    "blocked": set(),
}


class ApprovalError(Exception):
    pass


class ApprovalChain:
    def __init__(self, amendment: Amendment, clock: LogicalClock):
        self.amendment = amendment
        self.clock = clock
        self.state: Optional[str] = None
        self.trail: list[dict] = []
        self.events: list[tuple] = []  # (actor, action, ts)

    def _to(self, state: str, actor: str, reason: Optional[str] = None) -> None:
        if state not in _ALLOWED.get(self.state, set()):
            raise ApprovalError(f"illegal approval transition {self.state} -> {state}")
        ts = self.clock.tick()
        self.state = state
        self.trail.append({"state": state, "actor": actor, "ts": ts, "reason": reason})
        self.events.append((actor, f"approval:{state}", ts))

    # -- explicit steps (used by probes for precise control) -----------------
    def open(self) -> None:
        self._to("draft", "system", "record entered review")

    def submit(self) -> None:
        self._to("in_review", "operator:reviewer", "submitted for review")

    def block(self, reason: str, actor: str = "system") -> None:
        self._to("blocked", actor, reason)

    def second_approval(self) -> None:
        self._to("in_review", f"{self.amendment.role}:officer",
                 f"amendment second approval (amount >= {self.amendment.threshold})")

    def approve(self) -> None:
        self._to("approved", "operator:reviewer", "approved for delivery")

    def deliver(self) -> None:
        if self.state != "approved":
            raise ApprovalError("delivery refused: record is not in 'approved' state")
        self._to("delivered", "system", "delivered")

    def can_deliver(self) -> bool:
        return self.state == "approved"

    # -- automated operator policy for the batch demo ------------------------
    def auto_review(
        self,
        verifier_pass: bool,
        amount: Optional[float],
        grant_normal: bool = True,
        grant_second: bool = True,
    ) -> str:
        """Drive the record through review the way the demo operator would. Returns
        the final state. Withhold grant_normal/grant_second to exercise refusals."""
        self.open()
        self.submit()
        if not verifier_pass:
            self.block("verifier did not pass")
            return self.state
        if not grant_normal:
            return self.state  # stuck in_review: not approved -> cannot deliver
        if self.amendment.applies_to(amount):
            if not grant_second:
                self.block(
                    f"amendment: {self.amendment.role} second approval required "
                    f"for amount >= {self.amendment.threshold}"
                )
                return self.state
            self.second_approval()
        self.approve()
        return self.state
