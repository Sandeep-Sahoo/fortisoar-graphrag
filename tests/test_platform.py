import pytest


def test_users_roles_and_permissions(monkeypatch):
    from app import users

    class S:
        app_username, app_password = "admin", "admin"

    monkeypatch.setattr("app.config.get_settings", lambda: S())
    users.create("ana", "longpassword", "analyst", "admin")
    users.create("vic", "longpassword", "viewer", "admin")
    with pytest.raises(ValueError):
        users.create("ana", "longpassword", "viewer", "admin")       # duplicate
    with pytest.raises(ValueError):
        users.create("bob", "short", "viewer", "admin")              # weak password
    with pytest.raises(ValueError):
        users.create("admin", "longpassword", "viewer", "admin")     # built-in name
    assert users.authenticate("ana", "longpassword") == "analyst"
    assert users.authenticate("ana", "wrong") is None
    assert users.authenticate("admin", "admin") == "admin"
    assert "longpassword" not in users.USERS_FILE.read_text()      # hashed
    users.update("ana", "admin", disabled=True)
    assert users.authenticate("ana", "longpassword") is None and users.role_of("ana") is None

    allowed = users.allowed
    assert allowed("viewer", "GET", "/catalog/playbooks")
    assert not allowed("viewer", "POST", "/uploads")
    assert not allowed("viewer", "GET", "/admin/settings")
    assert allowed("viewer", "POST", "/chats/x/messages")            # graph-only enforced in the endpoint
    assert allowed("analyst", "POST", "/uploads") and allowed("analyst", "POST", "/review/rules")
    assert not allowed("analyst", "POST", "/admin/jobs")
    assert allowed("admin", "POST", "/admin/jobs")
    assert users.can_use_ai("analyst") and not users.can_use_ai("viewer")


def test_usage_limits_and_metering(monkeypatch):
    from app import usage

    class S:
        enforce_daily_limits, llm_daily_limit, embed_daily_limit = True, 2, 5
        llm_provider, embed_provider, llm_model, embed_model = "gemini", "gemini", "m", "e"

    monkeypatch.setattr("app.config.get_settings", lambda: S())

    class Inner:
        dim = 3
        def complete(self, system, prompt, json_mode=False):
            return "ok"
        def stream(self, system, prompt):
            yield "o"
        def embed(self, texts, query=False):
            return [[0.0, 0.0, 1.0] for _ in texts]

    llm = usage.MeteredLLM(Inner(), "gemini")
    assert llm.complete("s", "p") == "ok" and "".join(llm.stream("s", "p")) == "o"
    with pytest.raises(usage.DailyLimitReached):
        llm.complete("s", "p")
    emb = usage.MeteredEmbedder(Inner(), "gemini")
    emb.embed(["a", "b", "c"])
    with pytest.raises(usage.DailyLimitReached):
        emb.embed(["d", "e", "f"])
    summary = usage.summary()
    assert summary["llm"]["used"] == 2 and summary["llm"]["remaining"] == 0 and summary["embeddings"]["remaining"] == 2


def test_cached_llm_is_free_on_repeat(tmp_path, monkeypatch):
    from app import usage

    class S:
        enforce_daily_limits, llm_daily_limit, embed_daily_limit = True, 1, 0
        llm_provider, embed_provider, llm_model, embed_model = "gemini", "gemini", "m", "e"

    monkeypatch.setattr("app.config.get_settings", lambda: S())

    class Inner:
        def complete(self, system, prompt, json_mode=False):
            return "answer"
        def stream(self, system, prompt):
            yield "ans"
            yield "wer"

    llm = usage.CachedLLM(usage.MeteredLLM(Inner(), "gemini"), tmp_path / "c.sqlite", "gemini:m")
    assert "".join(llm.stream("s", "q")) == "answer"
    assert "".join(llm.stream("s", "q")) == "answer"          # cached: no second model call, limit not hit
    assert usage.counts()["llm"] == 1 and usage.counts()["llm_cached"] == 1


def test_cached_embedder_leaves_over_limit_items_pending(tmp_path, monkeypatch):
    from app import usage
    from app.providers import CachedEmbedder

    class S:
        enforce_daily_limits, llm_daily_limit, embed_daily_limit = True, 0, 2
        llm_provider, embed_provider, llm_model, embed_model = "gemini", "gemini", "m", "e"

    monkeypatch.setattr("app.config.get_settings", lambda: S())

    class Inner:
        dim = 3
        def embed(self, texts, query=False):
            return [[1.0, 0.0, 0.0] for _ in texts]

    cache = CachedEmbedder(usage.MeteredEmbedder(Inner(), "gemini"), tmp_path / "e.sqlite", "g:e")
    out = cache.embed(["a", "b", "c"])
    assert out[:2] != [None, None] and out[2] is None and cache.skipped == 1


def test_synonym_expansion_and_saved_searches():
    from app import search_store
    from app.retrieval import group_query, keyword_terms

    terms = keyword_terms("lock out the compromised user")
    groups = search_store.expand("lock out the compromised user", terms)
    lock = next(g for g in groups if g[0] == "lock")
    assert "disable" in lock and any("account" in g for g in groups)
    assert group_query(["file hash", "md5"]) == '"file hash" OR md5'
    search_store.set_synonyms({"Foo": "bar, baz"})
    assert search_store.synonyms() == {"foo": ["bar", "baz"]}
    entry = search_store.save_search("VT", "virustotal hash", {"origin": "github"}, "t")
    search_store.record_run(entry["id"], 5)
    run = search_store.record_run(entry["id"], 7)
    assert run["previous_count"] == 5 and run["last_count"] == 7
    assert search_store.delete_search(entry["id"]) and not search_store.saved()


def test_audit_log_roundtrip():
    from app import audit

    audit.record("ana", "rule.add", "CUS-1", title="x", empty="")
    audit.record("bob", "source.upload", "abc")
    assert [e["action"] for e in audit.read()] == ["source.upload", "rule.add"]
    assert audit.read(user="ana")[0]["details"] == {"title": "x"}


def test_roles_enforced_by_middleware(monkeypatch):
    from fastapi.testclient import TestClient

    import app.api as api
    from app import users
    from app.auth import SessionStore

    class S:
        app_username, app_password, session_hours = "admin", "admin", 1

    monkeypatch.setattr("app.config.get_settings", lambda: S())

    class Svc:
        settings = S()

    monkeypatch.setattr(api, "get_services", lambda: Svc())
    monkeypatch.setattr(api, "_sessions", SessionStore(1))
    users.create("vic", "viewerpass1", "viewer", "admin")
    users.create("ana", "analystpass1", "analyst", "admin")
    client = TestClient(api.app)

    assert client.post("/auth/login", json={"username": "vic", "password": "viewerpass1"}).json()["role"] == "viewer"
    me = client.get("/auth/me").json()
    assert me["role"] == "viewer" and me["can_use_ai"] is False
    assert client.post("/uploads", json={"name": "x", "files": []}).status_code == 403
    assert client.get("/admin/users").status_code == 403
    assert client.post("/review/rules", json={"title": "x"}).status_code == 403
    r = client.post("/chats/s1/messages", json={"question": "q", "mode": "ai"})
    assert r.status_code == 403 and "Graph only" in r.json()["detail"]
    client.post("/auth/logout")

    client.post("/auth/login", json={"username": "ana", "password": "analystpass1"})
    assert client.get("/admin/users").status_code == 403          # still not admin
    r = client.post("/review/rules", json={"title": "Peer review", "check": "manual"})
    assert r.status_code == 200 and r.json()["id"].startswith("CUS-")
