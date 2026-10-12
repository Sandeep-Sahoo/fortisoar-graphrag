"""Background jobs for the Settings page: an allow-list of CLI operations, one at a time, with logs."""
from __future__ import annotations

import logging
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

log = logging.getLogger("graphrag.jobs")
ROOT = Path(__file__).resolve().parent.parent
LOGS = ROOT / "data" / "jobs"

# name -> (title, argv builder(options) -> list, description, destructive)
OPERATIONS = {
    "status": ("Graph status", lambda o: ["-m", "app.cli", "status"], "Node counts and the active configuration.", False),
    "fetch": ("Fetch GitHub content", lambda o: ["scripts/fetch_fortinet.py", "--repos-only"],
              "Download / re-sync repos from the configured GitHub org (unchanged repos are skipped).", False),
    "ingest": ("Ingest GitHub content",
               lambda o: ["-m", "app.cli", "ingest-fortinet"] + (["--wipe", "--yes"] if o.get("wipe") else [])
                         + (["--skip-docs"] if o.get("skip_docs") else []),
               "Load the downloaded content into the graph (embeddings are cached; quota limits apply).", False),
    "derive": ("Rebuild derived links", lambda o: ["-m", "app.cli", "derive-links"],
               "REFERENCES, collection roll-ups and ACTS_ON module links, from the current graph. No model calls.", False),
    "link_docs": ("Link internal docs", lambda o: ["-m", "app.cli", "link-docs", "--method", o.get("method", "auto")],
                  "Name matching, then LLM or embedding suggestions for review.", False),
    "review_github": ("Score GitHub playbooks", lambda o: ["-m", "app.cli", "review-github"],
                      "Review every downloaded Fortinet playbook against the best practices and store the scores "
                      "(Review and Insights pages). No model calls; about a minute.", False),
    "sync_sources": ("Sync your playbook sources", lambda o: ["-m", "app.cli", "sync-sources"],
                     "Re-pull connected Git repositories and re-review uploads. No model calls.", False),
    "scheduled_sync": ("Sync now", lambda o: ["-m", "app.cli", "scheduled-sync", "--force"],
                       "Re-download changed GitHub repos; per Settings, re-ingest them with 0 new embeddings and re-pull "
                       "your Git sources. GitHub only, no model calls.", False),
    "embed_today": ("Embed today's batch",
                    lambda o: ["-m", "app.cli", "embed-pending", "--limit", str(int(o.get("limit") or 0))],
                    "Embed the highest-priority pending items, up to the given number (uses the embedding quota).", False),
    "continue_repos": ("Continue selected repos",
                       lambda o: ["-m", "app.cli", "embed-pending", "--repos", ",".join(o["repos"]),
                                  "--limit", str(int(o.get("max_embeddings") or 0))],
                       "Embed only the pending playbooks / doc chunks of the chosen repos, up to the cap. Nothing is rewritten.", False),
    "ingest_repos": ("Ingest selected repos",
                     lambda o: ["-m", "app.cli", "ingest-fortinet", "--repos", ",".join(o["repos"]),
                                "--mode", "reingest" if o.get("mode") == "reingest" else "ingest"]
                               + (["--max-embeddings", str(int(o["max_embeddings"]))] if o.get("max_embeddings") not in (None, "") else [])
                               + (["--skip-docs"] if o.get("skip_docs") else []),
                     "Load only the repos chosen on the GitHub page (nothing else is pruned).", False),
    "ingest_guide": ("Ingest Playbooks Guide",
                     lambda o: ["-m", "app.cli", "ingest-guide", "data/vendor_docs/fortinet/FortiSOAR-7.6.5-Playbooks_Guide.pdf"]
                               + (["--max-embeddings", str(int(o["max_embeddings"]))] if o.get("max_embeddings") not in (None, "") else []),
                     "Split the FortiSOAR 7.6.5 Playbooks Guide PDF into sections; embed the most useful first within the budget.", False),
    "embed_pending": ("Embed pending items",
                      lambda o: ["-m", "app.cli", "embed-pending", "--limit", str(int(o.get("limit") or 100)), "--what", o.get("what", "all")],
                      "Give vectors to stored playbooks / doc chunks that don't have one yet (highest priority first).", False),
    "init_db": ("Create indexes", lambda o: ["-m", "app.cli", "init-db"], "Constraints, vector and full-text indexes.", False),
    "wipe": ("Wipe the graph", lambda o: ["-m", "app.cli", "wipe", "--yes"],
             "Deletes every node, relationship and chat. Downloaded files and the embedding cache are kept.", True),
}


class JobManager:
    def __init__(self):
        self.jobs: dict[str, dict] = {}
        self.lock = threading.Lock()

    def running(self) -> dict | None:
        return next((j for j in self.jobs.values() if j["status"] == "running"), None)

    def start(self, name: str, options: dict) -> dict:
        if name not in OPERATIONS:
            raise ValueError(f"unknown operation {name}")
        with self.lock:
            if self.running():
                raise RuntimeError("another operation is still running; wait for it or cancel it")
            LOGS.mkdir(parents=True, exist_ok=True)
            job_id = uuid.uuid4().hex[:10]
            log_path = LOGS / f"{job_id}-{name}.log"
            argv = [sys.executable, "-u", *OPERATIONS[name][1](options)]
            proc = subprocess.Popen(argv, cwd=ROOT, stdout=log_path.open("w"), stderr=subprocess.STDOUT)
            job = {"id": job_id, "name": name, "title": OPERATIONS[name][0], "status": "running", "started": time.time(),
                   "ended": None, "returncode": None, "log": str(log_path), "options": options, "_proc": proc}
            self.jobs[job_id] = job
            # start the watcher before anything else can fail, so a job can never be left "running" forever
            threading.Thread(target=self._wait, args=(job,), daemon=True).start()
        log.info("operation %s started (job %s, options %s)", name, job_id, options)
        return self.public(job)

    def _wait(self, job: dict) -> None:
        code = job["_proc"].wait()
        job.update(status="cancelled" if job["status"] == "cancelling" else ("done" if code == 0 else "failed"),
                   returncode=code, ended=time.time())
        (log.info if job["status"] == "done" else log.warning)(
            "operation %s %s (job %s, exit %s, %.0fs, log %s)", job["name"], job["status"], job["id"], code,
            job["ended"] - job["started"], job["log"])
        if job["name"] in ("ingest", "wipe", "derive", "link_docs", "init_db"):
            from .settings_store import reset_services
            reset_services()  # pick up schema / data changes cleanly

    def cancel(self, job_id: str) -> dict:
        job = self.jobs[job_id]
        if job["status"] == "running":
            job["status"] = "cancelling"
            job["_proc"].terminate()
            log.info("operation %s cancel requested (job %s)", job["name"], job_id)
        return self.public(job)

    def public(self, job: dict, tail: int = 0) -> dict:
        out = {k: v for k, v in job.items() if not k.startswith("_")}
        if tail:
            try:
                text = Path(job["log"]).read_text(errors="replace")
                out["log_tail"] = "\n".join(text.splitlines()[-tail:])
            except OSError:
                out["log_tail"] = ""
        return out

    def list(self) -> list[dict]:
        return [self.public(j) for j in sorted(self.jobs.values(), key=lambda j: -j["started"])][:20]


JOBS = JobManager()
