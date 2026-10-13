from types import SimpleNamespace

from fastapi.testclient import TestClient


def _client(monkeypatch):
    from app import api
    from app.auth import SessionStore

    class S:
        app_username, app_password, session_hours, mode = "admin", "admin", 1, "local"

    monkeypatch.setattr("app.config.get_settings", lambda: S())
    monkeypatch.setattr(api, "get_services", lambda: SimpleNamespace(settings=S()))
    monkeypatch.setattr(api, "_sessions", SessionStore(1))
    client = TestClient(api.app)
    client.post("/auth/login", json={"username": "admin", "password": "admin"})
    return client


def test_app_info_has_version_and_owner(monkeypatch):
    from app import __version__

    monkeypatch.setenv("OWNER_CONTACT", "team@example.com")
    info = _client(monkeypatch).get("/app/info").json()
    assert info["version"] == __version__
    assert info["owner"]["contact"] == "team@example.com"
    assert info["owner"]["name"]  # defaults to the repo owner
    assert "commit" in info and "repo" in info


def test_guide_settings_reference_has_no_values(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "secret-value")
    ref = _client(monkeypatch).get("/guide/settings").json()
    keys = {r["key"] for r in ref}
    assert {"APP_MODE", "NEO4J_URI", "OWNER_NAME", "GEMINI_API_KEY"} <= keys
    assert all("value" not in r for r in ref)
    assert "secret-value" not in str(ref)
    mode = next(r for r in ref if r["key"] == "APP_MODE")
    assert mode["options"] == ["local", "cloud", "full-local"] and mode["default"] == "local"
