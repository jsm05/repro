"""Resumable eval batches (DESIGN 5.9).

A work unit is (instance, config); one run has one config, so a unit is an instance
id. A unit is either done (it has a row) or not started. The runner skips done
units, runs the rest in order, and stops cleanly on --max-units, a deadline, quota
exhaustion, or SIGTERM/SIGINT.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import signal
import statistics
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import FrameType
from typing import Any, Literal, Protocol

from repro.config import EvalConfig
from repro.eval.rows import ResultRow
from repro.eval.store import ResultsStore
from repro.llm.clock import Clock, RealClock
from repro.llm.router import Deferred

log = logging.getLogger(__name__)


def shard_of(instance_id: str, shards: int) -> int:
    """Stable shard for an instance: the same on every machine and every run."""
    digest = hashlib.sha256(instance_id.encode()).hexdigest()
    return int(digest[:12], 16) % shards


def shard_units(ids: Sequence[str], shard: int, shards: int) -> list[str]:
    """Units of shard k of N, numbered from 1 like `--shard k/N`."""
    if not 1 <= shard <= shards:
        raise ValueError(f"bad shard {shard}/{shards}")
    return [i for i in ids if shard_of(i, shards) == shard - 1]


@dataclass(frozen=True)
class UnitOutput:
    row: ResultRow
    files: dict[str, str] = field(default_factory=dict)


class UnitExecutor(Protocol):
    def execute(self, instance_id: str) -> UnitOutput: ...

    def infra_row(self, instance_id: str, reason: str) -> ResultRow: ...


class LockHeld(RuntimeError):
    pass


class ShardLock:
    """One lock file per (run, shard). A lock whose process is gone is taken over."""

    def __init__(self, path: Path):
        self.path = path
        self.held = False

    @staticmethod
    def _alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for _ in range(2):
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                try:
                    pid = int(self.path.read_text().strip() or "0")
                except (OSError, ValueError):
                    pid = 0
                if pid and self._alive(pid):
                    raise LockHeld(f"{self.path} is held by process {pid}") from None
                self.path.unlink(missing_ok=True)
                continue
            with os.fdopen(fd, "w") as f:
                f.write(str(os.getpid()))
            self.held = True
            return
        raise LockHeld(f"could not take {self.path}")

    def release(self) -> None:
        if self.held:
            self.path.unlink(missing_ok=True)
            self.held = False


@dataclass(frozen=True)
class BatchOptions:
    shard: int = 1  # numbered from 1, like --shard k/N
    shards: int = 1
    max_units: int | None = None
    deadline_at: float | None = None  # epoch seconds; no unit starts that would end after it
    stop_when_exhausted: bool = False
    grace_s: float = 60.0  # how long in-flight work may finish after SIGTERM
    unit_retries: int = 1  # automatic retries after a crash before INFRA_ERROR


StopReason = Literal["all done", "max units", "deadline", "quota exhausted", "signal"]


@dataclass
class BatchSummary:
    stop_reason: StopReason = "all done"
    written: list[str] = field(default_factory=list)
    deferred: list[str] = field(default_factory=list)
    infra_errors: list[str] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)
    skipped_done: int = 0
    remaining: int = 0


class ConfigMismatch(RuntimeError):
    pass


class Runner:
    def __init__(
        self,
        config: EvalConfig,
        run_id: str,
        instance_ids: Sequence[str],
        store: ResultsStore,
        executor: UnitExecutor,
        *,
        options: BatchOptions | None = None,
        clock: Clock | None = None,
        quota_ok: Callable[[], bool] = lambda: True,
        on_unit_done: Callable[[], None] | None = None,
        on_stop: Callable[[], None] | None = None,
    ):
        self.config = config
        self.run_id = run_id
        self.ids = list(instance_ids)
        self.store = store
        self.executor = executor
        self.options = options or BatchOptions()
        self.clock = clock or RealClock()
        self.quota_ok = quota_ok
        self.on_unit_done = on_unit_done
        self.on_stop = on_stop  # cleanup when the batch ends for any reason (e.g. reaper)
        self._stop_at: float | None = None
        self.durations: list[float] = []

    # -- signals ----------------------------------------------------------------------

    def request_stop(self) -> None:
        if self._stop_at is None:
            log.warning("stop requested: no new units; in-flight work gets %.0f s",
                        self.options.grace_s)
            self._stop_at = time.monotonic()

    def _on_signal(self, signum: int, frame: FrameType | None) -> None:
        self.request_stop()

    # -- sidecar ----------------------------------------------------------------------

    @property
    def _deferrals_path(self) -> Path:
        return self.store.run_dir / f"deferrals-{self.options.shard}.json"

    def _load_deferrals(self) -> dict[str, int]:
        p = self._deferrals_path
        return json.loads(p.read_text()) if p.exists() else {}

    def _save_deferrals(self, counts: dict[str, int], message: str) -> None:
        self._deferrals_path.write_text(json.dumps(counts, indent=1, sort_keys=True) + "\n")
        self.store.save(message)

    def _check_config(self) -> None:
        frozen = self.store.run_dir / "config.yaml"
        if frozen.exists():
            old = EvalConfig.from_yaml(frozen)
            if old.hash != self.config.hash:
                raise ConfigMismatch(
                    f"run {self.run_id} was started with config hash {old.hash}; "
                    f"this config is {self.config.hash}. Use a new run id."
                )
        else:
            frozen.write_text(self.config.to_yaml())
            self.store.save(f"results: start run {self.run_id}")

    # -- one unit in a worker thread --------------------------------------------------

    def _run_unit(self, iid: str) -> tuple[str, Any]:
        box: dict[str, Any] = {}

        def work() -> None:
            try:
                box["out"] = ("ok", self.executor.execute(iid))
            except Deferred as exc:
                box["out"] = ("deferred", exc)
            except BaseException as exc:  # noqa: BLE001 - any crash becomes INFRA_ERROR
                box["out"] = ("error", exc)

        t = threading.Thread(target=work, name=f"unit-{iid}", daemon=True)
        t.start()
        while t.is_alive():
            t.join(0.05)
            if self._stop_at is not None and time.monotonic() - self._stop_at > \
                    self.options.grace_s:
                return ("dropped", None)
        return box["out"]  # type: ignore[no-any-return]

    def _deadline_hit(self) -> bool:
        if self.options.deadline_at is None:
            return False
        left = self.options.deadline_at - self.clock.now()
        if left <= 0:
            return True
        return bool(self.durations) and statistics.median(self.durations) > left

    # -- the batch --------------------------------------------------------------------

    def run(self) -> BatchSummary:
        opts = self.options
        lock = ShardLock(self.store.lock_path(opts.shard))
        lock.acquire()
        old_handlers: dict[int, Any] = {}
        if threading.current_thread() is threading.main_thread():
            for sig in (signal.SIGTERM, signal.SIGINT):
                old_handlers[sig] = signal.signal(sig, self._on_signal)
        try:
            return self._batch()
        finally:
            if self.on_stop is not None:
                try:
                    self.on_stop()
                except Exception:  # cleanup must never hide the real outcome
                    log.exception("on_stop cleanup failed")
            for signum, handler in old_handlers.items():
                signal.signal(signum, handler)
            lock.release()

    def _batch(self) -> BatchSummary:
        opts = self.options
        summary = BatchSummary()
        self._check_config()
        rows = self.store.rows()
        for r in rows:
            if r.config_hash != self.config.hash:
                raise ConfigMismatch(f"row for {r.instance_id} has config hash {r.config_hash}")
        done = {r.instance_id for r in rows}
        units = shard_units(self.ids, opts.shard, opts.shards)
        todo = [i for i in units if i not in done]
        summary.skipped_done = len(units) - len(todo)
        deferrals = self._load_deferrals()
        for started, iid in enumerate(todo):
            if self._stop_at is not None:
                summary.stop_reason = "signal"
                break
            if opts.max_units is not None and started >= opts.max_units:
                summary.stop_reason = "max units"
                break
            if self._deadline_hit():
                summary.stop_reason = "deadline"
                break
            if opts.stop_when_exhausted and not self.quota_ok():
                summary.stop_reason = "quota exhausted"
                break
            t0 = self.clock.now()
            for attempt in range(opts.unit_retries + 1):
                kind, value = self._run_unit(iid)
                if kind == "ok":
                    out: UnitOutput = value
                    row = out.row.model_copy(update={"deferrals": deferrals.get(iid, 0)})
                    self.store.commit_unit(opts.shard, row, out.files)
                    summary.written.append(iid)
                    break
                if kind == "dropped":
                    summary.dropped.append(iid)
                    summary.stop_reason = "signal"
                    finished = set(summary.written)
                    summary.remaining = sum(1 for i in todo if i not in finished)
                    return summary
                if kind == "deferred":
                    n = deferrals.get(iid, 0) + 1
                    deferrals[iid] = n
                    if n >= self.config.max_deferrals:
                        row = self.executor.infra_row(iid, f"deferred {n} times: {value}")
                        self.store.commit_unit(opts.shard, row.model_copy(
                            update={"deferrals": n}), {})
                        summary.infra_errors.append(iid)
                        summary.written.append(iid)
                    else:
                        summary.deferred.append(iid)
                    self._save_deferrals(deferrals, f"results: defer {iid} ({n})")
                    break
                # crash
                log.error("unit %s crashed (attempt %d): %r", iid, attempt + 1, value)
                if attempt == opts.unit_retries:
                    row = self.executor.infra_row(iid, f"crashed: {value!r}"[:500])
                    self.store.commit_unit(opts.shard, row.model_copy(
                        update={"deferrals": deferrals.get(iid, 0)}), {})
                    summary.infra_errors.append(iid)
                    summary.written.append(iid)
            self.durations.append(self.clock.now() - t0)
            if self.on_unit_done is not None:
                self.on_unit_done()
        else:
            summary.stop_reason = "all done"
        finished = set(summary.written)
        summary.remaining = sum(1 for i in todo if i not in finished)
        return summary
