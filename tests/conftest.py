from __future__ import annotations

from collections.abc import Iterator

import pytest

from repro.datasets.fixtures import FixtureSource
from tests.helpers import FIXTURES_ROOT


@pytest.fixture(scope="session")
def fixture_source() -> Iterator[FixtureSource]:
    yield FixtureSource(FIXTURES_ROOT)
