"""Insights computed from the graph and review results (no model calls)."""
from __future__ import annotations

import hashlib
import re
import threading

_lock = threading.Lock()
_cache: dict = {}


def _graph_version(g) -> tuple:
    row = g.read("MATCH (p:Playbook) RETURN count(p) AS n, max(p.synced_at) AS at")[0]
    return row["n"], row["at"]


# ---------------------------------------------------------------- connector impact

def connector_impact(g, name: str) -> dict:
    """Everything that depends on one connector: playbooks, packs, modules, and indirect callers."""
    rows = g.read(
        """
        MATCH (c:Connector {name: $name})<-[:OF]-(op:ConnectorOperation)<-[:USES]-(s:Step)<-[:HAS_STEP]-(p:Playbook)
        WHERE NOT coalesce(p.placeholder, false)
        WITH p, collect(DISTINCT op.name) AS ops, count(DISTINCT s) AS steps,
             any(x IN collect(s.has_secret) WHERE x) AS secret
        OPTIONAL MATCH (p)-[:TRIGGERED_ON]->(m:Module)
        RETURN p.uuid AS uuid, p.name AS name, p.pack AS pack, p.pack_type AS pack_type, p.trigger_type AS trigger,
               p.active AS active, ops, steps, secret, collect(DISTINCT m.name) AS modules
        ORDER BY pack, name
        """, name=name)
    if not rows and not g.read("MATCH (c:Connector {name: $name}) RETURN c.name AS n", name=name):
        return {"error": f"no connector named '{name}'"}
    direct = {r["uuid"] for r in rows}
    callers = g.read(
        """
        UNWIND $uuids AS u
        MATCH path = (caller:Playbook)-[:CALLS*1..4]->(:Playbook {uuid: u})
        WHERE NOT caller.uuid IN $uuids AND NOT coalesce(caller.placeholder, false)
        WITH caller, min(length(path)) AS hops, collect(DISTINCT [n IN nodes(path) | n.name][-1]) AS via
        RETURN caller.uuid AS uuid, caller.name AS name, caller.pack AS pack, hops, via[0..3] AS via ORDER BY hops, name
        """, uuids=list(direct))
    acts = g.read(
        """
        UNWIND $uuids AS u MATCH (:Playbook {uuid: u})-[a:ACTS_ON]->(m:Module)
        RETURN m.name AS module, count(*) AS playbooks, collect(DISTINCT a.ops)[0..1][0] AS ops ORDER BY playbooks DESC
        """, uuids=list(direct))
    ops: dict[str, int] = {}
    for r in rows:
        for op in r["ops"]:
            ops[op] = ops.get(op, 0) + 1
    packs = sorted({r["pack"] for r in rows if r["pack"]})
    modules = sorted({m for r in rows for m in r["modules"]})
    return {"connector": name, "playbooks": rows, "indirect": callers, "packs": packs, "trigger_modules": modules,
            "acts_on": acts, "operations": sorted(ops.items(), key=lambda x: -x[1]),
            "summary": {"direct": len(rows), "indirect": len(callers), "packs": len(packs),
                        "uploads": sum(1 for r in rows if r["pack_type"] == "upload"),
                        "active": sum(1 for r in rows if r["active"] is not False),
                        "with_secrets": sum(1 for r in rows if r["secret"])}}


def connector_usage(g) -> list[dict]:
    return g.read(
        """
        MATCH (c:Connector)
        OPTIONAL MATCH (c)<-[:OF]-(:ConnectorOperation)<-[:USES]-(:Step)<-[:HAS_STEP]-(p:Playbook)
        WITH c, count(DISTINCT p) AS playbooks
        RETURN c.name AS name, c.label AS label, playbooks ORDER BY playbooks DESC, name
        """)


# ---------------------------------------------------------------- duplicates / similarity

def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).strip()


def duplicates(g, threshold: float = 0.85) -> dict:
    """Exact structural duplicates (same steps / kinds / operations) and near-duplicates (Jaccard on steps)."""
    key = ("dups", _graph_version(g), threshold)
    with _lock:
        if key in _cache:
            return _cache[key]
    rows = g.read(
        """
        MATCH (p:Playbook)-[:HAS_STEP]->(s:Step) WHERE NOT coalesce(p.placeholder, false) AND s.kind <> 'trigger'
        OPTIONAL MATCH (s)-[:USES]->(op:ConnectorOperation)
        WITH p, collect(s.kind + ':' + coalesce(op.key, '') + ':' + toLower(s.name)) AS tokens
        RETURN p.uuid AS uuid, p.name AS name, p.pack AS pack, p.pack_type AS pack_type, tokens
        """)
    exact: dict[str, list] = {}
    sets = {}
    for r in rows:
        tokens = sorted(r["tokens"])
        if len(tokens) < 3:
            continue
        sig = hashlib.sha1("|".join(tokens).encode()).hexdigest()
        exact.setdefault(sig, []).append(r)
        sets[r["uuid"]] = (r, set(tokens))
    exact_groups = [{"size": len(v), "steps": len(sets[v[0]["uuid"]][1]),
                     "playbooks": [{"uuid": x["uuid"], "name": x["name"], "pack": x["pack"], "pack_type": x["pack_type"]} for x in v]}
                    for v in exact.values() if len(v) > 1]
    exact_groups.sort(key=lambda g_: (-g_["size"], -g_["steps"]))
    in_exact = {p["uuid"] for grp in exact_groups for p in grp["playbooks"]}
    # near duplicates: inverted index on uncommon tokens keeps candidate pairs small
    df: dict[str, int] = {}
    for _, toks in sets.values():
        for t in toks:
            df[t] = df.get(t, 0) + 1
    inv: dict[str, list[str]] = {}
    for u, (_, toks) in sets.items():
        for t in toks:
            if df[t] <= 40:
                inv.setdefault(t, []).append(u)
    pairs = set()
    for us in inv.values():
        for i in range(len(us)):
            for j in range(i + 1, len(us)):
                pairs.add((us[i], us[j]) if us[i] < us[j] else (us[j], us[i]))
    near = []
    for a, b in pairs:
        (ra, ta), (rb, tb) = sets[a], sets[b]
        if a in in_exact and b in in_exact and ta == tb:
            continue
        inter = len(ta & tb)
        if not inter:
            continue
        sim = inter / len(ta | tb)
        if sim >= threshold:
            near.append({"similarity": round(sim, 3), "a": {"uuid": a, "name": ra["name"], "pack": ra["pack"], "steps": len(ta)},
                         "b": {"uuid": b, "name": rb["name"], "pack": rb["pack"], "steps": len(tb)},
                         "only_a": len(ta - tb), "only_b": len(tb - ta)})
    near.sort(key=lambda x: (-x["similarity"], -(x["a"]["steps"] + x["b"]["steps"])))
    result = {"exact": exact_groups[:300], "near": near[:500], "threshold": threshold, "playbooks_compared": len(sets),
              "summary": {"exact_groups": len(exact_groups), "exact_playbooks": len(in_exact), "near_pairs": len(near)}}
    with _lock:
        _cache[key] = result
    return result


# ---------------------------------------------------------------- coverage gaps

def gaps(g) -> dict:
    modules = g.read(
        """
        MATCH (m:Module)
        RETURN m.name AS module, COUNT { (:Playbook)-[:TRIGGERED_ON]->(m) } AS triggered_by,
               COUNT { (:Playbook)-[:ACTS_ON]->(m) } AS acted_on_by
        ORDER BY triggered_by, acted_on_by DESC, module
        """)
    unused_connectors = g.read(
        """
        MATCH (c:Connector) WHERE NOT (c)<-[:OF]-(:ConnectorOperation)<-[:USES]-(:Step)
        RETURN c.name AS name, c.label AS label, c.category AS category ORDER BY name
        """)
    orphans = g.read(
        """
        MATCH (p:Playbook) WHERE p.trigger_type = 'referenced' AND NOT coalesce(p.placeholder, false)
          AND NOT (:Playbook)-[:CALLS]->(p)
        RETURN p.uuid AS uuid, p.name AS name, p.pack AS pack ORDER BY pack, name
        """)
    unresolved = g.read(
        """
        MATCH (caller:Playbook)-[:CALLS]->(p:Playbook {placeholder: true})
        RETURN caller.uuid AS uuid, caller.name AS caller, caller.pack AS pack, p.uuid AS missing ORDER BY pack, caller
        """)
    undocumented = g.read(
        """
        MATCH (p:Playbook) WHERE NOT coalesce(p.placeholder, false)
          AND (p.description IS NULL OR trim(p.description) = '')
          AND NOT (p)-[:DOCUMENTED_BY {status: 'approved'}]->()
        RETURN p.uuid AS uuid, p.name AS name, p.pack AS pack ORDER BY pack, name LIMIT 500
        """)
    inactive = g.read("MATCH (p:Playbook {active: false}) RETURN p.uuid AS uuid, p.name AS name, p.pack AS pack ORDER BY pack, name")
    no_trigger_modules = [m for m in modules if m["triggered_by"] == 0]
    return {"modules": modules, "modules_without_triggers": no_trigger_modules, "unused_connectors": unused_connectors,
            "orphan_child_playbooks": orphans, "unresolved_calls": unresolved, "undocumented": undocumented,
            "inactive": inactive,
            "summary": {"modules_without_triggers": len(no_trigger_modules), "unused_connectors": len(unused_connectors),
                        "orphan_child_playbooks": len(orphans), "unresolved_calls": len(unresolved),
                        "undocumented": len(undocumented), "inactive": len(inactive)}}


# ---------------------------------------------------------------- call chains

def call_chains(g, root: str | None = None, depth: int = 6) -> dict:
    depth = max(1, min(int(depth), 8))
    if root:
        rows = g.read(
            f"""
            MATCH (r:Playbook {{uuid: $root}})
            OPTIONAL MATCH path = (r)-[:CALLS*1..{depth}]->(c:Playbook)
            WITH r, [rel IN relationships(path) | [startNode(rel).uuid, endNode(rel).uuid]] AS edges
            UNWIND (CASE WHEN edges IS NULL OR size(edges) = 0 THEN [null] ELSE edges END) AS e
            RETURN r.uuid AS root, r.name AS root_name, collect(DISTINCT e) AS edges
            """, root=root)
        if not rows:
            return {"error": "no such playbook"}
        edges = [e for e in rows[0]["edges"] if e]
        ids = {rows[0]["root"]} | {x for e in edges for x in e}
        names = {r["uuid"]: {"name": r["name"], "pack": r["pack"], "placeholder": r["ph"]} for r in g.read(
            "UNWIND $ids AS u MATCH (p:Playbook {uuid: u}) RETURN p.uuid AS uuid, p.name AS name, p.pack AS pack, "
            "coalesce(p.placeholder, false) AS ph", ids=list(ids))}
        callers = g.read(
            f"MATCH path = (c:Playbook)-[:CALLS*1..{depth}]->(:Playbook {{uuid: $root}}) "
            "RETURN c.uuid AS uuid, c.name AS name, min(length(path)) AS hops ORDER BY hops, name", root=root)
        cycles = g.read(f"MATCH path = (r:Playbook {{uuid: $root}})-[:CALLS*1..{depth}]->(r) RETURN length(path) AS n LIMIT 1", root=root)
        return {"root": rows[0]["root"], "root_name": rows[0]["root_name"], "nodes": names,
                "edges": [{"from": a, "to": b} for a, b in edges], "callers": callers, "cycle": bool(cycles)}
    deepest = g.read(
        f"""
        MATCH path = (a:Playbook)-[:CALLS*1..{depth}]->(b:Playbook)
        WHERE NOT ()-[:CALLS]->(a) AND NOT coalesce(a.placeholder, false)
        WITH a, max(length(path)) AS depth, count(DISTINCT b) AS descendants
        RETURN a.uuid AS uuid, a.name AS name, a.pack AS pack, depth, descendants
        ORDER BY depth DESC, descendants DESC LIMIT 100
        """)
    most_called = g.read(
        """
        MATCH (c:Playbook)-[:CALLS]->(p:Playbook) WHERE NOT coalesce(p.placeholder, false)
        RETURN p.uuid AS uuid, p.name AS name, p.pack AS pack, count(DISTINCT c) AS callers
        ORDER BY callers DESC, name LIMIT 50
        """)
    cycles = g.read(
        f"""
        MATCH path = (a:Playbook)-[:CALLS*1..{depth}]->(a)
        RETURN a.uuid AS uuid, a.name AS name, length(path) AS length LIMIT 50
        """)
    return {"deepest": deepest, "most_called": most_called, "cycles": cycles}


# ---------------------------------------------------------------- security

def security(g) -> dict:
    """Hard-coded secrets (live from the graph) + security findings from the latest reviews."""
    from .review import sources, store

    secrets = g.read(
        """
        MATCH (p:Playbook)-[:HAS_STEP]->(s:Step {has_secret: true})
        RETURN p.uuid AS uuid, p.name AS name, p.pack AS pack, p.pack_type AS pack_type, collect(s.name) AS steps
        ORDER BY pack, name
        """)
    findings = []
    gh = store.github_scores()
    for key, r in gh.get("playbooks", {}).items():
        for f in r.get("security") or []:
            findings.append({**f, "key": key, "playbook": r["name"], "uuid": r["uuid"], "origin": "github", "source": r.get("repo")})
    for src in sources.sources():
        for key, r in store.source_reviews(src["id"]).get("reviews", {}).items():
            for f in r["findings"]:
                if f["category"] == "Security" and f["severity"] != "info":
                    findings.append({"rule": f["rule"], "title": f["title"], "severity": f["severity"], "step": f["step"],
                                     "detail": f["detail"], "key": key, "playbook": r["name"], "uuid": r.get("graph_uuid"),
                                     "origin": "upload", "source": src["name"]})
    order = ["critical", "high", "medium", "low"]
    findings.sort(key=lambda f: (order.index(f["severity"]) if f["severity"] in order else 9, f["rule"], f["playbook"]))
    by_rule: dict[str, dict] = {}
    for f in findings:
        e = by_rule.setdefault(f["rule"], {"rule": f["rule"], "title": f["title"], "severity": f["severity"], "findings": 0, "playbooks": set()})
        e["findings"] += 1
        e["playbooks"].add(f["key"])
    rules = sorted(({**v, "playbooks": len(v["playbooks"])} for v in by_rule.values()),
                   key=lambda x: (order.index(x["severity"]) if x["severity"] in order else 9, -x["playbooks"]))
    return {"secrets": secrets, "findings": findings[:3000], "by_rule": rules, "reviews_generated": gh.get("generated"),
            "summary": {"playbooks_with_secrets": len(secrets), "findings": len(findings),
                        "critical": sum(1 for f in findings if f["severity"] == "critical"),
                        "high": sum(1 for f in findings if f["severity"] == "high")}}


# ---------------------------------------------------------------- changes

def changes() -> dict:
    """Recent ingests (with playbook diffs) and upload syncs (with structural changes)."""
    from .ingest import state
    from .review import sources, store

    runs = []
    for repo, entry in state.load().items():
        for run in entry.get("runs", []):
            runs.append({"repo": repo, **run})
    runs.sort(key=lambda r: r.get("at") or "", reverse=True)
    uploads = []
    for src in sources.sources():
        for key, r in store.source_reviews(src["id"]).get("reviews", {}).items():
            hist = store.history(key)
            if r.get("changes") or len(hist) > 1:
                uploads.append({"key": key, "name": r["name"], "source": src["name"], "changes": r.get("changes"),
                                "history": hist[:5]})
    return {"ingests": runs[:300], "uploads": uploads}
