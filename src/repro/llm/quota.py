"""Daily and monthly quota counters that survive between batches.

Each route's daily window resets at its provider's boundary: midnight UTC (most
providers) or midnight Pacific (Gemini, including daylight-saving changes). Monthly
token counters reset at the start of the calendar month, UTC. State is written to a
JSON file atomically after every change. Where a provider reports remaining quota,
that number overrides the local count.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from repro.llm.clock import Clock
from repro.llm.limiter import split
from repro.llm.registry import Route

PACIFIC = ZoneInfo("America/Los_Angeles")


def _zone(reset: str) -> dt.tzinfo:
    return PACIFIC if reset == "pacific_day" else dt.UTC


def day_key(reset: str, now: float) -> str:
    return dt.datetime.fromtimestamp(now, _zone(reset)).date().isoformat()


def next_day_reset(reset: str, now: float) -> float:
    tz = _zone(reset)
    local = dt.datetime.fromtimestamp(now, tz)
    tomorrow = local.date() + dt.timedelta(days=1)
    return dt.datetime.combine(tomorrow, dt.time(0, 0), tzinfo=tz).timestamp()


def month_key(now: float) -> str:
    return dt.datetime.fromtimestamp(now, dt.UTC).strftime("%Y-%m")


def next_month_reset(now: float) -> float:
    d = dt.datetime.fromtimestamp(now, dt.UTC)
    year, month = (d.year + 1, 1) if d.month == 12 else (d.year, d.month + 1)
    return dt.datetime(year, month, 1, tzinfo=dt.UTC).timestamp()


@dataclass(frozen=True)
class QuotaStatus:
    ok: bool
    reason: str = ""
    reset_at: float | None = None


class QuotaStore:
    def __init__(self, path: str | Path | None, clock: Clock, shard_count: int = 1):
        self.path = Path(path) if path else None
        self.clock = clock
        self.shard_count = shard_count
        self.state: dict[str, dict[str, Any]] = {}
        if self.path and self.path.exists():
            self.state = json.loads(self.path.read_text())

    def _entry(self, route: Route) -> dict[str, Any]:
        now = self.clock.now()
        e = self.state.setdefault(route.id, {})
        day = day_key(route.reset, now)
        if e.get("day") != day:
            e.update(day=day, day_requests=0, day_tokens=0, reported_remaining=None)
        month = month_key(now)
        if e.get("month") != month:
            e.update(month=month, month_tokens=0)
        e.setdefault("exhausted_until", None)
        return e

    def check(self, route: Route, tokens: int) -> QuotaStatus:
        now = self.clock.now()
        e = self._entry(route)
        n = self.shard_count
        until = e.get("exhausted_until")
        if until is not None and until > now:
            return QuotaStatus(False, "marked exhausted", until)
        lim = route.limits
        day_reset = next_day_reset(route.reset, now)
        rpd = split(lim.rpd, n)
        if rpd is not None and e["day_requests"] + 1 > rpd:
            return QuotaStatus(False, "requests/day", day_reset)
        remaining = e.get("reported_remaining")
        if remaining is not None and remaining <= 0:
            return QuotaStatus(False, "provider-reported remaining is 0", day_reset)
        tpd = split(lim.tpd, n)
        if tpd is not None and e["day_tokens"] + tokens > tpd:
            return QuotaStatus(False, "tokens/day", day_reset)
        tpmo = split(lim.tokens_per_month, n)
        if tpmo is not None and e["month_tokens"] + tokens > tpmo:
            return QuotaStatus(False, "tokens/month", next_month_reset(now))
        return QuotaStatus(True)

    def record(self, route: Route, tokens: int, requests: int = 1) -> None:
        e = self._entry(route)
        e["day_requests"] += requests
        e["day_tokens"] += tokens
        e["month_tokens"] += tokens
        if e.get("reported_remaining") is not None:
            e["reported_remaining"] -= requests
        self.save()

    def mark_exhausted(self, route: Route, until: float | None = None) -> float:
        """Mark a route exhausted until `until` (default: its next daily reset)."""
        e = self._entry(route)
        when = until if until is not None else next_day_reset(route.reset, self.clock.now())
        e["exhausted_until"] = when
        self.save()
        return when

    def observe_remaining(
        self, route: Route, remaining: int, reset_at: float | None = None
    ) -> None:
        """A provider told us how many requests are left; trust it over our count."""
        e = self._entry(route)
        e["reported_remaining"] = remaining
        if remaining <= 0:
            e["exhausted_until"] = reset_at or next_day_reset(route.reset, self.clock.now())
        self.save()

    def save(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".quota-")
        with os.fdopen(fd, "w") as f:
            json.dump(self.state, f, indent=1, sort_keys=True)
        os.replace(tmp, self.path)
