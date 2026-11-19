"""Token estimates, made safe by a margin and corrected by what providers report.

Providers use different tokenizers. The estimate is tiktoken (o200k_base) times 1.15.
If tiktoken's encoding file cannot be downloaded (offline sandboxes), the estimator
falls back to one token per 3 characters, which overestimates code. Each route then
learns a correction factor from reported usage: a moving average of reported/estimated,
clamped to 0.8 to 1.5.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence

MARGIN = 1.15
ALPHA = 0.2
CLAMP = (0.8, 1.5)
PER_MESSAGE = 4


def chars_counter(text: str) -> int:
    return math.ceil(len(text) / 3)


def tiktoken_counter() -> Callable[[str], int] | None:
    try:
        import tiktoken

        enc = tiktoken.get_encoding("o200k_base")
    except Exception:  # not installed, or the encoding file cannot be fetched
        return None

    def count(text: str) -> int:
        return len(enc.encode(text, disallowed_special=()))

    return count


class TokenEstimator:
    def __init__(self, counter: Callable[[str], int] | None = None, alpha: float = ALPHA):
        if counter is None:
            counter = tiktoken_counter()
            self.backend = "tiktoken" if counter else "chars/3"
        else:
            self.backend = "custom"
        self._count = counter or chars_counter
        self.alpha = alpha
        self._correction: dict[str, float] = {}

    def count(self, text: str) -> int:
        return self._count(text)

    def base(self, messages: Sequence[Mapping[str, str]]) -> int:
        """Raw count, before margin and correction."""
        return sum(self._count(m.get("content", "")) + PER_MESSAGE for m in messages) + 2

    def correction(self, route_id: str | None) -> float:
        return self._correction.get(route_id or "", 1.0)

    def estimate(self, messages: Sequence[Mapping[str, str]], route_id: str | None = None) -> int:
        return math.ceil(self.base(messages) * MARGIN * self.correction(route_id))

    def observe(self, route_id: str, base: int, reported: int) -> float:
        """Learn from provider-reported prompt tokens. Returns the new factor."""
        if base <= 0 or reported <= 0:
            return self.correction(route_id)
        ratio = reported / base
        old = self.correction(route_id)
        new = min(max(old * (1 - self.alpha) + ratio * self.alpha, CLAMP[0]), CLAMP[1])
        self._correction[route_id] = new
        return new
