"""Token ceilings and the ledger (TEST_SPEC M3)."""

from __future__ import annotations

import multiprocessing
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from repro.llm.budget import BudgetExceeded, Ledger, LedgerEntry, RunBudget, TokenBudget
from repro.llm.fake import FakeProvider
from tests.llm_helpers import MSGS, registry, route, router

calls = st.lists(
    st.tuples(st.integers(0, 50_000), st.integers(1, 8_000), st.floats(0.0, 1.0)),
    max_size=80,
)


@settings(max_examples=300, deadline=None)
@given(calls, st.integers(1_000, 200_000), st.integers(1, 40))
def test_ceiling_property(seq: list[tuple[int, int, float]], ceiling: int, max_req: int) -> None:
    """T3.3: recorded tokens never exceed the per-bug ceiling; refused calls record nothing."""
    budget = TokenBudget(max_tokens=ceiling, max_requests=max_req)
    accepted = 0
    for est_prompt, max_tokens, used_fraction in seq:
        worst = est_prompt + max_tokens
        before = (budget.used_tokens, budget.used_requests)
        try:
            budget.check_call(worst)
        except BudgetExceeded:
            assert (budget.used_tokens, budget.used_requests) == before
            continue
        budget.record_call(int(worst * used_fraction))  # a provider never bills past worst case
        accepted += 1
        assert budget.used_tokens <= ceiling
        assert budget.used_requests <= max_req
    assert budget.used_requests == accepted


def test_router_refuses_before_calling(tmp_path: Path) -> None:
    """T3.3: a call that could cross the ceiling is refused before any request is sent."""
    with FakeProvider() as fp:
        reg = registry({"p": fp.base_url}, [route("p/m", "p", "m")], {"primary": ["p/m"]})
        r = router(reg, tmp_path)
        budget = TokenBudget(max_tokens=1_000)
        with pytest.raises(BudgetExceeded) as exc:
            r.complete("primary", MSGS, max_tokens=2_000, budget=budget)
        assert exc.value.kind == "tokens"
        assert fp.requests == []
        assert budget.used_tokens == 0


def _append_many(path: str, worker: int) -> None:
    ledger = Ledger(path)
    for i in range(200):
        ledger.append(LedgerEntry(ts=float(i), run_id="r", unit_id=f"w{worker}",
                                  route_id="p/m", model="m", prompt_tokens=7,
                                  completion_tokens=3))


def test_ledger_parallel_appends(tmp_path: Path) -> None:
    """T3.4: 4 processes each append 200 entries: 800 valid lines, correct token sum."""
    path = str(tmp_path / "ledger.jsonl")
    ctx = multiprocessing.get_context("fork")
    procs = [ctx.Process(target=_append_many, args=(path, w)) for w in range(4)]
    for p in procs:
        p.start()
    for p in procs:
        p.join(30)
        assert p.exitcode == 0
    entries, bad = Ledger(path).read()
    assert bad == 0
    assert len(entries) == 800
    assert sum(e.total_tokens for e in entries) == 800 * 10


def test_run_level_ceiling(tmp_path: Path) -> None:
    """T3.5: near the run ceiling, a call that fits the per-bug budget is still refused."""
    ledger = Ledger(tmp_path / "ledger.jsonl")
    ledger.append(LedgerEntry(ts=0, run_id="run1", route_id="p/m", model="m",
                              prompt_tokens=9_900, completion_tokens=0))
    with FakeProvider() as fp:
        reg = registry({"p": fp.base_url}, [route("p/m", "p", "m")], {"primary": ["p/m"]})
        r = router(reg, tmp_path, run_budget=RunBudget(ledger, "run1", max_tokens=10_000))
        with pytest.raises(BudgetExceeded) as exc:
            r.complete("primary", MSGS, max_tokens=500, budget=TokenBudget())
        assert exc.value.kind == "run_tokens"
        assert "run ceiling" in str(exc.value)
        assert fp.requests == []


def test_truncated_ledger_line(tmp_path: Path) -> None:
    """T3.6: a truncated final line (crash mid-write) is skipped on reload and reported."""
    ledger = Ledger(tmp_path / "ledger.jsonl")
    for i in range(3):
        ledger.append(LedgerEntry(ts=i, route_id="p/m", model="m", prompt_tokens=1,
                                  completion_tokens=1))
    with ledger.path.open("a") as f:
        f.write('{"ts": 9, "route_id": "p/m", "mod')
    entries, bad = ledger.read()
    assert len(entries) == 3
    assert bad == 1
