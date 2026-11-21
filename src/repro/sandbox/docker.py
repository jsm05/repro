"""DockerSandbox: one locked-down container per sandbox, driven through the docker CLI.

Settings (DESIGN 5.2): no network, 4 GB memory and no extra swap, 2 CPUs, 512 PIDs, all
capabilities dropped, no new privileges, no host mounts, no Docker socket. Every
container carries the `repro.managed` and `repro.run_id` labels so a reaper can remove
leftovers after a crash. Commands run under an in-container `timeout -s KILL`, and a
host watchdog kills the whole container if that ever fails to fire.

The CLI is used instead of the Docker SDK so every action can be copied and rerun by
hand; the binary can be swapped with REPRO_DOCKER (tests use a fake one).
"""

from __future__ import annotations

import contextlib
import io
import logging
import os
import posixpath
import shutil
import subprocess
import tarfile
import tempfile
import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any

from repro.models import ExecResult, PatchResult
from repro.sandbox.base import Limits, SandboxError, SandboxPathError
from repro.sandbox.local import _read_capped

log = logging.getLogger(__name__)

MANAGED_LABEL = "repro.managed=1"
RUN_LABEL = "repro.run_id"
WATCHDOG_GRACE_S = 15.0
PATH_ESCAPE = 99


def docker_bin() -> str:
    return os.environ.get("REPRO_DOCKER", "docker")


def _docker(*args: str, input: bytes | None = None, timeout: float | None = 120.0,
            check: bool = False) -> subprocess.CompletedProcess[bytes]:
    proc = subprocess.run([docker_bin(), *args], input=input, capture_output=True,
                          timeout=timeout, check=False)
    if check and proc.returncode != 0:
        raise SandboxError(f"docker {' '.join(args[:2])} failed: "
                           f"{proc.stderr.decode(errors='replace').strip()[:500]}")
    return proc


@dataclass(frozen=True)
class DockerSettings:
    run_id: str = "adhoc"
    network: str = "none"
    memory: str = "4g"
    cpus: str = "2"
    pids: int = 512
    workdir: str = "/testbed"
    python: str = "python"
    # Shell snippet run before every command, e.g. conda activation; must be quiet.
    activate: str = ""
    shell: str = "/bin/bash"
    env: Mapping[str, str] = field(default_factory=dict)


def run_argv(image: str, name: str, s: DockerSettings) -> list[str]:
    """The exact `docker run` command. T6.1 snapshots it so no flag can quietly vanish."""
    argv = [
        "run", "--detach", "--init", "--name", name,
        "--label", MANAGED_LABEL, "--label", f"{RUN_LABEL}={s.run_id}",
        "--network", s.network,
        "--memory", s.memory, "--memory-swap", s.memory,
        "--cpus", s.cpus,
        "--pids-limit", str(s.pids),
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "--workdir", s.workdir,
    ]
    for k, v in sorted(s.env.items()):
        argv += ["--env", f"{k}={v}"]
    # No --volume, no --mount, no Docker socket: nothing from the host is visible.
    argv += ["--entrypoint", "tail", image, "-f", "/dev/null"]
    return argv


def treehash_source() -> str:
    return resources.files("repro.sandbox").joinpath("treehash.py").read_text("utf-8")


class DockerSandbox:
    def __init__(
        self,
        image: str,
        settings: DockerSettings | None = None,
        limits: Limits | None = None,
        source_dir: str | os.PathLike[str] | None = None,
        harness: Any = None,
    ):
        self.image = image
        self.settings = settings or DockerSettings()
        self._limits = limits or Limits()
        self._source = Path(source_dir) if source_dir is not None else None
        self.harness = harness  # test command and log parser for non-pytest repos
        self.name = ""
        self._started = False
        self._closed = False
        self.dead = False  # set when the watchdog had to kill the container

    # -- lifecycle -----------------------------------------------------------------

    def start(self) -> None:
        if self._started:
            return
        self.name = f"repro-{uuid.uuid4().hex[:12]}"
        _docker(*run_argv(self.image, self.name, self.settings), check=True)
        self._started = True
        if self._source is not None:
            w = self.settings.workdir
            _docker("exec", self.name, "mkdir", "-p", w, check=True)
            # Stream a tar owned by root: with all capabilities dropped, root inside the
            # container could not write files that keep the host user's ownership.
            _docker("cp", "-", f"{self.name}:{w}", input=_root_tar(self._source), check=True,
                    timeout=600)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self.name:
            with contextlib.suppress(subprocess.TimeoutExpired):
                _docker("rm", "--force", self.name, timeout=60)

    def __enter__(self) -> DockerSandbox:
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @property
    def workdir(self) -> str:
        self._live()
        return self.settings.workdir

    @property
    def python(self) -> str:
        return self.settings.python

    def _live(self) -> None:
        if self._closed:
            raise SandboxError("sandbox is closed")
        if not self._started:
            raise SandboxError("sandbox not started")
        if self.dead:
            raise SandboxError("container was killed by the host watchdog")

    # -- exec ----------------------------------------------------------------------

    def _wrap(self, inner: Sequence[str], timeout: float) -> list[str]:
        prefix = f"{self.settings.activate} >/dev/null 2>&1; " if self.settings.activate else ""
        script = f'{prefix}exec timeout -s KILL {int(max(1, round(timeout)))} "$@"'
        return [self.settings.shell, "-c", script, "repro", *inner]

    def exec(
        self,
        cmd: str | Sequence[str],
        timeout_s: float | None = None,
        env: Mapping[str, str] | None = None,
    ) -> ExecResult:
        self._live()
        timeout = self._limits.default_timeout_s if timeout_s is None else timeout_s
        inner = [self.settings.shell, "-c", cmd] if isinstance(cmd, str) else list(cmd)
        argv = [docker_bin(), "exec", "--interactive", "--workdir", self.settings.workdir]
        for k, v in sorted((env or {}).items()):
            argv += ["--env", f"{k}={v}"]
        argv += [self.name, *self._wrap(inner, timeout)]
        with tempfile.TemporaryDirectory(prefix="repro-dexec-") as tmp:
            out_p, err_p = Path(tmp) / "out", Path(tmp) / "err"
            start = time.monotonic()
            watchdog = False
            with out_p.open("wb") as out_f, err_p.open("wb") as err_f:
                proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=out_f,
                                        stderr=err_f)
                try:
                    proc.wait(timeout=timeout + WATCHDOG_GRACE_S)
                except subprocess.TimeoutExpired:
                    # The in-container timeout did not fire: kill the container itself.
                    watchdog = True
                    log.error("watchdog: killing container %s", self.name)
                    with contextlib.suppress(subprocess.TimeoutExpired):
                        _docker("kill", self.name, timeout=30)
                    self.dead = True
                    proc.kill()
                    proc.wait(timeout=30)
            duration = time.monotonic() - start
            cap = self._limits.output_cap_bytes
            stdout, t1 = _read_capped(out_p, cap)
            stderr, t2 = _read_capped(err_p, cap)
        code = proc.returncode if proc.returncode is not None else -9
        timed_out = watchdog or (code in (124, 137) and duration >= timeout * 0.9)
        oom = (not timed_out and code == 137) or "MemoryError" in stderr[-2000:]
        return ExecResult(stdout=stdout, stderr=stderr, exit_code=code, duration_s=duration,
                          timed_out=timed_out, truncated=t1 or t2,
                          resource_failure="memory" if oom else None)

    # -- files ---------------------------------------------------------------------

    def _abs(self, path: str) -> str:
        if not path or path.startswith("/") or posixpath.normpath(path).startswith(".."):
            raise SandboxPathError(f"path must be relative and inside the repo: {path!r}")
        return posixpath.join(self.settings.workdir, posixpath.normpath(path))

    def _guarded(self, path: str, action: str, *, input: bytes | None = None
                 ) -> subprocess.CompletedProcess[bytes]:
        """Run `action` on a path after checking, inside the container, that symlinks do
        not resolve outside the work dir."""
        self._live()
        full = self._abs(path)
        w = self.settings.workdir
        script = (f'p=$(realpath -m -- "$1"); case "$p" in "{w}"|"{w}"/*) ;; '
                  f'*) exit {PATH_ESCAPE};; esac; {action}')
        proc = _docker("exec", "--interactive", self.name, "/bin/sh", "-c", script, "repro",
                       full, input=input, timeout=300)
        if proc.returncode == PATH_ESCAPE:
            raise SandboxPathError(f"path escapes the sandbox: {path!r}")
        return proc

    def put_file(self, path: str, content: str | bytes) -> None:
        data = content.encode("utf-8") if isinstance(content, str) else content
        proc = self._guarded(path, 'mkdir -p "$(dirname "$p")" && cat > "$p"', input=data)
        if proc.returncode != 0:
            raise SandboxError(f"put_file {path}: {proc.stderr.decode(errors='replace')[:300]}")

    def get_file(self, path: str) -> bytes:
        proc = self._guarded(path, '[ -f "$p" ] || exit 3; cat "$p"')
        if proc.returncode == 3:
            raise FileNotFoundError(path)
        if proc.returncode != 0:
            raise SandboxError(f"get_file {path}: {proc.stderr.decode(errors='replace')[:300]}")
        return proc.stdout

    def delete_file(self, path: str) -> None:
        self._guarded(path, 'rm -f -- "$p"')

    def apply_patch(self, diff: str) -> PatchResult:
        self._live()
        if not diff.strip():
            return PatchResult(ok=False, error="empty patch")
        for args in (["--check"], []):
            proc = _docker("exec", "--interactive", "--workdir", self.settings.workdir,
                           self.name, "git", "apply", "--whitespace=nowarn", *args, "-",
                           input=diff.encode("utf-8"), timeout=120)
            if proc.returncode != 0:
                err = proc.stderr.decode("utf-8", errors="replace").strip()
                return PatchResult(ok=False, error=err or "git apply failed")
        return PatchResult(ok=True)

    def source_tree_hash(self) -> str:
        code = treehash_source() + "\nimport sys\nprint(tree_hash(sys.argv[1]))\n"
        res = self.exec([self.python, "-c", code, self.settings.workdir], timeout_s=300)
        if res.exit_code != 0:
            raise SandboxError(f"tree hash failed: {res.stderr[-300:]}")
        return res.stdout.strip()


def _root_tar(src: Path) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:

        def as_root(info: tarfile.TarInfo) -> tarfile.TarInfo:
            info.uid = info.gid = 0
            info.uname = info.gname = "root"
            return info

        for child in sorted(src.iterdir()):
            tar.add(child, arcname=child.name, filter=as_root)
    return buf.getvalue()


def docker_factory(image: str, settings: DockerSettings, limits: Limits | None = None,
                   harness: Any = None) -> Any:
    def make() -> DockerSandbox:
        sb = DockerSandbox(image, settings, limits, harness=harness)
        sb.start()
        return sb

    return make


# -- cleanup --------------------------------------------------------------------------


def reap(run_id: str | None = None) -> list[str]:
    """Remove every container Repro labelled (optionally only one run's). Returns ids."""
    args = ["ps", "--all", "--quiet", "--filter", f"label={MANAGED_LABEL}"]
    if run_id:
        args += ["--filter", f"label={RUN_LABEL}={run_id}"]
    proc = _docker(*args, timeout=60)
    ids = proc.stdout.decode().split()
    if ids:
        _docker("rm", "--force", *ids, timeout=300)
        log.warning("reaper removed %d container(s)", len(ids))
    return ids


def image_exists(image: str) -> bool:
    return _docker("image", "inspect", image, timeout=60).returncode == 0


def remove_images(images: Sequence[str]) -> None:
    if images:
        _docker("rmi", "--force", *images, timeout=600)


def prune() -> None:
    _docker("system", "prune", "--force", timeout=900)


def disk_free_gb(path: str = "/") -> float:
    return shutil.disk_usage(path).free / 1e9
