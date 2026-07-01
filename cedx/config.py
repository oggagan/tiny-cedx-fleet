"""Central configuration for the Tiny CEDX agent fleet.

Everything the pipeline needs to be deterministic (replay), governed (budgets,
thresholds), and reproducible (a single pinned "now") is read here, once, from the
environment. No module reaches for os.environ on its own.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Pipeline identity ---------------------------------------------------------------
PIPELINE_VERSION = "tiny-cedx-fleet/1.0.0"

# The four amendment roles, in the canonical order used by the CASE_ID algorithm.
AMENDMENT_ROLES = ["risk_officer", "legal_counsel", "compliance", "finance_controller"]


def _get(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _as_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _as_float(name: str, default: float) -> float:
    try:
        return float(os.environ[name])
    except (KeyError, ValueError):
        return default


def _as_int(name: str, default: int) -> int:
    try:
        return int(os.environ[name])
    except (KeyError, ValueError):
        return default


# Per-1M-token USD pricing for the model router. DeepSeek list prices (cache-miss)
# are the reference cheap/strong pair; overridable so the grader's models can be
# priced too. Kept as a table so cost accounting is explicit, not guessed.
MODEL_PRICES = {
    "deepseek-chat": {"in": 0.27, "out": 1.10},
    "deepseek-reasoner": {"in": 0.55, "out": 2.19},
    # grader-side references (approximate public list prices)
    "gpt-4o-mini": {"in": 0.15, "out": 0.60},
    "claude-3-5-haiku": {"in": 0.80, "out": 4.00},
    "gemini-1.5-flash": {"in": 0.075, "out": 0.30},
}
# Fallback price for any unpriced model, so cost is never silently zero.
DEFAULT_PRICE = {"in": 0.50, "out": 1.50}


@dataclass(frozen=True)
class Config:
    # replay vs real
    replay_llm: bool = field(default_factory=lambda: _as_bool("REPLAY_LLM", True))
    provider: str = field(default_factory=lambda: _get("LLM_PROVIDER", "deepseek"))
    api_key: str = field(default_factory=lambda: _get("LLM_API_KEY") or _get("DEEPSEEK_API_KEY"))
    base_url: str = field(default_factory=lambda: _get("LLM_BASE_URL", "https://api.deepseek.com"))
    # cheap default model + the escalate-to-strong model for the router
    model_cheap: str = field(default_factory=lambda: _get("LLM_MODEL", "deepseek-chat"))
    model_strong: str = field(default_factory=lambda: _get("LLM_MODEL_STRONG", "deepseek-reasoner"))

    # data locations
    seed_dir: Path = field(default_factory=lambda: Path(_get("SEED_DIR", str(REPO_ROOT / "seed"))))
    out_dir: Path = field(default_factory=lambda: Path(_get("OUT_DIR", str(REPO_ROOT / "out"))))
    transcripts_dir: Path = field(
        default_factory=lambda: Path(_get("TRANSCRIPTS_DIR", str(REPO_ROOT / "transcripts")))
    )
    schema_dir: Path = field(default_factory=lambda: REPO_ROOT / "schema")

    # governance / amendment
    case_id: str = field(default_factory=lambda: _get("CASE_ID", "CEDX-XXXX"))
    # intake "now" — pins STALE detection so replay is deterministic
    pipeline_now: str = field(default_factory=lambda: _get("PIPELINE_NOW", "2026-06-26"))

    # budgets (per record). Exceeding these raises BUDGET_EXCEEDED / AGENT_LOOP.
    max_cost_usd_per_record: float = field(
        default_factory=lambda: _as_float("MAX_COST_USD_PER_RECORD", 0.02)
    )
    max_steps_per_record: int = field(default_factory=lambda: _as_int("MAX_STEPS_PER_RECORD", 6))

    # outlier detector: modified z-score (robust, median/MAD based) cutoff.
    outlier_mad_cutoff: float = field(default_factory=lambda: _as_float("OUTLIER_MAD_CUTOFF", 3.5))

    @property
    def db_path(self) -> Path:
        return self.out_dir / "state.db"

    def price_for(self, model: str) -> dict:
        return MODEL_PRICES.get(model, DEFAULT_PRICE)


def load_config() -> Config:
    return Config()
