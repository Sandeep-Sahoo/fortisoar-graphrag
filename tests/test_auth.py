import time

import pytest

from app.auth import SessionStore, check_credentials


def test_credentials_and_sessions():
    assert check_credentials("admin", "admin", "admin", "admin")
    assert not check_credentials("admin", "wrong", "admin", "admin")
    assert not check_credentials("root", "admin", "admin", "admin")
    store = SessionStore(ttl_hours=1)
    token = store.create("admin")
    assert store.user(token) == "admin" and store.user("forged") is None and store.user(None) is None
    store.drop(token)
    assert store.user(token) is None
    expired = SessionStore(ttl_hours=0)
    t = expired.create("admin")
    time.sleep(0.01)
    assert expired.user(t) is None


@pytest.fixture
def client(monkeypatch):
    from fastapi.testclient import TestClient

    import app.api as api
    from app.auth import SessionStore

    class Settings:
        app_username, app_password, session_hours = "admin", "admin", 1

    class Svc:
        settings = Settings()

    monkeypatch.setattr(api, "get_services", lambda: Svc())
    monkeypatch.setattr(api, "_sessions", SessionStore(1))
    return TestClient(api.app)


def test_login_gate(client):
    assert client.get("/chats").status_code == 401                                   # API: JSON 401
    page = client.get("/", headers={"accept": "text/html"}, follow_redirects=False)
    assert page.status_code == 303 and page.headers["location"] == "/login"         # pages: redirect
    assert client.get("/login").status_code == 200                                   # login page is public
    assert client.post("/auth/login", json={"username": "admin", "password": "nope"}).status_code == 401
    ok = client.post("/auth/login", json={"username": "admin", "password": "admin"})
    assert ok.status_code == 200 and ok.json()["default_password"] is True
    cookie = ok.headers["set-cookie"]
    assert "graphrag_session=" in cookie and "HttpOnly" in cookie and "samesite=lax" in cookie.lower()
    assert client.get("/auth/me").json()["user"] == "admin"
    client.post("/auth/logout")
    assert client.get("/auth/me").status_code == 401
