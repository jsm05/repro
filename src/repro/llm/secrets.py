"""API keys: read them in one place, and scrub them from anything written or logged.

Keys are stripped of surrounding whitespace (a key pasted with a trailing newline
makes HTTP libraries reject the header, and their error message then contains the
whole header). Every report, log line and error message that could carry provider
text goes through redact() first. A leaked report on 2026-11-20 is why this exists.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from repro.llm.registry import Registry

REDACTED = "[REDACTED]"
MIN_SECRET_LEN = 8


def read_key(env: Mapping[str, str], name: str) -> str:
    return env.get(name, "").strip()


def known_secrets(registry: Registry, env: Mapping[str, str]) -> list[str]:
    """Every configured key value, raw and stripped, longest first."""
    values: set[str] = set()
    for prov in registry.data.providers.values():
        raw = env.get(prov.api_key_env, "")
        for v in (raw, raw.strip()):
            if len(v.strip()) >= MIN_SECRET_LEN:
                values.add(v)
    return sorted(values, key=len, reverse=True)


def redact(text: str, secrets: Iterable[str]) -> str:
    for s in secrets:
        if s and len(s.strip()) >= MIN_SECRET_LEN:
            text = text.replace(s, REDACTED).replace(s.strip(), REDACTED)
            text = text.replace(repr(s.encode())[2:-1], REDACTED)  # bytes-escaped form
    return text
