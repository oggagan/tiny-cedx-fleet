"""Stage 1 - Intake.

Parse all three source formats (feed.json, .eml, .pdf) into raw field dicts and
persist each to the store. Original field NAMES are preserved verbatim (e.g. an
email that says "Value:" instead of "Amount:") so the normalize stage can detect
SCHEMA_DRIFT rather than silently swallowing it.

Nothing here is hardcoded to the dev seed: any *.eml / *.pdf in inbox/ and any
records in feed.json are ingested generically.
"""
from __future__ import annotations

import email
import json
import re
from pathlib import Path
from typing import Any

from .hashing import sha, sha_bytes
from .store import Store

_KV = re.compile(r"^\s*([A-Za-z][A-Za-z0-9 _-]*?)\s*:\s*(.*?)\s*$")
_VER_IN_NAME = re.compile(r"_v(\d+)", re.IGNORECASE)


def _coerce(value: str) -> Any:
    v = value.strip()
    if v == "" or v.lower() in {"null", "none", "n/a"}:
        return None
    if re.fullmatch(r"-?\d+", v):
        return int(v)
    if re.fullmatch(r"-?\d+\.\d+", v):
        return float(v)
    return v


def _parse_kv_block(text: str) -> dict:
    fields: dict[str, Any] = {}
    for line in text.splitlines():
        m = _KV.match(line)
        if not m:
            continue
        key, val = m.group(1).strip(), m.group(2)
        # Title lines like "Work Request REC-007" have no colon and are skipped.
        fields[key] = _coerce(val)
    return fields


def _version_from(fields: dict, filename: str, default: int = 1) -> int:
    for k in ("Version", "version"):
        if isinstance(fields.get(k), int):
            return fields[k]
    m = _VER_IN_NAME.search(filename)
    return int(m.group(1)) if m else default


def _id_from(fields: dict, filename: str) -> str:
    for k in ("Id", "id", "ID"):
        if fields.get(k):
            return str(fields[k])
    return Path(filename).stem.split("_")[0]


def parse_feed(path: Path) -> list[dict]:
    out = []
    for obj in json.loads(path.read_text(encoding="utf-8")):
        rid = str(obj["id"])
        version = int(obj.get("version", 1))
        fields = {k: v for k, v in obj.items() if k != "id"}
        out.append(
            {
                "id": rid,
                "version": version,
                "source_format": "feed",
                "source_version_hash": sha(obj),
                "fields": fields,
            }
        )
    return out


def parse_eml(path: Path) -> dict:
    raw = path.read_bytes()
    msg = email.message_from_bytes(raw)
    body = msg.get_payload(decode=False)
    if isinstance(body, list):  # multipart, take first text part
        body = body[0].get_payload(decode=False)
    fields = _parse_kv_block(body or "")
    return {
        "id": _id_from(fields, path.name),
        "version": _version_from(fields, path.name),
        "source_format": "eml",
        "source_version_hash": sha_bytes(raw),
        "fields": fields,
    }


def parse_pdf(path: Path) -> dict:
    from pypdf import PdfReader

    raw = path.read_bytes()
    reader = PdfReader(path)
    text = "\n".join((page.extract_text() or "") for page in reader.pages)
    fields = _parse_kv_block(text)
    return {
        "id": _id_from(fields, path.name),
        "version": _version_from(fields, path.name),
        "source_format": "pdf",
        "source_version_hash": sha_bytes(raw),
        "fields": fields,
    }


def run_intake(seed_dir: Path, store: Store) -> list[dict]:
    """Ingest every source under seed_dir, persist, return the raw records."""
    seed_dir = Path(seed_dir)
    records: list[dict] = []

    feed = seed_dir / "feed.json"
    if feed.exists():
        records.extend(parse_feed(feed))

    inbox = seed_dir / "inbox"
    if inbox.exists():
        for f in sorted(inbox.iterdir()):
            if f.suffix.lower() == ".eml":
                records.append(parse_eml(f))
            elif f.suffix.lower() == ".pdf":
                records.append(parse_pdf(f))

    for r in records:
        store.persist_record(
            r["id"], r["version"], r["source_format"], r["source_version_hash"], r
        )
    return records
