"""Playbook documentation in the fortisoar-playbook-docs format, for a playbook, a collection, a solution pack, an
uploaded source or a Develop build.

Sections: header · overview + "why it matters to the SOC team" · dependencies · connectors used · playbook collection
table · per playbook (trigger, configuration variables, Flow at a Glance, Detailed Flow, phased step-by-step) ·
operational notes · record sets · append-only version history.

Facts (steps, branches, connectors, variables, approvals, dependencies) always come from the playbook JSON and the
repo's own files. Without AI the narrative parts use templates; with AI (1 answer request per playbook, cached) the
model rewrites only the narrative: why-it-matters, plain-language glance diagram, phase names / intros / step sentences.
Branch outcomes and every fact stay deterministic, and step names returned by the model are checked against the JSON.
"""
from __future__ import annotations

import json
import re
import threading
import uuid as uuidlib
from datetime import datetime, timezone
from pathlib import Path

from .diagrams import _label
from .workspace import path as _ws

DIR = Path(__file__).resolve().parent.parent / "data" / "documentation"
GITHUB = Path(__file__).resolve().parent.parent / "data" / "fortinet" / "github"
AI_PLAYBOOK_LIMIT = 15
_lock = threading.Lock()
PLACEHOLDER = re.compile(r"(example\.(com|org|net)|exampl\.com|change[_ -]?me|your[_ -]|<[^>]{2,40}>|xxx|todo|placeholder|"
                         r"\b192\.0\.2\.|\b198\.51\.100\.|\b203\.0\.113\.)", re.I)
CIDR = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}/\d{1,2}\b")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")


# ---------------------------------------------------------------- gathering the material

def _repo_meta(repo: str) -> dict:
    """README / info.json / docs/*.md / release notes of a downloaded Fortinet repo."""
    base = GITHUB / repo
    out = {"repo": repo, "url": None, "info": {}, "readme": "", "usage": "", "setup": "", "contents": "", "release_notes": ""}
    if not base.is_dir():
        return out
    info_path = next((p for p in [base / "info.json", *sorted(base.glob("*/info.json"))] if p.is_file()), None)
    if info_path:
        try:
            out["info"] = json.loads(info_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
    for key, names in (("readme", ["README.md"]), ("usage", ["docs/usage.md"]), ("setup", ["docs/setup.md"]),
                       ("contents", ["docs/contents.md"]), ("release_notes", ["release_notes.md"])):
        for n in names:
            hit = next((p for p in [base / n, *sorted(base.glob(f"*/{n}"))] if p.is_file()), None)
            if hit:
                out[key] = hit.read_text(encoding="utf-8", errors="ignore")[:20000]
                break
    return out


def _collection_name(file: str | Path, coll: str) -> str:
    """Repos often keep one playbook per file under playbooks/<collection>/: the folder is the collection."""
    f = Path(file)
    if coll == f.stem and f.parent.name not in ("playbooks", "") and f.parent.parent.name == "playbooks":
        return f.parent.name
    return coll


def resolve(svc, kind: str, target: str) -> dict:
    """What to document: {title, kind, target, source, meta, playbooks: [{wf, collection}], names}."""
    from . import workspace
    from .review import github_index, sources

    items: list[dict] = []
    meta: dict = {}
    source: dict = {}
    title = target
    if kind in ("playbook", "collection"):
        if kind == "playbook":
            rows = svc.graph.read("MATCH (p:Playbook {uuid: $u}) RETURN p.uuid AS uuid, p.name AS name, p.collection AS collection, "
                                  "p.pack_type AS pack_type, p.source_id AS source_id, p.repo AS repo", u=target)
        else:
            rows = svc.graph.read("MATCH (c:PlaybookCollection {name: $n})-[:CONTAINS]->(p:Playbook) WHERE NOT coalesce(p.placeholder, false) "
                                  "RETURN p.uuid AS uuid, p.name AS name, p.collection AS collection, p.pack_type AS pack_type, "
                                  "p.source_id AS source_id, p.repo AS repo ORDER BY p.name", n=target)
        if not rows:
            raise KeyError(f"no {kind} '{target}' in this workspace")
        title = rows[0]["name"] if kind == "playbook" else target
        for r in rows:
            if r["pack_type"] == "upload" and r["source_id"]:
                original = r["uuid"].split("-", 2)[2] if r["uuid"].startswith("u-") else r["uuid"]
                wf = next((w for _f, _c, w in sources._workflows(r["source_id"]) if w.get("uuid") == original), None)
                if wf:
                    items.append({"wf": wf, "collection": r["collection"]})
                    src = sources.get(r["source_id"]) or {}
                    source = {"type": "upload", "name": src.get("name"), "kind": src.get("kind"), "url": src.get("url")}
            elif workspace.has("github"):
                found = github_index.raw(r["uuid"])
                if found:
                    items.append({"wf": found[0], "collection": _collection_name(found[1]["file"], found[1]["collection"])})
                    source = source or {"type": "github", "repo": found[1]["repo"]}
    elif kind == "pack":
        if not workspace.has("github"):
            raise KeyError("solution packs belong to the Fortinet workspace")
        for repo, file, coll, _i, wf in github_index._iter_workflows():
            if repo == target and not any(x["wf"].get("uuid") == wf.get("uuid") for x in items):
                items.append({"wf": wf, "collection": _collection_name(file, coll)})
        source = {"type": "github", "repo": target}
    elif kind == "source":
        src = sources.get(target)
        if not src:
            raise KeyError("no such source")
        title = src["name"]
        items = [{"wf": wf, "collection": coll} for _f, coll, wf in sources._workflows(target) if "__error__" not in wf]
        source = {"type": "upload", "name": src["name"], "kind": src["kind"], "url": src.get("url")}
    elif kind == "develop":
        from .develop import store as dstore

        s = dstore.get(target)
        if not s or not s.get("generated"):
            raise KeyError("develop the design first")
        g = s["generated"][-1]
        doc = json.loads((dstore.files_dir(target, g["version"]) / "playbook.json").read_text())
        coll = doc["data"][0].get("name") or "Developed Playbooks"
        items = [{"wf": wf, "collection": coll} for wf in doc["data"][0]["workflows"]]
        title = s["spec"]["name"]
        source = {"type": "develop", "name": title, "build": g["version"]}
    else:
        raise KeyError("kind must be playbook, collection, pack, source or develop")
    if not items:
        raise KeyError("no playbook JSON found to document")
    if source.get("type") == "github":
        meta = _repo_meta(source["repo"])
        meta["url"] = f"https://github.com/{svc.settings.fetch_org}/{source['repo']}"
        if kind == "pack":
            title = (meta["info"].get("label") or target)
    names = {x["wf"].get("uuid"): x["wf"].get("name") for x in items}
    return {"kind": kind, "target": target, "title": title, "source": source, "meta": meta, "playbooks": items, "names": names}


# ---------------------------------------------------------------- per-playbook analysis

def _lookup_name(uuid: str, names: dict, svc) -> str:
    from . import workspace

    if uuid in names:
        return names[uuid]
    if workspace.has("github"):
        from .review import github_index

        entry = github_index.index().get(uuid)
        if entry:
            return entry.get("name") or uuid
    try:
        rows = svc.graph.read("MATCH (p:Playbook {uuid: $u}) RETURN p.name AS name", u=uuid)
        if rows and rows[0]["name"]:
            return rows[0]["name"]
    except Exception:
        pass
    return f"playbook {uuid[:8]}…"


def analyse(item: dict, names: dict, svc) -> dict:
    from .develop import catalog
    from .ingest.playbooks import UUID_RE, iri_tail
    from .review.engine import build_model

    wf, scrub = item["wf"], svc.scrubber
    m = build_model(wf, item["collection"], scrub)
    pb = m["pb"]
    by_uuid = {s["uuid"]: s for s in m["steps"]}
    nxt: dict[str, list] = {}
    for r in pb.routes:
        nxt.setdefault(r["src"], []).append({"to": r["dst"], "label": r["label"] or None})
    steps = []
    for s in m["steps"]:
        a = s["raw_args"]
        info = {"key": s["key"], "name": s["name"], "kind": s["kind"], "description": (s["description"] or "").strip(),
                "connector": s["connector"], "operation": s["operation"], "next": nxt.get(s["key"], []), "args": a}
        if s["connector"]:
            c = catalog.get(s["connector"]) or {}
            op = catalog.operation(s["connector"], s["operation"] or "") or {}
            info["connector_label"] = a.get("name") or c.get("label") or s["connector"]
            info["op_title"] = a.get("operationTitle") or op.get("title") or s["operation"]
            info["purpose"] = op.get("purpose")
        if s["kind"] == "reference":
            ref = str(a.get("workflowReference") or "")
            mm = UUID_RE.search(ref)
            info["child"] = _lookup_name(mm.group(0), names, svc) if mm and "{{" not in ref else "a playbook chosen at run time"
        if s["kind"] == "manual_input":
            teams = [t.get("teamname") for t in ((a.get("owner_detail") or {}).get("assignedToTeam") or []) if isinstance(t, dict)]
            info["teams"] = [t for t in teams if t]
            info["approval"] = bool(a.get("is_approval"))
            options = ((a.get("response_mapping") or {}).get("options") or [])
            info["options"] = [{"option": o.get("option"), "to": (by_uuid.get(iri_tail(o.get("step_iri"))) or {}).get("name")} for o in options if isinstance(o, dict)]
        if s["kind"] == "decision":
            info["options"] = [{"option": c.get("option") or ("Otherwise" if c.get("default") else "Condition met"),
                                "to": c.get("step_name") or (by_uuid.get(iri_tail(c.get("step_iri"))) or {}).get("name"),
                                "default": bool(c.get("default"))} for c in (a.get("conditions") or []) if isinstance(c, dict)]
        if s["kind"] == "wait":
            d = a.get("delay") or {}
            info["wait"] = ", ".join(f"{d[k]} {k}" for k in ("weeks", "days", "hours", "minutes", "seconds") if d.get(k)) or "a while"
        if s["kind"] in ("update_record", "create_record"):
            info["fields"] = list((a.get("resource") or {}).keys())[:8] if isinstance(a.get("resource"), dict) else []
            info["target"] = str(a.get("collectionType") or a.get("collection") or "")
        if s["kind"] == "find_records":
            info["target"] = str(a.get("module") or a.get("resource") or "")
        if a.get("when"):
            info["when"] = str(a["when"])[:120]
        if isinstance(a.get("for_each"), dict) and a["for_each"].get("item"):
            info["for_each"] = str(a["for_each"]["item"])[:100]
        steps.append(info)
    # configuration variables: Set Variable steps named like "config", else the first one after the trigger
    cfg_steps = [s for s in steps if s["kind"] == "set_variable" and re.search(r"config", s["name"], re.I)] or \
                [s for s in steps[1:2] if s["kind"] == "set_variable"]
    config = []
    for s in cfg_steps:
        raw_vars = s["args"].get("step_variables") or {}
        masked = scrub.obj(raw_vars)
        if isinstance(raw_vars, dict):
            for k, v in raw_vars.items():
                raw = v if isinstance(v, str) else json.dumps(v)
                shown = masked.get(k) if isinstance(masked, dict) else raw
                shown = shown if isinstance(shown, str) else json.dumps(shown)
                flags = []
                if PLACEHOLDER.search(raw):
                    flags.append("placeholder — replace with real values")
                    shown = raw  # example.com-style placeholders aren't real data, so show them as written
                elif EMAIL.search(raw):
                    flags.append("contact address — confirm the recipient")
                if CIDR.search(raw):
                    flags.append("network range — confirm for your environment")
                if re.search(r"(policy|group|tenant|domain|team)", k, re.I) and "{{" not in raw and raw.strip():
                    flags.append("must match your environment")
                config.append({"name": k, "value": shown[:160], "source": "Jinja expression" if "{{" in raw else "static value", "flags": flags})
    trig = steps[0] if steps and steps[0]["kind"] == "trigger" else None
    return {"uuid": pb.uuid, "name": pb.name, "collection": item["collection"], "description": pb.description,
            "trigger_type": pb.trigger_type, "modules": pb.modules, "steps": steps, "config": config,
            "trigger_args": (trig or {}).get("args") or {}, "calls": [s["child"] for s in steps if s.get("child")],
            "active": wf.get("isActive"), "demo": bool(re.search(r"useMockOutput|Demo_mode|mock_result", json.dumps(wf)[:400000]))}


TRIGGER_TEXT = {"manual": "Manual — run by an analyst from the record", "record_event": "Automatic — when a record is created or updated",
                "referenced": "Called by another playbook (sub-playbook)", "api_endpoint": "Custom API endpoint", "unknown": "Not recognised"}


def trigger_text(p: dict) -> str:
    base = TRIGGER_TEXT.get(p["trigger_type"], p["trigger_type"])
    if p["modules"]:
        base += f" ({', '.join(p['modules'])})"
    filters = ((p["trigger_args"].get("fieldbasedtrigger") or {}).get("filters") or [])
    conds = [f"{f.get('field')} {f.get('operator')} {(f.get('_value') or {}).get('itemValue') or f.get('value') or ''}".strip()
             for f in filters if isinstance(f, dict) and f.get("field")]
    if conds:
        base += f"; only when {' and '.join(conds)}"
    return base


def role_of(p: dict, all_calls: set) -> str:
    n = p["name"].lower()
    if re.search(r"scenario|demo|simulat|generate (sample|test|alert)", n):
        return "Demo / simulation"
    if p["trigger_type"] == "referenced":
        return "Sub-playbook (utility)" if p["name"] in all_calls else "Sub-playbook (not called by the others here)"
    if p["trigger_type"] == "record_event":
        return "Automatic trigger"
    if p["trigger_type"] == "api_endpoint":
        return "API endpoint"
    if p["calls"] or len(p["steps"]) > 10:
        return "Main playbook (manual trigger)"
    return "Manual trigger"


# ---------------------------------------------------------------- diagrams

def detailed_flow(p: dict) -> str:
    """Step-level diagram: decisions / approvals as diamonds, sub-playbooks as subroutine boxes, edge labels."""
    ids = {s["key"]: f"n{i}" for i, s in enumerate(p["steps"])}
    lines = ["flowchart TD"]
    for s in p["steps"]:
        nid, name = ids[s["key"]], s["name"]
        if s["kind"] == "trigger":
            lines.append(f'    {nid}(["{_label(name)}<br/><small>{_label(trigger_text(p))[:60]}</small>"])')
        elif s["kind"] in ("decision", "manual_input"):
            lines.append(f'    {nid}{{"{_label(name)}"}}')
        elif s["kind"] == "reference":
            lines.append(f'    {nid}[["Sub-playbook:<br/>{_label(s.get("child") or name)}"]]')
        else:
            detail = f"{s.get('connector_label')}: {s.get('op_title')}" if s.get("connector") else \
                {"wait": f"wait {s.get('wait', '')}", "update_record": "update record", "create_record": "create record",
                 "find_records": "find records", "set_variable": "set variables"}.get(s["kind"], "")
            lines.append(f'    {nid}["{_label(name)}' + (f'<br/><small>{_label(detail)[:70]}</small>' if detail else "") + '"]')
    key_name = {x["key"]: x["name"] for x in p["steps"]}
    for s in p["steps"]:
        by_target = {o.get("to"): o.get("option") for o in s.get("options") or [] if o.get("to")}
        for n in s["next"]:
            if n["to"] in ids:
                label = n.get("label") or by_target.get(key_name.get(n["to"]))
                lines.append(f'    {ids[s["key"]]} -->' + (f'|"{_label(label)}"|' if label else "") + f' {ids[n["to"]]}')
    return "\n".join(lines)


def glance_from_nodes(nodes: list[dict], edges: list[dict]) -> str:
    """Render a story-level diagram from {id, label, shape} nodes and {from, to, label} edges (used for AI output)."""
    ids = {}
    lines = ["flowchart TD"]
    for i, n in enumerate(nodes[:10]):
        nid = f"g{i}"
        ids[str(n.get("id"))] = nid
        label = _label(str(n.get("label") or "")[:60])
        shape = n.get("shape")
        lines.append(f'    {nid}{{"{label}"}}' if shape == "decision" else f'    {nid}(["{label}"])' if shape == "end" else f'    {nid}["{label}"]')
    for e in edges[:20]:
        a, b = ids.get(str(e.get("from"))), ids.get(str(e.get("to")))
        if a and b:
            lines.append(f"    {a} -->" + (f'|"{_label(str(e["label"])[:30])}"|' if e.get("label") else "") + f" {b}")
    return "\n".join(lines)


GLANCE_LABEL = {"Investigation": "Gather evidence", "Analyst input": "Ask an analyst for input", "Approval": "Ask for approval",
                "Response": "Take response action", "Ticketing": "Open a ticket", "Record updates": "Update the record",
                "Notification": "Notify the team", "Orchestration": "Run reusable sub-steps", "Setup": "Load settings"}
START_LABEL = {"manual": "Analyst runs it on a record", "record_event": "A record is created or updated", "referenced": "Called by another playbook",
               "api_endpoint": "An external system calls in"}


def glance(p: dict) -> str:
    """Story-level template diagram: one node per phase (data-shuffling steps dropped), decisions with their branches."""
    nodes, edges, last = [{"id": "S", "label": START_LABEL.get(p["trigger_type"], "Start"), "shape": "box"}], [], "S"
    for i, ph in enumerate(phases(p)):
        if ph["title"] in ("Setup", "Processing") and i:
            continue
        nid = f"P{i}"
        nodes.append({"id": nid, "label": GLANCE_LABEL.get(ph["title"], ph["title"]), "shape": "box"})
        edges.append({"from": last, "to": nid})
        last = nid
        gate = next((x for x in ph["steps"] if x["kind"] in ("decision", "manual_input") and len(x.get("options") or []) >= 2), None)
        if gate and len(nodes) < 8:
            did = f"D{i}"
            nodes.append({"id": did, "label": "Approved?" if gate["kind"] == "manual_input" else "Which path applies?", "shape": "decision"})
            edges.append({"from": last, "to": did})
            other = gate["options"][-1].get("option") or "No"
            eid = f"E{i}"
            nodes.append({"id": eid, "label": "Stop or take the other path", "shape": "end"})
            edges.append({"from": did, "to": eid, "label": other})
            last = did
        if len(nodes) >= 9:
            break
    nodes.append({"id": "Z", "label": "Done", "shape": "end"})
    edges.append({"from": last, "to": "Z"})
    return glance_from_nodes(nodes, edges)


# ---------------------------------------------------------------- narrative (templates)

def _phase_of(s: dict) -> str:
    k = s["kind"]
    if k == "set_variable" and re.search(r"config", s["name"], re.I):
        return "Setup"
    if k == "manual_input":
        return "Approval" if s.get("approval") or re.search(r"approv", s["name"], re.I) else "Analyst input"
    if k in ("connector",):
        if s.get("purpose") == "contain":
            return "Response"
        if s.get("purpose") == "notify" or re.search(r"\b(notify|send|email|mail|message)\b", s["name"], re.I):
            return "Notification"
        if s.get("purpose") == "ticket":
            return "Ticketing"
        return "Investigation"
    if k in ("find_records", "utility", "code_snippet"):
        return "Investigation"
    if k in ("update_record", "create_record"):
        return "Record updates"
    if k == "reference":
        return "Orchestration"
    return ""


PHASE_INTRO = {
    "Setup": "The playbook first collects the values it works with, so nothing environment-specific is buried in later steps.",
    "Investigation": "Next it gathers the evidence, so the decision is based on the same facts every time{systems}.",
    "Analyst input": "Here the playbook asks an analyst for information it can't work out on its own.",
    "Approval": "The next actions change other systems or accounts, so a person has to approve them first.",
    "Response": "With the go-ahead, the playbook takes action{systems}.",
    "Ticketing": "The case is handed over to the ticketing system so other teams can follow it up{systems}.",
    "Record updates": "The outcome is written back to FortiSOAR, so the case history is complete for whoever reviews it later.",
    "Notification": "Finally, the right people are told what happened{systems}.",
    "Orchestration": "Part of the work is delegated to sub-playbooks, so the same logic is reused instead of copied.",
    "Processing": "The playbook prepares and moves data between steps.",
}


def phases(p: dict) -> list[dict]:
    out: list[dict] = []
    for s in p["steps"]:
        if s["kind"] == "trigger":
            continue
        ph = _phase_of(s) or (out[-1]["title"] if out else "Processing")
        if not out or out[-1]["title"] != ph:
            out.append({"title": ph, "steps": []})
        out[-1]["steps"].append(s)
    for ph in out:
        systems = sorted({s.get("connector_label") for s in ph["steps"] if s.get("connector_label")})
        ph["intro"] = PHASE_INTRO.get(ph["title"], "").format(systems=f" ({', '.join(systems)})" if systems else "")
    return out


def step_sentence(s: dict) -> str:
    if s.get("description"):
        base = s["description"].rstrip(".") + "."
    elif s.get("connector"):
        base = f"Calls {s['connector_label']}'s **{s['op_title']}** operation (`{s['connector']}.{s['operation']}`)."
    else:
        base = {"set_variable": "Sets variables used later" + (f": {', '.join(list((s['args'].get('step_variables') or {}).keys())[:6])}" if isinstance(s['args'].get('step_variables'), dict) and s['args'].get('step_variables') else "") + ".",
                "decision": "Checks the result and chooses the next path.",
                "manual_input": ("Raises an approval task" if s.get("approval") else "Asks an analyst for input")
                + (f" assigned to {', '.join(s['teams'])}" if s.get("teams") else " (not assigned to a team in the export)") + ".",
                "reference": f"Runs the sub-playbook **{s.get('child')}**.",
                "wait": f"Waits {s.get('wait')}.",
                "update_record": f"Updates the record" + (f" ({', '.join(s.get('fields') or [])})" if s.get("fields") else "") + ".",
                "create_record": f"Creates a record in `{s.get('target') or 'a module'}`" + (f" ({', '.join(s.get('fields') or [])})" if s.get("fields") else "") + ".",
                "find_records": f"Searches `{s.get('target') or 'records'}`.",
                "code_snippet": "Runs a custom code snippet.", "utility": "Uses a built-in utility action."}.get(s["kind"], "Performs an action.")
    mods = []
    if s.get("when"):
        mods.append(f"only when `{s['when']}`")
    if s.get("for_each"):
        mods.append(f"once per item of `{s['for_each']}`")
    return base + (f" _({'; '.join(mods)})_" if mods else "")


def branch_points(s: dict) -> list[str]:
    """Each branch outcome as its own point (decisions and approvals)."""
    out = []
    for o in s.get("options") or []:
        target = o.get("to") or "the next step"
        if s["kind"] == "manual_input":
            out.append(f"On **{o.get('option')}** → continues to **{target}**.")
        else:
            out.append(f"If **{o.get('option')}**{' (default)' if o.get('default') else ''} → continues to **{target}**.")
    return out


def why_it_matters(pbs: list[dict]) -> str:
    systems = sorted({s["connector_label"] for p in pbs for s in p["steps"] if s.get("connector_label")})
    has = lambda ph: any(_phase_of(s) == ph for p in pbs for s in p["steps"])
    manual = []
    if has("Investigation"):
        manual.append("pivoting between " + (", ".join(systems[:4]) if systems else "several consoles") + " to collect evidence")
    if has("Response"):
        manual.append("deciding in the moment whether to act and then making the change by hand")
    if has("Record updates"):
        manual.append("copying results back into the case")
    if has("Notification"):
        manual.append("remembering who to tell")
    gains = ["the same evidence is gathered every time"]
    if has("Approval"):
        gains.append("risky actions go through an explicit approval instead of one analyst acting alone")
    if has("Record updates"):
        gains.append("every outcome is written to the record, leaving an audit trail for whoever reviews the case")
    if has("Notification"):
        gains.append("the right people are told automatically")
    return ((f"Without this automation, an analyst would be {', '.join(manual[:-1]) + (' and ' if len(manual) > 1 else '') + manual[-1]}. " if manual else "")
            + "This playbook turns that into one consistent process: " + "; ".join(gains)
            + ". The result is a faster, more consistent response with less to remember under pressure.")


# ---------------------------------------------------------------- AI write-up

AI_SYSTEM = """You write FortiSOAR playbook documentation for a SOC team. You get one playbook's parsed structure.
Return ONLY JSON:
{"why_it_matters": "<one paragraph for analysts / stakeholders: the manual work it removes and what they gain, in outcomes language, no step names>",
 "summary": "<2 sentences: what triggers it and what it accomplishes>",
 "glance": {"nodes": [{"id": "A", "label": "<plain-language purpose, e.g. Check if already escalated>", "shape": "box|decision|end"}],
            "edges": [{"from": "A", "to": "B", "label": "<optional branch label>"}]},
 "phases": [{"title": "<phase name>", "intro": "<1-2 sentences telling why this phase exists>",
             "steps": [{"step": "<EXACT step name from the input>", "text": "<one sentence on what it does>"}]}]}
Rules: glance has 5-8 nodes, no step names, connector names or technical detail. Phases follow execution order and every
non-trigger step appears in exactly one phase, using its exact name. Use only facts in the input; never invent values."""


def ai_writeup(p: dict, svc) -> dict:
    from .providers import parse_json

    lines = [f"PLAYBOOK: {p['name']}", f"Description: {p['description'] or '-'}", f"Trigger: {trigger_text(p)}", "STEPS (execution order):"]
    for s in p["steps"]:
        if s["kind"] == "trigger":
            continue
        extra = f" — {s['connector_label']}: {s['op_title']}" if s.get("connector") else f" — runs {s['child']}" if s.get("child") else ""
        br = "; ".join(f"{o.get('option')} -> {o.get('to')}" for o in s.get("options") or [])
        lines.append(f"- {s['name']} [{s['kind']}{extra}]" + (f" branches: {br}" if br else "") + (f" ({s['description'][:150]})" if s.get("description") else ""))
    reply = parse_json(svc.llm.complete(AI_SYSTEM, "\n".join(lines)[:14000], json_mode=True))
    if not isinstance(reply, dict):
        raise ValueError("unusable reply")
    names = {s["name"] for s in p["steps"] if s["kind"] != "trigger"}
    by_name = {s["name"]: s for s in p["steps"]}
    out_phases, used = [], set()
    for ph in reply.get("phases") or []:
        steps = []
        for st in ph.get("steps") or []:
            n = st.get("step")
            if n in names and n not in used:
                used.add(n)
                steps.append({**by_name[n], "ai_text": str(st.get("text") or "").strip()[:400]})
        if steps:
            out_phases.append({"title": str(ph.get("title") or "Phase")[:60], "intro": str(ph.get("intro") or "")[:500], "steps": steps})
    missing = [s for s in p["steps"] if s["kind"] != "trigger" and s["name"] not in used]
    if missing:  # anything the model skipped keeps its factual sentence
        out_phases.append({"title": "Other steps", "intro": "", "steps": missing})
    g = reply.get("glance") or {}
    nodes, edges = g.get("nodes") or [], g.get("edges") or []
    return {"why": str(reply.get("why_it_matters") or "")[:1500], "summary": str(reply.get("summary") or "")[:600],
            "glance": glance_from_nodes(nodes, edges) if 3 <= len(nodes) <= 10 else None, "phases": out_phases}


# ---------------------------------------------------------------- the document

def _cell(text) -> str:
    return str(text or "").replace("|", "\\|").replace("\n", " ")


def build_markdown(svc, spec: dict, ai: bool, history: list[dict]) -> tuple[str, dict]:
    """(markdown, stats). spec comes from resolve()."""
    pbs = [analyse(item, spec["names"], svc) for item in spec["playbooks"]]
    all_calls = {c for p in pbs for c in p["calls"]}
    ai_parts, ai_errors = {}, []
    if ai:
        ranked = sorted(pbs, key=lambda p: (role_of(p, all_calls).startswith("Demo"), -len(p["steps"])))[:AI_PLAYBOOK_LIMIT]
        for p in ranked:
            try:
                ai_parts[p["uuid"]] = ai_writeup(p, svc)
            except Exception as exc:
                from .chat import friendly_error

                ai_errors.append(f"{p['name']}: {friendly_error(exc)}")
                if "quota" in str(exc).lower() or "limit" in str(exc).lower():
                    break
    meta, info, src = spec["meta"], spec["meta"].get("info") or {}, spec["source"]
    main = max(pbs, key=lambda p: (not role_of(p, all_calls).startswith(("Demo", "Sub")), len(p["steps"])))
    out = [f"# {spec['title']} — Playbook Documentation", ""]
    if src.get("type") == "github":
        out += [f"**Solution Pack:** `{src['repo']}`", f"**Source:** {meta.get('url')}"]
        bits = [f"**Version:** {info.get('version')}" if info.get("version") else "", f"**Publisher:** {info.get('publisher')}" if info.get("publisher") else "",
                f"**Contributor:** {info.get('contributor')}" if info.get("contributor") else ""]
        if any(bits):
            out.append(" | ".join(b for b in bits if b))
        if info.get("fsrMinCompatibility"):
            out.append(f"**Compatible Version:** FortiSOAR v{info['fsrMinCompatibility']} and above")
    elif src.get("type") == "upload":
        out += [f"**Source:** user-supplied export — {src.get('name')}" + (f" (Git: {src.get('url')})" if src.get("url") else ""),
                "_Built from the playbook JSON only: there is no README / info.json, so dependencies and record sets are not listed._"]
    else:
        out += [f"**Source:** playbook developed in the Graph RAG designer (build {src.get('build')})",
                "_Built from the generated playbook JSON._"]
    out += [f"**Generated:** {datetime.now(timezone.utc).strftime('%Y-%m-%d')} · **Write-up:** {'AI-assisted (facts from the JSON)' if ai_parts else 'standard (no AI)'}", "", "---", "",
            "## 1. Overview", ""]
    overview = info.get("description") or (ai_parts.get(main["uuid"]) or {}).get("summary") or main["description"] or \
        f"{len(pbs)} playbook(s). The main one, **{main['name']}**, runs: {trigger_text(main).lower()}."
    out += [overview.strip(), ""]
    if meta.get("usage"):
        first = next((para.strip() for para in re.split(r"\n\s*\n", re.sub(r"^#.*$", "", meta["usage"], flags=re.M)) if len(para.strip()) > 80), "")
        if first:
            out += [f"> From the pack's usage guide: {first[:700]}", ""]
    why = (ai_parts.get(main["uuid"]) or {}).get("why") or why_it_matters(pbs)
    out += ["### Why It Matters to the SOC Team", "", why, ""]
    deps = [d for d in info.get("dependencies") or [] if isinstance(d, dict)]
    if src.get("type") == "github":
        out += ["### Dependencies", ""]
        out += (["| Solution Pack | Version | Purpose |", "|---|---|---|"] + [f"| {_cell(d.get('label') or d.get('name'))} | {_cell(d.get('minVersion') or '–')} | Prerequisite solution pack |" for d in deps]
                if deps else ["_No prerequisite solution packs listed in info.json._"]) + [""]
    con_rows: dict[str, dict] = {}
    for p in pbs:
        for s in p["steps"]:
            if s.get("connector"):
                r = con_rows.setdefault(s["connector"], {"label": s["connector_label"], "ops": [], "pbs": set()})
                r["ops"].append(s["op_title"])
                r["pbs"].add(p["name"])
    for c in (info.get("contents") or {}).get("connectors") or []:
        if isinstance(c, dict) and c.get("apiName") and c["apiName"] not in con_rows:
            con_rows[c["apiName"]] = {"label": c.get("name") or c["apiName"], "ops": [], "pbs": set()}
    out += ["### Connectors Used", "", "| Connector | API Name | Purpose |", "|---|---|---|"]
    out += [f"| {_cell(v['label'])} | `{k}` | {_cell(', '.join(dict.fromkeys(v['ops']))) or 'listed by the pack'}"
            + (f" (in {len(v['pbs'])} playbook{'s' if len(v['pbs']) != 1 else ''})" if v["pbs"] else "") + " |" for k, v in sorted(con_rows.items())] or ["| – | – | No connectors |"]
    if src.get("type") == "github":
        out += ["", "> ⚠️ Installing this solution pack will install or upgrade the connectors listed above."]
    out += ["", "---", ""]
    colls: dict[str, list] = {}
    for p in pbs:
        colls.setdefault(p["collection"], []).append(p)
    n = 2
    for cname, items in colls.items():
        out += [f"## {n}. Playbook Collection: `{cname}`", "", "| Playbook | Type | Purpose |", "|---|---|---|"]
        out += [f"| **{_cell(p['name'])}** | {role_of(p, all_calls)} | {_cell((p['description'] or trigger_text(p))[:160])} |" for p in items]
        out += ["", "---", ""]
        n += 1
    for p in pbs:
        role = role_of(p, all_calls)
        heading = "Sub-playbook" if role.startswith("Sub") else "Playbook"
        part = ai_parts.get(p["uuid"]) or {}
        out += [f"## {n}. {heading}: {p['name']}", "", f"**Trigger:** {trigger_text(p)}",
                f"**Description:** {part.get('summary') or p['description'] or '_No description in the playbook._'}", ""]
        if p["active"] is False:
            out += ["_The playbook is exported as **inactive**._", ""]
        if p["config"]:
            out += ["### Configuration Variables", "", "| Variable | Default / Source | Purpose |", "|---|---|---|"]
            for c in p["config"]:
                note = f" — **{'; '.join(c['flags'])}**" if c["flags"] else ""
                out.append(f"| `{_cell(c['name'])}` | `{_cell(c['value'])}` | {c['source']}{note} |")
            out.append("")
        out += ["### Flow at a Glance", "", "A simplified view of the story, stripped of implementation detail.", "", "```mermaid",
                part.get("glance") or glance(p), "```", "", "### Detailed Flow", "", "```mermaid", detailed_flow(p), "```", "",
                "### Step-by-step Description", ""]
        phs = part.get("phases") or phases(p)
        for i, ph in enumerate(phs, 1):
            out += [f"**Phase {i} — {ph['title']}**", ""]
            if ph.get("intro"):
                out += [ph["intro"], ""]
            k = 1
            for s in ph["steps"]:
                out.append(f"{k}. **{s['name']}** — {s.get('ai_text') or step_sentence(s)}")
                k += 1
                for b in branch_points(s):
                    out.append(f"{k}. {b}")
                    k += 1
            out.append("")
        out += ["---", ""]
        n += 1
    # operational notes
    notes = []
    approvals = [(p["name"], s) for p in pbs for s in p["steps"] if s["kind"] == "manual_input"]
    if approvals:
        notes.append("- **Human approval / input gates:** " + "; ".join(
            f"*{s['name']}* in {pn} ({'assigned to ' + ', '.join(s['teams']) if s.get('teams') else 'no team assigned in the export — assign one after import'})"
            for pn, s in approvals[:8]) + ".")
    custom = [(p["name"], c) for p in pbs for c in p["config"] if c["flags"]]
    if custom:
        notes.append("- **Customise before production use:**")
        notes += [f"  - `{c['name']}` in *{pn}*: {'; '.join(c['flags'])}." for pn, c in custom[:15]]
    if any(p["demo"] for p in pbs):
        notes.append("- **Demo mode:** some steps can return mock results (`useMockOutput` / `Demo_mode`); make sure it's off in production.")
    contain_no_gate = [p["name"] for p in pbs if any(s.get("purpose") == "contain" for s in p["steps"]) and not any(s["kind"] == "manual_input" for s in p["steps"])]
    gated = [p["name"] for p in pbs if any(s.get("purpose") == "contain" for s in p["steps"]) and any(s["kind"] == "manual_input" for s in p["steps"])]
    if contain_no_gate and gated:
        notes.append(f"- **Asymmetry:** {', '.join(gated)} ask for approval before response actions, but {', '.join(contain_no_gate)} do not.")
    elif contain_no_gate:
        notes.append(f"- **No approval before response actions** in {', '.join(contain_no_gate)}; approvals may happen in a calling playbook.")
    gv = (info.get("contents") or {}).get("globalVariables") or []
    if gv:
        notes.append("- **Global variables used by the pack:** " + ", ".join(f"`{g.get('name')}`" for g in gv if isinstance(g, dict))[:600] + ".")
    if src.get("type") == "github":
        notes.append("- **Cloning recommended:** clone these playbooks before customising them, so solution-pack upgrades don't overwrite your changes.")
    if ai_errors:
        notes.append("- **AI write-up not applied for:** " + "; ".join(ai_errors[:5]) + " (standard write-up used instead).")
    out += [f"## {n}. Operational Notes", ""] + (notes or ["- No approval gates, placeholders or demo mode detected."]) + ["", "---", ""]
    n += 1
    rs = (info.get("contents") or {}).get("recordSets") or []
    if rs:
        out += [f"## {n}. Record Set", "", "| Record Set | Records |", "|---|---|"]
        out += [f"| {_cell(r.get('name'))} | {r.get('count', '–')} |" for r in rs if isinstance(r, dict)] + ["", "---", ""]
        n += 1
    out += [f"## {n}. Version History", "", "One row per generation of this document — new rows are appended, earlier rows are never edited.", "",
            "| Version | Date | Changed By | Summary of Changes |", "|---|---|---|---|"]
    out += [f"| {h['version']} | {h['date']} | {_cell(h['by'])} | {_cell(h['summary'])} |" for h in history]
    origin = {"github": f"the playbook JSON and repository files of `{src.get('repo')}`", "upload": "the uploaded playbook JSON",
              "develop": "the playbook JSON produced by the designer"}.get(src.get("type"), "the playbook JSON")
    out += ["", "---", "", f"*Documentation generated from {origin}.*"]
    return "\n".join(out) + "\n", {"playbooks": len(pbs), "ai_used": len(ai_parts), "ai_errors": ai_errors}


# ---------------------------------------------------------------- store (per workspace)

def _index() -> dict:
    try:
        return json.loads((_ws(DIR) / "index.json").read_text())
    except (OSError, ValueError):
        return {}


def _save_index(data: dict) -> None:
    d = _ws(DIR)
    d.mkdir(parents=True, exist_ok=True)
    (d / "index.json").write_text(json.dumps(data, indent=1))


def estimate(svc, kind: str, target: str) -> dict:
    spec = resolve(svc, kind, target)
    n = len(spec["playbooks"])
    return {"title": spec["title"], "playbooks": n, "ai_requests": min(n, AI_PLAYBOOK_LIMIT), "ai_limit": AI_PLAYBOOK_LIMIT}


def generate(svc, kind: str, target: str, ai: bool, by: str, requested_by: str = "", note: str = "") -> dict:
    spec = resolve(svc, kind, target)
    key = f"{kind}:{target}"
    with _lock:
        idx = _index()
        doc = next((d for d in idx.values() if d["key"] == key), None)
        if not doc:
            doc = {"id": uuidlib.uuid4().hex[:10], "key": key, "kind": kind, "target": target, "history": [], "created": None}
        history = list(doc["history"])
    version = "1.0" if not history else f"1.{len(history)}"
    who = (requested_by or by or "-").strip()[:60]
    summary = ((f"Initial documentation generated from {spec['source'].get('repo') or spec['source'].get('name') or 'the playbook JSON'}: "
                if not history else "Regenerated: ")
               + f"overview, connectors, flow diagrams and step-by-step description for {len(spec['playbooks'])} playbook(s)"
               + (" with an AI-assisted write-up" if ai else "") + (f". Note: {note.strip()[:200]}" if note.strip() else "") + ".")
    row = {"version": version, "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"), "by": who, "summary": summary}
    markdown, stats = build_markdown(svc, spec, ai, history + [row])
    if ai and not stats["ai_used"]:
        row["summary"] = row["summary"].replace(" with an AI-assisted write-up", " (AI write-up unavailable: standard write-up used)")
        markdown = markdown.replace(_cell(summary), _cell(row["summary"]))
    with _lock:
        idx = _index()
        doc.update(title=spec["title"], history=history + [row], updated=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                   created=doc.get("created") or datetime.now(timezone.utc).isoformat(timespec="seconds"),
                   ai=bool(stats["ai_used"]), playbooks=stats["playbooks"], source=spec["source"])
        idx[doc["id"]] = doc
        _save_index(idx)
        (_ws(DIR) / f"{doc['id']}.md").write_text(markdown)
    return {**doc, "markdown": markdown, "stats": stats}


def list_docs() -> list[dict]:
    return sorted(_index().values(), key=lambda d: d.get("updated") or "", reverse=True)


def get(doc_id: str) -> dict | None:
    if not re.fullmatch(r"[a-f0-9]{10}", doc_id or ""):
        return None
    doc = _index().get(doc_id)
    if not doc:
        return None
    try:
        return {**doc, "markdown": (_ws(DIR) / f"{doc_id}.md").read_text()}
    except OSError:
        return None


def delete(doc_id: str) -> bool:
    with _lock:
        idx = _index()
        if idx.pop(doc_id, None) is None:
            return False
        _save_index(idx)
        (_ws(DIR) / f"{doc_id}.md").unlink(missing_ok=True)
    return True
