"""Audit trail (data/audit.jsonl): who did what, when. Values of settings / secrets are never recorded."""
from __future__ import annotations

import json
import threading
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

AUDIT_FILE = Path(__file__).resolve().parent.parent / "data" / "audit.jsonl"
_lock = threading.Lock()


def record(user: str | None, action: str, target: str = "", **details) -> None:
    entry = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "user": user or "-",
             "action": action, "target": str(target)[:300]}
    if details:
        entry["details"] = {k: v for k, v in details.items() if v not in (None, "", [], {})}
    with _lock:
        AUDIT_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(AUDIT_FILE, "a") as fh:
            fh.write(json.dumps(entry, default=str) + "\n")


def read(limit: int = 300, user: str = "", action: str = "", q: str = "") -> list[dict]:
    """Newest first, optionally filtered."""
    if not AUDIT_FILE.exists():
        return []
    keep: deque = deque(maxlen=20000)
    with open(AUDIT_FILE) as fh:
        for line in fh:
            keep.append(line)
    out = []
    q = q.lower()
    for line in reversed(keep):
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if user and e.get("user") != user:
            continue
        if action and not e.get("action", "").startswith(action):
            continue
        if q and q not in line.lower():
            continue
        out.append(e)
        if len(out) >= limit:
            break
    return out
