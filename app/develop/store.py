"""Develop sessions on disk (data/develop/<id>.json); generated files under data/develop/<id>/v<n>/."""
from __future__ import annotations

import json
import re
import shutil
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from ..workspace import path as _ws

DIR = Path(__file__).resolve().parent.parent.parent / "data" / "develop"
ID_RE = re.compile(r"^[a-f0-9]{10}$")
_lock = threading.RLock()


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _path(sid: str) -> Path:
    if not ID_RE.match(sid or ""):
        raise KeyError(sid)
    return _ws(DIR) / f"{sid}.json"


def create(by: str) -> dict:
    s = {"id": uuid.uuid4().hex[:10], "title": "New playbook", "created_by": by, "created": now(), "updated": now(),
         "stage": "intake", "question": "usecase", "answers": {}, "prefill": {}, "spec": None, "versions": [], "messages": [],
         "generated": [], "pb_uuid": str(uuid.uuid4()), "coll_uuid": str(uuid.uuid4())}
    save(s)
    return s


def get(sid: str) -> dict | None:
    try:
        return json.loads(_path(sid).read_text())
    except (OSError, ValueError, KeyError):
        return None


def save(s: dict) -> None:
    s["updated"] = now()
    with _lock:
        _ws(DIR).mkdir(parents=True, exist_ok=True)
        tmp = _path(s["id"]).with_suffix(".tmp")
        tmp.write_text(json.dumps(s, indent=1, default=str))
        tmp.replace(_path(s["id"]))


def list_sessions() -> list[dict]:
    out = []
    for p in sorted(_ws(DIR).glob("*.json")) if _ws(DIR).exists() else []:
        try:
            s = json.loads(p.read_text())
        except ValueError:
            continue
        last = (s.get("generated") or [None])[-1]
        out.append({"id": s["id"], "title": s.get("title"), "stage": s.get("stage"), "updated": s.get("updated"),
                    "created_by": s.get("created_by"), "versions": len(s.get("versions") or []),
                    "score": last["review"]["score"] if last else None, "grade": last["review"]["grade"] if last else None})
    return sorted(out, key=lambda x: x["updated"] or "", reverse=True)


def delete(sid: str) -> bool:
    path = _path(sid)
    if not path.exists():
        return False
    with _lock:
        path.unlink()
        shutil.rmtree(_ws(DIR) / sid, ignore_errors=True)
    return True


def files_dir(sid: str, version: int) -> Path:
    _path(sid)
    d = _ws(DIR) / sid / f"v{int(version)}"
    d.mkdir(parents=True, exist_ok=True)
    return d
