"""Per-repo ingestion history (data/fortinet/ingest_state.json): when, how, and with what cap."""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path

STATE = Path(__file__).resolve().parent.parent.parent / "data" / "fortinet" / "ingest_state.json"
_lock = threading.Lock()


def load() -> dict:
    try:
        return json.loads(STATE.read_text())
    except (OSError, ValueError):
        return {}


def record(repos: list[str], mode: str, max_embeddings: int | None, result: dict | None = None,
           per_repo: dict | None = None) -> None:
    """mode: ingest | reingest | continue | sync. Keeps the last 10 runs per repo; per_repo adds repo-specific
    details (e.g. which playbooks were added / removed / changed)."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with _lock:
        state = load()
        for repo in repos:
            entry = state.setdefault(repo, {"runs": []})
            entry.update(last_ingested=now, last_mode=mode, last_cap=max_embeddings)
            extra = (per_repo or {}).get(repo) or {}
            entry["runs"] = ([{"at": now, "mode": mode, "cap": max_embeddings, **(result or {}), **extra}] + entry["runs"])[:10]
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps(state, indent=1))


def playbook_signatures_from_graph(graph, repos: list[str] | None) -> dict:
    """repo -> {uuid: (name, signature)} for what's in the graph now."""
    import hashlib

    rows = graph.read(
        "MATCH (p:Playbook) WHERE p.repo IS NOT NULL AND ($repos IS NULL OR p.repo IN $repos) "
        "OPTIONAL MATCH (p)-[:HAS_STEP]->(s:Step) OPTIONAL MATCH (s)-[:USES]->(op:ConnectorOperation) "
        "RETURN p.repo AS repo, p.uuid AS uuid, p.name AS name, collect(s.name + '|' + s.kind + '|' + coalesce(op.key, '')) AS steps",
        repos=repos)
    out: dict = {}
    for r in rows:
        sig = hashlib.sha1("\n".join(sorted(r["steps"])).encode()).hexdigest()
        out.setdefault(r["repo"], {})[r["uuid"]] = (r["name"], sig)
    return out


def playbook_signatures_from_parse(playbooks) -> dict:
    import hashlib

    out: dict = {}
    seen = set()
    for pb in playbooks:
        if pb.uuid in seen or not pb.pack_repo:
            continue
        seen.add(pb.uuid)
        steps = [f"{s.name}|{s.kind}|{(s.connector + '.' + s.operation) if s.connector else ''}" for s in pb.steps]
        out.setdefault(pb.pack_repo, {})[pb.uuid] = (pb.name, hashlib.sha1("\n".join(sorted(steps)).encode()).hexdigest())
    return out


def diff(before: dict, after: dict) -> dict:
    """Per repo: added / removed / changed playbook names (capped) and counts."""
    out = {}
    for repo in set(before) | set(after):
        b, a = before.get(repo, {}), after.get(repo, {})
        added = sorted(a[u][0] for u in set(a) - set(b))
        removed = sorted(b[u][0] for u in set(b) - set(a))
        changed = sorted(a[u][0] for u in set(a) & set(b) if a[u][1] != b[u][1])
        if b or added:  # first-ever load: everything is "added", report counts only
            out[repo] = {"diff": {"added": added[:50], "removed": removed[:50], "changed": changed[:50],
                                  "counts": {"added": len(added), "removed": len(removed), "changed": len(changed)},
                                  "first_load": not b}}
    return out
