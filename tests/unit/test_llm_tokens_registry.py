"""Token estimates and route configuration (TEST_SPEC M3)."""

from __future__ import annotations

import datetime as dt
import math
from typing import Any

import pytest

from repro.llm.registry import ConfigError, Registry
from repro.llm.tokens import MARGIN, TokenEstimator, tiktoken_counter
from tests.helpers import ROOT
from tests.llm_helpers import registry, route

MSGS = [{"role": "system", "content": "be brief"}, {"role": "user", "content": "x" * 400}]


def test_estimate_margin_and_correction() -> None:
    """T3.1: estimate is the base count times 1.15; correction converges and is clamped."""
    est = TokenEstimator(counter=len)
    base = est.base(MSGS)
    assert est.estimate(MSGS, "r") == math.ceil(base * MARGIN)
    for _ in range(60):
        est.observe("r", base, int(base * 1.3))
    assert est.correction("r") == pytest.approx(1.3, abs=0.01)
    assert est.estimate(MSGS, "r") == math.ceil(base * MARGIN * est.correction("r"))
    for _ in range(60):
        est.observe("big", base, base * 5)
        est.observe("small", base, max(1, base // 10))
    assert est.correction("big") == 1.5
    assert est.correction("small") == 0.8
    assert est.correction("unseen") == 1.0


@pytest.mark.skipif(tiktoken_counter() is None, reason="tiktoken encoding not downloadable here")
def test_tiktoken_backend_when_available() -> None:
    """T3.1: with tiktoken's o200k_base available, it is the counter used."""
    est = TokenEstimator()
    assert est.backend == "tiktoken"
    assert est.count("hello world") == 2


def test_fallback_backend_overestimates() -> None:
    """T3.1: without tiktoken the fallback counts one token per 3 characters."""
    est = TokenEstimator(counter=None)
    if est.backend == "chars/3":
        assert est.count("abcdefghi") == 3


def _raw(**overrides: Any) -> dict[str, Any]:
    raw: dict[str, Any] = {
        "version": 1,
        "providers": {"p": {"base_url": "http://x/v1", "api_key_env": "P_KEY"}},
        "routes": [route("p/m", "p", "m")],
        "logical_models": {"primary": ["p/m"]},
    }
    raw.update(overrides)
    return raw


def test_routes_validation() -> None:
    """T3.2: missing base URL, model or limits is an error; -latest refused; stale warns."""
    bad_provider = _raw(providers={"p": {"api_key_env": "P_KEY"}})
    no_model = _raw(routes=[{k: v for k, v in route("p/m", "p", "m").items() if k != "model"}])
    no_limits = _raw(routes=[{k: v for k, v in route("p/m", "p", "m").items() if k != "limits"}])
    mixed = _raw(routes=[route("p/a", "p", "a"), route("p/b", "p", "b")],
                 logical_models={"primary": ["p/a", "p/b"]})
    for raw in (bad_provider, no_model, no_limits, mixed):
        with pytest.raises(ConfigError):
            Registry.from_dict(raw)

    latest = Registry.from_dict(_raw(routes=[route("p/m", "p", "m", model="mistral-large-latest")]))
    with pytest.raises(ConfigError, match="floating alias"):
        latest.validate_for_eval("primary")

    old = (dt.date.today() - dt.timedelta(days=45)).isoformat()
    stale = Registry.from_dict(_raw(routes=[route("p/m", "p", "m", verified_on=old)]))
    assert any("re-check" in w for w in stale.validate_for_eval("primary"))
    fresh = Registry.from_dict(_raw())
    assert fresh.validate_for_eval("primary") == []


def test_shipped_routes_file_is_valid() -> None:
    """T3.2: config/routes.yaml loads, and the primary is usable in evals."""
    reg = Registry.from_yaml(ROOT / "config" / "routes.yaml")
    reg.validate_for_eval("primary", today=dt.date(2026, 11, 20))
    assert reg.enabled_routes("primary")[0].model == "mistralai/mistral-large-4-1t-a52b"


def test_free_and_private_code_rules() -> None:
    """T3.23: non-free routes fail eval validation; private_code refuses training routes."""
    paid = registry({"p": "http://x/v1"}, [route("p/m", "p", "m", free=False)],
                    {"primary": ["p/m"]})
    with pytest.raises(ConfigError, match="not marked free"):
        paid.validate_for_eval("primary")
    trains = registry({"p": "http://x/v1"}, [route("p/m", "p", "m", trains_on_data=True)],
                      {"primary": ["p/m"]})
    trains.validate_for_eval("primary")
    with pytest.raises(ConfigError, match="private code"):
        trains.validate_for_eval("primary", private_code=True)
