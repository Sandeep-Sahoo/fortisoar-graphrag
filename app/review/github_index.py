"""Find the raw JSON of any downloaded Fortinet playbook by uuid, and score them all (no model calls)."""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from ..ingest.playbooks import _collections

ROOT = Path(__file__).resolve().parent.parent.parent / "data" / "fortinet" / "github"
_lock = threading.Lock()
_cache: dict = {"signature": None, "index": {}}


def _signature() -> tuple:
    from ..github_catalog import _signature as sig

    return sig()


def _iter_workflows():
    for file in sorted(ROOT.rglob("playbooks/**/*.json")):
        try:
            if file.stat().st_size > 30_000_000:
                continue
            doc = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        repo = file.relative_to(ROOT).parts[0]
        for coll in _collections(doc, file.stem):
            for i, wf in enumerate(coll["workflows"]):
                if isinstance(wf, dict) and wf.get("uuid"):
                    yield repo, file, coll.get("name") or file.stem, i, wf


def index() -> dict:
    """uuid -> {repo, file, collection, idx} (first occurrence wins, like ingestion)."""
    sig = _signature()
    with _lock:
        if _cache["signature"] == sig and _cache["index"]:
            return _cache["index"]
        idx = {}
        for repo, file, coll, i, wf in _iter_workflows():
            idx.setdefault(wf["uuid"], {"repo": repo, "file": str(file), "collection": coll, "idx": i, "name": wf.get("name")})
        _cache.update(signature=sig, index=idx)
        return idx


def raw(uuid: str) -> tuple[dict, dict] | None:
    entry = index().get(uuid)
    if not entry:
        return None
    doc = json.loads(Path(entry["file"]).read_text(encoding="utf-8"))
    for coll in _collections(doc, Path(entry["file"]).stem):
        wfs = coll["workflows"]
        for wf in wfs:
            if isinstance(wf, dict) and wf.get("uuid") == uuid:
                return wf, entry
    return None


def review_all(scrubber, progress=print) -> dict:
    """Score every downloaded Fortinet playbook; results go to data/review/github.json."""
    from . import store
    from .engine import Context, review_workflow
    from .rules import all_rules, version

    rules = all_rules(include_disabled=False)
    known = set(index())
    out, seen, t0 = {}, set(), time.time()
    for repo, file, coll, i, wf in _iter_workflows():
        if wf["uuid"] in seen:
            continue
        seen.add(wf["uuid"])
        key = f"g:{wf['uuid']}"
        try:
            r = review_workflow(wf, coll, scrubber, Context(known, store.answers(key)), rules)
        except Exception as exc:  # one malformed export shouldn't stop the batch
            progress(f"  ! {repo}/{wf.get('name')}: {exc}")
            continue
        out[key] = store.light(r) | {"repo": repo}
        if len(out) % 500 == 0:
            progress(f"  reviewed {len(out)} playbooks")
    data = {"generated": store.now(), "rules_version": version(), "seconds": round(time.time() - t0, 1), "playbooks": out}
    store.save_github_scores(data)
    return {"playbooks": len(out), "seconds": data["seconds"],
            "average": round(sum(r["score"] for r in out.values()) / max(1, len(out)), 1)}
