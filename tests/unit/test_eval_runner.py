"""Batch runner: resume, batch controls, deferral, signals, locking (DESIGN 5.9)."""

from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from repro.config import EvalConfig
from repro.eval.rows import ResultRow
from repro.eval.run import (
    BatchOptions,
    ConfigMismatch,
    LockHeld,
    Runner,
    ShardLock,
    UnitOutput,
    shard_of,
    shard_units,
)
from repro.eval.store import GitStore, LocalStore
from repro.llm.clock import FakeClock
from repro.llm.router import Deferred
from repro.models import InstanceOutcome as O

CFG = EvalConfig(config_id="t", kind="B0", llm="cassette")
IDS = [f"inst-{i}" for i in range(5)]


class FakeExecutor:
    """Scripted units. `script[id]` is a list of actions consumed one per attempt:
    "ok", "defer", "crash", ("sleep", s), ("advance", s)."""

    def __init__(self, script: dict[str, list[Any]] | None = None,
                 clock: FakeClock | None = None, advance_s: float = 0.0):
        self.script = script or {}
        self.clock = clock
        self.advance_s = advance_s
        self.calls: list[str] = []

    def _base(self, iid: str) -> dict[str, Any]:
        return dict(run_id="r", instance_id=iid, config_id=CFG.config_id,
                    config_hash=CFG.hash, kind="B0", logical_model="primary")

    def infra_row(self, iid: str, reason: str) -> ResultRow:
        return ResultRow(**self._base(iid), outcome=O.INFRA_ERROR, outcome_gated=O.INFRA_ERROR,
                         infra_error=reason)

    def execute(self, iid: str) -> UnitOutput:
        self.calls.append(iid)
        if self.clock is not None and self.advance_s:
            self.clock.advance(self.advance_s)
        actions = self.script.get(iid, [])
        action = actions.pop(0) if actions else "ok"
        if action == "defer":
            raise Deferred(0.0)
        if action == "crash":
            raise RuntimeError("worker died")
        if isinstance(action, tuple) and action[0] == "sleep":
            time.sleep(action[1])
        return UnitOutput(ResultRow(**self._base(iid), outcome=O.REPRODUCED,
                                    outcome_gated=O.REPRODUCED, tokens_in=10),
                          {f"traces/{iid}.jsonl": "{}\n"})


def runner(store: Any, ex: Any, **opts: Any) -> Runner:
    clock = opts.pop("clock", None)
    quota_ok = opts.pop("quota_ok", lambda: True)
    return Runner(CFG, "r", IDS, store, ex, options=BatchOptions(**opts), clock=clock,
                  quota_ok=quota_ok)


def ids_of(store: LocalStore) -> list[str]:
    return [r.instance_id for r in store.rows()]


def test_max_units_then_continue(tmp_path: Path) -> None:
    """T4.14: --max-units N stops after N units; the next run continues with the rest."""
    store = LocalStore(tmp_path, "r")
    ex = FakeExecutor()
    s = runner(store, ex, max_units=2).run()
    assert s.stop_reason == "max units" and s.written == IDS[:2] and s.remaining == 3
    s = runner(store, ex, max_units=2).run()
    assert s.written == IDS[2:4] and s.skipped_done == 2
    s = runner(store, ex, max_units=2).run()
    assert s.written == IDS[4:] and s.stop_reason == "all done" and s.remaining == 0
    assert ids_of(store) == IDS
    assert ex.calls == IDS


def test_deadline_uses_median_unit_time(tmp_path: Path) -> None:
    """T4.15: no new unit starts when the median unit time exceeds the time left."""
    clock = FakeClock()
    store = LocalStore(tmp_path, "r")
    ex = FakeExecutor(clock=clock, advance_s=100)
    s = runner(store, ex, clock=clock, deadline_at=clock.now() + 250).run()
    assert s.stop_reason == "deadline"
    assert s.written == IDS[:2]
    s = runner(store, ex, clock=clock, deadline_at=clock.now() - 1).run()
    assert s.stop_reason == "deadline" and s.written == []


def test_shards_partition_exactly_and_stably() -> None:
    """T4.16: the N shards are disjoint and complete, and the split is stable."""
    ids = [f"repo__name-{i}" for i in range(300)]
    parts = [shard_units(ids, k, 6) for k in range(6)]
    flat = [i for p in parts for i in p]
    assert sorted(flat) == sorted(ids) and len(flat) == len(set(flat))
    assert all(parts)  # every shard gets work
    assert parts == [shard_units(ids, k, 6) for k in range(6)]
    # Pinned value: changing the hash would silently reshuffle a run in progress.
    assert shard_of("django__django-11099", 6) == 3


def test_deferral_then_infra_error(tmp_path: Path) -> None:
    """T4.17: a deferred unit writes no row and reruns; the third deferral is INFRA_ERROR."""
    store = LocalStore(tmp_path, "r")
    ex = FakeExecutor({"inst-1": ["defer", "defer", "defer"]})
    s = runner(store, ex).run()
    assert s.deferred == ["inst-1"] and "inst-1" not in ids_of(store)
    assert len(store.rows()) == 4 and s.remaining == 1
    s = runner(store, ex).run()
    assert s.deferred == ["inst-1"] and len(store.rows()) == 4
    s = runner(store, ex).run()
    assert s.infra_errors == ["inst-1"]
    row = next(r for r in store.rows() if r.instance_id == "inst-1")
    assert row.outcome is O.INFRA_ERROR and row.deferrals == 3
    assert "deferred 3 times" in row.infra_error
    assert len(store.rows()) == 5
    assert runner(store, ex).run().written == []  # nothing loops forever


def test_deferral_count_recorded_on_eventual_success(tmp_path: Path) -> None:
    """T4.17: a unit that succeeds after a deferral records how many times it was deferred."""
    store = LocalStore(tmp_path, "r")
    ex = FakeExecutor({"inst-0": ["defer"]})
    runner(store, ex).run()
    runner(store, ex).run()
    row = next(r for r in store.rows() if r.instance_id == "inst-0")
    assert row.outcome is O.REPRODUCED and row.deferrals == 1


def test_crash_retried_once_then_infra_error(tmp_path: Path) -> None:
    """T4.7: a crash gives INFRA_ERROR after one retry; other units finish; N unchanged."""
    store = LocalStore(tmp_path, "r")
    ex = FakeExecutor({"inst-2": ["crash", "crash"], "inst-3": ["crash", "ok"]})
    s = runner(store, ex).run()
    assert s.stop_reason == "all done"
    rows = {r.instance_id: r for r in store.rows()}
    assert len(rows) == 5
    assert rows["inst-2"].outcome is O.INFRA_ERROR and "worker died" in rows["inst-2"].infra_error
    assert rows["inst-3"].outcome is O.REPRODUCED
    assert ex.calls.count("inst-2") == 2 and ex.calls.count("inst-3") == 2


def test_sigterm_lets_inflight_unit_finish(tmp_path: Path) -> None:
    """T4.18: SIGTERM stops new units; the in-flight unit finishes inside the grace period."""
    store = LocalStore(tmp_path, "r")
    ex = FakeExecutor({"inst-0": [("sleep", 0.6)]})
    threading.Timer(0.2, lambda: os.kill(os.getpid(), signal.SIGTERM)).start()
    s = runner(store, ex, grace_s=10).run()
    assert s.stop_reason == "signal"
    assert ids_of(store) == ["inst-0"] and ex.calls == ["inst-0"]
    assert signal.getsignal(signal.SIGTERM) is signal.SIG_DFL  # handler restored


def test_sigterm_drops_slow_unit_without_partial_row(tmp_path: Path) -> None:
    """T4.18: an in-flight unit that outlives the grace period is dropped, no partial row."""
    store = LocalStore(tmp_path, "r")
    ex = FakeExecutor({"inst-0": [("sleep", 3.0)]})
    threading.Timer(0.1, lambda: os.kill(os.getpid(), signal.SIGINT)).start()
    t0 = time.monotonic()
    s = runner(store, ex, grace_s=0.3).run()
    assert time.monotonic() - t0 < 2.0
    assert s.stop_reason == "signal" and s.dropped == ["inst-0"]
    assert store.rows() == []
    assert not (store.run_dir / "traces").exists()
    s = runner(store, FakeExecutor()).run()  # the dropped unit runs again later
    assert s.written == IDS


def test_second_process_on_same_shard_refuses(tmp_path: Path) -> None:
    """T4.19: a second process on the same (run, shard) refuses to start."""
    store = LocalStore(tmp_path, "r")
    held = ShardLock(store.lock_path(0))
    held.acquire()
    with pytest.raises(LockHeld):
        runner(store, FakeExecutor()).run()
    assert runner(store, FakeExecutor(), shard=1, shards=2).run().stop_reason == "all done"
    held.release()
    # A lock left by a dead process is taken over.
    store.lock_path(0).write_text("999999999")
    assert runner(store, FakeExecutor()).run().stop_reason == "all done"
    assert not store.lock_path(0).exists()


def test_stop_when_exhausted(tmp_path: Path) -> None:
    """T4.21: --stop-when-exhausted exits cleanly when no route has quota."""
    store = LocalStore(tmp_path, "r")
    left = iter([True, True, False])
    s = runner(store, FakeExecutor(), stop_when_exhausted=True, quota_ok=lambda: next(left)).run()
    assert s.stop_reason == "quota exhausted" and s.written == IDS[:2]
    s = runner(store, FakeExecutor(), quota_ok=lambda: False).run()  # flag off: keeps going
    assert s.stop_reason == "all done"


def test_config_change_on_same_run_is_refused(tmp_path: Path) -> None:
    """T4.10: a run started with one config refuses rows from another."""
    store = LocalStore(tmp_path, "r")
    runner(store, FakeExecutor(), max_units=1).run()
    other = CFG.model_copy(update={"k": 2})
    with pytest.raises(ConfigMismatch):
        Runner(other, "r", IDS, store, FakeExecutor()).run()


def test_kill_and_resume_with_fixtures(tmp_path: Path, fixture_source: Any) -> None:
    """T4.6: kill after k units, restart: finished units skipped, ledger unchanged, one row each."""
    from repro.eval.execute import BaselineExecutor, FixtureDataset, LLMHandle
    from repro.llm.budget import Ledger
    from repro.llm.fake import FakeLLM
    from tests.unit.test_baselines import gold_answer

    root = str(fixture_source.root)
    data = FixtureDataset(root)
    ids = data.ids()[:4]
    names = {fixture_source.load(n).instance.public.instance_id: n for n in fixture_source.names()}
    cfg = EvalConfig(config_id="b0-fx", kind="B0", llm="cassette", gate_timeout_s=30)
    store = LocalStore(tmp_path, "run1")
    ledger = Ledger(store.run_dir / "ledger-0.jsonl")
    executed: list[str] = []

    def factory(iid: str) -> LLMHandle:
        executed.append(iid)
        return LLMHandle(FakeLLM([gold_answer(fixture_source, names[iid])]), ledger=ledger)

    ex = BaselineExecutor(cfg, "run1", data, factory)

    class Killed(BaseException):
        pass

    done = {"n": 0}

    def kill_after_two() -> None:
        done["n"] += 1
        if done["n"] == 2:
            raise Killed

    r = Runner(cfg, "run1", ids, store, ex, on_unit_done=kill_after_two)
    with pytest.raises(Killed):
        r.run()
    assert [x.instance_id for x in store.rows()] == ids[:2]
    before, _ = ledger.read()
    finished_before = sorted((e.unit_id, e.total_tokens) for e in before)
    assert {u for u, _ in finished_before} == set(ids[:2])

    s = Runner(cfg, "run1", ids, store, ex).run()
    assert s.skipped_done == 2 and s.written == ids[2:]
    assert executed == ids  # nothing redone
    rows = store.rows()
    assert sorted(x.instance_id for x in rows) == sorted(ids)
    assert all(x.outcome is O.REPRODUCED for x in rows)
    after, _ = ledger.read()
    assert sorted((e.unit_id, e.total_tokens) for e in after if e.unit_id in ids[:2]) \
        == finished_before


# -- results branch -----------------------------------------------------------------


def _bare(tmp_path: Path) -> Path:
    bare = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
    return bare


def _row(iid: str) -> ResultRow:
    return FakeExecutor().execute(iid).row


def _read_remote(bare: Path, tmp_path: Path, name: str) -> GitStore:
    return GitStore(str(bare), "r", tmp_path / name, sleep=lambda s: None)


def test_results_branch_commit_per_unit_and_rebase(tmp_path: Path) -> None:
    """T4.20: one commit per unit; a stale shard's push lands via rebase-and-retry."""
    bare = _bare(tmp_path)
    s0 = GitStore(str(bare), "r", tmp_path / "w0", sleep=lambda s: None)
    s0.commit_unit(0, _row("a"), {"traces/a.jsonl": "{}\n"})
    s1 = GitStore(str(bare), "r", tmp_path / "w1", sleep=lambda s: None)
    s0.commit_unit(0, _row("b"), {})
    s1.commit_unit(1, _row("c"), {})  # s1 is behind: first push is rejected, then rebased
    check = _read_remote(bare, tmp_path, "check")
    assert sorted(r.instance_id for r in check.rows()) == ["a", "b", "c"]
    log = subprocess.run(["git", "-C", str(bare), "log", "--oneline", "results"],
                         capture_output=True, text=True, check=True).stdout.splitlines()
    assert len(log) == 3 and all("results: r shard" in x for x in log)
    assert (check.run_dir / "traces" / "a.jsonl").exists()


def test_results_branch_concurrent_shards(tmp_path: Path) -> None:
    """T4.20: two shards pushing at once both land, with no lost or duplicate rows."""
    bare = _bare(tmp_path)
    seed = GitStore(str(bare), "r", tmp_path / "seed", sleep=lambda s: None)
    seed.save("init")  # nothing to commit: branch is created by the first unit instead
    seed.commit_unit(0, _row("seed"), {})
    stores = [GitStore(str(bare), "r", tmp_path / f"w{k}", sleep=lambda s: None,
                       attempts=20) for k in (1, 2)]

    def work(k: int, store: GitStore) -> None:
        for i in range(4):
            store.commit_unit(k, _row(f"s{k}-{i}"), {})

    threads = [threading.Thread(target=work, args=(k + 1, s)) for k, s in enumerate(stores)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    rows = _read_remote(bare, tmp_path, "check").rows()
    ids = [r.instance_id for r in rows]
    assert sorted(ids) == sorted(["seed"] + [f"s{k}-{i}" for k in (1, 2) for i in range(4)])
    assert len(ids) == len(set(ids))


def test_results_branch_failed_push_retried_without_duplicates(tmp_path: Path) -> None:
    """T4.20: a failed push is retried and the row lands exactly once."""
    bare = _bare(tmp_path)
    hook = bare / "hooks" / "pre-receive"
    hook.write_text('#!/bin/sh\nf="$GIT_DIR/rejected-once"\n'
                    'if [ ! -f "$f" ]; then touch "$f"; echo "simulated outage" >&2; exit 1; fi\n'
                    'exit 0\n')
    hook.chmod(0o755)
    store = GitStore(str(bare), "r", tmp_path / "w", sleep=lambda s: None)
    store.commit_unit(0, _row("x"), {})
    assert (bare / "rejected-once").exists()
    rows = _read_remote(bare, tmp_path, "check").rows()
    assert [r.instance_id for r in rows] == ["x"]


def test_runner_on_results_branch_resumes_from_remote(tmp_path: Path) -> None:
    """T4.20: a fresh job (new workdir) resumes from rows and deferrals on the branch."""
    bare = _bare(tmp_path)
    ex = FakeExecutor({"inst-4": ["defer", "ok"]})
    s = Runner(CFG, "r", IDS, GitStore(str(bare), "r", tmp_path / "job1", sleep=lambda s: None),
               ex, options=BatchOptions(max_units=5)).run()
    assert s.deferred == ["inst-4"]
    s = Runner(CFG, "r", IDS, GitStore(str(bare), "r", tmp_path / "job2", sleep=lambda s: None),
               ex).run()
    assert s.written == ["inst-4"] and s.skipped_done == 4
    rows = _read_remote(bare, tmp_path, "check").rows()
    assert next(r for r in rows if r.instance_id == "inst-4").deferrals == 1
