"""Per-route request and token limits over sliding windows.

DESIGN 5.5 said token buckets. A token bucket of capacity C refilling C per minute
lets up to 2C through in one 60-second window (a full bucket plus a minute of
refill), so six shards each holding 1/6 of a limit could together send twice the
limit. A sliding window bounds every window exactly, which is what the static shard
split needs (T3.21).

Before a call the limiter reserves the worst case (estimated prompt + max_tokens);
after the call it settles the reservation to the tokens actually used.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field

from repro.llm.clock import Clock
from repro.llm.registry import Route


def parse_shard(text: str) -> tuple[int, int]:
    """'2/6' -> (2, 6). Shards are numbered from 1."""
    k, _, n = text.partition("/")
    shard, count = int(k), int(n)
    if count < 1 or not 1 <= shard <= count:
        raise ValueError(f"bad shard {text!r}: expected k/N with 1 <= k <= N")
    return shard, count


def split(limit: float | None, shards: int) -> float | None:
    """This shard's share of a limit (at least 1 unit, so a shard can always progress)."""
    if limit is None:
        return None
    return max(1.0, math.floor(limit / shards))


@dataclass
class Window:
    limit: float
    seconds: float
    events: deque[list[float]] = field(default_factory=deque)  # [time, amount]

    def _prune(self, now: float) -> None:
        while self.events and self.events[0][0] <= now - self.seconds:
            self.events.popleft()

    def used(self, now: float) -> float:
        self._prune(now)
        return sum(e[1] for e in self.events)

    def wait_for(self, now: float, amount: float) -> float:
        """Seconds until `amount` fits in the window (inf if it never can)."""
        if amount > self.limit:
            return math.inf
        self._prune(now)
        excess = self.used(now) + amount - self.limit
        if excess <= 0:
            return 0.0
        freed = 0.0
        for t, a in self.events:
            freed += a
            if freed >= excess:
                return max(0.0, t + self.seconds - now)
        return math.inf

    def add(self, now: float, amount: float) -> list[float]:
        event = [now, amount]
        self.events.append(event)
        return event


@dataclass
class Reservation:
    route_id: str
    tokens: int
    token_events: list[list[float]]


class RateLimiter:
    def __init__(self, clock: Clock, shard_count: int = 1):
        self.clock = clock
        self.shard_count = shard_count
        self._windows: dict[str, dict[str, Window]] = {}
        self.waited_s = 0.0

    def _route_windows(self, route: Route) -> dict[str, Window]:
        if route.id not in self._windows:
            lim = route.limits
            n = self.shard_count
            wins: dict[str, Window] = {}
            for name, value, seconds in (
                ("rps", split(lim.rps, n), 1.0),
                ("rpm", split(lim.rpm, n), 60.0),
                ("tpm", split(lim.tpm, n), 60.0),
            ):
                if value is not None:
                    wins[name] = Window(value, seconds)
            self._windows[route.id] = wins
        return self._windows[route.id]

    def limit(self, route: Route, name: str) -> float | None:
        win = self._route_windows(route).get(name)
        return win.limit if win else None

    def wait_time(self, route: Route, tokens: int) -> float:
        now = self.clock.now()
        waits = [0.0]
        for name, win in self._route_windows(route).items():
            waits.append(win.wait_for(now, tokens if name == "tpm" else 1))
        return max(waits)

    def acquire(self, route: Route, tokens: int, max_wait: float) -> Reservation | None:
        """Reserve a slot, sleeping up to max_wait. None if it would take longer."""
        wait = self.wait_time(route, tokens)
        if wait > max_wait:
            return None
        if wait > 0:
            self.clock.sleep(wait)
            self.waited_s += wait
        now = self.clock.now()
        token_events = []
        for name, win in self._route_windows(route).items():
            event = win.add(now, tokens if name == "tpm" else 1)
            if name == "tpm":
                token_events.append(event)
        return Reservation(route.id, tokens, token_events)

    def settle(self, reservation: Reservation, actual_tokens: int) -> None:
        for event in reservation.token_events:
            event[1] = float(actual_tokens)
