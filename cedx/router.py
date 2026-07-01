"""The model router: cheap by default, escalate only when it pays off.

Policy (declarative, generalizes by SEMANTIC signal, not by id):
  * Default every record to the cheap model (deepseek-chat).
  * Escalate to the strong model (deepseek-reasoner) when either
      - the record shows uncertainty/contradiction markers (needs careful reasoning
        to decide whether to abstain), OR
      - the normalized amount is >= the amendment threshold (high-value: worth the
        extra spend to get right), OR
      - a prior Verifier bounce asked for a retry (handled by the orchestrator).

Escalating only the genuinely hard/high-value records keeps average cost near the
cheap-model floor while protecting quality where it matters.
"""
from __future__ import annotations

import re

from .amendment import Amendment
from .config import Config
from .contracts import NormalizedRecord

_UNCERTAINTY = re.compile(
    r"\b(unclear|inconsistent|conflicting|ambiguous|could be|not attached|tbd|"
    r"side letter|describes a|figures inconsistent)\b|\?",
    re.IGNORECASE,
)


class Router:
    def __init__(self, cfg: Config, amendment: Amendment):
        self.cfg = cfg
        self.amendment = amendment

    def is_hard(self, rec: NormalizedRecord) -> bool:
        if rec.category is not None and rec.category.strip() in {"", "?"}:
            return True
        return bool(_UNCERTAINTY.search(rec.notes or ""))

    def choose(self, rec: NormalizedRecord, force_strong: bool = False) -> tuple[str, str, str]:
        """Return (tier, model, reason)."""
        if force_strong:
            return "strong", self.cfg.model_strong, "verifier_bounce_retry"
        if self.amendment.applies_to(rec.amount):
            return "strong", self.cfg.model_strong, "high_value_ge_threshold"
        if self.is_hard(rec):
            return "strong", self.cfg.model_strong, "uncertainty_markers"
        return "cheap", self.cfg.model_cheap, "clean_record_default"
