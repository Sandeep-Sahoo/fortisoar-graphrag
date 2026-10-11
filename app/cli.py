"""Command line: python -m app.cli <command>"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .services import get_services


def _print(obj) -> None:
    print(json.dumps(obj, indent=2, default=str))


def _derive(svc) -> None:
    from .ingest.linking import derive_links

    print("derived links:", json.dumps(derive_links(svc.graph)))


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("config", help="show the resolved mode / providers")
    sub.add_parser("init-db", help="create constraints, vector and fulltext indexes")
    sub.add_parser("status", help="node counts")

    p = sub.add_parser("ingest-playbooks", help="parse playbook JSON exports into the graph")
    p.add_argument("path", type=Path)
    p.add_argument("--prune", action="store_true", help="delete playbooks not present in this export")

    p = sub.add_parser("ingest-docs", help="chunk + embed documents")
    p.add_argument("path", type=Path)
    p.add_argument("--source", choices=["vendor", "internal"], required=True)
    p.add_argument("--version", help="FortiSOAR version tag (defaults to FORTISOAR_VERSION)")

    p = sub.add_parser("link-docs", help="link internal docs to playbooks, then rebuild derived links")
    p.add_argument("--method", choices=["auto", "llm", "embedding", "none"], default="auto",
                   help="how to propose links for docs that don't name a playbook "
                        "(auto = LLM, falling back to embedding similarity; none = name matching only)")
    p.add_argument("--no-llm", action="store_true", help="same as --method none (kept for compatibility)")

    sub.add_parser("derive-links", help="rebuild playbook->vendor doc and collection->doc links")

    p = sub.add_parser("ingest-fortinet", help="load the fortinet-fortisoar GitHub content (scripts/fetch_fortinet.py)")
    p.add_argument("path", type=Path, nargs="?", default=Path("data/fortinet/github"))
    p.add_argument("--wipe", action="store_true", help="delete everything in the graph first (including chats)")
    p.add_argument("--yes", action="store_true", help="confirm --wipe")
    p.add_argument("--skip-docs", action="store_true", help="playbooks and catalog only")

    p = sub.add_parser("links", help="review doc -> playbook links")
    p.add_argument("action", choices=["list", "approve", "reject", "add"],
                   help="add = create an approved link by hand (needs --doc and --playbook)")
    p.add_argument("--status", default="proposed", help="for list: which status to show")
    p.add_argument("--doc", help="document id, e.g. internal:runbook.md")
    p.add_argument("--playbook", help="playbook uuid or exact name")
    p.add_argument("--all", action="store_true", help="approve/reject every proposed link matching the filters")
    p.add_argument("--method", choices=["llm", "embedding"], help="with --all: only links from this method")
    p.add_argument("--min-confidence", type=float, default=0.0, help="with --all: only links at or above this")

    p = sub.add_parser("ask")
    p.add_argument("question")

    p = sub.add_parser("serve", help="run the REST API")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=8000)

    p = sub.add_parser("wipe", help="delete everything in the graph (needed when switching embedding model)")
    p.add_argument("--yes", action="store_true")

    args = parser.parse_args()
    svc = get_services()
    svc.settings  # validate config up front so problems surface before any work starts

    if args.cmd == "config":
        _print(svc.settings.describe())
    elif args.cmd == "init-db":
        svc.graph.ensure_schema()
        print("schema ready")
    elif args.cmd == "status":
        _print({"config": svc.settings.describe(), "graph": svc.graph.stats()})
    elif args.cmd == "ingest-playbooks":
        from .ingest.playbooks import ingest_playbooks

        svc.graph.ensure_schema()
        _print(ingest_playbooks(args.path, svc.graph, svc.embedder, svc.scrubber, prune=args.prune))
        _derive(svc)
    elif args.cmd == "ingest-docs":
        from .ingest.docs import ingest_docs

        svc.graph.ensure_schema()
        version = args.version or svc.settings.fortisoar_version
        _print(ingest_docs(args.path, args.source, version, svc.graph, svc.embedder, svc.scrubber))
        _derive(svc)
    elif args.cmd == "link-docs":
        from .ingest.docs import link_internal_docs

        _print(link_internal_docs(svc.graph, svc.llm, method="none" if args.no_llm else args.method))
        _derive(svc)
    elif args.cmd == "derive-links":
        _derive(svc)
    elif args.cmd == "ingest-fortinet":
        import time

        from .ingest.docs import write_documents
        from .ingest.fortinet import link_repo_docs, load_all, write_catalog
        from .ingest.playbooks import write_playbooks

        if args.wipe:
            if not args.yes:
                parser.error("--wipe deletes all graph data including chats; add --yes to confirm")
            svc.graph.wipe()
            print("graph wiped")
        svc.graph.ensure_schema()
        t0 = time.time()
        packs, playbooks, docs = load_all(args.path, svc.scrubber)
        print(f"loaded {len(packs)} repos: {len(playbooks)} playbooks, {len(docs)} documents ({time.time() - t0:.0f}s)", flush=True)
        _print({"playbooks": write_playbooks(playbooks, svc.graph, svc.embedder, prune=True,
                                             progress=lambda m: print(m, flush=True))})
        _print({"catalog": write_catalog(packs, svc.graph)})
        if not args.skip_docs:
            _print({"documents": write_documents(docs, svc.graph, svc.embedder, progress=lambda m: print(m, flush=True))})
            print("linked docs:", link_repo_docs(docs, svc.graph))
        _derive(svc)
        print(f"done in {time.time() - t0:.0f}s")
    elif args.cmd == "links":
        if args.action == "list":
            _print(svc.graph.read(
                "MATCH (p:Playbook)-[r:DOCUMENTED_BY]->(d:Document) WHERE r.status = $status "
                "RETURN d.id AS doc, p.uuid AS playbook, p.name AS name, r.method AS method, "
                "r.confidence AS confidence, r.reason AS reason ORDER BY doc, confidence DESC",
                status=args.status,
            ))
        elif args.action == "add":
            if not (args.doc and args.playbook):
                parser.error("add needs --doc and --playbook")
            rows = svc.graph.run(
                "MATCH (p:Playbook), (d:Document {id: $d}) WHERE p.uuid = $p OR p.name = $p "
                "MERGE (p)-[r:DOCUMENTED_BY]->(d) "
                "SET r.status = 'approved', r.reviewed = true, r.method = coalesce(r.method, 'manual'), "
                "    r.confidence = coalesce(r.confidence, 1.0), r.reason = coalesce(r.reason, 'added by reviewer') "
                "RETURN count(r) AS n",
                p=args.playbook, d=args.doc,
            )
            print(f"linked {rows[0]['n']} (check --doc id and --playbook name if 0)")
            _derive(svc)
        else:
            status = "approved" if args.action == "approve" else "rejected"
            if args.all:
                rows = svc.graph.run(
                    "MATCH (p:Playbook)-[r:DOCUMENTED_BY {status: 'proposed'}]->(d:Document) "
                    "WHERE ($method IS NULL OR r.method = $method) AND coalesce(r.confidence, 0) >= $min "
                    "AND ($doc IS NULL OR d.id = $doc) AND ($pb IS NULL OR p.uuid = $pb OR p.name = $pb) "
                    "SET r.status = $status, r.reviewed = true RETURN count(r) AS n",
                    method=args.method, min=args.min_confidence, doc=args.doc, pb=args.playbook, status=status,
                )
            else:
                if not (args.doc and args.playbook):
                    parser.error("--doc and --playbook are required (or use --all with optional filters)")
                rows = svc.graph.run(
                    "MATCH (p:Playbook)-[r:DOCUMENTED_BY]->(:Document {id: $d}) WHERE p.uuid = $p OR p.name = $p "
                    "SET r.status = $status, r.reviewed = true RETURN count(r) AS n",
                    p=args.playbook, d=args.doc, status=status,
                )
            print(f"{status} {rows[0]['n']} link(s)")
            _derive(svc)
    elif args.cmd == "ask":
        result = svc.ask(args.question)
        print(result["answer"])
        print("\nSources:")
        for s in result["sources"]:
            print(f"  [{s['label']}] {s.get('name') or s.get('title')}")
    elif args.cmd == "serve":
        import uvicorn

        uvicorn.run("app.api:app", host=args.host, port=args.port)
    elif args.cmd == "wipe":
        if not args.yes:
            parser.error("refusing to wipe without --yes")
        svc.graph.wipe()
        print("graph wiped")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, RuntimeError) as exc:  # config / schema problems: show the message, not a traceback
        raise SystemExit(f"error: {exc}")
