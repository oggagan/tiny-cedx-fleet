"""Stage 5 - Delivery.

Writes the branded engagement work-orders for every delivered record and a manifest,
then returns the output_package_hash. The hash is over the canonical manifest (case id
+ each work-order's delivered_fields_hash), so the package is tamper-evident and
reproducible.
"""
from __future__ import annotations

import json
from pathlib import Path

from .config import PIPELINE_VERSION
from .hashing import sha

_BRAND = "CEDX Systems - Investment Banking (Boutique & Mid-Market)"


def _render_work_order(case_id: str, df: dict) -> str:
    return (
        f"# {_BRAND}\n"
        f"## Engagement Work-Order - {df['record_id']}\n\n"
        f"- **Case:** {case_id}\n"
        f"- **Client owner:** {df['client_owner']}\n"
        f"- **Engagement type:** {df['engagement_category']}\n"
        f"- **Value:** {df['primary_amount_usd']:.2f} {df['currency']}\n"
        f"- **Deadline:** {df['deadline']}\n\n"
        f"**Summary.** {df['summary']}\n\n"
        f"---\n_Assembled by the CEDX agent fleet; verified and approved before release._\n"
    )


def write_package(out_dir: Path, case_id: str, delivered_rows: list[dict]) -> str:
    """delivered_rows: audit row dicts with delivered_fields + delivered_fields_hash."""
    pkg = Path(out_dir) / "package"
    pkg.mkdir(parents=True, exist_ok=True)
    work_orders = []
    for row in sorted(delivered_rows, key=lambda r: r["id"]):
        df = row["delivered_fields"]
        (pkg / f"{row['id']}_work_order.md").write_text(
            _render_work_order(case_id, df), encoding="utf-8"
        )
        work_orders.append(
            {
                "record_id": df["record_id"],
                "delivered_fields_hash": row["delivered_fields_hash"],
                "amount_usd": df["primary_amount_usd"],
                "deadline": df["deadline"],
            }
        )
    manifest = {
        "case_id": case_id,
        "generator": PIPELINE_VERSION,
        "brand": _BRAND,
        "count": len(work_orders),
        "work_orders": work_orders,
    }
    (pkg / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
    )
    return sha(manifest)
