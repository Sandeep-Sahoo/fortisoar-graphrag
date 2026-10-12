"""Vendor + internal document ingestion, and internal-doc -> playbook linking."""
from __future__ import annotations

import re
from pathlib import Path

from ..providers import parse_json
from ..scrub import Scrubber

DOC_SUFFIXES = {".md", ".markdown", ".txt", ".html", ".htm", ".pdf", ".rst"}

# FortiSOAR concepts that vendor/internal chunks can EXPLAIN. Extend freely.
CONCEPTS = [
    "Playbook", "Playbook Collection", "Connector", "Module", "Jinja", "Manual Input", "Approval",
    "Reference a Playbook", "Code Snippet", "Data Ingestion", "Solution Pack", "Widget", "Queue Management",
    "SLA", "Rules Engine", "Record Set", "Dynamic Variables", "Set Variable", "Decision Step",
    "Find Record", "Create Record", "Update Record", "Wait", "Utilities", "Content Hub", "Agent",
    "Playbook Debugging", "Execution History", "Picklist", "Role", "Team", "Appliance",
]


# ------------------------------------------------------------------ reading / chunking

def read_text(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        from pypdf import PdfReader

        return "\n\n".join(page.extract_text() or "" for page in PdfReader(path).pages)
    raw = path.read_text(encoding="utf-8", errors="ignore")
    if suffix in (".html", ".htm"):
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(raw, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
        return soup.get_text("\n")
    return raw


def title_of(text: str, path: Path) -> str:
    match = re.search(r"^\s*#\s+(.+)$", text, re.M)
    return (match.group(1) if match else path.stem.replace("_", " ").replace("-", " ")).strip()[:200]


def chunk_text(text: str, size: int = 1200, overlap: int = 200) -> list[str]:
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    pieces: list[str] = []
    for p in paragraphs:  # hard-split oversized paragraphs
        pieces.extend(p[i : i + size] for i in range(0, len(p), size))
    chunks, current = [], ""
    for piece in pieces:
        if current and len(current) + len(piece) + 2 > size:
            chunks.append(current)
            current = current[-overlap:] + "\n\n" + piece
        else:
            current = f"{current}\n\n{piece}" if current else piece
    if current:
        chunks.append(current)
    return chunks


def _name_pattern(name: str) -> re.Pattern:
    # connector API names use hyphens/underscores; docs usually use spaces
    words = re.split(r"[-_\s]+", name.strip())
    return re.compile(r"\b" + r"[-_\s]+".join(map(re.escape, words)) + r"\b", re.I)


# ------------------------------------------------------------------ ingestion

UPSERT_DOCS = """
UNWIND $docs AS doc
MERGE (d:Document {id: doc.id})
SET d.title = doc.title, d.path = doc.path, d.source = doc.source, d.version = doc.version,
    d.ingested_at = toString(datetime())
SET d += doc.props
WITH d
OPTIONAL MATCH (d)-[:HAS_CHUNK]->(old:Chunk)
DETACH DELETE old
"""

CREATE_CHUNKS = """
UNWIND $chunks AS c
MATCH (d:Document {id: c.doc_id})
CREATE (ch:Chunk {id: c.id, text: c.text, idx: c.idx, source: c.source, version: c.version,
                  doc_id: c.doc_id, doc_title: c.title, priority: c.priority})
SET ch.embedding = c.embedding
CREATE (d)-[:HAS_CHUNK]->(ch)
WITH ch, c
UNWIND c.connectors AS con_name
MATCH (con:Connector {name: con_name})
MERGE (ch)-[:EXPLAINS]->(con)
"""

LINK_CONCEPTS = """
UNWIND $links AS l
MATCH (ch:Chunk {id: l.chunk})
MERGE (k:Concept {name: l.concept})
MERGE (ch)-[:EXPLAINS]->(k)
"""

DOC_BATCH = 40  # documents per write; their chunks are embedded together


def write_documents(docs: list[dict], graph, embedder, progress=print, priority=None) -> dict:
    """docs: [{id, title, text, source, version?, path?, props?: {...}}] -> Document + Chunk nodes.

    All chunks are stored (and keyword-searchable). Embeddings are requested in `priority` order
    (priority(doc, chunk_index) -> float, higher first), so when the embedder has a budget the most
    useful chunks get vectors and the rest stay pending for `embed-pending`.
    Chunks are linked to the Connectors (by name) and Concepts they mention.
    """
    connectors = [r["name"] for r in graph.read("MATCH (c:Connector) RETURN c.name AS name") if len(r["name"]) >= 4]
    connector_patterns = [(n, _name_pattern(n)) for n in connectors]
    concept_patterns = [(n, _name_pattern(n)) for n in CONCEPTS]
    docs = [d for d in docs if (d.get("text") or "").strip()]
    rows, concept_links = [], []
    for doc in docs:
        for i, chunk in enumerate(chunk_text(doc["text"])):
            chunk_id = f"{doc['id']}#{i}"
            rows.append({"id": chunk_id, "idx": i, "text": chunk, "doc_id": doc["id"], "title": doc["title"],
                         "source": doc["source"], "version": doc.get("version"),
                         "priority": float(priority(doc, i)) if priority else 0.0,
                         "connectors": [n for n, pat in connector_patterns if pat.search(chunk)]})
            concept_links += [{"chunk": chunk_id, "concept": n} for n, pat in concept_patterns if pat.search(chunk)]

    order = sorted(range(len(rows)), key=lambda i: -rows[i]["priority"]) if priority else list(range(len(rows)))
    for start in range(0, len(order), 100):
        batch = order[start : start + 100]
        vectors = embedder.embed([f"{rows[i]['title']}\n\n{rows[i]['text']}" for i in batch])
        for i, vector in zip(batch, vectors):
            rows[i]["embedding"] = vector
        if len(order) > 100:
            progress(f"  chunks embedded/queued {min(start + 100, len(order))}/{len(order)}")

    by_doc: dict[str, list[dict]] = {}
    for row in rows:
        by_doc.setdefault(row["doc_id"], []).append(row)
    for start in range(0, len(docs), DOC_BATCH):
        batch = docs[start : start + DOC_BATCH]
        graph.run(UPSERT_DOCS, docs=[{"id": d["id"], "title": d["title"], "path": d.get("path"), "source": d["source"],
                                      "version": d.get("version"), "props": d.get("props") or {}} for d in batch])
        graph.run(CREATE_CHUNKS, chunks=[r for d in batch for r in by_doc.get(d["id"], [])])
    for start in range(0, len(concept_links), 2000):
        graph.run(LINK_CONCEPTS, links=concept_links[start : start + 2000])
    embedded = sum(1 for r in rows if r.get("embedding") is not None)
    return {"documents": len(docs), "chunks": len(rows), "embedded": embedded, "pending": len(rows) - embedded}


def embed_pending(graph, embedder, limit: int, what: str = "all", repos: list[str] | None = None) -> dict:
    """Give vectors to stored playbooks / chunks that don't have one yet (highest priority first),
    optionally only those loaded from the given GitHub repos."""
    from .playbooks import EMBED_CHARS

    done = {"playbooks": 0, "chunks": 0}
    remaining = limit
    if what in ("all", "playbooks") and remaining > 0:
        rows = graph.read(
            "MATCH (p:Playbook) WHERE p.embedding IS NULL AND NOT coalesce(p.placeholder, false) AND p.summary IS NOT NULL "
            "AND ($repos IS NULL OR p.repo IN $repos) "
            "RETURN p.uuid AS uuid, p.summary AS text "
            "ORDER BY CASE p.pack_type WHEN 'solution_pack' THEN 0 WHEN 'ai' THEN 1 ELSE 2 END, p.name LIMIT $limit",
            limit=remaining, repos=repos)
        for start in range(0, len(rows), 100):
            batch = rows[start : start + 100]
            vectors = embedder.embed([r["text"][:EMBED_CHARS] for r in batch])
            pairs = [{"uuid": r["uuid"], "embedding": v} for r, v in zip(batch, vectors) if v is not None]
            graph.run("UNWIND $rows AS r MATCH (p:Playbook {uuid: r.uuid}) SET p.embedding = r.embedding", rows=pairs)
            done["playbooks"] += len(pairs)
        remaining -= done["playbooks"]
    if what in ("all", "chunks") and remaining > 0:
        rows = graph.read(
            "MATCH (d:Document)-[:HAS_CHUNK]->(c:Chunk) WHERE c.embedding IS NULL AND ($repos IS NULL OR d.repo IN $repos) "
            "RETURN c.id AS id, c.doc_title AS title, c.text AS text ORDER BY coalesce(c.priority, 0) DESC, c.idx LIMIT $limit",
            limit=remaining, repos=repos)
        for start in range(0, len(rows), 100):
            batch = rows[start : start + 100]
            vectors = embedder.embed([f"{r['title']}\n\n{r['text']}" for r in batch])
            pairs = [{"id": r["id"], "embedding": v} for r, v in zip(batch, vectors) if v is not None]
            graph.run("UNWIND $rows AS r MATCH (c:Chunk {id: r.id}) SET c.embedding = r.embedding", rows=pairs)
            done["chunks"] += len(pairs)
    left = graph.read(
        "CALL { MATCH (p:Playbook) WHERE p.embedding IS NULL AND NOT coalesce(p.placeholder, false) RETURN count(p) AS pb } "
        "CALL { MATCH (c:Chunk) WHERE c.embedding IS NULL RETURN count(c) AS ch } RETURN pb, ch")[0]
    return {"embedded": done, "still_pending": {"playbooks": left["pb"], "chunks": left["ch"]}}


def ingest_docs(path: Path, source: str, version: str, graph, embedder, scrubber: Scrubber) -> dict:
    assert source in ("vendor", "internal")
    files = [path] if path.is_file() else sorted(p for p in path.rglob("*") if p.suffix.lower() in DOC_SUFFIXES)
    root = path if path.is_dir() else path.parent
    docs = []
    for file in files:
        text = read_text(file)
        if source == "internal":
            text = scrubber.text(text)  # internal docs can contain IPs, tenant names, creds
        rel = str(file.relative_to(root))
        docs.append({"id": f"{source}:{rel}", "title": title_of(text, file), "text": text, "source": source,
                     "version": version, "path": rel})
    result = write_documents(docs, graph, embedder)
    return {"documents": result["documents"], "chunks": result["chunks"]}


# ------------------------------------------------------------------ doc -> playbook linking

LINK_SYSTEM = (
    "You map internal SOC documentation to FortiSOAR playbooks. Only link a playbook when the document "
    "clearly describes, operates, or troubleshoots that specific playbook. Return JSON: "
    '{"links": [{"uuid": "...", "confidence": 0.0-1.0, "reason": "short"}]}. Return an empty list if none fit.'
)

MERGE_LINK = """
MATCH (p:Playbook {uuid: $uuid}), (d:Document {id: $doc_id})
MERGE (p)-[r:DOCUMENTED_BY]->(d)
ON CREATE SET r.method = $method, r.status = $status, r.confidence = $confidence, r.reason = $reason
"""


# Embedding fallback: scores from the cosine vector index are tightly bunched (~0.86-0.94 with
# Gemini embeddings), so require both a floor and a clear lead over the next candidate.
EMBED_FLOOR = 0.90
EMBED_TIE = 0.005   # candidates this close to the top score are proposed together
EMBED_LEAD = 0.012  # ...and the group must lead the next candidate by at least this much

CANDIDATES = """
MATCH (c:Chunk {doc_id: $doc_id})
CALL db.index.vector.queryNodes('playbook_embedding', 8, c.embedding) YIELD node, score
WHERE NOT coalesce(node.placeholder, false)
WITH node, max(score) AS score
RETURN node.uuid AS uuid, node.name AS name, node.summary AS summary, score
ORDER BY score DESC LIMIT 8
"""


def embedding_matches(candidates: list[dict]) -> list[dict]:
    """Pick the clear winner(s) among similarity candidates, or nothing if it's ambiguous."""
    if not candidates or candidates[0]["score"] < EMBED_FLOOR:
        return []
    top = candidates[0]["score"]
    group = [c for c in candidates if c["score"] >= max(EMBED_FLOOR, top - EMBED_TIE)]
    rest = candidates[len(group):]
    if rest and top - rest[0]["score"] < EMBED_LEAD:
        return []
    return group


def _is_quota_error(message: str) -> bool:
    return "429" in message or "RESOURCE_EXHAUSTED" in message or "quota" in message.lower()


def link_internal_docs(graph, llm, method: str = "auto") -> dict:
    """Link internal docs to playbooks.

    1. Name / UUID appears in the document -> approved.
    2. Otherwise, for each doc: `llm` asks the LLM to choose among similar playbooks; `embedding`
       uses similarity alone; `auto` uses the LLM and falls back to embeddings if it fails
       (e.g. quota). Both create status=proposed links that need review (`links approve`).
    """
    assert method in ("auto", "llm", "embedding", "none")
    playbooks = graph.read(
        "MATCH (p:Playbook) WHERE NOT coalesce(p.placeholder, false) RETURN p.uuid AS uuid, p.name AS name"
    )
    patterns = [(p["uuid"], _name_pattern(p["name"])) for p in playbooks if len(p["name"]) >= 6]
    docs = graph.read(
        "MATCH (d:Document {source: 'internal'})-[:HAS_CHUNK]->(c:Chunk) WITH d, c ORDER BY c.idx "
        "RETURN d.id AS id, d.title AS title, collect(c.text) AS texts"
    )
    counts = {"name_match": 0, "llm_proposed": 0, "embedding_proposed": 0, "unlinked": []}
    llm_available = method in ("auto", "llm")
    for doc in docs:
        text = "\n\n".join(doc["texts"])
        matched = {uuid for uuid, pat in patterns if pat.search(text)}
        matched |= {p["uuid"] for p in playbooks if p["uuid"] in text}
        for uuid in matched:
            graph.run(MERGE_LINK, uuid=uuid, doc_id=doc["id"], method="name_match", status="approved",
                      confidence=1.0, reason="playbook name or UUID appears in document")
        counts["name_match"] += len(matched)
        if matched or method == "none":
            continue

        candidates = graph.read(CANDIDATES, doc_id=doc["id"])
        if not candidates:
            continue

        if llm_available:
            listing = "\n".join(f"- uuid={c['uuid']} | {c['name']} | {c['summary'][:400]}" for c in candidates)
            prompt = f"DOCUMENT '{doc['title']}':\n{text[:6000]}\n\nCANDIDATE PLAYBOOKS:\n{listing}"
            try:
                reply = parse_json(llm.complete(LINK_SYSTEM, prompt, json_mode=True))
                valid = {c["uuid"] for c in candidates}
                links = [l for l in (reply.get("links", []) if isinstance(reply, dict) else [])
                         if l.get("uuid") in valid and float(l.get("confidence", 0)) >= 0.5]
                for link in links:
                    graph.run(MERGE_LINK, uuid=link["uuid"], doc_id=doc["id"], method="llm", status="proposed",
                              confidence=float(link["confidence"]), reason=str(link.get("reason", ""))[:300])
                counts["llm_proposed"] += len(links)
                if not links:
                    counts["unlinked"].append(doc["title"])
                continue
            except Exception as exc:
                message = str(exc).splitlines()[0][:160]
                if method == "llm" and not _is_quota_error(message):
                    print(f"  ! LLM linking failed for {doc['id']}: {message}")
                    continue
                if _is_quota_error(message):
                    llm_available = False  # don't retry for every remaining doc
                    print("  ! LLM quota exhausted; " + ("using embedding similarity for the remaining documents."
                                                          if method == "auto" else "skipping LLM linking."))
                    counts["llm_skipped_quota"] = True
                if method == "llm":
                    continue

        chosen = embedding_matches(candidates)
        for c in chosen:
            graph.run(MERGE_LINK, uuid=c["uuid"], doc_id=doc["id"], method="embedding", status="proposed",
                      confidence=round(c["score"], 3), reason="clear best match by embedding similarity")
        counts["embedding_proposed"] += len(chosen)
        if not chosen:
            counts["unlinked"].append(doc["title"])
    return counts
