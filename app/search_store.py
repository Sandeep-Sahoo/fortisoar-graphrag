"""Graph-only search helpers stored on disk: the synonym map and saved searches (data/search/)."""
from __future__ import annotations

import json
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from .workspace import path as _ws

DIR = Path(__file__).resolve().parent.parent / "data" / "search"
SYNONYMS_FILE = DIR / "synonyms.json"
SAVED_FILE = DIR / "saved.json"
_lock = threading.Lock()

# term or phrase -> words that mean the same in playbook / step / connector names. Editable in the UI.
DEFAULT_SYNONYMS: dict[str, list[str]] = {
    "lock out": ["disable", "block", "suspend"],
    "lockout": ["disable", "block", "suspend"],
    "disable": ["block", "suspend", "deactivate"],
    "user": ["account", "identity", "username"],
    "account": ["user", "identity"],
    "ip": ["address", "ipv4", "ip_address"],
    "malware": ["malicious", "virus", "hash", "file"],
    "hash": ["md5", "sha1", "sha256", "filehash"],
    "file hash": ["md5", "sha256", "filehash"],
    "phishing": ["email", "spam", "mail"],
    "email": ["mail", "phishing", "smtp"],
    "isolate": ["quarantine", "contain"],
    "quarantine": ["isolate", "contain"],
    "contain": ["isolate", "quarantine", "block"],
    "host": ["endpoint", "device", "machine", "asset"],
    "endpoint": ["host", "device", "edr"],
    "block": ["deny", "blacklist", "blocklist"],
    "unblock": ["allow", "whitelist", "allowlist"],
    "enrich": ["enrichment", "reputation", "lookup"],
    "reputation": ["enrichment", "lookup", "score"],
    "ticket": ["incident", "case", "jira", "servicenow"],
    "notify": ["notification", "email", "slack", "teams", "message"],
    "approval": ["approve", "manual", "input"],
    "vulnerability": ["vuln", "cve", "scan"],
    "domain": ["url", "dns", "fqdn"],
    "url": ["domain", "link", "uri"],
    "indicator": ["ioc", "observable", "artifact"],
    "ioc": ["indicator", "observable"],
    "ransomware": ["malware", "encrypt", "crypto"],
    "aws": ["amazon", "ec2", "s3", "iam"],
    "azure": ["microsoft", "entra", "defender"],
}


def _read(path: Path, default):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def _write(path: Path, data) -> None:
    _ws(DIR).mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1))
    tmp.replace(path)


# ---------------------------------------------------------------- synonyms

def synonyms() -> dict[str, list[str]]:
    data = _read(_ws(SYNONYMS_FILE), None)
    return DEFAULT_SYNONYMS if data is None else data


def set_synonyms(mapping: dict) -> dict[str, list[str]]:
    clean: dict[str, list[str]] = {}
    for key, values in mapping.items():
        key = re.sub(r"\s+", " ", str(key).strip().lower())[:60]
        if not key:
            continue
        if isinstance(values, str):
            values = values.split(",")
        words = [re.sub(r"\s+", " ", str(v).strip().lower())[:60] for v in values]
        words = sorted({w for w in words if w and w != key})[:20]
        if words:
            clean[key] = words
    if len(clean) > 2000:
        raise ValueError("too many synonym entries (max 2000)")
    with _lock:
        _write(_ws(SYNONYMS_FILE), clean)
    return clean


def reset_synonyms() -> dict[str, list[str]]:
    with _lock:
        _ws(SYNONYMS_FILE).unlink(missing_ok=True)
    return DEFAULT_SYNONYMS


def expand(question: str, terms: list[str]) -> list[list[str]]:
    """One group per search term: [term, synonyms...]. Phrases in the question add their synonyms
    to the matching term's group (or as an extra group)."""
    syn = synonyms()
    lowered = " " + re.sub(r"\s+", " ", question.lower()) + " "
    groups = []
    for term in terms:
        group = [term] + [w for w in syn.get(term.lower(), []) if w.lower() != term.lower()]
        groups.append(group)
    for phrase, words in syn.items():
        if " " in phrase and f" {phrase} " in lowered:
            first = phrase.split()[0]
            target = next((g for g in groups if g[0].lower() == first), None)
            if target is not None:
                target.extend(w for w in words if w not in target)
    return groups


# ---------------------------------------------------------------- saved searches

def saved() -> list[dict]:
    return _read(_ws(SAVED_FILE), [])


def save_search(name: str, query: str, filters: dict, by: str) -> dict:
    name, query = name.strip()[:80], query.strip()[:500]
    if not name or not query:
        raise ValueError("name and query are required")
    entry = {"id": uuid.uuid4().hex[:10], "name": name, "query": query,
             "filters": {k: str(v)[:120] for k, v in (filters or {}).items() if v},
             "created_by": by, "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
             "last_run": None, "last_count": None, "previous_count": None}
    with _lock:
        items = saved()
        if len(items) >= 500:
            raise ValueError("too many saved searches (max 500)")
        items.append(entry)
        _write(_ws(SAVED_FILE), items)
    return entry


def delete_search(search_id: str) -> bool:
    with _lock:
        items = saved()
        keep = [s for s in items if s["id"] != search_id]
        if len(keep) == len(items):
            return False
        _write(_ws(SAVED_FILE), keep)
    return True


def record_run(search_id: str, count: int) -> dict | None:
    with _lock:
        items = saved()
        for s in items:
            if s["id"] == search_id:
                s["previous_count"] = s.get("last_count")
                s["last_count"] = count
                s["last_run"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
                _write(_ws(SAVED_FILE), items)
                return s
    return None
