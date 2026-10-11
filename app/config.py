"""Runtime configuration.

APP_MODE picks a preset (local | cloud | full-local). Any env var set explicitly
overrides the preset, so you can e.g. run `local` mode with Anthropic instead of Gemini.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

load_dotenv()

MODES = ("local", "cloud", "full-local")

PRESETS = {
    # Neo4j in local Docker, hosted LLM + embeddings (current Gemini testing setup).
    "local": {"llm_provider": "gemini", "embed_provider": "gemini", "neo4j_uri": "bolt://localhost:7687"},
    # Everything hosted: Neo4j Aura (or any remote Neo4j) + hosted LLM. NEO4J_URI is required.
    "cloud": {"llm_provider": "gemini", "embed_provider": "gemini", "neo4j_uri": ""},
    # Nothing leaves the machine: Neo4j + Ollama on localhost / docker network.
    "full-local": {"llm_provider": "ollama", "embed_provider": "ollama", "neo4j_uri": "bolt://localhost:7687"},
}

LLM_PROVIDERS = ("gemini", "anthropic", "ollama")
EMBED_PROVIDERS = ("gemini", "ollama")
LOCAL_PROVIDERS = {"ollama"}
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "neo4j", "ollama", "host.docker.internal"}

DEFAULT_LLM_MODEL = {"gemini": "gemini-2.5-flash", "anthropic": "claude-sonnet-5-5", "ollama": "qwen2.5:14b"}
DEFAULT_EMBED_MODEL = {"gemini": "gemini-embedding-001", "ollama": "nomic-embed-text"}


def _env(name: str, default=None):
    value = os.getenv(name)
    return value if value not in (None, "") else default


@dataclass(frozen=True)
class Settings:
    mode: str
    llm_provider: str
    llm_model: str
    embed_provider: str
    embed_model: str
    embed_dim: int
    neo4j_uri: str
    neo4j_user: str
    neo4j_password: str
    neo4j_database: str
    gemini_api_key: str | None
    anthropic_api_key: str | None
    ollama_url: str
    scrub_terms: tuple[str, ...]
    fortisoar_version: str
    include_proposed_links: bool
    company_name: str
    embed_tpm: int
    embed_cache: str
    app_username: str
    app_password: str
    session_hours: int

    def validate(self) -> None:
        errors = []
        if self.llm_provider not in LLM_PROVIDERS:
            errors.append(f"LLM_PROVIDER must be one of {LLM_PROVIDERS}")
        if self.embed_provider not in EMBED_PROVIDERS:
            errors.append(f"EMBED_PROVIDER must be one of {EMBED_PROVIDERS}")
        if "gemini" in (self.llm_provider, self.embed_provider) and not self.gemini_api_key:
            errors.append("GEMINI_API_KEY is required for the gemini provider")
        if self.llm_provider == "anthropic" and not self.anthropic_api_key:
            errors.append("ANTHROPIC_API_KEY is required for the anthropic provider")
        if not self.neo4j_uri:
            errors.append("NEO4J_URI is required (cloud mode has no default)")
        if not self.neo4j_password:
            errors.append("NEO4J_PASSWORD is required")
        if self.mode == "cloud" and (self.app_username, self.app_password) == ("admin", "admin"):
            errors.append("cloud mode requires APP_USERNAME/APP_PASSWORD to be changed from admin/admin")
        if self.mode == "full-local":
            # Hard guard: full-local must never send playbook data off the box.
            for kind, provider in (("LLM_PROVIDER", self.llm_provider), ("EMBED_PROVIDER", self.embed_provider)):
                if provider not in LOCAL_PROVIDERS:
                    errors.append(f"full-local mode requires a local {kind}, got '{provider}'")
            for name, url in (("NEO4J_URI", self.neo4j_uri), ("OLLAMA_URL", self.ollama_url)):
                if urlparse(url).hostname not in LOCAL_HOSTS:
                    errors.append(f"full-local mode requires {name} on a local host, got '{url}'")
        if errors:
            raise ValueError("Invalid configuration:\n  - " + "\n  - ".join(errors))

    def describe(self) -> dict:
        return {
            "mode": self.mode,
            "llm": f"{self.llm_provider}:{self.llm_model}",
            "embeddings": f"{self.embed_provider}:{self.embed_model} ({self.embed_dim}d)",
            "neo4j": self.neo4j_uri,
            "fortisoar_version": self.fortisoar_version,
        }


@lru_cache
def get_settings() -> Settings:
    mode = _env("APP_MODE", "local")
    if mode not in MODES:
        raise ValueError(f"APP_MODE must be one of {MODES}, got '{mode}'")
    preset = PRESETS[mode]
    llm_provider = _env("LLM_PROVIDER", preset["llm_provider"])
    embed_provider = _env("EMBED_PROVIDER", preset["embed_provider"])
    settings = Settings(
        mode=mode,
        llm_provider=llm_provider,
        llm_model=_env("LLM_MODEL", DEFAULT_LLM_MODEL.get(llm_provider, "")),
        embed_provider=embed_provider,
        embed_model=_env("EMBED_MODEL", DEFAULT_EMBED_MODEL.get(embed_provider, "")),
        embed_dim=int(_env("EMBED_DIM", "768")),
        neo4j_uri=_env("NEO4J_URI", preset["neo4j_uri"]),
        neo4j_user=_env("NEO4J_USER", "neo4j"),
        neo4j_password=_env("NEO4J_PASSWORD", ""),
        neo4j_database=_env("NEO4J_DATABASE", "neo4j"),
        gemini_api_key=_env("GEMINI_API_KEY"),
        anthropic_api_key=_env("ANTHROPIC_API_KEY"),
        ollama_url=_env("OLLAMA_URL", "http://localhost:11434").rstrip("/"),
        scrub_terms=tuple(t.strip() for t in _env("SCRUB_TERMS", "").split(",") if t.strip()),
        fortisoar_version=_env("FORTISOAR_VERSION", "7.6"),
        include_proposed_links=_env("INCLUDE_PROPOSED_LINKS", "false").lower() == "true",
        company_name=_env("COMPANY_NAME", "our company"),
        # Gemini free tier allows ~30k embedding tokens/minute; 0 disables pacing (paid keys)
        embed_tpm=int(_env("EMBED_TPM", "28000" if embed_provider == "gemini" else "0")),
        embed_cache=_env("EMBED_CACHE", str(Path(__file__).resolve().parent.parent / "data" / ".cache" / "embeddings.sqlite")),
        app_username=_env("APP_USERNAME", "admin"),
        app_password=_env("APP_PASSWORD", "admin"),
        session_hours=int(_env("SESSION_HOURS", "12")),
    )
    settings.validate()
    return settings
