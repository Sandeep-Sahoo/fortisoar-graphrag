"""Playbook design spec: the editable description of a playbook that the Develop chat builds and the generator
turns into a FortiSOAR import file.

spec = {
  name, description, collection, tags, use_case,
  trigger: {type: on_create|on_update|manual|referenced, module, conditions: [{field, operator, value}], button},
  indicators: [ip|url|domain|hash|email|user|host],
  fields: {indicator: record field},           # where each indicator is read from on the triggering record
  config: [{name, value, description, required}],
  steps: [{id, kind, name, ...}],               # in execution order (the Configuration step is added automatically)
  safety: {approval: bool, approver: str, verdict: any|two},
}
step kinds: extract · enrich · verdict · decision · approval · contain · notify · update_record · create_record · ticket · wait · note
decision / approval steps: yes -> the next step; no -> step id or "end" (a labelled end step).
"""
from __future__ import annotations

import copy
import re
import uuid

from . import catalog

MODULES = {"alerts": "Alert", "incidents": "Incident", "indicators": "Indicator"}
TRIGGERS = {"on_create": "When a record is created", "on_update": "When a record is updated", "manual": "Manual (button on the record)",
            "referenced": "Called by another playbook"}
STEP_KINDS = ("extract", "enrich", "verdict", "decision", "approval", "contain", "notify", "update_record", "create_record",
              "ticket", "wait", "note")
DEFAULT_FIELDS = {
    "alerts": {"ip": "sourceIp", "url": "url", "domain": "domain", "hash": "fileHash", "email": "emailFrom", "user": "userName", "host": "computerName"},
    "incidents": {"ip": "sourceIp", "url": "url", "domain": "domain", "hash": "fileHash", "email": "emailFrom", "user": "affectedUser", "host": "affectedHost"},
    "indicators": {k: "value" for k in catalog.INDICATORS},
}
NOTIFY = {"email": ("smtp", "send_email_new"), "teams": ("microsoft-teams", "send_message"), "slack": ("slack", "send_message")}
OPERATORS = ("eq", "neq", "in", "like", "contains", "changed", "isnull")
# picklist fields hold instance-specific IDs, so conditions on them become a "scope check" step on their display value
PICKLIST_FIELDS = {"severity", "type", "status", "state", "typeofindicator", "reputation", "tlp", "priority", "category"}


def scope_step(field: str, values: str) -> dict:
    return {"id": new_id(), "kind": "decision", "name": f"Check {field} is in scope", "scope": {"field": field, "values": values},
            "condition": f"{{{{ (vars.input.records[0].{field}.itemValue | default('')) in (vars.scope_{field} | string).split(',') }}}}",
            "yes_label": "In scope", "no_label": "Out of scope", "no": "end", "end_label": "Out of scope"}


def new_id() -> str:
    return "s" + uuid.uuid4().hex[:6]


def var(indicator: str) -> str:
    return f"{indicator}_value"


def snake(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_") or "step"


def empty_spec() -> dict:
    return {"name": "", "description": "", "collection": "Developed Playbooks", "tags": ["developed"], "use_case": "",
            "trigger": {"type": "on_create", "module": "alerts", "conditions": [], "button": "Run"},
            "indicators": [], "fields": {}, "config": [], "steps": [],
            "safety": {"approval": True, "approver": "SOC Team", "verdict": "any"}}


# ---------------------------------------------------------------- parameter mapping

PARAM_HINTS = [  # (words in a parameter name/title, indicator) -> value from the extracted variables
    (("ip", "ip address", "ip_address", "ipv4", "address"), "ip"), (("url", "uri", "link"), "url"),
    (("domain", "fqdn", "hostname"), "domain"), (("hash", "md5", "sha1", "sha256", "filehash", "file hash"), "hash"),
    (("email", "sender", "from address", "mail address"), "email"), (("user", "username", "account", "upn", "samaccountname"), "user"),
    (("host", "endpoint", "computer", "device", "agent", "machine"), "host"),
]


def fill_params(connector: str, operation: str, indicators: list[str], purpose: str) -> tuple[dict, list[dict]]:
    """Map an operation's parameters to the playbook's variables. Returns (params, config entries for values the
    playbook can't know: they go into the Configuration step for the engineer to set)."""
    op = catalog.operation(connector, operation) or {"parameters": []}
    params, config = {}, []
    for p in op["parameters"]:
        name, title = p["name"], (p.get("title") or p["name"])
        text = f"{name} {title}".lower().replace("_", " ")
        value = None
        for words, ind in PARAM_HINTS:
            if ind in indicators and any(re.search(rf"\b{re.escape(w)}\b", text) for w in words):
                value = f"{{{{vars.{var(ind)}}}}}"
                break
        if value is None and purpose == "notify":
            if re.search(r"\b(to|recipient|recipients|email to|to recipients|channel|channel id|chat)\b", text):
                value = "{{vars.notify_to}}"
            elif re.search(r"\b(subject|title)\b", text):
                value = "FortiSOAR: {{vars.input.records[0].name}} — {{vars.verdict}}"
            elif re.search(r"\b(body|content|message|text|html)\b", text):
                value = "{{vars.summary}}"
            elif re.search(r"\bfrom\b", text):
                value = "{{vars.notify_from}}"
        if value is None and p.get("value") not in (None, "", []):
            value = p["value"]
        if value is None and p.get("required"):
            cname = snake(f"{connector}_{name}").lower()
            config.append({"name": cname, "value": "", "description": f"{title} for {connector}.{operation}", "required": True})
            value = f"{{{{vars.{cname}}}}}"
        if value is not None:
            params[name] = value
    return params, config


# ---------------------------------------------------------------- build from intake answers

def build(answers: dict) -> dict:
    """Intake answers -> spec, applying the best practices by construction."""
    s = empty_spec()
    s["use_case"] = answers.get("usecase", "")
    module = answers.get("module") or "alerts"
    conditions = answers.get("conditions") or []
    s["trigger"] = {"type": answers.get("trigger") or "on_create", "module": module,
                    "conditions": [c for c in conditions if c.get("field") not in PICKLIST_FIELDS],
                    "button": "Run " + (answers.get("name") or "playbook")[:30]}
    inds = [i for i in answers.get("indicators") or [] if i in catalog.INDICATORS] or ["ip"]
    s["indicators"] = inds
    s["fields"] = {i: DEFAULT_FIELDS.get(module, DEFAULT_FIELDS["alerts"]).get(i, i) for i in inds}
    s["name"] = (answers.get("name") or "").strip() or f"{MODULES.get(module, 'Record')} - {', '.join(catalog.INDICATOR_LABELS[i] for i in inds)} triage"
    s["description"] = (answers.get("description") or answers.get("usecase") or "").strip()[:1500]
    s["safety"] = {"approval": bool(answers.get("approval", True)), "approver": answers.get("approver") or "SOC Team",
                   "verdict": answers.get("verdict") or "any"}
    s["tags"] = ["developed"] + inds
    steps = s["steps"]
    for c in conditions:
        if c.get("field") in PICKLIST_FIELDS:
            steps.append(scope_step(c["field"], str(c.get("value") or "")))
    steps.append({"id": new_id(), "kind": "extract", "name": "Extract indicators"})
    enrich = answers.get("enrich") or []
    for item in enrich:
        con, op = item.split(".", 1)
        steps.append(_connector_step("enrich", con, op, inds))
    contain = answers.get("contain") or []
    record_updates = set(answers.get("record_updates") or [])
    if module != "alerts":
        record_updates.discard("close_benign")  # closing uses the alert status picklist
    if enrich:
        steps.append({"id": new_id(), "kind": "verdict", "name": "Compute verdict"})
        benign_id = new_id() if "close_benign" in record_updates else None
        steps.append({"id": new_id(), "kind": "decision", "name": "Is it malicious?", "condition": "{{vars.is_malicious}}",
                      "yes_label": "Malicious", "no_label": "Not malicious", "no": benign_id or "end", "end_label": "Not malicious"})
    else:
        benign_id = None
    if contain and s["safety"]["approval"]:
        steps.append({"id": new_id(), "kind": "approval", "name": "Approve response actions", "yes_label": "Approve",
                      "no_label": "Reject", "no": "end", "end_label": "Rejected by analyst"})
    for item in contain:
        con, op = item.split(".", 1)
        steps.append(_connector_step("contain", con, op, inds))
    if "escalate" in record_updates:
        steps.append({"id": new_id(), "kind": "create_record", "name": "Escalate to incident", "module": "incidents",
                      "fields": {"name": "{{vars.input.records[0].name}}", "description": "{{vars.summary}}",
                                 "severity": "{{ ('Severity' | picklist('High'))['@id'] }}"}})
    for item in answers.get("tickets") or []:
        con, op = item.split(".", 1)
        steps.append(_connector_step("ticket", con, op, inds))
    if "note" in record_updates or not record_updates:
        steps.append({"id": new_id(), "kind": "update_record", "name": "Add analysis summary", "operation": "Append",
                      "fields": {"description": "{{vars.summary}}"}})
    if "severity" in record_updates and enrich:
        steps.append({"id": new_id(), "kind": "update_record", "name": "Raise severity", "operation": "Overwrite",
                      "fields": {"severity": "{{ ('Severity' | picklist('High'))['@id'] }}"}})
    for channel in answers.get("notify") or []:
        if channel in NOTIFY:
            con, op = NOTIFY[channel]
            st = _connector_step("notify", con, op, inds)
            st["name"] = f"Notify via {channel.title() if channel != 'email' else 'email'}"
            steps.append(st)
    if benign_id:  # the benign branch: close as false positive, then end
        steps.append({"id": benign_id, "kind": "update_record", "name": "Close as benign", "operation": "Overwrite", "branch_only": True,
                      "fields": {"status": "{{ ('AlertStatus' | picklist('Closed'))['@id'] }}",
                                 "closureNotes": "Closed automatically: no enrichment source reported the indicators as malicious."}})
    s["config"] = default_config(s)
    return normalise(s)


def _connector_step(kind: str, con: str, op: str, inds: list[str]) -> dict:
    meta = catalog.operation(con, op) or {}
    c = catalog.get(con) or {}
    params, extra = fill_params(con, op, inds, kind)
    title = meta.get("title") or op.replace("_", " ").title()
    step = {"id": new_id(), "kind": kind, "name": f"{title} via {c.get('label', con)}"[:80], "connector": con, "operation": op,
            "title": title, "version": c.get("version"), "params": params, "extra_config": extra}
    if kind == "contain":
        step["rollback"] = catalog.rollback_for(con, op)
    return step


def default_config(s: dict) -> list[dict]:
    """Tunable values collected in the Configuration step (best practice: nothing environment-specific inside steps)."""
    cfg = [{"name": f"{i}_field", "value": s["fields"].get(i, i), "description": f"Field on the {s['trigger']['module']} record that holds the {catalog.INDICATOR_LABELS[i]}", "required": False}
           for i in s["indicators"]]
    for st in s["steps"]:
        if st.get("scope"):
            cfg.append({"name": f"scope_{st['scope']['field']}", "value": st["scope"]["values"],
                        "description": f"Comma-separated {st['scope']['field']} values this playbook handles (others end as out of scope)", "required": False})
    kinds = {st["kind"] for st in s["steps"]}
    if "approval" in kinds:
        cfg.append({"name": "approver_team", "value": s["safety"].get("approver") or "SOC Team", "description": "Team that approves response actions (assign it on the approval step after import)", "required": False})
    if "notify" in kinds:
        cfg.append({"name": "notify_to", "value": "", "description": "Recipients / channel for notifications", "required": True})
        if any(st.get("connector") == "smtp" for st in s["steps"]):
            cfg.append({"name": "notify_from", "value": "", "description": "Sender address for notification email", "required": True})
    for con in sorted({st["connector"] for st in s["steps"] if st.get("connector")}):
        cfg.append({"name": f"{snake(con).lower()}_config", "value": "", "description": f"{con} connector configuration ID (blank = the connector's default configuration)", "required": False})
    seen = {c["name"] for c in cfg}
    for st in s["steps"]:
        for e in st.get("extra_config") or []:
            if e["name"] not in seen:
                cfg.append(e)
                seen.add(e["name"])
    return cfg


def normalise(s: dict) -> dict:
    """Fix references and keep the spec consistent after edits."""
    ids = {st["id"] for st in s["steps"]}
    for st in s["steps"]:
        st.setdefault("id", new_id())
        if st["kind"] in ("decision", "approval") and st.get("no") not in ids and st.get("no") != "end":
            st["no"] = "end"
    names = set()
    for st in s["steps"]:  # unique step names (FortiSOAR refers to steps by name in Jinja)
        # letters, digits and spaces only: results are read in Jinja as vars.steps.<Name_With_Underscores>
        clean = re.sub(r"\s+", " ", re.sub(r"[^A-Za-z0-9 ]+", " ", st["name"])).strip()
        base, n = clean or st["kind"].replace("_", " ").title(), 2
        name = base
        while name.lower() in names:
            name, n = f"{base} {n}", n + 1
        st["name"] = name
        names.add(name.lower())
    have = {c["name"] for c in s["config"]}
    for c in default_config(s):  # add config entries new steps need (keep the engineer's values)
        if c["name"] not in have:
            s["config"].append(c)
    return s


# ---------------------------------------------------------------- edits (guided change requests and the design panel)

def summary_lines(s: dict) -> list[str]:
    t = s["trigger"]
    cond = " and ".join(f"{c['field']} {c['operator']} {c.get('value', '')}".strip() for c in t.get("conditions") or [])
    lines = [f"**Trigger:** {TRIGGERS.get(t['type'], t['type'])} — {MODULES.get(t['module'], t['module'])}" + (f", only when {cond}" if cond else ""),
             f"**Works on:** {', '.join(catalog.INDICATOR_LABELS.get(i, i) for i in s['indicators'])}"]
    for i, st in enumerate([x for x in s["steps"] if not x.get("branch_only")], 1):
        extra = f" — `{st['connector']}.{st['operation']}`" if st.get("connector") else ""
        branch = ""
        if st["kind"] in ("decision", "approval"):
            target = "end" if st.get("no") == "end" else next((x["name"] for x in s["steps"] if x["id"] == st.get("no")), "end")
            branch = f" ({st.get('yes_label', 'Yes')} → continue · {st.get('no_label', 'No')} → {target})"
        lines.append(f"{i}. **{st['name']}**{extra}{branch}")
    for st in s["steps"]:
        if st.get("branch_only"):
            lines.append(f"   ↳ on the other branch: **{st['name']}**")
    return lines


def apply_edit(s: dict, edit: dict) -> tuple[dict, str]:
    """Structured edit from the design panel / guided commands. Returns (new spec, what changed)."""
    s = copy.deepcopy(s)
    op = edit.get("op")
    steps = s["steps"]
    idx = next((i for i, st in enumerate(steps) if st["id"] == edit.get("id")), None)
    if op == "remove" and idx is not None:
        removed = steps.pop(idx)
        msg = f"Removed “{removed['name']}”."
    elif op in ("up", "down") and idx is not None:
        j = idx - 1 if op == "up" else idx + 1
        if 0 <= j < len(steps):
            steps[idx], steps[j] = steps[j], steps[idx]
        msg = f"Moved “{steps[j if 0 <= j < len(steps) else idx]['name']}” {op}."
    elif op == "rename" and idx is not None:
        steps[idx]["name"] = str(edit.get("name") or steps[idx]["name"])[:80]
        msg = f"Renamed to “{steps[idx]['name']}”."
    elif op == "set_params" and idx is not None:
        steps[idx]["params"] = {str(k): v for k, v in (edit.get("params") or {}).items()}
        msg = f"Updated the parameters of “{steps[idx]['name']}”."
    elif op == "add_connector":
        kind = edit.get("kind") or "enrich"
        st = _connector_step(kind, edit["connector"], edit["operation"], s["indicators"])
        pos = _insert_position(s, kind)
        steps.insert(pos, st)
        msg = f"Added “{st['name']}”."
    elif op == "add_step":
        kind = edit.get("kind")
        st = {"id": new_id(), "kind": kind, "name": edit.get("name") or kind.replace("_", " ").title()}
        if kind == "approval":
            st.update(yes_label="Approve", no_label="Reject", no="end", end_label="Rejected by analyst")
        elif kind == "decision":
            st.update(condition=edit.get("condition") or "{{vars.is_malicious}}", yes_label="Yes", no_label="No", no="end")
        elif kind == "wait":
            st["delay_minutes"] = int(edit.get("delay_minutes") or 5)
        elif kind == "update_record":
            st.update(operation="Append", fields=edit.get("fields") or {"description": "{{vars.summary}}"})
        steps.insert(_insert_position(s, kind) if edit.get("position") is None else int(edit["position"]), st)
        msg = f"Added a {kind.replace('_', ' ')} step “{st['name']}”."
    elif op == "approval":
        on = bool(edit.get("on"))
        s["safety"]["approval"] = on
        has = [st for st in steps if st["kind"] == "approval"]
        if on and not has:
            first_contain = next((i for i, st in enumerate(steps) if st["kind"] == "contain"), None)
            if first_contain is None:
                return s, "There are no response actions to approve."
            steps.insert(first_contain, {"id": new_id(), "kind": "approval", "name": "Approve response actions", "yes_label": "Approve",
                                         "no_label": "Reject", "no": "end", "end_label": "Rejected by analyst"})
            msg = "Added an approval step before the response actions."
        elif not on and has:
            s["steps"] = [st for st in steps if st["kind"] != "approval"]
            msg = "Removed the approval step: response actions will run automatically (the reviewer will flag this)."
        else:
            msg = "No change needed."
    elif op == "trigger":
        t = s["trigger"]
        if edit.get("type") in TRIGGERS:
            t["type"] = edit["type"]
        if edit.get("module") in MODULES:
            t["module"] = edit["module"]
            s["fields"] = {i: DEFAULT_FIELDS[t["module"]].get(i, i) for i in s["indicators"]}
            for c in s["config"]:
                if c["name"].endswith("_field") and c["name"][:-6] in s["fields"]:
                    c["value"] = s["fields"][c["name"][:-6]]
        if "conditions" in edit:
            t["conditions"] = [c for c in edit["conditions"] if c.get("field") and c.get("operator") in OPERATORS]
        msg = f"Trigger: {TRIGGERS[t['type']]} on {MODULES.get(t['module'], t['module'])}" + (f", {len(t['conditions'])} condition(s)" if t["conditions"] else "") + "."
    elif op == "meta":
        for k in ("name", "description", "collection"):
            if edit.get(k):
                s[k] = str(edit[k])[:1500 if k == "description" else 120]
        if isinstance(edit.get("tags"), list):
            s["tags"] = [str(t)[:40] for t in edit["tags"]][:10]
        msg = "Updated the playbook details."
    elif op == "config":
        values = edit.get("values") or {}
        for c in s["config"]:
            if c["name"] in values:
                c["value"] = str(values[c["name"]])[:500]
        msg = "Updated the configuration values."
    else:
        return s, "That change isn't supported."
    return normalise(s), msg


def _insert_position(s: dict, kind: str) -> int:
    order = {"extract": 0.5, "enrich": 1, "verdict": 2, "decision": 3, "approval": 4, "contain": 5, "create_record": 6, "ticket": 6,
             "update_record": 7, "notify": 8, "wait": 7, "note": 9}
    rank = order.get(kind, 7)
    main = [i for i, st in enumerate(s["steps"]) if not st.get("branch_only")]
    pos = 0
    for i in main:
        if order.get(s["steps"][i]["kind"], 7) <= rank:
            pos = i + 1
    return pos
