"""Daily model-usage meter shared by the server and job subprocesses (data/usage.json, file-locked).

Counts are this app's own calls (cache hits are free and counted separately), so they're an estimate of
the provider's quota, not the provider's own counter: other apps using the same key aren't seen.
"""
from __future__ import annotations

import fcntl
import json
import os
from contextlib import contextmanager
from datetime import date
from pathlib import Path

USAGE_FILE = Path(__file__).resolve().parent.parent / "data" / "usage.json"
KEEP_DAYS = 30


class DailyLimitReached(RuntimeError):
    """Raised before a call that would exceed the configured daily limit."""


@contextmanager
def _locked():
    USAGE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(USAGE_FILE, "a+") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            fh.seek(0)
            raw = fh.read()
            try:
                data = json.loads(raw) if raw.strip() else {}
            except ValueError:
                data = {}
            yield data
            for day in sorted(data)[:-KEEP_DAYS]:
                data.pop(day, None)
            fh.seek(0)
            fh.truncate()
            fh.write(json.dumps(data, indent=1))
            fh.flush()
            os.fsync(fh.fileno())
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def today() -> str:
    return date.today().isoformat()


def record(kind: str, n: int = 1, provider: str = "") -> None:
    """kind: llm | llm_cached | embed | embed_cached."""
    if n <= 0:
        return
    with _locked() as data:
        day = data.setdefault(today(), {})
        day[kind] = day.get(kind, 0) + n
        if provider:
            day.setdefault("by_provider", {})
            key = f"{provider}:{kind}"
            day["by_provider"][key] = day["by_provider"].get(key, 0) + n


def counts(day: str | None = None) -> dict:
    try:
        data = json.loads(USAGE_FILE.read_text())
    except (OSError, ValueError):
        data = {}
    return data.get(day or today(), {})


def history(days: int = 14) -> dict:
    try:
        data = json.loads(USAGE_FILE.read_text())
    except (OSError, ValueError):
        data = {}
    return {d: data[d] for d in sorted(data)[-days:]}


def _limits():
    from .config import get_settings

    s = get_settings()
    return s


def remaining(kind: str) -> int | None:
    """Calls left today under the configured limit (None = unlimited / not enforced / local model)."""
    s = _limits()
    if not s.enforce_daily_limits:
        return None
    if kind == "llm":
        if s.llm_provider == "ollama" or not s.llm_daily_limit:
            return None
        return max(0, s.llm_daily_limit - counts().get("llm", 0))
    if s.embed_provider == "ollama" or not s.embed_daily_limit:
        return None
    return max(0, s.embed_daily_limit - counts().get("embed", 0))


def summary() -> dict:
    s = _limits()
    c = counts()
    local_llm, local_embed = s.llm_provider == "ollama", s.embed_provider == "ollama"
    return {
        "date": today(),
        "enforced": s.enforce_daily_limits,
        "llm": {"used": c.get("llm", 0), "cached": c.get("llm_cached", 0),
                "limit": None if local_llm else s.llm_daily_limit, "local": local_llm,
                "remaining": remaining("llm"), "model": f"{s.llm_provider}:{s.llm_model}"},
        "embeddings": {"used": c.get("embed", 0), "cached": c.get("embed_cached", 0),
                       "limit": None if local_embed else s.embed_daily_limit, "local": local_embed,
                       "remaining": remaining("embed"), "model": f"{s.embed_provider}:{s.embed_model}"},
    }


# ---------------------------------------------------------------- provider wrappers

class MeteredLLM:
    """Counts real model calls and refuses new ones once today's limit is used up."""

    def __init__(self, inner, provider: str):
        self.inner, self.provider = inner, provider

    def _check(self) -> None:
        left = remaining("llm")
        if left is not None and left <= 0:
            s = _limits()
            raise DailyLimitReached(
                f"Today's answer-model limit is used up ({s.llm_daily_limit}/{s.llm_daily_limit} requests). "
                "Graph-only mode and cached answers still work. Raise LLM_DAILY_LIMIT in Settings if your plan allows more.")

    def complete(self, system: str, prompt: str, json_mode: bool = False) -> str:
        self._check()
        record("llm", 1, self.provider)
        return self.inner.complete(system, prompt, json_mode)

    def stream(self, system: str, prompt: str):
        self._check()
        record("llm", 1, self.provider)
        yield from self.inner.stream(system, prompt)


class MeteredEmbedder:
    """Counts embedded items (each batch item counts against the free tier)."""

    def __init__(self, inner, provider: str):
        self.inner, self.provider, self.dim = inner, provider, inner.dim

    def embed(self, texts: list[str], query: bool = False):
        left = remaining("embed")
        if left is not None and len(texts) > left:
            s = _limits()
            raise DailyLimitReached(
                f"Today's embedding limit would be exceeded ({counts().get('embed', 0)}/{s.embed_daily_limit} used, "
                f"{len(texts)} requested). Raise EMBED_DAILY_LIMIT in Settings, or continue tomorrow.")
        record("embed", len(texts), self.provider)
        return self.inner.embed(texts, query=query)


class CachedLLM:
    """SQLite cache keyed on (model, system, prompt): asking the same thing over the same context is free."""

    def __init__(self, inner, path, model_key: str):
        import sqlite3
        import threading

        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.inner, self.model_key = inner, model_key
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.execute("CREATE TABLE IF NOT EXISTS llm (key TEXT PRIMARY KEY, reply TEXT, created REAL)")
        self.lock = threading.Lock()

    def _key(self, system: str, prompt: str, json_mode: bool) -> str:
        import hashlib

        return hashlib.sha256(f"{self.model_key}|{int(json_mode)}|{system}|{prompt}".encode()).hexdigest()

    def _get(self, key: str) -> str | None:
        with self.lock:
            row = self.db.execute("SELECT reply FROM llm WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def _put(self, key: str, reply: str) -> None:
        import time

        if not reply.strip():
            return
        with self.lock:
            self.db.execute("INSERT OR REPLACE INTO llm VALUES (?, ?, ?)", (key, reply, time.time()))
            self.db.commit()

    def cached(self, system: str, prompt: str, json_mode: bool = False) -> str | None:
        return self._get(self._key(system, prompt, json_mode))

    def complete(self, system: str, prompt: str, json_mode: bool = False) -> str:
        key = self._key(system, prompt, json_mode)
        hit = self._get(key)
        if hit is not None:
            record("llm_cached")
            return hit
        reply = self.inner.complete(system, prompt, json_mode)
        self._put(key, reply)
        return reply

    def stream(self, system: str, prompt: str):
        key = self._key(system, prompt, False)
        hit = self._get(key)
        if hit is not None:
            record("llm_cached")
            for i in range(0, len(hit), 120):  # replay in pieces so the UI still streams
                yield hit[i : i + 120]
            return
        parts = []
        for piece in self.inner.stream(system, prompt):
            parts.append(piece)
            yield piece
        self._put(key, "".join(parts))

    def clear(self) -> int:
        with self.lock:
            n = self.db.execute("SELECT count(*) FROM llm").fetchone()[0]
            self.db.execute("DELETE FROM llm")
            self.db.commit()
        return n
