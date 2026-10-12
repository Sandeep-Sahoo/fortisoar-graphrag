"""Derived links, rebuilt from the graph after every ingestion or link change (no LLM).

* (Playbook)-[:REFERENCES {reasons}]->(Document {source: vendor})
    - via connector: the playbook uses a connector whose vendor doc is titled after it
    - via feature:   the playbook uses a feature (approval, decision, child playbook, trigger type)
                     that a vendor doc is about, or hard-codes a credential (-> connector config guidance)
* (PlaybookCollection)-[:DOCUMENTED_BY {playbooks, count}]->(Document)  rolled up from member playbooks'
  approved DOCUMENTED_BY links
* (PlaybookCollection)-[:REFERENCES {playbooks, count}]->(Document)     rolled up from REFERENCES
"""
from __future__ import annotations

import re

# step kind / trigger type -> words that identify the vendor doc about that feature (matched in its title)
FEATURE_DOCS = {
    # step kind / trigger type -> (words in the vendor doc title, reason). Matches the Playbooks Guide's
    # step chapters ("Core/Evaluate/Execute/References Steps") as well as older per-feature docs.
    "manual_input": (("evaluate steps", "manual input"), "approval / manual input step"),
    "decision": (("evaluate steps", "decision step"), "branches with a Decision step"),
    "wait": (("evaluate steps",), "uses a Wait step"),
    "reference": (("references steps", "reference a playbook"), "calls child playbooks"),
    "create_record": (("core steps",), "creates records"),
    "update_record": (("core steps",), "updates records"),
    "find_records": (("core steps",), "finds records"),
    "set_variable": (("core steps",), "sets variables"),
    "connector": (("execute steps",), "calls connector actions"),
    "code_snippet": (("execute steps",), "runs a code snippet"),
    "utility": (("execute steps",), "uses utility actions"),
    "trigger:manual": (("trigger steps", "playbook triggers"), "manual trigger"),
    "trigger:record_event": (("trigger steps", "playbook triggers"), "record create/update trigger"),
    "trigger:api_endpoint": (("trigger steps", "playbook triggers"), "custom API endpoint trigger"),
    "trigger:referenced": (("trigger steps",), "started by other playbooks"),
    "secret": (("security considerations", "connectors overview"), "hard-coded credential: see credential guidance"),
}


def _connector_title(name: str) -> str:
    return name.replace("-", " ").replace("_", " ").lower()


# Record steps name their target module in these fields; picklists/tags inside the payload are values, not targets.
MODULE_FIELDS = [
    re.compile(r'"collectionType": "/api/3/([a-z_]+)'),
    re.compile(r'"collection": "/api/3/([a-z_]+)'),
    re.compile(r'"module": "([a-z_]+)'),
]
NOT_MODULES = {"picklists", "picklist_names", "upsert", "tags", "contexts", "workflows", "workflow_steps",
               "workflow_collections", "appliances", "query", "people_query", "files"}
OPS = {"create_record": "creates", "update_record": "updates", "find_records": "finds"}


def derive_module_links(graph) -> int:
    """(Step)-[:ACTS_ON]->(Module) and (Playbook)-[:ACTS_ON {ops, steps}]->(Module) from record steps."""
    graph.run("MATCH (:Step)-[r:ACTS_ON]->(:Module) DELETE r")
    graph.run("MATCH (:Playbook)-[r:ACTS_ON]->(:Module) DELETE r")
    rows = graph.read(
        "MATCH (p:Playbook)-[:HAS_STEP]->(s:Step) WHERE s.kind IN ['create_record', 'update_record', 'find_records'] "
        "RETURN p.uuid AS pb, s.key AS step, s.name AS name, s.kind AS kind, s.args AS args"
    )
    step_links, pb_links = [], {}
    for r in rows:
        modules = {m for pat in MODULE_FIELDS for m in pat.findall(r["args"] or "")} - NOT_MODULES
        for module in modules:
            step_links.append({"step": r["step"], "module": module, "op": OPS[r["kind"]]})
            entry = pb_links.setdefault((r["pb"], module), {"ops": set(), "steps": []})
            entry["ops"].add(OPS[r["kind"]])
            entry["steps"].append(r["name"])
    for i in range(0, len(step_links), 2000):
        graph.run("UNWIND $rows AS r MATCH (s:Step {key: r.step}) MERGE (m:Module {name: r.module}) "
                  "MERGE (s)-[a:ACTS_ON]->(m) SET a.op = r.op", rows=step_links[i : i + 2000])
    rows = [{"pb": pb, "module": m, "ops": sorted(v["ops"]), "steps": v["steps"][:20]} for (pb, m), v in pb_links.items()]
    for i in range(0, len(rows), 2000):
        graph.run("UNWIND $rows AS r MATCH (p:Playbook {uuid: r.pb}), (m:Module {name: r.module}) "
                  "MERGE (p)-[a:ACTS_ON]->(m) SET a.ops = r.ops, a.steps = r.steps", rows=rows[i : i + 2000])
    return len(rows)


def derive_links(graph) -> dict:
    graph.run("MATCH (:Playbook)-[r:REFERENCES]->(:Document) DELETE r")
    graph.run("MATCH (:PlaybookCollection)-[r:DOCUMENTED_BY|REFERENCES]->(:Document) DELETE r")

    vendor_docs = graph.read(
        "MATCH (d:Document {source: 'vendor'}) RETURN d.id AS id, toLower(d.title) AS title, d.connector AS connector"
    )
    playbooks = graph.read(
        """
        MATCH (p:Playbook) WHERE NOT coalesce(p.placeholder, false)
        OPTIONAL MATCH (p)-[:HAS_STEP]->(s:Step)
        OPTIONAL MATCH (s)-[:USES]->(:ConnectorOperation)-[:OF]->(c:Connector)
        RETURN p.uuid AS uuid, p.trigger_type AS trigger_type,
               collect(DISTINCT s.kind) AS kinds, collect(DISTINCT c.name) AS connectors,
               any(x IN collect(s.has_secret) WHERE x) AS has_secret
        """
    )

    # connector -> its docs: exact (generated connector docs carry `connector`), else title words
    by_connector: dict[str, list[str]] = {}
    for d in vendor_docs:
        if d["connector"]:
            by_connector.setdefault(d["connector"], []).append(d["id"])
    all_connectors = {c for pb in playbooks for c in pb["connectors"]}
    for connector in all_connectors - set(by_connector):
        words = _connector_title(connector)
        by_connector[connector] = [d["id"] for d in vendor_docs if len(words) >= 4 and words in d["title"]]
    feature_docs = {feature: [d["id"] for d in vendor_docs if any(w in d["title"] for w in words)]
                    for feature, (words, _) in FEATURE_DOCS.items()}

    links: dict[tuple[str, str], set[str]] = {}

    def add(pb: str, doc: str, reason: str) -> None:
        links.setdefault((pb, doc), set()).add(reason)

    for pb in playbooks:
        for connector in pb["connectors"]:
            for doc in by_connector.get(connector, []):
                add(pb["uuid"], doc, f"uses connector {connector}")
        features = set(pb["kinds"]) | {f"trigger:{pb['trigger_type']}"}
        if pb["has_secret"]:
            features.add("secret")
        for feature in features & set(FEATURE_DOCS):
            for doc in feature_docs[feature]:
                add(pb["uuid"], doc, FEATURE_DOCS[feature][1])

    rows = [{"pb": pb, "doc": doc, "reasons": sorted(reasons)} for (pb, doc), reasons in links.items()]
    for i in range(0, len(rows), 2000):
        graph.run(
        """
        UNWIND $rows AS row
        MATCH (p:Playbook {uuid: row.pb}), (d:Document {id: row.doc})
        MERGE (p)-[r:REFERENCES]->(d)
        SET r.reasons = row.reasons, r.via = CASE WHEN any(x IN row.reasons WHERE x STARTS WITH 'uses connector')
                                                  THEN 'connector' ELSE 'feature' END
        """,
        rows=rows[i : i + 2000],
    )

    # a solution pack's own docs (README, setup, usage, contents) describe every playbook it ships
    graph.run(
        """
        MATCH (sp:SolutionPack)-[:INCLUDES]->(:PlaybookCollection)-[:CONTAINS]->(p:Playbook), (sp)-[:DOCUMENTED_BY]->(d:Document)
        MERGE (p)-[r:REFERENCES]->(d)
        SET r.reasons = coalesce(r.reasons, []) + ['documentation of solution pack ' + sp.label], r.via = coalesce(r.via, 'pack')
        """
    )
    pack_refs = graph.read("MATCH (:Playbook)-[r:REFERENCES {via: 'pack'}]->() RETURN count(r) AS n")[0]["n"]

    rollup = """
    MATCH (c:PlaybookCollection)-[:CONTAINS]->(p:Playbook)-[r:{rel}]->(d:Document)
    {where}
    WITH c, d, collect(DISTINCT p.name) AS playbooks
    MERGE (c)-[x:{rel}]->(d)
    SET x.playbooks = playbooks, x.count = size(playbooks), x.derived = true
    RETURN count(x) AS n
    """
    documented = graph.run(rollup.format(rel="DOCUMENTED_BY", where="WHERE r.status = 'approved'"))[0]["n"]
    referenced = graph.run(rollup.format(rel="REFERENCES", where=""))[0]["n"]
    module_links = derive_module_links(graph)
    return {"playbook_module_links": module_links, "playbook_vendor_refs": len(rows) + pack_refs, "collection_internal_docs": documented,
            "collection_vendor_refs": referenced}
