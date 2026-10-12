"""Load the public fortinet-fortisoar GitHub content (fetched by scripts/fetch_fortinet.py).

Repo layouts handled:
  * solution-pack-* / ai-*  : info.json at the root (type "solutionpack"), playbooks in Content Hub
                              layout playbooks/<collection>/<playbook>.json + collection.metadata.json,
                              docs in README.md and docs/*.md (setup, usage, contents).
  * connector-*             : <connector>/info.json with configuration + operations, sample playbooks
                              as a collection export in <connector>/playbooks/playbooks.json,
                              <connector>/release_notes.md.
  * widget-*                : info.json + README only (documentation, no playbooks).

Graph additions on top of the playbook graph:
  (:SolutionPack {repo, name, label, version, kind, ...})-[:INCLUDES]->(:PlaybookCollection)
  (:SolutionPack)-[:DEPENDS_ON]->(:SolutionPack)          from info.json dependencies
  (:SolutionPack)-[:DOCUMENTED_BY]->(:Document)           README / docs/*.md
  (:Connector {label, version, category, ...})-[:HAS_SAMPLES]->(:PlaybookCollection)
  (:Connector)-[:DOCUMENTED_BY]->(:Document)              generated from info.json + release notes
  (:ConnectorOperation {title, description, parameters})  for every operation, used or not
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from ..scrub import Scrubber
from .playbooks import Playbook, _collections, parse_playbook

GITHUB = "https://github.com/fortinet-fortisoar"


@dataclass
class Pack:
    repo: str
    kind: str                      # solution_pack | connector | ai | widget
    name: str = ""
    label: str = ""
    version: str = ""
    description: str = ""
    publisher: str = ""
    category: str = ""
    dependencies: list[str] = field(default_factory=list)
    collections: list[str] = field(default_factory=list)
    info: dict = field(default_factory=dict)


def _kind(repo: str) -> str:
    if repo.startswith("connector-"):
        return "connector"
    if repo.startswith("widget-"):
        return "widget"
    if repo.startswith("ai-"):
        return "ai"
    return "solution_pack"


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (ValueError, OSError):
        return None


def _find_info(repo_dir: Path) -> tuple[dict, Path | None]:
    candidates = [repo_dir / "info.json", *sorted(repo_dir.glob("*/info.json"))]
    for path in candidates:
        info = _read_json(path) if path.exists() else None
        if isinstance(info, dict) and (info.get("name") or info.get("label")):
            return info, path
    return {}, None


def _text(value) -> str:
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    return re.sub(r"<[^>]+>", " ", str(value or "")).strip()


def _clean_markdown(text: str) -> str:
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)          # images
    text = re.sub(r"<img[^>]*>", "", text, flags=re.I)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def load_repo(repo_dir: Path, scrubber: Scrubber) -> tuple[Pack, list[Playbook], list[dict]]:
    repo = repo_dir.name
    info, info_path = _find_info(repo_dir)
    pack = Pack(
        repo=repo, kind=_kind(repo), info=info,
        name=str(info.get("name") or repo), label=_text(info.get("label")) or repo,
        version=str(info.get("version") or ""), description=_text(info.get("description")),
        publisher=_text(info.get("publisher")), category=_text(info.get("category")),
        dependencies=[str(d.get("name")) for d in info.get("dependencies") or [] if isinstance(d, dict) and d.get("name")],
    )
    playbooks = _load_playbooks(repo_dir, pack, scrubber)
    docs = _repo_docs(repo_dir, pack, info_path)
    return pack, playbooks, docs


def _collection_name(pack: Pack, name: str) -> str:
    # solution packs reuse generic collection names ("03 - Triage"), so qualify them with the pack
    if pack.kind in ("solution_pack", "ai") and pack.label.lower() not in name.lower():
        return f"{pack.label} · {name}"
    return name


def _load_playbooks(repo_dir: Path, pack: Pack, scrubber: Scrubber) -> list[Playbook]:
    out: list[Playbook] = []
    seen_files: set[Path] = set()

    # Content Hub layout: one playbook per file, collection metadata per folder
    for meta_path in sorted(repo_dir.rglob("collection.metadata.json")):
        meta = _read_json(meta_path) or {}
        coll = _collection_name(pack, str(meta.get("name") or meta_path.parent.name))
        pack.collections.append(coll)
        for file in sorted(meta_path.parent.glob("*.json")):
            seen_files.add(file)
            if file.name == "collection.metadata.json":
                continue
            wf = _read_json(file)
            if isinstance(wf, dict) and isinstance(wf.get("steps"), list):
                out.append(_tag(parse_playbook(wf, coll, scrubber), pack))

    # Collection exports (connector sample playbooks, older packs)
    for file in sorted(repo_dir.rglob("playbooks/*.json")):
        if file in seen_files:
            continue
        doc = _read_json(file)
        if doc is None:
            continue
        for coll in _collections(doc, file.stem):
            name = _collection_name(pack, scrubber.text(coll.get("name") or file.stem))
            if name not in pack.collections:
                pack.collections.append(name)
            for wf in coll["workflows"]:
                if isinstance(wf, dict):
                    out.append(_tag(parse_playbook(wf, name, scrubber), pack))
    return out


def _tag(pb: Playbook, pack: Pack) -> Playbook:
    pb.pack, pb.pack_type, pb.pack_repo = pack.label, pack.kind, pack.repo
    return pb


# ------------------------------------------------------------------ documents

def _repo_docs(repo_dir: Path, pack: Pack, info_path: Path | None) -> list[dict]:
    docs = []
    url = f"{GITHUB}/{pack.repo}"
    base_props = {"repo": pack.repo, "url": url, "pack": pack.label, "pack_kind": pack.kind}

    if pack.kind == "connector" and pack.info.get("operations") is not None:
        text = connector_markdown(pack.info, repo_dir, info_path)
        docs.append({"id": f"vendor:connector/{pack.name}", "title": f"{pack.label} connector", "text": text,
                     "source": "vendor", "version": pack.version, "path": f"{pack.repo}/info.json",
                     "props": {**base_props, "kind": "connector", "connector": pack.name,
                               "help_url": pack.info.get("help_online") or ""}})

    for md in sorted([*repo_dir.glob("README.md"), *repo_dir.glob("docs/*.md"), *repo_dir.glob("*/docs/*.md")]):
        text = _clean_markdown(md.read_text(encoding="utf-8", errors="replace"))
        if len(text) < 80:
            continue
        rel = str(md.relative_to(repo_dir))
        heading = re.search(r"^\s*#+\s+(.+)$", text, re.M)
        part = md.stem.replace("_", " ").replace("-", " ")
        title = f"{pack.label}: {part.title() if md.name != 'README.md' else (heading.group(1).strip() if heading else 'Overview')}"
        docs.append({"id": f"vendor:{pack.kind}/{pack.repo}/{rel}", "title": title[:200], "text": text,
                     "source": "vendor", "version": pack.version, "path": f"{pack.repo}/{rel}",
                     "props": {**base_props, "kind": "connector_readme" if pack.kind == "connector" else pack.kind}})
    return docs


def connector_markdown(info: dict, repo_dir: Path, info_path: Path | None) -> str:
    """A readable connector reference generated from info.json (+ release notes)."""
    label, name = _text(info.get("label")) or info.get("name"), info.get("name")
    lines = [f"# {label} connector", "",
             f"Connector name `{name}`, version {info.get('version', '?')}, publisher {_text(info.get('publisher')) or '?'}, "
             f"category {_text(info.get('category')) or '?'}.", "", _text(info.get("description")), ""]
    fields = (info.get("configuration") or {}).get("fields") or []
    if fields:
        lines += ["## Configuration", ""]
        for f in fields:
            desc = _text(f.get("description") or f.get("tooltip"))
            lines.append(f"- **{_text(f.get('title')) or f.get('name')}**"
                         + (" (required)" if f.get("required") else "") + (f": {desc}" if desc else ""))
        lines.append("")
    ops = [o for o in info.get("operations") or [] if isinstance(o, dict) and o.get("enabled", True) is not False]
    if ops:
        lines += ["## Actions", ""]
        for op in ops:
            params = [_text(p.get("title")) or p.get("name") for p in op.get("parameters") or [] if isinstance(p, dict)]
            lines += [f"### {_text(op.get('title')) or op.get('operation')} (`{op.get('operation')}`)",
                      _text(op.get("description")),
                      (f"Parameters: {', '.join(p for p in params if p)}." if params else ""), ""]
    notes_dir = info_path.parent if info_path else repo_dir
    notes = notes_dir / "release_notes.md"
    if notes.exists():
        lines += ["## Release notes", "", _clean_markdown(notes.read_text(encoding="utf-8", errors="replace"))[:4000], ""]
    if info.get("help_online"):
        lines += [f"Online documentation: {info['help_online']}"]
    return "\n".join(l for l in lines if l is not None).strip()


# ------------------------------------------------------------------ graph writes

WRITE_PACKS = """
UNWIND $packs AS p
MERGE (sp:SolutionPack {repo: p.repo})
SET sp.ingested_at = toString(datetime()), sp.name = p.name, sp.label = p.label, sp.version = p.version, sp.kind = p.kind,
    sp.description = p.description, sp.publisher = p.publisher, sp.category = p.category, sp.url = p.url
WITH sp, p
UNWIND p.collections AS coll
MATCH (c:PlaybookCollection {name: coll})
MERGE (sp)-[:INCLUDES]->(c)
"""

WRITE_DEPENDENCIES = """
UNWIND $deps AS d
MATCH (a:SolutionPack {repo: d.repo}), (b:SolutionPack {name: d.depends_on})
MERGE (a)-[:DEPENDS_ON]->(b)
"""

WRITE_CONNECTORS = """
UNWIND $connectors AS c
MERGE (con:Connector {name: c.name})
SET con.label = c.label, con.version = c.version, con.category = c.category, con.publisher = c.publisher,
    con.description = c.description, con.repo = c.repo, con.url = c.url, con.help_url = c.help_url
WITH con, c
CALL (con, c) {
  UNWIND c.operations AS o
  MERGE (op:ConnectorOperation {key: c.name + '.' + o.operation})
  SET op.name = o.operation, op.connector = c.name, op.title = o.title, op.description = o.description,
      op.category = o.category, op.parameters = o.parameters
  MERGE (op)-[:OF]->(con)
}
CALL (con, c) {
  UNWIND c.collections AS coll
  MATCH (col:PlaybookCollection {name: coll})
  MERGE (con)-[:HAS_SAMPLES]->(col)
}
"""

LINK_DOCS = """
UNWIND $links AS l
MATCH (d:Document {id: l.doc})
CALL (d, l) {
  WITH d, l WHERE l.connector IS NOT NULL
  MATCH (c:Connector {name: l.connector})
  MERGE (c)-[:DOCUMENTED_BY]->(d)
}
CALL (d, l) {
  WITH d, l WHERE l.repo IS NOT NULL
  MATCH (sp:SolutionPack {repo: l.repo})
  MERGE (sp)-[:DOCUMENTED_BY]->(d)
}
"""


def connector_row(pack: Pack) -> dict:
    info = pack.info
    ops = []
    for o in info.get("operations") or []:
        if not isinstance(o, dict) or not o.get("operation"):
            continue
        ops.append({"operation": str(o["operation"]), "title": _text(o.get("title")),
                    "description": _text(o.get("description"))[:1000], "category": _text(o.get("category")),
                    "parameters": [(_text(p.get("title")) or str(p.get("name") or "")) for p in o.get("parameters") or [] if isinstance(p, dict)][:40]})
    return {"name": pack.name, "label": pack.label, "version": pack.version, "category": pack.category,
            "publisher": pack.publisher, "description": pack.description[:2000], "repo": pack.repo,
            "url": f"{GITHUB}/{pack.repo}", "help_url": info.get("help_online") or "",
            "operations": ops, "collections": pack.collections}


def link_repo_docs(docs: list[dict], graph) -> int:
    """Connector docs -> Connector, pack/widget docs -> SolutionPack (run after the documents are written)."""
    links = [{"doc": d["id"], "connector": d["props"].get("connector"),
              "repo": d["props"]["repo"] if d["props"].get("pack_kind") != "connector" else None} for d in docs]
    for i in range(0, len(links), 500):
        graph.run(LINK_DOCS, links=links[i : i + 500])
    return len(links)


def write_catalog(packs: list[Pack], graph) -> dict:
    connectors = [connector_row(p) for p in packs if p.kind == "connector" and p.info.get("operations") is not None]
    others = [p for p in packs if p.kind != "connector"]
    for i in range(0, len(connectors), 50):
        graph.run(WRITE_CONNECTORS, connectors=connectors[i : i + 50])
    graph.run(WRITE_PACKS, packs=[{"repo": p.repo, "name": p.name, "label": p.label, "version": p.version, "kind": p.kind,
                                   "description": p.description[:2000], "publisher": p.publisher, "category": p.category,
                                   "url": f"{GITHUB}/{p.repo}", "collections": p.collections} for p in others])
    graph.run(WRITE_DEPENDENCIES, deps=[{"repo": p.repo, "depends_on": d} for p in others for d in p.dependencies])
    return {"connectors": len(connectors), "solution_packs": sum(p.kind == "solution_pack" for p in others),
            "ai_packs": sum(p.kind == "ai" for p in others), "widgets": sum(p.kind == "widget" for p in others)}


def load_all(root: Path, scrubber: Scrubber, only: set[str] | None = None) -> tuple[list[Pack], list[Playbook], list[dict]]:
    packs, playbooks, docs = [], [], []
    for repo_dir in sorted(d for d in root.iterdir() if d.is_dir() and not d.name.endswith(".tmp")):
        if only is not None and repo_dir.name not in only:
            continue
        pack, pbs, ds = load_repo(repo_dir, scrubber)
        packs.append(pack)
        playbooks += pbs
        docs += ds
    # Prefer a solution pack's copy of a playbook over a connector's sample copy of the same uuid.
    rank = {"solution_pack": 0, "ai": 1, "connector": 2, "widget": 3}
    playbooks.sort(key=lambda pb: rank.get(pb.pack_type, 9))
    return packs, playbooks, docs
