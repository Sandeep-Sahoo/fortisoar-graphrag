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


def _workspaces() -> list[str]:
    from .workspace import registry

    return list(registry())


def _account(entry: dict) -> dict:
    """Normalise a stored user: per-company access + super-admin flag (older records had one global role)."""
    access = entry.get("access")
    if not isinstance(access, dict):
        access = {ws: entry.get("role", "viewer") for ws in _workspaces()}
    access = {ws: r for ws, r in access.items() if r in ROLES}
    superadmin = bool(entry.get("superadmin", entry.get("role") == "admin" and "access" not in entry))
    return {"access": access, "superadmin": superadmin}


def _clean_access(access: dict | None, fallback_role: str | None) -> dict:
    known = set(_workspaces())
    if access is None:
        return {ws: fallback_role or "viewer" for ws in known}
    out = {}
    for ws, r in (access or {}).items():
        if ws not in known:
            raise ValueError(f"unknown company / workspace '{ws}'")
        if r in (None, "", "none"):
            continue
        if r not in ROLES:
            raise ValueError(f"role must be one of {ROLES}")
        out[ws] = r
    return out


def list_users() -> list[dict]:
    out = []
    for u, v in sorted(_load().items()):
        acc = _account(v)
        out.append({"username": u, "access": acc["access"], "superadmin": acc["superadmin"], "disabled": v.get("disabled", False),
                    "created": v.get("created"), "created_by": v.get("created_by"), "last_login": v.get("last_login")})
    return out


def _check_password(password: str) -> None:
    if len(password) < 8:
        raise ValueError("password must be at least 8 characters")


def create(username: str, password: str, role: str | None = None, by: str = "-", access: dict | None = None,
           superadmin: bool | None = None) -> dict:
    """access: {workspace: role}. Without it, `role` applies to every company (and role "admin" means super-admin)."""
    from .config import get_settings

    if not NAME_RE.match(username):
        raise ValueError("username: 2–64 letters, digits, . _ @ -")
    if role is not None and role not in ROLES:
        raise ValueError(f"role must be one of {ROLES}")
    if username == get_settings().app_username:
        raise ValueError("that name is the built-in admin account from .env")
    _check_password(password)
    acc = _clean_access(access, role)
    sa = bool(superadmin) if superadmin is not None else (access is None and role == "admin")
    if not acc and not sa:
        raise ValueError("give the user a role in at least one company")
    with _lock:
        users = _load()
        if username in users:
            raise ValueError("user already exists")
        users[username] = {"access": acc, "superadmin": sa, "password": _hash(password), "created_by": by,
                           "created": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        _save(users)
    return {"username": username, "access": acc, "superadmin": sa}


def update(username: str, by: str, role: str | None = None, password: str | None = None,
           disabled: bool | None = None, access: dict | None = None, superadmin: bool | None = None) -> dict:
    with _lock:
        users = _load()
        if username not in users:
            raise KeyError(username)
        u = users[username]
        acc = _account(u)
        if role is not None:  # one role everywhere (older API)
            if role not in ROLES:
                raise ValueError(f"role must be one of {ROLES}")
            acc["access"] = {ws: role for ws in _workspaces()}
        if access is not None:
            acc["access"] = _clean_access(access, None)
        if superadmin is not None:
            acc["superadmin"] = bool(superadmin)
        if not acc["access"] and not acc["superadmin"]:
            raise ValueError("give the user a role in at least one company")
        u.update(access=acc["access"], superadmin=acc["superadmin"])
        u.pop("role", None)
        if password:
            _check_password(password)
            u["password"] = _hash(password)
        if disabled is not None:
            u["disabled"] = bool(disabled)
        u["updated_by"] = by
        _save(users)
    return {"username": username, "access": acc["access"], "superadmin": acc["superadmin"], "disabled": u.get("disabled", False)}


def delete(username: str) -> bool:
    with _lock:
        users = _load()
        if users.pop(username, None) is None:
            return False
        _save(users)
    return True


def authenticate(username: str, password: str) -> str | None:
    """'ok' for valid, enabled credentials, else None. Always does constant-time work."""
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
    acc = _account(entry)
    return "admin" if acc["superadmin"] else (next(iter(acc["access"].values()), None) or "viewer")


def account(username: str | None) -> dict | None:
    """{superadmin, access: {workspace: role}} for an enabled user (re-read so changes apply to live sessions)."""
    from .config import get_settings

    if not username:
        return None
    if username == get_settings().app_username:
        return {"superadmin": True, "access": {ws: "admin" for ws in _workspaces()}, "builtin": True}
    entry = _load().get(username)
    if not entry or entry.get("disabled"):
        return None
    return _account(entry)


def role_in(acc: dict | None, ws: str) -> str | None:
    if not acc:
        return None
    return "admin" if acc.get("superadmin") else acc["access"].get(ws)


def role_of(username: str | None, ws: str | None = None) -> str | None:
    """Role in a workspace (default: the current one). Super-admins are admin everywhere."""
    from .workspace import current

    return role_in(account(username), ws or current())


def workspaces_for(acc: dict | None) -> list[str]:
    if not acc:
        return []
    return _workspaces() if acc.get("superadmin") else [ws for ws in _workspaces() if ws in acc["access"]]


# ---------------------------------------------------------------- permissions

ADMIN_PREFIXES = ("/admin/", "/github/sync", "/embeddings/run")
ANALYST_WRITE_PREFIXES = ("/uploads", "/review/", "/search/saved", "/search/synonyms", "/develop/")
VIEWER_WRITE_OK = ("/chats", "/client-errors", "/auth/logout")


SUPERADMIN_PREFIXES = ("/admin/users", "/admin/logs", "/admin/cache")


def allowed(role: str, method: str, path: str, superadmin: bool | None = None) -> bool:
    """Coarse route permissions for a role in the current workspace; AI use and settings scopes are checked again
    where they happen. Users, logs and caches are shared by every company, so only super-admins manage them."""
    if superadmin is None:
        superadmin = role == "admin"
    if path.startswith(SUPERADMIN_PREFIXES) and not superadmin:
        return False
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
