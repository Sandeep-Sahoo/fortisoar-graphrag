"""Develop page endpoints: chat-style playbook design sessions, generation, downloads and the customer view."""
from __future__ import annotations

import base64
import json

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, Response
from pydantic import BaseModel

from ..services import get_services
from . import catalog, conversation, store

router = APIRouter()


def _user(request: Request) -> str:
    return getattr(request.state, "user", None) or "-"


def _audit(request: Request, action: str, target: str = "", **details) -> None:
    from .. import audit

    audit.record(_user(request), action, target, **details)


def _session(sid: str) -> dict:
    s = store.get(sid)
    if not s:
        raise HTTPException(404, "design session not found")
    return s


def _public(s: dict) -> dict:
    return {k: v for k, v in s.items() if k not in ("prefill",)} | {"versions": [{k: v for k, v in x.items() if k != "spec"} for x in s["versions"]]}


@router.get("/develop/sessions")
def list_sessions():
    return store.list_sessions()


@router.post("/develop/sessions")
def create_session(request: Request):
    s = store.create(_user(request))
    conversation.start(s)
    store.save(s)
    _audit(request, "develop.create", s["id"])
    return _public(s)


@router.get("/develop/sessions/{sid}")
def get_session(sid: str):
    return _public(_session(sid))


@router.delete("/develop/sessions/{sid}")
def delete_session(sid: str, request: Request):
    _session(sid)
    store.delete(sid)
    _audit(request, "develop.delete", sid)
    return {"ok": True}


class MessageIn(BaseModel):
    text: str = ""
    values: list[str] = []
    ai: bool = False
    skip: bool = False


@router.post("/develop/sessions/{sid}/message")
def post_message(sid: str, body: MessageIn, request: Request):
    from ..chat import friendly_error
    from ..users import can_use_ai

    s = _session(sid)
    if body.ai and not can_use_ai(getattr(request.state, "role", None)):
        raise HTTPException(403, "Your role can't use the AI model; switch AI off to continue in guided mode.")
    if len(body.text) > 4000:
        raise HTTPException(400, "message is too long (max 4000 characters)")
    try:
        new = conversation.handle(s, get_services(), body.text, body.values, ai=body.ai, skip=body.skip)
    except Exception as exc:
        if body.ai:
            raise HTTPException(503, friendly_error(exc))
        raise
    store.save(s)
    if body.ai:
        _audit(request, "develop.ai", sid)
    return {"session": _public(s), "new": new}


class EditIn(BaseModel):
    edit: dict


@router.post("/develop/sessions/{sid}/edit")
def edit_design(sid: str, body: EditIn, request: Request):
    s = _session(sid)
    try:
        new = conversation.panel_edit(s, body.edit, get_services().scrubber)
    except (ValueError, KeyError) as exc:
        raise HTTPException(400, str(exc))
    store.save(s)
    return {"session": _public(s), "new": new}


@router.get("/develop/sessions/{sid}/diagram")
def design_diagram(sid: str, kind: str = "flowchart", dir: str = "TB", children: bool = False):
    """The current design as any of the playbook diagram kinds (incl. scenarios), drawn from its generated workflow."""
    from .. import diagrams
    from . import generate

    if kind not in diagrams.KINDS:
        raise HTTPException(400, f"kind must be one of {', '.join(diagrams.KINDS)}")
    s = _session(sid)
    if not s.get("spec"):
        raise HTTPException(404, "this design has no proposed workflow yet")
    doc = generate.export(s["spec"], s["pb_uuid"], s["coll_uuid"])
    flow = generate.flow_of(doc["data"][0]["workflows"][0], get_services().scrubber)
    if children:  # child playbooks the design calls, when they exist in the graph
        try:
            flow = diagrams.expand_children(flow, get_services().child_flow)
        except Exception:  # noqa: BLE001 - no graph available: draw the design as it is
            pass
    return {"sid": sid, "name": flow.get("name") or s["spec"].get("name") or "Design", "kind": kind,
            **diagrams.render(flow, kind, dir)}


@router.get("/develop/sessions/{sid}/spec")
def get_spec(sid: str):
    s = _session(sid)
    return {"spec": s.get("spec"), "stage": s["stage"]}


@router.post("/develop/sessions/{sid}/develop")
def develop(sid: str, request: Request):
    s = _session(sid)
    try:
        msg = conversation.develop(s, get_services(), _user(request))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    store.save(s)
    _audit(request, "develop.build", sid, version=msg["version"], score=msg["review"]["score"])
    return {"session": _public(s), "new": [msg]}


def _version(s: dict, version: int | None) -> dict:
    if not s["generated"]:
        raise HTTPException(404, "nothing developed yet: confirm the design first")
    g = next((x for x in s["generated"] if x["version"] == version), None) if version else s["generated"][-1]
    if not g:
        raise HTTPException(404, "no such version")
    return g


@router.get("/develop/sessions/{sid}/playbook.json")
def download_playbook(sid: str, version: int | None = None):
    s = _session(sid)
    g = _version(s, version)
    data = (store.files_dir(sid, g["version"]) / "playbook.json").read_bytes()
    return Response(data, media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="{g["file"]}-v{g["version"]}.json"'})


@router.get("/develop/sessions/{sid}/document.md")
def download_document(sid: str, version: int | None = None):
    s = _session(sid)
    g = _version(s, version)
    text = (store.files_dir(sid, g["version"]) / "document.md").read_text()
    return PlainTextResponse(text, media_type="text/markdown",
                             headers={"Content-Disposition": f'attachment; filename="{g["file"]}-v{g["version"]}-design.md"'})


@router.get("/develop/sessions/{sid}/view", response_class=HTMLResponse)
def customer_view(sid: str, version: int | None = None):
    """Print-ready customer document (Save as PDF from the browser). The current design if nothing is developed yet."""
    from ..exports import print_html

    s = _session(sid)
    if s["generated"]:
        g = _version(s, version)
        text = (store.files_dir(sid, g["version"]) / "document.md").read_text()
    elif s.get("spec"):
        text = conversation.customer_markdown(s, get_services())
    else:
        raise HTTPException(404, "no design yet")
    return HTMLResponse(print_html(s["spec"]["name"] if s.get("spec") else "Playbook design", text))


@router.post("/develop/sessions/{sid}/to-review")
def send_to_review(sid: str, request: Request):
    """Add the latest build to Review (source "Developed playbooks"): it joins the graph and gets a full review."""
    from ..review import sources

    s = _session(sid)
    g = _version(s, None)
    data = (store.files_dir(sid, g["version"]) / "playbook.json").read_bytes()
    item = [{"name": f"{sid}-{g['file']}.json", "content_base64": base64.b64encode(data).decode()}]
    svc = get_services()
    existing = next((x for x in sources.sources() if x["name"] == "Developed playbooks"), None)
    try:
        result = sources.add_files(existing["id"], item, False, svc) if existing else sources.create_upload("Developed playbooks", item, _user(request), svc)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    _audit(request, "develop.to_review", sid, version=g["version"])
    original = json.loads(data)["data"][0]["workflows"][0]["uuid"]
    return {"source": result["source"], "summary": result["summary"], "review_key": f"u:{result['source']['id']}:{original}"}


# ---------------------------------------------------------------- connector picker for the design panel

@router.get("/develop/connectors")
def connectors(q: str = "", purpose: str = "", indicators: str = ""):
    if purpose:
        inds = [i for i in indicators.split(",") if i]
        return {"suggestions": catalog.suggest(purpose, inds, limit=25)}
    return {"connectors": catalog.search(q)}


@router.get("/develop/connectors/{name}")
def connector_ops(name: str):
    c = catalog.get(name)
    if not c:
        raise HTTPException(404, "unknown connector")
    return {k: v for k, v in c.items() if k != "operations"} | {"operations": [
        {k: o.get(k) for k in ("operation", "title", "purpose", "indicators", "description", "used")} for o in c["operations"]]}
