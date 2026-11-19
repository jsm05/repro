"""Builders for router tests: registries whose routes point at FakeProviders."""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

from repro.llm.budget import Ledger
from repro.llm.client import OpenAICompatClient
from repro.llm.clock import FakeClock
from repro.llm.limiter import RateLimiter
from repro.llm.quota import QuotaStore
from repro.llm.registry import Registry, Route
from repro.llm.router import Router, RouterConfig
from repro.llm.tokens import TokenEstimator

TEST_KEY = "sk-test-SECRET-KEY-0123456789"
MSGS = [{"role": "user", "content": "hello there"}]


def route(id: str, provider: str, weights: str, **kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": id,
        "provider": provider,
        "model": f"{weights}-v1",
        "weights": weights,
        "context_window": 32_000,
        "max_output_tokens": 4096,
        "limits": {},
        "free": True,
        "trains_on_data": False,
        "verified_on": dt.date.today().isoformat(),
    }
    base.update(kw)
    return base


def registry(
    base_urls: dict[str, str],
    routes: list[dict[str, Any]],
    logical: dict[str, list[str]],
    chains: dict[str, list[str]] | None = None,
) -> Registry:
    return Registry.from_dict({
        "version": 1,
        "providers": {name: {"base_url": url, "api_key_env": f"{name.upper()}_KEY"}
                      for name, url in base_urls.items()},
        "routes": routes,
        "logical_models": logical,
        "chains": chains or {},
    })


def router(
    reg: Registry,
    tmp_path: Path,
    *,
    clock: FakeClock | None = None,
    config: RouterConfig | None = None,
    timeout_s: float = 30.0,
    **kw: Any,
) -> Router:
    clock = clock or FakeClock()

    def clients(r: Route) -> OpenAICompatClient:
        return OpenAICompatClient(r, reg.provider(r).base_url, TEST_KEY, timeout_s=timeout_s)

    return Router(
        reg,
        clients=clients,
        clock=clock,
        limiter=RateLimiter(clock),
        quota=QuotaStore(tmp_path / "quota.json", clock),
        estimator=TokenEstimator(counter=lambda s: max(1, len(s) // 4)),
        ledger=Ledger(tmp_path / "ledger.jsonl"),
        config=config,
        **kw,
    )
