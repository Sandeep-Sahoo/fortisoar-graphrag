"""Secret / identifier scrubbing applied at ingestion, before anything is stored or embedded."""
from __future__ import annotations

import re

# Matched against whole snake_case words of a key, so `api_key` / `access_token` / `clientSecret` hit
# but `authentication_methods` or `token_type_hint`-style config names don't.
SENSITIVE_KEYS = re.compile(
    r"(^|_)(password|passwd|passphrase|secret|token|api_?key|apikey|authorization|bearer|credentials?|private_?key|cookie)(_|$)",
    re.I,
)
JINJA_ONLY = re.compile(r"^\s*\{\{.*\}\}\s*$", re.S)  # "{{vars.input.password}}" is a reference, not a secret


def _is_literal_secret(value) -> bool:
    # only non-empty literal strings count; booleans/numbers are settings, Jinja is a reference
    return isinstance(value, str) and bool(value.strip()) and not JINJA_ONLY.match(value)


def _snake(key: str) -> str:
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", key).replace("-", "_").lower()


PATTERNS = [
    # key: value / key=value pairs in free text
    (re.compile(r"(?i)\b(api[_-]?key|token|secret|password|passwd|bearer|authorization)\b(\s*[\"']?\s*[:=]\s*[\"']?)[^\s\"',}]+"), r"\1\2<REDACTED>"),
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}"), "Bearer <REDACTED>"),
    (re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"), "<EMAIL>"),
    (re.compile(r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)(?:/\d{1,2})?\b"), "<IP>"),
    (re.compile(r"\b(?:[0-9a-fA-F]{1,4}:){4,7}[0-9a-fA-F]{1,4}\b"), "<IP>"),
    (re.compile(r"(?i)\b(https?://)[^/\s:@]+:[^/\s@]+@"), r"\1<CREDS>@"),
    # AWS: access key IDs, and account IDs inside ARNs (they identify the tenant)
    (re.compile(r"\b(?:AKIA|ASIA|AIDA|AROA)[0-9A-Z]{16}\b"), "<AWS_KEY_ID>"),
    (re.compile(r"(arn:aws[\w-]*:[\w-]*:[\w-]*:)\d{12}(?=:)"), r"\1<AWS_ACCOUNT>"),
    # long opaque strings (keys, hashes of secrets); UUIDs are hyphenated so they survive
    (re.compile(r"\b[A-Za-z0-9+/_=]{40,}\b"), "<OPAQUE>"),
]


class Scrubber:
    def __init__(self, terms: tuple[str, ...] = ()):
        # Tenant / customer / hostname terms supplied via SCRUB_TERMS
        self.terms = [re.compile(re.escape(t), re.I) for t in sorted(terms, key=len, reverse=True)]

    def text(self, value: str | None) -> str:
        if not value:
            return value or ""
        for pattern, replacement in PATTERNS:
            value = pattern.sub(replacement, value)
        for term in self.terms:
            value = term.sub("<TENANT>", value)
        return value

    def obj(self, value):
        """Recursively scrub a JSON-like structure; values of sensitive keys are dropped."""
        if isinstance(value, dict):
            return {k: "<REDACTED>" if SENSITIVE_KEYS.search(_snake(str(k))) and _is_literal_secret(v) else self.obj(v)
                    for k, v in value.items()}
        if isinstance(value, list):
            return [self.obj(v) for v in value]
        if isinstance(value, str):
            return self.text(value)
        return value
