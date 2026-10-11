"""Live facts for the in-app Guide: what's in the graph, where it came from, how it's configured."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data" / "fortinet"
HIDDEN = "n:Meta OR n:ChatSession OR n:ChatMessage"


def about(svc) -> dict:
    g = svc.graph
    nodes = g.read(f"MATCH (n) WHERE NOT ({HIDDEN}) RETURN labels(n)[0] AS label, count(*) AS count ORDER BY count DESC")
    rels = g.read(
        "MATCH (a)-[r]->(b) WHERE NOT (a:ChatSession OR a:ChatMessage OR a:Meta) "
        "RETURN type(r) AS type, labels(a)[0] AS source, labels(b)[0] AS target, count(*) AS count ORDER BY count DESC"
    )
    one = lambda cypher: (g.read(cypher) or [{}])[0]
    playbooks = one(
        "MATCH (p:Playbook) RETURN sum(CASE WHEN coalesce(p.placeholder, false) THEN 0 ELSE 1 END) AS loaded, "
        "sum(CASE WHEN coalesce(p.placeholder, false) THEN 1 ELSE 0 END) AS placeholders, "
        "sum(CASE WHEN p.embedding IS NULL OR coalesce(p.placeholder, false) THEN 0 ELSE 1 END) AS embedded"
    )
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "config": svc.settings.describe(),
        "nodes": nodes,
        "relationships": rels,
        "playbooks": playbooks,
        "by_source": g.read("MATCH (p:Playbook) WHERE NOT coalesce(p.placeholder, false) "
                            "RETURN coalesce(p.pack_type, 'own export') AS source, count(*) AS count ORDER BY count DESC"),
        "step_kinds": g.read("MATCH (s:Step) RETURN s.kind AS kind, count(*) AS count ORDER BY count DESC"),
        "triggers": g.read("MATCH (p:Playbook) WHERE NOT coalesce(p.placeholder, false) "
                           "RETURN p.trigger_type AS trigger, count(*) AS count ORDER BY count DESC"),
        "secrets": one("MATCH (s:Step {has_secret: true}) RETURN count(s) AS steps, count(DISTINCT s.playbook_uuid) AS playbooks"),
        "chunks": one("MATCH (c:Chunk) RETURN count(c) AS chunks, sum(CASE WHEN c.embedding IS NULL THEN 0 ELSE 1 END) AS embedded"),
        "top_packs": g.read("MATCH (sp:SolutionPack)-[:INCLUDES]->()-[:CONTAINS]->(p:Playbook) "
                            "RETURN sp.label AS label, count(p) AS playbooks ORDER BY playbooks DESC LIMIT 8"),
        "top_connectors": g.read("MATCH (c:Connector)<-[:OF]-(:ConnectorOperation)<-[:USES]-(s:Step) "
                                 "WHERE NOT c.name IN ['cyops_utilities', 'code-snippet'] "
                                 "RETURN coalesce(c.label, c.name) AS label, count(s) AS steps ORDER BY steps DESC LIMIT 8"),
        "source": _source_info(),
    }


def _source_info() -> dict:
    listing = DATA / "repos.jsonl"
    github = DATA / "github"
    if not listing.exists():
        return {"present": False}
    repos = [json.loads(line) for line in listing.read_text().splitlines() if line.strip()]
    on_disk = [d for d in github.iterdir() if d.is_dir() and not d.name.endswith(".tmp")] if github.exists() else []
    markers = [(d / ".pushed_at").read_text() for d in on_disk if (d / ".pushed_at").exists()]
    prefixes = {}
    for r in repos:
        key = r["name"].split("-")[0]
        prefixes[key] = prefixes.get(key, 0) + 1
    newest = max((d.stat().st_mtime for d in on_disk), default=listing.stat().st_mtime)
    return {
        "present": True, "org": "fortinet-fortisoar", "repos_listed": len(repos), "repos_by_prefix": prefixes,
        "repos_downloaded": len(on_disk), "from_release_branch": sum("|release/" in m for m in markers),
        "files_kept": sum(1 for d in on_disk for f in d.rglob("*") if f.is_file() and f.name != ".pushed_at"),
        "last_fetch": datetime.fromtimestamp(newest, timezone.utc).isoformat(timespec="minutes"),
    }
