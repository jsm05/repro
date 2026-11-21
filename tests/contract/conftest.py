"""Sandbox backends under contract test: LocalSandbox always, DockerSandbox when a test
image is configured (docker-tests.yml sets REPRO_DOCKER_TEST_IMAGE)."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from repro.sandbox.base import Limits, Sandbox
from repro.sandbox.local import LocalSandbox
from tests.helpers import wait_dead

DOCKER_IMAGE = os.environ.get("REPRO_DOCKER_TEST_IMAGE", "")


class LocalBackend:
    name = "local"

    def __init__(self) -> None:
        self.opened: list[LocalSandbox] = []
        self._paths: dict[int, Path] = {}

    def make(self, source: Path, **limits: Any) -> Sandbox:
        sb = LocalSandbox(source, Limits(**limits))
        sb.start()
        self.opened.append(sb)
        self._paths[id(sb)] = Path(sb.workdir).parent
        return sb

    def is_gone(self, sb: Sandbox) -> bool:
        return not self._paths[id(sb)].exists()

    def process_dead(self, sb: Sandbox, pid: int) -> bool:
        return wait_dead(pid)

    def close_all(self) -> None:
        for sb in self.opened:
            sb.close()


class DockerBackend:
    name = "docker"

    def __init__(self) -> None:
        self.opened: list[Any] = []

    def make(self, source: Path, **limits: Any) -> Sandbox:
        from repro.sandbox.docker import DockerSandbox, DockerSettings

        lim = Limits(**limits)
        memory = f"{lim.memory_bytes // 2**20}m"
        sb = DockerSandbox(DOCKER_IMAGE, DockerSettings(run_id="contract", memory=memory),
                           lim, source_dir=source)
        sb.start()
        self.opened.append(sb)
        return sb

    def is_gone(self, sb: Any) -> bool:
        out = subprocess.run(["docker", "ps", "-aq", "--filter", f"name={sb.name}"],
                             capture_output=True, text=True, check=False).stdout
        return out.strip() == ""

    def process_dead(self, sb: Any, pid: int) -> bool:
        if self.is_gone(sb):
            return True  # the container and every process in it are gone
        return sb.exec(f"kill -0 {pid}", timeout_s=10).exit_code != 0

    def close_all(self) -> None:
        for sb in self.opened:
            sb.close()


BACKENDS = ["local"] + (["docker"] if DOCKER_IMAGE else [])


@pytest.fixture(params=BACKENDS)
def backend(request: pytest.FixtureRequest) -> Iterator[Any]:
    b: Any = LocalBackend() if request.param == "local" else DockerBackend()
    yield b
    b.close_all()
