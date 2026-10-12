"""Users and roles (data/users.json, scrypt-hashed passwords).

Roles
  admin    everything: settings, users, ingestion / jobs, GitHub, best-practice rules, plus analyst rights
  analyst  AI answers and explanations (uses model quota), uploads, reviews, best-practice rules, saved searches
  viewer   read-only: browse, graph-only chat, insights, exports

The APP_USERNAME / APP_PASSWORD account from .env is always an admin (bootstrap account).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import threading
from datetime import datetime, timezone
from pathlib import Path

USERS_FILE = Path(__file__).resolve().parent.parent / "data" / "users.json"
ROLES = ("admin", "analyst", "viewer")
NAME_RE = re.compile(r"^[A-Za-z0-9._@-]{2,64}$")
_lock = threading.Lock()


def _hash(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(digest).decode()


def _verify(password: str, stored: str) -> bool:
    try:
        _, salt, digest = stored.split("$")
        expected = _hash(password, base64.b64decode(salt)).split("$")[2]
        return hmac.compare_digest(expected, digest)
    except (ValueError, TypeError):
        return False


def _load() -> dict:
    try:
        return json.loads(USERS_FILE.read_text())
    except (OSError, ValueError):
        return {}


def _save(users: dict) -> None:
    USERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    USERS_FILE.write_text(json.dumps(users, indent=1))
    USERS_FILE.chmod(0o600)


def list_users() -> list[dict]:
    return [{"username": u, "role": v["role"], "disabled": v.get("disabled", False), "created": v.get("created"),
             "created_by": v.get("created_by"), "last_login": v.get("last_login")} for u, v in sorted(_load().items())]


def _check_password(password: str) -> None:
    if len(password) < 8:
        raise ValueError("password must be at least 8 characters")


def create(username: str, password: str, role: str, by: str) -> dict:
    from .config import get_settings

    if not NAME_RE.match(username):
        raise ValueError("username: 2–64 letters, digits, . _ @ -")
    if role not in ROLES:
        raise ValueError(f"role must be one of {ROLES}")
    if username == get_settings().app_username:
        raise ValueError("that name is the built-in admin account from .env")
    _check_password(password)
    with _lock:
        users = _load()
        if username in users:
            raise ValueError("user already exists")
        users[username] = {"role": role, "password": _hash(password), "created_by": by,
                           "created": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        _save(users)
    return {"username": username, "role": role}


def update(username: str, by: str, role: str | None = None, password: str | None = None,
           disabled: bool | None = None) -> dict:
    with _lock:
        users = _load()
        if username not in users:
            raise KeyError(username)
        u = users[username]
        if role is not None:
            if role not in ROLES:
                raise ValueError(f"role must be one of {ROLES}")
            u["role"] = role
        if password:
            _check_password(password)
            u["password"] = _hash(password)
        if disabled is not None:
            u["disabled"] = bool(disabled)
        u["updated_by"] = by
        _save(users)
    return {"username": username, "role": u["role"], "disabled": u.get("disabled", False)}


def delete(username: str) -> bool:
    with _lock:
        users = _load()
        if users.pop(username, None) is None:
            return False
        _save(users)
    return True


def authenticate(username: str, password: str) -> str | None:
    """Role for valid credentials, else None. Always does constant-time work."""
    from .auth import check_credentials
    from .config import get_settings

    s = get_settings()
    if check_credentials(username, password, s.app_username, s.app_password):
        return "admin"
    users = _load()
    entry = users.get(username)
    ok = _verify(password, entry["password"]) if entry else (_verify(password, _hash("x")) and False)
    if not ok or entry.get("disabled"):
        return None
    with _lock:
        users = _load()
        if username in users:
            users[username]["last_login"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            _save(users)
    return entry["role"]


def role_of(username: str | None) -> str | None:
    """Current role (re-read so role changes and disabling apply to live sessions)."""
    from .config import get_settings

    if not username:
        return None
    if username == get_settings().app_username:
        return "admin"
    entry = _load().get(username)
    if not entry or entry.get("disabled"):
        return None
    return entry["role"]


# ---------------------------------------------------------------- permissions

ADMIN_PREFIXES = ("/admin/", "/github/sync", "/embeddings/run")
ANALYST_WRITE_PREFIXES = ("/uploads", "/review/", "/search/saved", "/search/synonyms")
VIEWER_WRITE_OK = ("/chats", "/client-errors", "/auth/logout")


def allowed(role: str, method: str, path: str) -> bool:
    """Coarse route permissions; AI-using actions are checked again where they happen."""
    if role == "admin":
        return True
    if path.startswith(ADMIN_PREFIXES):
        return False
    if method in ("GET", "HEAD", "OPTIONS"):
        return True
    if role == "analyst":
        return True
    # viewer: only chat sessions (graph-only is enforced in the chat endpoint) and housekeeping
    return path.startswith(VIEWER_WRITE_OK) or path == "/cypher"


def can_use_ai(role: str | None) -> bool:
    return role in ("admin", "analyst")
