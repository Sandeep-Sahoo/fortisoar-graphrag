"""Your own playbooks: upload JSON / ZIP exports or connect a Git repository, load them into the graph
(linked to the Fortinet connectors, modules and child playbooks they use), and review every playbook.

Files are parsed as data, never executed. Git clones run with hooks, submodules, symlinks and non-HTTPS
protocols disabled, and only *.json files are kept. Uploaded playbooks get their own uuids in the graph
(u-<source>-<uuid>) so they never overwrite the Fortinet copies; a COPY_OF link records the match.
"""
from __future__ import annotations

import base64
import copy
import io
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import uuid as uuidlib
import zipfile
from pathlib import Path, PurePosixPath

from ..ingest.playbooks import UUID_RE, _collections, parse_playbook
from . import store
from .engine import Context, diff_signatures, review_workflow
from ..workspace import path as _ws

ROOT = Path(__file__).resolve().parent.parent.parent / "data" / "uploads"
SOURCES_FILE = ROOT / "sources.json"
MAX_FILE = 25_000_000
MAX_TOTAL = 200_000_000
MAX_FILES = 5000
URL_RE = re.compile(r"^https://[A-Za-z0-9.-]+(:\d{2,5})?/[A-Za-z0-9._~/%+-]+?(\.git)?/?$")
BRANCH_RE = re.compile(r"^[A-Za-z0-9._/-]{1,200}$")
_lock = threading.RLock()


class NullEmbedder:
    """Uploads are stored without vectors (keyword search works; embed later on approval)."""

    dim = 0

    def embed(self, texts, query=False):
        return [None] * len(texts)


# ---------------------------------------------------------------- source registry

def sources() -> list[dict]:
    try:
        return json.loads(_ws(SOURCES_FILE).read_text())
    except (OSError, ValueError):
        return []


def get(source_id: str) -> dict | None:
    return next((s for s in sources() if s["id"] == source_id), None)


def _save_sources(items: list[dict]) -> None:
    _ws(ROOT).mkdir(parents=True, exist_ok=True)
    tmp = _ws(SOURCES_FILE).with_suffix(".tmp")
    tmp.write_text(json.dumps(items, indent=1))
    tmp.replace(_ws(SOURCES_FILE))


def _update(source_id: str, **changes) -> dict:
    with _lock:
        items = sources()
        for s in items:
            if s["id"] == source_id:
                s.update(changes)
                _save_sources(items)
                return s
    raise KeyError(source_id)


def _files_dir(source_id: str) -> Path:
    return _ws(ROOT) / source_id / "files"


def _new_source(name: str, kind: str, by: str, **extra) -> dict:
    name = re.sub(r"\s+", " ", name).strip()[:80]
    if not name:
        raise ValueError("give the source a name")
    with _lock:
        items = sources()
        if any(s["name"].lower() == name.lower() for s in items):
            raise ValueError("a source with that name already exists")
        entry = {"id": uuidlib.uuid4().hex[:8], "name": name, "kind": kind, "created_by": by, "created": store.now(),
                 "last_synced": None, "status": "new", "files": 0, "playbooks": 0, **extra}
        items.append(entry)
        _save_sources(items)
    return entry


# ---------------------------------------------------------------- receiving files

def _safe_rel(name: str) -> str | None:
    parts = [p for p in PurePosixPath(name.replace("\\", "/")).parts if p not in ("", ".")]
    if not parts or any(p == ".." or p.startswith("/") for p in parts):
        return None
    return "/".join(parts)[:300]


def _accept(files: dict[str, bytes], rel: str, data: bytes) -> None:
    if len(files) >= MAX_FILES:
        raise ValueError(f"too many files (max {MAX_FILES})")
    if len(data) > MAX_FILE:
        raise ValueError(f"{rel} is larger than {MAX_FILE // 1_000_000} MB")
    if sum(map(len, files.values())) + len(data) > MAX_TOTAL:
        raise ValueError(f"upload is larger than {MAX_TOTAL // 1_000_000} MB")
    files[rel] = data


def unpack(uploads: list[dict]) -> dict[str, bytes]:
    """[{name, content_base64}] -> {relative path: bytes}; ZIPs are expanded (JSON members only)."""
    files: dict[str, bytes] = {}
    for item in uploads:
        rel = _safe_rel(str(item.get("name") or ""))
        if not rel:
            raise ValueError("invalid file name")
        try:
            data = base64.b64decode(item.get("content_base64") or "", validate=True)
        except ValueError:
            raise ValueError(f"{rel}: not valid base64")
        if rel.lower().endswith(".zip"):
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                total = 0
                for info in zf.infolist():
                    if info.is_dir() or not info.filename.lower().endswith(".json"):
                        continue
                    inner = _safe_rel(f"{rel[:-4]}/{info.filename}")
                    total += info.file_size
                    if not inner or info.file_size > MAX_FILE or total > MAX_TOTAL:
                        raise ValueError(f"{info.filename}: unsafe path or too large")
                    with zf.open(info) as fh:
                        _accept(files, inner, fh.read(MAX_FILE + 1))
        elif rel.lower().endswith(".json"):
            _accept(files, rel, data)
        else:
            raise ValueError(f"{rel}: only .json playbook exports or .zip archives of them")
    if not files:
        raise ValueError("no JSON files found")
    return files


def _store_files(source_id: str, files: dict[str, bytes]) -> None:
    target = _files_dir(source_id)
    staging = target.with_name("files.tmp")
    shutil.rmtree(staging, ignore_errors=True)
    for rel, data in files.items():
        path = staging.joinpath(*rel.split("/"))
        if not str(path.resolve()).startswith(str(staging.resolve())):
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    staging.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(target, ignore_errors=True)
    staging.rename(target)


# ---------------------------------------------------------------- git

def _git_env(token: str | None, token_user: str) -> dict:
    env = {"GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "/bin/echo", "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
           "HOME": tempfile.gettempdir(), "GIT_CONFIG_NOSYSTEM": "1"}
    if token:  # header via environment config, so the token never appears in argv / process lists
        auth = base64.b64encode(f"{token_user}:{token}".encode()).decode()
        env.update(GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="http.extraHeader", GIT_CONFIG_VALUE_0=f"Authorization: Basic {auth}")
    return env


def clone(url: str, branch: str, subpath: str, token: str | None, token_user: str) -> tuple[dict[str, bytes], str]:
    if not URL_RE.match(url) or "@" in url:
        raise ValueError("use an https:// repository URL without credentials (set a token in Settings for private repos)")
    if branch and not BRANCH_RE.match(branch):
        raise ValueError("invalid branch name")
    sub = _safe_rel(subpath) if subpath.strip("/ ") else ""
    if subpath.strip("/ ") and not sub:
        raise ValueError("invalid folder path")
    tmp = Path(tempfile.mkdtemp(prefix="graphrag-git-"))
    try:
        cmd = ["git", "-c", "protocol.allow=never", "-c", "protocol.https.allow=always", "-c", "core.hooksPath=/dev/null",
               "-c", "core.symlinks=false", "-c", "submodule.recurse=false", "clone", "--depth", "1", "--single-branch",
               "--no-tags", "--quiet"] + (["--branch", branch] if branch else []) + ["--", url, str(tmp / "repo")]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=240, env=_git_env(token, token_user))
        if result.returncode:
            message = (result.stderr or result.stdout).strip().splitlines()
            message = next((l for l in message if l.strip()), "git clone failed")
            raise RuntimeError(re.sub(r"Authorization: \S+ \S+", "Authorization: ***", message)[:300])
        repo = tmp / "repo"
        commit = subprocess.run(["git", "-C", str(repo), "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                                timeout=30, env=_git_env(None, "")).stdout.strip()
        base = (repo / sub) if sub else repo
        if not base.resolve().is_relative_to(repo.resolve()) or not base.is_dir():
            raise ValueError(f"folder '{subpath}' not found in the repository")
        files: dict[str, bytes] = {}
        for path in sorted(base.rglob("*.json")):
            if path.is_symlink() or ".git" in path.relative_to(repo).parts or not path.is_file():
                continue
            _accept(files, str(path.relative_to(base)), path.read_bytes())
        if not files:
            raise ValueError("no .json files in that repository / folder")
        return files, commit
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------- parsing, graph, reviews

def _workflows(source_id: str) -> list[tuple[str, str, dict]]:
    """(relative file, collection, workflow) for every playbook in the source's files."""
    out = []
    base = _files_dir(source_id)
    for path in sorted(base.rglob("*.json")):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            out.append((str(path.relative_to(base)), "", {"__error__": f"not valid JSON: {str(exc)[:120]}"}))
            continue
        found = False
        for coll in _collections(doc, path.stem):
            for wf in coll["workflows"]:
                if isinstance(wf, dict) and isinstance(wf.get("steps"), list):
                    out.append((str(path.relative_to(base)), coll.get("name") or path.stem, wf))
                    found = True
        if not found:
            out.append((str(path.relative_to(base)), "", {"__error__": "no FortiSOAR playbooks in this file"}))
    return out


def graph_uuid(source_id: str, original: str) -> str:
    return f"u-{source_id}-{original}"


def _rewrite(wf: dict, source_id: str, originals: set[str]) -> dict:
    """Copy with graph uuids: the playbook's own and references to playbooks in the same source."""
    wf = copy.deepcopy(wf)
    wf["uuid"] = graph_uuid(source_id, wf["uuid"])
    for step in wf.get("steps") or []:
        args = step.get("arguments") if isinstance(step, dict) else None
        if isinstance(args, dict) and isinstance(args.get("workflowReference"), str):
            ref = args["workflowReference"]
            m = UUID_RE.search(ref)
            if m and m.group(0) in originals and "{{" not in ref:
                args["workflowReference"] = ref.replace(m.group(0), graph_uuid(source_id, m.group(0)))
    return wf


def process(source_id: str, svc, version: str | None = None, progress=print) -> dict:
    """(Re)load a source into the graph and review every playbook in it. No model calls."""
    from ..ingest.linking import derive_links
    from ..ingest.playbooks import write_playbooks
    from . import github_index

    src = get(source_id)
    if not src:
        raise KeyError(source_id)
    items = _workflows(source_id)
    errors = [{"file": f, "error": wf["__error__"]} for f, _, wf in items if "__error__" in wf]
    wfs = [(f, c, wf) for f, c, wf in items if "__error__" not in wf]
    for f, c, wf in wfs:  # exports without uuids get a stable one from the file + name
        if not wf.get("uuid"):
            wf["uuid"] = str(uuidlib.uuid5(uuidlib.NAMESPACE_URL, f"{source_id}/{f}/{wf.get('name')}"))
    originals = {wf["uuid"] for _, _, wf in wfs}

    # graph: playbooks with their steps, connectors, modules and child-playbook calls
    playbooks = []
    for f, coll, wf in wfs:
        pb = parse_playbook(_rewrite(wf, source_id, originals), f"{coll} · {src['name']}", svc.scrubber)
        pb.pack, pb.pack_type = src["name"], "upload"
        playbooks.append(pb)
    g = svc.graph
    g.ensure_schema()
    written = write_playbooks(playbooks, g, NullEmbedder(), prune=False, progress=progress) if playbooks else {"playbooks": 0}
    uuids = [pb.uuid for pb in playbooks]
    g.run("UNWIND $uuids AS u MATCH (p:Playbook {uuid: u}) SET p.source_id = $sid, p.origin = 'upload', p.repo = null",
          uuids=uuids, sid=source_id)
    removed = g.run("MATCH (p:Playbook {source_id: $sid}) WHERE NOT p.uuid IN $uuids "
                    "OPTIONAL MATCH (p)-[:HAS_STEP]->(s:Step) DETACH DELETE s, p RETURN count(DISTINCT p) AS n",
                    uuids=uuids, sid=source_id)
    g.run("MATCH (c:PlaybookCollection) WHERE NOT (c)-[:CONTAINS]->() DETACH DELETE c")
    copies = g.run(
        "UNWIND $pairs AS pr MATCH (u:Playbook {uuid: pr.u}), (o:Playbook {uuid: pr.o}) "
        "WHERE coalesce(o.pack_type, '') <> 'upload' AND NOT coalesce(o.placeholder, false) "
        "MERGE (u)-[:COPY_OF]->(o) RETURN count(*) AS n",
        pairs=[{"u": graph_uuid(source_id, o), "o": o} for o in originals])[0]["n"]
    progress("linking playbooks to docs and modules…")
    derive_links(g)

    # reviews
    known = originals | {r["uuid"] for r in g.read("MATCH (p:Playbook) WHERE NOT coalesce(p.placeholder, false) "
                                                   "AND coalesce(p.pack_type, '') <> 'upload' RETURN p.uuid AS uuid")}
    from .. import workspace

    gh = github_index.index() if workspace.has("github") else {}  # fully separate workspaces don't compare with Fortinet
    previous = store.source_reviews(source_id).get("reviews", {})
    reviews = {}
    for f, coll, wf in wfs:
        key = f"u:{source_id}:{wf['uuid']}"
        r = review_workflow(wf, coll, svc.scrubber, Context(known, store.answers(key)))
        r.update(key=key, file=f, graph_uuid=graph_uuid(source_id, wf["uuid"]), source_id=source_id, source=src["name"],
                 copy_of=wf["uuid"] if wf["uuid"] in gh else None)
        changes = diff_signatures(previous.get(key, {}).get("signature"), r["signature"]) if key in previous else None
        r["changes"] = changes if changes and any(changes.values()) else None
        store.add_history(key, r, changes, version)
        reviews[key] = r
    store.forget([k for k in previous if k not in reviews])
    store.save_source_reviews(source_id, {"reviews": reviews, "errors": errors, "processed": store.now()})
    scores = [r["score"] for r in reviews.values()]
    summary = {"files": len({f for f, _, _ in items}), "playbooks": len(reviews), "errors": len(errors),
               "removed": removed[0]["n"] if removed else 0, "copies_of_fortinet": copies,
               "avg_score": round(sum(scores) / len(scores), 1) if scores else None,
               "steps": written.get("steps", 0)}
    _update(source_id, status="ready" if reviews else "no playbooks", last_synced=store.now(), error=None,
            files=summary["files"], playbooks=summary["playbooks"], avg_score=summary["avg_score"],
            **({"commit": version} if version and src["kind"] == "git" else {}))
    return summary


def create_upload(name: str, uploads: list[dict], by: str, svc) -> dict:
    files = unpack(uploads)
    src = _new_source(name, "upload", by)
    try:
        _store_files(src["id"], files)
        summary = process(src["id"], svc, version=store.now()[:19])
    except Exception:
        delete(src["id"], svc)
        raise
    return {"source": get(src["id"]), "summary": summary}


def add_files(source_id: str, uploads: list[dict], replace: bool, svc) -> dict:
    src = get(source_id)
    if not src or src["kind"] != "upload":
        raise ValueError("files can only be added to an upload source")
    files = unpack(uploads)
    if not replace:
        base = _files_dir(source_id)
        existing = {str(p.relative_to(base)): p.read_bytes() for p in base.rglob("*.json")} if base.exists() else {}
        files = {**existing, **files}
    _store_files(source_id, files)
    return {"source": get(source_id), "summary": process(source_id, svc, version=store.now()[:19])}


def create_git(name: str, url: str, branch: str, path: str, by: str, svc) -> dict:
    s = svc.settings
    files, commit = clone(url.strip(), branch.strip(), path.strip(), s.git_token, s.git_token_user)
    src = _new_source(name, "git", by, url=url.strip(), branch=branch.strip(), path=path.strip())
    try:
        _store_files(src["id"], files)
        summary = process(src["id"], svc, version=commit)
    except Exception:
        delete(src["id"], svc)
        raise
    return {"source": get(src["id"]), "summary": summary}


def sync(source_id: str, svc) -> dict:
    """Git: pull again and re-review. Upload: re-review the stored files (e.g. after rule changes)."""
    src = get(source_id)
    if not src:
        raise KeyError(source_id)
    if src["kind"] == "git":
        s = svc.settings
        try:
            files, commit = clone(src["url"], src.get("branch", ""), src.get("path", ""), s.git_token, s.git_token_user)
        except Exception as exc:
            _update(source_id, status="error", error=str(exc)[:300])
            raise
        unchanged = commit and commit == src.get("commit")
        if not unchanged:
            _store_files(source_id, files)
        summary = process(source_id, svc, version=commit)
        summary["unchanged"] = bool(unchanged)
        return {"source": get(source_id), "summary": summary}
    return {"source": get(source_id), "summary": process(source_id, svc, version=src.get("last_synced"))}


def delete(source_id: str, svc) -> bool:
    with _lock:
        items = sources()
        keep = [s for s in items if s["id"] != source_id]
        if len(keep) == len(items):
            return False
        _save_sources(keep)
    keys = list(store.source_reviews(source_id).get("reviews", {}))
    store.forget(keys)
    store.drop_source_reviews(source_id)
    shutil.rmtree(_ws(ROOT) / source_id, ignore_errors=True)
    try:
        g = svc.graph
        g.run("MATCH (p:Playbook {source_id: $sid}) OPTIONAL MATCH (p)-[:HAS_STEP]->(s:Step) DETACH DELETE s, p", sid=source_id)
        g.run("MATCH (c:PlaybookCollection) WHERE NOT (c)-[:CONTAINS]->() DETACH DELETE c")
    except Exception:  # graph offline: files and reviews are gone; nodes are cleaned on the next sync
        pass
    return True


# ---------------------------------------------------------------- internal docs (runbooks / SOPs)

DOC_SUFFIXES = (".md", ".markdown", ".txt", ".pdf", ".html", ".htm")


def upload_docs(uploads: list[dict], by: str, svc) -> dict:
    """Internal docs: stored as source 'internal', keyword-searchable, linked to playbooks they name. No embeddings."""
    from ..ingest.docs import link_internal_docs, read_text, title_of, write_documents

    target = _ws(ROOT) / "_docs"
    docs = []
    for item in uploads:
        rel = _safe_rel(str(item.get("name") or ""))
        if not rel or not rel.lower().endswith(DOC_SUFFIXES):
            raise ValueError(f"{item.get('name')}: only {', '.join(DOC_SUFFIXES)} files")
        data = base64.b64decode(item.get("content_base64") or "", validate=True)
        if len(data) > MAX_FILE:
            raise ValueError(f"{rel} is too large")
        path = target / rel
        if not str(path.resolve()).startswith(str(target.resolve())):
            raise ValueError("invalid path")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        text = svc.scrubber.text(read_text(path))
        if not text.strip():
            raise ValueError(f"{rel}: no text could be extracted")
        docs.append({"id": f"internal:upload/{rel}", "title": title_of(text, path), "text": text, "source": "internal",
                     "version": store.now()[:10], "path": f"upload/{rel}", "props": {"kind": "internal", "uploaded_by": by}})
    result = write_documents(docs, svc.graph, NullEmbedder(), progress=lambda m: None)
    links = link_internal_docs(svc.graph, None, method="none")
    return {"documents": result["documents"], "chunks": result["chunks"], "linked_by_name": links["name_match"]}
