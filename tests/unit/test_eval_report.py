"""Report, export, status and show on synthetic rows (TEST_SPEC M4)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from repro.eval.export import predictions, predictions_jsonl
from repro.eval.report import ReportError, build_report
from repro.eval.rows import ResultRow, TraceEvent
from repro.eval.show import render_trace
from repro.eval.status import compute_status, render_status
from repro.llm.budget import Ledger, LedgerEntry
from repro.models import InstanceOutcome as O


def row(i: int, out: O, cid: str = "c", h: str = "h1", **kw: Any) -> ResultRow:
    base: dict[str, Any] = dict(run_id="r", instance_id=f"i{i}", config_id=cid, config_hash=h,
                                kind="B0", logical_model="m", outcome=out, outcome_gated=out)
    base.update(kw)
    return ResultRow(**base)


ROWS = [row(1, O.REPRODUCED, tokens_in=100, candidate_tokens_at=50, route_calls={"a": 1},
            wall_clock_s=12.3),
        row(2, O.NO_TEST, tokens_in=80, route_calls={"a": 1}, wall_clock_s=99.9),
        row(1, O.WRONG_TEST, cid="d", h="h2", tokens_in=300, candidate_tokens_at=200),
        row(2, O.REPRODUCED, cid="d", h="h2", tokens_in=50, candidate_tokens_at=40)]


def test_report_is_byte_identical_and_has_no_timestamps() -> None:
    """T4.9: the report twice on the same input is byte-identical, with no timestamps."""
    a, plots_a = build_report(ROWS)
    b, plots_b = build_report(list(reversed(ROWS)))
    assert a == b and plots_a == plots_b
    assert not re.search(r"\d{4}-\d{2}-\d{2}|\d{2}:\d{2}|UTC", a)  # no dates or times
    assert "12.3" not in a and "99.9" not in a  # no wall-clock numbers
    assert "| `c` | B0 | m | `h1` | 2 | 1 | 50.0%" in a
    assert "| `c` | `d` | 2 | 1 | 1 | 1.0000 |" in a
    assert "token_ceiling.svg" in plots_a


def test_report_refuses_mixed_hashes_and_duplicates() -> None:
    """T4.10: a report over rows with mixed config hashes is an error."""
    with pytest.raises(ReportError, match="different config hashes"):
        build_report([row(1, O.REPRODUCED), row(2, O.REPRODUCED, h="other")])
    with pytest.raises(ReportError, match="more than one row"):
        build_report([row(1, O.REPRODUCED), row(1, O.NO_TEST)])


def test_predictions_export_format(tmp_path: Path) -> None:
    """T4.8: predictions match the SWT-bench format; NO_TEST gets an empty patch."""
    (tmp_path / "candidates").mkdir()
    (tmp_path / "candidates" / "i1.diff").write_text("diff --git a/t b/t\n")
    (tmp_path / "candidates" / "i3.diff").write_text("diff --git a/u b/u\n")
    rows = [row(2, O.NO_TEST), row(1, O.REPRODUCED, candidate_sha256="x"),
            row(3, O.INFRA_ERROR, candidate_sha256="y"), row(4, O.INFRA_ERROR)]
    preds = predictions(rows, tmp_path, "repro-c")
    assert [p["instance_id"] for p in preds] == ["i1", "i2", "i3", "i4"]
    assert all(set(p) == {"instance_id", "model_name_or_path", "model_patch"} for p in preds)
    assert preds[0]["model_patch"].startswith("diff --git")
    assert preds[1]["model_patch"] == "" and preds[3]["model_patch"] == ""
    assert preds[2]["model_patch"].startswith("diff --git")  # official harness may score it
    lines = predictions_jsonl(rows, tmp_path, "repro-c").splitlines()
    assert len(lines) == 4 and json.loads(lines[1])["model_name_or_path"] == "repro-c"


def test_status_counts_and_nights_left(tmp_path: Path) -> None:
    """T4.22: done, remaining, deferred, infra errors, tokens, quota and a nights-left estimate."""
    run = tmp_path / "run"
    run.mkdir()
    rows = [row(i, O.REPRODUCED, wall_clock_s=600.0) for i in range(3)]
    rows.append(row(3, O.INFRA_ERROR, wall_clock_s=600.0))
    (run / "shard-1.jsonl").write_text("".join(r.model_dump_json() + "\n" for r in rows))
    (run / "deferrals-1.json").write_text(json.dumps({"i4": 2, "i0": 1}))
    Ledger(run / "ledger-1.jsonl").append(LedgerEntry(ts=0, route_id="nv/a", model="m",
                                                      prompt_tokens=900, completion_tokens=100))
    Ledger(run / "ledger-2.jsonl").append(LedgerEntry(ts=0, route_id="nv/a", model="m",
                                                      prompt_tokens=10, completion_tokens=0))
    (run / "quota-1.json").write_text(json.dumps({"nv/a": {"day_requests": 5, "day_tokens": 70,
                                                           "exhausted_until": None}}))
    ids = [f"i{i}" for i in range(200)]
    st = compute_status(run, ids, shards=2, night_minutes=100)
    assert (st.total, st.done, st.remaining, st.deferred, st.infra_errors) == (200, 4, 196, 1, 1)
    assert st.tokens_by_route == {"nv/a": 1010} and st.requests_by_route == {"nv/a": 2}
    assert st.quota["nv/a"]["day_requests"] == 5
    # 2 shards x 100 min / 10 min per unit = 20 units a night -> 196 / 20 -> 10 nights
    assert st.median_unit_s == 600.0 and st.nights_left == 10
    text = render_status("run", st)
    assert "4/200 units done" in text and "estimated nights to finish: 10" in text
    assert compute_status(run, ids[:4]).nights_left == 0
    assert compute_status(tmp_path / "empty", ids).nights_left is None


def test_show_renders_tokens_route_and_waits() -> None:
    """T4.12: `repro show` renders tokens, route and rate-limit waits per step."""
    ev = [
        TraceEvent(ts=0, run_id="r", instance_id="i", seq=0, type="llm_call", route="nv/a",
                   tokens=1234, payload={"model": "m", "prompt_tokens": 1200,
                                         "completion_tokens": 34, "latency_s": 2.5,
                                         "rate_limit_wait_s": 7.5}),
        TraceEvent(ts=1, run_id="r", instance_id="i", seq=1, type="gate_check",
                   payload={"candidate": "b1-1", "passed": False, "failed_check": "G2",
                            "reason": "passes on buggy code"}),
        TraceEvent(ts=2, run_id="r", instance_id="i", seq=2, type="oracle",
                   payload={"outcome": "REPRODUCED", "reason": "ok",
                            "transitions": {"t::x": "F→P"}}),
    ]
    text = render_trace(ev)
    assert "nv/a" in text and "1,234" in text and "7.5" in text
    assert "FAIL at G2" in text and "F→P t::x" in text
    assert "total tokens 1,234, rate-limit wait 7.5 s" in text
