"""Keep tests away from the real data/ files (users, audit, usage, reviews, uploads, saved searches)."""
import pytest


@pytest.fixture(autouse=True)
def isolated_data(tmp_path, monkeypatch):
    import app.audit as audit
    import app.develop.store as develop_store
    import app.docgen as docgen
    import app.exports as exports
    import app.ingest.state as ingest_state
    import app.review.rules as rules
    import app.review.sources as sources
    import app.review.store as store
    import app.search_store as search_store
    import app.usage as usage
    import app.users as users
    import app.workspace as workspace

    monkeypatch.setattr(audit, "AUDIT_FILE", tmp_path / "audit.jsonl")
    monkeypatch.setattr(develop_store, "DIR", tmp_path / "develop")
    monkeypatch.setattr(docgen, "DIR", tmp_path / "documentation")
    monkeypatch.setattr(users, "USERS_FILE", tmp_path / "users.json")
    monkeypatch.setattr(usage, "USAGE_FILE", tmp_path / "usage.json")
    monkeypatch.setattr(rules, "RULES_FILE", tmp_path / "review" / "rules.json")
    for name in ("ANSWERS", "HISTORY", "AI_NOTES", "GITHUB"):
        monkeypatch.setattr(store, name, tmp_path / "review" / f"{name.lower()}.json")
    monkeypatch.setattr(store, "SOURCES_DIR", tmp_path / "review" / "sources")
    monkeypatch.setattr(sources, "ROOT", tmp_path / "uploads")
    monkeypatch.setattr(sources, "SOURCES_FILE", tmp_path / "uploads" / "sources.json")
    monkeypatch.setattr(search_store, "DIR", tmp_path / "search")
    monkeypatch.setattr(search_store, "SYNONYMS_FILE", tmp_path / "search" / "synonyms.json")
    monkeypatch.setattr(search_store, "SAVED_FILE", tmp_path / "search" / "saved.json")
    monkeypatch.setattr(exports, "HISTORY", tmp_path / "exports.json")
    monkeypatch.setattr(ingest_state, "STATE", tmp_path / "ingest_state.json")
    monkeypatch.setattr(workspace, "DATA_ROOT", tmp_path)
    monkeypatch.setattr(workspace, "CONFIG", tmp_path / "workspaces.json")
    token = workspace.set_current(workspace.DEFAULT)
    yield tmp_path
    workspace.reset(token)
