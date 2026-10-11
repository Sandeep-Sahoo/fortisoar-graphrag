"""Minimal login for the web UI: one configured user, opaque session tokens in an HttpOnly cookie.

Sessions live in memory, so a server restart logs everyone out. That's fine for a single-node
internal tool; put the app behind SSO/a reverse proxy for anything larger.
"""
from __future__ import annotations

import hmac
import secrets
import threading
import time

COOKIE = "graphrag_session"


class SessionStore:
    def __init__(self, ttl_hours: int):
        self.ttl = ttl_hours * 3600
        self._sessions: dict[str, tuple[str, float]] = {}
        self._lock = threading.Lock()

    def create(self, username: str) -> str:
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._sessions[token] = (username, time.time() + self.ttl)
        return token

    def user(self, token: str | None) -> str | None:
        if not token:
            return None
        with self._lock:
            entry = self._sessions.get(token)
            if not entry:
                return None
            if entry[1] < time.time():
                del self._sessions[token]
                return None
            return entry[0]

    def drop(self, token: str | None) -> None:
        with self._lock:
            self._sessions.pop(token or "", None)


def check_credentials(username: str, password: str, expected_user: str, expected_password: str) -> bool:
    # compare both fields in constant time, and always do both comparisons
    user_ok = hmac.compare_digest(username.encode(), expected_user.encode())
    pass_ok = hmac.compare_digest(password.encode(), expected_password.encode())
    return user_ok and pass_ok
