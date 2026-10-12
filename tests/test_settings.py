import os

import pytest

from app.settings_store import _write_env, apply


def test_write_env_replaces_uncomments_appends_and_clears(tmp_path):
    env = tmp_path / ".env"
    env.write_text("APP_MODE=local   # comment\n# LLM_MODEL=gemini-2.5-flash\nNEO4J_USER=neo4j\n")
    _write_env({"APP_MODE": "cloud", "LLM_MODEL": "gemini-2.5-flash-lite", "SESSION_HOURS": "8", "NEO4J_USER": ""}, env)
    lines = env.read_text().splitlines()
    assert "APP_MODE=cloud" in lines and "LLM_MODEL=gemini-2.5-flash-lite" in lines
    assert "SESSION_HOURS=8" in lines and "# NEO4J_USER=" in lines
    assert oct(env.stat().st_mode & 0o777) == "0o600"


@pytest.fixture
def base_env(monkeypatch):
    from app.config import get_settings

    for k, v in {"APP_MODE": "local", "GEMINI_API_KEY": "test-key", "NEO4J_PASSWORD": "pw",
                 "APP_USERNAME": "admin", "APP_PASSWORD": "admin"}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr("app.settings_store.reset_services", lambda: None)
    get_settings.cache_clear()
    yield
    for key in ("RETRIEVAL_PLAYBOOKS_K", "LLM_PROVIDER", "COMPANY_NAME"):
        os.environ.pop(key, None)
    os.environ["APP_MODE"] = "local"
    get_settings.cache_clear()


def test_apply_validates_and_rolls_back(tmp_path, base_env):
    env = tmp_path / ".env"
    env.write_text("")
    with pytest.raises(ValueError, match="RETRIEVAL_PLAYBOOKS_K"):
        apply({"RETRIEVAL_PLAYBOOKS_K": "99"}, env)
    assert os.environ.get("RETRIEVAL_PLAYBOOKS_K") is None and env.read_text() == ""   # rolled back
    with pytest.raises(ValueError, match="full-local"):
        apply({"APP_MODE": "full-local", "LLM_PROVIDER": "gemini"}, env)            # guard still enforced
    result = apply({"RETRIEVAL_PLAYBOOKS_K": "8", "GEMINI_API_KEY": ""}, env)        # blank secret = keep
    assert result["changed"] == ["RETRIEVAL_PLAYBOOKS_K"] and os.environ["GEMINI_API_KEY"] == "test-key"
    assert "RETRIEVAL_PLAYBOOKS_K=8" in env.read_text()
    with pytest.raises(ValueError, match="unknown setting"):
        apply({"PATH": "/tmp"}, env)
    with pytest.raises(ValueError, match="single line"):
        apply({"COMPANY_NAME": "a\nINJECTED=1"}, env)
