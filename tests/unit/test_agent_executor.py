"""The agent inside `repro eval`: one row with both modes, freeze before oracle, deferral."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from repro.config import EvalConfig
from repro.eval.execute import Executor, FixtureDataset, LLMHandle
from repro.eval.rows import read_trace
from repro.eval.run import Runner
from repro.eval.store import LocalStore
from repro.llm.budget import Ledger
from repro.llm.fake import FakeLLM
from repro.llm.router import Deferred
from repro.models import Candidate
from repro.models import InstanceOutcome as O
from tests.helpers import FIXTURES_ROOT
from tests.unit.test_agent import F01_TEST, TEST_PATH, act, variant, write

F01 = "fixture__f01_off_by_one"
CFG = EvalConfig(config_id="a-test", kind="agent", llm="cassette", gate_timeout_s=30)


def run_unit(tmp_path: Path, script: list[Any]) -> tuple[LocalStore, Any]:
    store = LocalStore(tmp_path, "run")
    ledger = Ledger(store.run_dir / "ledger-1.jsonl")
    ex = Executor(CFG, "run", FixtureDataset(str(FIXTURES_ROOT)),
                  lambda iid: LLMHandle(FakeLLM(list(script)), ledger=ledger))
    summary = Runner(CFG, "run", [F01], store, ex).run()
    return store, summary


def test_t5_15_freeze_hash_matches_export_and_precedes_oracle(tmp_path: Path) -> None:
    """T5.15: the frozen SHA-256 equals the exported diff's; the oracle comes after freeze."""
    store, _ = run_unit(tmp_path, [act("view", path="pager/pages.py"), write(), act("check"),
                                   act("submit")])
    row = store.rows()[0]
    assert (row.outcome, row.outcome_gated) == (O.REPRODUCED, O.REPRODUCED)
    assert row.gate_passed is True and row.stop_reason == "SUBMITTED"
    assert row.steps == 4 and row.gate_checks == 1 and row.requests == 4
    events = read_trace(store.run_dir / "traces" / f"{F01}.jsonl")
    types = [e.type for e in events]
    assert types.index("freeze") < types.index("oracle")
    frozen = events[types.index("freeze")].payload["candidate_sha256"]
    diff = (store.run_dir / "candidates" / f"{F01}.diff").read_text()
    meta = json.loads((store.run_dir / "candidates" / f"{F01}.json").read_text())
    assert Candidate(diff=diff, test_ids=meta["test_ids"]).sha256 == frozen == row.candidate_sha256
    assert F01_TEST.splitlines()[3] in diff and TEST_PATH in diff
    entries, _ = Ledger(store.run_dir / "ledger-1.jsonl").read()
    llm_tokens = sum(e.tokens for e in events if e.type == "llm_call")
    assert llm_tokens == sum(e.total_tokens for e in entries) == row.tokens


def test_one_run_gives_both_modes(tmp_path: Path) -> None:
    """One agent run yields gated and always-emit: a failing best is emitted only always."""
    passes_everywhere = ("def test_issue():\n    assert True\n")
    script = [write(passes_everywhere, path="tests/test_issue.py"), act("check"),
              act("abstain", reason="cannot make it fail")]
    store, _ = run_unit(tmp_path, script)
    row = store.rows()[0]
    assert row.outcome is O.WRONG_TEST and row.outcome_gated is O.NO_TEST
    assert row.gate_passed is False and row.gate_rejections == {"G2": 1}
    assert row.stop_reason == "ABSTAINED(cannot make it fail)"


def test_t5_23_deferred_unit_writes_nothing(tmp_path: Path) -> None:
    """T5.23: quota out mid-run -> the unit is deferred: no row, no candidate, count kept."""
    store, summary = run_unit(tmp_path, [write(variant(1)), act("bash", cmd="ls"),
                                         Deferred(9e9)])
    assert summary.deferred == [F01]
    assert store.rows() == []
    assert not (store.run_dir / "candidates").exists()
    assert json.loads((store.run_dir / "deferrals-1.json").read_text()) == {F01: 1}


def test_agent_config_fields_are_in_the_hash() -> None:
    """T4.10: agent limits are part of the config hash."""
    for field, value in {"max_steps": 10, "max_gate_checks": 2, "active_minutes": 5.0,
                         "hard_cap_minutes": 9.0, "max_tool_errors": 1, "max_repeats": 2,
                         "prompt_version": "v2", "cassette_match": "order"}.items():
        assert CFG.model_copy(update={field: value}).hash != CFG.hash, field


@pytest.mark.parametrize("bad", ["swtbench"])
def test_cli_refuses_unbuilt_dataset(bad: str, tmp_path: Path) -> None:
    """The CLI says clearly which datasets arrive later."""
    from click.testing import CliRunner

    from repro.cli import main

    cfg = tmp_path / "c.yaml"
    cfg.write_text(f"config_id: x\nkind: agent\ndataset: {bad}\nllm: cassette\n"
                   "cassette_dir: nowhere\n")
    res = CliRunner().invoke(main, ["eval", "--config", str(cfg), "--run-id", "x",
                                    "--results-dir", str(tmp_path)])
    assert res.exit_code != 0 and "arrives in M6" in res.output
