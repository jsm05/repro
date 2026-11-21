"""Real-Docker tests (TEST_SPEC M6, `pytest -m docker`, run in docker-tests.yml).

They need a Docker daemon and the small test image from tests/docker/Dockerfile, named
by REPRO_DOCKER_TEST_IMAGE. The gold-agreement test (T6.D8) also needs the SWT-bench
harness installed and the pilot records exported (REPRO_PILOT_RECORDS).
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from repro.sandbox.docker import DockerSandbox, DockerSettings, reap
from tests.helpers import ROOT, tiny_repo

IMAGE = os.environ.get("REPRO_DOCKER_TEST_IMAGE", "")
pytestmark = [pytest.mark.docker,
              pytest.mark.skipif(not IMAGE, reason="REPRO_DOCKER_TEST_IMAGE not set")]


def box(tmp_path: Path, run_id: str = "dtest", **kw: object) -> DockerSandbox:
    sb = DockerSandbox(IMAGE, DockerSettings(run_id=run_id, **kw),  # type: ignore[arg-type]
                       source_dir=tiny_repo(tmp_path))
    sb.start()
    return sb


def containers(run_id: str) -> list[str]:
    out = subprocess.run(["docker", "ps", "-aq", "--filter", f"label=repro.run_id={run_id}"],
                         capture_output=True, text=True, check=True).stdout
    return out.split()


def test_t6_d1_contract_suite_on_docker() -> None:
    """T6.D1: contract tests T1.1 to T1.8 pass against DockerSandbox."""
    res = subprocess.run([sys.executable, "-m", "pytest", "-o", "addopts=", "-p",
                          "no:randomly", "-p", "no:cacheprovider",
                          str(ROOT / "tests" / "contract"), "-k", "docker"],
                         capture_output=True, text=True, cwd=ROOT, check=False)
    assert res.returncode == 0, res.stdout[-3000:] + res.stderr[-2000:]
    assert "8 passed" in res.stdout, res.stdout[-2000:]


def test_t6_d2_no_network(tmp_path: Path) -> None:
    """T6.D2: an HTTP request and a DNS lookup from inside fail."""
    with box(tmp_path) as sb:
        http = sb.exec(["python", "-c", "import urllib.request as u; "
                        "u.urlopen('http://example.com', timeout=5)"], timeout_s=30)
        dns = sb.exec(["python", "-c", "import socket; socket.gethostbyname('example.com')"],
                      timeout_s=30)
        ip = sb.exec(["python", "-c", "import socket; "
                      "socket.create_connection(('1.1.1.1', 80), timeout=5)"], timeout_s=30)
    assert http.exit_code != 0 and dns.exit_code != 0 and ip.exit_code != 0


def test_t6_d3_memory_limit(tmp_path: Path) -> None:
    """T6.D3: allocating past the memory limit is killed and reported as a resource failure."""
    with box(tmp_path, memory="256m") as sb:
        r = sb.exec(["python", "-c", "b = bytearray(2 * 1024 ** 3)"], timeout_s=60)
    assert r.exit_code != 0 and not r.timed_out and r.resource_failure == "memory"


def test_t6_d4_fork_bomb_is_contained(tmp_path: Path) -> None:
    """T6.D4: a fork bomb hits the PID limit, the container is removed, the host is fine."""
    sb = box(tmp_path, run_id="forkbomb")
    r = sb.exec("bomb() { bomb | bomb & }; bomb; sleep 5; echo survived", timeout_s=20)
    assert r.timed_out or r.exit_code != 0 or "survived" in r.stdout
    sb.close()
    assert containers("forkbomb") == []
    assert subprocess.run(["true"], check=False).returncode == 0  # host can still fork


def test_t6_d5_no_privileges_or_host_access(tmp_path: Path) -> None:
    """T6.D5: no host mounts, no Docker socket, effective capabilities zero."""
    with box(tmp_path) as sb:
        caps = sb.exec("grep CapEff /proc/self/status", timeout_s=10).stdout
        sock = sb.exec("test -e /var/run/docker.sock", timeout_s=10).exit_code
        mounts = sb.exec("cat /proc/mounts", timeout_s=10).stdout
        nnp = sb.exec("grep NoNewPrivs /proc/self/status", timeout_s=10).stdout
    assert caps.split()[-1] == "0000000000000000"
    assert sock != 0
    assert "/home/runner" not in mounts and "docker.sock" not in mounts
    assert nnp.split()[-1] == "1"


def test_t6_d6_git_has_one_commit_and_no_remote(tmp_path: Path) -> None:
    """T6.D6: after hardening, `git log --all` shows one commit and there are no remotes."""
    from repro.datasets.swtbench import harden

    repo = tiny_repo(tmp_path)
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run([*git, "init", "-q"], cwd=repo, check=True)
    subprocess.run([*git, "add", "-A"], cwd=repo, check=True)
    subprocess.run([*git, "commit", "-qm", "base"], cwd=repo, check=True)
    (repo / "pkg" / "future.py").write_text("FIX = 'from the future'\n")
    subprocess.run([*git, "add", "-A"], cwd=repo, check=True)
    subprocess.run([*git, "commit", "-qm", "the fix"], cwd=repo, check=True)
    subprocess.run([*git, "reset", "-q", "--hard", "HEAD~1"], cwd=repo, check=True)
    subprocess.run([*git, "remote", "add", "origin", "https://example.com/x.git"], cwd=repo,
                   check=True)
    src = DockerSandbox(IMAGE, DockerSettings(run_id="harden-src"), source_dir=repo)
    src.start()
    subprocess.run(["docker", "commit", src.name, "repro-test-unhardened:1"], check=True)
    src.close()
    tag = harden("repro-test-unhardened:1", "repro-test-hardened:1", "harden-test")
    with DockerSandbox(tag, DockerSettings(run_id="harden-test")) as sb:
        log = sb.exec("git log --all --oneline", timeout_s=30).stdout.strip().splitlines()
        remotes = sb.exec("git remote", timeout_s=30).stdout.strip()
        future = sb.exec("git log --all -p | grep -c 'from the future' || true",
                         timeout_s=30).stdout.strip()
        objects = sb.exec("git count-objects -v | grep '^count\\|^in-pack'", timeout_s=30)
    assert len(log) == 1 and remotes == "" and future == "0", (log, remotes, future,
                                                             objects.stdout)
    subprocess.run(["docker", "rmi", "-f", tag, "repro-test-unhardened:1"], check=False)


def test_t6_d7_kill_9_leaves_nothing_after_reaper(tmp_path: Path) -> None:
    """T6.D7: kill -9 of the runner mid-instance; after the reaper no labelled container is left."""
    script = textwrap.dedent(f"""
        import sys, time
        sys.path.insert(0, {str(ROOT / 'src')!r})
        from repro.sandbox.docker import DockerSandbox, DockerSettings
        for _ in range(2):
            DockerSandbox({IMAGE!r}, DockerSettings(run_id="killed")).start()
        print("started", flush=True)
        time.sleep(600)
    """)
    proc = subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE, text=True)
    assert proc.stdout is not None and proc.stdout.readline().strip() == "started"
    assert len(containers("killed")) == 2
    os.kill(proc.pid, signal.SIGKILL)
    proc.wait(timeout=30)
    assert len(containers("killed")) == 2  # nothing cleaned up the killed process's work
    reap("killed")
    time.sleep(1)
    assert containers("killed") == []


def test_t6_d9_api_keys_never_enter_a_container(tmp_path: Path,
                                                monkeypatch: pytest.MonkeyPatch) -> None:
    """T6.D9: the API key variables set on the host are not present inside any container."""
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-host-only-secret")
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-host-only-secret")
    with box(tmp_path) as sb:
        env = sb.exec("env", timeout_s=10).stdout
        proc_env = sb.exec("cat /proc/1/environ | tr '\\0' '\\n'", timeout_s=10).stdout
    for text in (env, proc_env):
        assert "host-only-secret" not in text and "NVIDIA_API_KEY" not in text


@pytest.mark.skipif(not os.environ.get("REPRO_PILOT_RECORDS"),
                    reason="pilot records not exported (REPRO_PILOT_RECORDS)")
def test_t6_d8_gold_agreement_on_pilot() -> None:
    """T6.D8: gold tests are F→P in our oracle on the pilot, and the official harness agrees."""
    out = ROOT / "eval" / "gold-agreement.md"
    res = subprocess.run([sys.executable, str(ROOT / "scripts" / "gold_agreement.py"),
                          str(ROOT / "eval" / "samples" / "pilot_10.txt"), "--records",
                          os.environ["REPRO_PILOT_RECORDS"], "--out", str(out)],
                         cwd=ROOT, capture_output=True, text=True, check=False)
    print(res.stdout[-5000:])
    assert out.exists(), res.stderr[-3000:]
    assert res.returncode == 0, out.read_text()
