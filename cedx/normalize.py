"""Stage 2a - Declarative normalization.

Raw source fields (with their original names) are mapped to the canonical schema
using schema/field_map.json. When a canonical field is recovered through an alias
that is NOT its canonical name (e.g. "Value" -> amount), that is a mid-batch rename:
we still map it, but flag SCHEMA_DRIFT so it is logged (Class-B, continues).

The mapping lives in data, not code, so the held-out set's different rename names
are absorbed by editing the table's alias lists, never the logic.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from .contracts import NormalizedRecord


@lru_cache(maxsize=1)
def load_field_map(schema_dir_str: str) -> dict:
    path = Path(schema_dir_str) / "field_map.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _alias_index(field_map: dict) -> dict[str, tuple[str, str]]:
    """lowercased alias -> (canonical_field, alias_used)."""
    idx: dict[str, tuple[str, str]] = {}
    for canonical, aliases in field_map["canonical"].items():
        for alias in aliases:
            idx[alias.lower()] = (canonical, alias.lower())
    return idx


def normalize_record(raw: dict, schema_dir: Path) -> tuple[NormalizedRecord, bool]:
    field_map = load_field_map(str(schema_dir))
    idx = _alias_index(field_map)

    canonical: dict = {"owner": None, "deadline": None, "category": None,
                       "notes": "", "amount": None, "version": None}
    drift_fields: list[str] = []

    for key, value in raw.get("fields", {}).items():
        hit = idx.get(str(key).lower())
        if not hit:
            continue
        canon_name, _alias = hit
        canonical[canon_name] = value
        # drift = a canonical field arrived under a different name than its own
        if str(key).lower() != canon_name and canon_name not in drift_fields:
            drift_fields.append(canon_name)

    amount = canonical["amount"]
    if isinstance(amount, str):
        try:
            amount = float(amount)
        except ValueError:
            amount = None

    version = raw.get("version") or canonical["version"] or 1

    rec = NormalizedRecord(
        id=str(raw["id"]),
        version=int(version),
        owner=(str(canonical["owner"]) if canonical["owner"] is not None else None),
        deadline=(str(canonical["deadline"]) if canonical["deadline"] is not None else None),
        category=(str(canonical["category"]) if canonical["category"] is not None else None),
        notes=str(canonical["notes"] or ""),
        amount=(float(amount) if amount is not None else None),
        source_format=raw["source_format"],
        source_version_hash=raw["source_version_hash"],
        raw=raw.get("fields", {}),
        drift_fields=drift_fields,
    )
    return rec, bool(drift_fields)
