"""Quota persistence, reset boundaries, and shard splits (TEST_SPEC M3)."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from repro.llm.clock import FakeClock
from repro.llm.limiter import RateLimiter, parse_shard
from repro.llm.quota import QuotaStore
from repro.llm.registry import Route
from tests.llm_helpers import route


def ts(text: str) -> float:
    return dt.datetime.fromisoformat(text).replace(tzinfo=dt.UTC).timestamp()


def mk(id: str = "p/m", **kw: object) -> Route:
    return Route.model_validate(route(id, "p", "m", **kw))


def test_quota_persistence_and_resets(tmp_path: Path) -> None:
    """T3.20: survives restart; resets at UTC day, Pacific day (DST), month; provider overrides."""
    path = tmp_path / "quota.json"
    utc = mk(limits={"rpd": 2})
    clock = FakeClock(ts("2026-11-19T23:58:00"))
    q = QuotaStore(path, clock)
    q.record(utc, 10)
    q.record(utc, 10)
    assert not q.check(utc, 1).ok
    q2 = QuotaStore(path, clock)  # restart
    assert not q2.check(utc, 1).ok
    clock.t = ts("2026-11-20T00:01:00")
    assert q2.check(utc, 1).ok, "UTC day rolled over"

    pac = mk(limits={"rpd": 1}, reset="pacific_day")
    # 2026-11-01 is the DST change: midnight Pacific is 07:00 UTC before it, 08:00 UTC after.
    clock.t = ts("2026-11-01T07:30:00")  # Nov 1, 00:30 PDT
    q.record(pac, 1)
    assert not q.check(pac, 1).ok
    clock.t = ts("2026-11-02T07:30:00")  # Nov 1, 23:30 PST: still the same Pacific day
    assert not q.check(pac, 1).ok
    clock.t = ts("2026-11-02T08:30:00")  # Nov 2, 00:30 PST
    assert q.check(pac, 1).ok

    monthly = mk(id="p/mo", limits={"tokens_per_month": 1000})
    clock.t = ts("2026-11-30T12:00:00")
    q.record(monthly, 900)
    assert not q.check(monthly, 200).ok
    clock.t = ts("2026-12-01T00:00:01")
    assert q.check(monthly, 200).ok

    reported = mk(id="p/rep", limits={"rpd": 50})
    clock.t = ts("2026-11-25T10:00:00")
    q.observe_remaining(reported, 2)
    q.record(reported, 1)
    assert q.check(reported, 1).ok
    q.record(reported, 1)
    assert not q.check(reported, 1).ok, "provider said 2 left; local rpd of 50 is overridden"


def test_shard_split_never_exceeds_limit() -> None:
    """T3.21: with shard 2/6 each limit is one sixth; six shards together stay under it."""
    assert parse_shard("2/6") == (2, 6)
    for bad in ("0/6", "7/6", "x"):
        with pytest.raises(ValueError):
            parse_shard(bad)
    rt = mk(limits={"rpm": 60, "tpm": 60_000, "rpd": 600})
    clock = FakeClock(ts("2026-11-25T10:00:00"))
    shards = [RateLimiter(clock, shard_count=6) for _ in range(6)]
    assert shards[0].limit(rt, "rpm") == 10
    q = QuotaStore(None, clock, shard_count=6)
    for _ in range(100):
        q.record(rt, 1)
    assert not q.check(rt, 1).ok, "rpd of 600 is 100 per shard"

    sent: list[tuple[float, int]] = []
    for _step in range(1200):  # ten minutes at full speed, half-second steps
        for lim in shards:
            while lim.acquire(rt, 1500, max_wait=0) is not None:
                sent.append((clock.now(), 1500))
        clock.advance(0.5)
    assert sent
    for start, _ in sent:
        window = [tok for t, tok in sent if start <= t < start + 60]
        assert len(window) <= 60
        assert sum(window) <= 60_000
