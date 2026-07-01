"""Canonical hashing.

These two functions MUST match verify_audit.py byte-for-byte, because the grader
recomputes delivered_fields_hash and response_hash with its own copy and compares.
Canonical form: JSON, sorted keys, tight separators, non-ASCII preserved.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any


def canon(obj: Any) -> bytes:
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def sha(obj: Any) -> str:
    return "sha256:" + hashlib.sha256(canon(obj)).hexdigest()


def sha_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()
