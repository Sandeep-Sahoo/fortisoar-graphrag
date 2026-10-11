"""Hybrid search (vector + fulltext, fused with RRF) and graph expansion."""
from __future__ import annotations

import re

LUCENE_SPECIAL = re.compile(r'[+\-&|!(){}\[\]^"~*?:\\/]')


def lucene_query(text: str) -> str:
    words = [w for w in LUCENE_SPECIAL.sub(" ", text).split() if w.upper() not in ("AND", "OR", "NOT")]
    return " ".join(words) or "*"


def rrf(*ranked_lists: list[dict], key: str = "id", k: int = 60) -> list[dict]:
    """Reciprocal rank fusion: robust way to merge vector and keyword rankings."""
    scores: dict[str, float] = {}
    items: dict[str, dict] = {}
    for ranked in ranked_lists:
        for rank, item in enumerate(ranked):
            scores[item[key]] = scores.get(item[key], 0.0) + 1.0 / (k + rank + 1)
            items.setdefault(item[key], item)
    return [dict(items[i], score=round(s, 5)) for i, s in sorted(scores.items(), key=lambda x: -x[1])]


class Retriever:
    def __init__(self, graph, embedder, include_proposed_links: bool = False):
        self.graph = graph
        self.embedder = embedder
        self.link_statuses = ["approved", "proposed"] if include_proposed_links else ["approved"]

    # ------------------------------------------------------------ entry points

    def search_chunks(self, query: str, source: str | None = None, k: int = 6, vector=None) -> list[dict]:
        vector = vector or self.embedder.embed([query], query=True)[0]
        fields = ("node.id AS id, node.text AS text, node.source AS source, node.doc_id AS doc_id, "
                  "node.doc_title AS title, node.version AS version")
        filt = "WHERE $source IS NULL OR node.source = $source"
        vec = self.graph.read(
            f"CALL db.index.vector.queryNodes('chunk_embedding', $n, $vec) YIELD node, score {filt} "
            f"RETURN {fields} ORDER BY score DESC LIMIT $k",
            n=k * 4, vec=vector, source=source, k=k,
        )
        kw = self.graph.read(
            f"CALL db.index.fulltext.queryNodes('chunk_text', $q) YIELD node, score {filt} "
            f"RETURN {fields} ORDER BY score DESC LIMIT $k",
            q=lucene_query(query), source=source, k=k,
        )
        return rrf(vec, kw)[:k]

    def search_playbooks(self, query: str, k: int = 5, vector=None) -> list[dict]:
        vector = vector or self.embedder.embed([query], query=True)[0]
        vec = self.graph.read(
            "CALL db.index.vector.queryNodes('playbook_embedding', $k, $vec) YIELD node, score "
            "RETURN node.uuid AS id, node.name AS name, node.summary AS summary",
            k=k, vec=vector,
        )
        # Keyword hits on steps / connectors / modules are mapped back to the playbooks that use them.
        kw = self.graph.read(
            """
            CALL db.index.fulltext.queryNodes('entity_text', $q) YIELD node, score
            WITH node, score,
              CASE
                WHEN node:Playbook THEN [node]
                WHEN node:Step THEN [(p:Playbook)-[:HAS_STEP]->(node) | p]
                WHEN node:ConnectorOperation THEN [(p:Playbook)-[:HAS_STEP]->(:Step)-[:USES]->(node) | p]
                WHEN node:Connector THEN [(p:Playbook)-[:HAS_STEP]->(:Step)-[:USES]->(:ConnectorOperation)-[:OF]->(node) | p]
                WHEN node:Module THEN [(p:Playbook)-[:TRIGGERED_ON]->(node) | p]
                ELSE [] END AS playbooks
            UNWIND playbooks AS p
            WITH p, max(score) AS score WHERE NOT coalesce(p.placeholder, false)
            RETURN p.uuid AS id, p.name AS name, p.summary AS summary
            ORDER BY score DESC LIMIT $k
            """,
            q=lucene_query(query), k=k * 2,
        )
        return rrf(vec, kw)[:k]

    # ------------------------------------------------------------ graph lookups

    def resolve_playbook(self, name_or_uuid: str) -> str | None:
        rows = self.graph.read(
            "MATCH (p:Playbook) WHERE p.uuid = $q OR toLower(p.name) = toLower($q) RETURN p.uuid AS uuid LIMIT 1",
            q=name_or_uuid,
        ) or self.graph.read(
            "MATCH (p:Playbook) WHERE toLower(p.name) CONTAINS toLower($q) AND NOT coalesce(p.placeholder, false) "
            "RETURN p.uuid AS uuid ORDER BY size(p.name) LIMIT 1",
            q=name_or_uuid,
        )
        return rows[0]["uuid"] if rows else None

    def get_playbook(self, uuid: str) -> dict | None:
        rows = self.graph.read(
            """
            MATCH (p:Playbook {uuid: $uuid})
            OPTIONAL MATCH (p)-[t:TRIGGERED_ON]->(m:Module)
            WITH p, collect(m.name) AS modules
            OPTIONAL MATCH (p)-[:HAS_STEP]->(s:Step)
            OPTIONAL MATCH (s)-[:USES]->(op:ConnectorOperation)
            OPTIONAL MATCH (s)-[nx:NEXT]->(n:Step)
            WITH p, modules, s, op, collect(CASE WHEN n IS NULL THEN null ELSE {to: n.name, label: nx.label} END) AS next
            ORDER BY s.order
            WITH p, modules, collect(CASE WHEN s IS NULL THEN null ELSE
                 {name: s.name, kind: s.kind, operation: op.key, next: next, args: s.args,
                  has_secret: coalesce(s.has_secret, false)} END) AS steps
            OPTIONAL MATCH (p)-[:CALLS]->(callee:Playbook)
            WITH p, modules, steps, collect(DISTINCT callee.name) AS calls
            OPTIONAL MATCH (caller:Playbook)-[:CALLS]->(p)
            WITH p, modules, steps, calls, collect(DISTINCT caller.name) AS called_by
            OPTIONAL MATCH (p)-[r:DOCUMENTED_BY]->(d:Document) WHERE r.status IN $statuses
            WITH p, modules, steps, calls, called_by,
                 collect(DISTINCT CASE WHEN d IS NULL THEN null ELSE {id: d.id, title: d.title, status: r.status} END) AS docs
            OPTIONAL MATCH (p)-[rf:REFERENCES]->(vd:Document)
            RETURN p.uuid AS uuid, p.name AS name, p.collection AS collection, p.description AS description,
                   p.trigger_type AS trigger_type, p.active AS active, p.tags AS tags, modules, steps,
                   calls, called_by, docs,
                   collect(DISTINCT CASE WHEN vd IS NULL THEN null ELSE {id: vd.id, title: vd.title, reasons: rf.reasons} END) AS references
            """,
            uuid=uuid, statuses=self.link_statuses,
        )
        return rows[0] if rows else None

    def find_dependents(self, uuid: str, depth: int = 3) -> list[dict]:
        depth = max(1, min(int(depth), 5))
        return self.graph.read(
            f"MATCH path = (caller:Playbook)-[:CALLS*1..{depth}]->(p:Playbook {{uuid: $uuid}}) "
            "RETURN caller.name AS name, caller.uuid AS uuid, min(length(path)) AS hops ORDER BY hops, name",
            uuid=uuid,
        )

    def playbooks_for_chunks(self, chunk_ids: list[str]) -> list[dict]:
        """Company tie-in: vendor chunks -> connectors they explain -> our playbooks using them;
        internal chunks -> playbooks their document is linked to."""
        return self.graph.read(
            """
            UNWIND $ids AS cid
            MATCH (c:Chunk {id: cid})
            CALL (c) {
              MATCH (c)-[:EXPLAINS]->(con:Connector)<-[:OF]-(:ConnectorOperation)<-[:USES]-(:Step)<-[:HAS_STEP]-(p:Playbook)
              RETURN p, 'uses connector ' + con.name AS why
              UNION
              MATCH (d:Document)-[:HAS_CHUNK]->(c), (p:Playbook)-[r:DOCUMENTED_BY]->(d) WHERE r.status IN $statuses
              RETURN p, 'documented by ' + d.title AS why
            }
            RETURN cid AS chunk, p.uuid AS uuid, p.name AS name, collect(DISTINCT why)[0..3] AS why
            LIMIT 20
            """,
            ids=chunk_ids, statuses=self.link_statuses,
        )

    # ------------------------------------------------------------ context for answering

    # Split into stages so the chat UI can show progress: search -> expand -> assemble.

    def search_all(self, question: str) -> dict:
        """Stage 1: hybrid search entry points (playbooks, vendor chunks, internal chunks)."""
        vector = self.embedder.embed([question], query=True)[0]
        return {
            "playbooks": self.search_playbooks(question, k=5, vector=vector),
            "vendor": self.search_chunks(question, source="vendor", k=5, vector=vector),
            "internal": self.search_chunks(question, source="internal", k=4, vector=vector),
        }

    def expand(self, hits: dict) -> dict:
        """Stage 2: pull in playbooks linked from the doc hits (1-2 hops), so vendor answers tie
        to our deployment."""
        playbooks = list(hits["playbooks"])
        linked = self.playbooks_for_chunks([c["id"] for c in hits["vendor"] + hits["internal"]])
        known = {p["id"] for p in playbooks}
        added = []
        for row in linked:
            if row["uuid"] not in known and len(playbooks) < 8:
                playbooks.append({"id": row["uuid"], "name": row["name"], "linked_because": row["why"]})
                known.add(row["uuid"])
                added.append(row["name"])
        return {**hits, "playbooks": playbooks, "linked": added}

    def assemble(self, hits: dict, detail_playbooks: int = 3) -> tuple[str, list[dict]]:
        """Stage 3: labelled context blocks + source list for the answer prompt."""
        blocks, sources = [], []
        for i, pb in enumerate(hits["playbooks"], 1):
            label = f"C{i}"
            detail = self.get_playbook(pb["id"]) if i <= detail_playbooks else None
            if detail:
                steps = "\n".join(
                    f"    {n}. {s['name']} ({s['kind']}{', ' + s['operation'] if s['operation'] else ''})"
                    + (" [SECURITY: hard-coded secret, redacted]" if s["has_secret"] else "")
                    for n, s in enumerate(detail["steps"], 1)
                )
                body = (f"Collection: {detail['collection']}\nTrigger: {detail['trigger_type']} "
                        f"on {', '.join(detail['modules']) or '-'}\nDescription: {detail['description'] or '-'}\n"
                        f"Steps:\n{steps}\nCalls: {', '.join(detail['calls']) or '-'}\n"
                        f"Called by: {', '.join(detail['called_by']) or '-'}\n"
                        f"Internal docs: {', '.join(d['title'] for d in detail['docs']) or '-'}\n"
                        f"Related vendor docs: {', '.join(r['title'] for r in detail['references']) or '-'}")
            else:
                body = pb.get("summary") or f"Linked because: {', '.join(pb.get('linked_because', []))}"
            blocks.append(f"[{label}] COMPANY PLAYBOOK '{pb['name']}'\n{body}")
            sources.append({"label": label, "type": "playbook", "name": pb["name"], "uuid": pb["id"]})

        for prefix, chunks, kind in (("V", hits["vendor"], "vendor_doc"), ("D", hits["internal"], "internal_doc")):
            for i, c in enumerate(chunks, 1):
                label = f"{prefix}{i}"
                version = f" (FortiSOAR {c['version']})" if kind == "vendor_doc" and c.get("version") else ""
                blocks.append(f"[{label}] {kind.upper()} '{c['title']}'{version}\n{c['text']}")
                sources.append({"label": label, "type": kind, "title": c["title"], "chunk": c["id"]})

        return "\n\n---\n\n".join(blocks), sources

    def build_context(self, question: str, detail_playbooks: int = 3) -> tuple[str, list[dict]]:
        return self.assemble(self.expand(self.search_all(question)), detail_playbooks)

    def step_flow(self, uuid: str) -> dict | None:
        """Steps with keys, NEXT edges and called child playbooks, for diagram generation."""
        rows = self.graph.read(
            """
            MATCH (p:Playbook {uuid: $uuid})
            OPTIONAL MATCH (p)-[:TRIGGERED_ON]->(m:Module)
            WITH p, collect(m.name) AS modules
            OPTIONAL MATCH (p)-[:HAS_STEP]->(s:Step)
            OPTIONAL MATCH (s)-[:USES]->(op:ConnectorOperation)
            OPTIONAL MATCH (s)-[nx:NEXT]->(n:Step)
            OPTIONAL MATCH (p)-[c:CALLS {via_step: s.key}]->(child:Playbook)
            WITH p, modules, s, op, child,
                 collect(CASE WHEN n IS NULL THEN null ELSE {to: n.key, label: nx.label} END) AS next
            ORDER BY s.order
            RETURN p.name AS name, p.trigger_type AS trigger_type, modules,
                   collect(CASE WHEN s IS NULL THEN null ELSE
                     {key: s.key, name: s.name, kind: s.kind, connector: op.connector, operation: op.name,
                      has_secret: coalesce(s.has_secret, false), child: child.name, next: next} END) AS steps
            """,
            uuid=uuid,
        )
        return rows[0] if rows and rows[0]["name"] else None
