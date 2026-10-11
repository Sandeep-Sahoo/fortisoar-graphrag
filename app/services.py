"""Lazily constructed shared services and the agent-facing tool functions.

The same tool functions back the REST API and the MCP server.
"""
from __future__ import annotations

import re
from functools import cached_property, lru_cache

from .config import get_settings
from .graph import Graph
from .providers import make_embedder, make_llm
from .retrieval import Retriever
from .scrub import Scrubber

WRITE_CYPHER = re.compile(
    r"\b(CREATE|MERGE|DELETE|DETACH|SET|REMOVE|DROP|FOREACH|LOAD\s+CSV)\b|\bCALL\s+(apoc|dbms|db\.create|gds)\.",
    re.I,
)

ANSWER_SYSTEM = """You are the FortiSOAR automation assistant for {company}'s SOC.
Answer only from the CONTEXT. Sources are labelled:
  [C#] = {company}'s deployed playbooks, [D#] = {company}'s internal docs, [V#] = Fortinet FortiSOAR vendor docs.
Rules:
- When a company playbook or internal doc answers the question, lead with it ("In your playbook X ...").
- Use vendor docs for general product behaviour ("Per FortiSOAR docs ...") and note the version if it matters.
- Cite every factual sentence with its label(s), e.g. [C1][V2].
- If the context does not contain the answer, say so plainly and suggest what to look at. Never invent playbook names, steps, or connector operations."""


class Services:
    @cached_property
    def settings(self):
        return get_settings()

    @cached_property
    def graph(self) -> Graph:
        return Graph(self.settings)

    @cached_property
    def llm(self):
        return make_llm(self.settings)

    @cached_property
    def embedder(self):
        return make_embedder(self.settings)

    @cached_property
    def scrubber(self) -> Scrubber:
        return Scrubber(self.settings.scrub_terms)

    @cached_property
    def chats(self):
        from .chat import ChatStore

        return ChatStore(self.graph)

    @cached_property
    def retriever(self) -> Retriever:
        return Retriever(self.graph, self.embedder, self.settings.include_proposed_links)

    # ------------------------------------------------------------ tools

    def search_vendor_docs(self, query: str, k: int = 6) -> list[dict]:
        return self.retriever.search_chunks(query, source="vendor", k=k)

    def search_internal_docs(self, query: str, k: int = 6) -> list[dict]:
        return self.retriever.search_chunks(query, source="internal", k=k)

    def search_company_playbooks(self, query: str, k: int = 5) -> list[dict]:
        return self.retriever.search_playbooks(query, k=k)

    def get_playbook(self, name_or_uuid: str) -> dict:
        uuid = self.retriever.resolve_playbook(name_or_uuid)
        return (self.retriever.get_playbook(uuid) if uuid else None) or {"error": f"no playbook matching '{name_or_uuid}'"}

    def find_dependents(self, name_or_uuid: str, depth: int = 3) -> dict:
        uuid = self.retriever.resolve_playbook(name_or_uuid)
        if not uuid:
            return {"error": f"no playbook matching '{name_or_uuid}'"}
        return {"playbook": uuid, "callers": self.retriever.find_dependents(uuid, depth)}

    def run_cypher(self, query: str) -> list[dict] | dict:
        if WRITE_CYPHER.search(query):
            return {"error": "only read-only Cypher is allowed"}
        return self.graph.read_only(query)

    def ask(self, question: str, with_graph: bool = False) -> dict:
        context, sources = self.retriever.build_context(question)
        if not sources:
            return {"answer": "Nothing relevant is indexed yet. Ingest playbooks and docs first.", "sources": []}
        system = ANSWER_SYSTEM.format(company=self.settings.company_name)
        answer = self.llm.complete(system, f"CONTEXT:\n{context}\n\nQUESTION: {question}")
        cited = set(re.findall(r"\[([CDV]\d+)\]", answer))
        result = {"answer": answer, "sources": [s for s in sources if s["label"] in cited] or sources}
        if with_graph:
            uuids = [s["uuid"] for s in result["sources"] if s["type"] == "playbook"]
            result["graph"] = self.playbook_graph(uuids, steps=True) if uuids else {"nodes": [], "edges": []}
        return result

    # ------------------------------------------------------------ visualisation

    def playbook_graph(self, uuids: list[str], steps: bool = True) -> dict:
        """Neighbourhood of the given playbooks: collection, trigger modules, callers/callees,
        docs, and (optionally) steps -> connector operations -> connectors."""
        step_paths = """
          UNION MATCH path = (p)-[:HAS_STEP]->(:Step) RETURN path
          UNION MATCH path = (p)-[:HAS_STEP]->(:Step)-[:NEXT]->(:Step) RETURN path
          UNION MATCH path = (p)-[:HAS_STEP]->(:Step)-[:USES]->(:ConnectorOperation)-[:OF]->(:Connector) RETURN path
        """ if steps else ""
        graph = self.graph.read_paths(
            f"""
            UNWIND $uuids AS id
            MATCH (p:Playbook {{uuid: id}})
            CALL (p) {{
              MATCH path = (p)<-[:CONTAINS]-(:PlaybookCollection) RETURN path
              UNION MATCH path = (p)-[:TRIGGERED_ON|CALLS]-() RETURN path
              UNION MATCH path = (p)-[r:DOCUMENTED_BY]->(:Document) WHERE r.status IN $statuses RETURN path
              UNION MATCH path = (p)-[:REFERENCES]->(:Document) RETURN path
              {step_paths}
            }}
            RETURN path
            """,
            uuids=uuids, statuses=self.retriever.link_statuses,
        )
        for node in graph["nodes"]:
            node["focus"] = node["props"].get("uuid") in uuids
        return graph

    def playbook_diagram(self, name_or_uuid: str, kind: str = "flowchart") -> dict:
        from . import diagrams

        uuid = self.retriever.resolve_playbook(name_or_uuid)
        flow = self.retriever.step_flow(uuid) if uuid else None
        if not flow:
            return {"error": f"no playbook matching '{name_or_uuid}'"}
        render = diagrams.sequence if kind == "sequence" else diagrams.flowchart
        return {"uuid": uuid, "name": flow["name"], "kind": kind, "mermaid": render(flow)}

    def list_collections(self) -> list[dict]:
        return self.graph.read(
            """
            MATCH (c:PlaybookCollection)
            RETURN c.name AS name,
                   COUNT { (c)-[:CONTAINS]->(:Playbook) } AS playbooks,
                   COUNT { (c)-[:DOCUMENTED_BY]->(:Document) } AS internal_docs,
                   COUNT { (c)-[:REFERENCES]->(:Document) } AS vendor_docs
            ORDER BY name
            """
        )

    def get_collection(self, name: str) -> dict:
        """A collection with its playbooks and every internal / vendor doc linked through them."""
        rows = self.graph.read(
            """
            MATCH (c:PlaybookCollection) WHERE toLower(c.name) = toLower($name) OR toLower(c.name) CONTAINS toLower($name)
            WITH c ORDER BY size(c.name) LIMIT 1
            OPTIONAL MATCH (c)-[:CONTAINS]->(p:Playbook)
            WITH c, collect({uuid: p.uuid, name: p.name, trigger: p.trigger_type}) AS playbooks
            OPTIONAL MATCH (c)-[dd:DOCUMENTED_BY]->(d:Document)
            WITH c, playbooks, collect(CASE WHEN d IS NULL THEN null ELSE {title: d.title, id: d.id, playbooks: dd.playbooks} END) AS internal_docs
            OPTIONAL MATCH (c)-[rr:REFERENCES]->(v:Document)
            RETURN c.name AS name, playbooks, internal_docs,
                   collect(CASE WHEN v IS NULL THEN null ELSE {title: v.title, id: v.id, playbooks: rr.playbooks} END) AS vendor_docs
            """,
            name=name,
        )
        return rows[0] if rows else {"error": f"no collection matching '{name}'"}

    def overview_graph(self, limit: int = 300, mode: str = "auto") -> dict:
        """mode=auto: large graphs (e.g. the full Fortinet content) get the ecosystem map, small ones the
        playbook map. mode=ecosystem / playbooks forces one (the playbook map is capped at 1500 playbooks)."""
        total = self.graph.read("MATCH (p:Playbook) WHERE NOT coalesce(p.placeholder, false) RETURN count(p) AS n")[0]["n"]
        has_packs = self.graph.read("MATCH (sp:SolutionPack)-[:INCLUDES]->() RETURN count(sp) > 0 AS ok")[0]["ok"]
        if has_packs and (mode == "ecosystem" or (mode == "auto" and total > limit)):
            return {**self.ecosystem_graph(), "total_playbooks": total}
        cap = 1500 if mode == "playbooks" else limit
        return {**self.playbook_overview(cap), "mode": "playbooks", "total_playbooks": total, "shown_limit": cap,
                "can_ecosystem": has_packs}

    def ecosystem_graph(self) -> dict:
        """Solution packs (with playbooks) and the connectors their playbooks use, plus pack dependencies."""
        packs = self.graph.read(
            """
            MATCH (sp:SolutionPack)-[:INCLUDES]->(:PlaybookCollection)-[:CONTAINS]->(p:Playbook)
            RETURN sp.repo AS repo, sp.label AS label, sp.kind AS kind, sp.version AS version,
                   count(DISTINCT p) AS playbooks, count(DISTINCT p.collection) AS collections
            """
        )
        uses = self.graph.read(
            """
            MATCH (sp:SolutionPack)-[:INCLUDES]->(:PlaybookCollection)-[:CONTAINS]->(:Playbook)
                  -[:HAS_STEP]->(:Step)-[:USES]->(:ConnectorOperation)-[:OF]->(c:Connector)
            WHERE NOT c.name IN ['cyops_utilities', 'code-snippet']
            RETURN sp.repo AS repo, c.name AS connector, coalesce(c.label, c.name) AS label, count(*) AS steps
            """
        )
        deps = self.graph.read("MATCH (a:SolutionPack)-[:DEPENDS_ON]->(b:SolutionPack) RETURN a.repo AS a, b.repo AS b")
        nodes, edges = {}, []
        for p in packs:
            nodes[f"sp:{p['repo']}"] = {"id": f"sp:{p['repo']}", "type": "SolutionPack", "label": p["label"],
                                         "props": {"repo": p["repo"], "kind": p["kind"], "version": p["version"],
                                                   "playbooks": p["playbooks"], "collections": p["collections"]}}
        for u in uses:
            cid = f"con:{u['connector']}"
            nodes.setdefault(cid, {"id": cid, "type": "Connector", "label": u["label"], "props": {"name": u["connector"]}})
            edges.append({"id": f"sp:{u['repo']}>u>{u['connector']}", "source": f"sp:{u['repo']}", "target": cid,
                          "type": "USES", "weight": u["steps"]})
        for d in deps:
            if f"sp:{d['a']}" in nodes and f"sp:{d['b']}" in nodes:
                edges.append({"id": f"sp:{d['a']}>d>{d['b']}", "source": f"sp:{d['a']}", "target": f"sp:{d['b']}", "type": "DEPENDS_ON"})
        return {"nodes": list(nodes.values()), "edges": edges, "mode": "ecosystem", "can_ecosystem": True}

    def module_graph(self, module: str) -> dict:
        """A record module (alerts, incidents…) at the centre; the playbooks triggered by it or acting on
        its records; the child playbooks they call; and the connectors all of them use."""
        direct = self.graph.read(
            """
            MATCH (m:Module {name: $module})
            MATCH (p:Playbook)-[r:TRIGGERED_ON|ACTS_ON]->(m) WHERE NOT coalesce(p.placeholder, false)
            RETURN p.uuid AS uuid, p.name AS name, p.collection AS collection, p.trigger_type AS trigger_type,
                   collect(DISTINCT CASE type(r) WHEN 'TRIGGERED_ON' THEN 'triggered (' + coalesce(r.type, p.trigger_type) + ')'
                                                  ELSE 'acts on: ' + reduce(s = '', o IN r.ops | s + CASE s WHEN '' THEN '' ELSE '/' END + o) END) AS relations
            """,
            module=module,
        )
        uuids = [d["uuid"] for d in direct]
        children = self.graph.read(
            "UNWIND $uuids AS id MATCH (p:Playbook {uuid: id})-[:CALLS]->(c:Playbook) "
            "RETURN p.uuid AS parent, c.uuid AS uuid, c.name AS name, coalesce(c.placeholder, false) AS placeholder",
            uuids=uuids,
        )
        everyone = list({*uuids, *(c["uuid"] for c in children)})
        uses = self.graph.read(
            """
            UNWIND $uuids AS id
            MATCH (p:Playbook {uuid: id})-[:HAS_STEP]->(:Step)-[:USES]->(:ConnectorOperation)-[:OF]->(c:Connector)
            WHERE NOT c.name IN ['cyops_utilities', 'code-snippet']
            RETURN p.uuid AS pb, c.name AS connector, coalesce(c.label, c.name) AS label, count(*) AS steps
            """,
            uuids=everyone,
        )
        mid = f"mod:{module}"
        nodes = {mid: {"id": mid, "type": "Module", "label": module, "ring": 5, "props": {"name": module}}}
        edges = []
        for d in direct:
            pid = f"pb:{d['uuid']}"
            nodes[pid] = {"id": pid, "type": "Playbook", "label": d["name"], "ring": 4,
                          "props": {"uuid": d["uuid"], "collection": d["collection"], "trigger_type": d["trigger_type"],
                                    "relation": ", ".join(d["relations"])}}
            for rel in d["relations"]:
                kind = "TRIGGERED_ON" if rel.startswith("triggered") else "ACTS_ON"
                edges.append({"id": f"{pid}>{kind}>{mid}", "source": pid, "target": mid, "type": kind, "detail": rel})
        for c in children:
            cid = f"pb:{c['uuid']}"
            nodes.setdefault(cid, {"id": cid, "type": "Playbook", "label": c["name"], "ring": 3,
                                   "props": {"uuid": c["uuid"], "relation": "child playbook", "placeholder": c["placeholder"]}})
            edges.append({"id": f"pb:{c['parent']}>c>{c['uuid']}", "source": f"pb:{c['parent']}", "target": cid, "type": "CALLS"})
        for u in uses:
            kid = f"con:{u['connector']}"
            nodes.setdefault(kid, {"id": kid, "type": "Connector", "label": u["label"], "ring": 1, "props": {"name": u["connector"]}})
            edges.append({"id": f"pb:{u['pb']}>u>{u['connector']}", "source": f"pb:{u['pb']}", "target": kid, "type": "USES"})
        triggered = sum(any(r.startswith("triggered") for r in d["relations"]) for d in direct)
        return {"nodes": list(nodes.values()), "edges": edges, "mode": f"module:{module}",
                "stats": {"playbooks": len(direct), "triggered": triggered, "acting": len(direct) - triggered,
                          "children": len({c["uuid"] for c in children}), "connectors": len({u["connector"] for u in uses})}}

    def playbook_overview(self, limit: int = 300) -> dict:
        """All playbooks with CALLS links, trigger modules and the connectors they use
        (step-level detail collapsed into playbook -> connector edges)."""
        rows = self.graph.read(
            """
            MATCH (p:Playbook) WHERE NOT coalesce(p.placeholder, false)
            WITH p LIMIT $limit
            OPTIONAL MATCH (p)-[:CALLS]->(callee:Playbook)
            OPTIONAL MATCH (p)-[:TRIGGERED_ON]->(m:Module)
            OPTIONAL MATCH (p)-[:HAS_STEP]->(:Step)-[:USES]->(:ConnectorOperation)-[:OF]->(c:Connector)
            RETURN p.uuid AS uuid, p.name AS name, p.collection AS collection, p.trigger_type AS trigger_type,
                   collect(DISTINCT {uuid: callee.uuid, name: callee.name}) AS calls,
                   collect(DISTINCT m.name) AS modules, collect(DISTINCT c.name) AS connectors
            """,
            limit=limit,
        )
        nodes, edges = {}, []

        def add(node_id, kind, label, **props):
            nodes.setdefault(node_id, {"id": node_id, "type": kind, "label": label, "props": props})

        for r in rows:
            pid = f"pb:{r['uuid']}"
            add(pid, "Playbook", r["name"], uuid=r["uuid"], collection=r["collection"], trigger_type=r["trigger_type"])
            if r["collection"]:
                add(f"col:{r['collection']}", "PlaybookCollection", r["collection"], name=r["collection"])
                edges.append({"id": f"col:{r['collection']}>{pid}", "source": f"col:{r['collection']}", "target": pid, "type": "CONTAINS"})
            for callee in r["calls"]:
                if callee["uuid"]:
                    add(f"pb:{callee['uuid']}", "Playbook", callee["name"], uuid=callee["uuid"])
                    edges.append({"id": f"{pid}>c>{callee['uuid']}", "source": pid, "target": f"pb:{callee['uuid']}", "type": "CALLS"})
            for m in r["modules"]:
                add(f"mod:{m}", "Module", m, name=m)
                edges.append({"id": f"{pid}>t>{m}", "source": pid, "target": f"mod:{m}", "type": "TRIGGERED_ON"})
            for c in r["connectors"]:
                add(f"con:{c}", "Connector", c, name=c)
                edges.append({"id": f"{pid}>u>{c}", "source": pid, "target": f"con:{c}", "type": "USES"})
        return {"nodes": list(nodes.values()), "edges": edges}


@lru_cache
def get_services() -> Services:
    return Services()
