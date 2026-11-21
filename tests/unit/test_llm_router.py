"""Router behaviour against FakeProviders and a FakeClock (TEST_SPEC M3)."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from repro.llm.budget import Ledger, TokenBudget
from repro.llm.cassette import CassetteLLM
from repro.llm.client import make_client_factory
from repro.llm.clock import FakeClock
from repro.llm.errors import ErrorClass
from repro.llm.fake import FakeProvider, Reply, error, ok
from repro.llm.limiter import RateLimiter
from repro.llm.quota import QuotaStore
from repro.llm.router import Deferred, RoutedLLM, Router, RouterConfig, RouterError
from repro.llm.tokens import TokenEstimator
from tests.llm_helpers import MSGS, TEST_KEY, registry, route, router

PER_DAY = {"error": {"message": "Rate limit exceeded: free-models-per-day", "code": 429}}


def two_same_weights(a: FakeProvider, b: FakeProvider, c: FakeProvider | None = None):  # type: ignore[no-untyped-def]
    urls = {"a": a.base_url, "b": b.base_url}
    routes = [route("a/m", "a", "m"), route("b/m", "b", "m")]
    logical = {"primary": ["a/m", "b/m"]}
    chains = {}
    if c is not None:
        urls["c"] = c.base_url
        routes.append(route("c/other", "c", "other"))
        logical["other"] = ["c/other"]
        chains["dev"] = ["primary", "other"]
    return registry(urls, routes, logical, chains)


def test_retries_counted_once(tmp_path: Path) -> None:
    """T3.10: a call that succeeds after retries is counted once, with the final usage."""
    with FakeProvider() as fp:
        fp.push(error(503, "busy"), error(503, "busy"), ok(prompt_tokens=100, completion_tokens=20))
        reg = registry({"p": fp.base_url}, [route("p/m", "p", "m")], {"primary": ["p/m"]})
        r = router(reg, tmp_path)
        budget = TokenBudget()
        resp = r.complete("primary", MSGS, max_tokens=50, budget=budget)
    assert resp.retries == 2
    entries, _ = Ledger(tmp_path / "ledger.jsonl").read()
    assert len(entries) == 1 and entries[0].total_tokens == 120 and entries[0].retries == 2
    assert (budget.used_tokens, budget.used_requests) == (120, 1)


def test_no_key_in_artefacts(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """T3.14: no key string appears in logs, traces, the ledger, quota file, or cassette."""
    caplog.set_level(logging.DEBUG)
    with FakeProvider() as good, FakeProvider(default=error(401, {"error": "bad key"})) as bad:
        reg = registry({"good": good.base_url, "bad": bad.base_url},
                       [route("bad/m", "bad", "m"), route("good/m", "good", "m")],
                       {"primary": ["bad/m", "good/m"]})
        clock = FakeClock()
        env = {"GOOD_KEY": TEST_KEY, "BAD_KEY": TEST_KEY + "-bad"}
        r = Router(reg, clients=make_client_factory(reg, env), clock=clock,
                   limiter=RateLimiter(clock), quota=QuotaStore(tmp_path / "quota.json", clock),
                   estimator=TokenEstimator(counter=len), ledger=Ledger(tmp_path / "ledger.jsonl"))
        llm = CassetteLLM(tmp_path / "cassette.jsonl", "record", RoutedLLM(r, "primary"))
        llm.complete(MSGS, max_tokens=10)
        assert good.requests[0]["headers"]["authorization"] == f"Bearer {TEST_KEY}"
    blobs = [p.read_text() for p in tmp_path.iterdir() if p.is_file()]
    blobs.append(caplog.text)
    for blob in blobs:
        assert TEST_KEY not in blob
        assert "sk-test" not in blob


def test_per_minute_429_waits_same_route(tmp_path: Path) -> None:
    """T3.16: a per-minute 429 waits for Retry-After and retries the same route."""
    with FakeProvider() as a, FakeProvider() as b:
        a.push(error(429, {"error": "slow down"}, {"Retry-After": "7"}))
        clock = FakeClock()
        r = router(two_same_weights(a, b), tmp_path, clock=clock)
        resp = r.complete("primary", MSGS, max_tokens=10)
        assert resp.route_id == "a/m"
        assert clock.sleeps == [7.0]
        assert len(a.requests) == 2 and b.requests == []
        assert r.rate_limit_wait_s == 7.0


def test_transient_backoff_and_breaker(tmp_path: Path) -> None:
    """T3.17: 5xx backs off 1, 2, 4, 8, 16 s within jitter, then fails over; 3 trip a breaker."""
    with FakeProvider(default=error(503, "overloaded")) as a, FakeProvider() as b:
        clock = FakeClock()
        r = router(two_same_weights(a, b), tmp_path, clock=clock)
        for call in range(3):
            clock.sleeps.clear()
            resp = r.complete("primary", MSGS, max_tokens=10)
            assert resp.route_id == "b/m"
            assert len(clock.sleeps) == 5
            for slept, base in zip(clock.sleeps, (1, 2, 4, 8, 16), strict=True):
                assert 0.8 * base <= slept <= 1.2 * base
            assert len(a.requests) == 6 * (call + 1)
        r.complete("primary", MSGS, max_tokens=10)
        assert len(a.requests) == 18, "breaker open: route a not tried"
        clock.advance(601)
        r.complete("primary", MSGS, max_tokens=10)
        assert len(a.requests) > 18, "after the cooldown route a is tried again"


def test_timeout_is_transient(tmp_path: Path) -> None:
    """T3.17: a timeout is treated like a 5xx: retried, then failed over."""
    with FakeProvider(default=Reply(200, {}, delay_s=1.0)) as a, FakeProvider() as b:
        r = router(two_same_weights(a, b), tmp_path, timeout_s=0.2,
                   config=RouterConfig(backoff_s=(1.0,)))
        assert r.complete("primary", MSGS, max_tokens=10).route_id == "b/m"
        assert len(a.requests) == 2


def test_pinned_policy(tmp_path: Path) -> None:
    """T3.18: pinned uses same-weights routes, waits short resets, defers long ones, never
    calls another model."""
    with FakeProvider() as a, FakeProvider() as b, FakeProvider() as c:
        reg = two_same_weights(a, b, c)
        a.push(error(429, PER_DAY))
        clock = FakeClock()
        r = router(reg, tmp_path, clock=clock)
        assert r.complete("primary", MSGS, max_tokens=10).route_id == "b/m"

        r2 = router(reg, tmp_path / "2", clock=clock)
        for rt in reg.enabled_routes("primary"):
            r2.quota.mark_exhausted(rt, clock.now() + 300)
        clock.sleeps.clear()
        assert r2.complete("primary", MSGS, max_tokens=10).route_id == "a/m"
        assert clock.sleeps == [pytest.approx(300)]

        r3 = router(reg, tmp_path / "3", clock=clock)
        for rt in reg.enabled_routes("primary"):
            r3.quota.mark_exhausted(rt, clock.now() + 3600)
        with pytest.raises(Deferred) as exc:
            r3.complete("primary", MSGS, max_tokens=10)
        assert exc.value.reset_at == pytest.approx(clock.now() + 3600)
        assert c.requests == [], "pinned never calls a different model"
        assert not r.mixed and not r2.mixed


def test_chain_policy_tags_mixed(tmp_path: Path) -> None:
    """T3.19: chain moves to the next model on exhaustion, tags the run mixed, records routes."""
    with FakeProvider() as a, FakeProvider(default=error(429, PER_DAY)) as b, \
            FakeProvider() as c:
        reg = two_same_weights(a, b, c)
        a.push(ok(), error(429, PER_DAY))
        r = router(reg, tmp_path)
        first = r.complete("dev", MSGS, max_tokens=10, policy="chain")
        assert not r.mixed
        second = r.complete("dev", MSGS, max_tokens=10, policy="chain")
    assert (first.route_id, second.route_id) == ("a/m", "c/other")
    assert r.mixed
    assert [c_.route_id for c_ in r.calls] == ["a/m", "c/other"]
    entries, _ = Ledger(tmp_path / "ledger.jsonl").read()
    assert [e.route_id for e in entries] == ["a/m", "c/other"]


def test_context_length_and_auth(tmp_path: Path) -> None:
    """T3.22: context length trims to 75% and retries once; 401 disables the route."""
    ctx = {"object": "error", "message": "This model's maximum context length is 1000 tokens"}
    long_msgs = [{"role": "system", "content": "rules"},
                 {"role": "user", "content": "old " * 2000},
                 {"role": "assistant", "content": "older reply " * 500},
                 {"role": "user", "content": "latest question " * 50}]
    with FakeProvider() as fp:
        fp.push(error(400, ctx), ok())
        reg = registry({"p": fp.base_url}, [route("p/m", "p", "m", context_window=1000)],
                       {"primary": ["p/m"]})
        r = router(reg, tmp_path)
        r.complete("primary", long_msgs, max_tokens=10)
        first, retried = (q["json"]["messages"] for q in fp.requests)
        assert r.estimator.base(retried) <= 750 < r.estimator.base(first)
        assert retried[-1]["content"].startswith("latest question")

        fp.push(error(400, ctx), error(400, ctx))
        with pytest.raises(RouterError) as exc:
            r.complete("primary", long_msgs, max_tokens=10)
        assert exc.value.cls is ErrorClass.CONTEXT_LENGTH

    with FakeProvider(default=error(401, {"error": "no"})) as a, FakeProvider() as b:
        r = router(two_same_weights(a, b), tmp_path / "auth")
        assert r.complete("primary", MSGS, max_tokens=10).route_id == "b/m"
        r.complete("primary", MSGS, max_tokens=10)
        assert len(a.requests) == 1, "route disabled for the rest of the run"
        assert "a/m" in r.disabled


def empty_reply(finish: str = "length", completion: int = 8000) -> Reply:
    r = ok(text="", prompt_tokens=900, completion_tokens=completion, reasoning_tokens=completion)
    r.body["choices"][0]["finish_reason"] = finish
    return r


def test_empty_output_is_returned_and_counted(tmp_path: Path) -> None:
    """T3.15: a 200 with no content is a model problem: returned empty, tokens counted."""
    with FakeProvider() as fp:
        fp.push(empty_reply())
        reg = registry({"p": fp.base_url}, [route("p/m", "p", "m")], {"primary": ["p/m"]})
        r = router(reg, tmp_path)
        budget = TokenBudget()
        resp = r.complete("primary", MSGS, max_tokens=8000, budget=budget)
    assert resp.text == "" and resp.finish_reason == "length" and resp.route_id == "p/m"
    entries, _ = Ledger(tmp_path / "ledger.jsonl").read()
    assert len(entries) == 1 and entries[0].total_tokens == 8900
    assert budget.used_tokens == 8900
