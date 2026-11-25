"""Metrics on synthetic rows with hand-computed answers (EVAL section 4)."""

from __future__ import annotations

from typing import Any

import pytest

from repro.config import EvalConfig
from repro.datasets.samples import allocate, make_samples, repo_of
from repro.eval.metrics import (
    ceiling_curve,
    flaky_rate,
    gate_stats,
    mcnemar_exact,
    mutation_summary,
    paired,
    route_mix,
    success,
    token_stats,
    wilson,
)
from repro.eval.rows import ResultRow
from repro.llm.budget import LedgerEntry, list_price_usd
from repro.models import InstanceOutcome as O


def row(i: int, out: O = O.NO_TEST, gated: O | None = None, **kw: Any) -> ResultRow:
    base: dict[str, Any] = dict(
        run_id="r", instance_id=f"i{i:02d}", config_id="c", config_hash="h", kind="B0",
        logical_model="m", outcome=out, outcome_gated=gated if gated is not None else out,
    )
    base.update(kw)
    return ResultRow(**base)


def test_wilson_interval() -> None:
    """T4.1: Wilson interval: 25 of 50 gives [0.366, 0.634]; also 0 of 10 and 10 of 10."""
    lo, hi = wilson(25, 50)  # type: ignore[misc]
    assert (round(lo, 3), round(hi, 3)) == (0.366, 0.634)
    lo, hi = wilson(0, 10)  # type: ignore[misc]
    assert lo == pytest.approx(0.0, abs=1e-12) and round(hi, 3) == 0.278
    lo, hi = wilson(10, 10)  # type: ignore[misc]
    assert round(lo, 3) == 0.722 and hi == pytest.approx(1.0)
    assert wilson(0, 0) is None


def test_mcnemar_exact() -> None:
    """T4.2: exact McNemar with discordant counts 12 and 3 gives p = 0.0352."""
    assert round(mcnemar_exact(12, 3), 4) == 0.0352
    assert round(mcnemar_exact(3, 12), 4) == 0.0352
    assert round(mcnemar_exact(10, 2), 3) == 0.039  # EVAL 4: smallest significant at N=50
    assert round(mcnemar_exact(9, 2), 3) == 0.065
    assert mcnemar_exact(0, 0) == 1.0


def test_paired_counts() -> None:
    """T4.2: paired comparison counts discordant pairs on the common instances."""
    a = [row(1, O.REPRODUCED), row(2, O.REPRODUCED), row(3, O.NO_TEST), row(4, O.REPRODUCED)]
    b = [row(1, O.REPRODUCED), row(2, O.WRONG_TEST), row(3, O.REPRODUCED), row(5, O.REPRODUCED)]
    p = paired(a, b)
    assert (p.a_only, p.b_only, p.both, p.neither) == (1, 1, 1, 0)


def test_gate_stats_known_counts() -> None:
    """T4.3: precision, recall, abstain rate on known counts; zero claimed gives n/a."""
    rows = (
        [row(i, O.REPRODUCED, O.REPRODUCED, gate_rejections={"G2": 1}) for i in range(6)]
        + [row(10 + i, O.WRONG_TEST, O.WRONG_TEST) for i in range(2)]
        + [row(20 + i, O.REPRODUCED, O.NO_TEST, gate_rejections={"G4": 1}) for i in range(2)]
        + [row(30 + i, O.WRONG_TEST, O.NO_TEST) for i in range(5)]
        + [row(40 + i, O.NO_TEST, O.NO_TEST) for i in range(4)]
        + [row(50, O.INFRA_ERROR, O.INFRA_ERROR)]
    )
    g = gate_stats(rows)
    assert len(rows) == 20
    assert g.claimed == 8
    assert g.precision == 6 / 8
    assert g.recall == 6 / 20
    assert g.abstain_rate == 11 / 20
    assert g.gate_recall_cost == (8 - 6) / 20
    assert g.precision_no_gate == 8 / 15
    assert g.rejections["G2"] == 6 and g.rejections["G4"] == 2 and g.rejections["G0"] == 0
    assert g.precision_ci is not None

    none_claimed = gate_stats([row(1, O.WRONG_TEST, O.NO_TEST), row(2, O.NO_TEST)])
    assert none_claimed.precision is None and none_claimed.precision_ci is None
    empty = gate_stats([])
    assert empty.recall is None and empty.abstain_rate is None


def test_success_counts_every_row() -> None:
    """T4.3: N counts NO_TEST and INFRA_ERROR; nothing is dropped."""
    rows = [row(1, O.REPRODUCED), row(2, O.INFRA_ERROR), row(3, O.NO_TEST), row(4, O.WRONG_TEST)]
    assert success(rows) == (1, 4)


def test_token_stats_and_list_price() -> None:
    """T4.4: mean, median, p90, max tokens; per reproduced; list price; zero reproduced n/a."""
    toks = [100, 200, 300, 400, 500, 600, 700, 800, 900, 1000]
    rows = [row(i, O.REPRODUCED if i < 2 else O.WRONG_TEST, tokens_in=t - 10, tokens_out=10,
                requests=2, list_price_usd=0.01) for i, t in enumerate(toks)]
    s = token_stats(rows)
    assert s.mean == 550 and s.median == 550 and s.p90 == 900 and s.max == 1000
    assert s.total == 5500 and s.per_reproduced == 2750
    assert s.requests_mean == 2
    assert s.list_price_total_usd == pytest.approx(0.10)
    assert s.list_price_per_bug_usd == pytest.approx(0.01)
    assert s.list_price_per_reproduced_usd == pytest.approx(0.05)

    none_rep = token_stats([row(1, O.WRONG_TEST, tokens_in=5)])
    assert none_rep.per_reproduced is None and none_rep.list_price_per_reproduced_usd is None
    unknown_price = token_stats([row(1, O.REPRODUCED, list_price_usd=None)])
    assert unknown_price.list_price_total_usd is None

    entries = [LedgerEntry(ts=0, route_id="a", model="m1", prompt_tokens=1_000_000,
                           completion_tokens=500_000)]
    assert list_price_usd(entries, {"m1": (2.0, 6.0)}) == pytest.approx(5.0)
    assert list_price_usd(entries, {"m1": None}) is None


def test_ceiling_curve_does_not_peek_ahead() -> None:
    """T4.5: at each ceiling, only candidates that existed before the crossing count."""
    rows = [
        row(1, O.REPRODUCED, candidate_tokens_at=40_000, tokens_in=300_000),
        row(2, O.REPRODUCED, candidate_tokens_at=150_000, tokens_in=150_000),
        row(3, O.REPRODUCED, candidate_tokens_at=450_000, tokens_in=500_000),
        row(4, O.WRONG_TEST, candidate_tokens_at=10_000),
        row(5, O.NO_TEST),
    ]
    curve = ceiling_curve(rows, (50_000, 100_000, 200_000, 400_000, 600_000))
    assert curve == [(50_000, 1, 5), (100_000, 1, 5), (200_000, 2, 5), (400_000, 2, 5),
                     (600_000, 3, 5)]


def test_route_mix_and_stubs() -> None:
    """T4.4: route shares, retries, deferrals; flaky and mutation aggregations."""
    rows = [row(1, route_calls={"a": 3}, retries=1), row(2, route_calls={"a": 1, "b": 4},
                                                          deferrals=2, mixed=True)]
    m = route_mix(rows)
    assert m.calls == {"a": 4, "b": 4} and m.share == {"a": 0.5, "b": 0.5}
    assert (m.retries, m.deferrals, m.mixed_rows) == (1, 2, 1)
    assert flaky_rate([True, False, False, False]) == (1, 4, 0.25)
    assert flaky_rate([]) == (0, 0, None)
    assert mutation_summary([0.5, 1.0, 0.0]) == (0.5, 0.5)
    assert mutation_summary([]) == (None, None)


def test_config_hash_changes_with_every_field() -> None:
    """T4.10: the config hash changes when any field changes."""
    base = EvalConfig(config_id="x", kind="B0")
    changed = {
        "config_id": "y", "kind": "B1", "dataset": "swtbench", "instances": ["a"],
        "logical_model": "other", "policy": "chain", "llm": "cassette", "cassette_dir": "d",
        "k": 3, "temperature": 0.1, "max_output_tokens": 10, "prompt_cap": 10,
        "per_bug_tokens": 10, "max_requests": 1, "run_tokens": 10, "gate_timeout_s": 1.0,
        "max_deferrals": 9, "notes": "n", "cassette_match": "order", "max_steps": 3,
        "max_gate_checks": 2, "active_minutes": 1.0, "hard_cap_minutes": 2.0,
        "max_tool_errors": 1, "max_repeats": 1, "prompt_version": "v2",
        "instances_file": "f.txt", "judge": True, "split": "dev",
    }
    assert set(changed) == set(EvalConfig.model_fields)
    hashes = {base.hash}
    for field, value in changed.items():
        h = base.model_copy(update={field: value}).hash
        assert h != base.hash, field
        hashes.add(h)
    assert len(hashes) == len(changed) + 1
    assert EvalConfig.model_validate_json(base.model_dump_json()).hash == base.hash


def synthetic_ids() -> list[str]:
    sizes = {"django__django": 120, "sympy__sympy": 70, "astropy__astropy": 20,
             "pytest-dev__pytest": 15, "psf__requests": 8}
    return [f"{repo}-{1000 + i}" for repo, n in sizes.items() for i in range(n)]


def test_samples_seeded_disjoint_stratified() -> None:
    """T4.13: same seed gives the same lists; dev and test disjoint; proportions hold."""
    ids = synthetic_ids()
    a = make_samples(ids, seed=7)
    b = make_samples(list(reversed(ids)), seed=7)
    assert a == b
    assert len(a.test) == 50 and len(a.dev) == 15
    assert not set(a.dev) & set(a.test)
    assert make_samples(ids, seed=8).test != a.test
    total = len(ids)
    for repo in {repo_of(i) for i in ids}:
        share = sum(1 for i in ids if repo_of(i) == repo) / total
        got = sum(1 for i in a.test if repo_of(i) == repo)
        assert abs(got - 50 * share) < 1.0, repo
    assert repo_of("pytest-dev__pytest-5103") == "pytest-dev__pytest"
    assert allocate({"a": 1, "b": 1, "c": 1}, 2) == {"a": 1, "b": 1, "c": 0}
    with pytest.raises(ValueError):
        allocate({"a": 1}, 2)
