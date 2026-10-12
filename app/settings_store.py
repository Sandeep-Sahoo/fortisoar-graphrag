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

    from .services import _instances

    for old in list(_instances.values()):
        try:
            if "graph" in old.__dict__:
                old.graph.close()
        except Exception:
            pass
    get_services.cache_clear()


# ---------------------------------------------------------------- scopes: General (all companies) / per company

WORKSPACE_KEYS = ("COMPANY_NAME", "FORTISOAR_VERSION", "SCRUB_TERMS", "NEO4J_URI", "NEO4J_USER", "NEO4J_PASSWORD", "NEO4J_DATABASE",
                  "GIT_TOKEN", "GIT_TOKEN_USER", "SYNC_SOURCES")
FORTINET_KEYS = ("FETCH_ORG", "FETCH_PREFIXES", "SYNC_AUTO_INGEST")   # GitHub content: only where the "github" feature is on


def scope_of(key: str) -> str:
    return "workspace" if key in WORKSPACE_KEYS else "github" if key in FORTINET_KEYS else "general"


def keys_for(scope: str) -> list[str]:
    """Setting keys shown on a Settings tab: 'general' or a workspace id."""
    from . import workspace

    if scope == "general":
        return [f[0] for f in FIELDS if scope_of(f[0]) == "general"]
    keys = [k for k in WORKSPACE_KEYS]
    if workspace.has("github", scope):
        keys += list(FORTINET_KEYS)
    return keys


def secret_env(key: str, ws: str) -> str:
    """Where a company's secret lives in .env: the plain key for the default company, KEY_<COMPANY> otherwise."""
    from . import workspace

    return key if ws == workspace.DEFAULT else f"{key}_{ws.upper()}"


def workspace_values(ws: str) -> dict:
    from . import workspace

    return dict(workspace.registry().get(ws, {}).get("settings") or {})


def describe_scope(settings, scope: str) -> list[dict]:
    """Fields for one Settings tab with their current values (secrets: set / not set only)."""
    from . import workspace

    keys = keys_for(scope)
    if scope == "general" or scope == workspace.DEFAULT:
        fields = [f for f in describe(settings) if f["key"] in keys]
        if scope == workspace.DEFAULT:
            for f in fields:
                f["scope"] = scope_of(f["key"])
        return fields
    stored = workspace_values(scope)
    w = workspace.info(scope)
    effective = {"NEO4J_URI": w.get("neo4j_uri") or "", "NEO4J_USER": "neo4j", "NEO4J_DATABASE": "neo4j", "COMPANY_NAME": w.get("label", scope),
                 "GIT_TOKEN_USER": "x-access-token", "SYNC_SOURCES": "true", "FORTISOAR_VERSION": "7.6"}
    out = []
    for key, group, label, ftype, help_text, *extra in FIELDS:
        if key not in keys:
            continue
        item = {"key": key, "group": group, "label": label, "type": ftype, "help": help_text, "scope": scope_of(key), **(extra[0] if extra else {})}
        if ftype == "secret":
            item["is_set"] = bool(os.environ.get(secret_env(key, scope)) or (key == "NEO4J_PASSWORD" and os.environ.get("NEO4J_PASSWORD")))
            if key == "NEO4J_PASSWORD" and not os.environ.get(secret_env(key, scope)):
                item["help"] = (help_text + " Not set for this company: the Fortinet Neo4j password is used.").strip()
        else:
            item["value"] = stored.get(key, "")
            item["effective"] = stored.get(key) or effective.get(key, "")
        out.append(item)
    return out


def apply_scope(scope: str, updates: dict, env_file: Path | None = None) -> dict:
    """Save one Settings tab. General and the default company use .env as before; other companies keep their
    values in data/workspaces.json and their secrets in .env as KEY_<COMPANY>."""
    import json

    from . import workspace

    allowed = set(keys_for(scope))
    bad = [k for k in updates if k not in allowed]
    if bad:
        raise ValueError(f"{', '.join(bad)} can't be set on the {scope} tab")
    if scope == "general" or scope == workspace.DEFAULT:
        return apply(updates, env_file)
    clean, secrets = {}, {}
    types = {f[0]: f[3] for f in FIELDS}
    for key, value in updates.items():
        value = "" if value is None else str(value).strip()
        if not VALUE_RE.match(value):
            raise ValueError(f"{key}: value must be a single line")
        if types[key] == "secret":
            if value:
                secrets[secret_env(key, scope)] = value
            continue
        if key == "NEO4J_URI" and value and not re.match(r"^(bolt|neo4j)(\+s|\+ssc)?://[\w.-]+(:\d+)?/?$", value):
            raise ValueError("NEO4J_URI must look like bolt://host:7687 or neo4j+s://host")
        clean[key] = value
    cfg_path = workspace.CONFIG
    try:
        cfg = json.loads(cfg_path.read_text())
    except (OSError, ValueError):
        cfg = {}
    entry = cfg.setdefault(scope, {})
    stored = entry.setdefault("settings", {})
    changed = []
    for key, value in clean.items():
        if stored.get(key, "") != value:
            changed.append(key)
            if value:
                stored[key] = value
            else:
                stored.pop(key, None)
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(json.dumps(cfg, indent=1))
    if secrets:
        for k, v in secrets.items():
            os.environ[k] = v
        _write_env(secrets, env_file)
        changed += sorted(k.rsplit("_", 1)[0] for k in secrets)
    reset_services()
    return {"changed": sorted(set(changed)), "needs_reingest": []}
