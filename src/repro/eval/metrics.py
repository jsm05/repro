"""Metrics from result rows only (EVAL_AND_METRICS section 4).

Every function takes rows (or plain numbers) and returns plain values; None means
"not defined" (shown as n/a), never 0.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Literal

from repro.eval.rows import GATE_CHECKS, ResultRow
from repro.models import InstanceOutcome

Mode = Literal["always", "gated"]
Z95 = 1.959963984540054
CEILINGS = (50_000, 100_000, 200_000, 400_000, 600_000)
R = InstanceOutcome.REPRODUCED
EMITTED = {InstanceOutcome.REPRODUCED, InstanceOutcome.WRONG_TEST}


def outcome(row: ResultRow, mode: Mode) -> InstanceOutcome:
    return row.outcome if mode == "always" else row.outcome_gated


# -- H1: success rate -------------------------------------------------------------------


def wilson(k: int, n: int, z: float = Z95) -> tuple[float, float] | None:
    """Wilson score interval for k successes out of n."""
    if n == 0:
        return None
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def success(rows: Sequence[ResultRow], mode: Mode = "always") -> tuple[int, int]:
    """(REPRODUCED, N). N counts every row: NO_TEST and INFRA_ERROR are failures."""
    return sum(1 for r in rows if outcome(r, mode) is R), len(rows)


def mcnemar_exact(b: int, c: int) -> float:
    """Two-sided exact McNemar p-value from the discordant counts b and c."""
    n = b + c
    if n == 0:
        return 1.0
    tail = sum(math.comb(n, i) for i in range(min(b, c) + 1)) / (1 << n)
    return min(1.0, 2 * tail)


@dataclass(frozen=True)
class Paired:
    a_only: int
    b_only: int
    both: int
    neither: int
    p_value: float


def paired(a: Sequence[ResultRow], b: Sequence[ResultRow], mode: Mode = "always") -> Paired:
    """Paired comparison on the instances both configs ran."""
    a_by = {r.instance_id: outcome(r, mode) is R for r in a}
    b_by = {r.instance_id: outcome(r, mode) is R for r in b}
    common = sorted(set(a_by) & set(b_by))
    a_only = sum(1 for i in common if a_by[i] and not b_by[i])
    b_only = sum(1 for i in common if b_by[i] and not a_by[i])
    both = sum(1 for i in common if a_by[i] and b_by[i])
    neither = len(common) - a_only - b_only - both
    return Paired(a_only, b_only, both, neither, mcnemar_exact(a_only, b_only))


# -- H3: gate precision -----------------------------------------------------------------


@dataclass(frozen=True)
class GateStats:
    claimed: int
    precision: float | None
    precision_ci: tuple[float, float] | None
    recall: float | None
    abstain_rate: float | None
    gate_recall_cost: float | None
    precision_no_gate: float | None
    rejections: dict[str, int]


def _ratio(k: int, n: int) -> float | None:
    return k / n if n else None


def gate_stats(rows: Sequence[ResultRow]) -> GateStats:
    n = len(rows)
    claimed = sum(1 for r in rows if r.outcome_gated in EMITTED)
    rep_gated = sum(1 for r in rows if r.outcome_gated is R)
    rep_always = sum(1 for r in rows if r.outcome is R)
    emitted_always = sum(1 for r in rows if r.outcome in EMITTED)
    no_test_gated = sum(1 for r in rows if r.outcome_gated is InstanceOutcome.NO_TEST)
    rejections = {c: sum(r.gate_rejections.get(c, 0) for r in rows) for c in GATE_CHECKS}
    cost = (rep_always - rep_gated) / n if n else None
    return GateStats(
        claimed=claimed,
        precision=_ratio(rep_gated, claimed),
        precision_ci=wilson(rep_gated, claimed),
        recall=_ratio(rep_gated, n),
        abstain_rate=_ratio(no_test_gated, n),
        gate_recall_cost=cost,
        precision_no_gate=_ratio(rep_always, emitted_always),
        rejections=rejections,
    )


# -- H2: compute ------------------------------------------------------------------------


def p90(values: Sequence[float]) -> float | None:
    """Nearest-rank 90th percentile."""
    if not values:
        return None
    s = sorted(values)
    return s[max(0, math.ceil(0.9 * len(s)) - 1)]


@dataclass(frozen=True)
class TokenStats:
    mean: float | None
    median: float | None
    p90: float | None
    max: int | None
    total: int
    per_reproduced: float | None
    requests_mean: float | None
    rate_limit_wait_mean_s: float | None
    list_price_total_usd: float | None
    list_price_per_bug_usd: float | None
    list_price_per_reproduced_usd: float | None


def token_stats(rows: Sequence[ResultRow], mode: Mode = "always") -> TokenStats:
    tokens = [r.tokens for r in rows]
    total = sum(tokens)
    reproduced = sum(1 for r in rows if outcome(r, mode) is R)
    prices = [r.list_price_usd for r in rows]
    price_total = None if not rows or any(p is None for p in prices) else sum(
        p for p in prices if p is not None
    )
    return TokenStats(
        mean=statistics.mean(tokens) if tokens else None,
        median=statistics.median(tokens) if tokens else None,
        p90=p90(tokens),
        max=max(tokens) if tokens else None,
        total=total,
        per_reproduced=total / reproduced if reproduced else None,
        requests_mean=statistics.mean(r.requests for r in rows) if rows else None,
        rate_limit_wait_mean_s=statistics.mean(r.rate_limit_wait_s for r in rows)
        if rows else None,
        list_price_total_usd=price_total,
        list_price_per_bug_usd=price_total / len(rows) if price_total is not None else None,
        list_price_per_reproduced_usd=price_total / reproduced
        if price_total is not None and reproduced else None,
    )


def ceiling_curve(
    rows: Sequence[ResultRow], ceilings: Iterable[int] = CEILINGS, mode: Mode = "always"
) -> list[tuple[int, int, int]]:
    """(ceiling, reproduced, N) as if the per-bug ceiling had been lower.

    A reproduction counts at a ceiling only if its candidate already existed before
    cumulative tokens crossed that ceiling. Nothing after the crossing is used."""
    n = len(rows)
    out = []
    for c in ceilings:
        k = sum(1 for r in rows if outcome(r, mode) is R
                and r.candidate_tokens_at is not None and r.candidate_tokens_at <= c)
        out.append((c, k, n))
    return out


@dataclass(frozen=True)
class RouteMix:
    calls: dict[str, int]
    share: dict[str, float]
    retries: int
    deferrals: int
    mixed_rows: int


def route_mix(rows: Sequence[ResultRow]) -> RouteMix:
    calls: dict[str, int] = {}
    for r in rows:
        for route, n in r.route_calls.items():
            calls[route] = calls.get(route, 0) + n
    total = sum(calls.values())
    return RouteMix(
        calls=dict(sorted(calls.items())),
        share={k: v / total for k, v in sorted(calls.items())} if total else {},
        retries=sum(r.retries for r in rows),
        deferrals=sum(r.deferrals for r in rows),
        mixed_rows=sum(1 for r in rows if r.mixed),
    )


# -- H4 and H5: inputs arrive in M8; the aggregations are fixed now ----------------------


def flaky_rate(flaky_flags: Sequence[bool]) -> tuple[int, int, float | None]:
    """(flaky, emitted, rate) from one flag per emitted test."""
    k = sum(1 for f in flaky_flags if f)
    return k, len(flaky_flags), _ratio(k, len(flaky_flags))


def mutation_summary(scores: Sequence[float]) -> tuple[float | None, float | None]:
    """(mean, median) of per-instance mutation scores."""
    if not scores:
        return None, None
    return statistics.mean(scores), statistics.median(scores)
