"""Golden end to end: fixtures + recorded cassettes in, the committed report out."""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from repro.cli import main
from repro.eval.report import build_report
from repro.eval.rows import read_rows
from repro.eval.store import shard_files
from repro.models import InstanceOutcome as O
from tests.helpers import ROOT

TITLE = "Fixture results (cassette replay)"
EXPECTED = {
    "fixtures-b0": {
        "fixture__f01_off_by_one": (O.REPRODUCED, O.REPRODUCED),
        "fixture__f02_raises": (O.REPRODUCED, O.REPRODUCED),
        "fixture__f03_wrong_reason_trap": (O.WRONG_TEST, O.WRONG_TEST),
        "fixture__f04_hash_order": (O.REPRODUCED, O.REPRODUCED),
        "fixture__f05_append_only": (O.REPRODUCED, O.REPRODUCED),
        "fixture__f06_two_modules": (O.NO_TEST, O.NO_TEST),
        "fixture__f07_ambiguous_issue": (O.REPRODUCED, O.REPRODUCED),
        "fixture__f08_can_hang": (O.WRONG_TEST, O.WRONG_TEST),
    },
    "fixtures-b1": {
        "fixture__f01_off_by_one": (O.REPRODUCED, O.REPRODUCED),
        "fixture__f02_raises": (O.REPRODUCED, O.REPRODUCED),
        "fixture__f03_wrong_reason_trap": (O.WRONG_TEST, O.NO_TEST),
        "fixture__f06_two_modules": (O.REPRODUCED, O.REPRODUCED),
    },
    "fixtures-a": {
        "fixture__f01_off_by_one": (O.REPRODUCED, O.REPRODUCED),
        "fixture__f02_raises": (O.REPRODUCED, O.REPRODUCED),
        "fixture__f03_wrong_reason_trap": (O.WRONG_TEST, O.NO_TEST),
        "fixture__f06_two_modules": (O.REPRODUCED, O.REPRODUCED),
    },
}


def test_golden_fixture_report(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """T4.11: fixtures + cassette give the expected outcomes and the committed report."""
    monkeypatch.chdir(ROOT)
    cli = CliRunner()
    results = tmp_path / "results"
    for cid in EXPECTED:
        res = cli.invoke(main, ["eval", "--config", f"eval/configs/{cid}.yaml", "--run-id", cid,
                                "--results-dir", str(results)])
        assert res.exit_code == 0, res.output
        assert "stopped (all done)" in res.output

    rows = []
    for cid, expected in EXPECTED.items():
        got = read_rows(shard_files(results / cid))
        assert {r.instance_id: (r.outcome, r.outcome_gated) for r in got} == expected
        rows += got
    b1 = {r.instance_id: r for r in rows if r.config_id == "fixtures-b1"}
    assert b1["fixture__f01_off_by_one"].gate_rejections == {"G2": 1}
    assert b1["fixture__f01_off_by_one"].requests == 5
    agent = {r.instance_id: r for r in rows if r.config_id == "fixtures-a"}
    assert agent["fixture__f01_off_by_one"].stop_reason == "SUBMITTED"
    assert agent["fixture__f03_wrong_reason_trap"].stop_reason.startswith("ABSTAINED")

    text, plots = build_report(rows, TITLE)
    assert text == (ROOT / "eval/reports/fixtures.md").read_text()
    assert plots["token_ceiling.svg"] == (ROOT / "eval/reports/plots/token_ceiling.svg").read_text()

    out = tmp_path / "report" / "report.md"
    res = cli.invoke(main, ["report", "--run", "fixtures-b0", "--run", "fixtures-b1", "--run",
                            "fixtures-a", "--results-dir", str(results), "--title", TITLE,
                            "--out", str(out)])
    assert res.exit_code == 0, res.output
    assert out.read_text() == text
    assert (out.parent / "plots" / "token_ceiling.svg").exists()

    # Rerunning a finished run does nothing; export and show work on its files.
    res = cli.invoke(main, ["eval", "--config", "eval/configs/fixtures-b1.yaml", "--run-id",
                            "fixtures-b1", "--results-dir", str(results)])
    assert "0 written" in res.output and "4 already done" in res.output
    res = cli.invoke(main, ["export", "--run", "fixtures-b0", "--results-dir", str(results)])
    assert res.exit_code == 0 and (results / "fixtures-b0" / "predictions.jsonl").exists()
    res = cli.invoke(main, ["show", "fixtures-b1", "fixture__f01_off_by_one", "--results-dir",
                            str(results)])
    assert res.exit_code == 0 and "FAIL at G2" in res.output and "REPRODUCED" in res.output
    res = cli.invoke(main, ["status", "fixtures-b1", "--results-dir", str(results)])
    assert res.exit_code == 0 and "4/4 units done" in res.output
