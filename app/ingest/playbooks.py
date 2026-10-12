"""Deterministic FortiSOAR playbook parser (no LLM) and graph loader.

Accepts the JSON produced by FortiSOAR's playbook collection export:
  {"type": "workflow_collections", "data": [{"name": ..., "workflows": [{... "steps": [...], "routes": [...]}]}]}
as well as a single collection object, a list of collections, or a single workflow.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

from ..scrub import Scrubber

UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


def iri_tail(value) -> str | None:
    """'/api/3/workflow_steps/<uuid>' -> '<uuid>'."""
    if isinstance(value, dict):
        value = value.get("@id") or value.get("uuid")
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip().rstrip("/").rsplit("/", 1)[-1]


@dataclass
class Step:
    key: str
    uuid: str
    name: str
    kind: str
    description: str = ""
    connector: str | None = None
    operation: str | None = None
    connector_version: str | None = None
    calls_uuid: str | None = None
    args_text: str = ""
    has_secret: bool = False  # credentials were hard-coded in the step and redacted at ingestion
    order: int = 0


@dataclass
class Playbook:
    uuid: str
    name: str
    collection: str
    description: str = ""
    tags: list[str] = field(default_factory=list)
    active: bool | None = None
    trigger_type: str = "unknown"
    modules: list[str] = field(default_factory=list)
    steps: list[Step] = field(default_factory=list)
    routes: list[dict] = field(default_factory=list)
    calls: list[dict] = field(default_factory=list)
    pack: str = ""        # solution pack / connector label the playbook ships in (empty for own exports)
    pack_type: str = ""   # solution_pack | connector | ai | widget
    pack_repo: str = ""   # GitHub repo the playbook was loaded from

    def summary(self, names: dict[str, str]) -> str:
        """Text used for the playbook embedding / fulltext entry point."""
        lines = [f"Playbook '{self.name}' in collection '{self.collection}'"
                 + (f" ({self.pack_type.replace('_', ' ')} {self.pack})." if self.pack else ".")]
        if self.description:
            lines.append(self.description)
        trigger = self.trigger_type + (f" on {', '.join(self.modules)}" if self.modules else "")
        lines.append(f"Trigger: {trigger}.")
        if self.tags:
            lines.append(f"Tags: {', '.join(self.tags)}.")
        step_bits = []
        for s in self.steps:
            bit = s.name
            if s.connector:
                bit += f" [connector {s.connector}.{s.operation}]"
            elif s.calls_uuid:
                bit += f" [calls playbook {names.get(s.calls_uuid, s.calls_uuid)}]"
            elif s.kind not in ("action", "trigger"):
                bit += f" [{s.kind}]"
            step_bits.append(bit)
        if step_bits:
            lines.append("Steps: " + "; ".join(step_bits) + ".")
        secret_steps = [s.name for s in self.steps if s.has_secret]
        if secret_steps:
            lines.append("Security finding: hard-coded credentials or secrets (redacted at ingestion) "
                         f"in steps: {', '.join(secret_steps)}.")
        return " ".join(lines)


# ------------------------------------------------------------------ loading

def _collections(doc, fallback_name: str):
    items = doc if isinstance(doc, list) else doc.get("data") if isinstance(doc, dict) and isinstance(doc.get("data"), list) else [doc]
    for item in items:
        if not isinstance(item, dict):
            continue
        if isinstance(item.get("workflows"), list):
            yield item
        elif isinstance(item.get("steps"), list):  # a bare workflow export
            yield {"name": fallback_name, "workflows": [item]}


def load_playbooks(path: Path, scrubber: Scrubber) -> list[Playbook]:
    files = [path] if path.is_file() else sorted(path.rglob("*.json"))
    playbooks: list[Playbook] = []
    for file in files:
        doc = json.loads(file.read_text(encoding="utf-8"))
        for coll in _collections(doc, file.stem):
            coll_name = scrubber.text(coll.get("name") or file.stem)
            for wf in coll["workflows"]:
                if isinstance(wf, dict):
                    playbooks.append(parse_playbook(wf, coll_name, scrubber))
    return playbooks


# ------------------------------------------------------------------ parsing

def _step_type_name(step: dict) -> str | None:
    st = step.get("stepType")
    return st.get("name") if isinstance(st, dict) else None


# Step types in exports are opaque IRIs (/api/3/workflow_step_types/<uuid>), so the kind is
# inferred from the step's arguments. A stepType {"name": ...} (older exports, tests) wins if present.
NAMED_KINDS = {"decision": "decision", "manual input": "manual_input", "approval": "manual_input",
               "create record": "create_record", "update record": "update_record", "find records": "find_records",
               "find record": "find_records", "set variable": "set_variable", "wait": "wait", "code snippet": "code_snippet"}
CONTROL_KEYS = {"step_variables", "when", "for_each", "mock_result", "do_until", "ignore_errors", "_showJson",
                "message", "retry", "pause"}


# Step type IRIs whose arguments are free-form, so they can't be recognised by argument keys.
STEP_TYPE_IDS = {"04d0cf46-b6a8-42c4-8683-60a7eaa69e8f": "set_variable"}


def _classify(step: dict, is_trigger: bool) -> str:
    args = step.get("arguments") or {}
    if is_trigger:
        return "trigger"
    type_name = _step_type_name(step)
    if type_name:
        return NAMED_KINDS.get(type_name.strip().lower(), re.sub(r"\W+", "_", type_name.strip().lower()))
    if not args.get("connector") and not args.get("workflowReference") and "conditions" not in args:
        known = STEP_TYPE_IDS.get(iri_tail(step.get("stepType")) or "")
        if known:
            return known
    connector = args.get("connector")
    if connector:
        return {"code-snippet": "code_snippet", "cyops_utilities": "utility"}.get(str(connector), "connector")
    if args.get("workflowReference"):
        return "reference"
    if "conditions" in args:
        return "decision"
    if args.get("is_approval") or args.get("type") == "InputBased" or "inputVariables" in args or "response_mapping" in args:
        return "manual_input"
    if "delay" in args:
        return "wait"
    if "query" in args and ("module" in args or "resource" in args):
        return "find_records"
    if "resource" in args and ("operation" in args or "collection" in args):
        collection = str(args.get("collection") or "")
        # creating targets a module IRI (/api/3/alerts); updating targets a record ({{...}} or /api/3/alerts/<id>)
        return "create_record" if re.fullmatch(r"/api/3/[\w-]+", collection) else "update_record"
    if args and set(args) <= CONTROL_KEYS:
        return "set_variable"
    return "action"


def _trigger(args: dict, type_name: str | None) -> tuple[str, list[str]]:
    """Heuristic trigger classification from the start step's arguments."""
    resources = args.get("resources") or ([args["resource"]] if args.get("resource") else [])
    if isinstance(resources, str):
        resources = [resources]
    modules = sorted({iri_tail(r) or r for r in resources if r})
    if "authentication_methods" in args:
        return "api_endpoint", modules
    if args.get("route") or "executeButtonText" in args:
        return "manual", modules
    if "fieldbasedtrigger" in args or args.get("resource"):
        return "record_event", modules
    if type_name and "refer" in type_name.lower():
        return "referenced", modules
    return ("referenced" if not modules else "unknown"), modules


def parse_playbook(wf: dict, collection: str, scrubber: Scrubber) -> Playbook:
    name = wf.get("name") or "unnamed"
    uuid = wf.get("uuid") or iri_tail(wf.get("@id")) or hashlib.sha1(f"{collection}/{name}".encode()).hexdigest()
    trigger_uuid = iri_tail(wf.get("triggerStep"))

    pb = Playbook(
        uuid=uuid,
        name=scrubber.text(name),
        collection=collection,
        description=scrubber.text(wf.get("description") or ""),
        tags=[scrubber.text(iri_tail(t) or str(t)) for t in (wf.get("recordTags") or wf.get("tags") or [])],
        active=wf.get("isActive"),
    )

    by_uuid: dict[str, Step] = {}
    for i, raw in enumerate(wf.get("steps") or []):
        if not isinstance(raw, dict):
            continue
        step_uuid = raw.get("uuid") or iri_tail(raw.get("@id")) or f"idx{i}"
        args = raw.get("arguments") or {}
        is_trigger = step_uuid == trigger_uuid
        step = Step(
            key=f"{uuid}:{step_uuid}",
            uuid=step_uuid,
            name=scrubber.text(raw.get("name") or f"step {i}"),
            kind=_classify(raw, is_trigger),
            description=scrubber.text(raw.get("description") or ""),
            args_text=json.dumps(scrubber.obj(args), sort_keys=True, default=str)[:1500],
        )
        # credential-type keys, credentials in URLs and bearer tokens count; long opaque strings are
        # usually hashes / fingerprints / IDs, so they're masked but not reported as secrets
        step.has_secret = any(marker in step.args_text for marker in ("<REDACTED>", "<CREDS>"))
        if step.kind == "connector":
            step.connector = str(args["connector"])
            step.operation = str(args.get("operation") or "unknown")
            step.connector_version = args.get("version")
        elif step.kind == "reference":
            ref = str(args["workflowReference"])
            match = UUID_RE.search(ref)
            if match and "{{" not in ref:  # Jinja-templated references are resolved at runtime
                step.calls_uuid = match.group(0)
                pb.calls.append({"uuid": step.calls_uuid, "step": step.key})
        if is_trigger:
            pb.trigger_type, pb.modules = _trigger(args, _step_type_name(raw))
        by_uuid[step_uuid] = step

    for route in wf.get("routes") or []:
        src, dst = iri_tail(route.get("sourceStep")), iri_tail(route.get("targetStep"))
        if src in by_uuid and dst in by_uuid:
            # route "name" is just "Source -> Target"; only "label" carries a branch outcome
            pb.routes.append({"src": by_uuid[src].key, "dst": by_uuid[dst].key,
                              "label": scrubber.text(route.get("label") or "")})

    # Order steps by BFS from the trigger so summaries read in execution order.
    adjacency: dict[str, list[str]] = {}
    for r in pb.routes:
        adjacency.setdefault(r["src"], []).append(r["dst"])
    keys = {s.key: s for s in by_uuid.values()}
    start = by_uuid[trigger_uuid].key if trigger_uuid in by_uuid else None
    seen, queue = [], deque([start] if start else [])
    while queue:
        key = queue.popleft()
        if key in seen:
            continue
        seen.append(key)
        queue.extend(adjacency.get(key, []))
    ordered = seen + [k for k in keys if k not in seen]
    for order, key in enumerate(ordered):
        keys[key].order = order
    pb.steps = sorted(keys.values(), key=lambda s: s.order)
    return pb


# ------------------------------------------------------------------ graph load

# Batched writes: one round trip per BATCH playbooks instead of three per playbook.
BATCH = 200

UPSERT_PLAYBOOKS = """
UNWIND $pbs AS pb
MERGE (p:Playbook {uuid: pb.uuid})
// a re-ingest under an embedding cap must not drop the vector of an unchanged playbook
SET p.embedding = CASE WHEN pb.embedding IS NOT NULL THEN pb.embedding
                       WHEN p.summary = pb.summary THEN p.embedding END
SET p.name = pb.name, p.description = pb.description, p.collection = pb.collection, p.tags = pb.tags,
    p.active = pb.active, p.trigger_type = pb.trigger_type, p.summary = pb.summary,
    p.pack = pb.pack, p.pack_type = pb.pack_type, p.repo = pb.repo, p.placeholder = false, p.synced_at = $synced_at
WITH p, pb
OPTIONAL MATCH (:PlaybookCollection)-[old_c:CONTAINS]->(p)
DELETE old_c
WITH DISTINCT p, pb
OPTIONAL MATCH (p)-[r:CALLS|TRIGGERED_ON]->()
DELETE r
WITH DISTINCT p, pb
OPTIONAL MATCH (p)-[:HAS_STEP]->(old:Step)
DETACH DELETE old
WITH DISTINCT p, pb
MERGE (c:PlaybookCollection {name: pb.collection})
SET c.pack = pb.pack, c.pack_type = pb.pack_type
MERGE (c)-[:CONTAINS]->(p)
"""

CREATE_STEPS = """
UNWIND $steps AS s
MATCH (p:Playbook {uuid: s.pb})
CREATE (st:Step {key: s.key, uuid: s.uuid, name: s.name, kind: s.kind, description: s.description,
                 args: s.args_text, has_secret: s.has_secret, playbook_uuid: s.pb, order: s.order})
CREATE (p)-[:HAS_STEP {order: s.order}]->(st)
WITH st, s WHERE s.connector IS NOT NULL
MERGE (con:Connector {name: s.connector})
MERGE (op:ConnectorOperation {key: s.connector + '.' + s.operation})
  ON CREATE SET op.name = s.operation, op.connector = s.connector
MERGE (op)-[:OF]->(con)
MERGE (st)-[u:USES]->(op) SET u.connector_version = s.connector_version
"""

CREATE_ROUTES = """
UNWIND $routes AS r
MATCH (a:Step {key: r.src}), (b:Step {key: r.dst})
MERGE (a)-[n:NEXT]->(b) SET n.label = r.label
"""

CREATE_CALLS = """
UNWIND $calls AS c
MATCH (p:Playbook {uuid: c.from})
MERGE (t:Playbook {uuid: c.uuid}) ON CREATE SET t.placeholder = true, t.name = 'unresolved:' + c.uuid
MERGE (p)-[k:CALLS]->(t) SET k.via_step = c.step
"""

CREATE_MODULES = """
UNWIND $mods AS m
MATCH (p:Playbook {uuid: m.pb})
MERGE (mod:Module {name: m.module})
MERGE (p)-[t:TRIGGERED_ON]->(mod) SET t.type = m.trigger_type
"""

EMBED_CHARS = 1200  # enough for name, description, trigger and the main steps; keeps token use down


def dedupe(playbooks: list[Playbook]) -> list[Playbook]:
    """The same playbook (same uuid) can ship in several packs; keep the first occurrence."""
    seen, out = set(), []
    for pb in playbooks:
        if pb.uuid not in seen:
            seen.add(pb.uuid)
            out.append(pb)
    return out


def write_playbooks(playbooks: list[Playbook], graph, embedder, prune: bool = False, progress=print) -> dict:
    from datetime import datetime, timezone

    playbooks = dedupe(playbooks)
    if not playbooks:
        return {"playbooks": 0}
    names = {pb.uuid: pb.name for pb in playbooks}
    summaries = [pb.summary(names) for pb in playbooks]
    synced_at = datetime.now(timezone.utc).isoformat()
    for start in range(0, len(playbooks), BATCH):
        batch = playbooks[start : start + BATCH]
        texts = summaries[start : start + BATCH]
        vectors = embedder.embed([t[:EMBED_CHARS] for t in texts])
        graph.run(UPSERT_PLAYBOOKS, synced_at=synced_at, pbs=[
            {"uuid": pb.uuid, "name": pb.name, "description": pb.description, "collection": pb.collection,
             "tags": pb.tags, "active": pb.active, "trigger_type": pb.trigger_type, "summary": summary,
             "embedding": vector, "pack": pb.pack, "pack_type": pb.pack_type, "repo": pb.pack_repo or None}
            for pb, summary, vector in zip(batch, texts, vectors)])
        graph.run(CREATE_STEPS, steps=[{**s.__dict__, "pb": pb.uuid} for pb in batch for s in pb.steps])
        graph.run(CREATE_ROUTES, routes=[r for pb in batch for r in pb.routes])
        graph.run(CREATE_CALLS, calls=[{**c, "from": pb.uuid} for pb in batch for c in pb.calls])
        graph.run(CREATE_MODULES, mods=[{"pb": pb.uuid, "module": m, "trigger_type": pb.trigger_type}
                                        for pb in batch for m in pb.modules])
        if len(playbooks) > BATCH:
            progress(f"  playbooks {min(start + BATCH, len(playbooks))}/{len(playbooks)}")

    result = {"playbooks": len(playbooks), "steps": sum(len(p.steps) for p in playbooks)}
    if prune:
        # Anything not seen in this sync is no longer deployed.
        stale = ("MATCH (p:Playbook) WHERE NOT coalesce(p.placeholder, false) AND p.synced_at < $synced_at "
                 "AND coalesce(p.pack_type, '') <> 'upload' ")  # your uploads are managed on the Review page
        result["pruned"] = graph.run(stale + "RETURN count(p) AS n", synced_at=synced_at)[0]["n"]
        graph.run(stale + "OPTIONAL MATCH (p)-[:HAS_STEP]->(s:Step) DETACH DELETE s, p", synced_at=synced_at)
    graph.run("MATCH (c:PlaybookCollection) WHERE NOT (c)-[:CONTAINS]->() DETACH DELETE c")
    return result


def ingest_playbooks(path: Path, graph, embedder, scrubber: Scrubber, prune: bool = False) -> dict:
    return write_playbooks(load_playbooks(path, scrubber), graph, embedder, prune=prune)
