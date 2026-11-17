"""Failure signatures: a stable fingerprint of why a test failed.

Two runs "fail the same way" (G4) when their signatures match. Noise that changes
between runs (addresses, temp paths, numbers) is masked.

For assertion failures the signature is the failing assertion's location rather than
its message: pytest's message shows the actual values, and for set or dict output
those can legitimately change order with the hash seed even though the same check
failed for the same reason.
"""

from __future__ import annotations

import re

from repro.models import FailureDetail

_ADDR = re.compile(r"0x[0-9a-fA-F]+")
_PATH = re.compile(r"(?:/[\w.\-+@~]+)+/?")
_NUM = re.compile(r"(?<![A-Za-z_<])\d+(?:\.\d+)?")
_WS = re.compile(r"\s+")
ASSERTION_TYPES = {"AssertionError", "Failed"}


def short_type(exc_type: str) -> str:
    return exc_type.rsplit(".", 1)[-1]


def is_assertion(exc_type: str) -> bool:
    return short_type(exc_type) in ASSERTION_TYPES


def mask_message(message: str, limit: int = 300) -> str:
    text = _ADDR.sub("<ADDR>", message)
    text = _PATH.sub("<PATH>", text)
    text = _NUM.sub("<N>", text)
    return _WS.sub(" ", text).strip()[:limit]


def failure_signature(f: FailureDetail) -> str:
    inner = f.frames[-1] if f.frames else None
    if is_assertion(f.exc_type):
        loc = f"{inner.path}:{inner.lineno}" if inner else "?"
        return f"{short_type(f.exc_type)}|{loc}"
    loc = f"{inner.path}:{inner.func}" if inner else "?"
    message = f.message if (f.exc_type or f.message) else f.text[-300:]
    return f"{f.phase}|{short_type(f.exc_type)}|{mask_message(message)}|{loc}"
