"""Fixture repos load, validate and apply (TEST_SPEC M1)."""

from __future__ import annotations

import pytest
from repro.datasets.fixtures import FixtureSource
from repro.sandbox.local import LocalSandbox

from tests.helpers import FIXTURE_NAMES


def test_eight_fixtures_present(fixture_source: FixtureSource) -> None:
    """T1.12: all 8 fixtures are discovered."""
    assert fixture_source.names() == FIXTURE_NAMES
    assert len(FIXTURE_NAMES) == 8


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_fixture_loads_and_patches_apply(fixture_source: FixtureSource, name: str) -> None:
    """T1.12: each fixture validates against the schema and both patches apply cleanly."""
    fx = fixture_source.load(name)
    assert fx.instance.public.instance_id == f"fixture__{name}"
    assert fx.instance.sealed.fail_to_pass
    for patch in (fx.instance.sealed.gold_fix_patch, fx.instance.sealed.gold_test_patch):
        with LocalSandbox(fx.repo_dir) as sb:
            res = sb.apply_patch(patch)
            assert res.ok, res.error
