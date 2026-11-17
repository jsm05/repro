"""Oracle O1/O2, per-test transitions and instance outcome (TEST_SPEC M2)."""

from __future__ import annotations

import shutil
from pathlib import Path

from repro.datasets.fixtures import FixtureSource
from repro.gate.checks import GateConfig
from repro.models import Candidate
from repro.oracle.oracle import run_oracle
from repro.sandbox.local import local_factory
from tests.helpers import new_file_diff

CFG = GateConfig(timeout_s=30)

FIVE = """
import os

import pytest

from pager.pages import page_count, paginate


@pytest.fixture
def needs_fix():
    assert paginate([1, 2, 3, 4, 5], 0, 2) == [1, 2]


def test_fp():
    assert paginate([1, 2, 3, 4, 5], 0, 2) == [1, 2]


def test_ff():
    assert paginate([1, 2, 3], 0, 3) == [9]


def test_pp():
    assert page_count([1, 2], 1) == 2


def test_pf():
    assert paginate([1, 2, 3, 4, 5], 0, 2) == [1]


def test_ep(needs_fix):
    assert True


def test_flaky_after_fix():
    assert paginate([1, 2, 3, 4, 5], 0, 2) == [1, 2]
    assert os.environ.get("PYTHONHASHSEED") != "2"
"""

P = "tests/test_five.py"


def ids(*names: str) -> list[str]:
    return [f"{P}::{n}" for n in names]


def only(*names: str) -> str:
    """The FIVE module trimmed to the named tests (plus imports and the fixture)."""
    head, *blocks = FIVE.split("\n\n\ndef ")
    keep = [b for b in blocks if b.split("(", 1)[0] in names]
    return "\n\n\ndef ".join([head, *keep]) + "\n"


def oracle(source: FixtureSource, cand: Candidate | None, fix: str | None = None):  # type: ignore[no-untyped-def]
    fx = source.load("f01_off_by_one")
    return run_oracle(cand, fix if fix is not None else fx.instance.sealed.gold_fix_patch,
                      local_factory(fx.repo_dir), CFG)


def test_transitions_and_outcome_rule(fixture_source: FixtureSource) -> None:
    """T2.10: crafted tests produce F→P, F→F, P→P, P→F, E→P; the outcome follows the rule."""
    names = ("test_fp", "test_ff", "test_pp", "test_pf", "test_ep")
    v = oracle(fixture_source, Candidate(diff=new_file_diff(P, only(*names)), test_ids=ids(*names)))
    labels = {t.rsplit("::", 1)[1]: tr.label for t, tr in v.transitions.items()}
    assert labels == {"test_fp": "F→P", "test_ff": "F→F", "test_pp": "P→P", "test_pf": "P→F",
                      "test_ep": "E→P"}
    assert v.outcome == "WRONG_TEST"
    assert not v.o1_pass_after

    good = ("test_fp", "test_pp")
    v = oracle(fixture_source, Candidate(diff=new_file_diff(P, only(*good)), test_ids=ids(*good)))
    assert v.outcome == "REPRODUCED", v.reason
    assert v.o1_pass_after and v.o2_no_collateral

    v = oracle(fixture_source, Candidate(diff=new_file_diff(P, only("test_ep")),
                                         test_ids=ids("test_ep")))
    assert v.outcome == "WRONG_TEST", "E→P alone is not a reproduction"

    assert oracle(fixture_source, None).outcome == "NO_TEST"
    broken_fix = "diff --git a/pager/pages.py b/pager/pages.py\n--- a/pager/pages.py\n" \
                 "+++ b/pager/pages.py\n@@ -1,1 +1,1 @@\n-no such line\n+x\n"
    v = oracle(fixture_source, Candidate(diff=new_file_diff(P, only(*good)), test_ids=ids(*good)),
               fix=broken_fix)
    assert v.outcome == "INFRA_ERROR"


def test_collateral_failure_in_touched_file(fixture_source: FixtureSource, tmp_path: Path) -> None:
    """T2.11: appending to a file whose existing test fails after the fix is not REPRODUCED."""
    fx = fixture_source.load("f01_off_by_one")
    repo = tmp_path / "repo"
    shutil.copytree(fx.repo_dir, repo)
    pages_test = repo / "tests" / "test_pages.py"
    pages_test.write_text(pages_test.read_text() + "\n\ndef test_old_behaviour():\n"
                          "    assert paginate([1, 2, 3], 0, 2) == [1]\n")
    old_lines = pages_test.read_text().count("\n")
    append = (
        "diff --git a/tests/test_pages.py b/tests/test_pages.py\n"
        "--- a/tests/test_pages.py\n+++ b/tests/test_pages.py\n"
        f"@@ -{old_lines},1 +{old_lines},5 @@\n"
        "     assert paginate([1, 2, 3], 0, 2) == [1]\n"
        "+\n+\n+def test_full_page():\n+    assert paginate([1, 2, 3, 4, 5], 0, 2) == [1, 2]\n"
    )
    cand = Candidate(diff=append, test_ids=["tests/test_pages.py::test_full_page"])
    v = run_oracle(cand, fx.instance.sealed.gold_fix_patch, local_factory(repo), CFG)
    assert v.transitions["tests/test_pages.py::test_full_page"].label == "F→P"
    assert v.transitions["tests/test_pages.py::test_old_behaviour"].label == "P→F"
    assert not v.o2_no_collateral
    assert v.outcome == "WRONG_TEST"


def test_pass_after_needs_three_of_three(fixture_source: FixtureSource) -> None:
    """T2.12: a test passing only 2 of 3 times on fixed code is not REPRODUCED and is flaky."""
    name = "test_flaky_after_fix"
    v = oracle(fixture_source, Candidate(diff=new_file_diff(P, only(name)), test_ids=ids(name)))
    assert v.outcome == "WRONG_TEST"
    assert v.flaky_tests == ids(name)
    assert v.transitions[ids(name)[0]].label == "F→~"
