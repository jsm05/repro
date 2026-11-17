"""Adversarial suite: every cheat is rejected, by the check TEST_SPEC names (M2)."""

from __future__ import annotations

import pytest

from repro.datasets.fixtures import FixtureSource
from repro.gate.checks import GateConfig, OnlineGate
from repro.models import Candidate
from repro.oracle.oracle import run_oracle
from repro.sandbox.local import local_factory
from tests.helpers import load_cheats

ALL = load_cheats()
GATE_CHEATS = [c for c in ALL if c[2]["rejected_by"] != "oracle"]
ORACLE_ONLY = [c for c in ALL if c[2]["rejected_by"] == "oracle"]
ARGS = ("fx", "cid", "meta", "patch")


def test_suite_is_complete() -> None:
    """C1 C2 C3 C4 C5 C6 C7 C8 C9 C10 C11 C12 C13 C14 C15 O-1 O-2: all cheats are stored."""
    have = {cid for _, cid, _, _ in ALL}
    assert have == {f"C{i}" for i in range(1, 16)} | {"O-1", "O-2"}


@pytest.mark.parametrize(ARGS, GATE_CHEATS, ids=[c[1] for c in GATE_CHEATS])
def test_cheat_rejected_by_named_check(
    fixture_source: FixtureSource, fx: str, cid: str, meta: dict[str, object], patch: str
) -> None:
    """C1 C2 C3 C4 C5 C6 C7 C8 C9 C10 C11 C12 C13 C14 C15: rejected by the named check."""
    cand = Candidate(diff=patch, test_ids=list(meta["test_ids"]), origin=cid)  # type: ignore[call-overload]
    gate = OnlineGate(local_factory(fixture_source.load(fx).repo_dir), GateConfig(timeout_s=3))
    v = gate.verify(cand)
    assert not v.passed, f"{cid} ({meta['cheat']}) got through the gate"
    assert v.failed_check == meta["rejected_by"], f"{cid}: {v.failed_check} {v.reason}"


@pytest.mark.parametrize(ARGS, ORACLE_ONLY, ids=[c[1] for c in ORACLE_ONLY])
def test_oracle_only_cases(
    fixture_source: FixtureSource, fx: str, cid: str, meta: dict[str, object], patch: str
) -> None:
    """O-1 O-2: these pass the online gate by design; only the oracle catches them (F→F)."""
    loaded = fixture_source.load(fx)
    factory = local_factory(loaded.repo_dir)
    cand = Candidate(diff=patch, test_ids=list(meta["test_ids"]), origin=cid)  # type: ignore[call-overload]
    v = OnlineGate(factory, GateConfig(timeout_s=30)).verify(cand)
    assert v.passed, f"{cid} is meant to pass the gate (documents its limit): {v.reason}"
    o = run_oracle(cand, loaded.instance.sealed.gold_fix_patch, factory, GateConfig(timeout_s=30))
    assert o.outcome == "WRONG_TEST"
    assert {tr.label for tr in o.transitions.values() if tr.test_id in cand.test_ids} == {"F→F"}
