"""The Develop chat: gather the use case -> propose a design (workflow + sequence diagrams) -> revise on request
-> on confirmation, develop the playbook, review it and write the customer document.

Guided mode (default) makes no model calls: questions with suggested answers drawn from the knowledge graph
(real connectors and operations), free text matched against them, and change requests understood from simple
phrases ("add an approval", "remove the email step", "notify on Teams", "trigger on update"...). The design
panel edits the same spec directly. AI mode (opt-in, 1 answer request per message) turns plain-English
descriptions and change requests into spec edits.
"""
from __future__ import annotations

import copy
import json
import re

from . import catalog, generate
from . import spec as S
from . import store

QUESTIONS = ["usecase", "trigger", "scope", "indicators", "enrich", "verdict", "contain", "approval", "notify", "record_updates", "name"]
DESTRUCTIVE_WORDS = re.compile(r"\b(block|isolate|quarantine|contain|disable|suspend|lock|ban|purge|delete|remove|kill|reset)\b", re.I)


def _msg(role: str, type_: str, **data) -> dict:
    return {"role": role, "type": type_, "at": store.now(), **data}


# ---------------------------------------------------------------- understanding free text

def parse_usecase(text: str) -> dict:
    t = text.lower()
    pre: dict = {}
    pre["module"] = "incidents" if re.search(r"\bincident", t) and not re.search(r"\balert", t) else \
        "indicators" if re.search(r"\b(indicator|ioc)s?\b", t) and not re.search(r"\balert", t) else "alerts"
    pre["trigger"] = "manual" if re.search(r"\b(manual(ly)?|button|on demand|analyst runs?)\b", t) else \
        "on_update" if re.search(r"\b(updated?|changes?|modified)\b", t) else \
        "referenced" if re.search(r"\b(child|sub-?playbook|called by)\b", t) else "on_create"
    inds = [k for k, words in USECASE_INDICATORS.items() if any(re.search(rf"\b{re.escape(w)}s?\b", t) for w in words)]
    if re.search(r"\bphish", t):
        inds += [i for i in ("email", "url") if i not in inds]
    if re.search(r"\b(malware|ransomware)\b", t) and "hash" not in inds:
        inds.append("hash")
    if re.search(r"\b(compromised|impossible travel|account takeover)\b", t) and "user" not in inds:
        inds.append("user")
    pre["indicators"] = [i for i in inds if i in catalog.INDICATORS][:4] or ["ip"]
    mentioned = [c["name"] for c in catalog.find_connectors(text)]
    pre["mentioned"] = mentioned
    roles = mention_roles(text, mentioned)
    pre["enrich"] = _clause_ops("enrich", pre["indicators"], [c for c in mentioned if roles.get(c) == "enrich"])
    pre["contain"] = _clause_ops("contain", pre["indicators"], [c for c in mentioned if roles.get(c) == "contain"]) \
        if DESTRUCTIVE_WORDS.search(t) else []
    if DESTRUCTIVE_WORDS.search(t) and not pre["contain"]:
        pre["contain"] = [f"{s['connector']}.{s['operation']}" for s in catalog.suggest("contain", pre["indicators"], limit=1)]
    pre["approval"] = not re.search(r"\b(no approval|without approval|fully automatic|automatically block)\b", t)
    pre["notify"] = [k for k, pat in NOTIFY_PATTERNS if re.search(pat, t)]
    updates = ["note"]
    if re.search(r"\b(close|false positive|benign)\b", t):
        updates.append("close_benign")
    if re.search(r"\b(escalat|create (an )?incident|promote)\w*", t) and pre["module"] == "alerts":
        updates.append("escalate")
    if re.search(r"\b(severity|priority)\b", t):
        updates.append("severity")
    pre["record_updates"] = updates
    pre["tickets"] = [f"{s['connector']}.{s['operation']}" for s in catalog.suggest("ticket", [], limit=10)
                      if s["connector"] in mentioned][:1]
    conds = []
    m = re.search(r"\b(critical|high|medium|low)(?:\s*(?:or|and|/|,)\s*(critical|high|medium|low))?\s+(?:severity|alerts?|incidents?)", t)
    if m:
        conds.append({"field": "severity", "operator": "in", "value": ",".join(x.title() for x in m.groups() if x)})
    pre["conditions"] = conds
    return pre


# indicator words in a use-case description ("email the SOC" is a notification, not an email indicator)
USECASE_INDICATORS = {**catalog.INDICATORS,
                      "email": ("sender", "email address", "from address", "suspicious email", "phishing email", "spoofed"),
                      "host": ("host", "endpoint", "device", "computer", "machine", "workstation", "server"),
                      "user": ("user account", "username", "upn", "compromised user", "disable user", "disable the user", "account")}
NOTIFY_PATTERNS = (("email", r"\b(e-?mail|mail) (the |to )?(soc|team|analysts?|owner|manager|admins?|security|us|them)\b|\b(by|via|over) e-?mail\b|\bsmtp\b|\bnotify\b[^.]*\be-?mail\b"),
                   ("teams", r"\bteams\b"), ("slack", r"\bslack\b"))
PURPOSE_WORDS = [("contain", DESTRUCTIVE_WORDS), ("notify", re.compile(r"\b(notify|email|e-mail|message|tell|inform|post|alert the)\b", re.I)),
                 ("ticket", re.compile(r"\b(ticket|incident in|raise|open a)\b", re.I)),
                 ("enrich", re.compile(r"\b(check|enrich|look ?up|reputation|query|scan|verify|analy[sz]e|investigate)\b", re.I))]


def mention_roles(text: str, mentioned: list[str]) -> dict[str, str]:
    """What each named connector is for, judged by the words in the same clause ("…block it on FortiGate…")."""
    clauses = re.split(r"[,;.]|\bthen\b|\band\b|\bafter\b|\bif\b", text, flags=re.I)
    roles = {}
    for con in mentioned:
        c = catalog.get(con) or {"name": con, "label": con}
        names = {con.replace("-", " "), c["label"].lower()} | {w for w in re.split(r"[\s-]+", c["label"].lower()) if len(w) >= 5 and w not in catalog.GENERIC_WORDS}
        for clause in clauses:
            low = clause.lower().replace("-", " ")
            if any(n and n in low for n in names):
                roles[con] = next((p for p, pat in PURPOSE_WORDS if pat.search(clause)), "enrich")
                CLAUSES[con] = clause
                break
        roles.setdefault(con, "enrich")
    return roles


CLAUSES: dict[str, str] = {}


def _clause_ops(purpose: str, indicators: list[str], connectors: list[str]) -> list[str]:
    """One operation per named connector, preferring the indicator named in the same clause."""
    out = []
    for con in connectors:
        clause = CLAUSES.get(con, "")
        local = [k for k, words in USECASE_INDICATORS.items() if any(re.search(rf"\b{re.escape(w)}s?\b", clause.lower()) for w in words)]
        for inds in [x for x in ([i for i in local if i in indicators] or local, indicators) if x]:
            hit = next((b for b in catalog.suggest(purpose, inds, limit=30, prefer=[con]) if b["connector"] == con), None)
            if hit:
                out.append(f"{hit['connector']}.{hit['operation']}")
                break
    return out


def _pick_ops(purpose: str, indicators: list[str], mentioned: list[str], only_mentioned: bool = False, limit: int = 3) -> list[str]:
    picked = []
    for con in mentioned:
        best = catalog.suggest(purpose, indicators, limit=20, prefer=[con])
        best = [b for b in best if b["connector"] == con]
        for b in best[:2]:
            picked.append(f"{b['connector']}.{b['operation']}")
    if not picked and not only_mentioned:
        picked = [f"{s['connector']}.{s['operation']}" for s in catalog.suggest(purpose, indicators, limit=limit)]
    return picked[:limit + 1]


def _op_label(value: str) -> str:
    con, op = value.split(".", 1)
    c = catalog.get(con) or {}
    o = catalog.operation(con, op) or {}
    return f"{c.get('label', con)} — {o.get('title', op)}{_src(o.get('source') or c.get('source'))}"


def _src(source: str | None) -> str:
    """Where a suggestion comes from, shown outside the Fortinet workspace."""
    from .. import workspace

    if workspace.has("github") or not source:
        return ""
    return " · Fortinet GitHub" if source == "fortinet" else f" · {workspace.info(source)['label'] if workspace.exists(source) else source}"


# ---------------------------------------------------------------- questions

def question(key: str, s: dict) -> dict | None:
    a, pre = s["answers"], s["prefill"]
    inds = a.get("indicators") or pre.get("indicators") or ["ip"]
    module = a.get("module") or pre.get("module") or "alerts"
    if key == "usecase":
        return _msg("bot", "question", key=key, multi=False, allow_text=True, chips=[], default=[],
                    text="Let's design a playbook. **Describe the use case in detail**: what should start it, what it should check, "
                         "what it should do when something is malicious, who must approve, and who should be told.\n\n"
                         "_Example: \"When a new alert has a suspicious source IP, check it on VirusTotal, and if it's malicious block it on "
                         "FortiGate after SOC approval, close benign alerts, and email the SOC.\"_")
    if key == "trigger":
        chips = []
        for mod, label in S.MODULES.items():
            for t in ("on_create", "on_update", "manual"):
                chips.append({"label": f"{label}: {S.TRIGGERS[t].lower()}", "value": f"{mod}:{t}"})
        chips.append({"label": "Called by another playbook", "value": f"{module}:referenced"})
        default = [f"{module}:{a.get('trigger') or pre.get('trigger') or 'on_create'}"]
        return _msg("bot", "question", key=key, multi=False, allow_text=False, chips=chips, default=default,
                    text="**When should it run?**")
    if key == "scope":
        chips = [{"label": "Only High and Critical severity", "value": "severity:High,Critical"},
                 {"label": "Only Critical severity", "value": "severity:Critical"},
                 {"label": "Every record (no filter)", "value": "none"}]
        default = [f"{c['field']}:{c['value']}" for c in pre.get("conditions") or []] or ["severity:High,Critical"]
        return _msg("bot", "question", key=key, multi=False, allow_text=True, chips=chips, default=default,
                    text="**Which records are in scope?** Running only on relevant records keeps the queue fast. "
                         "You can also type a condition, e.g. _source is FortiSIEM_.")
    if key == "indicators":
        chips = [{"label": label, "value": k} for k, label in catalog.INDICATOR_LABELS.items()]
        return _msg("bot", "question", key=key, multi=True, allow_text=False, chips=chips, default=pre.get("indicators") or ["ip"],
                    text=f"**What should it investigate on the {S.MODULES.get(module, 'record').lower()}?**")
    if key == "enrich":
        sug = catalog.suggest("enrich", inds, limit=8, prefer=pre.get("mentioned") or [])
        chips = [{"label": f"{x['label']} — {x['title']}{_src(x.get('source'))}", "value": f"{x['connector']}.{x['operation']}"} for x in sug]
        default = [v for v in (pre.get("enrich") or []) if v in {c["value"] for c in chips}] or [c["value"] for c in chips[:1]]
        for v in pre.get("enrich") or []:
            if v not in {c["value"] for c in chips}:
                chips.insert(0, {"label": _op_label(v), "value": v})
                default.append(v)
        chips.append({"label": "No enrichment", "value": "none"})
        return _msg("bot", "question", key=key, multi=True, allow_text=True, chips=chips, default=list(dict.fromkeys(default)),
                    text="**Which sources should enrich the indicators?** These are the operations Fortinet's own playbooks use most. "
                         "Type a connector name to add another.")
    if key == "verdict":
        return _msg("bot", "question", key=key, multi=False, allow_text=False, default=["any"], chips=[
            {"label": "Malicious if any source flags it", "value": "any"},
            {"label": "Malicious only if 2+ sources agree", "value": "two"}], text="**When is it malicious?**")
    if key == "contain":
        sug = catalog.suggest("contain", inds, limit=8, prefer=pre.get("mentioned") or [])
        chips = [{"label": f"{x['label']} — {x['title']}{_src(x.get('source'))}", "value": f"{x['connector']}.{x['operation']}"} for x in sug]
        default = list(pre.get("contain") or [])
        for v in default:
            if v not in {c["value"] for c in chips}:
                chips.insert(0, {"label": _op_label(v), "value": v})
        chips.append({"label": "No response actions (enrich and report only)", "value": "none"})
        return _msg("bot", "question", key=key, multi=True, allow_text=True, chips=chips, default=default or ["none"],
                    text="**What should it do when the indicator is malicious?** Response actions change other systems, so the design "
                         "adds an approval step and a rollback playbook where the connector supports undo.")
    if key == "approval":
        return _msg("bot", "question", key=key, multi=False, allow_text=True, default=["yes"] if pre.get("approval", True) else ["no"], chips=[
            {"label": "Yes — an analyst approves first (recommended)", "value": "yes"},
            {"label": "No — run automatically", "value": "no"}],
            text="**Should an analyst approve the response actions?** You can name the approving team, e.g. _SOC Tier 2_.")
    if key == "notify":
        return _msg("bot", "question", key=key, multi=True, allow_text=False, default=pre.get("notify") or ["email"], chips=[
            {"label": "Email (SMTP)", "value": "email"}, {"label": "Microsoft Teams", "value": "teams"},
            {"label": "Slack", "value": "slack"}, {"label": "No notification", "value": "none"}], text="**Who should be told, and how?**")
    if key == "record_updates":
        chips = [{"label": "Add the analysis summary to the record", "value": "note"},
                 {"label": "Raise severity when malicious", "value": "severity"}]
        if module == "alerts":
            chips.insert(1, {"label": "Close as benign when nothing is malicious", "value": "close_benign"})
            chips.append({"label": "Create an incident when malicious", "value": "escalate"})
        for x in catalog.suggest("ticket", [], limit=3):
            chips.append({"label": f"Open a ticket: {x['label']} — {x['title']}", "value": f"ticket:{x['connector']}.{x['operation']}"})
        default = list(pre.get("record_updates") or ["note"]) + [f"ticket:{t}" for t in pre.get("tickets") or []]
        return _msg("bot", "question", key=key, multi=True, allow_text=False, chips=chips, default=default,
                    text="**How should the record be updated?**")
    if key == "name":
        draft = S.build(_answers_with_defaults(s))
        return _msg("bot", "question", key=key, multi=False, allow_text=True, default=[draft["name"]],
                    chips=[{"label": draft["name"], "value": draft["name"]}],
                    text="**Last one: what should the playbook be called?** Type a name or keep the suggestion.")
    return None


def _skip(key: str, s: dict) -> bool:
    a = s["answers"]
    if key == "verdict":
        return len(a.get("enrich") or []) < 2
    if key == "approval":
        return not a.get("contain")
    if key == "scope":
        return (a.get("trigger") or "") == "referenced"
    return False


def _answers_with_defaults(s: dict) -> dict:
    a, pre = dict(s["answers"]), s["prefill"]
    a.setdefault("module", pre.get("module", "alerts"))
    a.setdefault("trigger", pre.get("trigger", "on_create"))
    a.setdefault("conditions", pre.get("conditions") or [])
    a.setdefault("indicators", pre.get("indicators") or ["ip"])
    a.setdefault("enrich", pre.get("enrich") or [f"{x['connector']}.{x['operation']}" for x in catalog.suggest("enrich", a["indicators"], limit=1)])
    a.setdefault("verdict", "any")
    a.setdefault("contain", pre.get("contain") or [])
    a.setdefault("approval", pre.get("approval", True))
    a.setdefault("notify", pre.get("notify") or [])
    a.setdefault("record_updates", pre.get("record_updates") or ["note"])
    a.setdefault("tickets", pre.get("tickets") or [])
    return a


def record_answer(s: dict, key: str, values: list[str], text: str) -> str | None:
    """Store one answer. Returns an error message if it couldn't be understood."""
    a, values, text = s["answers"], [v for v in values if v], (text or "").strip()
    if key == "usecase":
        if len(text) < 15:
            return "Please describe the use case in a sentence or two (what triggers it, what to check, what to do)."
        a["usecase"] = text
        s["prefill"] = parse_usecase(text)
        s["title"] = text[:70]
    elif key == "trigger":
        if not values:
            return "Pick one of the options."
        mod, trig = values[0].split(":", 1)
        a["module"], a["trigger"] = mod, trig
    elif key == "scope":
        conds = []
        for v in values:
            if v != "none" and ":" in v:
                f, val = v.split(":", 1)
                conds.append({"field": f, "operator": "in", "value": val})
        if text:
            m = re.match(r"(\w+)\s+(?:is|=|equals|eq|contains|like)\s+(.+)", text, re.I)
            if not m:
                return "Write the condition as _field is value_, e.g. _source is FortiSIEM_ or _severity is Critical_."
            field, value = m.group(1), m.group(2).strip().strip("'\"")
            conds.append({"field": field, "operator": "in" if field in S.PICKLIST_FIELDS else ("like" if "contain" in text.lower() or "like" in text.lower() else "eq"), "value": value})
        a["conditions"] = conds
    elif key == "indicators":
        a["indicators"] = [v for v in values if v in catalog.INDICATORS] or ["ip"]
    elif key in ("enrich", "contain"):
        picked = [v for v in values if v != "none" and "." in v]
        if text:
            mentioned = [c["name"] for c in catalog.find_connectors(text)]
            extra = _pick_ops("enrich" if key == "enrich" else "contain", a.get("indicators") or ["ip"], mentioned, only_mentioned=True)
            if not extra and mentioned:
                return f"{', '.join(mentioned)} has no {'enrichment' if key == 'enrich' else 'response'} operation for these indicators."
            if not extra:
                return "I couldn't find that connector. Try its product name, e.g. _AbuseIPDB_ or _FortiEDR_."
            picked += extra
        a[key] = list(dict.fromkeys(picked))
    elif key == "verdict":
        a["verdict"] = values[0] if values else "any"
    elif key == "approval":
        a["approval"] = (values[0] if values else "yes") != "no"
        if text and not values:
            a["approval"], a["approver"] = True, text[:60]
    elif key == "notify":
        a["notify"] = [v for v in values if v in S.NOTIFY]
    elif key == "record_updates":
        a["record_updates"] = [v for v in values if not v.startswith("ticket:")]
        a["tickets"] = [v[7:] for v in values if v.startswith("ticket:")]
    elif key == "name":
        a["name"] = (text or (values[0] if values else ""))[:120]
    return None


def next_question(s: dict, after: str | None) -> str | None:
    i = QUESTIONS.index(after) + 1 if after in QUESTIONS else 0
    while i < len(QUESTIONS):
        if not _skip(QUESTIONS[i], s):
            return QUESTIONS[i]
        i += 1
    return None


# ---------------------------------------------------------------- proposals

def proposal(s: dict, note: str, scrubber) -> dict:
    """Draft the playbook from the spec and show it for confirmation (diagrams + steps + draft review)."""
    from .. import diagrams
    from ..exports import glance_diagram
    from ..review.engine import review_workflow

    spec = s["spec"]
    doc = generate.export(spec, s["pb_uuid"], s["coll_uuid"])
    wf = doc["data"][0]["workflows"][0]
    flow = generate.flow_of(wf, scrubber)
    r = review_workflow(wf, spec.get("collection") or "Developed", scrubber)
    version = len(s["versions"]) + 1
    s["versions"].append({"n": version, "at": store.now(), "note": note, "spec": copy.deepcopy(spec)})
    findings = [f for f in r["findings"] if f["severity"] not in ("info",)]
    required = [c["name"] for c in spec["config"] if c.get("required") and not c.get("value")]
    return _msg("bot", "proposal", version=version, note=note,
                summary="\n".join(S.summary_lines(spec)),
                flowchart=diagrams.flowchart(flow), sequence=diagrams.sequence(flow), glance=glance_diagram(flow),
                review={"score": r["score"], "grade": r["grade"], "verdict": r["verdict"], "counts": r["counts"],
                        "findings": [{"severity": f["severity"], "title": f["title"], "step": f["step"], "detail": f["detail"]} for f in findings[:12]]},
                rollback=len(doc["data"][0]["workflows"]) > 1, required_config=required,
                problems=generate.validate(doc))


# ---------------------------------------------------------------- change requests (guided)

def guided_change(spec: dict, text: str) -> tuple[dict, list[str]]:
    t = text.lower().strip()
    notes: list[str] = []
    new = spec

    def edit(e):
        nonlocal new
        new, m = S.apply_edit(new, e)
        notes.append(m)

    if re.search(r"\b(no|remove|without|skip|drop)\b.*\bapprov", t):
        edit({"op": "approval", "on": False})
    elif re.search(r"\b(add|require|need|with|include)\b.*\bapprov", t):
        edit({"op": "approval", "on": True})
    m = re.search(r"\b(?:rename|call)\b(?: (?:the )?playbook| it)?(?: to| as)? [\"']?(.+?)[\"']?$", text, re.I)
    if m and not notes:
        edit({"op": "meta", "name": m.group(1).strip()})
    for t_key, pat in (("on_update", r"\b(trigger|run|start)\w*\b.*\b(update|updated|changes?)\b"), ("on_create", r"\b(trigger|run|start)\w*\b.*\b(create|created|new)\b"),
                       ("manual", r"\b(manual|button|on demand)\b"), ("referenced", r"\b(called by|child playbook|sub-?playbook)\b")):
        if re.search(pat, t):
            module = next((m_ for m_ in S.MODULES if re.search(rf"\b{m_[:-1]}s?\b", t)), None)
            edit({"op": "trigger", "type": t_key, **({"module": module} if module else {})})
            break
    m = re.search(r"\bonly (?:when|if|for)\b.*\b(severity|type|status)\b.*?\b(?:is|=|in)\b\s*(.+)$", t)
    if m:
        field, values = m.group(1), ",".join(v.strip().title() for v in re.split(r",|\bor\b|\band\b|/", m.group(2)) if v.strip())
        new = copy.deepcopy(new)
        existing = next((st for st in new["steps"] if (st.get("scope") or {}).get("field") == field), None)
        if existing:
            existing["scope"]["values"] = values
            for c in new["config"]:
                if c["name"] == f"scope_{field}":
                    c["value"] = values
        else:
            new["steps"].insert(0, S.scope_step(field, values))
            new = S.normalise(new)
            for c in new["config"]:
                if c["name"] == f"scope_{field}":
                    c["value"] = values
        notes.append(f"In scope only when {field} is {values}.")
    channels = [k for k, pat in (("email", r"\b(e-?mail|smtp)\b"), ("teams", r"\bteams\b"), ("slack", r"\bslack\b")) if re.search(pat, t)]
    if channels and re.search(r"\b(notify|notification|send|tell|alert|message|post)\b", t) and not re.search(r"\b(remove|no|don't|stop)\b", t):
        for ch in channels:
            con, op = S.NOTIFY[ch]
            if not any(st.get("connector") == con for st in new["steps"]):
                edit({"op": "add_connector", "kind": "notify", "connector": con, "operation": op})
    m = re.search(r"\b(?:remove|delete|drop|don't|do not|no)\b\s+(?:the\s+)?(.+?)(?:\s+step)?$", t)
    if m and not notes:
        target = m.group(1)
        match = _find_step(new, target)
        if match:
            edit({"op": "remove", "id": match["id"]})
    m = re.search(r"\bwait (\d+) ?(min|minute|minutes|m)\b", t)
    if m:
        edit({"op": "add_step", "kind": "wait", "name": f"Wait {m.group(1)} minutes", "delay_minutes": int(m.group(1))})
    if re.search(r"\b(escalate|create (an )?incident)\b", t) and not any(st["kind"] == "create_record" for st in new["steps"]):
        new = copy.deepcopy(new)
        new["steps"].insert(S._insert_position(new, "create_record"), {
            "id": S.new_id(), "kind": "create_record", "name": "Escalate to incident", "module": "incidents",
            "fields": {"name": "{{vars.input.records[0].name}}", "description": "{{vars.summary}}"}})
        new = S.normalise(new)
        notes.append("Added “Escalate to incident”.")
    if not notes:  # connectors mentioned: add them for the purpose the wording suggests
        mentioned = [c["name"] for c in catalog.find_connectors(text)]
        if not mentioned and re.search(r"\b(add|use|also|enrich with|check (?:on|with))\b", t):
            word = re.sub(r"^(add|use|also|enrich with|check on|check with)\s+", "", t).strip(" .")
            notes.append(f"!I couldn't find a connector called “{word[:40]}” among the connectors published on Fortinet's GitHub. "
                         "Use the design panel's connector search to pick an available one.")
            return spec, notes
        purpose = "contain" if DESTRUCTIVE_WORDS.search(t) and not re.search(r"\b(check|enrich|look ?up|reputation)\b", t) else \
            "notify" if re.search(r"\b(notify|send|post|message)\b", t) else "ticket" if re.search(r"\bticket\b", t) else "enrich"
        for value in _pick_ops(purpose, new["indicators"], mentioned, only_mentioned=True, limit=1):
            con, op = value.split(".", 1)
            if not any(st.get("connector") == con and st.get("operation") == op for st in new["steps"]):
                edit({"op": "add_connector", "kind": purpose, "connector": con, "operation": op})
        if purpose == "contain" and notes and new["safety"].get("approval", True):
            edit({"op": "approval", "on": True})
    return new, [n for n in notes if n and n != "No change needed."]


def _find_step(spec: dict, text: str) -> dict | None:
    words = set(re.findall(r"[a-z0-9]+", text.lower())) - {"the", "step", "a", "an"}
    best, score = None, 0
    for st in spec["steps"]:
        hay = set(re.findall(r"[a-z0-9]+", f"{st['name']} {st.get('connector') or ''} {st['kind']}".lower().replace("_", " ")))
        n = len(words & hay)
        if n > score:
            best, score = st, n
    return best


# ---------------------------------------------------------------- AI mode

AI_SYSTEM = """You design FortiSOAR playbooks. You receive the current design SPEC (JSON), a CATALOG of allowed connector
operations, and the user's message. Return ONLY JSON: {"reply": "<short reply to the user>", "spec": <the full updated spec>}.
Rules:
- Keep the spec schema exactly (trigger, indicators, steps, safety, config, name, description, tags, collection).
- Step kinds: extract, enrich, verdict, decision, approval, contain, notify, update_record, create_record, ticket, wait, note.
- Connector steps (enrich, contain, notify, ticket) must use a connector + operation pair from CATALOG; never invent one.
- Keep best practices: an approval before contain steps unless the user explicitly refuses it; a verdict and a decision after enrichment;
  decisions/approvals have "no" set to a step id or "end".
- Keep existing step ids for steps you keep; give new steps a new short id like "s1a2b3".
- If the request is unclear, keep the spec unchanged and ask one clarifying question in "reply"."""


def ai_turn(s: dict, text: str, svc) -> tuple[dict | None, str]:
    from ..providers import parse_json

    spec = s.get("spec") or S.build(_answers_with_defaults({**s, "answers": {**s["answers"], "usecase": s["answers"].get("usecase") or text}}))
    inds = spec.get("indicators") or ["ip"]
    mentioned = [c["name"] for c in catalog.find_connectors(text + " " + (s["answers"].get("usecase") or ""))]
    options = []
    for purpose in ("enrich", "contain", "notify", "ticket"):
        options += catalog.suggest(purpose, inds if purpose in ("enrich", "contain") else [], limit=8, prefer=mentioned)
    cat = "\n".join(f"- {o['connector']}.{o['operation']}: {o['title']}" for o in options)
    slim = {k: v for k, v in spec.items() if k != "use_case"}
    for st in slim["steps"]:
        st.pop("extra_config", None)
    prompt = f"SPEC:\n{json.dumps(slim)[:12000]}\n\nCATALOG:\n{cat}\n\nUSER: {text}"
    reply = parse_json(svc.llm.complete(AI_SYSTEM, prompt, json_mode=True))
    if not isinstance(reply, dict):
        return None, "The model's reply wasn't usable; nothing changed."
    new = reply.get("spec")
    msg = str(reply.get("reply") or "Updated the design.")[:1500]
    if not isinstance(new, dict) or not isinstance(new.get("steps"), list):
        return None, msg
    allowed = {f"{o['connector']}.{o['operation']}" for o in options} | {f"{st.get('connector')}.{st.get('operation')}" for st in spec["steps"] if st.get("connector")}
    dropped, steps = [], []
    for st in new["steps"]:
        if not isinstance(st, dict) or st.get("kind") not in S.STEP_KINDS:
            continue
        if st.get("connector"):
            key = f"{st['connector']}.{st.get('operation')}"
            if key not in allowed and not catalog.operation(st["connector"], st.get("operation") or ""):
                dropped.append(key)
                continue
            old = next((x for x in spec["steps"] if x["id"] == st.get("id")), None)
            if not old or old.get("operation") != st.get("operation"):
                st = {**S._connector_step(st["kind"], st["connector"], st["operation"], new.get("indicators") or inds), "id": st.get("id") or S.new_id(),
                      **({"name": st["name"]} if st.get("name") else {})}
            else:
                st = {**old, **{k: v for k, v in st.items() if k in ("name", "params")}}
        st.setdefault("id", S.new_id())
        st.setdefault("name", st["kind"].title())
        steps.append(st)
    merged = {**spec, **{k: new[k] for k in ("name", "description", "collection", "tags", "trigger", "indicators", "safety") if k in new}, "steps": steps}
    merged["indicators"] = [i for i in merged.get("indicators") or inds if i in catalog.INDICATORS] or inds
    merged["fields"] = {i: spec["fields"].get(i) or S.DEFAULT_FIELDS.get(merged["trigger"].get("module", "alerts"), {}).get(i, i) for i in merged["indicators"]}
    if dropped:
        msg += f"\n\n(Ignored operations that aren't in the catalog: {', '.join(dropped)}.)"
    return S.normalise(merged), msg


# ---------------------------------------------------------------- the turn

def start(s: dict) -> None:
    s["messages"].append(question("usecase", s))


def handle(s: dict, svc, text: str = "", values: list[str] | None = None, ai: bool = False, skip: bool = False) -> list[dict]:
    """One user turn. Returns the new messages (also appended to the session)."""
    values = values or []
    new: list[dict] = []
    shown = text or ", ".join(_value_label(s, v) for v in values) or ("Use the suggested answers" if skip else "")
    if shown:
        new.append(_msg("user", "text", text=shown))
    if s["stage"] == "intake":
        key = s["question"]
        if ai and key == "usecase" and text:
            s["answers"]["usecase"] = text
            s["prefill"] = parse_usecase(text)
            s["title"] = text[:70]
            spec, reply = ai_turn(s, f"Design a playbook for this use case: {text}", svc)
            s["spec"] = spec or S.build(_answers_with_defaults(s))
            s["stage"] = "design"
            new.append(_msg("bot", "text", text=reply))
            new.append(proposal(s, "First design (AI)", svc.scrubber))
        else:
            if skip:
                if key == "usecase":
                    new.append(_msg("bot", "text", text="I need the use case first."))
                    s["messages"] += new
                    return new
                s["answers"] = _answers_with_defaults(s)
                nxt = None
            else:
                err = record_answer(s, key, values, text)
                if err:
                    new.append(_msg("bot", "text", text=err))
                    new.append(question(key, s))
                    s["messages"] += new
                    return new
                if key == "usecase":
                    pre = s["prefill"]
                    found = []
                    if pre.get("mentioned"):
                        found.append("connectors: " + ", ".join((catalog.get(c) or {}).get("label", c) for c in pre["mentioned"]))
                    found.append("indicators: " + ", ".join(catalog.INDICATOR_LABELS[i] for i in pre["indicators"]))
                    new.append(_msg("bot", "text", text="Got it. From your description I picked up " + "; ".join(found)
                                    + ". I'll suggest answers for the next questions; adjust them or press **Continue**. "
                                      "You can also jump straight to the design with **Use suggestions for the rest**."))
                nxt = next_question(s, key)
            if nxt:
                s["question"] = nxt
                new.append(question(nxt, s))
            else:
                s["answers"] = _answers_with_defaults(s)
                s["spec"] = S.build(s["answers"])
                s["stage"] = "design"
                s["title"] = s["spec"]["name"]
                new.append(_msg("bot", "text", text="Here's the proposed design. Check the workflow and sequence diagrams. "
                                                    "**Confirm** to develop it, or tell me what to change (e.g. _add AbuseIPDB_, "
                                                    "_remove the email step_, _trigger on update_, _only when severity is Critical_)."))
                new.append(proposal(s, "First design", svc.scrubber))
    else:  # design or developed: change requests
        if not text:
            new.append(_msg("bot", "text", text="Tell me what to change, or use the design panel."))
        else:
            if ai:
                spec, reply = ai_turn(s, text, svc)
                notes = [reply] if spec is None else []
                if spec is not None:
                    s["spec"] = spec
                    new.append(_msg("bot", "text", text=reply))
                    new.append(proposal(s, f"AI: {text[:80]}", svc.scrubber))
                    s["stage"] = "design"
                else:
                    new.append(_msg("bot", "text", text=notes[0]))
            else:
                spec, notes = guided_change(s["spec"], text)
                if notes and notes[0].startswith("!"):
                    new.append(_msg("bot", "text", text=notes[0][1:]))
                elif notes:
                    s["spec"] = spec
                    s["stage"] = "design"
                    new.append(_msg("bot", "text", text=" ".join(notes)))
                    new.append(proposal(s, text[:80], svc.scrubber))
                else:
                    new.append(_msg("bot", "text", text=(
                        "I couldn't turn that into a change. Try phrases like _add AbuseIPDB_, _block on FortiGate_, _notify on Teams_, "
                        "_remove the email step_, _no approval_, _trigger on update_, _only when severity is Critical_, _wait 10 minutes_, "
                        "_rename to …_. Or edit it in the design panel, or switch on **AI** to describe the change in your own words "
                        "(1 AI request).")))
    s["messages"] += new
    return new


def _value_label(s: dict, v: str) -> str:
    q = question(s["question"], s) if s["stage"] == "intake" else None
    if q:
        hit = next((c["label"] for c in q.get("chips") or [] if c["value"] == v), None)
        if hit:
            return hit
    return v


def panel_edit(s: dict, edit: dict, scrubber) -> list[dict]:
    if not s.get("spec"):
        raise ValueError("finish the use-case questions first")
    spec, note = S.apply_edit(s["spec"], edit)
    s["spec"] = spec
    s["stage"] = "design"
    new = [_msg("bot", "text", text=f"Design panel: {note}"), proposal(s, f"Panel: {note}", scrubber)]
    s["messages"] += new
    return new


# ---------------------------------------------------------------- develop + customer document

def develop(s: dict, svc, by: str) -> dict:
    from ..review.engine import review_workflow

    if not s.get("spec"):
        raise ValueError("there's no confirmed design yet")
    spec = s["spec"]
    doc = generate.export(spec, s["pb_uuid"], s["coll_uuid"])
    problems = generate.validate(doc)
    if problems:
        raise ValueError("the design can't be generated: " + "; ".join(problems))
    reviews = [review_workflow(wf, spec.get("collection") or "Developed", svc.scrubber) for wf in doc["data"][0]["workflows"]]
    version = len(s["generated"]) + 1
    d = store.files_dir(s["id"], version)
    fname = re.sub(r"[^\w.-]+", "-", spec["name"]).strip("-")[:60] or "playbook"
    (d / "playbook.json").write_text(json.dumps(doc, indent=2))
    entry = {"version": version, "design_version": len(s["versions"]), "at": store.now(), "by": by, "file": fname,
             "workflows": [wf["name"] for wf in doc["data"][0]["workflows"]],
             "review": {"score": reviews[0]["score"], "grade": reviews[0]["grade"], "verdict": reviews[0]["verdict"],
                        "counts": reviews[0]["counts"], "summary": reviews[0]["summary"],
                        "findings": [{k: f[k] for k in ("severity", "rule", "title", "step", "detail", "suggestion")} for f in reviews[0]["findings"]],
                        "others": [{"name": r["name"], "score": r["score"], "grade": r["grade"]} for r in reviews[1:]]}}
    s["generated"].append(entry)
    s["stage"] = "developed"
    (d / "document.md").write_text(customer_markdown(s, svc))
    msg = _msg("bot", "developed", **entry)
    s["messages"] += [_msg("user", "text", text=f"Confirm design v{len(s['versions'])} and develop"), msg]
    return msg


def customer_markdown(s: dict, svc) -> str:
    from .. import diagrams
    from ..exports import glance_diagram

    spec, gen = s["spec"], (s["generated"] or [None])[-1]
    doc = generate.export(spec, s["pb_uuid"], s["coll_uuid"])
    wf = doc["data"][0]["workflows"][0]
    flow = generate.flow_of(wf, svc.scrubber)
    t = spec["trigger"]
    steps = [st for st in spec["steps"] if not st.get("branch_only")]
    connectors = {}
    for st in spec["steps"]:
        if st.get("connector"):
            c = catalog.get(st["connector"]) or {}
            connectors.setdefault(st["connector"], {"label": c.get("label", st["connector"]), "version": st.get("version") or c.get("version"), "ops": []})
            connectors[st["connector"]]["ops"].append(st.get("title") or st["operation"])
    contain = [st for st in spec["steps"] if st["kind"] == "contain"]
    out = [f"# {spec['name']}", "",
           f"_Playbook design document · version {gen['version'] if gen else 'draft'} · {store.now()[:10]}_", "",
           "## 1. Summary", "", spec.get("description") or s["answers"].get("usecase") or "", "",
           f"- **Runs:** {S.TRIGGERS.get(t['type'], t['type'])} on **{S.MODULES.get(t['module'], t['module'])}** records"
           + (" (" + "; ".join(f"{c['field']} {c['operator']} {c.get('value', '')}" for c in t.get('conditions') or []) + ")" if t.get("conditions")
              else f" that have {' or '.join(sorted(set(spec['fields'].values())))} set" if t["type"] in ("on_create", "on_update") and spec.get("fields") else ""),
           f"- **Investigates:** {', '.join(catalog.INDICATOR_LABELS[i] for i in spec['indicators'])}",
           f"- **Systems involved:** {', '.join(v['label'] for v in connectors.values()) or 'FortiSOAR only'}",
           f"- **Human approval:** {'yes — before any response action' if any(st['kind'] == 'approval' for st in spec['steps']) else 'no'}", "",
           "## 2. Flow at a glance", "", "```mermaid", glance_diagram(flow), "```", "",
           "## 3. Detailed workflow", "", "```mermaid", diagrams.flowchart(flow), "```", "",
           "## 4. Sequence", "", "```mermaid", diagrams.sequence(flow), "```", "",
           "## 5. Step by step", "", "| # | Step | What it does | System |", "|---|---|---|---|"]
    explain = {"extract": "Reads the indicators from the record (fields set in Configuration)", "verdict": "Counts sources reporting the indicators as malicious and writes a summary",
               "decision": "Branches on the result", "approval": "Pauses for an analyst to approve or reject", "update_record": "Updates the triggering record",
               "create_record": "Creates a linked record", "wait": "Waits before continuing", "note": "Records a note"}
    out.append("| 0 | Configuration | Holds every environment-specific value | FortiSOAR |")
    for i, st in enumerate(steps, 1):
        what = (catalog.operation(st["connector"], st["operation"]) or {}).get("description") or st.get("title") if st.get("connector") else explain.get(st["kind"], st["kind"])
        if st.get("scope"):
            what = f"Continues only when {st['scope']['field']} is one of: {st['scope']['values']}"
        system = (connectors.get(st.get("connector")) or {}).get("label") or "FortiSOAR"
        out.append(f"| {i} | {st['name']} | {str(what or '').replace('|', '/')[:160]} | {system} |")
    for st in spec["steps"]:
        if st.get("branch_only"):
            out.append(f"| ↳ | {st['name']} | Runs on the other branch: {explain.get(st['kind'], st['kind'])} | FortiSOAR |")
    out += ["", "## 6. Safety controls", "",
            "- **Approval:** " + ("response actions run only after an analyst approves; rejecting ends the playbook." if any(st['kind'] == 'approval' for st in spec['steps']) else "none (actions are automatic)."),
            "- **Error handling:** every connector step continues on error; the verdict and summary record each source's status.",
            "- **Configuration first:** all environment-specific values live in one Configuration step.",
            "- **Safe import:** the playbook is imported **inactive**; activate it after the staging test.",
            "- **No secrets:** credentials stay in the connector configurations; nothing is hard-coded."]
    if contain:
        rb = [st for st in contain if st.get("rollback")]
        out.append("- **Rollback:** " + (f"a manual playbook *Rollback - {spec['name']}* undoes: " + ", ".join(f"{st['name']} → {st['rollback']['title']}" for st in rb) if rb
                                          else "the connectors have no undo operation; follow the manual procedure in section 9."))
    out += ["", "## 7. Prerequisites", "", "| Connector | Version | Operations |", "|---|---|---|"]
    out += [f"| {v['label']} | {v['version'] or '–'} | {', '.join(dict.fromkeys(v['ops']))} |" for v in connectors.values()] or ["| – | – | – |"]
    out += ["", "**Configuration values to set after import** (Configuration step):", "", "| Name | Value | Purpose |", "|---|---|---|"]
    out += [f"| `{c['name']}` | {c.get('value') or ('**REQUIRED**' if c.get('required') else '_blank = default_')} | {c['description']} |" for c in spec["config"]]
    out += ["", "## 8. Test plan (staging)", "",
            "1. Import the JSON (Automation → Playbooks → Import) into a **staging** FortiSOAR; check the connectors above are installed and configured.",
            "2. Fill in the Configuration step; assign the approver team on the approval step.",
            f"3. Create a test {S.MODULES.get(t['module'], 'record').lower()} with a **known-bad** test indicator and confirm: enrichment runs, the verdict is *Malicious*, the approval appears, the response action runs on a test object only, the record is updated and the notification arrives.",
            f"4. Create a test {S.MODULES.get(t['module'], 'record').lower()} with a **known-good** indicator and confirm it takes the benign path.",
            "5. Break one connector (e.g. wrong configuration) and confirm the playbook still finishes and reports the failed source.",
            "6. Activate in production; watch the first runs in the Executed Playbook Logs."]
    out += ["", "## 9. Rollback and support", "",
            f"- To undo response actions on a record, run *Rollback - {spec['name']}* from it" + (" (included)." if any(st.get('rollback') for st in contain) else " — not available for these connectors; undo the actions in the product console."),
            "- To stop the automation, deactivate the playbook; nothing else depends on it.", ""]
    if gen:
        r = gen["review"]
        out += ["## 10. Quality review", "", f"Automated best-practice review: **{r['score']}/100, grade {r['grade']} — {r['verdict']}**.", ""]
        out += [f"- [{f['severity']}] {f['title']}" + (f" — {f['step']}" if f.get("step") else "") + f": {f['detail']}" for f in r["findings"]] or ["- No findings."]
        out += ["", "Before production, the reviewer's manual checklist must be completed: tested in staging, owner and failure notification set, "
                "matches the documented use case, rollback plan agreed.", ""]
    out += ["## 11. Version history", "", "| Version | Date | Changed by | Summary of changes |", "|---|---|---|---|"]
    for v in s["versions"]:
        out.append(f"| design {v['n']} | {v['at'][:16].replace('T', ' ')} | {s.get('created_by') or '-'} | {str(v['note']).replace('|', '/')[:120]} |")
    for g in s["generated"]:
        out.append(f"| build {g['version']} | {g['at'][:16].replace('T', ' ')} | {g.get('by') or '-'} | Developed from design {g['design_version']}: score {g['review']['score']} ({g['review']['grade']}) |")
    return "\n".join(out) + "\n"
