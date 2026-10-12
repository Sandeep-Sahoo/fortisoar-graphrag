"""Scheduled sync: re-download changed GitHub repos, optionally re-ingest them with 0 new embeddings,
and re-pull connected Git sources. Uses GitHub only; never calls the AI model.

The API process runs a small background thread that starts the `scheduled_sync` job when it's due
(SYNC_SCHEDULE = daily | weekly) and nothing else is running.
"""
from __future__ import annotations

import json
import logging
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "data" / "sync_state.json"
INTERVALS = {"daily": timedelta(days=1), "weekly": timedelta(days=7)}
log = logging.getLogger("graphrag.scheduler")
_thread: threading.Thread | None = None


def state() -> dict:
    try:
        return json.loads(STATE.read_text())
    except (OSError, ValueError):
        return {}


def _save(data: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(data, indent=1))


def next_due(schedule: str, last: str | None) -> datetime | None:
    if schedule not in INTERVALS:
        return None
    if not last:
        return datetime.now(timezone.utc)
    return datetime.fromisoformat(last) + INTERVALS[schedule]


def status(settings) -> dict:
    st = state()
    due = next_due(settings.sync_schedule, st.get("last_run"))
    return {"schedule": settings.sync_schedule, "auto_ingest": settings.sync_auto_ingest, "sources": settings.sync_sources,
            "last_run": st.get("last_run"), "last_result": st.get("last_result"),
            "next_due": due.isoformat(timespec="seconds") if due else None}


def _run(cmd: list[str], progress, timeout: int) -> tuple[int, str]:
    progress("$ " + " ".join(cmd[1:]))
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=timeout)
    out = (proc.stdout + proc.stderr).strip()
    for line in out.splitlines()[-15:]:
        progress("  " + line)
    return proc.returncode, out


def run_sync(svc, force: bool = False, progress=print) -> dict:
    s = svc.settings
    due = next_due(s.sync_schedule, state().get("last_run"))
    if not force and (due is None or due > datetime.now(timezone.utc)):
        return {"skipped": True, "reason": "schedule is off" if due is None else f"next run due {due.isoformat(timespec='minutes')}"}
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    result: dict = {"started": started}
    code, _ = _run([sys.executable, "scripts/fetch_fortinet.py", "--repos-only", "--refresh-list"], progress, 3600)
    result["fetch"] = "ok" if code == 0 else f"exit {code}"
    if s.sync_auto_ingest:
        from .github_catalog import repo_table

        stale = [r["repo"] for r in repo_table(svc)["repos"] if r["stale"]]
        result["stale_repos"] = len(stale)
        if stale:
            progress(f"re-ingesting {len(stale)} changed repo(s) with 0 new embeddings…")
            code, _ = _run([sys.executable, "-m", "app.cli", "ingest-fortinet", "--repos", ",".join(stale),
                            "--max-embeddings", "0", "--mode", "sync"], progress, 7200)
            result["ingest"] = "ok" if code == 0 else f"exit {code}"
    if s.sync_sources:
        from .review import sources

        synced = {}
        for src in sources.sources():
            if src["kind"] != "git":
                continue
            try:
                summary = sources.sync(src["id"], svc)["summary"]
                synced[src["name"]] = "unchanged" if summary.get("unchanged") else f"{summary['playbooks']} playbooks"
            except Exception as exc:
                synced[src["name"]] = f"error: {str(exc)[:120]}"
        result["sources"] = synced
    result["finished"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    _save({"last_run": started, "last_result": result})
    return result


def _loop() -> None:
    from .config import get_settings
    from .jobs import JOBS

    time.sleep(60)
    while True:
        try:
            s = get_settings()
            due = next_due(s.sync_schedule, state().get("last_run"))
            if due and due <= datetime.now(timezone.utc) and not JOBS.running():
                log.info("scheduled sync is due; starting it")
                JOBS.start("scheduled_sync", {})
        except Exception:
            log.exception("scheduler check failed")
        time.sleep(600)


def start_background() -> None:
    global _thread
    if _thread is None:
        _thread = threading.Thread(target=_loop, name="sync-scheduler", daemon=True)
        _thread.start()
