"""`repro status`: progress between batches (DESIGN 5.9)."""

from __future__ import annotations

import json
import math
import statistics
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from repro.eval.rows import read_rows
from repro.eval.store import shard_files
from repro.llm.budget import Ledger
from repro.models import InstanceOutcome


@dataclass
class Status:
    total: int
    done: int
    remaining: int
    deferred: int
    infra_errors: int
    tokens_by_route: dict[str, int] = field(default_factory=dict)
    requests_by_route: dict[str, int] = field(default_factory=dict)
    quota: dict[str, dict[str, Any]] = field(default_factory=dict)
    median_unit_s: float | None = None
    nights_left: int | None = None


def compute_status(
    run_dir: Path, instance_ids: Sequence[str], *, shards: int = 6, night_minutes: float = 330
) -> Status:
    rows = read_rows(shard_files(run_dir))
    wanted = set(instance_ids)
    done_ids = {r.instance_id for r in rows if r.instance_id in wanted}
    deferrals: dict[str, int] = {}
    for p in sorted(run_dir.glob("deferrals-*.json")):
        deferrals.update(json.loads(p.read_text()))
    deferred = sum(1 for i, n in deferrals.items() if n > 0 and i in wanted and i not in done_ids)
    tokens: dict[str, int] = {}
    reqs: dict[str, int] = {}
    for p in sorted(run_dir.glob("ledger-*.jsonl")):
        entries, _ = Ledger(p).read()
        for e in entries:
            tokens[e.route_id] = tokens.get(e.route_id, 0) + e.total_tokens
            reqs[e.route_id] = reqs.get(e.route_id, 0) + 1
    quota: dict[str, dict[str, Any]] = {}
    for p in sorted(run_dir.glob("quota-*.json")):
        for route, st in json.loads(p.read_text()).items():
            q = quota.setdefault(route, {"day_requests": 0, "day_tokens": 0,
                                         "exhausted_until": None})
            q["day_requests"] += int(st.get("day_requests", 0))
            q["day_tokens"] += int(st.get("day_tokens", 0))
            until = st.get("exhausted_until")
            if until is not None:
                q["exhausted_until"] = max(q["exhausted_until"] or 0, until)
    remaining = len(wanted) - len(done_ids)
    times = [r.wall_clock_s for r in rows if r.instance_id in wanted and r.wall_clock_s > 0]
    median = statistics.median(times) if times else None
    if remaining == 0:
        nights: int | None = 0
    elif median is None:
        nights = None
    else:
        per_night = shards * night_minutes * 60 / median
        nights = math.ceil(remaining / per_night)
    return Status(
        total=len(wanted), done=len(done_ids), remaining=remaining, deferred=deferred,
        infra_errors=sum(1 for r in rows if r.instance_id in wanted
                         and r.outcome is InstanceOutcome.INFRA_ERROR),
        tokens_by_route=dict(sorted(tokens.items())),
        requests_by_route=dict(sorted(reqs.items())),
        quota=dict(sorted(quota.items())), median_unit_s=median, nights_left=nights,
    )


def render_status(run_id: str, s: Status) -> str:
    lines = [f"run {run_id}: {s.done}/{s.total} units done, {s.remaining} remaining "
             f"({s.deferred} deferred, {s.infra_errors} infra errors)"]
    if s.median_unit_s is not None:
        lines.append(f"median unit time {s.median_unit_s:.0f} s")
    nights = "unknown (no finished units yet)" if s.nights_left is None else str(s.nights_left)
    lines.append(f"estimated nights to finish: {nights}")
    for route in s.tokens_by_route:
        lines.append(f"  {route}: {s.requests_by_route[route]} requests, "
                     f"{s.tokens_by_route[route]:,} tokens")
    for route, q in s.quota.items():
        ex = f", exhausted until {q['exhausted_until']:.0f}" if q["exhausted_until"] else ""
        lines.append(f"  quota {route}: {q['day_requests']} requests and "
                     f"{q['day_tokens']:,} tokens today{ex}")
    return "\n".join(lines)
