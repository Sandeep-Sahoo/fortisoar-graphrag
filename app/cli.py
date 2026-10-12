"""Command line: python -m app.cli <command>"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .services import get_services


def _print(obj) -> None:
    print(json.dumps(obj, indent=2, default=str))


def _set_budget(svc, limit: int) -> None:
    if not hasattr(svc.embedder, "budget"):
        raise ValueError("embedding budgets need the embedding cache (EMBED_CACHE)")
    svc.embedder.budget = max(0, limit)


def _report_budget(svc) -> None:
    emb = svc.embedder
    if getattr(emb, "budget", None) is not None:
        print(f"embedding budget: {emb.budget} left, {emb.skipped} item(s) stored without a vector (pending)")


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
    p.add_argument("--repos", help="comma-separated repo names to load (others are kept; no pruning)")
    p.add_argument("--max-embeddings", type=int, help="cap NEW embeddings for this run; the rest is stored as pending")
    p.add_argument("--mode", choices=["ingest", "reingest", "sync"], default="ingest", help="label recorded in the ingest history")

    p = sub.add_parser("ingest-guide", help="load a product guide PDF, one document per bookmarked section")
    p.add_argument("path", type=Path)
    p.add_argument("--guide", default="Playbooks Guide")
    p.add_argument("--version", default="7.6.5")
    p.add_argument("--max-embeddings", type=int, help="cap NEW embeddings; most useful sections first")
    p.add_argument("--dry-run", action="store_true", help="show the sections and what would be embedded, write nothing")

    p = sub.add_parser("embed-pending", help="embed stored playbooks / chunks that have no vector yet")
    p.add_argument("--limit", type=int, required=True)
    p.add_argument("--what", choices=["all", "playbooks", "chunks"], default="all")
    p.add_argument("--repos", help="only items loaded from these comma-separated GitHub repos (records a 'continue' run)")

    p = sub.add_parser("links", help="review doc -> playbook links")
    p.add_argument("action", choices=["list", "approve", "reject", "add"],
                   help="add = create an approved link by hand (needs --doc and --playbook)")
    p.add_argument("--status", default="proposed", help="for list: which status to show")
    p.add_argument("--doc", help="document id, e.g. internal:runbook.md")
    p.add_argument("--playbook", help="playbook uuid or exact name")
    p.add_argument("--all", action="store_true", help="approve/reject every proposed link matching the filters")
    p.add_argument("--method", choices=["llm", "embedding"], help="with --all: only links from this method")
    p.add_argument("--min-confidence", type=float, default=0.0, help="with --all: only links at or above this")

    sub.add_parser("review-github", help="score every downloaded Fortinet playbook against the best practices (no model calls)")
    sub.add_parser("sync-sources", help="re-pull connected Git sources and re-review uploads (no model calls)")
    p = sub.add_parser("scheduled-sync", help="fetch changed GitHub repos, optionally re-ingest them (0 new embeddings), sync sources")
    p.add_argument("--force", action="store_true", help="run even if the schedule is off / not due")

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

        if args.max_embeddings is not None:
            _set_budget(svc, args.max_embeddings)
        if args.wipe:
            if not args.yes:
                parser.error("--wipe deletes all graph data including chats; add --yes to confirm")
            svc.graph.wipe()
            print("graph wiped")
        svc.graph.ensure_schema()
        t0 = time.time()
        only = {r.strip() for r in args.repos.split(",") if r.strip()} if args.repos else None
        packs, playbooks, docs = load_all(args.path, svc.scrubber, only=only)
        print(f"loaded {len(packs)} repos: {len(playbooks)} playbooks, {len(docs)} documents ({time.time() - t0:.0f}s)", flush=True)
        from .ingest import state as ingest_state

        before = ingest_state.playbook_signatures_from_graph(svc.graph, sorted(only) if only else None)
        per_repo = ingest_state.diff(before, ingest_state.playbook_signatures_from_parse(playbooks))
        _print({"playbooks": write_playbooks(playbooks, svc.graph, svc.embedder, prune=only is None,
                                             progress=lambda m: print(m, flush=True))})
        _print({"catalog": write_catalog(packs, svc.graph)})
        if not args.skip_docs:
            _print({"documents": write_documents(docs, svc.graph, svc.embedder, progress=lambda m: print(m, flush=True))})
            print("linked docs:", link_repo_docs(docs, svc.graph))
        _derive(svc)
        _report_budget(svc)
        from .ingest.state import record

        record(sorted(only) if only else [p.repo for p in packs], args.mode, args.max_embeddings,
               {"embedded_skipped": getattr(svc.embedder, "skipped", 0)}, per_repo)
        changed = {r: d["diff"]["counts"] for r, d in per_repo.items() if not d["diff"]["first_load"] and any(d["diff"]["counts"].values())}
        if changed:
            print("playbook changes since the last ingest:", json.dumps(changed))
        print(f"done in {time.time() - t0:.0f}s")
    elif args.cmd == "ingest-guide":
        from .ingest.docs import write_documents
        from .ingest.guide import guide_priority, split_guide

        docs = split_guide(args.path, args.guide, args.version)
        chunks = [(d, i) for d in docs for i, _ in enumerate(__import__("app.ingest.docs", fromlist=["chunk_text"]).chunk_text(d["text"]))]
        ranked = sorted(chunks, key=lambda x: -guide_priority(*x))
        budget = args.max_embeddings if args.max_embeddings is not None else len(ranked)
        print(f"{len(docs)} sections, {len(chunks)} chunks; embedding budget {budget}")
        covered = {}
        for d, _ in ranked[:budget]:
            covered[d["props"]["section"]] = covered.get(d["props"]["section"], 0) + 1
        for section, n in sorted(covered.items(), key=lambda x: -x[1]):
            print(f"  {n:3} embedded  {section}")
        if args.dry_run:
            print("dry run: nothing written")
            return
        svc.graph.ensure_schema()
        _set_budget(svc, budget)
        _print(write_documents(docs, svc.graph, svc.embedder, progress=lambda m: print(m, flush=True), priority=guide_priority))
        _derive(svc)
        _report_budget(svc)
    elif args.cmd == "embed-pending":
        from .ingest.docs import embed_pending

        _set_budget(svc, args.limit)
        repos = [r.strip() for r in args.repos.split(",") if r.strip()] if args.repos else None
        result = embed_pending(svc.graph, svc.embedder, args.limit, args.what, repos)
        _print(result)
        _report_budget(svc)
        if repos:
            from .ingest.state import record

            record(repos, "continue", args.limit, {"embedded": result["embedded"]})
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
    elif args.cmd == "review-github":
        from .review.github_index import review_all

        _print(review_all(svc.scrubber, progress=lambda m: print(m, flush=True)))
    elif args.cmd == "sync-sources":
        from .review import sources

        for src in sources.sources():
            try:
                result = sources.sync(src["id"], svc)["summary"]
                print(f"{src['name']}: {result['playbooks']} playbooks, avg score {result['avg_score']}"
                      + (" (unchanged)" if result.get("unchanged") else ""), flush=True)
            except Exception as exc:
                print(f"  ! {src['name']}: {exc}", flush=True)
    elif args.cmd == "scheduled-sync":
        from .scheduler import run_sync

        _print(run_sync(svc, force=args.force, progress=lambda m: print(m, flush=True)))
    elif args.cmd == "ask":
        result = svc.ask(args.question)
        print(result["answer"])
        print("\nSources:")
        for s in result["sources"]:
            print(f"  [{s['label']}] {s.get('name') or s.get('title')}")
    elif args.cmd == "serve":
        import uvicorn

        # the app logs every request itself (with request ids), so uvicorn's access log would duplicate it
        uvicorn.run("app.api:app", host=args.host, port=args.port, access_log=False)
    elif args.cmd == "wipe":
        if not args.yes:
            parser.error("refusing to wipe without --yes")
        svc.graph.wipe()
        print("graph wiped")


if __name__ == "__main__":
    import logging

    from .logging_setup import setup_logging

    setup_logging()
    cli_log = logging.getLogger("graphrag.cli")
    try:
        main()
    except (ValueError, RuntimeError) as exc:  # config / schema problems: show the message, not a traceback
        cli_log.error("command failed: %s", exc)
        raise SystemExit(f"error: {exc}")
    except KeyboardInterrupt:
        cli_log.warning("interrupted")
        raise SystemExit(130)
    except Exception:
        cli_log.exception("unexpected error")
        raise SystemExit("error: unexpected failure; details are in data/logs/app.log")
