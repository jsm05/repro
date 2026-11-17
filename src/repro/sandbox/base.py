"""Sandbox protocol. One Sandbox object is one isolated copy of a repo."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from repro.models import ExecResult, PatchResult


class SandboxError(Exception):
    """The sandbox itself failed or was used after close()."""


class SandboxPathError(SandboxError):
    """A file path would escape the sandbox root."""


@dataclass(frozen=True)
class Limits:
    memory_bytes: int = 2 * 1024**3
    output_cap_bytes: int = 16 * 1024
    default_timeout_s: float = 180.0
    file_size_bytes: int = 512 * 1024**2


@runtime_checkable
class Sandbox(Protocol):
    @property
    def workdir(self) -> str:
        """Absolute path of the repo root inside the sandbox."""
        ...

    @property
    def python(self) -> str:
        """Interpreter to run tests with inside the sandbox."""
        ...

    def start(self) -> None: ...

    def exec(
        self,
        cmd: str | Sequence[str],
        timeout_s: float | None = None,
        env: Mapping[str, str] | None = None,
    ) -> ExecResult:
        """Run a command in workdir. A str runs through /bin/sh -c."""
        ...

    def put_file(self, path: str, content: str | bytes) -> None: ...

    def get_file(self, path: str) -> bytes:
        """Raises FileNotFoundError if missing, SandboxPathError if path escapes."""
        ...

    def delete_file(self, path: str) -> None: ...

    def apply_patch(self, diff: str) -> PatchResult:
        """All or nothing: on failure the tree is unchanged."""
        ...

    def source_tree_hash(self) -> str:
        """Hash of non-test source files (see treehash.py)."""
        ...

    def close(self) -> None:
        """Remove all state. Idempotent."""
        ...
