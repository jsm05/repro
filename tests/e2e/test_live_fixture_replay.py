"""Replay of the live A-chain fixture run (recorded by live-smoke.yml, mode fixtures).

Skipped until that run has committed its cassettes. Once they exist, the hermetic suite
replays every recorded reply with no network and must reach the same outcomes.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from repro.cli import main
from repro.eval.rows import read_rows
from repro.eval.store import shard_files
from tests.helpers import ROOT

CASSETTES = ROOT / "eval" / "cassettes" / "fixtures-a-chain"
LIVE_ROWS = ROOT / "eval" / "reports" / "live" / "fixtures-live-rows.jsonl"


@pytest.mark.skipif(not (CASSETTES.is_dir() and any(CASSETTES.glob("*.jsonl"))
                         and LIVE_ROWS.exists()),
                    reason="live fixture cassettes not recorded yet (live-smoke.yml, fixtures)")
def test_live_fixture_cassettes_replay(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """M5: the hermetic suite replays the live fixture run and gets the same outcomes."""
    monkeypatch.chdir(ROOT)
    res = CliRunner().invoke(main, ["eval", "--config", "eval/configs/fixtures-a-chain-replay.yaml",
                                    "--run-id", "replay", "--results-dir", str(tmp_path)])
    assert res.exit_code == 0, res.output
    live = {r.instance_id: r for r in read_rows([LIVE_ROWS])}
    replay = {r.instance_id: r for r in read_rows(shard_files(tmp_path / "replay"))}
    recorded = {p.stem for p in CASSETTES.glob("*.jsonl")}
    for iid, row in live.items():
        if iid not in recorded or row.outcome.value == "INFRA_ERROR":
            continue
        got = replay[iid]
        assert "no recorded response" not in got.infra_error, iid
        assert "cassette has only" not in got.infra_error, iid
        assert (got.outcome, got.outcome_gated) == (row.outcome, row.outcome_gated), iid
        assert got.requests == row.requests, iid
