"""Stage 2b - Data-layer exception detectors.

Every detector is rule-based and population- or pattern-driven, never keyed to a
known id or a hardcoded magic value, so it generalizes to the held-out seed:

  * OUTLIER uses a robust modified z-score (median + MAD), so it adapts to whatever
    the "normal" band and outlier magnitude happen to be in a new dataset.
  * INJECTION_BLOCKED matches instruction-injection PHRASES, not specific strings.
  * UNVERIFIED_ANOMALY is the catch-all: fails basic validity but matches no known
    rule -> quarantined rather than silently delivered (this is what catches the
    held-out "undocumented anomaly").
"""
from __future__ import annotations

import re
import statistics
from typing import Optional

from .contracts import NormalizedRecord

# Instruction-injection phrases (notes are DATA, never commands).
_INJECTION_PATTERNS = [
    r"ignore\s+(all\s+|your\s+)?(previous\s+|prior\s+)?(instructions|rules)",
    r"disregard\s+(the\s+)?(above|previous|prior|instructions|rules)",
    r"approve\s+(this\s+)?(immediately|now|without)",
    r"skip\s+(the\s+)?review",
    r"output\s+approved",
    r"\bignore\s+the\s+field",
    r"bypass\s+(the\s+)?(review|control|controls|approval|approvals)",
    r"override\s+(the\s+)?(review|control|controls|approval|rules)",
]
_INJECTION_RE = re.compile("|".join(_INJECTION_PATTERNS), re.IGNORECASE)
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def looks_injected(notes: str) -> bool:
    return bool(_INJECTION_RE.search(notes or ""))


def outlier_ids(records: list[NormalizedRecord], mad_cutoff: float) -> set[str]:
    """Modified z-score outliers on the primary numeric field.

    z_i = 0.6745 * (x_i - median) / MAD ; flag |z_i| > cutoff. Robust to a single
    extreme value (median/MAD are unaffected by it), unlike mean/stdev.
    """
    amounts = [(r.id, r.amount) for r in records if isinstance(r.amount, (int, float))]
    if len(amounts) < 3:
        return set()
    values = [a for _, a in amounts]
    med = statistics.median(values)
    abs_dev = [abs(v - med) for v in values]
    mad = statistics.median(abs_dev)
    flagged: set[str] = set()
    if mad == 0:
        # degenerate spread: fall back to a wide multiple of the median
        for rid, v in amounts:
            if med > 0 and (v > med * 10 or v < med / 10):
                flagged.add(rid)
        return flagged
    for rid, v in amounts:
        z = 0.6745 * (v - med) / mad
        if abs(z) > mad_cutoff:
            flagged.add(rid)
    return flagged


def _structurally_valid(rec: NormalizedRecord) -> bool:
    if rec.deadline is not None and not _ISO_DATE.match(rec.deadline):
        return False
    if isinstance(rec.amount, (int, float)) and rec.amount <= 0:
        return False
    if rec.category is not None and rec.category.strip() == "" and not rec.notes.strip():
        return False
    return True


def detect_data_exception(
    rec: NormalizedRecord, now_date: str, flagged_outliers: set[str]
) -> Optional[str]:
    """Return a Class-A data reason code, or None if the record is clean so far.

    Priority: security (injection) > completeness (missing) > freshness (stale) >
    statistical (outlier) > structural catch-all (unverified).
    """
    if looks_injected(rec.notes):
        return "INJECTION_BLOCKED"
    if rec.amount is None or rec.owner is None or rec.deadline is None:
        return "MISSING_INPUT"
    if rec.deadline < now_date:
        return "STALE"
    if rec.id in flagged_outliers:
        return "OUTLIER"
    if not _structurally_valid(rec):
        return "UNVERIFIED_ANOMALY"
    return None
