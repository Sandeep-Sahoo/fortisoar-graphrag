"""Deterministic Mermaid diagrams for a playbook (built from the graph, never by the LLM).

* flowchart: the step logic, decisions and branch labels, and approval gates.
* sequence:  who talks to whom: trigger -> playbook -> connectors / child playbooks / analyst.
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


# Every step is drawn as a circle (trigger as a double circle); colour tells the step type apart.
STEP_CLASS = {"trigger": "trigger", "decision": "decision", "manual_input": "approval",
              "connector": "connector", "reference": "reference"}


def flowchart(flow: dict) -> str:
    ids = {s["key"]: f"s{i}" for i, s in enumerate(flow["steps"])}
    lines = ["flowchart TD"]
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
        else:
            label = _label(s["name"])
        shape = ('((("', '")))') if kind == "trigger" else ('(("', '"))')
        lines.append(f"    {nid}{shape[0]}{label}{shape[1]}:::{STEP_CLASS.get(kind, 'step')}")
        if s.get("has_secret"):
            lines.append(f"    class {nid} secret")
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


def sequence(flow: dict) -> str:
    participants: dict[str, str] = {}

    def actor(name: str, prefix: str) -> str:
        if name not in participants:
            participants[name] = f"{prefix}{len(participants)}"
        return participants[name]

    body = []
    trigger_actor = actor(f"Trigger ({_trigger_text(flow)})", "T")
    pb = actor(f"Playbook: {flow['name']}", "P")
    for s in flow["steps"]:
        kind, name = s["kind"], _msg(s["name"])
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
    body.append(f"    {pb}-->>{'-' if started else ''}{trigger_actor}: finished")

    head = ["sequenceDiagram", "    autonumber"]
    for name, alias in participants.items():
        keyword = "actor" if alias.startswith("A") else "participant"
        head.append(f"    {keyword} {alias} as {_msg(name)}")
    return "\n".join(head + body)
