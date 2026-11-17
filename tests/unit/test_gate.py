"""Online gate G0 to G5 on fixture repos (TEST_SPEC M2)."""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Mapping, Sequence
from typing import Any

import pytest

from repro.datasets.fixtures import FixtureSource
from repro.gate.candidate import diff_from_workspace, repo_reader
from repro.gate.checks import GateConfig, OnlineGate
from repro.models import Candidate, ExecResult, GateVerdict, PatchResult, TestStatus
from repro.oracle.oracle import run_oracle
from repro.sandbox.base import Sandbox
from repro.sandbox.local import LocalSandbox, local_factory
from tests.helpers import FIXTURE_NAMES, SANDBOX_DIGEST, load_cheats, new_file_diff

CHEATS = {cid: (fx, meta, patch) for fx, cid, meta, patch in load_cheats()}


def gate_for(source: FixtureSource, name: str, timeout_s: float = 30.0) -> OnlineGate:
    fx = source.load(name)
    return OnlineGate(local_factory(fx.repo_dir), GateConfig(timeout_s=timeout_s))


def cheat(cid: str) -> tuple[str, Candidate]:
    fx, meta, patch = CHEATS[cid]
    return fx, Candidate(diff=patch, test_ids=list(meta["test_ids"]))  # type: ignore[call-overload]


def gold(source: FixtureSource, name: str) -> Candidate:
    sealed = source.load(name).instance.sealed
    return Candidate(diff=sealed.gold_test_patch, test_ids=sealed.fail_to_pass, origin="gold")


def test_syntax_error_and_bad_import_stop_at_g1(fixture_source: FixtureSource) -> None:
    """T2.3: a syntax error and a bad import are rejected at G1."""
    fx, syntax = cheat("C9")
    assert gate_for(fixture_source, fx).verify(syntax).failed_check == "G1"
    bad_import = Candidate(
        diff=new_file_diff("tests/test_imp.py", "import no_such_module_xyz\n\n\n"
                           "def test_imp():\n    assert no_such_module_xyz\n"),
        test_ids=["tests/test_imp.py::test_imp"],
    )
    v = gate_for(fixture_source, "f01_off_by_one").verify(bad_import)
    assert v.failed_check == "G1", v.reason


def test_passing_test_rejected_at_g2(fixture_source: FixtureSource) -> None:
    """T2.4: a test that passes on the buggy code is rejected at G2."""
    cand = Candidate(
        diff=new_file_diff("tests/test_ok.py", "from pager.pages import paginate\n\n\n"
                           "def test_ok():\n    assert paginate([1, 2, 3], 5, 2) == []\n"),
        test_ids=["tests/test_ok.py::test_ok"],
    )
    v = gate_for(fixture_source, "f01_off_by_one").verify(cand)
    assert v.failed_check == "G2"
    assert v.test_status["tests/test_ok.py::test_ok"][0] is TestStatus.PASSED


class SpySandbox:
    """Delegates to a real sandbox and records the hash seed of every pytest run."""

    def __init__(self, inner: Sandbox, index: int, log: list[tuple[int, str]]):
        self._inner, self._index, self._log = inner, index, log

    @property
    def workdir(self) -> str:
        return self._inner.workdir

    @property
    def python(self) -> str:
        return self._inner.python

    def start(self) -> None:
        self._inner.start()

    def exec(self, cmd: str | Sequence[str], timeout_s: float | None = None,
             env: Mapping[str, str] | None = None) -> ExecResult:
        if "pytest" in (cmd if isinstance(cmd, str) else " ".join(cmd)):
            self._log.append((self._index, (env or {}).get("PYTHONHASHSEED", "?")))
        return self._inner.exec(cmd, timeout_s, env)

    def put_file(self, path: str, content: str | bytes) -> None:
        self._inner.put_file(path, content)

    def get_file(self, path: str) -> bytes:
        return self._inner.get_file(path)

    def delete_file(self, path: str) -> None:
        self._inner.delete_file(path)

    def apply_patch(self, diff: str) -> PatchResult:
        return self._inner.apply_patch(diff)

    def source_tree_hash(self) -> str:
        return self._inner.source_tree_hash()

    def close(self) -> None:
        self._inner.close()


def test_seed_dependent_test_rejected_at_g4(fixture_source: FixtureSource) -> None:
    """T2.7: the careless f04 test is rejected at G4; 3 fresh sandboxes ran seeds 0, 1, 2."""
    seeds = {
        s: subprocess.run(
            [sys.executable, "-c", "print(list(set(['x', 'y'])) == ['x', 'y'])"],
            env={"PYTHONHASHSEED": str(s)}, capture_output=True, text=True,
        ).stdout.strip()
        for s in (0, 1, 2)
    }
    assert seeds[0] == "False", "precondition: seed 0 must fail"
    assert len(set(seeds.values())) > 1, "precondition: seeds 0-2 must disagree"

    fx, careless = cheat("C11")
    real = local_factory(fixture_source.load(fx).repo_dir)
    log: list[tuple[int, str]] = []
    made: list[int] = []

    def spy_factory() -> Sandbox:
        made.append(len(made))
        return SpySandbox(real(), made[-1], log)

    v = OnlineGate(spy_factory, GateConfig(timeout_s=30)).verify(careless)
    assert v.failed_check == "G4", v.reason
    assert log == [(0, "0"), (1, "1"), (2, "2")]
    assert len(made) == 3


def test_trivial_and_side_effecting_tests_rejected_at_g5(fixture_source: FixtureSource) -> None:
    """T2.8: `assert False` fails G5 (no repo code ran); a source-rewriting test fails G5 (hash)."""
    fx, trivial = cheat("C1")
    v = gate_for(fixture_source, fx).verify(trivial)
    assert v.failed_check == "G5"
    assert "repo function" in v.reason
    fx, rewrite = cheat("C12")
    v = gate_for(fixture_source, fx).verify(rewrite)
    assert v.failed_check == "G5"
    assert "source tree" in v.reason


def _comparable(v: GateVerdict) -> dict[str, Any]:
    return v.model_dump()


def test_verification_ignores_workspace_state(fixture_source: FixtureSource) -> None:
    """T2.9: a polluted workspace gives the same verdict as a clean one and is left untouched."""
    fx = fixture_source.load("f01_off_by_one")
    test_path = "tests/test_paginate_size.py"
    with LocalSandbox(fx.repo_dir) as scratch:
        assert scratch.apply_patch(fx.instance.sealed.gold_test_patch).ok
        test_text = scratch.get_file(test_path)

    gate = OnlineGate(local_factory(fx.repo_dir), GateConfig(timeout_s=30))
    reader = repo_reader(fx.repo_dir)
    with LocalSandbox(fx.repo_dir) as clean, LocalSandbox(fx.repo_dir) as dirty:
        clean.put_file(test_path, test_text)
        dirty.put_file(test_path, test_text)
        dirty.put_file("ws_helper.py", "EXPECTED = [1, 2]\n")
        dirty.put_file("tests/ws_helper.py", "EXPECTED = [1, 2]\n")
        assert dirty.apply_patch(fx.instance.sealed.gold_fix_patch).ok  # source edited
        before = dirty.exec([dirty.python, "-c", SANDBOX_DIGEST]).stdout

        c_clean = Candidate(diff=diff_from_workspace(clean, reader, [test_path]),
                            test_ids=fx.instance.sealed.fail_to_pass)
        c_dirty = Candidate(diff=diff_from_workspace(dirty, reader, [test_path]),
                            test_ids=fx.instance.sealed.fail_to_pass)
        assert c_clean.diff == c_dirty.diff
        v_clean, v_dirty = gate.verify(c_clean), gate.verify(c_dirty)
        assert v_clean.passed, v_clean.reason
        assert _comparable(v_clean) == _comparable(v_dirty)
        assert dirty.exec([dirty.python, "-c", SANDBOX_DIGEST]).stdout == before


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_gold_passes_gate_and_oracle(fixture_source: FixtureSource, name: str) -> None:
    """T2.13: gold tests pass G0 to G5 and the oracle on all 8 fixtures."""
    fx = fixture_source.load(name)
    cand = gold(fixture_source, name)
    factory = local_factory(fx.repo_dir)
    v = OnlineGate(factory, GateConfig(timeout_s=30)).verify(cand)
    assert v.passed, f"{v.failed_check}: {v.reason}"
    o = run_oracle(cand, fx.instance.sealed.gold_fix_patch, factory, GateConfig(timeout_s=30))
    assert o.outcome == "REPRODUCED", o.reason


def test_hang_is_timeout_and_rejected(fixture_source: FixtureSource) -> None:
    """T2.14: the f08 wrong test (collatz_steps(0)) is TIMEOUT, not FAILED, and is rejected."""
    cand = Candidate(
        diff=new_file_diff("tests/test_zero.py", "from collatz.steps import collatz_steps\n\n\n"
                           "def test_zero():\n    assert collatz_steps(0) == 0\n"),
        test_ids=["tests/test_zero.py::test_zero"],
    )
    v = gate_for(fixture_source, "f08_can_hang", timeout_s=3).verify(cand)
    assert not v.passed
    assert v.failed_check == "G2"
    assert v.test_status["tests/test_zero.py::test_zero"] == [TestStatus.TIMEOUT]


def test_verdict_serialises_with_check_and_signature(fixture_source: FixtureSource) -> None:
    """T2.15: verdicts round-trip through JSON, name the first failing check, carry signatures."""
    passed = gate_for(fixture_source, "f01_off_by_one").verify(gold(fixture_source,
                                                                     "f01_off_by_one"))
    fx, trivial = cheat("C1")
    failed = gate_for(fixture_source, fx).verify(trivial)
    for v in (passed, failed):
        assert GateVerdict.model_validate_json(v.model_dump_json()) == v
        assert v.signatures and all(v.signatures.values())
        assert v.reason
    assert passed.failed_check is None and passed.reproducing_tests
    assert failed.failed_check == "G5"
    assert '"failed_check":"G5"' in failed.model_dump_json()

