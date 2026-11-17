"""End-to-end: gold tests fail before and pass after the fix (TEST_SPEC M1)."""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from repro.cli import main
from repro.datasets.fixtures import FixtureSource, check_fixture
from repro.models import TestStatus
from tests.helpers import FIXTURE_NAMES, FIXTURES_ROOT


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_gold_is_fail_to_pass(fixture_source: FixtureSource, name: str) -> None:
    """T1.13: the gold test is F->P on every fixture, and pass-to-pass tests stay green."""
    result = check_fixture(fixture_source.load(name))
    assert all(s is TestStatus.FAILED for s in result.before.values()), result.before
    assert all(s is TestStatus.PASSED for s in result.after.values()), result.after
    assert result.ok


def test_cli_fixtures_check() -> None:
    """T1.13: `repro fixtures check` prints F→P for all 8 fixtures and exits 0."""
    out = CliRunner().invoke(main, ["fixtures", "check", "--root", str(FIXTURES_ROOT)])
    assert out.exit_code == 0, out.output
    lines = [line for line in out.output.splitlines() if line.startswith("f0")]
    assert len(lines) == 8
    assert all("F→P" in line for line in lines)
