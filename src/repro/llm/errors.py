"""Turn provider errors into a small set of classes the router knows how to handle.

Providers word their errors differently, so classification is a table of
(provider, status codes, body pattern) rules, checked in order. The distinction that
matters most: a per-minute 429 is waited out on the same route, while a daily or
monthly quota error marks the route exhausted until its reset.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from enum import StrEnum
from typing import Any


class ErrorClass(StrEnum):
    RATE_LIMIT = "rate_limit"
    QUOTA_EXHAUSTED = "quota_exhausted"
    TRANSIENT = "transient"
    CONTEXT_LENGTH = "context_length"
    BAD_REQUEST = "bad_request"
    AUTH = "auth"
    EMPTY_OUTPUT = "empty_output"


class LLMError(Exception):
    def __init__(
        self,
        cls: ErrorClass,
        message: str,
        *,
        status: int | None = None,
        provider: str = "",
        retry_after: float | None = None,
        headers: Mapping[str, str] | None = None,
        response: Any = None,
    ):
        super().__init__(f"{cls.value} (HTTP {status}): {message[:300]}")
        # For EMPTY_OUTPUT: the (empty) LLMResponse, so its token usage is still counted.
        self.response = response
        self.cls = cls
        self.status = status
        self.provider = provider
        self.retry_after = retry_after
        self.headers = dict(headers or {})


_LONG_WINDOW = (
    r"(?i)per[\s_-]?day|perday|daily|per[\s_-]?month|permonth|monthly"
    r"|free-models-per-day|insufficient credits|credits? (?:exhausted|remaining: ?0)"
)
_CONTEXT = (
    r"(?i)context[\s_-]?length|maximum context|context window|too many tokens"
    r"|exceeds the maximum number of tokens|input token count|prompt is too long"
    r"|reduce the length|too large for model"
)
_QUOTA_WORDS = r"(?i)quota|exceeded|limit"

# (provider or "*", status codes or None for any, body regex or None, class). First match wins.
_BAD_KEY = r"(?i)api key not valid|invalid api key|invalid_api_key|api_key_invalid"

RULES: list[tuple[str, frozenset[int] | None, str | None, ErrorClass]] = [
    ("*", frozenset({401}), None, ErrorClass.AUTH),
    ("*", frozenset({400}), _BAD_KEY, ErrorClass.AUTH),
    ("*", frozenset({403}), _QUOTA_WORDS, ErrorClass.QUOTA_EXHAUSTED),
    ("*", frozenset({403}), None, ErrorClass.AUTH),
    ("*", frozenset({402}), None, ErrorClass.QUOTA_EXHAUSTED),
    ("*", frozenset({429}), _LONG_WINDOW, ErrorClass.QUOTA_EXHAUSTED),
    ("*", frozenset({429}), None, ErrorClass.RATE_LIMIT),
    ("*", frozenset({400, 413, 422}), _CONTEXT, ErrorClass.CONTEXT_LENGTH),
    ("*", frozenset({400, 404, 405, 413, 422}), None, ErrorClass.BAD_REQUEST),
    ("*", frozenset({408, 409, 425}), None, ErrorClass.TRANSIENT),
]


def classify(provider: str, status: int | None, body: str) -> ErrorClass:
    """Classify an HTTP error. status None means timeout or connection failure."""
    if status is None or status >= 500:
        return ErrorClass.TRANSIENT
    for prov, statuses, pattern, cls in RULES:
        if prov not in ("*", provider):
            continue
        if statuses is not None and status not in statuses:
            continue
        if pattern is not None and not re.search(pattern, body):
            continue
        return cls
    return ErrorClass.BAD_REQUEST


_BODY_RETRY = [
    re.compile(r"(?i)retry in ([\d.]+)\s*s"),
    re.compile(r'"retryDelay"\s*:\s*"([\d.]+)s"'),
    re.compile(r"(?i)try again in ([\d.]+)\s*s"),
]


def retry_after_seconds(headers: Mapping[str, str], body: str) -> float | None:
    """Seconds to wait, from a Retry-After header or the error body, if either says."""
    for key, value in headers.items():
        if key.lower() == "retry-after":
            try:
                return max(0.0, float(value))
            except ValueError:
                return None
    for pattern in _BODY_RETRY:
        m = pattern.search(body)
        if m:
            return float(m.group(1))
    return None
