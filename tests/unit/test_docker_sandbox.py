"""DockerSandbox against a fake `docker` binary (TEST_SPEC M6, unit part)."""

from __future__ import annotations

import json
import os
import signal
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

import repro.sandbox.docker as dk
from repro.sandbox.docker import DockerSandbox, DockerSettings, reap, run_argv

FAKE = Path(__file__).resolve().parent.parent / "fake_docker" / "docker"


class Fake:
    def __init__(self, tmp: Path):
        self.log = tmp / "docker.log"
        self.state = tmp / "docker.json"

    def calls(self) -> list[list[str]]:
        if not self.log.exists():
            return []
        return [json.loads(x) for x in self.log.read_text().splitlines()]

    def containers(self) -> dict[str, dict[str, dict[str, str]]]:
        return json.loads(self.state.read_text())["containers"] if self.state.exists() else {}

    def add_images(self, *images: str) -> None:
        st = json.loads(self.state.read_text()) if self.state.exists() else {"containers": {}}
        st["images"] = list(images)
        st.setdefault("containers", {})
        self.state.write_text(json.dumps(st))


@pytest.fixture
def fake(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Fake]:
    f = Fake(tmp_path)
    monkeypatch.setenv("REPRO_DOCKER", str(FAKE))
    monkeypatch.setenv("FAKE_DOCKER_LOG", str(f.log))
    monkeypatch.setenv("FAKE_DOCKER_STATE", str(f.state))
    yield f


def test_t6_1_run_argv_has_every_hardening_flag() -> None:
    """T6.1: the docker run argv contains every flag from DESIGN 5.2."""
    argv = run_argv("img:1", "repro-x", DockerSettings(run_id="r1", env={"PATH": "/p"}))
    joined = " ".join(argv)
    for flag in ("--network none", "--memory 4g", "--memory-swap 4g", "--cpus 2",
                 "--pids-limit 512", "--cap-drop ALL", "--security-opt no-new-privileges",
                 "--label repro.managed=1", "--label repro.run_id=r1", "--workdir /testbed",
                 "--detach"):
        assert flag in joined, flag
    for forbidden in ("--volume", "-v ", "--mount", "docker.sock", "--privileged",
                      "--cap-add", "--network host", "--pid host"):
        assert forbidden not in joined, forbidden
    assert argv[-4:] == ["tail", "img:1", "-f", "/dev/null"]
    # Snapshot: changing the flags must be a deliberate edit of this test.
    assert argv == [
        "run", "--detach", "--name", "repro-x", "--label", "repro.managed=1", "--label",
        "repro.run_id=r1", "--network", "none", "--memory", "4g", "--memory-swap", "4g",
        "--cpus", "2", "--pids-limit", "512", "--cap-drop", "ALL", "--security-opt",
        "no-new-privileges", "--workdir", "/testbed", "--env", "PATH=/p", "--entrypoint",
        "tail", "img:1", "-f", "/dev/null"]


def test_exec_wraps_command_in_timeout_and_activation(fake: Fake) -> None:
    """T6.1: every command runs under an in-container `timeout -s KILL`."""
    s = DockerSettings(activate="source /opt/miniconda3/bin/activate testbed")
    with DockerSandbox("img", s) as sb:
        res = sb.exec(["python", "-c", "print(1)"], timeout_s=42, env={"PYTHONHASHSEED": "1"})
    assert res.exit_code == 0
    ex = next(c for c in fake.calls() if c[0] == "exec")
    assert "--env" in ex and "PYTHONHASHSEED=1" in ex
    script = ex[ex.index("-c") + 1]
    assert "timeout -s KILL 42" in script and "activate testbed" in script
    assert ex[-3:] == ["python", "-c", "print(1)"]
    assert fake.calls()[-1][:2] == ["rm", "--force"]


def test_t6_2_watchdog_kills_container(fake: Fake, monkeypatch: pytest.MonkeyPatch) -> None:
    """T6.2: when the in-container timeout does not fire, the host watchdog kills it."""
    monkeypatch.setenv("FAKE_DOCKER_HANG", "1")
    monkeypatch.setattr(dk, "WATCHDOG_GRACE_S", 0.5)
    sb = DockerSandbox("img")
    sb.start()
    res = sb.exec("sleep 100", timeout_s=1)
    assert res.timed_out
    assert sb.dead
    assert ["kill", sb.name] in fake.calls()
    with pytest.raises(dk.SandboxError):
        sb.exec("true")
    sb.close()


def test_t6_3_reaper_removes_labelled_containers(fake: Fake) -> None:
    """T6.3: the reaper removes labelled containers, optionally only one run's."""
    a = DockerSandbox("img", DockerSettings(run_id="r1"))
    b = DockerSandbox("img", DockerSettings(run_id="r2"))
    a.start()
    b.start()
    assert set(fake.containers()) == {a.name, b.name}
    assert reap("r1") == [a.name]
    assert set(fake.containers()) == {b.name}
    assert reap() == [b.name]
    assert fake.containers() == {}
    assert reap() == []


def test_t6_3_reaper_runs_on_sigterm(fake: Fake, tmp_path: Path) -> None:
    """T6.3: a batch stopped by SIGTERM reaps its containers on the way out."""
    from repro.config import EvalConfig
    from repro.eval.rows import ResultRow
    from repro.eval.run import BatchOptions, Runner, UnitOutput
    from repro.eval.store import LocalStore
    from repro.models import InstanceOutcome as O

    cfg = EvalConfig(config_id="d", kind="B0", llm="cassette")
    started = threading.Event()

    class Ex:
        def execute(self, iid: str) -> UnitOutput:
            DockerSandbox("img", DockerSettings(run_id="run-d")).start()  # leaks on purpose
            started.set()
            threading.Event().wait(5)
            raise AssertionError("unreachable")

        def infra_row(self, iid: str, reason: str) -> ResultRow:
            return ResultRow(run_id="run-d", instance_id=iid, config_id="d",
                             config_hash=cfg.hash, kind="B0", logical_model="m",
                             outcome=O.INFRA_ERROR, outcome_gated=O.INFRA_ERROR)

    def stop() -> None:
        started.wait(5)
        os.kill(os.getpid(), signal.SIGTERM)

    threading.Thread(target=stop).start()
    r = Runner(cfg, "run-d", ["a", "b"], LocalStore(tmp_path / "res", "run-d"), Ex(),
               options=BatchOptions(grace_s=0.2), on_stop=lambda: reap("run-d"))
    summary = r.run()
    assert summary.stop_reason == "signal" and summary.dropped == ["a"]
    assert fake.containers() == {}


def test_paths_outside_workdir_are_refused(fake: Fake) -> None:
    """T1.5 (Docker side): absolute and parent paths are refused before any docker call."""
    with DockerSandbox("img") as sb:
        for bad in ("/etc/passwd", "../x", "a/../../x", ""):
            with pytest.raises(dk.SandboxPathError):
                sb.put_file(bad, "x")
