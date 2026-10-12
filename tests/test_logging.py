import logging

import pytest

from app import logging_setup


def test_redaction_and_request_id(tmp_path, monkeypatch):
    record = logging.LogRecord("t", logging.INFO, __file__, 1, "key api_key=%s and Bearer abcdefghijk123 AIzaSyD-FAKEFAKEFAKEFAKEFAKE1",
                               ("supersecret",), None)
    token = logging_setup.request_id_var.set("req-123")
    logging_setup.ContextFilter().filter(record)
    logging_setup.request_id_var.reset(token)
    assert record.request_id == "req-123"
    assert "supersecret" not in record.msg and "abcdefghijk123" not in record.msg and "AIzaSy" not in record.msg


def test_tail_keeps_tracebacks_and_filters(tmp_path, monkeypatch):
    log_file = tmp_path / "app.log"
    log_file.write_text(
        "2026-10-12 10:00:00,001 INFO    a [-] started\n"
        "2026-10-12 10:00:01,002 ERROR   a [r1] boom\nTraceback (most recent call last):\n  File x\nValueError: bad\n"
        "2026-10-12 10:00:02,003 WARNING a [r2] slow neo4j\n")
    monkeypatch.setattr(logging_setup, "LOG_FILE", log_file)
    errors = logging_setup.tail(10, "ERROR")
    assert len(errors) == 1 and "ValueError: bad" in errors[0]
    assert [l.split("] ")[1] for l in logging_setup.tail(10, "WARNING")] == ["boom\nTraceback (most recent call last):\n  File x\nValueError: bad", "slow neo4j"]
    assert len(logging_setup.tail(10, "DEBUG", "neo4j")) == 1


@pytest.fixture
def client(monkeypatch):
    from fastapi.testclient import TestClient

    import app.api as api
    from app.auth import SessionStore

    class Settings:
        app_username, app_password, session_hours = "admin", "admin", 1

    class Svc:
        settings = Settings()

    store = SessionStore(1)
    monkeypatch.setattr(api, "get_services", lambda: Svc())
    monkeypatch.setattr(api, "_sessions", store)
    c = TestClient(api.app, raise_server_exceptions=False)
    c.post("/auth/login", json={"username": "admin", "password": "admin"})
    return c


def test_unexpected_errors_are_clean_and_traceable(client, monkeypatch):
    import app.api as api
    from neo4j.exceptions import ServiceUnavailable

    monkeypatch.setattr(api, "get_services", lambda: (_ for _ in ()).throw(KeyError("internal detail")))
    r = client.get("/chats")
    assert r.status_code == 500 and "internal detail" not in r.text
    assert r.json()["request_id"] == r.headers["x-request-id"]

    def down():
        raise ServiceUnavailable("connection refused")
    monkeypatch.setattr(api, "get_services", down)
    r = client.get("/chats")
    assert r.status_code == 503 and "graph database isn't reachable" in r.json()["detail"]

    r = client.get("/chats", headers={"X-Request-ID": "trace-me"})
    assert r.headers["x-request-id"] == "trace-me"
