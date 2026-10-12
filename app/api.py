"""REST API: `uvicorn app.api:app` (or `python -m app.cli serve`)."""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from typing import Literal

from pydantic import BaseModel

from .services import get_services

import logging
import time
import uuid

from .logging_setup import request_id_var, setup_logging

setup_logging()
log = logging.getLogger("graphrag.api")
from contextlib import asynccontextmanager  # noqa: E402


@asynccontextmanager
async def lifespan(_app):
    from .scheduler import start_background

    start_background()  # scheduled GitHub / Git-source sync (off unless SYNC_SCHEDULE is set)
    yield


app = FastAPI(title="FortiSOAR Graph RAG", lifespan=lifespan)
STATIC = Path(__file__).parent / "static"
PUBLIC_PATHS = {"/login", "/auth/login", "/favicon.ico"}
NO_CACHE = {"Cache-Control": "no-cache"}  # always revalidate the app pages so UI updates load immediately
_sessions = None


def sessions():
    global _sessions
    if _sessions is None:
        from .auth import SessionStore

        _sessions = SessionStore(get_services().settings.session_hours)
    return _sessions


@app.middleware("http")
async def require_login(request: Request, call_next):
    from fastapi.responses import RedirectResponse

    from .auth import COOKIE

    from . import workspace
    from .users import account, allowed, role_in, workspaces_for

    if request.url.path in PUBLIC_PATHS:
        return await call_next(request)
    user = sessions().user(request.cookies.get(COOKIE))
    acc = account(user)
    if user and acc:
        # the company / workspace this browser selected; fall back to one the user may use
        allowed_ws = workspaces_for(acc)
        if not allowed_ws:
            return JSONResponse(status_code=403, content={"detail": "Your account has no company assigned. Ask an admin."})
        ws = request.headers.get("x-workspace") or request.cookies.get(workspace.COOKIE)
        if ws not in allowed_ws:
            ws = workspace.DEFAULT if workspace.DEFAULT in allowed_ws else allowed_ws[0]
        role = role_in(acc, ws)
        if not allowed(role, request.method, request.url.path, acc.get("superadmin")):
            log.warning("user %r (%s in %s) denied %s %s", user, role, ws, request.method, request.url.path)
            return JSONResponse(status_code=403, content={"detail": f"Your role in this company ({role}) can't do this. Ask an admin."})
        request.state.user, request.state.role, request.state.account = user, role, acc
        token = workspace.set_current(ws)
        request.state.workspace = workspace.current()
        try:
            if request.url.path.startswith("/github/") and not workspace.has("github"):
                return JSONResponse(status_code=404, content={
                    "detail": f"The GitHub page belongs to the Fortinet workspace; you're in {workspace.info()['label']}."})
            return await call_next(request)
        finally:
            workspace.reset(token)
    if request.method == "GET" and "text/html" in request.headers.get("accept", ""):
        return RedirectResponse("/login", status_code=303)
    return JSONResponse(status_code=401, content={"detail": "not logged in"})


def current_user(request: Request) -> str | None:
    return getattr(request.state, "user", None)


def current_role(request: Request) -> str | None:
    return getattr(request.state, "role", None)


def require_ai(request: Request) -> None:
    from .users import can_use_ai

    if not can_use_ai(current_role(request)):
        raise HTTPException(403, "Viewers can't use the AI model. Use Graph only mode, or ask an admin for the analyst role.")


@app.middleware("http")
async def request_log(request: Request, call_next):
    """Outermost middleware: request id, timing, access log, and a last-resort error boundary."""
    rid = (request.headers.get("x-request-id") or uuid.uuid4().hex[:12])[:40]
    token = request_id_var.set(rid)
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception as exc:  # raised in middleware, where exception handlers don't apply
        response = map_exception(request, exc)
    ms = (time.perf_counter() - started) * 1000
    status = response.status_code
    level = logging.ERROR if status >= 500 else logging.WARNING if status >= 400 and status != 401 else \
        logging.INFO if request.method != "GET" else logging.DEBUG
    log.log(level, "%s %s -> %s (%.0f ms) client=%s", request.method, request.url.path, status, ms,
            request.client.host if request.client else "-")
    response.headers["X-Request-ID"] = rid
    request_id_var.reset(token)
    return response


class LoginIn(BaseModel):
    username: str
    password: str


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    from fastapi.responses import Response

    return Response(status_code=204)  # the pages declare an inline icon; this just avoids 404 noise in the log


@app.get("/login", include_in_schema=False)
def login_page():
    return FileResponse(STATIC / "login.html", headers=NO_CACHE)


@app.post("/auth/login")
async def login(body: LoginIn, request: Request):
    import asyncio

    from . import audit
    from .auth import COOKIE
    from .users import authenticate

    s = get_services().settings
    client = request.client.host if request.client else "-"
    role = await asyncio.to_thread(authenticate, body.username, body.password)
    if not role:
        log.warning("failed sign-in for user %r from %s", body.username[:60], client)
        audit.record(body.username[:60], "auth.failed", client)
        await asyncio.sleep(0.6)  # slow down guessing
        raise HTTPException(401, "Incorrect username or password")
    log.info("user %r (%s) signed in from %s", body.username[:60], role, client)
    audit.record(body.username, "auth.login", client, role=role)
    response = JSONResponse({"ok": True, "user": body.username, "role": role,
                             "default_password": (s.app_username, s.app_password) == ("admin", "admin")})
    response.set_cookie(COOKIE, sessions().create(body.username), httponly=True, samesite="lax",
                        secure=request.url.scheme == "https", max_age=s.session_hours * 3600)
    return response


@app.post("/auth/logout")
def logout(request: Request):
    from .auth import COOKIE

    from . import audit

    user = sessions().user(request.cookies.get(COOKIE))
    log.info("user %r signed out", user)
    audit.record(user, "auth.logout")
    sessions().drop(request.cookies.get(COOKIE))
    response = JSONResponse({"ok": True})
    response.delete_cookie(COOKIE)
    return response


@app.get("/auth/me")
def me(request: Request):
    from .auth import COOKIE

    s = get_services().settings
    from .users import can_use_ai

    from . import workspace

    role = current_role(request)
    from .users import workspaces_for

    acc = getattr(request.state, "account", None) or {}
    return {"user": sessions().user(request.cookies.get(COOKIE)), "role": role, "can_use_ai": can_use_ai(role),
            "workspace": workspace.info(), "superadmin": bool(acc.get("superadmin")), "access": acc.get("access") or {},
            "workspaces": workspaces_for(acc),
            "default_password": (s.app_username, s.app_password) == ("admin", "admin")}


def _error(status: int, detail: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"detail": detail, "request_id": request_id_var.get()})


@app.exception_handler(ValueError)
@app.exception_handler(RuntimeError)
async def config_error(request: Request, exc: Exception):
    # Config / schema problems: return the message to the UI instead of a 500 traceback.
    log.warning("%s on %s %s: %s", type(exc).__name__, request.method, request.url.path, exc)
    return _error(503, str(exc))


@app.exception_handler(Exception)
async def unexpected_error(request: Request, exc: Exception):
    return map_exception(request, exc)


def map_exception(request: Request, exc: Exception) -> JSONResponse:
    """One mapping for handler- and middleware-level failures: friendly 503s for known outages,
    otherwise a logged traceback and a generic 500 that carries the request id."""
    from neo4j.exceptions import AuthError, ServiceUnavailable, SessionExpired

    if isinstance(exc, (ServiceUnavailable, SessionExpired)):
        from . import workspace

        w = workspace.info(getattr(request.state, "workspace", None) or workspace.current())
        log.error("Neo4j (%s workspace) unavailable on %s %s: %s", w["id"], request.method, request.url.path, exc)
        return _error(503, f"The {w.get('label', w['id'])} workspace's graph database isn't reachable. "
                           f"Start it with: docker compose up -d {w.get('service') or 'neo4j'}")
    if isinstance(exc, AuthError):
        log.error("Neo4j authentication failed: %s", exc)
        return _error(503, "The graph database rejected the credentials. Check NEO4J_USER / NEO4J_PASSWORD in Settings.")
    log.exception("Unhandled error on %s %s", request.method, request.url.path)
    return _error(500, "Something went wrong on the server. The error has been logged.")


UI = STATIC / "index.html"


class Question(BaseModel):
    question: str
    with_graph: bool = False


class CypherQuery(BaseModel):
    query: str


class ChatCreate(BaseModel):
    title: str = "New chat"


class ChatMessageIn(BaseModel):
    question: str
    mode: Literal["ai", "graph"] = "ai"   # graph = keyword search + graph only, no model API calls
    filters: dict[str, str] = {}          # graph mode: pack / connector / trigger / module / origin


@app.get("/", include_in_schema=False)
def ui():
    return FileResponse(UI, headers=NO_CACHE)


@app.get("/static/{folder}/{name}", include_in_schema=False)
def static_asset(folder: str, name: str):
    import re as _re

    if folder not in ("js", "css") or not _re.fullmatch(r"[a-z0-9_-]+\.(js|css)", name) or not name.endswith(folder):
        raise HTTPException(404, "not found")
    path = STATIC / folder / name
    if not path.is_file():
        raise HTTPException(404, "not found")
    return FileResponse(path, headers=NO_CACHE, media_type="text/javascript" if folder == "js" else "text/css")


@app.get("/about")
def about():
    from .about import about as build

    return build(get_services())


@app.get("/health")
def health():
    svc = get_services()
    config = svc.settings.describe()
    try:
        counts = svc.graph.stats()
    except Exception as exc:
        from . import workspace

        w = workspace.info()
        raise HTTPException(503, f"The {w.get('label', w['id'])} workspace's graph database isn't reachable ({svc.settings.neo4j_uri}). "
                                 f"Start it with: docker compose up -d {w.get('service') or 'neo4j'}. Pages that don't need the graph "
                                 f"(Review rules, Develop, Settings) still work. Detail: {str(exc).splitlines()[0][:160]}")
    return {"config": config, "graph": counts}


@app.post("/ask")
def ask(body: Question, request: Request):
    require_ai(request)
    return get_services().ask(body.question, with_graph=body.with_graph)


# ------------------------------------------------------------ chats

@app.get("/chats")
def list_chats():
    return get_services().chats.list()


@app.post("/chats")
def create_chat(body: ChatCreate | None = None):
    svc = get_services()
    svc.graph.ensure_schema()
    return svc.chats.create((body or ChatCreate()).title)


@app.get("/chats/{session_id}")
def get_chat(session_id: str):
    session = get_services().chats.get(session_id)
    if not session:
        raise HTTPException(404, "chat session not found")
    return session


@app.patch("/chats/{session_id}")
def rename_chat(session_id: str, body: ChatCreate):
    if not get_services().chats.rename(session_id, body.title):
        raise HTTPException(404, "chat session not found")
    return {"ok": True}


@app.delete("/chats/{session_id}")
def delete_chat(session_id: str):
    if not get_services().chats.delete(session_id):
        raise HTTPException(404, "chat session not found")
    return {"ok": True}


@app.post("/chats/{session_id}/messages")
def chat_message(session_id: str, body: ChatMessageIn, request: Request):
    """Server-sent events: stage / token / done / error, one JSON object per `data:` line."""
    import json

    from . import audit
    from .chat import chat_events

    if body.mode == "ai":
        require_ai(request)
    svc = get_services()
    audit.record(current_user(request), f"chat.{body.mode}", session_id, chars=len(body.question))

    def events():
        for event in chat_events(svc, session_id, body.question, body.mode, filters=body.filters):
            yield f"data: {json.dumps(event, default=str)}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/playbooks/{name_or_uuid}/diagram")
def playbook_diagram(name_or_uuid: str, kind: str = "flowchart"):
    if kind not in ("flowchart", "sequence"):
        raise HTTPException(400, "kind must be flowchart or sequence")
    result = get_services().playbook_diagram(name_or_uuid, kind)
    if "error" in result:
        raise HTTPException(404, result["error"])
    return result


# ------------------------------------------------------------ collections

@app.get("/collections")
def list_collections():
    return get_services().list_collections()


@app.get("/collections/{name}")
def get_collection(name: str):
    result = get_services().get_collection(name)
    if "error" in result:
        raise HTTPException(404, result["error"])
    return result


# ------------------------------------------------------------ settings / admin

class SettingsUpdate(BaseModel):
    values: dict[str, str | bool | int | float | None]
    scope: str = "general"


class JobStart(BaseModel):
    operation: str
    options: dict = {}
    confirm: str = ""


class LinkReview(BaseModel):
    action: str                 # approve | reject
    doc: str | None = None
    playbook: str | None = None
    all: bool = False
    min_confidence: float = 0.0


def _settings_scopes(request: Request) -> list[dict]:
    """Settings tabs this user may open: General (super-admin) and each company they administer."""
    from . import workspace

    acc = getattr(request.state, "account", None) or {}
    out = [{"id": "general", "label": "General", "description": "Applies to every company: AI models, keys, limits, sign-in, retrieval"}] \
        if acc.get("superadmin") else []
    for ws in _admin_workspaces(request):
        w = workspace.info(ws)
        out.append({"id": ws, "label": w.get("label", ws), "color": w.get("color"),
                    "description": f"{w.get('label', ws)} only: database, company name, redaction, Git token" + (", GitHub sync" if "github" in (w.get("features") or []) else "")})
    return out


def _check_scope(request: Request, scope: str) -> None:
    if scope not in {s["id"] for s in _settings_scopes(request)}:
        raise HTTPException(403, "You can't manage these settings. General settings need a super-admin; company settings need that company's admin.")


@app.get("/admin/settings/scopes")
def admin_settings_scopes(request: Request):
    return {"scopes": _settings_scopes(request)}


@app.get("/admin/settings")
def admin_settings(request: Request, scope: str = "general"):
    from . import workspace
    from .settings_store import describe_scope

    _check_scope(request, scope)
    svc = get_services(scope if workspace.exists(scope) else None)
    return {"scope": scope, "fields": describe_scope(svc.settings, scope), "config": svc.settings.describe(), "env_file": ".env"}


@app.post("/admin/settings")
def admin_update_settings(body: SettingsUpdate, request: Request):
    from .settings_store import apply

    _check_scope(request, body.scope)
    from .settings_store import apply_scope

    try:
        values = {k: ("true" if v is True else "false" if v is False else v) for k, v in body.values.items()}
        result = apply_scope(body.scope, values)
    except ValueError as exc:
        log.warning("settings change rejected (%s): %s", ", ".join(sorted(body.values)), str(exc).replace("\n", " "))
        raise HTTPException(400, str(exc))
    log.info("settings changed: %s", ", ".join(result["changed"]) or "nothing")  # keys only, never values
    from . import audit

    audit.record(current_user(request), "settings.change", ", ".join(result["changed"]), scope=body.scope)
    if "LOG_LEVEL" in result["changed"]:
        setup_logging(get_services().settings.log_level)
    return {**result, "config": get_services().settings.describe()}


@app.get("/github/repos")
def github_repos():
    from .github_catalog import repo_table

    return repo_table(get_services())


@app.get("/github/repos/{repo}")
def github_repo(repo: str):
    from .github_catalog import repo_detail

    detail = repo_detail(get_services(), repo)
    if detail is None:
        raise HTTPException(404, f"repo {repo!r} has not been downloaded")
    return detail


@app.get("/admin/operations")
def admin_operations():
    from .jobs import JOBS, OPERATIONS

    from . import workspace
    from .jobs import GITHUB_ONLY

    return {"operations": [{"name": k, "title": v[0], "description": v[2], "destructive": v[3]} for k, v in OPERATIONS.items()
                            if k not in ("ingest_repos", "continue_repos", "embed_today")  # run from their pages
                            and (workspace.has("github") or k not in GITHUB_ONLY)],
            "jobs": JOBS.list(), "running": JOBS.public(JOBS.running(), tail=60) if JOBS.running() else None}


@app.post("/admin/jobs")
def admin_start_job(body: JobStart, request: Request):
    from .jobs import JOBS, OPERATIONS

    from . import workspace
    from .jobs import GITHUB_ONLY

    op = OPERATIONS.get(body.operation)
    if not op:
        raise HTTPException(404, "unknown operation")
    if body.operation in GITHUB_ONLY and not workspace.has("github"):
        raise HTTPException(400, f"“{op[0]}” works on Fortinet's GitHub content: switch to the Fortinet workspace.")
    if body.operation == "continue_repos" and not str(body.options.get("max_embeddings", "")).isdigit():
        raise HTTPException(400, "continue needs a max_embeddings cap (a whole number)")
    if body.operation in ("ingest_repos", "continue_repos"):
        import re as _re
        from .github_catalog import disk_index

        known = {r["repo"] for r in disk_index(get_services().scrubber)}
        repos = body.options.get("repos") or []
        if not repos or any(not isinstance(r, str) or not _re.fullmatch(r"[A-Za-z0-9._-]+", r) or r not in known for r in repos):
            raise HTTPException(400, "select one or more downloaded repos")
    for key in ("max_embeddings", "limit"):
        value = body.options.get(key)
        if value not in (None, "") and (not str(value).isdigit() or int(value) > 100000):
            raise HTTPException(400, f"{key} must be a whole number")
    destructive = op[3] or bool(body.options.get("wipe"))
    if destructive and body.confirm != "WIPE":
        raise HTTPException(400, "type WIPE to confirm this operation")
    try:
        job = JOBS.start(body.operation, body.options)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(409, str(exc))
    from . import audit

    audit.record(current_user(request), f"job.{body.operation}", job.get("id", ""),
                 **{k: v for k, v in body.options.items() if k in ("repos", "max_embeddings", "limit", "mode", "skip_docs")})
    return job


@app.get("/admin/jobs/{job_id}")
def admin_job(job_id: str):
    from .jobs import JOBS

    if job_id not in JOBS.jobs:
        raise HTTPException(404, "no such job")
    return JOBS.public(JOBS.jobs[job_id], tail=200)


@app.post("/admin/jobs/{job_id}/cancel")
def admin_cancel_job(job_id: str):
    from .jobs import JOBS

    if job_id not in JOBS.jobs:
        raise HTTPException(404, "no such job")
    return JOBS.cancel(job_id)


@app.post("/admin/test/{target}")
def admin_test(target: str, request: Request):
    import time as _t

    svc = get_services()
    t0 = _t.time()
    from . import audit

    audit.record(current_user(request), f"test.{target}")
    try:
        if target == "neo4j":
            svc.graph.driver.verify_connectivity()
            detail = f"connected to {svc.settings.neo4j_uri}"
        elif target == "llm":
            reply = svc.llm.complete("Reply with the single word OK.", "Health check.")
            detail = f"{svc.settings.llm_provider}:{svc.settings.llm_model} replied “{reply.strip()[:40]}”"
        elif target == "embeddings":
            vec = svc.embedder.embed(["FortiSOAR settings connectivity check"], query=True)[0]
            detail = f"{svc.settings.embed_provider}:{svc.settings.embed_model} returned {len(vec)} dimensions"
        else:
            raise HTTPException(404, "target must be neo4j, llm or embeddings")
    except HTTPException:
        raise
    except Exception as exc:
        from .chat import friendly_error

        return {"ok": False, "detail": friendly_error(exc), "ms": round((_t.time() - t0) * 1000)}
    return {"ok": True, "detail": detail, "ms": round((_t.time() - t0) * 1000)}


# ------------------------------------------------------------ workspaces

@app.get("/workspaces")
def list_workspaces(request: Request):
    from . import workspace
    from .users import workspaces_for

    mine = set(workspaces_for(getattr(request.state, "account", None)))
    return {"current": workspace.current(), "workspaces": [
        {"id": k, "label": v.get("label", k), "description": v.get("description", ""), "color": v.get("color"),
         "features": v.get("features") or []} for k, v in workspace.registry().items() if k in mine]}


def _admin_workspaces(request: Request) -> list[str]:
    """Workspaces this user administers (super-admin: all)."""
    from .users import workspaces_for

    acc = getattr(request.state, "account", None) or {}
    return [ws for ws in workspaces_for(acc) if acc.get("superadmin") or acc["access"].get(ws) == "admin"]


@app.get("/admin/workspaces")
def admin_workspaces(request: Request):
    """Connection status and contents of the workspaces this user administers."""
    from . import workspace

    out = []
    mine = set(_admin_workspaces(request))
    for k, v in workspace.registry().items():
        if k not in mine:
            continue
        svc = get_services(k)
        row = {"id": k, "label": v.get("label", k), "neo4j_uri": svc.settings.neo4j_uri, "data_dir": str(workspace.data_dir(k)),
               "features": v.get("features") or []}
        try:
            row["graph"] = {r["label"]: r["count"] for r in svc.graph.stats()}
            row["ok"] = True
        except Exception as exc:
            row["ok"], row["error"] = False, str(exc).splitlines()[0][:200]
        out.append(row)
    return {"workspaces": out}


# ------------------------------------------------------------ usage, users, audit

@app.get("/usage")
def usage_today(days: int = 0):
    from . import usage

    out = usage.summary()
    if days:
        out["history"] = usage.history(min(days, 30))
    return out


@app.post("/admin/cache/clear-answers")
def admin_clear_answer_cache(request: Request):
    from . import audit

    llm = get_services().llm
    n = llm.clear() if hasattr(llm, "clear") else 0
    audit.record(current_user(request), "cache.clear_answers", n=n)
    return {"removed": n}


class UserIn(BaseModel):
    username: str
    password: str | None = None
    role: str | None = None
    disabled: bool | None = None
    access: dict[str, str | None] | None = None   # {company: viewer|analyst|admin|none}
    superadmin: bool | None = None


@app.get("/admin/users")
def admin_users():
    from . import workspace
    from .users import ROLES, list_users

    s = get_services().settings
    return {"users": list_users(), "roles": ROLES, "builtin_admin": s.app_username,
            "workspaces": [{"id": k, "label": v.get("label", k)} for k, v in workspace.registry().items()]}


@app.post("/admin/users")
def admin_create_user(body: UserIn, request: Request):
    from . import audit, users

    try:
        result = users.create(body.username, body.password or "", body.role, current_user(request),
                              access=body.access, superadmin=body.superadmin)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    audit.record(current_user(request), "user.create", body.username, access=result["access"], superadmin=result["superadmin"])
    return result


@app.patch("/admin/users/{username}")
def admin_update_user(username: str, body: UserIn, request: Request):
    from . import audit, users

    if username == current_user(request) and (body.disabled or (body.role and body.role != "admin") or body.superadmin is False):
        raise HTTPException(400, "you can't demote or disable your own account")
    try:
        result = users.update(username, current_user(request), role=body.role, password=body.password, disabled=body.disabled,
                              access=body.access, superadmin=body.superadmin)
    except KeyError:
        raise HTTPException(404, "no such user")
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    audit.record(current_user(request), "user.update", username, role=body.role, disabled=body.disabled, access=body.access,
                 superadmin=body.superadmin, password_changed=bool(body.password) or None)
    return result


@app.delete("/admin/users/{username}")
def admin_delete_user(username: str, request: Request):
    from . import audit, users

    if username == current_user(request):
        raise HTTPException(400, "you can't delete your own account")
    if not users.delete(username):
        raise HTTPException(404, "no such user")
    audit.record(current_user(request), "user.delete", username)
    return {"ok": True}


@app.get("/admin/audit")
def admin_audit(request: Request, limit: int = 300, user: str = "", action: str = "", q: str = ""):
    """Super-admins see everything; company admins see their companies' entries."""
    from . import audit

    acc = getattr(request.state, "account", None) or {}
    entries = audit.read(max(1, min(limit, 5000)) * (1 if acc.get("superadmin") else 5), user, action, q)
    if not acc.get("superadmin"):
        mine = set(_admin_workspaces(request))
        entries = [e for e in entries if e.get("ws", "fortinet") in mine][:limit]
    return {"entries": entries}


class ClientError(BaseModel):
    message: str
    source: str = ""
    stack: str = ""
    page: str = ""


@app.post("/client-errors")
def client_error(body: ClientError):
    log.warning("browser error on %s: %s %s\n%s", body.page[:200], body.message[:500], body.source[:200], body.stack[:2000])
    return {"logged": True}


@app.get("/admin/logs")
def admin_logs(lines: int = 300, level: str = "DEBUG", q: str = ""):
    from .logging_setup import LOG_FILE, tail

    return {"file": str(LOG_FILE), "lines": tail(max(1, min(lines, 2000)), level, q)}


@app.get("/admin/links")
def admin_links(status: str = "proposed"):
    return get_services().graph.read(
        "MATCH (p:Playbook)-[r:DOCUMENTED_BY]->(d:Document) WHERE r.status = $status "
        "RETURN d.id AS doc, d.title AS title, p.uuid AS playbook, p.name AS name, r.method AS method, "
        "r.confidence AS confidence, r.reason AS reason ORDER BY title, confidence DESC LIMIT 500",
        status=status,
    )


@app.post("/admin/links")
def admin_review_links(body: LinkReview):
    from .ingest.linking import derive_links

    if body.action not in ("approve", "reject"):
        raise HTTPException(400, "action must be approve or reject")
    status = "approved" if body.action == "approve" else "rejected"
    g = get_services().graph
    if body.all:
        n = g.run("MATCH (p:Playbook)-[r:DOCUMENTED_BY {status: 'proposed'}]->(:Document) "
                  "WHERE coalesce(r.confidence, 0) >= $min SET r.status = $status, r.reviewed = true RETURN count(r) AS n",
                  min=body.min_confidence, status=status)[0]["n"]
    else:
        if not (body.doc and body.playbook):
            raise HTTPException(400, "doc and playbook are required")
        n = g.run("MATCH (p:Playbook {uuid: $p})-[r:DOCUMENTED_BY]->(:Document {id: $d}) "
                  "SET r.status = $status, r.reviewed = true RETURN count(r) AS n", p=body.playbook, d=body.doc, status=status)[0]["n"]
    derive_links(g)
    return {"updated": n, "status": status}


@app.post("/admin/cache/clear")
def admin_clear_cache(request: Request):
    from .settings_store import reset_services

    from . import audit

    audit.record(current_user(request), "cache.clear_embeddings")
    path = Path(get_services().settings.embed_cache)
    removed = path.exists()
    if removed:
        reset_services()
        path.unlink()
    return {"removed": removed, "path": str(path)}


# ------------------------------------------------------------ catalog

@app.get("/catalog/{kind}")
def catalog_list(kind: str):
    from .catalog import KINDS, catalog

    if kind not in KINDS:
        raise HTTPException(404, f"kind must be one of {KINDS}")
    return catalog(get_services().graph, kind)


@app.get("/packs/{repo}")
def pack(repo: str):
    from .catalog import pack_detail

    detail = pack_detail(get_services().graph, repo)
    if not detail:
        raise HTTPException(404, f"no solution pack '{repo}'")
    return detail


@app.get("/connectors/{name}")
def connector(name: str):
    from .catalog import connector_detail

    detail = connector_detail(get_services().graph, name)
    if not detail:
        raise HTTPException(404, f"no connector named '{name}'")
    return detail


@app.get("/documents")
def document(id: str):
    from .catalog import document_detail

    detail = document_detail(get_services().graph, id)
    if not detail:
        raise HTTPException(404, f"no document with id '{id}'")
    return detail


# ------------------------------------------------------------ graph

@app.get("/graph/overview")
def graph_overview(limit: int | None = None, mode: str = "auto"):
    if mode not in ("auto", "ecosystem", "playbooks"):
        raise HTTPException(400, "mode must be auto, ecosystem or playbooks")
    svc = get_services()
    return svc.overview_graph(limit or svc.settings.overview_playbook_limit, mode)


@app.get("/graph/module/{module}")
def graph_module(module: str):
    return get_services().module_graph(module)


@app.get("/graph/playbook/{name_or_uuid}")
def graph_playbook(name_or_uuid: str, steps: bool = True):
    svc = get_services()
    uuid = svc.retriever.resolve_playbook(name_or_uuid)
    if not uuid:
        raise HTTPException(404, f"no playbook matching '{name_or_uuid}'")
    return svc.playbook_graph([uuid], steps=steps)


@app.get("/search/playbooks")
def search_playbooks(q: str, k: int = 5):
    return get_services().search_company_playbooks(q, k)


@app.get("/search/docs")
def search_docs(q: str, source: str = "vendor", k: int = 6):
    if source not in ("vendor", "internal"):
        raise HTTPException(400, "source must be vendor or internal")
    svc = get_services()
    return svc.search_vendor_docs(q, k) if source == "vendor" else svc.search_internal_docs(q, k)


# ------------------------------------------------------------ review router (uploads, rules, reviews)

from .review.api import router as review_router  # noqa: E402

app.include_router(review_router)

from .develop.api import router as develop_router  # noqa: E402

app.include_router(develop_router)


# ------------------------------------------------------------ insights (graph analysis, no model calls)

@app.get("/insights/connectors")
def insights_connectors():
    from .insights import connector_usage

    return connector_usage(get_services().graph)


@app.get("/insights/connector/{name}")
def insights_connector(name: str):
    from .insights import connector_impact

    result = connector_impact(get_services().graph, name)
    if "error" in result:
        raise HTTPException(404, result["error"])
    return result


@app.get("/insights/duplicates")
def insights_duplicates(threshold: float = 0.85):
    from .insights import duplicates

    if not 0.5 <= threshold <= 1.0:
        raise HTTPException(400, "threshold must be between 0.5 and 1.0")
    return duplicates(get_services().graph, threshold)


@app.get("/insights/gaps")
def insights_gaps():
    from .insights import gaps

    return gaps(get_services().graph)


@app.get("/insights/chains")
def insights_chains(root: str | None = None, depth: int = 6):
    from .insights import call_chains

    svc = get_services()
    uuid = svc.retriever.resolve_playbook(root) if root else None
    if root and not uuid:
        raise HTTPException(404, f"no playbook matching '{root}'")
    return call_chains(svc.graph, uuid, depth)


@app.get("/insights/security")
def insights_security():
    from .insights import security

    return security(get_services().graph)


@app.get("/insights/changes")
def insights_changes():
    from .insights import changes

    return changes()


# ------------------------------------------------------------ exports (Markdown / print-ready HTML)

def _export(kind: str, ident: str, fmt: str, request: Request):
    from fastapi.responses import HTMLResponse, PlainTextResponse

    from . import audit
    from .exports import pack_markdown, playbook_markdown, print_html

    svc = get_services()
    try:
        if kind == "playbook":
            uuid = svc.retriever.resolve_playbook(ident)
            if not uuid:
                raise KeyError(ident)
            md, title = playbook_markdown(svc, uuid, current_user(request) or "-")
        else:
            md, title = pack_markdown(svc, ident, current_user(request) or "-")
    except KeyError:
        raise HTTPException(404, f"no {kind} matching '{ident}'")
    audit.record(current_user(request), f"export.{kind}", ident, format=fmt)
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in title)[:60] or kind
    if fmt == "html":
        return HTMLResponse(print_html(title, md))
    return PlainTextResponse(md, media_type="text/markdown", headers={"Content-Disposition": f'attachment; filename="{safe}.md"'})


@app.get("/playbooks/{name_or_uuid}/export.md")
def export_playbook_md(name_or_uuid: str, request: Request):
    return _export("playbook", name_or_uuid, "md", request)


@app.get("/playbooks/{name_or_uuid}/export.html")
def export_playbook_html(name_or_uuid: str, request: Request):
    return _export("playbook", name_or_uuid, "html", request)


@app.get("/packs/{repo}/export.md")
def export_pack_md(repo: str, request: Request):
    return _export("pack", repo, "md", request)


@app.get("/packs/{repo}/export.html")
def export_pack_html(repo: str, request: Request):
    return _export("pack", repo, "html", request)


# ------------------------------------------------------------ explain a playbook (AI, cached)

EXPLAIN_SYSTEM = """You explain FortiSOAR playbooks to SOC analysts. Using only the PLAYBOOK data, write Markdown with:
**Purpose** (2 sentences) · **When it runs** · **What it does** (the flow as 3-7 phases, naming key steps) ·
**Decisions and approvals** · **External systems touched** · **Things to watch** (risks, failure points, values to customise).
Be concrete and brief. Never invent steps, connectors or values."""


@app.get("/playbooks/{name_or_uuid}/explain")
def get_explanation(name_or_uuid: str):
    svc = get_services()
    uuid = svc.retriever.resolve_playbook(name_or_uuid)
    if not uuid:
        raise HTTPException(404, f"no playbook matching '{name_or_uuid}'")
    row = svc.graph.read("MATCH (p:Playbook {uuid: $u}) RETURN p.explanation AS text, p.explained_at AS at, "
                         "p.explained_by AS by, p.explained_model AS model", u=uuid)[0]
    return row if row["text"] else {"text": None}


@app.post("/playbooks/{name_or_uuid}/explain")
def explain_playbook(name_or_uuid: str, request: Request):
    from datetime import datetime, timezone

    from . import audit
    from .chat import friendly_error

    require_ai(request)
    svc = get_services()
    uuid = svc.retriever.resolve_playbook(name_or_uuid)
    if not uuid:
        raise HTTPException(404, f"no playbook matching '{name_or_uuid}'")
    p = svc.retriever.get_playbook(uuid)
    steps = "\n".join(f"{i}. {s['name']} ({s['kind']}{', ' + s['operation'] if s.get('operation') else ''})"
                      + (f" -> {', '.join(n['to'] + (' [' + n['label'] + ']' if n.get('label') else '') for n in s['next'])}" if s.get("next") else "")
                      for i, s in enumerate(p["steps"], 1))
    prompt = (f"PLAYBOOK: {p['name']}\nCollection: {p.get('collection')}\nDescription: {p.get('description') or '-'}\n"
              f"Trigger: {p.get('trigger_type')} on {', '.join(p.get('modules') or []) or '-'}\nSteps:\n{steps}\n"
              f"Calls: {', '.join(p.get('calls') or []) or '-'}\nCalled by: {', '.join(p.get('called_by') or []) or '-'}")
    try:
        text = svc.llm.complete(EXPLAIN_SYSTEM, prompt)
    except Exception as exc:
        raise HTTPException(503, friendly_error(exc))
    at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    model = f"{svc.settings.llm_provider}:{svc.settings.llm_model}"
    svc.graph.run("MATCH (p:Playbook {uuid: $u}) SET p.explanation = $t, p.explained_at = $at, p.explained_by = $by, "
                  "p.explained_model = $m", u=uuid, t=text, at=at, by=current_user(request), m=model)
    audit.record(current_user(request), "ai.explain", p["name"])
    return {"text": text, "at": at, "by": current_user(request), "model": model}


# ------------------------------------------------------------ embedding planner + sync status

@app.get("/embeddings/plan")
def embeddings_plan():
    """Pending embeddings in the order embed-pending processes them, against today's quota."""
    from . import usage

    g = get_services().graph
    pb = g.read("MATCH (p:Playbook) WHERE p.embedding IS NULL AND NOT coalesce(p.placeholder, false) AND p.summary IS NOT NULL "
                "RETURN coalesce(p.pack_type, 'other') AS kind, count(p) AS n")
    ch = g.read("MATCH (d:Document)-[:HAS_CHUNK]->(c:Chunk) WHERE c.embedding IS NULL "
                "RETURN coalesce(d.kind, d.source) AS kind, CASE WHEN coalesce(c.priority, 0) > 0 THEN 'high' ELSE 'normal' END AS band, "
                "count(c) AS n")
    pb_kind = {r["kind"]: r["n"] for r in pb}
    labels = {"solution_pack": "Solution pack playbooks", "ai": "AI pack playbooks", "connector": "Connector sample playbooks",
              "widget": "Widget playbooks", "upload": "Your uploaded playbooks", "other": "Other playbooks"}
    groups = [{"group": labels.get(k, k), "kind": "playbooks", "pending": pb_kind[k]}
              for k in ["solution_pack", "ai"] + sorted(set(pb_kind) - {"solution_pack", "ai"}) if pb_kind.get(k)]
    for band in ("high", "normal"):
        rows = sorted((r for r in ch if r["band"] == band), key=lambda r: -r["n"])
        for r in rows:
            groups.append({"group": f"{(r['kind'] or 'doc').replace('_', ' ')} doc chunks" + (" (priority)" if band == "high" else ""),
                           "kind": "chunks", "pending": r["n"]})
    s = get_services().settings
    left = usage.remaining("embed")
    daily = s.embed_daily_limit or None
    total = sum(gr["pending"] for gr in groups)
    cumulative = 0
    for gr in groups:
        cumulative += gr["pending"]
        gr["cumulative"] = cumulative
        if daily:
            first_day = left if left is not None else daily
            gr["done_by_day"] = 1 if cumulative <= first_day else 1 + -(-(cumulative - first_day) // daily)
    return {"groups": groups, "total_pending": total, "today": usage.summary()["embeddings"],
            "daily_limit": daily, "days_needed": (1 + -(-(max(0, total - (left or 0))) // daily)) if daily and total else 0,
            "suggested_batch": min(total, left) if left is not None else min(total, 500)}


@app.get("/sync/status")
def sync_status():
    from .scheduler import status

    return status(get_services().settings)


# ------------------------------------------------------------ graph-only search helpers

@app.get("/search/filters")
def search_filters():
    """Values for the graph-only filters (packs, connectors, triggers, modules)."""
    g = get_services().graph
    return {
        "pack": [r["v"] for r in g.read("MATCH (p:Playbook) WHERE p.pack IS NOT NULL AND p.pack <> '' "
                                        "RETURN DISTINCT p.pack AS v ORDER BY v")],
        "connector": [r["v"] for r in g.read("MATCH (c:Connector)<-[:OF]-(:ConnectorOperation)<-[:USES]-(:Step) "
                                             "RETURN DISTINCT c.name AS v ORDER BY v")],
        "trigger": [r["v"] for r in g.read("MATCH (p:Playbook) WHERE p.trigger_type IS NOT NULL "
                                           "RETURN DISTINCT p.trigger_type AS v ORDER BY v")],
        "module": [r["v"] for r in g.read("MATCH (m:Module)<-[:TRIGGERED_ON|ACTS_ON]-(:Playbook) RETURN DISTINCT m.name AS v ORDER BY v")],
        "origin": ["github", "upload"],
    }


@app.get("/search/keyword")
def search_keyword(q: str = "", pack: str = "", trigger: str = "", connector: str = "", module: str = "", origin: str = ""):
    """Graph-only search without a chat (used by saved searches and share links). No model calls."""
    return get_services().retriever.search_keyword(q, {"pack": pack, "trigger": trigger, "connector": connector,
                                                        "module": module, "origin": origin})


@app.get("/search/synonyms")
def get_synonyms():
    from .search_store import DEFAULT_SYNONYMS, synonyms

    current = synonyms()
    return {"synonyms": current, "is_default": current == DEFAULT_SYNONYMS}


class SynonymsIn(BaseModel):
    synonyms: dict[str, list[str] | str] = {}
    reset: bool = False


@app.put("/search/synonyms")
def put_synonyms(body: SynonymsIn, request: Request):
    from . import audit
    from .search_store import reset_synonyms, set_synonyms

    try:
        result = reset_synonyms() if body.reset else set_synonyms(body.synonyms)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    audit.record(current_user(request), "search.synonyms", "reset" if body.reset else f"{len(result)} entries")
    return {"synonyms": result}


class SavedSearchIn(BaseModel):
    name: str
    query: str
    filters: dict[str, str] = {}


@app.get("/search/saved")
def list_saved_searches():
    from .search_store import saved

    return saved()


@app.post("/search/saved")
def create_saved_search(body: SavedSearchIn, request: Request):
    from . import audit
    from .search_store import save_search

    try:
        entry = save_search(body.name, body.query, body.filters, current_user(request) or "-")
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    audit.record(current_user(request), "search.save", body.name)
    return entry


@app.post("/search/saved/{search_id}/run")
def run_saved_search(search_id: str):
    from .search_store import record_run, saved

    entry = next((s for s in saved() if s["id"] == search_id), None)
    if not entry:
        raise HTTPException(404, "no such saved search")
    hits = get_services().retriever.search_keyword(entry["query"], entry.get("filters"))
    updated = record_run(search_id, len(hits["playbooks"]))
    return {"search": updated, "hits": hits}


@app.delete("/search/saved/{search_id}")
def delete_saved_search(search_id: str, request: Request):
    from . import audit
    from .search_store import delete_search

    if not delete_search(search_id):
        raise HTTPException(404, "no such saved search")
    audit.record(current_user(request), "search.delete", search_id)
    return {"ok": True}


@app.get("/playbooks/{name_or_uuid}")
def get_playbook(name_or_uuid: str):
    return get_services().get_playbook(name_or_uuid)


@app.get("/playbooks/{name_or_uuid}/dependents")
def dependents(name_or_uuid: str, depth: int = 3):
    return get_services().find_dependents(name_or_uuid, depth)


@app.post("/cypher")
def cypher(body: CypherQuery):
    result = get_services().run_cypher(body.query)
    if isinstance(result, dict) and "error" in result:
        raise HTTPException(400, result["error"])
    return result
