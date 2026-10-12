"""Review page endpoints: best-practice rules, your playbook sources (upload / Git), reviews and scores."""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from ..services import get_services
from . import rules as rules_mod
from . import sources as sources_mod
from . import store

router = APIRouter()
log = logging.getLogger("graphrag.review")


def _user(request: Request) -> str:
    return getattr(request.state, "user", None) or "-"


def _audit(request: Request, action: str, target: str = "", **details) -> None:
    from .. import audit

    audit.record(_user(request), action, target, **details)


# ---------------------------------------------------------------- rules (best practices)

class RuleIn(BaseModel):
    title: str | None = None
    category: str | None = None
    severity: str | None = None
    description: str | None = None
    suggestion: str | None = None
    rationale: str | None = None
    check: str | None = None
    params: dict = {}
    enabled: bool | None = None
    weight: float | None = None


@router.get("/review/rules")
def list_rules():
    return {"rules": rules_mod.all_rules(), "version": rules_mod.version(), "categories": rules_mod.CATEGORIES,
            "severities": rules_mod.SEVERITIES, "check_types": rules_mod.CHECK_TYPES, "step_kinds": rules_mod.STEP_KINDS,
            "fields": rules_mod.FIELDS}


@router.post("/review/rules")
def add_rule(body: RuleIn, request: Request):
    try:
        rule = rules_mod.add_rule(body.model_dump(exclude_none=True), _user(request))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    _audit(request, "rule.add", rule["id"], title=rule["title"], check=rule["check"])
    return rule


@router.patch("/review/rules/{rule_id}")
def update_rule(rule_id: str, body: RuleIn, request: Request):
    try:
        rule = rules_mod.update_rule(rule_id, body.model_dump(exclude_none=True, exclude={"params"} if not body.params else set()),
                                     _user(request))
    except KeyError:
        raise HTTPException(404, "no such rule")
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    _audit(request, "rule.update", rule_id, **body.model_dump(exclude_none=True, include={"enabled", "weight", "severity"}))
    return rule


@router.delete("/review/rules/{rule_id}")
def delete_rule(rule_id: str, request: Request):
    try:
        if not rules_mod.delete_rule(rule_id):
            raise HTTPException(404, "no such rule")
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    _audit(request, "rule.delete", rule_id)
    return {"ok": True}


class RuleImportIn(BaseModel):
    source: str
    rules: list[str] = []
    overrides: list[str] = []


def _other_workspace(name: str) -> str:
    from .. import workspace

    if not workspace.exists(name) or name == workspace.current():
        raise HTTPException(400, "pick another existing workspace to import from")
    return name


@router.get("/review/rules/import-preview")
def rules_import_preview(source: str):
    return rules_mod.import_preview(_other_workspace(source))


@router.post("/review/rules/import")
def rules_import(body: RuleImportIn, request: Request):
    result = rules_mod.import_rules(_other_workspace(body.source), body.rules, body.overrides, _user(request))
    _audit(request, "rule.import", body.source, **result)
    return result


@router.post("/review/rules/{rule_id}/reset")
def reset_rule(rule_id: str, request: Request):
    rules_mod.reset_builtin(rule_id)
    _audit(request, "rule.reset", rule_id)
    return next(r for r in rules_mod.all_rules() if r["id"] == rule_id)


# ---------------------------------------------------------------- sources (your playbooks)

class UploadFile(BaseModel):
    name: str
    content_base64: str


class UploadIn(BaseModel):
    name: str = ""
    files: list[UploadFile]
    replace: bool = False


class GitIn(BaseModel):
    name: str
    url: str
    branch: str = ""
    path: str = ""


@router.get("/uploads")
def list_sources():
    items = [src | {"errors": store.source_reviews(src["id"]).get("errors", [])} for src in sources_mod.sources()]
    return {"sources": items, "git_token_set": bool(get_services().settings.git_token)}


@router.post("/uploads")
def upload(body: UploadIn, request: Request):
    try:
        result = sources_mod.create_upload(body.name, [f.model_dump() for f in body.files], _user(request), get_services())
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    _audit(request, "source.upload", result["source"]["id"], name=body.name, files=len(body.files),
           playbooks=result["summary"]["playbooks"])
    return result


@router.post("/uploads/git")
def connect_git(body: GitIn, request: Request):
    try:
        result = sources_mod.create_git(body.name, body.url, body.branch, body.path, _user(request), get_services())
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except RuntimeError as exc:
        raise HTTPException(502, f"git: {exc}")
    _audit(request, "source.git", result["source"]["id"], name=body.name, url=body.url, playbooks=result["summary"]["playbooks"])
    return result


@router.post("/uploads/docs")
def upload_docs(body: UploadIn, request: Request):
    try:
        result = sources_mod.upload_docs([f.model_dump() for f in body.files], _user(request), get_services())
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    _audit(request, "docs.upload", f"{len(body.files)} file(s)", documents=result["documents"])
    return result


@router.post("/uploads/{source_id}/files")
def add_files(source_id: str, body: UploadIn, request: Request):
    try:
        result = sources_mod.add_files(source_id, [f.model_dump() for f in body.files], body.replace, get_services())
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    _audit(request, "source.files", source_id, files=len(body.files), replace=body.replace)
    return result


@router.post("/uploads/{source_id}/sync")
def sync_source(source_id: str, request: Request):
    try:
        result = sources_mod.sync(source_id, get_services())
    except KeyError:
        raise HTTPException(404, "no such source")
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except RuntimeError as exc:
        raise HTTPException(502, f"git: {exc}")
    _audit(request, "source.sync", source_id, playbooks=result["summary"]["playbooks"])
    return result


@router.delete("/uploads/{source_id}")
def delete_source(source_id: str, request: Request):
    if not sources_mod.delete(source_id, get_services()):
        raise HTTPException(404, "no such source")
    _audit(request, "source.delete", source_id)
    return {"ok": True}


@router.post("/review/rescore")
def rescore(request: Request):
    """Re-review every uploaded source with the current rules (no Git pull, no model calls)."""
    svc = get_services()
    done = {}
    for src in sources_mod.sources():
        try:
            done[src["name"]] = sources_mod.process(src["id"], svc, version=src.get("commit") or src.get("last_synced"))["avg_score"]
        except Exception as exc:
            done[src["name"]] = f"error: {exc}"
    _audit(request, "review.rescore", f"{len(done)} source(s)")
    return {"sources": done}


# ---------------------------------------------------------------- reviews

def _rows(source: str) -> list[dict]:
    meta = store.all_meta()
    rows = []
    if source in ("all", "upload"):
        for src in sources_mod.sources():
            for key, r in store.source_reviews(src["id"]).get("reviews", {}).items():
                rows.append(store.light(r) | {"key": key, "origin": "upload", "source": src["name"], "source_id": src["id"],
                                              "graph_uuid": r.get("graph_uuid"), "copy_of": r.get("copy_of"),
                                              "changes": r.get("changes"), "status": (meta.get(key) or {}).get("status") or "open"})
    from .. import workspace

    if source in ("all", "github") and workspace.has("github"):
        for key, r in store.github_scores().get("playbooks", {}).items():
            rows.append(r | {"key": key, "origin": "github", "source": r.get("repo"), "graph_uuid": r["uuid"],
                             "status": (meta.get(key) or {}).get("status") or "open"})
    return rows


@router.get("/review/playbooks")
def review_list(source: str = "all"):
    if source not in ("all", "upload", "github"):
        raise HTTPException(400, "source must be all, upload or github")
    gh = store.github_scores()
    return {"rows": _rows(source), "rules_version": rules_mod.version(),
            "github": {"generated": gh.get("generated"), "rules_version": gh.get("rules_version"),
                       "count": len(gh.get("playbooks", {}))}}


@router.get("/review/summary")
def review_summary():
    rows = _rows("all")
    out = {}
    for origin in ("upload", "github"):
        rs = [r for r in rows if r["origin"] == origin]
        grades = {}
        for r in rs:
            grades[r["grade"]] = grades.get(r["grade"], 0) + 1
        out[origin] = {"playbooks": len(rs), "avg_score": round(sum(r["score"] for r in rs) / len(rs), 1) if rs else None,
                       "grades": grades, "request_changes": sum(1 for r in rs if r["verdict"] == "Request changes")}
    return out | {"sources": len(sources_mod.sources()), "rules": len(rules_mod.all_rules(include_disabled=False))}


def full_review(key: str) -> dict:
    """Fresh review of one playbook with the current rules and the reviewer's answers."""
    from . import github_index
    from .engine import Context, diff_signatures, review_workflow

    svc = get_services()
    known = {r["uuid"] for r in svc.graph.read("MATCH (p:Playbook) WHERE NOT coalesce(p.placeholder, false) "
                                               "AND coalesce(p.pack_type, '') <> 'upload' RETURN p.uuid AS uuid")}
    if key.startswith("u:"):
        try:
            _, sid, original = key.split(":", 2)
        except ValueError:
            raise HTTPException(404, "bad review key")
        src = sources_mod.get(sid)
        if not src:
            raise HTTPException(404, "source not found")
        found = [(f, c, wf) for f, c, wf in sources_mod._workflows(sid) if "__error__" not in wf]
        known |= {wf.get("uuid") for _, _, wf in found}
        match = next(((f, c, wf) for f, c, wf in found if wf.get("uuid") == original), None)
        if not match:
            raise HTTPException(404, "playbook no longer in this source")
        f, coll, wf = match
        r = review_workflow(wf, coll, svc.scrubber, Context(known, store.answers(key)))
        stored = store.source_reviews(sid).get("reviews", {}).get(key, {})
        r.update(key=key, origin="upload", file=f, source=src["name"], source_id=sid,
                 graph_uuid=sources_mod.graph_uuid(sid, original), changes=stored.get("changes"))
        from .. import workspace

        gh = github_index.raw(original) if workspace.has("github") else None
        if gh:  # it's a copy of a Fortinet playbook: show what you changed
            ref = review_workflow(gh[0], gh[1]["collection"], svc.scrubber, Context(known))
            r["fortinet"] = {"repo": gh[1]["repo"], "name": ref["name"], "score": ref["score"], "grade": ref["grade"],
                             "diff": diff_signatures(ref["signature"], r["signature"])}
    elif key.startswith("g:"):
        from .. import workspace

        if not workspace.has("github"):
            raise HTTPException(404, "Fortinet playbook reviews live in the Fortinet workspace")
        found = github_index.raw(key[2:])
        if not found:
            raise HTTPException(404, "playbook not found in the downloaded GitHub repos")
        wf, entry = found
        r = review_workflow(wf, entry["collection"], svc.scrubber, Context(known | set(github_index.index()), store.answers(key)))
        r.update(key=key, origin="github", source=entry["repo"], graph_uuid=wf["uuid"],
                 url=f"https://github.com/{svc.settings.fetch_org}/{entry['repo']}")
    else:
        raise HTTPException(404, "bad review key")
    r["meta"] = store.meta(key)
    r["history"] = store.history(key)
    r["ai_note"] = store.ai_note(key)
    r.pop("signature", None)
    return r


@router.get("/review/playbooks/{key:path}/export.md")
def review_export(key: str):
    from ..exports import review_markdown

    r = full_review(key)
    name = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in r["name"])[:60] or "playbook"
    return PlainTextResponse(review_markdown(r), media_type="text/markdown",
                             headers={"Content-Disposition": f'attachment; filename="review-{name}.md"'})


@router.get("/review/playbooks/{key:path}")
def review_detail(key: str):
    return full_review(key)


class AnswerIn(BaseModel):
    rule: str
    answer: str | None = None
    note: str = ""


@router.post("/review/answers/{key:path}")
def review_answer(key: str, body: AnswerIn, request: Request):
    try:
        store.set_answer(key, body.rule, body.answer, body.note, _user(request))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    _audit(request, "review.answer", key, rule=body.rule, answer=body.answer)
    r = full_review(key)
    _refresh_light(key, r)
    return r


class StatusIn(BaseModel):
    status: str
    notes: str = ""


@router.post("/review/status/{key:path}")
def review_status(key: str, body: StatusIn, request: Request):
    try:
        meta = store.set_status(key, body.status, body.notes, _user(request))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    _audit(request, "review.status", key, status=body.status)
    return meta


def _refresh_light(key: str, r: dict) -> None:
    """Keep list rows in step with answer changes."""
    if key.startswith("u:"):
        sid = key.split(":", 2)[1]
        data = store.source_reviews(sid)
        if key in data.get("reviews", {}):
            data["reviews"][key].update({k: r[k] for k in ("score", "grade", "verdict", "provisional", "manual_pending",
                                                           "categories", "counts", "findings", "checklist", "summary")})
            store.save_source_reviews(sid, data)
    else:
        gh = store.github_scores()
        if key in gh.get("playbooks", {}):
            gh["playbooks"][key].update(store.light(r) | {"repo": gh["playbooks"][key].get("repo")})
            store.save_github_scores(gh)


AI_REVIEW_SYSTEM = """You are a senior FortiSOAR playbook engineer reviewing a colleague's playbook before production.
You get the playbook's structure and the automated best-practice findings. Write a concise review in Markdown:
1. **Overall assessment** (2-3 sentences, plain language).
2. **Top risks** — the findings that matter most for this playbook's purpose, and why.
3. **Recommended changes** — numbered, concrete (name the steps), most important first. Include design improvements the automated checks can't see (flow, error paths, idempotency, notifications, data handling).
4. **Questions for the author** — things the structure alone can't answer.
Only refer to steps, connectors and findings that are in the input. Never invent values."""


@router.post("/review/ai/{key:path}")
def review_ai(key: str, request: Request):
    from ..users import can_use_ai

    if not can_use_ai(getattr(request.state, "role", None)):
        raise HTTPException(403, "Viewers can't use the AI model.")
    r = full_review(key)
    svc = get_services()
    steps = "\n".join(f"- {f['step'] or '(playbook)'}: [{f['severity']}] {f['title']} — {f['detail']}" for f in r["findings"][:40])
    prompt = (f"PLAYBOOK: {r['name']} (collection {r['collection']})\nDescription: {r.get('description') or '-'}\n"
              f"Trigger: {r['trigger_type']} {', '.join(r['modules'])}\nSteps: {r['steps']}; connectors: {', '.join(r['connectors']) or '-'}\n"
              f"Automated score: {r['score']}/100 ({r['grade']}, {r['verdict']})\n\nFINDINGS:\n{steps or '- none'}\n\n"
              f"MANUAL CHECKLIST:\n" + "\n".join(f"- {c['title']}: {c['answer'] or 'not answered'}" for c in r["checklist"]))
    from ..chat import friendly_error

    try:
        text = svc.llm.complete(AI_REVIEW_SYSTEM, prompt)
    except Exception as exc:
        raise HTTPException(503, friendly_error(exc))
    note = store.set_ai_note(key, text, f"{svc.settings.llm_provider}:{svc.settings.llm_model}", _user(request), r["score"])
    _audit(request, "review.ai", key)
    return note
