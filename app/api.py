"""REST API: `uvicorn app.api:app` (or `python -m app.cli serve`)."""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

from .services import get_services

app = FastAPI(title="FortiSOAR Graph RAG")
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

    if request.url.path in PUBLIC_PATHS or sessions().user(request.cookies.get(COOKIE)):
        return await call_next(request)
    if request.method == "GET" and "text/html" in request.headers.get("accept", ""):
        return RedirectResponse("/login", status_code=303)
    return JSONResponse(status_code=401, content={"detail": "not logged in"})


class LoginIn(BaseModel):
    username: str
    password: str


@app.get("/login", include_in_schema=False)
def login_page():
    return FileResponse(STATIC / "login.html", headers=NO_CACHE)


@app.post("/auth/login")
async def login(body: LoginIn, request: Request):
    import asyncio

    from .auth import COOKIE, check_credentials

    s = get_services().settings
    if not check_credentials(body.username, body.password, s.app_username, s.app_password):
        await asyncio.sleep(0.6)  # slow down guessing
        raise HTTPException(401, "Incorrect username or password")
    response = JSONResponse({"ok": True, "user": body.username,
                             "default_password": (s.app_username, s.app_password) == ("admin", "admin")})
    response.set_cookie(COOKIE, sessions().create(body.username), httponly=True, samesite="lax",
                        secure=request.url.scheme == "https", max_age=s.session_hours * 3600)
    return response


@app.post("/auth/logout")
def logout(request: Request):
    from .auth import COOKIE

    sessions().drop(request.cookies.get(COOKIE))
    response = JSONResponse({"ok": True})
    response.delete_cookie(COOKIE)
    return response


@app.get("/auth/me")
def me(request: Request):
    from .auth import COOKIE

    s = get_services().settings
    return {"user": sessions().user(request.cookies.get(COOKIE)),
            "default_password": (s.app_username, s.app_password) == ("admin", "admin")}


@app.exception_handler(ValueError)
@app.exception_handler(RuntimeError)
async def config_error(request: Request, exc: Exception):
    # Config / schema problems: return the message to the UI instead of a 500 traceback.
    return JSONResponse(status_code=503, content={"detail": str(exc)})


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


@app.get("/", include_in_schema=False)
def ui():
    return FileResponse(UI, headers=NO_CACHE)


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
        raise HTTPException(503, f"neo4j unavailable: {exc}")
    return {"config": config, "graph": counts}


@app.post("/ask")
def ask(body: Question):
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
def chat_message(session_id: str, body: ChatMessageIn):
    """Server-sent events: stage / token / done / error, one JSON object per `data:` line."""
    import json

    from .chat import chat_events

    svc = get_services()

    def events():
        for event in chat_events(svc, session_id, body.question):
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
def graph_overview(limit: int = 300, mode: str = "auto"):
    if mode not in ("auto", "ecosystem", "playbooks"):
        raise HTTPException(400, "mode must be auto, ecosystem or playbooks")
    return get_services().overview_graph(limit, mode)


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
