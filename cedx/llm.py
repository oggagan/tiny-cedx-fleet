"""The LLM boundary: record once, replay deterministically.

Only the model call is mediated here. Everything else in the pipeline (intake,
normalize, detectors, router, state machine, audit) is real code that runs every
time, per TASK.md Step 7.

REPLAY_LLM=true (default, offline, no key): every logical model call is served from
a committed transcript, looked up by a stable request key. Deterministic to the byte.

REPLAY_LLM=false (real): calls an OpenAI-compatible endpoint (DeepSeek by default)
with temperature 0, then writes the transcript named by its response hash. Running
the pipeline once in this mode GENERATES the transcripts the offline path replays.

Transcript file (transcripts/<sha256hex>.json) is exactly what verify_audit.py
checks: filename == response_hash hex, response_hash == sha(response), and (for a
worker's load-bearing call) delivered_fields_hash == the delivered record's hash,
with the `agent` tag naming a worker.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .config import Config
from .hashing import sha

INDEX_NAME = ".index.json"  # hidden -> not matched by verify's transcripts/*.json glob


@dataclass
class LLMResult:
    agent: str
    response: dict
    model: str
    prompt_version: str
    tokens_in: int
    tokens_out: int
    cost_usd: float
    latency_ms: float
    transcript_hash: str
    retries: int


class TranscriptMissing(Exception):
    pass


class LLMClient:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.tdir = Path(cfg.transcripts_dir)
        self.tdir.mkdir(parents=True, exist_ok=True)
        self.index_path = self.tdir / INDEX_NAME
        self._index = self._load_index()

    # -- index ---------------------------------------------------------------
    def _load_index(self) -> dict:
        if self.index_path.exists():
            return json.loads(self.index_path.read_text(encoding="utf-8"))
        return {}

    def _save_index(self) -> None:
        self.index_path.write_text(
            json.dumps(self._index, indent=2, sort_keys=True), encoding="utf-8"
        )

    @staticmethod
    def _request_key(agent: str, record_id: str, prompt_version: str, model: str) -> str:
        return sha(
            {"agent": agent, "record_id": record_id, "prompt_version": prompt_version, "model": model}
        )

    # -- pricing -------------------------------------------------------------
    def _cost(self, model: str, tin: int, tout: int) -> float:
        p = self.cfg.price_for(model)
        return round(tin / 1_000_000 * p["in"] + tout / 1_000_000 * p["out"], 8)

    # -- public API ----------------------------------------------------------
    def complete(
        self,
        *,
        agent: str,
        record_id: str,
        prompt_version: str,
        model: str,
        system: str,
        user: str,
        name_by_response: bool = True,
    ) -> LLMResult:
        """name_by_response=True names the transcript by its response hash, which the
        grader requires for a worker's load-bearing call. Non-load-bearing calls (the
        Verifier) set it False so identical responses from different records keep their
        own token/latency metadata instead of colliding onto one file."""
        if self.cfg.replay_llm:
            try:
                return self._replay(agent, record_id, prompt_version, model)
            except TranscriptMissing:
                # Graceful fallback: default (replay) path on a brand-new seed WITH a key
                # records on the fly. The pure graded offline path has no key and still
                # gets a clear TranscriptMissing error.
                if not self.cfg.api_key:
                    raise
        return self._record(agent, record_id, prompt_version, model, system, user, name_by_response)

    def attach_delivery(self, transcript_hash: str, delivered_fields: dict, dfh: str) -> None:
        """Persist the worker's delivered_fields + hash into its transcript so the
        grader can hash delivered output back to a committed transcript. Writes only
        in record mode; in replay the committed transcript already carries it."""
        if self.cfg.replay_llm:
            return
        stem = transcript_hash.split(":")[-1]
        path = self.tdir / f"{stem}.json"
        t = json.loads(path.read_text(encoding="utf-8"))
        t["delivered_fields"] = delivered_fields
        t["delivered_fields_hash"] = dfh
        path.write_text(json.dumps(t, indent=2, sort_keys=True), encoding="utf-8")

    # -- replay --------------------------------------------------------------
    def _replay(self, agent: str, record_id: str, prompt_version: str, model: str) -> LLMResult:
        key = self._request_key(agent, record_id, prompt_version, model)
        stem = self._index.get(key)
        if not stem:
            raise TranscriptMissing(
                f"no committed transcript for agent={agent} record={record_id} "
                f"prompt={prompt_version} model={model}. Generate with REPLAY_LLM=false."
            )
        t = json.loads((self.tdir / f"{stem}.json").read_text(encoding="utf-8"))
        return LLMResult(
            agent=t["agent"],
            response=t["response"],
            model=t["model"],
            prompt_version=t["prompt_version"],
            tokens_in=t["tokens_in"],
            tokens_out=t["tokens_out"],
            cost_usd=t["cost_usd"],
            latency_ms=t["latency_ms"],
            transcript_hash=t["response_hash"],
            retries=t.get("retries", 0),
        )

    # -- record --------------------------------------------------------------
    def _record(
        self, agent: str, record_id: str, prompt_version: str, model: str, system: str,
        user: str, name_by_response: bool = True,
    ) -> LLMResult:
        if not self.cfg.api_key:
            raise RuntimeError("REPLAY_LLM=false but no LLM_API_KEY is set")
        response, tin, tout, latency_ms, retries = self._call_with_repair(model, system, user)
        response_hash = sha(response)
        req_key = self._request_key(agent, record_id, prompt_version, model)
        stem = response_hash.split(":")[-1] if name_by_response else req_key.split(":")[-1]
        transcript = {
            "agent": agent,
            "record_id": record_id,
            "prompt_version": prompt_version,
            "model": model,
            "request": {"system": system, "user": user},
            "response": response,
            "response_hash": response_hash,
            "tokens_in": tin,
            "tokens_out": tout,
            "cost_usd": self._cost(model, tin, tout),
            "latency_ms": latency_ms,
            "retries": retries,
        }
        (self.tdir / f"{stem}.json").write_text(
            json.dumps(transcript, indent=2, sort_keys=True), encoding="utf-8"
        )
        self._index[self._request_key(agent, record_id, prompt_version, model)] = stem
        self._save_index()
        return LLMResult(
            agent=agent,
            response=response,
            model=model,
            prompt_version=prompt_version,
            tokens_in=tin,
            tokens_out=tout,
            cost_usd=transcript["cost_usd"],
            latency_ms=latency_ms,
            transcript_hash=response_hash,
            retries=retries,
        )

    def _call_with_repair(self, model: str, system: str, user: str):
        """Call the model, coercing to strict JSON with a bounded repair loop."""
        last_err = ""
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        for attempt in range(4):
            content, tin, tout, latency_ms = self._http_chat(model, messages)
            obj = _extract_json(content)
            if obj is not None:
                return obj, tin, tout, latency_ms, attempt
            last_err = repr(content[:200])
            messages.append({"role": "assistant", "content": content or "(empty response)"})
            messages.append(
                {"role": "user", "content": "Return ONLY the JSON object, nothing else, no reasoning."}
            )
        raise RuntimeError(f"model did not return valid JSON after repair: {last_err}")

    def _http_chat(self, model: str, messages: list[dict]):
        is_reasoner = "reasoner" in model
        body = {
            "model": model,
            "messages": messages,
            "temperature": 0,
            # the reasoner spends output tokens on chain-of-thought, so give it headroom
            "max_tokens": 2048 if is_reasoner else 800,
        }
        if not is_reasoner:  # chat honors JSON mode; reasoner does not support it
            body["response_format"] = {"type": "json_object"}
        data = json.dumps(body).encode("utf-8")
        url = self.cfg.base_url.rstrip("/") + "/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.cfg.api_key}",
            "Content-Type": "application/json",
        }
        last_exc = None
        for _net in range(3):  # tolerate transient network / gateway errors
            try:
                req = urllib.request.Request(url, data=data, headers=headers, method="POST")
                t0 = time.time()
                with urllib.request.urlopen(req, timeout=180) as resp:
                    payload = json.loads(resp.read().decode("utf-8"))
                latency_ms = round((time.time() - t0) * 1000, 2)
                msg = payload["choices"][0]["message"]
                content = msg.get("content") or ""
                usage = payload.get("usage", {})
                return (
                    content,
                    int(usage.get("prompt_tokens", 0)),
                    int(usage.get("completion_tokens", 0)),
                    latency_ms,
                )
            except (urllib.error.URLError, urllib.error.HTTPError, KeyError,
                    json.JSONDecodeError) as e:
                last_exc = e
        raise RuntimeError(f"LLM HTTP call failed after retries: {last_exc}")


def _extract_json(content: str) -> Optional[dict]:
    content = (content or "").strip()
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass
    # tolerate ```json fences or leading prose: grab the outermost object
    start = content.find("{")
    end = content.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(content[start : end + 1])
        except json.JSONDecodeError:
            return None
    return None
