import sys
import time

from app import jobs


def test_job_runs_logs_and_finishes(monkeypatch, tmp_path):
    monkeypatch.setattr(jobs, "LOGS", tmp_path)
    monkeypatch.setitem(jobs.OPERATIONS, "echo", ("Echo", lambda o: ["-c", "print('hello from job')"], "test", False))
    monkeypatch.setattr("app.settings_store.reset_services", lambda: None)
    manager = jobs.JobManager()
    job = manager.start("echo", {})
    for _ in range(100):
        if manager.jobs[job["id"]]["status"] != "running":
            break
        time.sleep(0.05)
    done = manager.public(manager.jobs[job["id"]], tail=5)
    assert done["status"] == "done" and done["returncode"] == 0
    assert "hello from job" in done["log_tail"]
    assert manager.running() is None   # nothing left blocking new jobs
