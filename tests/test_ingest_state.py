from app.ingest import state
from app.jobs import OPERATIONS


def test_record_keeps_history(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "STATE", tmp_path / "s.json")
    state.record(["solution-pack-a"], "ingest", 100, {"embedded_skipped": 3})
    state.record(["solution-pack-a"], "continue", 50, {"embedded": 50})
    entry = state.load()["solution-pack-a"]
    assert entry["last_mode"] == "continue" and entry["last_cap"] == 50
    assert [r["mode"] for r in entry["runs"]] == ["continue", "ingest"]


def test_repo_job_arguments():
    cont = OPERATIONS["continue_repos"][1]({"repos": ["a", "b"], "max_embeddings": 0})
    assert cont[-4:] == ["--repos", "a,b", "--limit", "0"]
    re_ = OPERATIONS["ingest_repos"][1]({"repos": ["a"], "mode": "reingest", "max_embeddings": 0})
    assert "reingest" in re_ and re_[re_.index("--max-embeddings") + 1] == "0"
