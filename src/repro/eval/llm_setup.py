"""Model access for an eval batch: recorded cassettes, or live routes via the router."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Literal

from repro.config import EvalConfig
from repro.eval.execute import LLMFactory, LLMHandle, eval_estimator
from repro.llm.budget import Ledger, RunBudget, TokenBudget
from repro.llm.cassette import CassetteLLM
from repro.llm.client import make_client_factory
from repro.llm.clock import RealClock
from repro.llm.limiter import RateLimiter
from repro.llm.quota import QuotaStore
from repro.llm.registry import Registry
from repro.llm.router import RoutedLLM, Router


def cassette_factory(cassette_dir: Path, ledger: Ledger,
                     match: Literal["request", "order"] = "request") -> LLMFactory:
    """One cassette file per instance: <dir>/<instance_id>.jsonl, replay only."""

    def make(instance_id: str) -> LLMHandle:
        return LLMHandle(CassetteLLM(cassette_dir / f"{instance_id}.jsonl", "replay",
                                     match=match), ledger=ledger)

    return make


def recording(factory: LLMFactory, cassette_dir: Path) -> LLMFactory:
    """Wrap a live factory so every response is also written to the instance's cassette.
    The file is restarted at the beginning of each unit, so a retried unit's cassette
    holds only its last attempt."""

    def make(instance_id: str) -> LLMHandle:
        handle = factory(instance_id)
        path = cassette_dir / f"{instance_id}.jsonl"
        path.unlink(missing_ok=True)
        handle.llm = CassetteLLM(path, "record", handle.llm)
        return handle

    return make


def chain_logicals(cfg: EvalConfig, registry: Registry) -> list[str]:
    """Logical models the config may use; under `chain`, those with no enabled route are
    skipped (e.g. a disabled OpenRouter route)."""
    if cfg.policy == "pinned":
        return [cfg.logical_model]
    usable = [m for m in registry.chain(cfg.logical_model) if registry.enabled_routes(m)]
    if not usable:
        raise ValueError(f"chain {cfg.logical_model!r} has no logical model with an enabled route")
    return usable


def live_factory(
    cfg: EvalConfig, registry: Registry, run_dir: Path, run_id: str, shard: int, shards: int
) -> tuple[LLMFactory, Callable[[], bool]]:
    """Router-backed model access shared across a batch's units.

    The limiter and quota store live for the whole batch (limits span units); each
    unit gets its own router so breakers, retries and route counts are per unit."""
    clock = RealClock()
    limiter = RateLimiter(clock, shard_count=shards)
    quota = QuotaStore(run_dir / f"quota-{shard}.json", clock, shard_count=shards)
    ledger = Ledger(run_dir / f"ledger-{shard}.jsonl")
    run_budget = RunBudget(ledger, run_id, cfg.run_tokens)
    clients = make_client_factory(registry)
    estimator = eval_estimator()
    logicals = chain_logicals(cfg, registry)
    for logical in logicals:
        registry.validate_for_eval(logical)

    def make(instance_id: str) -> LLMHandle:
        router = Router(registry, clients=clients, clock=clock, limiter=limiter, quota=quota,
                        estimator=estimator, ledger=ledger, run_budget=run_budget,
                        run_id=run_id, unit_id=instance_id)
        budget = TokenBudget(max_tokens=cfg.per_bug_tokens, max_requests=cfg.max_requests)
        return LLMHandle(RoutedLLM(router, cfg.logical_model, cfg.policy, budget),
                         rate_limit_wait_s=lambda: router.rate_limit_wait_s,
                         mixed=lambda: router.mixed)

    def quota_ok() -> bool:
        routes = [r for m in logicals for r in registry.enabled_routes(m)]
        return any(quota.check(r, 1).ok for r in routes)

    return make, quota_ok
