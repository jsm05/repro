"""Where result rows live: a local directory or a `results` git branch (DESIGN 5.9).

Layout under the run directory (EVAL section 10):

    config.yaml           frozen copy of the config
    shard-<k>.jsonl       one row per finished unit
    deferrals-<k>.json    deferral counts for units with no row yet
    ledger-<k>.jsonl      every model call
    quota-<k>.json        quota state
    traces/<id>.jsonl     trace of each finished unit
    candidates/<id>.diff  emitted candidate of each finished unit

A row is written last for each unit, so a unit is either fully recorded or absent.
"""

from __future__ import annotations

import logging
import os
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from repro.eval.rows import ResultRow, read_rows

log = logging.getLogger(__name__)


class ResultsStore(Protocol):
    run_dir: Path

    def rows(self) -> list[ResultRow]: ...

    def commit_unit(self, shard: int, row: ResultRow, files: dict[str, str]) -> None: ...

    def save(self, message: str) -> None: ...

    def lock_path(self, shard: int) -> Path: ...


def shard_files(run_dir: Path) -> list[Path]:
    return sorted(run_dir.glob("shard-*.jsonl"))


def _write_unit(run_dir: Path, shard: int, row: ResultRow, files: dict[str, str]) -> None:
    for rel, text in files.items():
        p = run_dir / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    run_dir.mkdir(parents=True, exist_ok=True)
    with (run_dir / f"shard-{shard}.jsonl").open("a") as f:
        f.write(row.model_dump_json() + "\n")
        f.flush()
        os.fsync(f.fileno())


class LocalStore:
    def __init__(self, root: str | Path, run_id: str):
        self.root = Path(root)
        self.run_dir = self.root / run_id
        self.run_dir.mkdir(parents=True, exist_ok=True)

    def rows(self) -> list[ResultRow]:
        return read_rows(shard_files(self.run_dir))

    def commit_unit(self, shard: int, row: ResultRow, files: dict[str, str]) -> None:
        _write_unit(self.run_dir, shard, row, files)

    def save(self, message: str) -> None:
        return None

    def lock_path(self, shard: int) -> Path:
        return self.root / ".locks" / f"{self.run_dir.name}-shard-{shard}.lock"


class PushFailed(RuntimeError):
    pass


class GitStore:
    """Rows on a `results` branch: commit per unit, push with rebase-and-retry.

    Each shard writes only its own files, so a rebase never conflicts. A push that
    still fails after all attempts leaves the commit local; the next push carries it.
    """

    def __init__(
        self,
        remote: str,
        run_id: str,
        workdir: str | Path,
        *,
        branch: str = "results",
        attempts: int = 6,
        sleep: Callable[[float], None] = time.sleep,
        author: tuple[str, str] = ("github-actions[bot]",
                                   "41898282+github-actions[bot]@users.noreply.github.com"),
    ):
        self.remote = remote
        self.branch = branch
        self.attempts = attempts
        self.sleep = sleep
        self.workdir = Path(workdir)
        self.rel = Path("results") / run_id
        self.run_dir = self.workdir / self.rel
        self.pending = False
        self._init(author)

    def _git(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["git", "-C", str(self.workdir), *args], capture_output=True,
                              text=True, check=check)

    def _remote_has_branch(self) -> bool:
        res = self._git("ls-remote", "--heads", "origin", self.branch, check=False)
        return bool(res.stdout.strip())

    def _init(self, author: tuple[str, str]) -> None:
        self.workdir.mkdir(parents=True, exist_ok=True)
        if not (self.workdir / ".git").exists():
            self._git("init", "-q")
            self._git("remote", "add", "origin", self.remote)
        self._git("config", "user.name", author[0])
        self._git("config", "user.email", author[1])
        self._git("config", "commit.gpgsign", "false")
        if self._remote_has_branch():
            self._git("fetch", "-q", "origin", self.branch)
            self._git("checkout", "-q", "-B", self.branch, f"origin/{self.branch}")
        elif self._git("rev-parse", "--verify", "HEAD", check=False).returncode != 0:
            self._git("checkout", "-q", "--orphan", self.branch)
        self.run_dir.mkdir(parents=True, exist_ok=True)

    def sync(self) -> None:
        """Bring in rows other shards pushed since we started."""
        if self._remote_has_branch():
            self._git("pull", "-q", "--rebase", "--autostash", "origin", self.branch)

    def rows(self) -> list[ResultRow]:
        self.sync()
        return read_rows(shard_files(self.run_dir))

    def _commit(self, message: str) -> bool:
        self._git("add", "-A", str(self.rel))
        if self._git("diff", "--cached", "--quiet", check=False).returncode == 0:
            return False
        self._git("commit", "-q", "-m", message)
        return True

    def push(self) -> None:
        last = ""
        for attempt in range(self.attempts):
            res = self._git("push", "-q", "origin", f"HEAD:{self.branch}", check=False)
            if res.returncode == 0:
                self.pending = False
                return
            last = res.stderr.strip()
            log.warning("push attempt %d failed: %s", attempt + 1, last)
            if self._remote_has_branch():
                rebase = self._git("pull", "-q", "--rebase", "--autostash", "origin",
                                   self.branch, check=False)
                if rebase.returncode != 0:
                    self._git("rebase", "--abort", check=False)
            self.sleep(min(30.0, 2.0**attempt))
        self.pending = True
        raise PushFailed(last)

    def commit_unit(self, shard: int, row: ResultRow, files: dict[str, str]) -> None:
        _write_unit(self.run_dir, shard, row, files)
        self._commit(f"results: {self.rel.name} shard {shard} {row.instance_id} {row.outcome}")
        try:
            self.push()
        except PushFailed:
            log.error("rows committed locally but not pushed yet; will retry on next push")

    def save(self, message: str) -> None:
        if self._commit(message) or self.pending:
            try:
                self.push()
            except PushFailed:
                log.error("save committed locally but not pushed yet")

    def lock_path(self, shard: int) -> Path:
        return self.workdir / ".git" / "repro-locks" / f"{self.rel.name}-shard-{shard}.lock"
