"""The live CASE_ID amendment (maker-checker second-approval gate).

Given a CASE_ID, derive the second-approver role R and the value threshold T
exactly as specified in TASK.md Step 8:

    H = sha256(CASE_ID)  # lowercase hex
    R = ROLES[int(H[0], 16) % 4]
    T = 10000 + (int(H[1:3], 16) % 50) * 1000

Any record whose normalized primary numeric field (amount) is >= T needs a
recorded approval by role R, in addition to the normal operator approval, before
delivery. Leaking someone else's answer is useless: R and T are bound to YOUR id.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass

from .config import AMENDMENT_ROLES


@dataclass(frozen=True)
class Amendment:
    role: str
    threshold: int

    def applies_to(self, amount: float | None) -> bool:
        return amount is not None and amount >= self.threshold

    def to_dict(self) -> dict:
        return {"role": self.role, "threshold": self.threshold}


def compute_amendment(case_id: str) -> Amendment:
    h = hashlib.sha256(case_id.encode("utf-8")).hexdigest()
    role = AMENDMENT_ROLES[int(h[0], 16) % 4]
    threshold = 10000 + (int(h[1:3], 16) % 50) * 1000
    return Amendment(role=role, threshold=threshold)
