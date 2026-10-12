"""Fortinet and Marlink workspaces are fully separate; only best-practice rules can be imported between them."""
from types import SimpleNamespace

import pytest

from app import workspace


def test_paths_and_files_are_separate():
    from app import search_store
    from app.develop import store as dstore
    from app.review import rules

    rules.add_rule({"title": "Fortinet only", "check": "manual"}, "t")
    search_store.save_search("fortinet search", "virustotal", {}, "t")
    fortinet_design = dstore.create("t")
    with workspace.using("marlink"):
        assert workspace.data_dir() == workspace.DATA_ROOT / "workspaces" / "marlink"
        assert not [r for r in rules.all_rules() if not r["builtin"]]          # no Fortinet rules leak in
        assert search_store.saved() == []
        assert dstore.get(fortinet_design["id"]) is None
        rules.add_rule({"title": "Marlink only", "check": "manual"}, "t")
        search_store.set_synonyms({"vpn": ["ipsec"]})
    titles = [r["title"] for r in rules.all_rules() if not r["builtin"]]
    assert titles == ["Fortinet only"]
    assert "vpn" not in search_store.synonyms()


def test_rule_import_preview_import_and_reimport():
    from app.review import rules

    with workspace.using("fortinet"):
        r1 = rules.add_rule({"title": "Peer review", "check": "manual", "severity": "medium"}, "ana")
        rules.update_rule("SEC-07", {"enabled": False}, "ana")
    with workspace.using("marlink"):
        prev = rules.import_preview("fortinet")
        assert [r["import_status"] for r in prev["custom"]] == ["new"]
        assert prev["overrides"][0]["id"] == "SEC-07" and prev["overrides"][0]["import_status"] == "changed"
        out = rules.import_rules("fortinet", [r1["id"]], ["SEC-07"], "bob")
        assert out == {"added": 1, "updated": 0, "overrides": 1}
        mine = [r for r in rules.all_rules() if not r["builtin"]]
        assert mine[0]["origin"]["workspace"] == "fortinet" and mine[0]["id"] != r1["id"]
        assert not next(r for r in rules.all_rules() if r["id"] == "SEC-07")["enabled"]
        assert rules.import_preview("fortinet")["custom"][0]["import_status"] == "same"
    with workspace.using("fortinet"):
        rules.update_rule(r1["id"], {"severity": "high"}, "ana")
    with workspace.using("marlink"):
        assert rules.import_preview("fortinet")["custom"][0]["import_status"] == "changed"
        assert rules.import_rules("fortinet", [r1["id"]], [], "bob")["updated"] == 1
        assert len([r for r in rules.all_rules() if not r["builtin"]]) == 1          # updated in place, no duplicate


def test_services_per_workspace_share_models():
    from app import services

    services._clear()
    try:
        f, m = services.get_services("fortinet"), services.get_services("marlink")
        assert f is not m and m._shared is f
        assert "7688" in m.settings.neo4j_uri and m.settings.neo4j_uri != f.settings.neo4j_uri
        with workspace.using("marlink"):
            assert services.get_services() is m
    finally:
        services._clear()


def test_connector_catalog_marlink_first_then_fortinet(monkeypatch):
    from pathlib import Path

    from app.develop import catalog
    from app.review import sources

    if not Path("data/fortinet/github").exists():
        pytest.skip("connector catalog not downloaded")
    wf = {"uuid": "x", "steps": [{"arguments": {"connector": "marlink-vsat", "operation": "block_terminal", "operationTitle": "Block Terminal",
                                                "params": {"terminal_ip": ""}, "version": "1.0.0"}},
                                 {"arguments": {"connector": "virustotal", "operation": "query_ip", "params": {"ip": ""}}}]}
    monkeypatch.setattr(sources, "sources", lambda: [{"id": "s1", "last_synced": "t"}])
    monkeypatch.setattr(sources, "_workflows", lambda sid: [("f.json", "c", wf)])
    with workspace.using("marlink"):
        cs = catalog.connectors()
        assert cs["marlink-vsat"]["source"] == "marlink"                        # Marlink's own connector
        assert cs["virustotal"]["source"] == "marlink"                          # used by Marlink too
        assert cs["fortigate-firewall"]["source"] == "fortinet"                 # fallback to Fortinet GitHub
        top = catalog.suggest("enrich", ["ip"], limit=3)
        assert top[0]["connector"] == "virustotal" and top[0]["source"] == "marlink"
        assert catalog.suggest("contain", ["ip"], limit=1)[0]["connector"] == "marlink-vsat"
    assert "marlink-vsat" not in catalog.connectors()                            # Fortinet workspace unaffected


def test_api_follows_the_workspace_cookie(monkeypatch):
    from fastapi.testclient import TestClient

    import app.api as api
    from app.auth import SessionStore

    class S:
        app_username, app_password, session_hours = "admin", "admin", 1

    monkeypatch.setattr("app.config.get_settings", lambda: S())
    monkeypatch.setattr(api, "get_services", lambda *a: SimpleNamespace(settings=S()))
    monkeypatch.setattr(api, "_sessions", SessionStore(1))
    client = TestClient(api.app)
    client.post("/auth/login", json={"username": "admin", "password": "admin"})
    assert client.get("/auth/me").json()["workspace"]["id"] == "fortinet"
    client.cookies.set(workspace.COOKIE, "marlink")
    me = client.get("/auth/me").json()
    assert me["workspace"]["id"] == "marlink" and "github" not in me["workspace"]["features"]
    assert client.get("/github/repos").status_code == 404
    r = client.post("/admin/jobs", json={"operation": "review_github"})
    assert r.status_code == 400 and "Fortinet workspace" in r.json()["detail"]
    client.post("/review/rules", json={"title": "Marlink rule", "check": "manual"})
    assert [r["title"] for r in client.get("/review/rules").json()["rules"] if not r["builtin"]] == ["Marlink rule"]
    client.cookies.set(workspace.COOKIE, "fortinet")
    assert [r["title"] for r in client.get("/review/rules").json()["rules"] if not r["builtin"]] == []
    client.cookies.set(workspace.COOKIE, "nonsense")
    assert client.get("/auth/me").json()["workspace"]["id"] == "fortinet"       # unknown -> default
    from app import audit
    assert {e["ws"] for e in audit.read()} >= {"fortinet", "marlink"}


def _client(monkeypatch):
    from fastapi.testclient import TestClient

    import app.api as api
    from app.auth import SessionStore

    class S:
        app_username, app_password, session_hours = "admin", "admin", 1

    monkeypatch.setattr("app.config.get_settings", lambda: S())
    monkeypatch.setattr(api, "_sessions", SessionStore(1))
    return TestClient(api.app)


def _login(client, user, pw):
    client.cookies.clear()
    assert client.post("/auth/login", json={"username": user, "password": pw}).status_code == 200


def test_company_access_and_scoped_settings(monkeypatch, tmp_path):
    from app import users

    client = _client(monkeypatch)
    users.create("mia", "marlinkadmin1", by="admin", access={"marlink": "admin"})
    users.create("ana", "analystboth1", by="admin", access={"fortinet": "analyst", "marlink": "viewer"})

    _login(client, "mia", "marlinkadmin1")
    me = client.get("/auth/me").json()
    assert me["workspaces"] == ["marlink"] and me["workspace"]["id"] == "marlink" and me["role"] == "admin"
    client.cookies.set(workspace.COOKIE, "fortinet")                       # not allowed -> stays in Marlink
    assert client.get("/auth/me").json()["workspace"]["id"] == "marlink"
    assert [w["id"] for w in client.get("/workspaces").json()["workspaces"]] == ["marlink"]
    assert [s["id"] for s in client.get("/admin/settings/scopes").json()["scopes"]] == ["marlink"]
    assert client.get("/admin/settings?scope=general").status_code == 403
    assert client.get("/admin/settings?scope=fortinet").status_code == 403
    assert client.get("/admin/users").status_code == 403                   # users are super-admin only
    keys = {f["key"] for f in client.get("/admin/settings?scope=marlink").json()["fields"]}
    assert "COMPANY_NAME" in keys and "NEO4J_URI" in keys and "FETCH_ORG" not in keys and "LLM_MODEL" not in keys
    r = client.post("/admin/settings", json={"scope": "marlink", "values": {"COMPANY_NAME": "Marlink SOC", "SCRUB_TERMS": "marlink-prod"}})
    assert r.status_code == 200 and set(r.json()["changed"]) == {"COMPANY_NAME", "SCRUB_TERMS"}
    assert client.post("/admin/settings", json={"scope": "marlink", "values": {"LLM_MODEL": "x"}}).status_code == 400

    stored = workspace.registry()["marlink"]["settings"]
    assert stored == {"COMPANY_NAME": "Marlink SOC", "SCRUB_TERMS": "marlink-prod"}

    _login(client, "ana", "analystboth1")
    client.cookies.set(workspace.COOKIE, "marlink")
    assert client.get("/auth/me").json()["role"] == "viewer"
    assert client.post("/review/rules", json={"title": "x", "check": "manual"}).status_code == 403   # viewer in Marlink
    client.cookies.set(workspace.COOKIE, "fortinet")
    assert client.get("/auth/me").json()["role"] == "analyst"
    assert client.post("/review/rules", json={"title": "x", "check": "manual"}).status_code == 200   # analyst in Fortinet
    assert client.get("/admin/settings/scopes").status_code == 403

    _login(client, "admin", "admin")
    assert [s["id"] for s in client.get("/admin/settings/scopes").json()["scopes"]] == ["general", "fortinet", "marlink"]
    listed = {u["username"]: u for u in client.get("/admin/users").json()["users"]}
    assert listed["mia"]["access"] == {"marlink": "admin"} and not listed["mia"]["superadmin"]
    r = client.patch("/admin/users/ana", json={"username": "ana", "access": {"fortinet": "analyst", "marlink": "none"}})
    assert r.status_code == 200 and r.json()["access"] == {"fortinet": "analyst"}


def test_settings_for_applies_company_values(monkeypatch):
    from app.config import get_settings

    monkeypatch.setenv("GIT_TOKEN_MARLINK", "marlink-token")
    (workspace.CONFIG).write_text('{"marlink": {"settings": {"COMPANY_NAME": "Marlink SOC", "SCRUB_TERMS": "a, b", "NEO4J_URI": "bolt://db:7999"}}}')
    try:
        base = get_settings()
    except Exception:
        pytest.skip("app settings not configured")
    m = workspace.settings_for(base, "marlink")
    assert m.company_name == "Marlink SOC" and m.scrub_terms == ("a", "b") and m.neo4j_uri == "bolt://db:7999"
    assert m.git_token == "marlink-token" and workspace.settings_for(base, "fortinet") is base
