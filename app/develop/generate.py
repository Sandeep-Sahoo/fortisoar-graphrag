"""Design spec -> FortiSOAR playbook collection export (importable JSON), built with the best practices:

* a Configuration step right after the trigger holds every environment-specific value
* connector steps ignore errors (the verdict and summary record each source's status) and read their
  connector configuration from the Configuration step
* decisions always have a default branch; approvals have Approve and Reject paths
* debug off, description and tags set, record triggers carry the chosen conditions
* imported inactive: activate after testing in staging
* a separate manual "Rollback" playbook when the response actions have an undo operation

Step-type IDs and argument shapes come from Fortinet's own 7.x exports.
"""
from __future__ import annotations

import json
import re
import uuid as uuidlib
from datetime import datetime, timezone

from . import catalog
from .spec import MODULES, snake, var

ST = {  # FortiSOAR workflow step type ids
    "on_create": "ea155646-3821-4542-9702-b246da430a8d", "on_update": "9300bf69-5063-486d-b3a6-47eb9da24872",
    "manual": "f414d039-bb0d-4e59-9c39-a8f1e880b18a", "referenced": "b348f017-9a94-471f-87f8-ce88b6a7ad62",
    "set_variable": "04d0cf46-b6a8-42c4-8683-60a7eaa69e8f", "connector": "0bfed618-0316-11e7-93ae-92361f002671",
    "decision": "12254cf5-5db7-4b1a-8cb1-3af081924b28", "manual_input": "fc04082a-d7dc-4299-96fb-6837b1baa0fe",
    "create_record": "2597053c-e718-44b4-8394-4d40fe26d357", "update_record": "b593663d-7d13-40ce-a3a3-96dece928722",
    "wait": "6832e556-b9c7-497a-babe-feda3bd27dbf",
}
NS = uuidlib.UUID("6b1f4c1e-3d2a-4f5e-9a7b-1c2d3e4f5a6b")
PRIORITY_MEDIUM = "/api/3/picklists/2b563c61-ae2c-41c0-a85a-c9709585e3f2"


def _u(*parts) -> str:
    return str(uuidlib.uuid5(NS, "/".join(str(p) for p in parts)))


def jinja_name(name: str) -> str:
    """How FortiSOAR exposes a step's result: vars.steps.<Name_With_Underscores>."""
    return re.sub(r"\W+", "_", name).strip("_")


def _step(pb_uuid: str, sid: str, name: str, kind: str, args: dict, top: int, left: int, description: str = "") -> dict:
    return {"@type": "WorkflowStep", "name": name, "description": description or None, "arguments": args, "status": None,
            "top": str(top), "left": str(left), "stepType": f"/api/3/workflow_step_types/{ST[kind]}", "group": None,
            "uuid": _u(pb_uuid, sid)}


def _route(pb_uuid: str, a: dict, b: dict, label: str | None = None) -> dict:
    return {"@type": "WorkflowRoute", "name": f"{a['name']} -> {b['name']}", "targetStep": f"/api/3/workflow_steps/{b['uuid']}",
            "sourceStep": f"/api/3/workflow_steps/{a['uuid']}", "label": label, "isExecuted": False, "group": None,
            "uuid": _u(pb_uuid, "route", a["uuid"], b["uuid"], label or "")}


def _trigger_args(spec: dict, pb_uuid: str) -> tuple[str, dict]:
    t, module = spec["trigger"], spec["trigger"]["module"]
    filters = [{"type": "primitive", "field": c["field"], "value": c.get("value", ""), "operator": c["operator"],
                "_operator": c["operator"]} for c in t.get("conditions") or []]
    logic = "AND"
    if not filters and t["type"] in ("on_create", "on_update") and spec.get("fields"):
        # only run when the record carries one of the indicators (isnull = false), so it doesn't fire on every record
        fields = list(dict.fromkeys(spec["fields"].get(i) for i in spec["indicators"] if spec["fields"].get(i)))
        filters = [{"type": "primitive", "field": f, "value": "false", "operator": "isnull", "_operator": "isnull"} for f in fields]
        logic = "OR"
    if t["type"] in ("on_create", "on_update"):
        return t["type"], {"resource": module, "resources": [module], "__triggerLimit": True,
                           "step_variables": {"input": {"params": [], "records": ["{{vars.input.records[0]}}"]}},
                           "triggerOnSource": True, "triggerOnReplicate": False,
                           "fieldbasedtrigger": {"sort": [], "limit": 30, "logic": logic, "filters": filters}}
    if t["type"] == "manual":
        return "manual", {"route": _u(pb_uuid, "route"), "title": spec["name"][:80], "resources": [module], "inputVariables": [],
                          "step_variables": {"input": {"records": "{{vars.input.records}}"}},
                          "displayConditions": {module: {"sort": [], "limit": 30, "logic": "AND", "filters": filters}},
                          "executeButtonText": (t.get("button") or "Run")[:40], "noRecordExecution": False, "singleRecordExecution": True}
    return "referenced", {"step_variables": {"input": {"params": [], "records": "{{vars.input.records}}"}}}


def _malicious_expr(spec: dict) -> str:
    enrich = [s for s in spec["steps"] if s["kind"] == "enrich"]
    checks = ", ".join(f"('malicious' in (vars.steps.{jinja_name(s['name'])}.data | default({{}}) | tojson | lower))" for s in enrich)
    need = 2 if spec["safety"].get("verdict") == "two" and len(enrich) >= 2 else 1
    return f"([{checks}] | select | list | length) >= {need}" if enrich else "false"


def _connector_args(spec: dict, st: dict) -> dict:
    c = catalog.get(st["connector"]) or {}
    args = {"name": c.get("label") or st["connector"], "config": f"{{{{vars.{snake(st['connector']).lower()}_config}}}}",
            "params": st.get("params") or {}, "version": st.get("version") or c.get("version") or "", "connector": st["connector"],
            "operation": st["operation"], "operationTitle": st.get("title") or st["operation"], "step_variables": [],
            "ignore_errors": True}
    return args


def build_workflows(spec: dict, pb_uuid: str, coll_uuid: str) -> list[dict]:
    main_wf = _main_workflow(spec, pb_uuid, coll_uuid)
    out = [main_wf]
    rollback = [s for s in spec["steps"] if s["kind"] == "contain" and s.get("rollback")]
    if rollback:
        out.append(_rollback_workflow(spec, rollback, _u(pb_uuid, "rollback"), coll_uuid))
    return out


def _main_workflow(spec: dict, pb_uuid: str, coll_uuid: str) -> dict:
    module = spec["trigger"]["module"]
    steps, routes = [], []
    x_main, x_branch, y = 300, 700, 30
    trig_kind, trig_args = _trigger_args(spec, pb_uuid)
    start = _step(pb_uuid, "start", "Start", trig_kind, trig_args, y, x_main)
    steps.append(start)
    y += 120
    cfg_vars = {c["name"]: c.get("value", "") for c in spec["config"]}
    config = _step(pb_uuid, "config", "Configuration", "set_variable", {"step_variables": cfg_vars}, y, x_main,
                   "Environment-specific values. Set these after import; nothing else needs editing.")
    steps.append(config)
    routes.append(_route(pb_uuid, start, config))
    by_id, pending_no = {}, []
    main = [s for s in spec["steps"] if not s.get("branch_only")]
    branch = [s for s in spec["steps"] if s.get("branch_only")]
    prev, prev_label = config, None
    mal = _malicious_expr(spec)
    for st in main:
        y += 120
        kind = st["kind"]
        if kind == "extract":
            vars_ = {var(i): f"{{{{vars.input.records[0][vars.{i}_field]}}}}" for i in spec["indicators"]}
            node = _step(pb_uuid, st["id"], st["name"], "set_variable", {"step_variables": vars_}, y, x_main,
                         "Reads the indicators from the triggering record (field names come from the Configuration step).")
        elif kind == "verdict":
            enrich = [s for s in spec["steps"] if s["kind"] == "enrich"]
            sources = ", ".join(f"{s['name']}: {{{{vars.steps.{jinja_name(s['name'])}.status | default('not run')}}}}" for s in enrich)
            node = _step(pb_uuid, st["id"], st["name"], "set_variable", {"step_variables": {
                "is_malicious": f"{{{{ {mal} }}}}",
                "verdict": f"{{{{ 'Malicious' if ({mal}) else 'Not malicious' }}}}",
                "summary": (f"Automated triage by playbook '{spec['name']}'. Verdict: {{{{ 'Malicious' if ({mal}) else 'Not malicious' }}}}. "
                            + " ".join(f"{catalog.INDICATOR_LABELS[i]}: {{{{vars.{var(i)}}}}}." for i in spec["indicators"])
                            + (f" Sources: {sources}." if sources else ""))}}, y, x_main,
                "Counts the enrichment sources that reported the indicators as malicious. Review the expression for your connectors' output.")
        elif kind in ("enrich", "contain", "notify", "ticket"):
            meta = catalog.operation(st["connector"], st["operation"]) or {}
            what = {"enrich": "Enrichment", "contain": "Response action", "notify": "Notification", "ticket": "Ticket"}[kind]
            node = _step(pb_uuid, st["id"], st["name"], "connector", _connector_args(spec, st), y, x_main,
                         st.get("description") or f"{what}: {meta.get('description') or st.get('title') or st['operation']} Continues on error.")
        elif kind == "decision":
            desc = (f"Continues only when {st['scope']['field']} is one of the values in Configuration (scope_{st['scope']['field']})."
                    if st.get("scope") else f"{st.get('yes_label') or 'Yes'} continues; {st.get('no_label') or 'No'} takes the other branch.")
            node = _step(pb_uuid, st["id"], st["name"], "decision", {"conditions": []}, y, x_main, desc)
        elif kind == "approval":
            inds = "".join(f"<li>{catalog.INDICATOR_LABELS[i]}: {{{{vars.{var(i)}}}}}</li>" for i in spec["indicators"])
            actions = "".join(f"<li>{s['name']}</li>" for s in spec["steps"] if s["kind"] == "contain")
            node = _step(pb_uuid, st["id"], st["name"], "manual_input", {
                "type": "InputBased", "input": {"schema": {"title": st["name"],
                    "description": f"<p>Verdict: <b>{{{{vars.verdict | default('n/a')}}}}</b></p><ul>{inds}</ul><p>Approve these actions?</p><ul>{actions}</ul>",
                    "inputVariables": []}},
                "record": "{{ vars.input.records[0][\"@id\"] }}", "resources": module, "is_approval": True,
                "owner_detail": {"isAssigned": False}, "isRecordLinked": True, "step_variables": [],
                "response_mapping": {"options": [], "duplicateOption": False},
                "email_notification": {"enabled": False, "smtpParameters": []}, "unauthenticated_input": False,
                "internal_email_subject": "A FortiSOAR playbook is requesting your approval"}, y, x_main,
                "Assign the approver team (Configuration: approver_team) on this step after import.")
        elif kind == "update_record":
            node = _step(pb_uuid, st["id"], st["name"], "update_record", {
                "resource": st.get("fields") or {}, "operation": st.get("operation") or "Overwrite",
                "collection": "{{vars.input.records[0]['@id']}}", "__recommend": [], "collectionType": f"/api/3/{module}",
                "fieldOperation": {"recordTags": "Append"}, "step_variables": []}, y, x_main,
                f"Updates the triggering {module[:-1]}: {', '.join(st.get('fields') or {})}.")
        elif kind == "create_record":
            target = st.get("module") or "incidents"
            fields = dict(st.get("fields") or {})
            fields.setdefault(module, ["{{vars.input.records[0]['@id']}}"])  # link the new record to the trigger record
            node = _step(pb_uuid, st["id"], st["name"], "create_record", {
                "resource": fields, "operation": "Overwrite", "collection": f"/api/3/{target}", "__recommend": [],
                "fieldOperation": {"recordTags": "Overwrite"}, "step_variables": []}, y, x_main,
                f"Creates a linked {target[:-1]} record.")
        elif kind == "wait":
            node = _step(pb_uuid, st["id"], st["name"], "wait", {
                "rule": {"actions": [{"type": "resume_playbook", "enabled": True, "channel_uuid": "e2ce87c2-c55a-11ec-9d64-0242ac120002"}],
                         "is_active": True, "event_source": "crudhub"}, "type": "TimeBased",
                "delay": {"days": 0, "hours": 0, "minutes": int(st.get("delay_minutes") or 5), "seconds": 0}}, y, x_main,
                f"Waits {int(st.get('delay_minutes') or 5)} minutes before continuing.")
        else:  # note
            node = _step(pb_uuid, st["id"], st["name"], "set_variable", {"step_variables": {"note": st.get("text") or st["name"]}}, y, x_main,
                         "Records a note in the playbook's variables.")
        steps.append(node)
        by_id[st["id"]] = node
        routes.append(_route(pb_uuid, prev, node, prev_label))
        prev, prev_label = node, None
        if kind in ("decision", "approval"):
            prev_label = st.get("yes_label") or ("Approve" if kind == "approval" else "Yes")
            pending_no.append((st, node))
    # yes-branch of a final decision needs somewhere to go
    if prev_label:
        y += 120
        done = _step(pb_uuid, "done", "Completed", "set_variable", {"step_variables": {"outcome": "completed"}}, y, x_main,
                     "End of the playbook.")
        steps.append(done)
        routes.append(_route(pb_uuid, prev, done, prev_label))
    # branch-only steps (e.g. close as benign)
    for st in branch:
        node = _step(pb_uuid, st["id"], st["name"], "update_record", {
            "resource": st.get("fields") or {}, "operation": st.get("operation") or "Overwrite",
            "collection": "{{vars.input.records[0]['@id']}}", "__recommend": [], "collectionType": f"/api/3/{module}",
            "fieldOperation": {"recordTags": "Append"}, "step_variables": []}, 0, x_branch,
            f"Other branch: updates the triggering {module[:-1]} ({', '.join(st.get('fields') or {})}).")
        steps.append(node)
        by_id[st["id"]] = node
    # "no" / "reject" targets and the decision / approval arguments
    for st, node in pending_no:
        top = int(node["top"])
        if st.get("no") in by_id and st.get("no") != st["id"]:
            target = by_id[st["no"]]
            if target["top"] == "0":
                target["top"] = str(top)
        else:
            label = st.get("end_label") or st.get("no_label") or "No action"
            target = _step(pb_uuid, f"end-{st['id']}", f"End - {label}"[:60], "set_variable", {"step_variables": {"outcome": label}}, top, x_branch,
                           f"Ends the playbook: {label.lower()}.")
            steps.append(target)
        yes_target = next((r for r in routes if r["sourceStep"].endswith(node["uuid"]) and r["label"]), None)
        yes_step = next(s for s in steps if yes_target and yes_target["targetStep"].endswith(s["uuid"]))
        yes_label, no_label = st.get("yes_label") or "Yes", st.get("no_label") or "No"
        routes.append(_route(pb_uuid, node, target, no_label))
        if st["kind"] == "decision":
            node["arguments"]["conditions"] = [
                {"option": yes_label, "step_iri": f"/api/3/workflow_steps/{yes_step['uuid']}", "condition": st.get("condition") or "{{vars.is_malicious}}",
                 "step_name": yes_step["name"]},
                {"option": no_label, "default": True, "step_iri": f"/api/3/workflow_steps/{target['uuid']}", "step_name": target["name"]}]
        else:
            node["arguments"]["response_mapping"]["options"] = [
                {"option": yes_label, "primary": True, "step_iri": f"/api/3/workflow_steps/{yes_step['uuid']}"},
                {"option": no_label, "primary": False, "step_iri": f"/api/3/workflow_steps/{target['uuid']}"}]
    for s in steps:  # any branch step never positioned
        if s["top"] == "0":
            s["top"] = "150"
    return _workflow(spec["name"], spec.get("description") or spec.get("use_case") or "", spec.get("tags") or [], pb_uuid,
                     coll_uuid, start, steps, routes)


def _rollback_workflow(spec: dict, contain: list[dict], pb_uuid: str, coll_uuid: str) -> dict:
    module = spec["trigger"]["module"]
    start = _step(pb_uuid, "start", "Start", "manual", {
        "route": _u(pb_uuid, "route"), "title": f"Rollback - {spec['name']}"[:80], "resources": [module], "inputVariables": [],
        "step_variables": {"input": {"records": "{{vars.input.records}}"}},
        "displayConditions": {module: {"sort": [], "limit": 30, "logic": "AND", "filters": []}},
        "executeButtonText": "Roll back", "noRecordExecution": False, "singleRecordExecution": True}, 30, 300,
        "Run manually from the record to undo the response actions.")
    cfg = _step(pb_uuid, "config", "Configuration", "set_variable", {"step_variables": {
        c["name"]: c.get("value", "") for c in spec["config"] if c["name"].endswith(("_field", "_config"))}}, 150, 300,
        "Same field names and connector configurations as the main playbook.")
    extract = _step(pb_uuid, "extract", "Extract indicators", "set_variable", {"step_variables": {
        var(i): f"{{{{vars.input.records[0][vars.{i}_field]}}}}" for i in spec["indicators"]}}, 270, 300,
        "Reads the indicators from the record.")
    steps, routes, prev, y = [start, cfg, extract], [], None, 270
    routes += [_route(pb_uuid, start, cfg), _route(pb_uuid, cfg, extract)]
    prev = extract
    for st in contain:
        rb = st["rollback"]
        y += 120
        params = {k: v for k, v in (st.get("params") or {}).items()}
        node = _step(pb_uuid, st["id"], f"Undo {st['name']}"[:80], "connector", {
            "name": (catalog.get(rb["connector"]) or {}).get("label") or rb["connector"],
            "config": f"{{{{vars.{snake(rb['connector']).lower()}_config}}}}", "params": params, "version": st.get("version") or "",
            "connector": rb["connector"], "operation": rb["operation"], "operationTitle": rb["title"], "step_variables": [],
            "ignore_errors": True}, y, 300, f"Undo: {rb['title']}.")
        steps.append(node)
        routes.append(_route(pb_uuid, prev, node))
        prev = node
    y += 120
    note = _step(pb_uuid, "note", "Record rollback", "update_record", {
        "resource": {"description": f"Response actions of '{spec['name']}' were rolled back."}, "operation": "Append",
        "collection": "{{vars.input.records[0]['@id']}}", "__recommend": [], "collectionType": f"/api/3/{module}",
        "fieldOperation": {"recordTags": "Append"}, "step_variables": []}, y, 300, "Notes the rollback on the record.")
    steps.append(note)
    routes.append(_route(pb_uuid, prev, note))
    return _workflow(f"Rollback - {spec['name']}"[:120], f"Undoes the response actions of '{spec['name']}'. Run it manually from the record.",
                     (spec.get("tags") or []) + ["rollback"], pb_uuid, coll_uuid, start, steps, routes)


def _workflow(name, description, tags, pb_uuid, coll_uuid, start, steps, routes) -> dict:
    return {"@type": "Workflow", "triggerLimit": None, "name": name, "aliasName": None, "tag": None, "description": description,
            "isActive": False, "debug": False, "singleRecordExecution": False, "remoteExecutableFlag": False, "parameters": [],
            "synchronous": False, "collection": f"/api/3/workflow_collections/{coll_uuid}", "versions": [],
            "triggerStep": f"/api/3/workflow_steps/{start['uuid']}", "steps": steps, "routes": routes, "groups": [],
            "priority": PRIORITY_MEDIUM, "uuid": pb_uuid, "owners": [], "isPrivate": False, "deletedAt": None,
            "recordTags": [str(t)[:40] for t in tags]}


def export(spec: dict, pb_uuid: str, coll_uuid: str) -> dict:
    """The import file: a FortiSOAR workflow collection export."""
    wfs = build_workflows(spec, pb_uuid, coll_uuid)
    return {"type": "workflow_collections", "data": [{
        "uuid": coll_uuid, "@type": "WorkflowCollection", "name": spec.get("collection") or "Developed Playbooks",
        "description": f"Generated by the FortiSOAR Graph RAG playbook designer on {datetime.now(timezone.utc).strftime('%Y-%m-%d')}.",
        "visible": True, "image": None, "recordTags": ["developed"], "workflows": wfs}], "exported_tags": ["developed"]}


# ---------------------------------------------------------------- checks + diagrams

def flow_of(wf: dict, scrubber) -> dict:
    """Diagram input (same shape as the graph's step_flow) for a generated workflow."""
    from ..ingest.playbooks import parse_playbook

    pb = parse_playbook(wf, "generated", scrubber)
    nxt: dict[str, list] = {}
    for r in pb.routes:
        nxt.setdefault(r["src"], []).append({"to": r["dst"], "label": r["label"] or None})
    return {"name": pb.name, "trigger_type": pb.trigger_type, "modules": pb.modules,
            "steps": [{"key": s.key, "name": s.name, "kind": s.kind, "connector": s.connector, "operation": s.operation,
                       "has_secret": s.has_secret, "child": None, "next": nxt.get(s.key, [])} for s in pb.steps]}


def validate(doc: dict) -> list[str]:
    """Structural checks an import would trip over."""
    problems = []
    for wf in doc["data"][0]["workflows"]:
        uuids = {s["uuid"] for s in wf["steps"]}
        names = [s["name"] for s in wf["steps"]]
        if wf["triggerStep"].rsplit("/", 1)[-1] not in uuids:
            problems.append(f"{wf['name']}: trigger step missing")
        if len(set(n.lower() for n in names)) != len(names):
            problems.append(f"{wf['name']}: duplicate step names")
        for r in wf["routes"]:
            if r["sourceStep"].rsplit("/", 1)[-1] not in uuids or r["targetStep"].rsplit("/", 1)[-1] not in uuids:
                problems.append(f"{wf['name']}: route {r['name']} points to a missing step")
        for s in wf["steps"]:
            a = s["arguments"]
            for opt in (a.get("conditions") or []) + ((a.get("response_mapping") or {}).get("options") or []):
                if opt.get("step_iri", "").rsplit("/", 1)[-1] not in uuids:
                    problems.append(f"{wf['name']}: '{s['name']}' branch '{opt.get('option')}' has no target")
        json.dumps(wf)  # serialisable
    return problems
