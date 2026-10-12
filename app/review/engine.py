"""Review one FortiSOAR playbook against the best-practice rules: findings, score, grade, verdict, summary.

Deterministic (no model calls). Evidence is masked so reviews never repeat secrets or full addresses.
"""
from __future__ import annotations

import json
import re
from collections import deque
from datetime import datetime, timezone

from ..ingest.playbooks import iri_tail, parse_playbook
from ..scrub import Scrubber
from .rules import SEVERITIES, all_rules, version

DESTRUCTIVE_VERBS = {"block", "isolate", "quarantine", "disable", "delete", "remove", "terminate", "kill", "lock",
                     "revoke", "contain", "shutdown", "suspend", "purge", "wipe", "ban", "deny", "reset", "expire",
                     "unshare", "detonate", "deactivate", "logoff", "signout"}
DESTRUCTIVE_TARGETS = ("blocklist", "blacklist", "quarantine", "isolation", "deny")
IP_RE = re.compile(r"(?<![\d.])((?:25[0-5]|2[0-4]\d|1?\d?\d)\.(?:25[0-5]|2[0-4]\d|1?\d?\d)\.(?:25[0-5]|2[0-4]\d|1?\d?\d)\.(?:25[0-5]|2[0-4]\d|1?\d?\d))(?![\d.])")
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
URL_RE = re.compile(r"https?://([A-Za-z0-9.-]+)")
IGNORED_IPS = {"0.0.0.0", "127.0.0.1", "255.255.255.255", "255.255.255.0"}
RECORD_IRI_RE = re.compile(r"/api/3/(?!picklists|picklist_names|workflow_steps|workflows|workflow_collections|appliances|"
                           r"workflow_step_types|connectors|attachments\b)([a-z_]+)/[0-9a-fA-F-]{36}")
GENERIC_STEP = re.compile(r"^(step( output| result)?\s*\d*|copy of .*|untitled.*|new step.*|action\s*\d*|set variable\s*\d*|"
                          r"connector\s*\d*|decision\s*\d*)$", re.I)
DEFAULT_PB_NAME = re.compile(r"^(new playbook.*|untitled.*|copy of .*|playbook\s*\d*|test.*)$", re.I)
SKIP_KEYS = {"mock_result", "_showJson", "__recommend"}


def _mask_ip(ip: str) -> str:
    a, b, *_ = ip.split(".")
    return f"{a}.{b}.x.x"


def _mask_email(email: str) -> str:
    user, domain = email.split("@", 1)
    return f"{user[:1]}***@{domain}"


def _strings(obj, skip=SKIP_KEYS):
    """All string values in arguments (excluding mock data)."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k not in skip:
                yield from _strings(v, skip)
    elif isinstance(obj, list):
        for v in obj:
            yield from _strings(v, skip)
    elif isinstance(obj, str):
        yield obj


def _truthy(v) -> bool:
    return v is True or (isinstance(v, str) and v.strip().lower() == "true")


def _delay_seconds(delay) -> int:
    if not isinstance(delay, dict):
        return 0
    units = {"weeks": 604800, "days": 86400, "hours": 3600, "minutes": 60, "seconds": 1}
    total = 0
    for unit, factor in units.items():
        try:
            total += int(float(delay.get(unit) or 0)) * factor
        except (TypeError, ValueError):
            pass
    return total


def _is_destructive(op: str, title: str = "", step_name: str = "") -> bool:
    words = [w for w in re.split(r"[\W_]+", f"{op}".lower()) if w]
    if not words:
        return False
    if words[0] in DESTRUCTIVE_VERBS:
        return True
    text = " ".join(words + re.split(r"\W+", title.lower()))
    return words[0] in ("add", "move", "set", "put") and any(t in text for t in DESTRUCTIVE_TARGETS)


class Context:
    """What the reviewer knows beyond the playbook itself."""

    def __init__(self, known_uuids: set[str] | None = None, answers: dict | None = None):
        self.known_uuids = known_uuids or set()
        self.answers = answers or {}


def _finding(rule: dict, detail: str, step: str | None = None, severity: str | None = None, evidence: str = "") -> dict:
    return {"rule": rule["id"], "title": rule["title"], "category": rule["category"],
            "severity": severity or rule["severity"], "step": step, "detail": detail,
            "evidence": evidence[:300], "suggestion": rule.get("suggestion", "")}


# ---------------------------------------------------------------- built-in checks

def _steps(model):
    return [s for s in model["steps"] if s["kind"] != "trigger"]


def check_sec01(rule, m, ctx):
    out = []
    for s in m["steps"]:
        if s["has_secret"]:
            keys = sorted(set(re.findall(r'"([^"]{1,60})": "[^"]*<(?:REDACTED|CREDS)>', s["args_text"])))[:5]
            out.append(_finding(rule, "Credential-like value written into the step" + (f" (fields: {', '.join(keys)})" if keys else "") + ".",
                                s["name"], evidence="value redacted"))
    return out


def check_sec02(rule, m, ctx):
    out = []
    for s in m["steps"]:
        ips, emails, hosts = set(), set(), set()
        for text in _strings(s["raw_args"]):
            if "{{" in text and len(text) < 40:
                continue
            ips |= {ip for ip in IP_RE.findall(text) if ip not in IGNORED_IPS and not ip.startswith("0.")}
            emails |= set(EMAIL_RE.findall(text))
            hosts |= {h.lower() for h in URL_RE.findall(text) if h.lower() not in ("localhost", "127.0.0.1")}
        if ips or emails or hosts:
            parts = []
            if ips:
                parts.append(f"{len(ips)} IP address(es)")
            if emails:
                parts.append(f"{len(emails)} email address(es)")
            if hosts:
                parts.append(f"{len(hosts)} URL host(s)")
            evidence = ", ".join([_mask_ip(i) for i in sorted(ips)[:3]] + [_mask_email(e) for e in sorted(emails)[:3]]
                                 + sorted(hosts)[:3])
            out.append(_finding(rule, "Literal " + ", ".join(parts) + " in the step arguments.", s["name"], evidence=evidence))
    return out


def check_sec03(rule, m, ctx):
    out = []
    approvals = {s["key"] for s in m["steps"] if s["kind"] == "manual_input"}
    for s in m["steps"]:
        if s["kind"] != "connector" or not _is_destructive(s["operation"] or "", s["raw_args"].get("operationTitle") or "", s["name"]):
            continue
        if m["ancestors"](s["key"]) & approvals:
            continue
        child = m["trigger_type"] == "referenced"
        out.append(_finding(
            rule, f"'{s['raw_args'].get('operationTitle') or s['operation']}' ({s['connector']}) runs without an approval step before it"
            + (" — this is a child playbook, so the approval may be in the calling playbook." if child else "."),
            s["name"], severity="low" if child else None))
    return out


def check_sec04(rule, m, ctx):
    return [_finding(rule, "Mock output is always on; the real connector is never called.", s["name"])
            for s in m["steps"] if _truthy(s["raw_args"].get("useMockOutput"))]


def check_sec05(rule, m, ctx):
    return [_finding(rule, "External responders can answer without authenticating.", s["name"])
            for s in m["steps"] if s["kind"] == "manual_input" and _truthy(s["raw_args"].get("unauthenticated_input"))]


def check_sec06(rule, m, ctx):
    return [_finding(rule, "Runs custom Python code.", s["name"]) for s in m["steps"] if s["kind"] == "code_snippet"][:5]


def check_sec07(rule, m, ctx):
    out = [_finding(rule, f"Uses the default configuration of {s['connector']}.", s["name"])
           for s in m["steps"] if s["kind"] == "connector" and "config" in s["raw_args"] and not s["raw_args"].get("config")]
    return out[:10]


def check_rel01(rule, m, ctx):
    out = []
    for s in m["steps"]:
        if s["kind"] != "connector":
            continue
        a = s["raw_args"]
        if not _truthy(a.get("ignore_errors")) and not a.get("do_until"):
            out.append(_finding(rule, f"{s['connector']}.{s['operation']} stops the playbook if the API call fails.", s["name"]))
    return out[:15]


def check_rel02(rule, m, ctx):
    if not m["trigger_key"]:
        return [_finding(rule, "The playbook has no trigger step, so nothing runs.", None, severity="critical")]
    reachable = m["reachable"]
    return [_finding(rule, "No route leads to this step.", s["name"]) for s in _steps(m) if s["key"] not in reachable][:15]


def check_rel03(rule, m, ctx):
    out = []
    for s in m["steps"]:
        if s["kind"] != "decision":
            continue
        conds = s["raw_args"].get("conditions") or []
        if isinstance(conds, list) and conds and not any(isinstance(c, dict) and c.get("default") for c in conds):
            out.append(_finding(rule, f"{len(conds)} condition(s), none marked as default.", s["name"]))
    return out


def check_rel04(rule, m, ctx):
    out = []
    for s in m["steps"]:
        du = s["raw_args"].get("do_until")
        if isinstance(du, dict) and du:
            try:
                retries = int(du.get("retries") or 0)
            except (TypeError, ValueError):
                retries = 0
            if retries <= 0 or retries > 10:
                out.append(_finding(rule, f"Retry loop with retries = {du.get('retries') or 'not set'}.", s["name"]))
    return out


def check_rel05(rule, m, ctx):
    out = []
    for s in m["steps"]:
        if s["kind"] != "manual_input":
            continue
        rm = s["raw_args"].get("response_mapping")
        if isinstance(rm, dict) and isinstance(rm.get("options"), list) and len(rm["options"]) < 2:
            out.append(_finding(rule, f"Only {len(rm['options'])} response option(s).", s["name"]))
    return out


def check_rel06(rule, m, ctx):
    if not ctx.known_uuids:
        return []
    return [_finding(rule, f"Calls playbook {c['uuid']} which isn't loaded.", m["step_names"].get(c["step"]))
            for c in m["calls"] if c["uuid"] not in ctx.known_uuids]


def check_rel07(rule, m, ctx):
    return [_finding(rule, "Child playbook chosen at run time.", s["name"])
            for s in m["steps"] if s["kind"] == "reference" and "{{" in str(s["raw_args"].get("workflowReference") or "")]


def check_perf01(rule, m, ctx):
    n = len(_steps(m))
    if n > 40:
        return [_finding(rule, f"{n} steps.", None)]
    if n > 25:
        return [_finding(rule, f"{n} steps.", None, severity="low")]
    return []


def check_perf02(rule, m, ctx):
    return [_finding(rule, "Debug mode is on.", None)] if _truthy(m["raw"].get("debug")) else []


def check_perf03(rule, m, ctx):
    if m["trigger_type"] != "record_event" or not m["trigger_key"]:
        return []
    args = m["by_key"][m["trigger_key"]]["raw_args"]
    fbt = args.get("fieldbasedtrigger")
    filters = fbt.get("filters") if isinstance(fbt, dict) else None
    if not filters:
        return [_finding(rule, f"Runs on every {'/'.join(m['modules']) or 'record'} create/update.", m["by_key"][m["trigger_key"]]["name"])]
    return []


def check_perf04(rule, m, ctx):
    out = []
    for s in m["steps"]:
        secs = _delay_seconds(s["raw_args"].get("delay"))
        if s["kind"] == "wait" and secs >= 3600:
            out.append(_finding(rule, f"Waits {secs // 3600} h {secs % 3600 // 60} min.", s["name"]))
    return out


def check_perf05(rule, m, ctx):
    out = []
    for s in m["steps"]:
        fe = s["raw_args"].get("for_each")
        if s["kind"] == "connector" and isinstance(fe, dict) and fe.get("item") and not _truthy(fe.get("parallel")):
            out.append(_finding(rule, f"Loops over {str(fe.get('item'))[:60]} one call at a time.", s["name"]))
    return out


def check_mnt01(rule, m, ctx):
    return [] if (m["raw"].get("description") or "").strip() else [_finding(rule, "No description.", None)]


def check_mnt02(rule, m, ctx):
    out, seen = [], {}
    for s in m["steps"]:
        name = s["name"].strip()
        if GENERIC_STEP.match(name):
            out.append(_finding(rule, "Generic step name.", name))
        seen[name.lower()] = seen.get(name.lower(), 0) + 1
    out += [_finding(rule, f"{n} steps share this name.", name) for name, n in seen.items() if n > 1]
    return out[:10]


def check_mnt03(rule, m, ctx):
    tags = m["raw"].get("recordTags") or m["raw"].get("tags") or []
    return [] if tags else [_finding(rule, "No tags.", None)]


def check_mnt04(rule, m, ctx):
    steps = _steps(m)
    if len(steps) <= 8:
        return []
    early = sorted(steps, key=lambda s: s["order"])[:3]
    if any(s["kind"] == "set_variable" for s in early):
        return []
    return [_finding(rule, f"{len(steps)} steps and no Set Variable step among the first three.", None)]


def check_mnt05(rule, m, ctx):
    name = (m["raw"].get("name") or "").strip()
    if len(name) < 6 or DEFAULT_PB_NAME.match(name):
        return [_finding(rule, f"Name '{name[:60]}'.", None)]
    return []


def check_mnt06(rule, m, ctx):
    steps = _steps(m)
    if len(steps) <= 5:
        return []
    missing = sum(1 for s in steps if not (s["description"] or "").strip())
    return [_finding(rule, f"{missing} of {len(steps)} steps have no description.", None)] if missing / len(steps) > 0.7 else []


def check_mnt07(rule, m, ctx):
    out = []
    for s in m["steps"]:
        modules = {mod for text in _strings(s["raw_args"]) for mod in RECORD_IRI_RE.findall(text)}
        if modules:
            out.append(_finding(rule, f"References specific {', '.join(sorted(modules))} record(s) by ID.", s["name"]))
    return out[:10]


def check_mnt08(rule, m, ctx):
    return [_finding(rule, "Exported as inactive.", None)] if m["raw"].get("isActive") is False else []


AUTO_CHECKS = {
    "SEC-01": check_sec01, "SEC-02": check_sec02, "SEC-03": check_sec03, "SEC-04": check_sec04, "SEC-05": check_sec05,
    "SEC-06": check_sec06, "SEC-07": check_sec07, "REL-01": check_rel01, "REL-02": check_rel02, "REL-03": check_rel03,
    "REL-04": check_rel04, "REL-05": check_rel05, "REL-06": check_rel06, "REL-07": check_rel07, "PERF-01": check_perf01,
    "PERF-02": check_perf02, "PERF-03": check_perf03, "PERF-04": check_perf04, "PERF-05": check_perf05,
    "MNT-01": check_mnt01, "MNT-02": check_mnt02, "MNT-03": check_mnt03, "MNT-04": check_mnt04, "MNT-05": check_mnt05,
    "MNT-06": check_mnt06, "MNT-07": check_mnt07, "MNT-08": check_mnt08,
}


# ---------------------------------------------------------------- custom (declarative) checks

def check_declarative(rule, m, ctx):
    p, kind = rule.get("params") or {}, rule["check"]
    flags = re.I if p.get("ignore_case", True) else 0
    if kind == "text_forbidden":
        pat = re.compile(p["pattern"], flags)
        out = []
        for s in m["steps"]:
            texts = []
            if p.get("scope", "args") in ("args", "all"):
                texts.append(s["args_text"])
            if p.get("scope", "args") in ("names", "all"):
                texts.append(s["name"])
            hit = next((mt for t in texts for mt in [pat.search(t[:20000])] if mt), None)
            if hit:
                out.append(_finding(rule, "Forbidden pattern found.", s["name"], evidence=hit.group(0)[:60]))
        return out[:10]
    if kind == "text_required":
        pat = re.compile(p["pattern"], flags)
        blob = " ".join([m["raw"].get("name") or "", m["raw"].get("description") or ""]
                        + [s["name"] + " " + s["args_text"] for s in m["steps"]])
        return [] if pat.search(blob[:500000]) else [_finding(rule, "Required pattern not found anywhere.", None)]
    if kind == "name_pattern":
        name = m["raw"].get("name") or ""
        return [] if re.search(p["pattern"], name, flags) else [_finding(rule, f"Name '{name[:60]}' doesn't match the convention.", None)]
    if kind == "step_name_pattern":
        pat = re.compile(p["pattern"], flags)
        return [_finding(rule, "Step name doesn't match the convention.", s["name"]) for s in _steps(m) if not pat.search(s["name"])][:10]
    if kind == "step_kind_required":
        return [] if any(s["kind"] == p["kind"] for s in m["steps"]) else [_finding(rule, f"No {p['kind'].replace('_', ' ')} step.", None)]
    if kind == "step_kind_forbidden":
        return [_finding(rule, f"Uses a {p['kind'].replace('_', ' ')} step.", s["name"]) for s in m["steps"] if s["kind"] == p["kind"]][:10]
    if kind == "connector_forbidden":
        banned = {c.strip().lower() for c in p["connectors"].split(",")}
        return [_finding(rule, f"Uses connector {s['connector']}.", s["name"]) for s in m["steps"]
                if s["connector"] and s["connector"].lower() in banned][:10]
    if kind == "max_steps":
        n = len(_steps(m))
        return [_finding(rule, f"{n} steps (limit {p['max']}).", None)] if n > p["max"] else []
    if kind == "field_required":
        value = {"description": m["raw"].get("description"), "tags": m["raw"].get("recordTags") or m["raw"].get("tags"),
                 "owners": m["raw"].get("owners"), "priority": m["raw"].get("priority")}[p["field"]]
        return [] if value else [_finding(rule, f"Playbook {p['field']} is not set.", None)]
    return []


# ---------------------------------------------------------------- model + scoring

def build_model(wf: dict, collection: str, scrubber: Scrubber) -> dict:
    pb = parse_playbook(wf, collection, scrubber)
    raw_steps = {}
    for i, raw in enumerate(wf.get("steps") or []):
        if isinstance(raw, dict):
            raw_steps[raw.get("uuid") or iri_tail(raw.get("@id")) or f"idx{i}"] = raw
    steps = []
    for st in pb.steps:
        raw = raw_steps.get(st.uuid, {})
        args = raw.get("arguments") if isinstance(raw.get("arguments"), dict) else {}
        steps.append({"key": st.key, "uuid": st.uuid, "name": st.name, "kind": st.kind, "description": st.description,
                      "connector": st.connector, "operation": st.operation, "has_secret": st.has_secret,
                      "args_text": json.dumps(scrubber.obj(args), sort_keys=True, default=str)[:20000],
                      "raw_args": args, "order": st.order})
    by_key = {s["key"]: s for s in steps}
    adj, radj = {}, {}
    for r in pb.routes:
        adj.setdefault(r["src"], []).append(r["dst"])
        radj.setdefault(r["dst"], []).append(r["src"])
    trigger_uuid = iri_tail(wf.get("triggerStep"))
    trigger_key = next((s["key"] for s in steps if s["uuid"] == trigger_uuid), None)
    reachable, queue = set(), deque([trigger_key] if trigger_key else [])
    while queue:
        k = queue.popleft()
        if k in reachable:
            continue
        reachable.add(k)
        queue.extend(adj.get(k, []))

    def ancestors(key: str) -> set[str]:
        seen, q = set(), deque(radj.get(key, []))
        while q:
            k = q.popleft()
            if k not in seen:
                seen.add(k)
                q.extend(radj.get(k, []))
        return seen

    return {"pb": pb, "raw": wf, "steps": steps, "by_key": by_key, "trigger_key": trigger_key, "reachable": reachable,
            "ancestors": ancestors, "trigger_type": pb.trigger_type, "modules": pb.modules, "calls": pb.calls,
            "step_names": {s["key"]: s["name"] for s in steps}}


def grade_of(score: int, has_critical: bool) -> str:
    grade = "A" if score >= 90 else "B" if score >= 80 else "C" if score >= 70 else "D" if score >= 60 else "F"
    if has_critical and grade in ("A", "B", "C"):
        grade = "D"
    return grade


def verdict_of(score: int, counts: dict) -> str:
    if counts.get("critical") or score < 70:
        return "Request changes"
    if counts.get("high") or score < 85:
        return "Approve with changes"
    return "Approve"


def review_workflow(wf: dict, collection: str, scrubber: Scrubber, ctx: Context | None = None,
                    rules: list[dict] | None = None) -> dict:
    ctx = ctx or Context()
    rules = rules if rules is not None else all_rules(include_disabled=False)
    m = build_model(wf, collection, scrubber)
    findings, rule_results, checklist = [], [], []
    for rule in rules:
        if not rule.get("enabled", True):
            continue
        if rule["check"] == "manual":
            ans = ctx.answers.get(rule["id"]) or {}
            item = {"rule": rule["id"], "title": rule["title"], "category": rule["category"], "severity": rule["severity"],
                    "question": (rule.get("params") or {}).get("question") or rule["description"],
                    "suggestion": rule.get("suggestion", ""), "answer": ans.get("answer"), "note": ans.get("note", ""),
                    "by": ans.get("by"), "at": ans.get("at"), "builtin": rule.get("builtin", False)}
            checklist.append(item)
            found = [_finding(rule, "Reviewer marked this check as failed." + (f" Note: {ans.get('note')}" if ans.get("note") else ""))] \
                if ans.get("answer") == "fail" else []
        elif rule["check"] == "auto":
            fn = AUTO_CHECKS.get(rule["id"])
            found = fn(rule, m, ctx) if fn else []
        else:
            try:
                found = check_declarative(rule, m, ctx)
            except re.error:
                found = []
        weight = float(rule.get("weight", 1.0))
        raw_penalty = sum(SEVERITIES.get(f["severity"], 0) for f in found) * weight
        penalty = min(raw_penalty, 2 * SEVERITIES.get(rule["severity"], 0) * weight)
        for f in found:
            f["penalty_share"] = round(penalty / len(found), 1) if found else 0
        findings += found
        rule_results.append({"id": rule["id"], "title": rule["title"], "category": rule["category"],
                             "severity": rule["severity"], "check": rule["check"], "findings": len(found),
                             "penalty": round(penalty, 1), "passed": not found,
                             "pending": rule["check"] == "manual" and not (ctx.answers.get(rule["id"]) or {}).get("answer")})
    total = sum(r["penalty"] for r in rule_results)
    score = max(0, round(100 - total))
    counts = {sev: sum(1 for f in findings if f["severity"] == sev) for sev in SEVERITIES}
    categories = {}
    for r in rule_results:
        categories[r["category"]] = categories.get(r["category"], 100) - r["penalty"]
    categories = {k: max(0, round(v)) for k, v in categories.items()}
    order = list(SEVERITIES)
    findings.sort(key=lambda f: (order.index(f["severity"]), f["rule"]))
    pending = sum(1 for c in checklist if not c["answer"])
    pb = m["pb"]
    connectors = sorted({s["connector"] for s in m["steps"] if s["connector"]})
    review = {
        "uuid": pb.uuid, "name": pb.name, "collection": collection, "description": pb.description,
        "trigger_type": pb.trigger_type, "modules": pb.modules, "steps": len(_steps(m)), "connectors": connectors,
        "score": score, "grade": grade_of(score, bool(counts["critical"])), "verdict": verdict_of(score, counts),
        "provisional": pending > 0, "manual_pending": pending, "categories": categories, "counts": counts,
        "findings": findings, "rules": rule_results, "checklist": checklist, "rules_version": version(),
        "reviewed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "signature": structure_signature(m),
    }
    review["summary"] = reviewer_summary(review)
    return review


def structure_signature(m: dict) -> dict:
    """Step names / kinds / operations, used to diff two versions of the same playbook."""
    return {s["name"]: f"{s['kind']}:{s['connector'] or ''}.{s['operation'] or ''}" for s in m["steps"]}


def diff_signatures(old: dict | None, new: dict | None) -> dict:
    old, new = old or {}, new or {}
    return {"added": sorted(set(new) - set(old)), "removed": sorted(set(old) - set(new)),
            "changed": sorted(k for k in set(old) & set(new) if old[k] != new[k])}


def reviewer_summary(r: dict) -> str:
    """A reviewer-style write-up of the findings (Markdown, deterministic)."""
    by_sev = {}
    for f in r["findings"]:
        by_sev.setdefault(f["severity"], {}).setdefault(f["title"], []).append(f)

    def bullet(title, fs):
        steps = [f["step"] for f in fs if f["step"]]
        where = f" — {len(steps)} step(s): {', '.join(steps[:3])}{'…' if len(steps) > 3 else ''}" if steps else ""
        return f"- **{title}**{where}. {fs[0]['suggestion']}"

    trig = r["trigger_type"].replace("_", " ") + (f" on {', '.join(r['modules'])}" if r["modules"] else "")
    lines = [f"**Verdict: {r['verdict']}** — score {r['score']}/100, grade {r['grade']}"
             + (" (provisional: manual checks pending)" if r["provisional"] else "") + ".", "",
             f"'{r['name']}' is a {trig} playbook with {r['steps']} steps"
             + (f" using {', '.join(r['connectors'][:6])}" if r["connectors"] else "") + "."]
    pending_cats = {c["category"] for c in r["checklist"] if not c["answer"]}
    strong = [c for c, v in r["categories"].items() if v >= 100 and c not in pending_cats]
    if strong:
        lines.append(f"Clean areas: {', '.join(strong)}.")
    must = [bullet(t, fs) for sev in ("critical", "high") for t, fs in by_sev.get(sev, {}).items()]
    should = [bullet(t, fs) for t, fs in by_sev.get("medium", {}).items()]
    nice = [bullet(t, fs) for sev in ("low", "info") for t, fs in by_sev.get(sev, {}).items()]
    if must:
        lines += ["", "**Must fix before production**", *must]
    if should:
        lines += ["", "**Should fix**", *should]
    if nice:
        lines += ["", "**Nice to have**", *nice]
    if not (must or should or nice):
        lines += ["", "No automated findings. Complete the manual checklist to finalise the review."]
    if r["checklist"]:
        done = sum(1 for c in r["checklist"] if c["answer"])
        lines += ["", f"Manual checklist: {done} of {len(r['checklist'])} answered."]
    return "\n".join(lines)
