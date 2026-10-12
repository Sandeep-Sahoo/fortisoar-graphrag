"""GitHub page: every downloaded repo with what it contains and how much of it is in the graph / embedded."""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path

from .ingest.docs import chunk_text
from .ingest import state as ingest_state
from .ingest.fortinet import load_repo
from .scrub import Scrubber

ROOT = Path(__file__).resolve().parent.parent
GITHUB_DIR = ROOT / "data" / "fortinet" / "github"
_lock = threading.Lock()
_cache: dict = {"signature": None, "repos": []}


def _signature() -> tuple:
    if not GITHUB_DIR.exists():
        return ()
    return tuple(sorted((d.name, (d / ".pushed_at").stat().st_mtime if (d / ".pushed_at").exists() else 0)
                        for d in GITHUB_DIR.iterdir() if d.is_dir() and not d.name.endswith(".tmp")))


def disk_index(scrubber: Scrubber) -> list[dict]:
    """Parse every repo once (re-parsed only when a repo is re-fetched)."""
    sig = _signature()
    with _lock:
        if _cache["signature"] == sig:
            return _cache["repos"]
        listing = {}
        listing_file = GITHUB_DIR.parent / "repos.jsonl"
        if listing_file.exists():
            for line in listing_file.read_text().splitlines():
                if line.strip():
                    r = json.loads(line)
                    listing[r["name"]] = r
        repos = []
        for d in sorted(x for x in GITHUB_DIR.iterdir() if x.is_dir() and not x.name.endswith(".tmp")):
            pack, playbooks, docs = load_repo(d, scrubber)
            marker = (d / ".pushed_at").read_text() if (d / ".pushed_at").exists() else ""
            fetched = (datetime.fromtimestamp((d / ".pushed_at").stat().st_mtime, timezone.utc).isoformat(timespec="seconds")
                       if (d / ".pushed_at").exists() else "")
            branch = marker.split("|", 1)[1] if "|" in marker else listing.get(d.name, {}).get("branch", "")
            repos.append({
                "repo": d.name, "kind": pack.kind, "label": pack.label, "version": pack.version,
                "category": pack.category, "publisher": pack.publisher, "description": pack.description[:300],
                "branch": branch, "pushed": listing.get(d.name, {}).get("pushed", ""), "fetched": fetched,
                "archived": listing.get(d.name, {}).get("archived", False),
                "connectors": sorted({c for p in playbooks for s in p.steps for c in [s.connector] if c}),
                "playbook_names": sorted({p.name for p in playbooks})[:200],
                "collection_names": sorted(pack.collections)[:50] if isinstance(pack.collections, (list, set, dict)) else [],
                "doc_titles": [doc["title"] for doc in docs][:50],
                "url": f"https://github.com/fortinet-fortisoar/{d.name}",
                "playbook_uuids": sorted({p.uuid for p in playbooks}),
                "collections": len(pack.collections),
                "doc_ids": [doc["id"] for doc in docs],
                "chunks": sum(len(chunk_text(doc["text"])) for doc in docs),
            })
        _cache.update(signature=sig, repos=repos)
        return repos


def repo_table(svc) -> dict:
    repos = disk_index(svc.scrubber)
    g = svc.graph
    loaded = {r["uuid"]: r["embedded"] for r in g.read(
        "MATCH (p:Playbook) WHERE NOT coalesce(p.placeholder, false) RETURN p.uuid AS uuid, p.embedding IS NOT NULL AS embedded")}
    graph_times = {r["repo"]: r["at"] for r in g.read(
        "MATCH (p:Playbook) WHERE p.repo IS NOT NULL RETURN p.repo AS repo, max(p.synced_at) AS at")}
    for r in g.read("MATCH (d:Document) WHERE d.repo IS NOT NULL AND d.ingested_at IS NOT NULL "
                    "RETURN d.repo AS repo, max(d.ingested_at) AS at"):
        graph_times[r["repo"]] = max(graph_times.get(r["repo"]) or "", r["at"])
    history = ingest_state.load()
    doc_stats = {r["id"]: r for r in g.read(
        "MATCH (d:Document) OPTIONAL MATCH (d)-[:HAS_CHUNK]->(c:Chunk) "
        "RETURN d.id AS id, count(c) AS chunks, sum(CASE WHEN c.embedding IS NULL THEN 0 ELSE 1 END) AS embedded")}
    rows, totals = [], {"repos": 0, "playbooks": 0, "playbooks_loaded": 0, "playbooks_embedded": 0,
                        "docs": 0, "docs_loaded": 0, "chunks": 0, "chunks_embedded": 0}
    for r in repos:
        pbs = r["playbook_uuids"]
        pb_loaded = sum(1 for u in pbs if u in loaded)
        pb_embedded = sum(1 for u in pbs if loaded.get(u))
        docs_loaded = sum(1 for d in r["doc_ids"] if d in doc_stats)
        chunks_embedded = sum(doc_stats[d]["embedded"] for d in r["doc_ids"] if d in doc_stats)
        pending = (len(pbs) - pb_embedded) + (r["chunks"] - chunks_embedded)
        has_content = len(pbs) + len(r["doc_ids"]) > 0
        if not has_content:
            status = "empty"
        elif pending == 0 and pb_loaded == len(pbs) and docs_loaded == len(r["doc_ids"]):
            status = "complete"
        elif pb_loaded or docs_loaded:
            status = "partial"
        else:
            status = "not ingested"
        hist = history.get(r["repo"], {})
        last = hist.get("last_ingested") or (graph_times.get(r["repo"]) if (pb_loaded or docs_loaded) else None)
        # downloaded content newer than the last ingest -> the graph may be out of date
        stale = bool(last and r["fetched"] and _ts(r["fetched"]) > _ts(last))
        rows.append({k: v for k, v in r.items() if k not in ("playbook_uuids", "doc_ids", "playbook_names", "collection_names", "doc_titles", "connectors")} | {
            "playbooks": len(pbs), "playbooks_loaded": pb_loaded, "playbooks_embedded": pb_embedded,
            "docs": len(r["doc_ids"]), "docs_loaded": docs_loaded, "chunks_embedded": chunks_embedded,
            "pending_embeddings": pending, "playbooks_pending": len(pbs) - pb_embedded,
            "chunks_pending": r["chunks"] - chunks_embedded, "connector_count": len(r["connectors"]),
            "status": status, "last_ingested": last, "last_mode": hist.get("last_mode"),
            "last_cap": hist.get("last_cap"), "runs": len(hist.get("runs", [])), "stale": stale})
        totals["repos"] += 1
        for key, value in (("playbooks", len(pbs)), ("playbooks_loaded", pb_loaded), ("playbooks_embedded", pb_embedded),
                           ("docs", len(r["doc_ids"])), ("docs_loaded", docs_loaded), ("chunks", r["chunks"]),
                           ("chunks_embedded", chunks_embedded)):
            totals[key] += value
    totals["pending_embeddings"] = (totals["playbooks"] - totals["playbooks_embedded"]) + (totals["chunks"] - totals["chunks_embedded"])
    return {"repos": rows, "totals": totals}


def _ts(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def repo_detail(svc, repo: str) -> dict | None:
    """One repo: the table row plus its contents (names) and ingest history."""
    disk = next((r for r in disk_index(svc.scrubber) if r["repo"] == repo), None)
    if disk is None:
        return None
    row = next(r for r in repo_table(svc)["repos"] if r["repo"] == repo)
    in_graph = {r["name"]: r["embedded"] for r in svc.graph.read(
        "MATCH (p:Playbook) WHERE p.uuid IN $uuids RETURN p.name AS name, p.embedding IS NOT NULL AS embedded",
        uuids=disk["playbook_uuids"])}
    return row | {
        "playbook_list": [{"name": n, "loaded": n in in_graph, "embedded": bool(in_graph.get(n))} for n in disk["playbook_names"]],
        "collection_names": disk["collection_names"], "doc_titles": disk["doc_titles"], "connectors": disk["connectors"],
        "history": ingest_state.load().get(repo, {}).get("runs", []),
    }
