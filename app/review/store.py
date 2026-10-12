"""Review state on disk (data/review/): reviewer answers, review status, score history, AI notes, GitHub scores.

Playbook keys:  u:<source_id>:<original uuid>  (your uploads)   g:<uuid>  (Fortinet GitHub playbooks)
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path

DIR = Path(__file__).resolve().parent.parent.parent / "data" / "review"
ANSWERS = DIR / "answers.json"
HISTORY = DIR / "history.json"
AI_NOTES = DIR / "ai_notes.json"
GITHUB = DIR / "github.json"
SOURCES_DIR = DIR / "sources"
STATUSES = ("open", "in review", "changes requested", "approved")
_lock = threading.RLock()


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _read(path: Path, default):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def _write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1, default=str))
    tmp.replace(path)


# ---------------------------------------------------------------- reviewer answers + status

def answers(key: str) -> dict:
    return _read(ANSWERS, {}).get(key, {}).get("rules", {})


def meta(key: str) -> dict:
    entry = _read(ANSWERS, {}).get(key, {})
    return {k: entry.get(k) for k in ("status", "notes", "status_by", "status_at")}


def all_meta() -> dict:
    return {k: {"status": v.get("status"), "answered": len(v.get("rules", {}))} for k, v in _read(ANSWERS, {}).items()}


def set_answer(key: str, rule_id: str, answer: str | None, note: str, by: str) -> None:
    if answer not in (None, "", "pass", "fail", "na"):
        raise ValueError("answer must be pass, fail or na")
    with _lock:
        data = _read(ANSWERS, {})
        rules = data.setdefault(key, {}).setdefault("rules", {})
        if not answer:
            rules.pop(rule_id, None)
        else:
            rules[rule_id] = {"answer": answer, "note": note.strip()[:500], "by": by, "at": now()}
        _write(ANSWERS, data)


def set_status(key: str, status: str, notes: str, by: str) -> dict:
    if status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}")
    with _lock:
        data = _read(ANSWERS, {})
        entry = data.setdefault(key, {})
        entry.update(status=status, notes=notes.strip()[:4000], status_by=by, status_at=now())
        _write(ANSWERS, data)
    return meta(key)


def forget(keys: list[str]) -> None:
    with _lock:
        for path in (ANSWERS, HISTORY, AI_NOTES):
            data = _read(path, {})
            if any(k in data for k in keys):
                for k in keys:
                    data.pop(k, None)
                _write(path, data)


# ---------------------------------------------------------------- score history (one entry per (re)review that changed something)

def add_history(key: str, review: dict, changes: dict | None, source_version: str | None = None) -> None:
    with _lock:
        data = _read(HISTORY, {})
        items = data.setdefault(key, [])
        entry = {"at": review["reviewed_at"], "score": review["score"], "grade": review["grade"], "verdict": review["verdict"],
                 "findings": len(review["findings"]), "version": source_version,
                 "changes": changes if changes and any(changes.values()) else None}
        last = items[0] if items else None
        if last and last["score"] == entry["score"] and not entry["changes"] and last.get("version") == source_version:
            return
        data[key] = ([entry] + items)[:25]
        _write(HISTORY, data)


def history(key: str) -> list[dict]:
    return _read(HISTORY, {}).get(key, [])


# ---------------------------------------------------------------- AI reviewer notes

def ai_note(key: str) -> dict | None:
    return _read(AI_NOTES, {}).get(key)


def set_ai_note(key: str, text: str, model: str, by: str, score: int) -> dict:
    with _lock:
        data = _read(AI_NOTES, {})
        data[key] = {"text": text, "model": model, "by": by, "at": now(), "score_at_time": score}
        _write(AI_NOTES, data)
    return data[key]


# ---------------------------------------------------------------- per-source reviews (uploads)

def source_reviews(source_id: str) -> dict:
    return _read(SOURCES_DIR / f"{source_id}.json", {"reviews": {}})


def save_source_reviews(source_id: str, data: dict) -> None:
    with _lock:
        _write(SOURCES_DIR / f"{source_id}.json", data)


def drop_source_reviews(source_id: str) -> None:
    with _lock:
        (SOURCES_DIR / f"{source_id}.json").unlink(missing_ok=True)


# ---------------------------------------------------------------- GitHub batch scores

def github_scores() -> dict:
    return _read(GITHUB, {"generated": None, "rules_version": None, "playbooks": {}})


def save_github_scores(data: dict) -> None:
    with _lock:
        _write(GITHUB, data)


def light(review: dict) -> dict:
    """Row for lists: no findings / checklist bodies."""
    return {k: review.get(k) for k in ("uuid", "name", "collection", "trigger_type", "modules", "steps", "connectors",
                                       "score", "grade", "verdict", "provisional", "manual_pending", "categories",
                                       "counts", "reviewed_at", "rules_version")} | {
        "top": sorted({f["title"] for f in review["findings"] if f["severity"] in ("critical", "high")})[:4],
        "security": [{"rule": f["rule"], "title": f["title"], "severity": f["severity"], "step": f["step"], "detail": f["detail"]}
                     for f in review["findings"] if f["category"] == "Security" and f["severity"] != "info"][:20],
    }
