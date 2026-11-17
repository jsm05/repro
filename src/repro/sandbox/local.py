"""LocalSandbox: a temp-dir copy of a repo, run with rlimits on the host.

Exists so the whole pipeline is testable without Docker. It is NOT a security
boundary: the process runs as the host user, can read the host filesystem, can use
the network, and can escape the process group with setsid. Use it only on fixture
repos and code you trust. Untrusted code goes in DockerSandbox (M6).
"""

from __future__ import annotations

import contextlib
import os
import resource
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from types import TracebackType

from repro.models import ExecResult, PatchResult
from repro.sandbox.base import Limits, SandboxError, SandboxPathError
from repro.sandbox.treehash import tree_hash

TRUNCATION_MARKER = "\n... [{} bytes truncated] ...\n"
KILL_GRACE_S = 1.0


def cap_output(data: bytes, cap: int) -> tuple[str, bool]:
    """Keep the head and tail of `data` so the result is at most about `cap` bytes."""
    if len(data) <= cap:
        return data.decode("utf-8", errors="replace"), False
    half = cap // 2
    head, tail = data[:half], data[-half:]
    marker = TRUNCATION_MARKER.format(len(data) - 2 * half)
    text = (
        head.decode("utf-8", errors="ignore") + marker + tail.decode("utf-8", errors="ignore")
    )
    return text, True


def _read_capped(path: Path, cap: int) -> tuple[str, bool]:
    size = path.stat().st_size
    with path.open("rb") as f:
        if size <= cap:
            return cap_output(f.read(), cap)
        half = cap // 2
        head = f.read(half)
        f.seek(size - half)
        tail = f.read(half)
    marker = TRUNCATION_MARKER.format(size - 2 * half)
    text = (
        head.decode("utf-8", errors="ignore") + marker + tail.decode("utf-8", errors="ignore")
    )
    return text, True


def _make_preexec(limits: Limits) -> Callable[[], None]:
    def preexec() -> None:
        resource.setrlimit(resource.RLIMIT_AS, (limits.memory_bytes, limits.memory_bytes))
        resource.setrlimit(
            resource.RLIMIT_FSIZE, (limits.file_size_bytes, limits.file_size_bytes)
        )
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))

    return preexec


def _looks_like_oom(stderr: str, exit_code: int, timed_out: bool) -> bool:
    if timed_out:
        return False
    if "MemoryError" in stderr:
        return True
    # SIGKILL without a timeout is how the kernel OOM killer shows up.
    return exit_code == -signal.SIGKILL


class LocalSandbox:
    def __init__(self, source_dir: str | os.PathLike[str], limits: Limits | None = None):
        self._source = Path(source_dir)
        self._limits = limits or Limits()
        self._base: Path | None = None
        self._root: Path | None = None
        self._pgids: set[int] = set()
        self._closed = False

    # -- lifecycle ---------------------------------------------------------------

    def start(self) -> None:
        if self._root is not None:
            return
        if not self._source.is_dir():
            raise SandboxError(f"source dir not found: {self._source}")
        self._base = Path(os.path.realpath(tempfile.mkdtemp(prefix="repro-sbx-")))
        root = self._base / "repo"
        shutil.copytree(self._source, root, symlinks=True)
        (self._base / "io").mkdir()
        self._root = root

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for pgid in list(self._pgids):
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(pgid, signal.SIGKILL)
        self._pgids.clear()
        if self._base is not None:
            shutil.rmtree(self._base, ignore_errors=True)

    def __enter__(self) -> LocalSandbox:
        self.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    # -- properties --------------------------------------------------------------

    @property
    def workdir(self) -> str:
        return str(self._live_root())

    @property
    def python(self) -> str:
        return sys.executable

    def _live_root(self) -> Path:
        if self._closed:
            raise SandboxError("sandbox is closed")
        if self._root is None:
            raise SandboxError("sandbox not started")
        return self._root

    # -- exec --------------------------------------------------------------------

    def exec(
        self,
        cmd: str | Sequence[str],
        timeout_s: float | None = None,
        env: Mapping[str, str] | None = None,
    ) -> ExecResult:
        root = self._live_root()
        assert self._base is not None
        timeout = self._limits.default_timeout_s if timeout_s is None else timeout_s
        argv = ["/bin/sh", "-c", cmd] if isinstance(cmd, str) else list(cmd)
        run_env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": str(self._base),
            "LANG": "C.UTF-8",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONIOENCODING": "utf-8",
        }
        if env:
            run_env.update(env)
        io_dir = Path(tempfile.mkdtemp(dir=self._base / "io"))
        out_path, err_path = io_dir / "stdout", io_dir / "stderr"
        start = time.monotonic()
        timed_out = False
        with out_path.open("wb") as out_f, err_path.open("wb") as err_f:
            proc = subprocess.Popen(
                argv,
                cwd=root,
                env=run_env,
                stdin=subprocess.DEVNULL,
                stdout=out_f,
                stderr=err_f,
                start_new_session=True,
                preexec_fn=_make_preexec(self._limits),
            )
            pgid = proc.pid  # new session: pgid == pid
            self._pgids.add(pgid)
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(pgid, signal.SIGKILL)
                proc.wait(timeout=KILL_GRACE_S)
        duration = time.monotonic() - start
        if timed_out:
            self._pgids.discard(pgid)
        stdout, t1 = _read_capped(out_path, self._limits.output_cap_bytes)
        stderr, t2 = _read_capped(err_path, self._limits.output_cap_bytes)
        shutil.rmtree(io_dir, ignore_errors=True)
        exit_code = proc.returncode if proc.returncode is not None else -signal.SIGKILL
        return ExecResult(
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
            duration_s=duration,
            timed_out=timed_out,
            truncated=t1 or t2,
            resource_failure="memory" if _looks_like_oom(stderr, exit_code, timed_out) else None,
        )

    # -- files -------------------------------------------------------------------

    def _resolve(self, path: str) -> Path:
        root = self._live_root()
        if not path or os.path.isabs(path):
            raise SandboxPathError(f"path must be relative and non-empty: {path!r}")
        full = Path(os.path.realpath(root / path))
        if full != root and root not in full.parents:
            raise SandboxPathError(f"path escapes the sandbox: {path!r}")
        return full

    def put_file(self, path: str, content: str | bytes) -> None:
        full = self._resolve(path)
        full.parent.mkdir(parents=True, exist_ok=True)
        data = content.encode("utf-8") if isinstance(content, str) else content
        full.write_bytes(data)

    def get_file(self, path: str) -> bytes:
        full = self._resolve(path)
        if not full.is_file():
            raise FileNotFoundError(path)
        return full.read_bytes()

    def delete_file(self, path: str) -> None:
        full = self._resolve(path)
        with contextlib.suppress(FileNotFoundError):
            full.unlink()

    def apply_patch(self, diff: str) -> PatchResult:
        root = self._live_root()
        if not diff.strip():
            return PatchResult(ok=False, error="empty patch")
        env = dict(os.environ)
        # Stop git from discovering an enclosing repository: paths stay relative to root.
        env["GIT_CEILING_DIRECTORIES"] = str(root.parent)
        for args in (["--check"], []):
            proc = subprocess.run(
                ["git", "apply", "--whitespace=nowarn", *args, "-"],
                cwd=root,
                input=diff.encode("utf-8"),
                capture_output=True,
                env=env,
                timeout=60,
            )
            if proc.returncode != 0:
                err = proc.stderr.decode("utf-8", errors="replace").strip()
                return PatchResult(ok=False, error=err or "git apply failed")
        return PatchResult(ok=True)

    def source_tree_hash(self) -> str:
        return tree_hash(str(self._live_root()))
