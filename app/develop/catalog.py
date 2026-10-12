"""Connector catalog for playbook design: every downloaded connector's operations, categories and parameters
(from connector-*/…/info.json), classified by purpose so the designer can suggest real operations."""
from __future__ import annotations

import json
import re
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent / "data" / "fortinet" / "github"
_lock = threading.Lock()
_cache: dict = {"sig": None, "connectors": {}}

# indicator / data types the designer works with -> words that identify matching operation parameters / titles
INDICATORS = {
    "ip": ("ip", "ip address", "ipv4", "ip_address", "source ip", "address"),
    "url": ("url", "uri", "link"),
    "domain": ("domain", "fqdn", "hostname"),
    "hash": ("hash", "file hash", "md5", "sha1", "sha256", "filehash", "file reputation", "file report", "malware sample"),
    "email": ("email", "sender", "mail", "message"),
    "user": ("user", "username", "account", "upn", "samaccountname", "identity"),
    "host": ("host", "endpoint", "device", "computer", "agent", "machine", "collector", "sensor"),
}
INDICATOR_LABELS = {"ip": "IP address", "url": "URL", "domain": "Domain", "hash": "File hash", "email": "Email / sender",
                    "user": "User account", "host": "Host / endpoint"}

PURPOSES = {
    "enrich": re.compile(r"^(get|lookup|query|search|fetch|list|check|analy[sz]e|scan|submit|detonate|retrieve|whois|reputation)", re.I),
    "contain": re.compile(r"^(block|isolate|quarantine|disable|suspend|contain|ban|deny|add_?(ip|url|domain|hash|address|indicator)s?_?(to_)?(block|deny|black)|"
                          r"add_.*(block|deny|black|ban)|lock|revoke|reset_?password|terminate|kill|unshare|remove|delete|purge)", re.I),
    "notify": re.compile(r"(send|post|notify).*(mail|message|notification|chat)|^send_|^post_message", re.I),
    "ticket": re.compile(r"^(create|open)_?(ticket|issue|incident|case|record|task)", re.I),
}
NOTIFY_CONNECTORS = {"email": "smtp", "teams": "microsoft-teams", "slack": "slack"}


def _signature() -> tuple:
    if not ROOT.exists():
        return ()
    return tuple(sorted(d.name for d in ROOT.iterdir() if d.name.startswith("connector-")))


ROLLBACK = re.compile(r"^(unblock|enable|unquarantine|unisolate|release|allow|restore|unlock|unban|remove_?(ip|url|domain|host|address|hash)s?_?from_?(block|deny|black|quarantine))", re.I)


def _purpose(op: dict) -> str | None:
    name, cat = op.get("operation", ""), (op.get("category") or "").lower()
    if ROLLBACK.search(name):
        return "rollback"
    if PURPOSES["notify"].search(name):
        return "notify"
    if PURPOSES["ticket"].search(name):
        return "ticket"
    if cat in ("containment", "remediation") or PURPOSES["contain"].search(name):
        return "contain"
    if cat == "investigation" or PURPOSES["enrich"].search(name) or re.search(r"(reputation|report|lookup|details|whois|search)", name, re.I):
        return "enrich"
    return None


def _match(text: str) -> list[str]:
    text = text.lower().replace("_", " ")
    return [k for k, words in INDICATORS.items() if any(re.search(rf"\b{re.escape(w)}s?\b", text) for w in words)]


def _indicators(op: dict) -> list[str]:
    """Indicator types an operation works on: from its name / title, else from its parameters."""
    primary = _match(f"{op.get('operation', '')} {op.get('title', '')}")
    return primary or _match(" ".join(f"{p.get('name', '')} {p.get('title', '')}" for p in op.get("parameters") or []))


def connectors() -> dict[str, dict]:
    """The catalog for the current workspace. Fortinet: Fortinet's connectors. Other workspaces: their own connectors
    (learned from the playbooks uploaded to that workspace) first, then Fortinet's GitHub catalog as a fallback.
    Every connector and operation says where it came from (source: marlink / fortinet / ...)."""
    from .. import workspace

    ws = workspace.current()
    base = fortinet_connectors()
    if workspace.has("github", ws):
        return base
    local, sig = _workspace_connectors(ws)
    key = (ws, id(base), sig)
    with _lock:
        if _ws_cache.get("key") == key:
            return _ws_cache["connectors"]
    merged = {}
    for name, c in base.items():
        merged[name] = {**c, "source": "fortinet", "operations": [{**o, "source": "fortinet"} for o in c["operations"]]}
    for name, lc in local.items():
        if name in merged:
            m = merged[name]
            m["source"], m["used_local"] = ws, lc["used_local"]
            known = {o["operation"]: o for o in m["operations"]}
            for op in lc["operations"]:
                if op["operation"] in known:
                    known[op["operation"]].update(source=ws, used_local=op["used_local"])
                else:
                    m["operations"].append(op)
            if lc.get("version"):
                m["version"] = lc["version"]
        else:
            merged[name] = lc
    with _lock:
        _ws_cache.update(key=key, connectors=merged)
    return merged


_ws_cache: dict = {}


def _workspace_connectors(ws: str) -> tuple[dict, tuple]:
    """Connectors / operations / parameters used by the playbooks uploaded to a workspace."""
    from collections import Counter, defaultdict

    from ..review import sources

    srcs = sources.sources()
    sig = tuple((x["id"], x.get("last_synced")) for x in srcs)
    used, titles, params, versions = Counter(), {}, defaultdict(set), defaultdict(Counter)
    for src in srcs:
        for _f, _c, wf in sources._workflows(src["id"]):
            for st in wf.get("steps") or []:
                a = st.get("arguments") if isinstance(st, dict) else None
                if not isinstance(a, dict) or not a.get("connector") or not a.get("operation"):
                    continue
                key = (str(a["connector"]), str(a["operation"]))
                used[key] += 1
                titles.setdefault(key, a.get("operationTitle") or a["operation"])
                if isinstance(a.get("params"), dict):
                    params[key] |= {k for k in a["params"] if isinstance(k, str)}
                if a.get("version"):
                    versions[key[0]][str(a["version"])] += 1
    out: dict = {}
    for (con, opname), n in used.items():
        if con in ("cyops_utilities", "code-snippet"):
            continue
        c = out.setdefault(con, {"name": con, "label": LABELS.get(con) or _label(con), "version": None, "category": "",
                                 "description": "", "repo": None, "operations": [], "source": ws, "used_local": 0})
        if versions[con]:
            c["version"] = versions[con].most_common(1)[0][0]
        op = {"operation": opname, "title": titles[(con, opname)], "category": "", "description": "", "source": ws, "used_local": n,
              "parameters": [{"name": k, "title": k, "required": False, "type": "text", "options": None, "value": None}
                             for k in sorted(params[(con, opname)])]}
        op["purpose"], op["indicators"] = _purpose(op), _indicators(op)
        c["operations"].append(op)
        c["used_local"] += n
    return out, sig


def fortinet_connectors() -> dict[str, dict]:
    """Fortinet's catalog: connector repos' info.json plus how Fortinet's own playbooks use them."""
    sig = _signature()
    with _lock:
        if _cache["sig"] == sig:
            return _cache["connectors"]
        out = {}
        for repo in sorted(ROOT.glob("connector-*")):
            for info_path in repo.rglob("info.json"):
                try:
                    info = json.loads(info_path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                if not isinstance(info, dict) or not info.get("name") or not isinstance(info.get("operations"), list):
                    continue
                ops = []
                for o in info["operations"]:
                    if not isinstance(o, dict) or not o.get("operation") or o.get("enabled") is False:
                        continue
                    params = [{"name": p.get("name"), "title": p.get("title") or p.get("name"), "required": bool(p.get("required")),
                               "type": p.get("type"), "options": p.get("options") if isinstance(p.get("options"), list) else None,
                               "value": p.get("value")}
                              for p in o.get("parameters") or [] if isinstance(p, dict) and p.get("name") and p.get("visible", True) is not False]
                    op = {"operation": o["operation"], "title": o.get("title") or o["operation"], "category": o.get("category") or "",
                          "description": (o.get("description") or "")[:300], "parameters": params}
                    op["purpose"] = _purpose(op)
                    op["indicators"] = _indicators(op)
                    ops.append(op)
                out[info["name"]] = {"name": info["name"], "label": info.get("label") or info["name"], "version": info.get("version"),
                                     "category": info.get("category") or "", "description": (info.get("description") or "")[:300],
                                     "repo": repo.name, "operations": ops}
                break
        _merge_usage(out)
        _cache.update(sig=sig, connectors=out)
        return out


LABELS = {"virustotal": "VirusTotal", "fortigate-firewall": "FortiGate Firewall", "smtp": "SMTP", "activedirectory": "Active Directory",
          "microsoft-teams": "Microsoft Teams", "slack": "Slack", "exchange": "Microsoft Exchange", "servicenow": "ServiceNow",
          "jira": "Jira", "abuseipdb": "AbuseIPDB", "urlscan-io": "urlscan.io", "splunk": "Splunk", "qradar": "IBM QRadar",
          "elasticsearch": "Elasticsearch", "github": "GitHub", "openai": "OpenAI", "sentinelone": "SentinelOne", "ssh": "SSH",
          "azure-active-directory": "Azure Active Directory", "microsoft-sentinel": "Microsoft Sentinel", "threatstream": "Anomali ThreatStream"}


def _label(name: str) -> str:
    words = name.replace("_", "-").split("-")
    return " ".join(("Forti" + w[5:].upper() if w.startswith("forti") and len(w) > 5 and w != "fortinet"
                     else w.upper() if len(w) <= 3 else w.title()) for w in words)


def _merge_usage(out: dict) -> None:
    """Add how Fortinet's playbooks actually use each connector: usage counts per operation, and connectors /
    operations that have no info.json on disk (their parameter names come from the steps)."""
    from collections import Counter, defaultdict

    from ..review.github_index import _iter_workflows

    used, titles, params, versions = Counter(), {}, defaultdict(set), defaultdict(Counter)
    for _repo, _file, _coll, _i, wf in _iter_workflows():
        for st in wf.get("steps") or []:
            a = st.get("arguments") if isinstance(st, dict) else None
            if not isinstance(a, dict) or not a.get("connector") or not a.get("operation"):
                continue
            key = (str(a["connector"]), str(a["operation"]))
            used[key] += 1
            titles.setdefault(key, a.get("operationTitle") or a["operation"])
            if isinstance(a.get("params"), dict):
                params[key] |= {k for k in a["params"] if isinstance(k, str)}
            if a.get("version"):
                versions[key[0]][str(a["version"])] += 1
    for (con, opname), n in used.items():
        if con in ("cyops_utilities", "code-snippet"):
            continue
        c = out.setdefault(con, {"name": con, "label": LABELS.get(con) or _label(con), "version": None, "category": "",
                                 "description": "", "repo": None, "operations": [], "from_usage": True})
        if not c.get("version") and versions[con]:
            c["version"] = versions[con].most_common(1)[0][0]
        op = next((o for o in c["operations"] if o["operation"] == opname), None)
        if op is None:
            op = {"operation": opname, "title": titles[(con, opname)], "category": "", "description": "",
                  "parameters": [{"name": k, "title": k, "required": False, "type": "text", "options": None, "value": None}
                                 for k in sorted(params[(con, opname)])]}
            op["purpose"] = _purpose(op)
            op["indicators"] = _indicators(op)
            c["operations"].append(op)
        op["used"] = n
        c["used"] = c.get("used", 0) + n


def get(name: str) -> dict | None:
    return connectors().get(name)


def operation(connector: str, op: str) -> dict | None:
    c = get(connector)
    return next((o for o in c["operations"] if o["operation"] == op), None) if c else None


GENERIC_WORDS = {"fortinet", "microsoft", "google", "azure", "cisco", "palo", "alto", "amazon", "aws", "ibm", "threat", "intelligence",
                 "security", "cloud", "firewall", "email", "mail", "server", "commands", "utilities", "connector", "service", "advanced",
                 "premium", "enterprise", "platform", "network", "endpoint", "manager", "management", "center", "api", "data", "active",
                 "directory", "graph", "defender", "online", "open", "free", "check", "point", "block", "list", "reputation",
                 "notify", "alert", "alerts", "incident", "report", "search", "lookup", "query", "scan", "files", "users", "hosts"}


def find_connectors(text: str, limit: int = 8) -> list[dict]:
    """Connectors named in free text: full name / label, or a distinctive word of it (e.g. 'FortiGate')."""
    t = " " + re.sub(r"[^a-z0-9]+", " ", text.lower()) + " "
    norm = lambda n: re.sub(r"[^a-z0-9]+", " ", n.lower()).strip()
    hits = []
    for c in connectors().values():
        names = {norm(c["name"]), norm(c["label"]), norm(re.sub(r"\s*\(.*?\)", "", c["label"]))}
        score = max((len(n) * 2 for n in names if len(n) >= 4 and f" {n} " in t), default=0)
        if not score:
            words = {w for n in names for w in n.split() if len(w) >= 5 and w not in GENERIC_WORDS}
            score = max((len(w) for w in words if f" {w} " in t), default=0)
        if score:
            hits.append((score + min(c.get("used", 0), 50) / 100, c))
    hits.sort(key=lambda x: -x[0])
    best, out = {}, []
    for _, c in hits:  # one connector per distinctive word (prefer the most used)
        key = norm(c["label"]).split()[0]
        if key not in best:
            best[key] = c
            out.append(c)
    return out[:limit]


def usage_counts(graph) -> dict[str, int]:
    """How many Fortinet playbooks use each connector (popularity for suggestions)."""
    try:
        return {r["name"]: r["n"] for r in graph.read(
            "MATCH (c:Connector)<-[:OF]-(:ConnectorOperation)<-[:USES]-(:Step)<-[:HAS_STEP]-(p:Playbook) "
            "RETURN c.name AS name, count(DISTINCT p) AS n")}
    except Exception:
        return {}


# well-known choices first when nothing is in the graph yet
POPULAR = ["virustotal", "fortinet-fortiguard-threat-intelligence", "abuseipdb", "urlscan-io", "alienvault-otx", "ibm-xforce",
           "fortigate-firewall", "fortinet-fortiedr", "activedirectory", "azure-active-directory", "microsoft-defender-for-endpoint",
           "exchange", "microsoft-graph-mail", "smtp", "microsoft-teams", "slack", "servicenow", "jira"]


def suggest(purpose: str, indicators: list[str], graph=None, limit: int = 8, prefer: list[str] | None = None) -> list[dict]:
    """[{connector, label, operation, title, indicators}] best operations for a purpose and indicator types."""
    counts = usage_counts(graph) if graph is not None else {}
    prefer = prefer or []
    rows = []
    for c in connectors().values():
        for op in c["operations"]:
            if op["purpose"] != purpose:
                continue
            match = set(op["indicators"]) & set(indicators) if indicators else set(op["indicators"])
            if indicators and not match:
                continue
            rank = (0 if c["name"] in prefer else 1, 0 if op.get("used_local") else 1, -min(op.get("used", 0), 5), -len(match), -(counts.get(c["name"], 0)),
                    POPULAR.index(c["name"]) if c["name"] in POPULAR else 99, -op.get("used", 0), len(op["operation"]))
            rows.append((rank, {"connector": c["name"], "label": c["label"], "operation": op["operation"], "title": op["title"],
                                "indicators": sorted(match), "version": c["version"], "source": op.get("source") or c.get("source") or "fortinet"}))
    rows.sort(key=lambda x: x[0])
    seen, out = set(), []
    for _, r in rows:
        key = (r["connector"], tuple(r["indicators"]))
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
        if len(out) >= limit:
            break
    return out


def search(q: str, limit: int = 40) -> list[dict]:
    q = q.lower().strip()
    out = []
    for c in connectors().values():
        if not q or q in c["name"] or q in c["label"].lower() or q in c["category"].lower():
            out.append({"name": c["name"], "label": c["label"], "version": c["version"], "category": c["category"], "operations": len(c["operations"]),
                        "source": c.get("source") or "fortinet"})
    return sorted(out, key=lambda c: (c["source"] == "fortinet", not c["name"].startswith(q), c["label"]))[:limit]


def rollback_for(connector: str, op: str) -> dict | None:
    """The undo operation for a containment action (block_ip -> unblock_ip), if the connector has one."""
    c = get(connector)
    if not c:
        return None
    base = re.sub(r"^(block|quarantine|isolate|disable|suspend|lock|ban|add)_?", "", op)
    for o in c["operations"]:
        if o.get("purpose") == "rollback" and (base and base in o["operation"]):
            return {"connector": connector, "operation": o["operation"], "title": o["title"]}
    return None
