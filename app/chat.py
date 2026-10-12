"""Multi-session chat: sessions + messages stored in Neo4j, and the streaming answer pipeline.

Follow-up questions are rewritten into standalone questions using the recent history, so
retrieval works on "and which of those need approval?" just like on a fresh question.
"""
from __future__ import annotations

import json
import logging
import re
import time
import uuid
from typing import Iterator

log = logging.getLogger("graphrag.chat")
HISTORY_TURNS = 6  # messages of history sent to the LLM

CONDENSE_SYSTEM = (
    "Rewrite the user's latest message as one standalone question about FortiSOAR or the company's "
    "playbooks and documentation. Resolve pronouns and references (\"it\", \"those\", \"that playbook\") "
    "using the conversation. Keep names exactly as written. If the message is already standalone, "
    "return it unchanged. Return only the question."
)

HISTORY_RULE = (
    "\nA CONVERSATION SO FAR section may precede the context. Use it only to understand what the user "
    "is referring to; every fact in your answer must still come from CONTEXT and carry a citation."
)


class ChatStore:
    def __init__(self, graph):
        self.graph = graph

    def create(self, title: str = "New chat") -> dict:
        now = time.time()
        rows = self.graph.run(
            "CREATE (s:ChatSession {id: $id, title: $title, created_at: $now, updated_at: $now}) "
            "RETURN s.id AS id, s.title AS title, s.updated_at AS updated_at",
            id=str(uuid.uuid4()), title=title[:80], now=now,
        )
        return rows[0]

    def list(self) -> list[dict]:
        return self.graph.read(
            "MATCH (s:ChatSession) OPTIONAL MATCH (s)-[:HAS_MESSAGE]->(m:ChatMessage) "
            "RETURN s.id AS id, s.title AS title, s.updated_at AS updated_at, count(m) AS messages "
            "ORDER BY s.updated_at DESC LIMIT 200"
        )

    def get(self, session_id: str) -> dict | None:
        rows = self.graph.read(
            """
            MATCH (s:ChatSession {id: $id})
            OPTIONAL MATCH (s)-[:HAS_MESSAGE]->(m:ChatMessage)
            WITH s, m ORDER BY m.idx
            RETURN s.id AS id, s.title AS title,
                   collect(CASE WHEN m IS NULL THEN null ELSE
                     {id: m.id, role: m.role, content: m.content, standalone: m.standalone,
                      sources: m.sources, playbooks: m.playbooks, created_at: m.created_at} END) AS messages
            """,
            id=session_id,
        )
        if not rows:
            return None
        session = rows[0]
        for m in session["messages"]:
            m["sources"] = json.loads(m["sources"]) if m.get("sources") else []
            m["playbooks"] = json.loads(m["playbooks"]) if isinstance(m.get("playbooks"), str) else []
        return session

    def rename(self, session_id: str, title: str) -> bool:
        rows = self.graph.run("MATCH (s:ChatSession {id: $id}) SET s.title = $title RETURN count(s) AS n",
                              id=session_id, title=title[:80])
        return bool(rows and rows[0]["n"])

    def delete(self, session_id: str) -> bool:
        rows = self.graph.run(
            "MATCH (s:ChatSession {id: $id}) OPTIONAL MATCH (s)-[:HAS_MESSAGE]->(m:ChatMessage) "
            "WITH s, collect(m) AS ms FOREACH (m IN ms | DETACH DELETE m) DETACH DELETE s RETURN 1 AS n",
            id=session_id,
        )
        return bool(rows and rows[0]["n"])

    def history(self, session_id: str, limit: int = HISTORY_TURNS) -> list[dict]:
        rows = self.graph.read(
            "MATCH (:ChatSession {id: $id})-[:HAS_MESSAGE]->(m:ChatMessage) "
            "RETURN m.role AS role, m.content AS content ORDER BY m.idx DESC LIMIT $limit",
            id=session_id, limit=limit,
        )
        return list(reversed(rows))

    def add(self, session_id: str, role: str, content: str, standalone: str | None = None,
            sources: list | None = None, playbooks: list | None = None) -> str:
        message_id = str(uuid.uuid4())
        self.graph.run(
            """
            MATCH (s:ChatSession {id: $sid})
            OPTIONAL MATCH (s)-[:HAS_MESSAGE]->(prev:ChatMessage)
            WITH s, count(prev) AS idx
            CREATE (s)-[:HAS_MESSAGE]->(:ChatMessage {id: $id, role: $role, content: $content, idx: idx,
                    standalone: $standalone, sources: $sources, playbooks: $playbooks, created_at: $now})
            SET s.updated_at = $now
            """,
            sid=session_id, id=message_id, role=role, content=content, standalone=standalone,
            sources=json.dumps(sources or []), playbooks=json.dumps(playbooks or []), now=time.time(),
        )
        return message_id


CITATION = re.compile(r"\[[CDV]\d+\]")


def _format_history(history: list[dict]) -> str:
    # Strip citation labels from earlier answers: they refer to that turn's context, not this one.
    return "\n".join(f"{m['role'].upper()}: {CITATION.sub('', m['content'])[:1500]}" for m in history)


def _answer_playbooks(svc, answer: str, cited_sources: list[dict]) -> list[dict]:
    """Playbooks to diagram: cited [C#] ones first, then any other playbook the answer names
    (answers often name a playbook while citing the runbook that describes it)."""
    result = [{"uuid": s["uuid"], "name": s["name"]} for s in cited_sources if s["type"] == "playbook"]
    seen = {p["uuid"] for p in result}
    lowered = answer.lower()
    for row in svc.graph.read("MATCH (p:Playbook) WHERE NOT coalesce(p.placeholder, false) "
                              "RETURN p.uuid AS uuid, p.name AS name ORDER BY size(p.name) DESC"):
        if row["uuid"] not in seen and len(row["name"]) >= 6 and row["name"].lower() in lowered:
            result.append(row)
            seen.add(row["uuid"])
    return result[:8]


def graph_only_answer(hits: dict, sources: list[dict]) -> str:
    """Deterministic summary of what the graph found (no LLM)."""
    terms = ", ".join(f"`{t}`" for t in hits.get("terms", []))
    filters = hits.get("filters") or {}
    filt = (" · filters: " + ", ".join(f"{k} = **{v}**" for k, v in filters.items())) if filters else ""
    if not sources:
        return (f"**Graph-only mode (no AI).** Nothing matched {terms or 'your question'}{filt}. Try the exact words used in "
                "playbook, step, connector or module names (e.g. *phishing*, *virustotal*, *block IP*), or add synonyms.")
    lines = [f"**Graph-only mode (no AI).** Keyword matches for {terms or 'the filters'}{filt}, expanded through the graph. "
             "Playbooks matching more of your terms come first. No answer is written; open a playbook or source for details.", ""]
    syn = [g for g in hits.get("groups", []) if len(g) > 1]
    if syn:
        lines.append("Synonyms used: " + "; ".join(f"{g[0]} → {', '.join(g[1:4])}{'…' if len(g) > 4 else ''}" for g in syn) + ".")
        lines.append("")
    pbs = hits["playbooks"]
    if pbs:
        lines.append(f"### Playbooks ({len(pbs)})")
        for pb in pbs:
            why = pb.get("why") or [f"linked: {w}" for w in pb.get("linked_because", [])]
            matched = f" · {pb['matched']}/{pb['of']} terms" if pb.get("of") else ""
            lines.append(f"- **{pb['name']}**{matched}" + (f" — matched {'; '.join(why)}" if why else ""))
        lines.append("")
    for key, title in (("vendor", "Vendor / pack docs"), ("internal", "Internal docs")):
        if hits[key]:
            lines.append(f"### {title} ({len(hits[key])})")
            for c in hits[key]:
                snippet = re.sub(r"\s+", " ", c["text"])[:220]
                lines.append(f"- **{c['title']}**: {snippet}…")
            lines.append("")
    if hits.get("linked"):
        lines.append(f"Linked from the docs above: {', '.join(hits['linked'])}.")
    return "\n".join(lines).strip()


def chat_events(svc, session_id: str, question: str, mode: str = "ai", filters: dict | None = None) -> Iterator[dict]:
    """Run one chat turn, yielding progress events for the UI:
       stage (rewrite/search/graph/answer) -> token* -> done (or error).
       mode "graph" makes no model API calls: keyword search + graph expansion, no written answer."""
    graph_only = mode == "graph"
    store = svc.chats
    session = store.get(session_id)
    if session is None:
        yield {"type": "error", "message": "chat session not found"}
        return
    history = store.history(session_id, svc.settings.chat_history_turns) if svc.settings.chat_history_turns else []
    store.add(session_id, "user", question)
    if not session["messages"] and session["title"] == "New chat":
        store.rename(session_id, question)

    try:
        # 1. Understand: rewrite follow-ups into a standalone question.
        yield {"type": "stage", "stage": "rewrite", "status": "running"}
        standalone = question
        if history and not graph_only:
            prompt = f"CONVERSATION:\n{_format_history(history)}\n\nLATEST MESSAGE: {question}"
            standalone = svc.llm.complete(CONDENSE_SYSTEM, prompt).strip().strip('"') or question
        yield {"type": "stage", "stage": "rewrite", "status": "done",
               "detail": "skipped (graph-only)" if graph_only and history else
                         standalone if standalone != question else "standalone question", "standalone": standalone}

        # 2. Search: hybrid vector + keyword entry points.
        yield {"type": "stage", "stage": "search", "status": "running"}
        hits = svc.retriever.search_keyword(standalone, filters) if graph_only else svc.retriever.search_all(standalone)
        yield {"type": "stage", "stage": "search", "status": "done",
               "detail": ("keywords only · " if graph_only else "") + f"{len(hits['playbooks'])} playbooks, {len(hits['vendor'])} vendor, "
                         f"{len(hits['internal'])} internal",
               "playbooks": [p["name"] for p in hits["playbooks"]]}

        # 3. Graph: follow links from docs to playbooks, load steps/callers/docs.
        yield {"type": "stage", "stage": "graph", "status": "running"}
        hits = svc.retriever.expand(hits)
        context, sources = svc.retriever.assemble(hits)
        yield {"type": "stage", "stage": "graph", "status": "done",
               "detail": f"+{len(hits['linked'])} linked playbooks" if hits["linked"] else "steps, callers, docs loaded",
               "linked": hits["linked"]}

        if graph_only:
            answer = graph_only_answer(hits, sources)
            yield {"type": "token", "text": answer}
            yield {"type": "stage", "stage": "answer", "status": "done", "detail": "skipped (graph-only, no AI)"}
            cited_sources = sources
        elif not sources:
            answer = "Nothing relevant is indexed yet. Ingest playbooks and docs first."
            yield {"type": "token", "text": answer}
            cited_sources = []
        else:
            # 4. Answer: stream tokens from the LLM.
            yield {"type": "stage", "stage": "answer", "status": "running"}
            from .services import ANSWER_SYSTEM

            system = ANSWER_SYSTEM.format(company=svc.settings.company_name) + HISTORY_RULE
            prefix = f"CONVERSATION SO FAR:\n{_format_history(history)}\n\n" if history else ""
            prompt = f"{prefix}CONTEXT:\n{context}\n\nQUESTION: {standalone}"
            parts = []
            for piece in svc.llm.stream(system, prompt):
                parts.append(piece)
                yield {"type": "token", "text": piece}
            answer = "".join(parts)
            cited = set(re.findall(r"\[([CDV]\d+)\]", answer))
            cited_sources = [s for s in sources if s["label"] in cited] or sources
            yield {"type": "stage", "stage": "answer", "status": "done",
                   "detail": f"{len(cited)} sources cited"}

        playbooks = ([{"uuid": s["uuid"], "name": s["name"]} for s in sources if s["type"] == "playbook"][:8]
                     if graph_only else _answer_playbooks(svc, answer, cited_sources))
        message_id = store.add(session_id, "assistant", answer, standalone=standalone,
                               sources=cited_sources, playbooks=playbooks)
        uuids = [p["uuid"] for p in playbooks]
        graph = svc.playbook_graph(uuids, steps=True) if uuids else {"nodes": [], "edges": []}
        yield {"type": "done", "message_id": message_id, "sources": cited_sources,
               "playbooks": playbooks, "standalone": standalone, "graph": graph}
    except Exception as exc:  # surface provider/DB errors in the chat instead of a dropped stream
        log.exception("chat turn failed in session %s", session_id)
        message = friendly_error(exc)
        store.add(session_id, "assistant", f"Error: {message}")
        yield {"type": "error", "message": message}


def friendly_error(exc: Exception) -> str:
    """Short, actionable text for common provider failures instead of a raw API payload."""
    text = str(exc)
    if "429" in text or "RESOURCE_EXHAUSTED" in text or "rate limit" in text.lower():
        retry = re.search(r"retry in ([0-9hms.]+)", text)
        return ("The LLM provider's quota or rate limit is used up"
                + (f" (retry in about {re.sub(r'[.][0-9]+s$', 's', retry.group(1).rstrip('.'))})" if retry else "")
                + ". Search and graph results still work. To keep chatting: wait for the reset, enable billing, "
                  "set LLM_MODEL to a model with more quota, or switch to full-local mode.")
    if "401" in text or "API key" in text or "PERMISSION_DENIED" in text:
        return "The LLM provider rejected the API key. Check GEMINI_API_KEY / ANTHROPIC_API_KEY in .env."
    if "ConnectError" in type(exc).__name__ or "Connection refused" in text:
        return "Couldn't reach the LLM or database. Check that Neo4j (and Ollama in full-local mode) are running."
    return text.splitlines()[0][:300]
