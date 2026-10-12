"""Documentation exports built from the graph and the reviewer (no model calls).

* playbook / solution pack write-ups in Markdown (overview, connectors, flow-at-a-glance + detailed
  Mermaid diagrams, steps, links, review score, append-only version history)
* the same as a print-ready HTML page (Save as PDF from the browser)
* review reports in Markdown
"""
from __future__ import annotations

import html
import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from .workspace import path as _ws

HISTORY = Path(__file__).resolve().parent.parent / "data" / "exports" / "history.json"
_lock = threading.Lock()
STAGES = {  # step kind -> stage label for the simplified diagram
    "set_variable": "Prepare data", "find_records": "Look up records", "utility": "Prepare data",
    "connector": "Call external systems", "code_snippet": "Run custom code", "decision": "Decide",
    "manual_input": "Ask an analyst", "create_record": "Create records", "update_record": "Update records",
    "reference": "Run child playbooks", "wait": "Wait", "action": "Other actions",
}


def _md_escape(text: str) -> str:
    return (text or "").replace("|", "\\|").replace("\n", " ")


def _record_export(key: str, user: str, summary: str) -> list[dict]:
    """Append-only generation log (never edits earlier rows)."""
    with _lock:
        try:
            data = json.loads(_ws(HISTORY).read_text())
        except (OSError, ValueError):
            data = {}
        rows = data.setdefault(key, [])
        version = f"1.{len(rows)}"
        rows.append({"version": version, "date": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
                     "by": user or "-", "summary": summary})
        _ws(HISTORY).parent.mkdir(parents=True, exist_ok=True)
        _ws(HISTORY).write_text(json.dumps(data, indent=1))
        return rows


def glance_diagram(flow: dict) -> str:
    """Story-level flowchart: consecutive steps of the same stage collapse into one node (max ~8)."""
    steps = [s for s in flow["steps"] if s["kind"] != "trigger"]
    trig = (flow.get("trigger_type") or "start").replace("_", " ")
    mods = ", ".join(flow.get("modules") or [])
    nodes, last = [f'T(["{trig}{(" on " + mods) if mods else ""}"])'], None
    stages = []
    for s in steps:
        stage = STAGES.get(s["kind"], "Other actions")
        detail = s.get("connector") if s["kind"] == "connector" else None
        if stages and stages[-1]["stage"] == stage:
            if detail and detail not in stages[-1]["details"]:
                stages[-1]["details"].append(detail)
            stages[-1]["count"] += 1
        else:
            stages.append({"stage": stage, "details": [detail] if detail else [], "count": 1, "kind": s["kind"]})
    while len(stages) > 8:  # merge the smallest neighbours until it fits
        i = min(range(len(stages) - 1), key=lambda j: stages[j]["count"] + stages[j + 1]["count"])
        a, b = stages[i], stages.pop(i + 1)
        a.update(stage=f"{a['stage']} / {b['stage']}", count=a["count"] + b["count"], details=a["details"] + b["details"])
    for i, st in enumerate(stages, 1):
        label = st["stage"] + (f"<br/><small>{', '.join(dict.fromkeys(st['details']))[:60]}</small>" if st["details"] else "")
        label = label.replace('"', "'")
        nodes.append(f'S{i}{{"{label}"}}' if st["kind"] == "decision" else f'S{i}["{label}"]')
    lines = ["flowchart TD"] + [f"  {n}" for n in nodes]
    ids = ["T"] + [f"S{i}" for i in range(1, len(stages) + 1)]
    lines += [f"  {a} --> {b}" for a, b in zip(ids, ids[1:])]
    return "\n".join(lines)


def playbook_markdown(svc, uuid: str, user: str = "-") -> tuple[str, str]:
    """(markdown, playbook name)."""
    from . import diagrams

    p = svc.retriever.get_playbook(uuid)
    if not p:
        raise KeyError(uuid)
    flow = svc.retriever.step_flow(uuid)
    meta = svc.graph.read("MATCH (p:Playbook {uuid: $u}) RETURN p.pack AS pack, p.pack_type AS pack_type, p.repo AS repo, "
                          "p.source_id AS source_id, p.synced_at AS synced", u=uuid)[0]
    review = _review_for(uuid, meta)
    org = svc.settings.fetch_org
    source = (f"GitHub [{org}/{meta['repo']}](https://github.com/{org}/{meta['repo']})" if meta.get("repo")
              else f"Your upload: {meta['pack']}" if meta.get("pack_type") == "upload" else meta.get("pack") or "graph")
    steps = [s for s in p["steps"] if s["kind"] != "trigger"]
    connectors: dict[str, dict] = {}
    for s in steps:
        if s.get("operation"):
            con, op = s["operation"].split(".", 1)
            entry = connectors.setdefault(con, {"ops": set(), "steps": 0})
            entry["ops"].add(op)
            entry["steps"] += 1
    out = [f"# {p['name']}", "",
           f"- **Source:** {source}", f"- **Collection:** {p.get('collection') or '–'}",
           f"- **Trigger:** {(p.get('trigger_type') or '').replace('_', ' ')}" + (f" on {', '.join(p['modules'])}" if p.get("modules") else ""),
           f"- **Active:** {'yes' if p.get('active') is not False else 'no'}" + (f" · **Tags:** {', '.join(p['tags'])}" if p.get("tags") else ""),
           f"- **Generated:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} from the knowledge graph (no AI)", "",
           "## Overview", "", p.get("description") or "_No description in the playbook._", ""]
    if review:
        out += ["## Review", "", f"**{review['verdict']}** — score {review['score']}/100, grade {review['grade']}. "
                f"{', '.join(f'{v} {k}' for k, v in review['counts'].items() if v) or 'no findings'}.", ""]
        if review.get("top"):
            out += [f"- {t}" for t in review["top"]] + [""]
    out += ["## Connectors used", ""]
    if connectors:
        out += ["| Connector | Operations | Steps |", "|---|---|---|"]
        out += [f"| {_md_escape(c)} | {_md_escape(', '.join(sorted(v['ops'])))} | {v['steps']} |" for c, v in sorted(connectors.items())]
    else:
        out.append("_None._")
    out += [""]
    if flow:
        out += ["## Flow at a glance", "", "```mermaid", glance_diagram(flow), "```", "",
                "## Detailed flow", "", "```mermaid", diagrams.flowchart(flow), "```", "",
                "## Sequence", "", "```mermaid", diagrams.sequence(flow), "```", ""]
    out += [f"## Steps ({len(steps)})", ""]
    for i, s in enumerate(steps, 1):
        nxt = [f"{n['to']}" + (f" [{n['label']}]" if n.get("label") else "") for n in s.get("next") or []]
        out.append(f"{i}. **{s['name']}** — {s['kind'].replace('_', ' ')}" + (f" · `{s['operation']}`" if s.get("operation") else "")
                   + (" · ⚠ hard-coded secret (redacted)" if s.get("has_secret") else "")
                   + (f" → {', '.join(nxt)}" if nxt else ""))
    out += [""]
    for title, items in (("Calls", p.get("calls")), ("Called by", p.get("called_by"))):
        if items:
            out += [f"## {title}", ""] + [f"- {x}" for x in items] + [""]
    if p.get("docs"):
        out += ["## Runbooks (internal docs)", ""] + [f"- {d['title']} ({d['status']})" for d in p["docs"]] + [""]
    if p.get("references"):
        out += ["## Related vendor docs", ""] + [f"- {d['title']} — {'; '.join(d.get('reasons') or [])}" for d in p["references"][:20]] + [""]
    rows = _record_export(f"playbook:{uuid}", user, f"Generated documentation ({len(steps)} steps, {len(connectors)} connectors)"
                          + (f", review score {review['score']}" if review else ""))
    out += ["## Version history", "", "| Version | Date | Changed by | Summary of changes |", "|---|---|---|---|"]
    out += [f"| {r['version']} | {r['date']} | {_md_escape(r['by'])} | {_md_escape(r['summary'])} |" for r in rows]
    return "\n".join(out) + "\n", p["name"]


def _review_for(uuid: str, meta: dict) -> dict | None:
    from .review import store

    if meta.get("pack_type") == "upload" and meta.get("source_id"):
        original = uuid.split("-", 2)[2] if uuid.startswith("u-") else uuid
        r = store.source_reviews(meta["source_id"]).get("reviews", {}).get(f"u:{meta['source_id']}:{original}")
        return store.light(r) if r else None
    return store.github_scores().get("playbooks", {}).get(f"g:{uuid}")


def pack_markdown(svc, repo: str, user: str = "-") -> tuple[str, str]:
    from .catalog import pack_detail

    p = pack_detail(svc.graph, repo)
    if not p:
        raise KeyError(repo)
    out = [f"# {p['label']}", "", f"- **Repository:** [{svc.settings.fetch_org}/{repo}]({p.get('url') or ''})",
           f"- **Kind:** {(p.get('kind') or '').replace('_', ' ')} · **Version:** {p.get('version') or '–'} · "
           f"**Publisher:** {p.get('publisher') or '–'} · **Category:** {p.get('category') or '–'}",
           f"- **Generated:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} from the knowledge graph (no AI)", "",
           "## Overview", "", p.get("description") or "_No description._", ""]
    if p.get("depends_on"):
        out += ["## Dependencies", ""] + [f"- {d['label']}" for d in p["depends_on"]] + [""]
    if p.get("connectors"):
        out += ["## Connectors used", ""] + [f"- {c}" for c in p["connectors"]] + [""]
    from .review import store

    gh = store.github_scores().get("playbooks", {})
    out += ["## Playbook collections", ""]
    for c in p.get("collections") or []:
        out += [f"### {c['name']}", "", "| Playbook | Trigger | Review |", "|---|---|---|"]
        for pb in c["playbooks"]:
            r = gh.get(f"g:{pb['uuid']}")
            out.append(f"| {_md_escape(pb['name'])} | {(pb.get('trigger') or '').replace('_', ' ')} | "
                       + (f"{r['score']} ({r['grade']})" if r else "–") + " |")
        out.append("")
    if p.get("docs"):
        out += ["## Documentation", ""] + [f"- {d['title']}" for d in p["docs"]] + [""]
    rows = _record_export(f"pack:{repo}", user, f"Generated pack documentation ({sum(len(c['playbooks']) for c in p.get('collections') or [])} playbooks)")
    out += ["## Version history", "", "| Version | Date | Changed by | Summary of changes |", "|---|---|---|---|"]
    out += [f"| {r['version']} | {r['date']} | {_md_escape(r['by'])} | {_md_escape(r['summary'])} |" for r in rows]
    return "\n".join(out) + "\n", p["label"]


def review_markdown(r: dict) -> str:
    out = [f"# Playbook review: {r['name']}", "",
           f"- **Source:** {r.get('source') or '–'} · **Collection:** {r.get('collection') or '–'}",
           f"- **Score:** {r['score']}/100 · **Grade:** {r['grade']} · **Verdict:** {r['verdict']}"
           + (" (provisional)" if r.get("provisional") else ""),
           f"- **Status:** {(r.get('meta') or {}).get('status') or 'open'} · **Reviewed:** {r['reviewed_at']}", "",
           "## Category scores", "", "| Category | Score |", "|---|---|"]
    out += [f"| {k} | {v} |" for k, v in r["categories"].items()]
    out += ["", "## Reviewer summary", "", r["summary"], "", "## Findings", ""]
    if r["findings"]:
        out += ["| Severity | Rule | Step | Finding | Suggestion |", "|---|---|---|---|---|"]
        out += [f"| {f['severity']} | {f['rule']} {_md_escape(f['title'])} | {_md_escape(f['step'] or '–')} | "
                f"{_md_escape(f['detail'])} | {_md_escape(f['suggestion'])} |" for f in r["findings"]]
    else:
        out.append("_No automated findings._")
    out += ["", "## Manual checklist", ""]
    out += [f"- [{'x' if c['answer'] == 'pass' else ' '}] **{c['title']}** — {c['answer'] or 'not answered'}"
            + (f" ({c['note']})" if c.get("note") else "") for c in r["checklist"]] or ["_None._"]
    if (r.get("meta") or {}).get("notes"):
        out += ["", "## Reviewer notes", "", r["meta"]["notes"]]
    if r.get("ai_note"):
        out += ["", f"## AI reviewer notes ({r['ai_note']['model']}, {r['ai_note']['at']})", "", r["ai_note"]["text"]]
    if r.get("history"):
        out += ["", "## Score history", "", "| Date | Score | Grade | Verdict | Changes |", "|---|---|---|---|---|"]
        for h in r["history"]:
            ch = h.get("changes") or {}
            out.append(f"| {h['at']} | {h['score']} | {h['grade']} | {h['verdict']} | "
                       + (", ".join(f"{len(v)} {k}" for k, v in ch.items() if v) or "–") + " |")
    return "\n".join(out) + "\n"


def print_html(title: str, markdown: str) -> str:
    """Standalone page: renders the Markdown + Mermaid, then offers Print / Save as PDF."""
    payload = json.dumps(markdown).replace("</", "<\\/")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>
 body {{ font: 14px/1.55 system-ui, -apple-system, "Segoe UI", sans-serif; color: #1f1f1d; background: #fff; max-width: 920px; margin: 0 auto; padding: 24px 16px; }}
 h1 {{ font-size: 24px; }} h2 {{ font-size: 18px; margin-top: 28px; border-bottom: 1px solid #e2e0d8; padding-bottom: 4px; }}
 table {{ border-collapse: collapse; width: 100%; font-size: 13px; }} th, td {{ border: 1px solid #e2e0d8; padding: 5px 8px; text-align: left; vertical-align: top; }}
 code {{ background: #f2f1ec; padding: 1px 4px; border-radius: 4px; }} .mermaid {{ margin: 10px 0; }}
 .bar {{ position: sticky; top: 0; background: #fff; padding: 8px 0; display: flex; gap: 8px; border-bottom: 1px solid #e2e0d8; }}
 button {{ font: inherit; padding: 6px 12px; border-radius: 8px; border: 1px solid #c9c7bf; background: #534AB7; color: #fff; cursor: pointer; }}
 @media print {{ .bar {{ display: none; }} h2 {{ break-after: avoid; }} .mermaid {{ break-inside: avoid; }} }}
</style></head>
<body><div class="bar"><button onclick="window.print()">Print / Save as PDF</button></div><main id="doc">Rendering…</main>
<script src="https://cdn.jsdelivr.net/npm/marked@12.0.2/marked.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/dompurify@3.1.6/dist/purify.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/mermaid@11.4.1/dist/mermaid.min.js"></script>
<script>
 const md = {payload};
 const doc = document.getElementById("doc");
 const renderer = new marked.Renderer();
 const code = renderer.code.bind(renderer);
 renderer.code = (text, lang) => lang === "mermaid" ? '<pre class="mermaid">' + text.replace(/</g, "&lt;") + "</pre>" : code(text, lang);
 doc.innerHTML = DOMPurify.sanitize(marked.parse(md, {{ gfm: true, renderer }}));
 mermaid.initialize({{ startOnLoad: false, securityLevel: "strict", theme: "default" }});
 mermaid.run({{ querySelector: ".mermaid" }}).catch(() => {{}});
</script></body></html>"""
