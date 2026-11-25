"""The optional G3 LLM judge (T7.2): off by default, metered, sees only AgentView content."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from repro.config import EvalConfig
from repro.eval.canary import markers, scan
from repro.eval.execute import Executor, FixtureDataset, LLMHandle
from repro.eval.rows import read_trace
from repro.eval.run import Runner
from repro.eval.store import LocalStore
from repro.gate.judge import Judge, parse_verdict
from repro.llm.budget import Ledger
from repro.llm.fake import FakeLLM
from repro.models import Candidate, FailureDetail
from repro.models import InstanceOutcome as O
from tests.helpers import FIXTURES_ROOT
from tests.unit.test_agent import act, write

F01 = "fixture__f01_off_by_one"
CFG = EvalConfig(config_id="a-judge", kind="agent", llm="cassette", gate_timeout_s=30)
AGENT = [act("view", path="pager/pages.py"), write(), act("check")]


def _run(tmp_path: Path, cfg: EvalConfig, script: list[Any]) -> tuple[LocalStore, FakeLLM]:
    store = LocalStore(tmp_path, "run")
    ledger = Ledger(store.run_dir / "ledger-1.jsonl")
    fake = FakeLLM(list(script))
    ex = Executor(cfg, "run", FixtureDataset(str(FIXTURES_ROOT)),
                  lambda iid: LLMHandle(fake, ledger=ledger))
    Runner(cfg, "run", [F01], store, ex).run()
    return store, fake


def test_t7_2_judge_is_off_by_default(tmp_path: Path) -> None:
    """Off by default: the same agent script runs with no judge call (the fake would run out)."""
    assert EvalConfig(config_id="x", kind="agent").judge is False
    store, fake = _run(tmp_path, CFG, [*AGENT, act("submit")])
    assert store.rows()[0].outcome_gated is O.REPRODUCED
    assert len(fake.calls) == 4


def test_t7_2_judge_calls_are_metered_and_see_no_sealed_content(tmp_path: Path) -> None:
    """On: one judge call per failing test after G3's rules pass; it's in the ledger, and its
    prompt holds the issue and the failure but nothing from SealedView."""
    cfg = CFG.model_copy(update={"judge": True})
    store, fake = _run(tmp_path, cfg, [*AGENT, "VERDICT: yes\nIt fails on the page bug.",
                                       act("submit")])
    row = store.rows()[0]
    assert row.outcome_gated is O.REPRODUCED and row.requests == 5
    entries, _ = Ledger(store.run_dir / "ledger-1.jsonl").read()
    assert len(entries) == 5
    judge_msgs = fake.calls[3]
    text = "\n".join(m["content"] for m in judge_msgs)
    inst = FixtureDataset(str(FIXTURES_ROOT)).load(F01).instance
    assert inst.public.issue_text[:80] in text
    assert "AssertionError" in text
    # Nothing sealed reaches the judge unless the agent itself wrote it: this fixture's
    # scripted agent happens to reuse the gold test's name, so that one hit is its own.
    diff = (store.run_dir / "candidates" / f"{F01}.diff").read_text()
    hits = scan([("judge", text)], markers(inst.sealed))
    assert all(h.marker in diff for h in hits)
    events = read_trace(store.run_dir / "traces" / f"{F01}.jsonl")
    assert [e.payload["verdict"] for e in events if e.type == "judge"] == ["yes"]


def test_t7_2_judge_no_rejects_at_g3(tmp_path: Path) -> None:
    cfg = CFG.model_copy(update={"judge": True})
    # The loop won't accept submit for a candidate the gate rejected, so the agent abstains.
    store, _ = _run(tmp_path, cfg, [*AGENT, "VERDICT: no\nIt tests an unrelated helper.",
                                    act("abstain", reason="the judge disagrees")])
    row = store.rows()[0]
    assert row.gate_rejections == {"G3": 1}
    assert row.outcome_gated is O.NO_TEST and row.outcome is O.REPRODUCED


def test_judge_parse_and_fail_open() -> None:
    assert parse_verdict("VERDICT: YES\nok") == (True, "ok")
    assert parse_verdict("blah\nverdict: no - wrong function") == (False, "- wrong function")
    ok, why = parse_verdict("I think so")
    assert ok and "no verdict" in why
    f = FailureDetail(phase="call", exc_type="AssertionError", message="1 != 2")
    j = Judge(FakeLLM(["VERDICT: no\nbroken"]), issue="paging is off by one")
    assert j(Candidate(diff="+x", test_ids=["t.py::test_a"]), "t.py::test_a", f) == (False,
                                                                                    "broken")
