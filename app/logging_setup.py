"""Logging: console + rotating file (data/logs/app.log), request IDs, and secret redaction."""
from __future__ import annotations

import contextvars
import logging
import logging.handlers
import os
import re
from pathlib import Path

LOG_DIR = Path(__file__).resolve().parent.parent / "data" / "logs"
LOG_FILE = LOG_DIR / "app.log"
request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")

_REDACT = [
    (re.compile(r"(?i)\b(api[_-]?key|token|secret|password|passwd|authorization|cookie)(\"?\s*[:=]\s*\"?)[^\s\",}&]+"), r"\1\2<REDACTED>"),
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}"), "Bearer <REDACTED>"),
    (re.compile(r"\bAIza[0-9A-Za-z_-]{20,}\b"), "<REDACTED_KEY>"),          # Google API keys
    (re.compile(r"\bsk-ant-[0-9A-Za-z_-]{10,}\b"), "<REDACTED_KEY>"),       # Anthropic API keys
]


class ContextFilter(logging.Filter):
    """Adds the current request id and masks secrets in every record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        message = record.getMessage()
        for pattern, replacement in _REDACT:
            message = pattern.sub(replacement, message)
        record.msg, record.args = message, ()
        return True


def setup_logging(level: str | None = None) -> None:
    """Idempotent; calling again just changes the level (used when LOG_LEVEL is changed in Settings)."""
    level = (level or os.environ.get("LOG_LEVEL") or "INFO").upper()
    if level not in ("DEBUG", "INFO", "WARNING", "ERROR"):
        level = "INFO"
    root = logging.getLogger()
    root.setLevel(level)
    if getattr(root, "_graphrag_configured", False):
        return
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s [%(request_id)s] %(message)s")
    context = ContextFilter()
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    console.addFilter(context)
    root.addHandler(console)
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(LOG_FILE, maxBytes=5_000_000, backupCount=5, encoding="utf-8")
        file_handler.setFormatter(fmt)
        file_handler.addFilter(context)
        root.addHandler(file_handler)
    except OSError as exc:  # read-only filesystem etc.: console logging still works
        root.warning("file logging disabled: %s", exc)
    for noisy in ("neo4j", "httpx", "httpcore", "google_genai", "anthropic", "uvicorn.access"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    root._graphrag_configured = True


def tail(lines: int = 300, min_level: str = "DEBUG", contains: str = "") -> list[str]:
    """Last matching lines of the log file (multi-line tracebacks stay attached to their record)."""
    if not LOG_FILE.exists():
        return []
    order = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40, "CRITICAL": 50}
    threshold = order.get(min_level.upper(), 10)
    records, current = [], None
    header = re.compile(r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d,\d+ (\w+)")
    with LOG_FILE.open(encoding="utf-8", errors="replace") as fh:
        text = fh.read()[-2_000_000:]
    for line in text.splitlines():
        m = header.match(line)
        if m:
            current = [order.get(m.group(1), 20), line]
            records.append(current)
        elif current is not None:
            current[1] += "\n" + line
    needle = contains.lower()
    picked = [r[1] for r in records if r[0] >= threshold and (not needle or needle in r[1].lower())]
    return picked[-lines:]
