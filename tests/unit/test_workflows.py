"""Static safety check over the GitHub workflows (T6.10)."""

from __future__ import annotations

import re
import sys
from pathlib import Path

from tests.helpers import ROOT

sys.path.insert(0, str(ROOT / "scripts"))
import check_workflows  # noqa: E402

WF = ROOT / ".github" / "workflows"


def test_t6_10_repo_workflows_pass() -> None:
    """T6.10: no pull_request_target, allowed triggers only, secrets only in model steps,
    the nightly job exits unless eval/active_run.yaml exists."""
    problems = [p for f in sorted(WF.glob("*.yml")) for p in check_workflows.check(f)]
    assert problems == []
    eval_wf = (WF / "eval.yml").read_text()
    assert "schedule" in eval_wf and "eval/active_run.yaml" in eval_wf
    assert 'echo "go=false"' in eval_wf


def test_t6_10_checker_catches_violations(tmp_path: Path) -> None:
    """T6.10: the checker flags each kind of unsafe workflow."""
    cases = {
        "prt.yml": "on: pull_request_target\nconcurrency: x\njobs: {}\n",
        "pr.yml": "on: [push, pull_request]\nconcurrency: x\njobs: {}\n",
        "jobenv.yml": ("on: push\nconcurrency: x\njobs:\n  a:\n    env:\n"
                       "      K: ${{ secrets.NVIDIA_API_KEY }}\n    steps: []\n"),
        "wrongstep.yml": ("on: push\nconcurrency: x\njobs:\n  a:\n    steps:\n"
                          "      - run: echo hi\n        env:\n"
                          "          K: ${{ secrets.NVIDIA_API_KEY }}\n"),
        "inline.yml": ("on: push\nconcurrency: x\njobs:\n  a:\n    steps:\n"
                       "      - run: echo ${{ secrets.NVIDIA_API_KEY }}\n"),
        "sched.yml": ("on:\n  schedule:\n    - cron: '0 0 * * *'\nconcurrency: x\n"
                      "jobs: {}\n"),
        "noconc.yml": "on: push\njobs: {}\n",
    }
    for name, text in cases.items():
        (tmp_path / name).write_text(text)
    found = {name: check_workflows.check(tmp_path / name) for name in cases}
    assert all(found[n] for n in cases), {n: p for n, p in found.items() if not p}
    assert "pull_request_target" in found["prt.yml"][0]
    assert "pull_request" in found["pr.yml"][0]
    assert "job-level" in found["jobenv.yml"][0]
    assert "does not call models" in found["wrongstep.yml"][0]
    assert "outside the step env" in found["inline.yml"][0]
    assert "active_run" in found["sched.yml"][0]
    assert "concurrency" in found["noconc.yml"][0]
    ok = ("on: push\nconcurrency: x\njobs:\n  a:\n    steps:\n"
          "      - run: repro eval --config c\n        env:\n"
          "          K: ${{ secrets.NVIDIA_API_KEY }}\n")
    (tmp_path / "ok.yml").write_text(ok)
    assert check_workflows.check(tmp_path / "ok.yml") == []


def test_harness_ref_is_pinned_everywhere() -> None:
    """T6.10: every workflow uses the same pinned SWT-bench commit as the code."""
    from repro.datasets.swt_harness import SWT_BENCH_REF

    refs = set()
    for f in WF.glob("*.yml"):
        refs |= set(re.findall(r"checkout -q ([0-9a-f]{40})", f.read_text()))
        refs |= set(re.findall(r"SWT_BENCH_REF: ([0-9a-f]{40})", f.read_text()))
    assert refs == {SWT_BENCH_REF}
