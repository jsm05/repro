"""Sandbox backends under contract test. DockerSandbox joins this list in M6."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from repro.sandbox.base import Limits, Sandbox
from repro.sandbox.local import LocalSandbox


class LocalBackend:
    name = "local"

    def __init__(self) -> None:
        self.opened: list[LocalSandbox] = []

    def make(self, source: Path, **limits: Any) -> Sandbox:
        sb = LocalSandbox(source, Limits(**limits))
        sb.start()
        self.opened.append(sb)
        return sb

    def is_gone(self, sb: Sandbox) -> bool:
        return not Path(sb.workdir).exists()

    def close_all(self) -> None:
        for sb in self.opened:
            sb.close()


@pytest.fixture(params=["local"])
def backend(request: pytest.FixtureRequest) -> Iterator[LocalBackend]:
    b = LocalBackend()
    yield b
    b.close_all()
