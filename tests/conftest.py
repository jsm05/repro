from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

from repro.datasets.fixtures import FixtureSource
from tests.helpers import FIXTURES_ROOT

# Fake providers listen on 127.0.0.1; keep any configured HTTP proxy away from them.
for _var in ("NO_PROXY", "no_proxy"):
    _cur = os.environ.get(_var, "")
    if "127.0.0.1" not in _cur:
        os.environ[_var] = ",".join(p for p in (_cur, "127.0.0.1", "localhost") if p)


@pytest.fixture(scope="session")
def fixture_source() -> Iterator[FixtureSource]:
    yield FixtureSource(FIXTURES_ROOT)
