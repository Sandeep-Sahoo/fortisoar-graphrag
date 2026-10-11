"""Catalog listings and detail views for the UI's Catalog page (playbooks, collections, connectors, documents)."""
from __future__ import annotations

KINDS = ("packs", "playbooks", "collections", "connectors", "documents")

LIST = {
    "packs": """
        MATCH (sp:SolutionPack)
        RETURN sp.repo AS id, sp.label AS name, sp.kind AS kind, sp.version AS version, sp.category AS category,
               sp.publisher AS publisher, sp.description AS description,
               COUNT { (sp)-[:INCLUDES]->() } AS collections,
               COUNT { (sp)-[:INCLUDES]->()-[:CONTAINS]->() } AS playbooks,
               COUNT { (sp)-[:DOCUMENTED_BY]->() } AS docs,
               [(sp)-[:DEPENDS_ON]->(d:SolutionPack) | d.label] AS depends_on,
               [(sp)-[:INCLUDES]->()-[:CONTAINS]->()-[:HAS_STEP]->()-[:USES]->()-[:OF]->(c:Connector)
                  WHERE NOT c.name IN ['cyops_utilities', 'code-snippet'] | c.name] AS connectors
        ORDER BY playbooks DESC, name""",
    "playbooks": """
        MATCH (p:Playbook) WHERE NOT coalesce(p.placeholder, false)
        OPTIONAL MATCH (p)-[:TRIGGERED_ON]->(m:Module)
        WITH p, collect(DISTINCT m.name) AS modules
        RETURN p.uuid AS id, p.name AS name, p.collection AS collection, p.trigger_type AS trigger, modules,
               p.pack AS pack, p.pack_type AS pack_type,
               p.description AS description, p.active AS active,
               COUNT { (p)-[:HAS_STEP]->() } AS steps,
               [(p)-[:HAS_STEP]->()-[:USES]->()-[:OF]->(c:Connector) | c.name] AS connectors,
               COUNT { (p)-[:CALLS]->() } AS calls,
               COUNT { (:Playbook)-[:CALLS]->(p) } AS called_by,
               COUNT { (p)-[:DOCUMENTED_BY {status: 'approved'}]->() } AS internal_docs,
               COUNT { (p)-[:REFERENCES]->() } AS vendor_docs,
               EXISTS { (p)-[:HAS_STEP]->(:Step {has_secret: true}) } AS has_secret
        ORDER BY name""",
    "collections": """
        MATCH (c:PlaybookCollection)
        RETURN c.name AS id, c.name AS name,
               [(c)-[:CONTAINS]->(p:Playbook) | p.name] AS playbooks,
               COUNT { (c)-[:DOCUMENTED_BY]->() } AS internal_docs,
               COUNT { (c)-[:REFERENCES]->() } AS vendor_docs,
               [(c)-[:CONTAINS]->(:Playbook)-[:HAS_STEP]->()-[:USES]->()-[:OF]->(k:Connector) | k.name] AS connectors
        ORDER BY name""",
    "connectors": """
        MATCH (c:Connector)
        RETURN c.name AS id, c.name AS name, c.label AS label, c.version AS version, c.category AS category,
               COUNT { (c)-[:HAS_SAMPLES]->()-[:CONTAINS]->() } AS samples,
               [(c)<-[:OF]-(op:ConnectorOperation) | op.name] AS operations,
               [(c)<-[:OF]-(:ConnectorOperation)<-[:USES]-(:Step)<-[:HAS_STEP]-(p:Playbook) | p.name] AS playbooks,
               [(c)<-[:EXPLAINS]-(:Chunk)<-[:HAS_CHUNK]-(d:Document {source: 'vendor'}) | d.title] AS docs
        ORDER BY name""",
    "documents": """
        MATCH (d:Document)
        RETURN d.id AS id, d.title AS name, d.source AS source, d.version AS version, d.path AS path,
               d.kind AS kind, d.pack AS pack, d.url AS url,
               COUNT { (d)-[:HAS_CHUNK]->() } AS chunks,
               [(p:Playbook)-[r:DOCUMENTED_BY]->(d) WHERE r.status = 'approved' | p.name] AS documents,
               [(p:Playbook)-[:REFERENCES]->(d) | p.name] AS referenced_by,
               COUNT { (:Playbook)-[:DOCUMENTED_BY {status: 'proposed'}]->(d) } AS pending_links
        ORDER BY source, name""",
}


def _dedupe(values) -> list:
    return sorted({v for v in values if v})


def catalog(graph, kind: str) -> list[dict]:
    rows = graph.read(LIST[kind])
    for row in rows:
        for key in ("connectors", "operations", "playbooks", "docs", "documents", "referenced_by", "depends_on"):
            if isinstance(row.get(key), list):
                row[key] = _dedupe(row[key])
    return rows


def pack_detail(graph, repo: str) -> dict | None:
    rows = graph.read(
        """
        MATCH (sp:SolutionPack {repo: $repo})
        OPTIONAL MATCH (sp)-[:INCLUDES]->(c:PlaybookCollection)
        OPTIONAL MATCH (c)-[:CONTAINS]->(p:Playbook)
        WITH sp, c, collect(CASE WHEN p IS NULL THEN null ELSE {uuid: p.uuid, name: p.name, trigger: p.trigger_type} END) AS pbs
        ORDER BY c.name
        WITH sp, collect(CASE WHEN c IS NULL THEN null ELSE {name: c.name, playbooks: pbs} END) AS collections
        RETURN sp.repo AS repo, sp.label AS label, sp.kind AS kind, sp.version AS version, sp.category AS category,
               sp.publisher AS publisher, sp.description AS description, sp.url AS url, collections,
               [(sp)-[:DEPENDS_ON]->(d:SolutionPack) | {repo: d.repo, label: d.label}] AS depends_on,
               [(d:SolutionPack)-[:DEPENDS_ON]->(sp) | {repo: d.repo, label: d.label}] AS required_by,
               [(sp)-[:DOCUMENTED_BY]->(doc:Document) | {id: doc.id, title: doc.title}] AS docs,
               [(sp)-[:INCLUDES]->()-[:CONTAINS]->()-[:HAS_STEP]->()-[:USES]->()-[:OF]->(k:Connector)
                  WHERE NOT k.name IN ['cyops_utilities', 'code-snippet'] | k.name] AS connectors
        """,
        repo=repo,
    )
    if not rows:
        return None
    detail = rows[0]
    detail["connectors"] = _dedupe(detail["connectors"])
    return detail


def connector_detail(graph, name: str) -> dict | None:
    rows = graph.read(
        """
        MATCH (c:Connector {name: $name})
        OPTIONAL MATCH (c)<-[:OF]-(op:ConnectorOperation)
        OPTIONAL MATCH (op)<-[u:USES]-(s:Step)<-[:HAS_STEP]-(p:Playbook)
        WITH c, op, collect(CASE WHEN p IS NULL THEN null ELSE
             {playbook: p.name, uuid: p.uuid, step: s.name, version: u.connector_version, has_secret: coalesce(s.has_secret, false)} END) AS uses
        WITH c, op, uses ORDER BY size(uses) DESC, op.name
        WITH c, collect(CASE WHEN op IS NULL THEN null ELSE {operation: op.name, title: op.title,
                                                             description: op.description, uses: uses} END) AS operations
        OPTIONAL MATCH (c)<-[:EXPLAINS]-(:Chunk)<-[:HAS_CHUNK]-(d:Document)
        WITH c, operations, collect(DISTINCT {id: d.id, title: d.title, source: d.source}) AS mentioned
        RETURN c.name AS name, c.label AS label, c.version AS version, c.category AS category, c.publisher AS publisher,
               c.description AS description, c.url AS url, c.help_url AS help_url, operations,
               [(c)-[:DOCUMENTED_BY]->(doc:Document) | {id: doc.id, title: doc.title, source: doc.source}] + mentioned AS docs,
               [(c)-[:HAS_SAMPLES]->(col:PlaybookCollection) | col.name] AS samples,
               1 AS _
        """,
        name=name,
    )
    if not rows:
        return None
    detail = rows[0]
    detail.pop("_", None)
    seen, docs = set(), []
    for d in detail["docs"]:
        if d.get("id") and d["id"] not in seen:
            seen.add(d["id"]); docs.append(d)
    detail["docs"] = docs[:60]
    return detail


def document_detail(graph, doc_id: str) -> dict | None:
    rows = graph.read(
        """
        MATCH (d:Document {id: $id})
        OPTIONAL MATCH (d)-[:HAS_CHUNK]->(c:Chunk)
        WITH d, c ORDER BY c.idx
        WITH d, collect(c.text) AS chunks, collect(c.id) AS chunk_ids
        OPTIONAL MATCH (p:Playbook)-[r:DOCUMENTED_BY]->(d)
        WITH d, chunks, chunk_ids, collect(CASE WHEN p IS NULL THEN null ELSE
             {playbook: p.name, uuid: p.uuid, status: r.status, method: r.method, confidence: r.confidence, reason: r.reason} END) AS links
        OPTIONAL MATCH (q:Playbook)-[rf:REFERENCES]->(d)
        WITH d, chunks, chunk_ids, links, collect(CASE WHEN q IS NULL THEN null ELSE
             {playbook: q.name, uuid: q.uuid, reasons: rf.reasons} END) AS references
        OPTIONAL MATCH (d)-[:HAS_CHUNK]->(:Chunk)-[:EXPLAINS]->(x)
        RETURN d.id AS id, d.title AS title, d.source AS source, d.version AS version, d.path AS path,
               d.kind AS kind, d.url AS url, d.help_url AS help_url, d.pack AS pack, d.repo AS repo,
               [(owner)-[:DOCUMENTED_BY]->(d) WHERE owner:Connector OR owner:SolutionPack |
                  {type: labels(owner)[0], id: coalesce(owner.repo, owner.name), label: coalesce(owner.label, owner.name)}] AS owners,
               chunks, links, references[0..200] AS references,
               collect(DISTINCT CASE WHEN x:Connector THEN 'connector:' + x.name ELSE 'concept:' + x.name END) AS explains
        """,
        id=doc_id,
    )
    if not rows:
        return None
    detail = rows[0]
    # chunks overlap by design (200 chars); show each once by trimming the repeated head
    text, prev = [], ""
    for chunk in detail.pop("chunks"):
        overlap = next((k for k in range(min(len(prev), len(chunk), 260), 20, -1) if prev.endswith(chunk[:k])), 0)
        text.append(chunk[overlap:].lstrip())
        prev = chunk
    detail["text"] = "\n\n".join(text)
    detail["explains"] = sorted(e for e in detail["explains"] if e)
    return detail
