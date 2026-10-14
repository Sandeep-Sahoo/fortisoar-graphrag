"""Workspaces: fully separate knowledge bases (own Neo4j database + own app data folder) in one app.

* fortinet (default): Fortinet's GitHub content; its data lives where it always has (data/...)
* marlink: Marlink's own playbooks / docs / designs; a second Neo4j (bolt://localhost:7688) and data/workspaces/marlink/

Shared by every workspace: users and roles, the audit log, the usage meter and daily limits, the embedding and
answer caches (keyed by text, so nothing leaks between workspaces), logs and the downloaded GitHub files.

The current workspace comes from the `graphrag_ws` cookie (set by the header selector, so it's per user / browser),
from APP_WORKSPACE for CLI jobs, and is held in a context variable for the request.
"""
from __future__ import annotations

import json
import os
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_ROOT = ROOT / "data"                    # the default workspace's data folder (patched in tests)
CONFIG = DATA_ROOT / "workspaces.json"
COOKIE = "graphrag_ws"
DEFAULT = "fortinet"
FEATURES = ("github",)                       # Fortinet-only features: GitHub page, ingest of GitHub repos, Fortinet scores

BUILTIN = {
    "fortinet": {"label": "Fortinet", "description": "Fortinet's public FortiSOAR content from GitHub",
                 "neo4j_uri": None, "data_dir": ".", "features": ["github"], "color": "#534AB7", "service": "neo4j"},
    "marlink": {"label": "Marlink", "description": "Marlink's own playbooks, docs and designs",
                "neo4j_uri": "bolt://localhost:7688", "neo4j_password_env": "NEO4J_PASSWORD_MARLINK",
                "data_dir": "workspaces/marlink", "features": [], "color": "#0F6E56", "service": "neo4j-marlink"},
}

_current: ContextVar[str] = ContextVar("workspace", default=os.environ.get("APP_WORKSPACE") or DEFAULT)


def registry() -> dict[str, dict]:
    """Built-in workspaces, overridable / extendable via data/workspaces.json."""
    out = {k: dict(v) for k, v in BUILTIN.items()}
    try:
        extra = json.loads(CONFIG.read_text())
    except (OSError, ValueError):
        extra = {}
    for key, value in (extra or {}).items():
        if isinstance(value, dict) and key.isidentifier():
            out[key] = {**out.get(key, {"features": [], "data_dir": f"workspaces/{key}"}), **value}
    return out


def exists(ws: str | None) -> bool:
    return bool(ws) and ws in registry()


def current() -> str:
    ws = _current.get()
    return ws if exists(ws) else DEFAULT


def set_current(ws: str | None):
    """Returns a token for reset(); unknown names fall back to the default workspace."""
    return _current.set(ws if exists(ws) else DEFAULT)


def reset(token) -> None:
    _current.reset(token)


@contextmanager
def using(ws: str):
    token = set_current(ws)
    try:
        yield
    finally:
        reset(token)


def info(ws: str | None = None) -> dict:
    ws = ws or current()
    return {"id": ws, **registry()[ws]}


def has(feature: str, ws: str | None = None) -> bool:
    return feature in (info(ws).get("features") or [])


def data_dir(ws: str | None = None) -> Path:
    d = info(ws).get("data_dir") or "."
    return DATA_ROOT if d in (".", "") else DATA_ROOT / d


def path(p: Path | str, ws: str | None = None) -> Path:
    """Map a default-workspace data path (under DATA_ROOT) to the same place in the current workspace."""
    p = Path(p)
    target = data_dir(ws)
    if target == DATA_ROOT:
        return p
    try:
        rel = p.relative_to(DATA_ROOT)
    except ValueError:
        return p
    return target / rel


def settings_for(base, ws: str | None = None):
    """The app settings as one company sees them: its own Neo4j database, company name, redaction terms, Git token
    and sync options (Settings → <company> tab). General settings (models, keys, limits…) are shared."""
    import dataclasses

    ws = ws or current()
    if ws == DEFAULT:
        return base
    w = info(ws)
    v = w.get("settings") or {}
    env = lambda key: os.environ.get(f"{key}_{ws.upper()}")
    password = env("NEO4J_PASSWORD") or os.environ.get(w.get("neo4j_password_env") or "") or w.get("neo4j_password") or base.neo4j_password
    terms = tuple(t.strip() for t in (v.get("SCRUB_TERMS") or "").split(",") if t.strip())
    return dataclasses.replace(
        # NEO4J_URI_<COMPANY> in the environment wins: the app container reaches each database by its service name
        base, neo4j_uri=env("NEO4J_URI") or v.get("NEO4J_URI") or w.get("neo4j_uri") or base.neo4j_uri, neo4j_user=v.get("NEO4J_USER") or w.get("neo4j_user") or base.neo4j_user,
        neo4j_password=password, neo4j_database=v.get("NEO4J_DATABASE") or w.get("neo4j_database") or "neo4j",
        company_name=v.get("COMPANY_NAME") or w.get("label") or base.company_name,
        fortisoar_version=v.get("FORTISOAR_VERSION") or base.fortisoar_version, scrub_terms=terms,
        git_token=env("GIT_TOKEN"), git_token_user=v.get("GIT_TOKEN_USER") or "x-access-token",
        sync_sources=(v.get("SYNC_SOURCES") or str(base.sync_sources)).lower() == "true")
