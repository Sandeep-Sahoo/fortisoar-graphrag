"""Settings page backend: field catalogue, validated updates written to .env and applied without restart."""
from __future__ import annotations

import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / ".env"

# key, group, label, type, help, extras
FIELDS = [
    ("COMPANY_NAME", "General", "Company name", "text", "Used in answers (\"In your playbook…\") and prompts."),
    ("FORTISOAR_VERSION", "General", "FortiSOAR version tag", "text", "Version recorded on documents loaded with ingest-docs."),

    ("APP_MODE", "AI models", "Mode", "select", "local: Neo4j here, hosted models · cloud: everything hosted · full-local: Ollama only, nothing leaves the machine.",
     {"options": ["local", "cloud", "full-local"]}),
    ("LLM_PROVIDER", "AI models", "Answer model provider", "select", "Blank uses the mode's default.", {"options": ["", "gemini", "anthropic", "ollama"]}),
    ("LLM_MODEL", "AI models", "Answer model", "text", "e.g. gemini-2.5-flash, gemini-2.5-flash-lite, claude-sonnet-5-5, qwen2.5:14b. Blank = provider default."),
    ("EMBED_PROVIDER", "AI models", "Embedding provider", "select", "Changing embeddings makes stored vectors incompatible: wipe and re-ingest afterwards.",
     {"options": ["", "gemini", "ollama"], "danger": True}),
    ("EMBED_MODEL", "AI models", "Embedding model", "text", "e.g. gemini-embedding-001, nomic-embed-text. Changing it requires wipe + re-ingest.", {"danger": True}),
    ("EMBED_DIM", "AI models", "Embedding dimensions", "number", "Must match the vector index; changing it requires wipe + re-ingest.", {"danger": True}),
    ("EMBED_TPM", "AI models", "Embedding tokens per minute", "number", "Pacing for rate-limited keys (Gemini free tier ≈ 30,000). 0 = no pacing."),
    ("EMBED_CACHE", "AI models", "Embedding cache file", "text", "SQLite cache so re-runs only embed new or changed text."),
    ("OLLAMA_URL", "AI models", "Ollama URL", "text", "Used when a provider is ollama (full-local mode)."),
    ("LLM_DAILY_LIMIT", "AI models", "Answer requests per day", "number", "Your plan's daily answer-model limit (Gemini free tier ≈ 20). 0 = no limit. Local (Ollama) models are never limited."),
    ("EMBED_DAILY_LIMIT", "AI models", "Embeddings per day", "number", "Daily embedding limit; each batch item counts (Gemini free tier ≈ 1,000). 0 = no limit."),
    ("ENFORCE_DAILY_LIMITS", "AI models", "Stop at the daily limits", "bool", "Refuse answer requests and leave embeddings pending once today's limit is used, instead of hitting provider errors."),
    ("LLM_CACHE", "AI models", "Answer cache file", "text", "SQLite cache: the same question over the same context is answered from cache for free. Blank disables it."),
    ("GEMINI_API_KEY", "AI models", "Gemini API key", "secret", "Write-only. Leave blank to keep the current key."),
    ("ANTHROPIC_API_KEY", "AI models", "Anthropic API key", "secret", "Write-only. Leave blank to keep the current key."),

    ("NEO4J_URI", "Database", "Neo4j URI", "text", "bolt://localhost:7687 locally; neo4j+s://<id>.databases.neo4j.io for Aura."),
    ("NEO4J_USER", "Database", "Neo4j user", "text", ""),
    ("NEO4J_PASSWORD", "Database", "Neo4j password", "secret", "Must match the database's password; this does not change the database itself."),
    ("NEO4J_DATABASE", "Database", "Neo4j database", "text", ""),

    ("APP_USERNAME", "Security", "Sign-in username", "text", "Takes effect for the next sign-in."),
    ("APP_PASSWORD", "Security", "Sign-in password", "secret", "Write-only. Cloud mode refuses admin/admin."),
    ("SESSION_HOURS", "Security", "Session length (hours)", "number", "How long a sign-in lasts."),
    ("SCRUB_TERMS", "Security", "Extra terms to redact", "text", "Comma-separated tenant, customer or host names, replaced with <TENANT> at ingestion."),

    ("RETRIEVAL_PLAYBOOKS_K", "Retrieval & chat", "Playbooks per answer", "number", "How many playbooks search returns into the answer context (1–20)."),
    ("RETRIEVAL_VENDOR_K", "Retrieval & chat", "Vendor doc passages per answer", "number", "0 disables vendor docs in answers."),
    ("RETRIEVAL_INTERNAL_K", "Retrieval & chat", "Internal doc passages per answer", "number", "0 disables internal docs in answers."),
    ("CHAT_HISTORY_TURNS", "Retrieval & chat", "Chat history turns", "number", "Earlier messages used to understand follow-ups (0 = every question stands alone)."),
    ("INCLUDE_PROPOSED_LINKS", "Retrieval & chat", "Use unreviewed doc links in answers", "bool", "When off, only approved doc→playbook links are used."),
    ("OVERVIEW_PLAYBOOK_LIMIT", "Retrieval & chat", "Home: switch to ecosystem above N playbooks", "number", "Automatic Home view uses the ecosystem map above this many playbooks."),

    ("LOG_LEVEL", "General", "Log level", "select", "Server log verbosity (console and data/logs/app.log). DEBUG also logs every GET request.",
     {"options": ["", "DEBUG", "INFO", "WARNING", "ERROR"]}),
    ("FETCH_ORG", "Data sources", "GitHub organisation", "text", "Source of playbooks, connectors and docs (github.com/<org>)."),
    ("FETCH_PREFIXES", "Data sources", "Repo name prefixes to fetch", "text", "Comma-separated, e.g. connector-,solution-,widget-,ai-"),
    ("SYNC_SCHEDULE", "Data sources", "Scheduled sync", "select", "Re-download changed GitHub repos (and re-pull connected Git sources) automatically. Uses GitHub only, never the AI model.",
     {"options": ["off", "daily", "weekly"]}),
    ("SYNC_AUTO_INGEST", "Data sources", "Re-ingest changed repos after sync", "bool", "Reload repos whose download is newer than the graph, with 0 new embeddings (cached vectors are reused; the rest stays pending)."),
    ("SYNC_SOURCES", "Data sources", "Also sync connected Git sources", "bool", "Re-pull your own playbook repositories (Review → Your playbooks) and re-score them."),
    ("GIT_TOKEN", "Data sources", "Git access token", "secret", "Optional, for private repositories you connect on the Review page. Write-only. Use a read-only token."),
    ("GIT_TOKEN_USER", "Data sources", "Git token username", "text", "x-access-token for GitHub, oauth2 for GitLab, your username for Bitbucket app passwords."),
]

DEFAULTS = {"LOG_LEVEL": "INFO", "FORTISOAR_VERSION": "7.6", "COMPANY_NAME": "our company", "APP_MODE": "local", "EMBED_DIM": "768",
            "NEO4J_USER": "neo4j", "NEO4J_DATABASE": "neo4j", "OLLAMA_URL": "http://localhost:11434",
            "SESSION_HOURS": "12", "RETRIEVAL_PLAYBOOKS_K": "5", "RETRIEVAL_VENDOR_K": "5", "RETRIEVAL_INTERNAL_K": "4",
            "CHAT_HISTORY_TURNS": "6", "INCLUDE_PROPOSED_LINKS": "false", "OVERVIEW_PLAYBOOK_LIMIT": "300",
            "FETCH_ORG": "fortinet-fortisoar", "FETCH_PREFIXES": "connector-,solution-,widget-,ai-",
            "LLM_DAILY_LIMIT": "20", "EMBED_DAILY_LIMIT": "1000", "ENFORCE_DAILY_LIMITS": "true", "SYNC_SCHEDULE": "off",
            "SYNC_AUTO_INGEST": "false", "SYNC_SOURCES": "true", "GIT_TOKEN_USER": "x-access-token"}
KEYS = {f[0] for f in FIELDS}
SECRETS = {f[0] for f in FIELDS if f[3] == "secret"}
VALUE_RE = re.compile(r"^[^\n\r]*$")


def describe(settings) -> list[dict]:
    """Field catalogue with current values (secrets reported as set / not set only)."""
    effective = {
        "LLM_PROVIDER": settings.llm_provider, "LLM_MODEL": settings.llm_model, "EMBED_PROVIDER": settings.embed_provider,
        "EMBED_MODEL": settings.embed_model, "EMBED_TPM": str(settings.embed_tpm), "EMBED_CACHE": settings.embed_cache,
        "NEO4J_URI": settings.neo4j_uri, "LLM_CACHE": settings.llm_cache,
    }
    out = []
    for key, group, label, ftype, help_text, *extra in FIELDS:
        raw = os.environ.get(key, "")
        item = {"key": key, "group": group, "label": label, "type": ftype, "help": help_text, **(extra[0] if extra else {})}
        if ftype == "secret":
            item["is_set"] = bool(raw)
        else:
            item["value"] = raw
            item["effective"] = effective.get(key, raw or DEFAULTS.get(key, ""))
        out.append(item)
    return out


def _write_env(updates: dict[str, str | None], path: Path | None = None) -> None:
    """Set KEY=value in .env: replace the active line, else uncomment the first `# KEY=` example, else append.
    An empty value comments the key out (so the default applies)."""
    path = path or ENV_FILE
    lines = path.read_text().splitlines() if path.exists() else []
    for key, value in updates.items():
        new_line = f"{key}={value}" if value not in (None, "") else f"# {key}="
        active = next((i for i, l in enumerate(lines) if re.match(rf"^\s*{key}\s*=", l)), None)
        commented = next((i for i, l in enumerate(lines) if re.match(rf"^\s*#\s*{key}\s*=", l)), None)
        if active is not None:
            lines[active] = new_line
        elif commented is not None and value not in (None, ""):
            lines[commented] = new_line
        elif value not in (None, ""):
            lines.append(new_line)
    path.write_text("\n".join(lines) + "\n")
    os.chmod(path, 0o600)  # it holds secrets


def apply(updates: dict, env_file: Path | None = None) -> dict:
    """Validate the merged configuration, persist to .env, update the process environment and reset caches."""
    from .config import get_settings

    clean: dict[str, str | None] = {}
    for key, value in updates.items():
        if key not in KEYS:
            raise ValueError(f"unknown setting {key}")
        value = "" if value is None else str(value).strip()
        if not VALUE_RE.match(value):
            raise ValueError(f"{key}: value must be a single line")
        if key in SECRETS and value == "":
            continue  # blank secret = keep the current one
        clean[key] = value
    if not clean:
        return {"changed": []}
    previous = {k: os.environ.get(k) for k in clean}
    for key, value in clean.items():
        if value:
            os.environ[key] = value
        else:
            os.environ.pop(key, None)
    get_settings.cache_clear()
    try:
        get_settings()  # raises ValueError with every problem listed
    except Exception:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        get_settings.cache_clear()
        raise
    _write_env(clean, env_file)
    reset_services()
    return {"changed": sorted(clean), "needs_reingest": sorted(k for k in clean if k in ("EMBED_PROVIDER", "EMBED_MODEL", "EMBED_DIM"))}


def reset_services() -> None:
    """Drop cached services (graph driver, models, retriever) so the next request uses the new settings."""
    from .services import get_services

    try:
        old = get_services()
        if "graph" in old.__dict__:
            old.graph.close()
    except Exception:
        pass
    get_services.cache_clear()
