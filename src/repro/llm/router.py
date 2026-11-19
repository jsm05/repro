"""The router: picks a route for every call, waits out short limits, fails over on
long ones, and records exactly what happened.

Policies:
  pinned  Only routes of one logical model (same weights). Never another model.
          Used by every headline eval config, because the eval is a paired
          comparison on one model.
  chain   An ordered list of logical models. Moves to the next on exhaustion and
          tags the run `mixed` once more than one model has served it.

Error handling (DESIGN 5.5): per-minute 429 -> wait and retry the same route;
daily/monthly quota -> mark the route exhausted until reset, fail over; 5xx or
timeout -> back off 1, 2, 4, 8, 16 s, then fail over, and three such failures in a
row open a 10-minute circuit breaker; context length -> trim to 75% of the window
and retry once; 401 -> disable the route for the run; other 400s -> raise.
If every route is exhausted: wait when the reset is under 10 minutes, otherwise
raise Deferred so the eval runner can retry the unit in a later batch.
"""

from __future__ import annotations

import logging
import math
import random
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Literal, Protocol

from repro.llm.budget import BudgetExceeded, Ledger, LedgerEntry, RunBudget, TokenBudget
from repro.llm.clock import Clock
from repro.llm.errors import ErrorClass, LLMError
from repro.llm.limiter import RateLimiter
from repro.llm.quota import QuotaStore
from repro.llm.registry import Registry, Route
from repro.llm.tokens import TokenEstimator
from repro.llm.types import LLMResponse, Message

log = logging.getLogger(__name__)
Policy = Literal["pinned", "chain"]


class ChatClient(Protocol):
    def complete(
        self,
        messages: Sequence[Message],
        *,
        max_tokens: int,
        temperature: float = 0.0,
        stop: Sequence[str] | None = None,
    ) -> LLMResponse: ...


class Deferred(Exception):
    """Every usable route is out of quota for longer than the wait threshold."""

    def __init__(self, reset_at: float, message: str = ""):
        super().__init__(message or f"all routes exhausted until {reset_at:.0f}")
        self.reset_at = reset_at


class RouterError(Exception):
    def __init__(self, cls: ErrorClass | None, message: str):
        super().__init__(message)
        self.cls = cls


@dataclass(frozen=True)
class RouterConfig:
    max_short_wait_s: float = 60.0
    max_rate_limit_retries: int = 5
    backoff_s: tuple[float, ...] = (1.0, 2.0, 4.0, 8.0, 16.0)
    jitter: float = 0.2
    breaker_threshold: int = 3
    breaker_cooldown_s: float = 600.0
    defer_threshold_s: float = 600.0
    context_trim: float = 0.75


@dataclass
class _Breaker:
    failures: int = 0
    open_until: float = 0.0


@dataclass
class CallRecord:
    route_id: str
    weights: str
    tokens: int
    retries: int


@dataclass
class _Outcome:
    response: LLMResponse | None = None
    reset_at: float | None = None  # set when the route is out for a while
    error: ErrorClass | None = None


def trim_messages(
    messages: Sequence[Message], target_tokens: int, estimator: TokenEstimator
) -> list[dict[str, str]]:
    """Drop the oldest middle messages, then shorten the longest, until under target."""
    msgs = [dict(m) for m in messages]
    while len(msgs) > 2 and estimator.base(msgs) > target_tokens:
        msgs.pop(1)
    while estimator.base(msgs) > target_tokens:
        longest = max(range(len(msgs)), key=lambda i: len(msgs[i]["content"]))
        content = msgs[longest]["content"]
        if len(content) < 200:
            break
        keep = int(len(content) * 0.8)
        half = keep // 2
        msgs[longest]["content"] = content[:half] + "\n...[trimmed]...\n" + content[-half:]
    return msgs


class Router:
    def __init__(
        self,
        registry: Registry,
        *,
        clients: Callable[[Route], ChatClient],
        clock: Clock,
        limiter: RateLimiter,
        quota: QuotaStore,
        estimator: TokenEstimator,
        ledger: Ledger | None = None,
        run_budget: RunBudget | None = None,
        config: RouterConfig | None = None,
        rng: random.Random | None = None,
        run_id: str = "",
        unit_id: str = "",
    ):
        self.registry = registry
        self.clients = clients
        self.clock = clock
        self.limiter = limiter
        self.quota = quota
        self.estimator = estimator
        self.ledger = ledger
        self.run_budget = run_budget
        self.config = config or RouterConfig()
        self.rng = rng or random.Random(0)
        self.run_id = run_id
        self.unit_id = unit_id
        self.disabled: set[str] = set()
        self.breakers: dict[str, _Breaker] = {}
        self.calls: list[CallRecord] = []
        self.rate_limit_wait_s = 0.0
        self.model_wait_s = 0.0

    # -- bookkeeping ------------------------------------------------------------------

    @property
    def weights_served(self) -> set[str]:
        return {c.weights for c in self.calls}

    @property
    def mixed(self) -> bool:
        """More than one model has answered calls in this run."""
        return len(self.weights_served) > 1

    def _wait(self, seconds: float) -> None:
        self.clock.sleep(seconds)
        self.rate_limit_wait_s += seconds

    def candidates(self, target: str, policy: Policy) -> list[Route]:
        if policy == "pinned":
            return self.registry.enabled_routes(target)
        routes: list[Route] = []
        for logical in self.registry.chain(target):
            routes.extend(self.registry.enabled_routes(logical))
        return routes

    def _breaker_open(self, route: Route) -> bool:
        b = self.breakers.get(route.id)
        return b is not None and b.open_until > self.clock.now()

    def _breaker_fail(self, route: Route) -> None:
        b = self.breakers.setdefault(route.id, _Breaker())
        b.failures += 1
        if b.failures >= self.config.breaker_threshold:
            b.open_until = self.clock.now() + self.config.breaker_cooldown_s
            log.warning("circuit breaker open for %s until %.0f", route.id, b.open_until)

    def _breaker_ok(self, route: Route) -> None:
        self.breakers[route.id] = _Breaker()

    # -- the call -------------------------------------------------------------------

    def complete(
        self,
        target: str,
        messages: Sequence[Message],
        *,
        policy: Policy = "pinned",
        max_tokens: int,
        temperature: float = 0.0,
        stop: Sequence[str] | None = None,
        budget: TokenBudget | None = None,
    ) -> LLMResponse:
        routes = self.candidates(target, policy)
        if not routes:
            raise RouterError(None, f"no enabled routes for {target!r}")
        worst = self.estimator.estimate(messages, routes[0].id) + max_tokens
        # Ceilings are checked before any call is made.
        if budget is not None:
            budget.check_call(worst)
        if self.run_budget is not None:
            self.run_budget.check(worst)

        last_error: ErrorClass | None = None
        for _round in range(2):
            resets: list[float] = []
            for route in routes:
                if route.id in self.disabled or self._breaker_open(route):
                    continue
                route_worst = self.estimator.estimate(messages, route.id) + min(
                    max_tokens, route.max_output_tokens
                )
                status = self.quota.check(route, route_worst)
                if not status.ok:
                    if status.reset_at is not None:
                        resets.append(status.reset_at)
                    continue
                reservation = self.limiter.acquire(
                    route, route_worst, self.config.max_short_wait_s
                )
                if reservation is None:
                    wait = self.limiter.wait_time(route, route_worst)
                    if math.isfinite(wait):
                        resets.append(self.clock.now() + wait)
                    continue
                outcome = self._call_route(route, messages, max_tokens, temperature, stop)
                if outcome.response is not None:
                    resp = outcome.response
                    self.limiter.settle(reservation, resp.total_tokens)
                    self._record(route, messages, resp, budget)
                    return resp
                self.limiter.settle(reservation, 0)
                last_error = outcome.error or last_error
                if outcome.reset_at is not None:
                    resets.append(outcome.reset_at)
            if not resets:
                break
            soonest = min(resets)
            wait = soonest - self.clock.now()
            if wait > self.config.defer_threshold_s or _round == 1:
                raise Deferred(soonest)
            self._wait(max(wait, 0.0))
        raise RouterError(last_error, f"every route for {target!r} failed (last: {last_error})")

    def _call_route(
        self,
        route: Route,
        messages: Sequence[Message],
        max_tokens: int,
        temperature: float,
        stop: Sequence[str] | None,
    ) -> _Outcome:
        cfg = self.config
        try:
            client = self.clients(route)
        except LLMError as exc:
            if exc.cls is ErrorClass.AUTH:
                log.error("route %s disabled: %s", route.id, exc)
                self.disabled.add(route.id)
                return _Outcome(error=ErrorClass.AUTH)
            raise
        msgs: Sequence[Message] = messages
        trimmed = False
        retries = transient = rate_limited = 0
        while True:
            try:
                resp = client.complete(
                    msgs, max_tokens=min(max_tokens, route.max_output_tokens),
                    temperature=temperature, stop=stop,
                )
            except LLMError as exc:
                cls = exc.cls
                if cls is ErrorClass.RATE_LIMIT:
                    wait = exc.retry_after
                    if wait is None:
                        wait = self.limiter.wait_time(route, 1) or 1.0
                    if rate_limited >= cfg.max_rate_limit_retries or wait > cfg.max_short_wait_s:
                        return _Outcome(reset_at=self.clock.now() + wait, error=cls)
                    self._wait(wait)
                    rate_limited += 1
                    retries += 1
                    continue
                if cls is ErrorClass.QUOTA_EXHAUSTED:
                    until = self.clock.now() + exc.retry_after if exc.retry_after else None
                    reset = self.quota.mark_exhausted(route, until)
                    return _Outcome(reset_at=reset, error=cls)
                if cls is ErrorClass.TRANSIENT:
                    if transient < len(cfg.backoff_s):
                        base = cfg.backoff_s[transient]
                        delay = base * self.rng.uniform(1 - cfg.jitter, 1 + cfg.jitter)
                        self._wait(delay)
                        transient += 1
                        retries += 1
                        continue
                    self._breaker_fail(route)
                    return _Outcome(error=cls)
                if cls is ErrorClass.CONTEXT_LENGTH and not trimmed:
                    target = int(route.context_window * cfg.context_trim)
                    msgs = trim_messages(msgs, target, self.estimator)
                    trimmed = True
                    retries += 1
                    continue
                if cls is ErrorClass.AUTH:
                    log.error("route %s disabled for this run: authentication failed", route.id)
                    self.disabled.add(route.id)
                    return _Outcome(error=cls)
                raise RouterError(cls, f"{route.id}: {exc}") from None
            self.model_wait_s += resp.latency_s
            self._breaker_ok(route)
            return _Outcome(response=resp.model_copy(update={"route_id": route.id,
                                                              "retries": retries}))

    def _record(
        self,
        route: Route,
        messages: Sequence[Message],
        resp: LLMResponse,
        budget: TokenBudget | None,
    ) -> None:
        total = resp.total_tokens
        self.quota.record(route, total)
        if budget is not None:
            budget.record_call(total)
        if self.run_budget is not None:
            self.run_budget.record(total)
        base = self.estimator.base(messages)
        estimated = self.estimator.estimate(messages, route.id)
        self.estimator.observe(route.id, base, resp.prompt_tokens)
        self.calls.append(CallRecord(route.id, route.weights, total, resp.retries))
        if self.ledger is not None:
            self.ledger.append(
                LedgerEntry(
                    ts=self.clock.now(),
                    run_id=self.run_id,
                    unit_id=self.unit_id,
                    route_id=route.id,
                    model=route.model,
                    prompt_tokens=resp.prompt_tokens,
                    completion_tokens=resp.completion_tokens,
                    reasoning_tokens=resp.reasoning_tokens,
                    estimated_prompt_tokens=estimated,
                    latency_s=resp.latency_s,
                    retries=resp.retries,
                )
            )


@dataclass
class RoutedLLM:
    """Adapts the router to the plain LLM interface the agent and baselines use."""

    router: Router
    target: str
    policy: Policy = "pinned"
    budget: TokenBudget | None = field(default=None)

    def complete(
        self,
        messages: Sequence[Message],
        *,
        max_tokens: int,
        temperature: float = 0.0,
        stop: Sequence[str] | None = None,
    ) -> LLMResponse:
        return self.router.complete(
            self.target, messages, policy=self.policy, max_tokens=max_tokens,
            temperature=temperature, stop=stop, budget=self.budget,
        )


__all__ = [
    "BudgetExceeded",
    "Deferred",
    "RoutedLLM",
    "Router",
    "RouterConfig",
    "RouterError",
    "trim_messages",
]
