"""LLM and embedding providers behind one small interface.

Add a provider by implementing `complete()` (LLM) or `embed()` (embeddings) and
registering it in `make_llm` / `make_embedder`.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Iterator, Protocol

import httpx

from .config import Settings

log = logging.getLogger("graphrag.providers")


class LLM(Protocol):
    def complete(self, system: str, prompt: str, json_mode: bool = False) -> str: ...

    def stream(self, system: str, prompt: str) -> Iterator[str]: ...


class Embedder(Protocol):
    dim: int

    def embed(self, texts: list[str], query: bool = False) -> list[list[float]]: ...


# ---------------------------------------------------------------- LLMs

class GeminiLLM:
    def __init__(self, model: str, api_key: str):
        from google import genai

        self.client = genai.Client(api_key=api_key)
        self.model = model

    def complete(self, system: str, prompt: str, json_mode: bool = False) -> str:
        from google.genai import types

        config = types.GenerateContentConfig(
            system_instruction=system,
            temperature=0.1,
            response_mime_type="application/json" if json_mode else None,
        )
        response = self.client.models.generate_content(model=self.model, contents=prompt, config=config)
        return response.text or ""

    def stream(self, system: str, prompt: str) -> Iterator[str]:
        from google.genai import types

        config = types.GenerateContentConfig(system_instruction=system, temperature=0.1)
        for chunk in self.client.models.generate_content_stream(model=self.model, contents=prompt, config=config):
            if chunk.text:
                yield chunk.text


class AnthropicLLM:
    def __init__(self, model: str, api_key: str):
        import anthropic

        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = model

    def complete(self, system: str, prompt: str, json_mode: bool = False) -> str:
        if json_mode:
            prompt += "\n\nRespond with a single JSON object and nothing else."
        response = self.client.messages.create(
            model=self.model,
            max_tokens=4096,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        return "".join(block.text for block in response.content if block.type == "text")

    def stream(self, system: str, prompt: str) -> Iterator[str]:
        with self.client.messages.stream(
            model=self.model,
            max_tokens=4096,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        ) as stream:
            yield from stream.text_stream


class OllamaLLM:
    def __init__(self, model: str, url: str):
        self.model = model
        self.url = url

    def complete(self, system: str, prompt: str, json_mode: bool = False) -> str:
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
            "stream": False,
            "options": {"temperature": 0.1, "num_ctx": 16384},
        }
        if json_mode:
            body["format"] = "json"
        response = httpx.post(f"{self.url}/api/chat", json=body, timeout=600)
        response.raise_for_status()
        text = response.json()["message"]["content"]
        # Reasoning models (qwen3, deepseek-r1) prepend their thinking.
        return re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()

    def stream(self, system: str, prompt: str) -> Iterator[str]:
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
            "stream": True,
            "options": {"temperature": 0.1, "num_ctx": 16384},
        }
        thinking = False
        with httpx.stream("POST", f"{self.url}/api/chat", json=body, timeout=600) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if not line:
                    continue
                piece = json.loads(line).get("message", {}).get("content", "")
                # Drop <think>...</think> blocks from reasoning models as they stream past.
                while piece:
                    if thinking:
                        end = piece.find("</think>")
                        if end < 0:
                            piece = ""
                        else:
                            thinking, piece = False, piece[end + len("</think>"):]
                    else:
                        start = piece.find("<think>")
                        if start < 0:
                            yield piece
                            piece = ""
                        else:
                            if start:
                                yield piece[:start]
                            thinking, piece = True, piece[start + len("<think>"):]


# ---------------------------------------------------------------- embeddings

class TokenPacer:
    """Keeps an estimated tokens-per-minute budget (≈ chars / 4) so free-tier limits aren't hit."""

    def __init__(self, tokens_per_minute: int):
        import collections

        self.tpm = tokens_per_minute
        self.window: "collections.deque[tuple[float, int]]" = collections.deque()

    def wait(self, tokens: int) -> None:
        import time

        if self.tpm <= 0:
            return
        while True:
            now = time.time()
            while self.window and now - self.window[0][0] > 60:
                self.window.popleft()
            used = sum(t for _, t in self.window)
            if used + tokens <= self.tpm or not self.window:
                self.window.append((now, tokens))
                return
            time.sleep(max(0.5, 60 - (now - self.window[0][0]) + 0.2))


class GeminiEmbedder:
    batch_size = 50

    def __init__(self, model: str, api_key: str, dim: int, tokens_per_minute: int = 0):
        from google import genai

        self.client = genai.Client(api_key=api_key)
        self.model = model
        self.dim = dim
        self.pacer = TokenPacer(tokens_per_minute)

    def embed(self, texts: list[str], query: bool = False) -> list[list[float]]:
        from google.genai import types

        config = types.EmbedContentConfig(
            task_type="RETRIEVAL_QUERY" if query else "RETRIEVAL_DOCUMENT",
            output_dimensionality=self.dim,
        )
        vectors = []
        for i in range(0, len(texts), self.batch_size):
            self.pacer.wait(sum(len(t) for t in texts[i : i + self.batch_size]) // 4 + 1)
            response = _with_backoff(lambda: self.client.models.embed_content(
                model=self.model, contents=texts[i : i + self.batch_size], config=config
            ))
            vectors.extend(e.values for e in response.embeddings)
        return _check_dim(vectors, self.dim, self.model)


class OllamaEmbedder:
    batch_size = 32

    def __init__(self, model: str, url: str, dim: int):
        self.model = model
        self.url = url
        self.dim = dim

    def embed(self, texts: list[str], query: bool = False) -> list[list[float]]:
        if "nomic" in self.model:  # nomic-embed-text expects task prefixes
            prefix = "search_query: " if query else "search_document: "
            texts = [prefix + t for t in texts]
        vectors = []
        for i in range(0, len(texts), self.batch_size):
            response = httpx.post(
                f"{self.url}/api/embed",
                json={"model": self.model, "input": texts[i : i + self.batch_size]},
                timeout=600,
            )
            response.raise_for_status()
            vectors.extend(response.json()["embeddings"])
        return _check_dim(vectors, self.dim, self.model)


TRANSIENT = ("RemoteProtocolError", "ConnectError", "ReadTimeout", "ConnectTimeout", "ServerDisconnected",
             "503", "500 INTERNAL", "UNAVAILABLE", "Server disconnected", "Connection reset")


def _with_backoff(call, attempts: int = 8):
    """Retry per-minute rate limits (429) with the server's suggested delay and transient network/5xx
    errors with exponential backoff; give up immediately on daily quotas."""
    import time

    for attempt in range(attempts):
        try:
            return call()
        except Exception as exc:
            text = f"{type(exc).__name__}: {exc}"
            rate_limited = "429" in text or "RESOURCE_EXHAUSTED" in text
            transient = any(t in text for t in TRANSIENT)
            if not (rate_limited or transient) or "PerDay" in text or attempt == attempts - 1:
                raise
            if transient and not rate_limited:
                delay = min(60.0, 3 * 2 ** attempt)
                log.warning("network error (%s); retrying in %.0fs (attempt %d/%d)", type(exc).__name__, delay, attempt + 1, attempts)
                time.sleep(delay)
                continue
            match = re.search(r"retry in ([0-9.]+)s", text) or re.search(r"'retryDelay': '([0-9.]+)s'", text)
            delay = min(90.0, float(match.group(1)) + 1) if match else min(60.0, 5 * 2 ** attempt)
            log.warning("rate limited; retrying in %.0fs (attempt %d/%d)", delay, attempt + 1, attempts)
            time.sleep(delay)


class CachedEmbedder:
    """Disk cache (SQLite) in front of an embedder: re-runs and re-syncs only embed new or changed text."""

    def __init__(self, inner, path, model_key: str):
        import sqlite3
        from pathlib import Path

        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.inner, self.dim, self.model_key = inner, inner.dim, model_key
        self.budget: int | None = None   # max NEW embeddings for this run (None = unlimited); cache hits are free
        self.skipped = 0                 # texts left without a vector because the budget ran out
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.execute("CREATE TABLE IF NOT EXISTS emb (key TEXT PRIMARY KEY, vec BLOB)")

    def _key(self, text: str, query: bool) -> str:
        import hashlib

        return hashlib.sha256(f"{self.model_key}|{self.dim}|{int(query)}|{text}".encode()).hexdigest()

    def embed(self, texts: list[str], query: bool = False) -> list[list[float]]:
        import array

        keys = [self._key(t, query) for t in texts]
        found = {}
        for i in range(0, len(keys), 500):
            chunk = keys[i : i + 500]
            rows = self.db.execute(f"SELECT key, vec FROM emb WHERE key IN ({','.join('?' * len(chunk))})", chunk)
            found.update({k: array.array("f", v).tolist() for k, v in rows})
        missing = [i for i, k in enumerate(keys) if k not in found]
        from . import usage

        usage.record("embed_cached", len(keys) - len(missing))
        left = usage.remaining("embed")  # never exceed today's limit: the rest stays pending
        if self.budget is not None or left is not None:
            allowed = min(x for x in (self.budget, left) if x is not None)
            allowed = max(0, allowed)
            self.skipped += max(0, len(missing) - allowed)
            missing = missing[:allowed]
            if self.budget is not None:
                self.budget -= len(missing)
        if missing:
            vectors = self.inner.embed([texts[i] for i in missing], query=query)
            self.db.executemany("INSERT OR REPLACE INTO emb VALUES (?, ?)",
                                [(keys[i], array.array("f", v).tobytes()) for i, v in zip(missing, vectors)])
            self.db.commit()
            found.update({keys[i]: v for i, v in zip(missing, vectors)})
        return [found.get(k) for k in keys]  # None = over budget, stored without a vector ("pending")


def _check_dim(vectors: list[list[float]], dim: int, model: str) -> list[list[float]]:
    if vectors and len(vectors[0]) != dim:
        raise ValueError(f"{model} returned {len(vectors[0])}-d vectors but EMBED_DIM={dim}")
    return [list(v) for v in vectors]


# ---------------------------------------------------------------- factories

def make_llm(s: Settings) -> LLM:
    from .usage import CachedLLM, MeteredLLM

    if s.llm_provider == "gemini":
        inner = GeminiLLM(s.llm_model, s.gemini_api_key)
    elif s.llm_provider == "anthropic":
        inner = AnthropicLLM(s.llm_model, s.anthropic_api_key)
    else:
        inner = OllamaLLM(s.llm_model, s.ollama_url)
    llm = MeteredLLM(inner, s.llm_provider)
    return CachedLLM(llm, s.llm_cache, f"{s.llm_provider}:{s.llm_model}") if s.llm_cache else llm


def make_embedder(s: Settings) -> Embedder:
    from .usage import MeteredEmbedder

    if s.embed_provider == "gemini":
        inner = GeminiEmbedder(s.embed_model, s.gemini_api_key, s.embed_dim, tokens_per_minute=s.embed_tpm)
    else:
        inner = OllamaEmbedder(s.embed_model, s.ollama_url, s.embed_dim)
    inner = MeteredEmbedder(inner, s.embed_provider)
    if not s.embed_cache:
        return inner
    return CachedEmbedder(inner, s.embed_cache, f"{s.embed_provider}:{s.embed_model}")


def parse_json(text: str):
    """Parse the first JSON object/array in an LLM reply."""
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"(\{.*\}|\[.*\])", text, re.S)
        if not match:
            raise
        return json.loads(match.group(1))
