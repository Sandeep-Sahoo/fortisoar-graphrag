"""Deterministic Mermaid diagrams for a playbook (built from the graph, never by the LLM).

* flowchart: the step logic, decisions and branch labels, and approval gates.
* sequence:  who talks to whom: trigger -> playbook -> connectors / child playbooks / analyst.
* linear:    every step in execution order, top to bottom, for the animated step-by-step view.
* scenarios: every end-to-end path through the branches, to play one at a time on the flowchart.
"""
from __future__ import annotations

import re


def _label(text: str) -> str:
    """Escape text for a quoted Mermaid flowchart label."""
    return (str(text or "").replace("&", "#amp;").replace('"', "#quot;")
            .replace("<", "#lt;").replace(">", "#gt;"))


def _two_line(title: str, subtitle: str) -> str:
    """Escaped title plus an italic second line (the only raw HTML we emit)."""
    return f"{_label(title)}<br/><i>{_label(subtitle)}</i>"


def _msg(text: str) -> str:
    """Sequence-diagram message text: `;` and `#` have special meaning there."""
    return re.sub(r"[;#\n]", " ", str(text or "")).strip() or "-"


def _trigger_text(flow: dict) -> str:
    modules = ", ".join(flow.get("modules") or [])
    return f"{flow.get('trigger_type') or 'trigger'}{' on ' + modules if modules else ''}"


MAX_CHILD_DEPTH = 3


def expand_children(flow: dict, load, depth: int = MAX_CHILD_DEPTH, _seen: frozenset = frozenset()) -> dict:
    """Inline the steps of the child playbooks a flow calls, so diagrams and scenarios include them.

    load(step) -> the child's flow (or None) for a reference step. The child's steps go right after the step that
    calls it: caller -> child's first step, child's last steps -> whatever followed the caller. Children of children
    are expanded too, up to `depth` levels; a playbook already being expanded (a call cycle) is left as a plain call.
    Inlined steps carry group (the child's name) and group_id (unique per call); the child's trigger becomes child_start."""
    if depth <= 0:
        return flow
    out = []
    seen = _seen | {flow.get("uuid") or flow.get("name")}  # this playbook and every caller above it
    for s in flow["steps"]:
        s = dict(s)
        ident = s.get("child_uuid") or s.get("child")
        child = load(s) if s["kind"] == "reference" and ident and ident not in seen else None
        if not child or not child.get("steps"):
            out.append(s)
            continue
        child = expand_children(child, load, depth - 1, seen | {ident})
        prefix = f"{s['key']}::"
        inlined = []
        for c in child["steps"]:
            c = dict(c)
            c["key"] = prefix + c["key"]
            c["next"] = [{"to": prefix + e["to"], "label": e.get("label") or ""} for e in c.get("next") or []]
            c.setdefault("group", child["name"])     # a grandchild keeps its own (innermost) box
            c.setdefault("group_id", prefix)
            if c["kind"] == "trigger":
                c["kind"] = "child_start"
            inlined.append(c)
        ends = [c for c in inlined if not c["next"]]
        after = [dict(e) for e in s.get("next") or []]
        s["next"] = [{"to": inlined[0]["key"], "label": ""}] + ([] if ends else after)
        for c in ends:
            c["next"] = [dict(e) for e in after]
        s["expanded"] = True
        out += [s] + inlined
    return {**flow, "steps": out}


# Every step is drawn as a circle (trigger as a double circle); colour tells the step type apart.
STEP_CLASS = {"trigger": "trigger", "decision": "decision", "manual_input": "approval",
              "connector": "connector", "reference": "reference", "child_start": "reference"}


def _groups(steps: list[dict]) -> list[tuple[str, str, list[int]]]:
    """(group_id, child name, step indexes) for every inlined child playbook, in order of appearance."""
    seen: dict[str, tuple[str, list[int]]] = {}
    for i, s in enumerate(steps):
        if s.get("group_id"):
            seen.setdefault(s["group_id"], (s["group"], []))[1].append(i)
    return [(gid, name, idx) for gid, (name, idx) in seen.items()]


def _direction(direction: str, vertical: str) -> str:
    return "LR" if str(direction).upper() in ("LR", "H", "HORIZONTAL") else vertical


def flowchart(flow: dict, direction: str = "TD") -> str:
    ids = {s["key"]: f"s{i}" for i, s in enumerate(flow["steps"])}
    lines = [f"flowchart {_direction(direction, 'TD')}"]
    node_lines: dict[str, list[str]] = {}
    for s in flow["steps"]:
        nid, kind = ids[s["key"]], s["kind"]
        if kind == "trigger":
            label = _two_line(s["name"], _trigger_text(flow))
        elif kind == "decision":
            label = _two_line(s["name"], "decision")
        elif kind == "manual_input":
            label = _two_line(s["name"], "approval")
        elif kind == "connector":
            label = _two_line(s["name"], f"{s['connector']}.{s['operation']}")
        elif kind == "reference":
            label = _two_line(s["name"], f"calls: {s.get('child') or 'dynamic playbook'}")
        elif kind == "child_start":
            label = _two_line(s["name"], f"start of {s.get('group')}")
        else:
            label = _label(s["name"])
        shape = ('((("', '")))') if kind == "trigger" else ('(("', '"))')
        node_lines[nid] = [f"    {nid}{shape[0]}{label}{shape[1]}:::{STEP_CLASS.get(kind, 'step')}"]
        if s.get("has_secret"):
            node_lines[nid].append(f"    class {nid} secret")
    groups = _groups(flow["steps"])
    grouped = {f"s{i}" for _, _, idx in groups for i in idx}
    for nid, nl in node_lines.items():
        if nid not in grouped:
            lines += nl
    for g, (_, name, idx) in enumerate(groups):  # each inlined child playbook in its own labelled box
        lines.append(f'    subgraph child{g}["▸ Child playbook: {_label(name)}"]')
        for i in idx:
            lines += ["  " + ln for ln in node_lines[f"s{i}"]]
        lines += ["    end", f"    style child{g} fill:transparent,stroke:#378ADD,stroke-width:1.5px,stroke-dasharray:6 4"]
    for s in flow["steps"]:
        for edge in s.get("next") or []:
            if edge["to"] in ids:
                arrow = f'-->|"{_label(edge["label"])}"|' if edge.get("label") else "-->"
                lines.append(f"    {ids[s['key']]} {arrow} {ids[edge['to']]}")
    lines += [
        "    classDef trigger fill:#EEEDFE,stroke:#534AB7,color:#26215C",
        "    classDef decision fill:#FAEEDA,stroke:#854F0B,color:#412402",
        "    classDef approval fill:#FBEAF0,stroke:#993556,color:#4B1528",
        "    classDef connector fill:#E1F5EE,stroke:#0F6E56,color:#04342C",
        "    classDef reference fill:#E6F1FB,stroke:#185FA5,color:#042C53",
        "    classDef step fill:#F1EFE8,stroke:#5F5E5A,color:#2C2C2A",
        "    classDef secret stroke:#E24B4A,stroke-width:3px,stroke-dasharray:4 3",
    ]
    return "\n".join(lines)


LINEAR_KIND = {"trigger": "Trigger", "decision": "Decision", "manual_input": "Approval",
               "connector": "Connector", "reference": "Child playbook", "child_start": "Child start"}


def linear(flow: dict, direction: str = "TB") -> str:
    """Every step in execution order, one box under the other (no overlap), numbered.

    Node ids are s<index> so the UI can walk and highlight them in order; branch labels from
    decisions are kept on the links that join consecutive steps."""
    steps = flow["steps"]
    lines = [f"flowchart {_direction(direction, 'TB')}"]
    for i, s in enumerate(steps):
        kind = s["kind"]
        if kind == "trigger":
            sub = _trigger_text(flow)
        elif kind == "connector":
            sub = f"{s['connector']}.{s['operation']}"
        elif kind == "reference":
            sub = f"calls: {s.get('child') or 'dynamic playbook'}"
        else:
            sub = ""
        if s.get("group"):
            sub = f"{sub + ' · ' if sub else ''}in child: {s['group']}"
        head = f"{i + 1} · {LINEAR_KIND.get(kind, (kind or 'step').replace('_', ' ').capitalize())}"
        label = f"<b>{_label(head)}</b><br/>{_label(s['name'])}" + (f"<br/><i>{_label(sub)}</i>" if sub else "")
        lines.append(f'    s{i}("{label}"):::{STEP_CLASS.get(kind, "step")}')
        if s.get("has_secret"):
            lines.append(f"    class s{i} secret")
        if s.get("group"):
            lines.append(f"    class s{i} inchild")
    for i in range(len(steps) - 1):
        label = next((e.get("label") for e in steps[i].get("next") or []
                      if e["to"] == steps[i + 1]["key"] and e.get("label")), None)
        lines.append(f'    s{i} -->|"{_label(label)}"| s{i + 1}' if label else f"    s{i} --> s{i + 1}")
    lines += [
        "    classDef trigger fill:#EEEDFE,stroke:#534AB7,color:#26215C",
        "    classDef decision fill:#FAEEDA,stroke:#854F0B,color:#412402",
        "    classDef approval fill:#FBEAF0,stroke:#993556,color:#4B1528",
        "    classDef connector fill:#E1F5EE,stroke:#0F6E56,color:#04342C",
        "    classDef reference fill:#E6F1FB,stroke:#185FA5,color:#042C53",
        "    classDef step fill:#F1EFE8,stroke:#5F5E5A,color:#2C2C2A",
        "    classDef secret stroke:#E24B4A,stroke-width:3px,stroke-dasharray:4 3",
        "    classDef inchild stroke-dasharray:6 4",
    ]
    return "\n".join(lines)


MAX_SCENARIOS = 100


def scenarios(flow: dict) -> dict:
    """Every way to run the playbook end to end: one path per combination of branches.

    A path starts at the trigger (or at each step nothing leads to) and follows NEXT links until a step with
    no way on. A link back to a step already on the path is a loop: the path ends there, marked as such.
    Indexes match the flowchart / linear node ids (s<index>), so the UI can play a path on the drawing."""
    steps = flow["steps"]
    index = {s["key"]: i for i, s in enumerate(steps)}
    outs = [[(index[e["to"]], e.get("label") or "") for e in s.get("next") or [] if e["to"] in index] for s in steps]
    has_in = {to for out in outs for to, _ in out}
    starts = [i for i, s in enumerate(steps) if s["kind"] == "trigger"] or \
             [i for i in range(len(steps)) if i not in has_in] or ([0] if steps else [])
    found: list[dict] = []
    truncated = False

    def walk(path: list[int], choices: list[dict]) -> None:
        nonlocal truncated
        if len(found) >= MAX_SCENARIOS:
            truncated = True
            return
        here = path[-1]
        if not outs[here]:
            found.append({"path": path, "choices": choices, "end": "end"})
            return
        branching = len(outs[here]) > 1
        for to, label in outs[here]:
            # a label on a single link (e.g. an approval's only option) is still a choice worth showing
            choice = [{"step": here, "label": label or f"to {steps[to]['name']}"}] if branching or label else []
            if to in path:
                found.append({"path": path, "choices": choices + choice, "end": "loop", "loops_to": to})
            else:
                walk(path + [to], choices + choice)

    for start in starts:
        walk([start], [])
    for n, sc in enumerate(found, 1):
        picked = [f"{steps[c['step']]['name']}: {c['label']}" for c in sc["choices"]]
        sc["name"] = " → ".join(c["label"] for c in sc["choices"]) or "Main path"
        sc["summary"] = "; ".join(picked)
        sc["id"] = n
        sc["steps"] = len(sc["path"])
        sc["last"] = steps[sc["path"][-1]]["name"]
    return {"scenarios": found, "truncated": truncated}


def sequence(flow: dict) -> str:
    participants: dict[str, str] = {}

    def actor(name: str, prefix: str) -> str:
        if name not in participants:
            participants[name] = f"{prefix}{len(participants)}"
        return participants[name]

    body = []
    trigger_actor = actor(f"Trigger ({_trigger_text(flow)})", "T")
    pb = actor(f"Playbook: {flow['name']}", "P")
    main = pb
    for s in flow["steps"]:
        kind, name = s["kind"], _msg(s["name"])
        pb = actor(f"Playbook: {s['group']}", "P") if s.get("group") else main  # inlined child steps: the child's lifeline
        if kind == "child_start":
            body.append(f"    Note over {pb}: starts")
            continue
        if kind == "reference" and s.get("expanded"):  # the child's own steps follow on its lifeline
            callee = actor(f"Playbook: {s.get('child')}", "P")
            body.append(f"    {pb}->>{callee}: {name}")
            continue
        if kind == "trigger":
            body.append(f"    {trigger_actor}->>+{pb}: {name}")
        elif kind == "connector":
            c = actor(s["connector"], "C")
            body.append(f"    {pb}->>+{c}: {_msg(s['operation'])} ({name})")
            body.append(f"    {c}-->>-{pb}: result")
            if s.get("has_secret"):
                body.append(f"    Note over {pb},{c}: hard-coded secret in step (redacted)")
        elif kind == "reference":
            child = actor(f"Playbook: {s.get('child') or 'dynamic'}", "R")
            body.append(f"    {pb}->>+{child}: {name}")
            body.append(f"    {child}-->>-{pb}: done")
        elif kind == "manual_input":
            analyst = actor("Analyst", "A")
            body.append(f"    {pb}->>+{analyst}: {name}")
            body.append(f"    {analyst}-->>-{pb}: decision")
        elif kind == "decision":
            labels = [e["label"] for e in s.get("next") or [] if e.get("label")]
            note = f"{name}" + (f" -> {', '.join(_msg(l) for l in labels)}" if labels else "")
            body.append(f"    Note over {pb}: {note}")
        else:
            body.append(f"    {pb}->>{pb}: {name}")
    # Mermaid errors on unbalanced activations, so only deactivate if the trigger activated.
    started = any(st["kind"] == "trigger" for st in flow["steps"])
    body.append(f"    {main}-->>{'-' if started else ''}{trigger_actor}: finished")

    head = ["sequenceDiagram", "    autonumber"]
    for name, alias in participants.items():
        keyword = "actor" if alias.startswith("A") else "participant"
        head.append(f"    {keyword} {alias} as {_msg(name)}")
    return "\n".join(head + body)


KINDS = ("flowchart", "sequence", "linear", "scenarios")


def render(flow: dict, kind: str = "flowchart", direction: str = "TB") -> dict:
    """One diagram of a flow for the UI: {mermaid} plus, for scenarios, the paths to play on the flowchart.
    direction: TB (vertical) or LR (horizontal); the sequence diagram has only one orientation."""
    if kind == "sequence":
        return {"mermaid": sequence(flow)}
    if kind == "linear":
        return {"mermaid": linear(flow, direction), "graph": graph(flow, linear_chain=True)}
    if kind == "scenarios":
        return {"mermaid": flowchart(flow, direction), "graph": graph(flow), **scenarios(flow)}
    return {"mermaid": flowchart(flow, direction), "graph": graph(flow)}


def graph(flow: dict, linear_chain: bool = False) -> dict:
    """The steps as plain nodes / links for the 3D view of a diagram card. Node ids are s<index>, as in the
    flowchart and linear drawings, so the step player can drive both. linear_chain: link consecutive steps
    (the Linear layout) instead of the NEXT links."""
    steps = flow["steps"]
    index = {s["key"]: i for i, s in enumerate(steps)}
    nodes = []
    for i, s in enumerate(steps):
        kind = s["kind"]
        detail = (_trigger_text(flow) if kind == "trigger" else f"{s.get('connector')}.{s.get('operation')}" if kind == "connector"
                  else f"calls: {s.get('child') or 'dynamic playbook'}" if kind == "reference"
                  else f"start of {s.get('group')}" if kind == "child_start" else "")
        nodes.append({"id": f"s{i}", "name": s["name"], "kind": kind, "detail": detail, "group": s.get("group") or ""})
    if linear_chain:
        links = []
        for i in range(len(steps) - 1):
            label = next((e.get("label") for e in steps[i].get("next") or []
                          if e["to"] == steps[i + 1]["key"] and e.get("label")), "")
            links.append({"source": f"s{i}", "target": f"s{i + 1}", "label": label or ""})
    else:
        links = [{"source": f"s{i}", "target": f"s{index[e['to']]}", "label": e.get("label") or ""}
                 for i, s in enumerate(steps) for e in s.get("next") or [] if e["to"] in index]
    return {"nodes": nodes, "links": links}
