"""Run pytest inside a sandbox and turn its report into per-test outcomes.

Counting rule (DESIGN 5.3): only FAILED in the call phase counts as "fails".
ERROR (collection, setup, teardown, import), SKIPPED, XFAIL and TIMEOUT never do,
so a broken or disabled test can never pose as a reproduction.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from collections.abc import Mapping, Sequence
from importlib import resources
from typing import Any

from pydantic import BaseModel, ConfigDict

from repro.models import ExecResult, FailureDetail, Frame, Phase, TestOutcome, TestStatus
from repro.sandbox.base import Sandbox

SCRATCH_DIR = ".repro"
PLUGIN_MODULE = "repro_plugin"
RESOURCE_EXC = {"MemoryError"}
_EXC_LINE = re.compile(r"^(?:E\s+)?([A-Za-z_][\w.]*(?:Error|Exception|Exit|Interrupt))\b")


class TestRunResult(BaseModel):
    __test__ = False

    model_config = ConfigDict(frozen=True)

    outcomes: dict[str, TestOutcome]
    exec: ExecResult
    timed_out: bool


def plugin_source() -> str:
    return resources.files("repro.runner").joinpath("repro_plugin.py").read_text("utf-8")


def build_pytest_cmd(python: str, workdir: str, test_ids: Sequence[str]) -> list[str]:
    return [
        python, "-m", "pytest",
        "-p", PLUGIN_MODULE,
        "-p", "no:cacheprovider",
        "-p", "no:randomly",
        "--continue-on-collection-errors",
        "--rootdir", workdir,
        "-q", "--tb=short",
        *test_ids,
    ]


def run_tests(
    sandbox: Sandbox,
    test_ids: Sequence[str],
    *,
    timeout_s: float = 180.0,
    hash_seed: int = 0,
    trace: bool = False,
    extra_env: Mapping[str, str] | None = None,
) -> TestRunResult:
    """Run `test_ids` (node ids or files) once and parse the outcomes.

    trace=True records which repo functions ran during each test's call phase (G5).
    """
    if not test_ids:
        raise ValueError("no tests requested")
    harness = getattr(sandbox, "harness", None)
    if harness is not None and harness.framework != "pytest":
        from repro.runner.harness_runner import run_harness_tests

        return run_harness_tests(sandbox, harness, list(test_ids), timeout_s=timeout_s,
                                 hash_seed=hash_seed, trace=trace)
    workdir = sandbox.workdir
    sandbox.put_file(f"{SCRATCH_DIR}/{PLUGIN_MODULE}.py", plugin_source())
    report_rel = f"{SCRATCH_DIR}/report-{uuid.uuid4().hex}.jsonl"
    env = {
        "PYTHONPATH": f"{workdir}/{SCRATCH_DIR}",
        "REPRO_REPORT": f"{workdir}/{report_rel}",
        "PYTHONHASHSEED": str(hash_seed),
    }
    if trace:
        env.update({"REPRO_TRACE": "1", "REPRO_ROOT": workdir})
    if extra_env:
        env.update(extra_env)
    result = sandbox.exec(build_pytest_cmd(sandbox.python, workdir, test_ids), timeout_s, env)
    try:
        report = sandbox.get_file(report_rel).decode("utf-8", errors="replace")
    except FileNotFoundError:
        report = ""
    sandbox.delete_file(report_rel)

    outcomes = parse_report(report, workdir, test_ids)
    if result.timed_out:
        outcomes = _mark_timeouts(outcomes, test_ids)
    elif result.resource_failure and not outcomes:
        outcomes = {
            t: TestOutcome(test_id=t, status=TestStatus.ERROR, note="resource: memory")
            for t in test_ids
        }
    return TestRunResult(outcomes=outcomes, exec=result, timed_out=result.timed_out)


# -- parsing --------------------------------------------------------------------------


def _rel_frames(frames: list[dict[str, Any]], workdir: str) -> list[Frame]:
    """Keep only frames inside the repo, with repo-relative paths."""
    root = os.path.normpath(workdir)
    out = []
    for f in frames:
        path = str(f.get("path", ""))
        full = os.path.normpath(path if os.path.isabs(path) else os.path.join(root, path))
        if full.startswith(root + os.sep):
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            out.append(Frame(path=rel, lineno=int(f.get("lineno") or 0), func=str(f.get("func"))))
    return out


def _failure(rec: dict[str, Any], phase: Phase, workdir: str) -> FailureDetail:
    exc = rec.get("exc") or {}
    return FailureDetail(
        phase=phase,
        exc_type=str(exc.get("type", "")),
        message=str(exc.get("message", "")),
        frames=_rel_frames(exc.get("frames") or [], workdir),
        text=str(rec.get("longrepr", ""))[-4000:],
    )


def _collect_failure(longrepr: str) -> FailureDetail:
    exc_type = ""
    for line in longrepr.splitlines():
        m = _EXC_LINE.match(line.strip())
        if m:
            exc_type = m.group(1)
    return FailureDetail(phase="collection", exc_type=exc_type, text=longrepr[-4000:])


def _classify(nodeid: str, phases: dict[str, dict[str, Any]], workdir: str) -> TestOutcome:
    setup, call, teardown = phases.get("setup"), phases.get("call"), phases.get("teardown")
    duration = sum(float(p.get("duration", 0.0)) for p in phases.values())
    status: TestStatus
    failure: FailureDetail | None = None
    note = ""
    if setup and setup["outcome"] == "failed":
        status, failure = TestStatus.ERROR, _failure(setup, "setup", workdir)
    elif setup and setup["outcome"] == "skipped":
        status = TestStatus.XFAIL if setup["wasxfail"] else TestStatus.SKIPPED
    elif call is None:
        status, note = TestStatus.ERROR, "test did not run"
    elif call["outcome"] == "passed":
        status = TestStatus.XPASS if call["wasxfail"] else TestStatus.PASSED
    elif call["outcome"] == "skipped":
        status = TestStatus.XFAIL if call["wasxfail"] else TestStatus.SKIPPED
    elif call.get("exc") is None and "XPASS(strict)" in str(call.get("longrepr", "")):
        status = TestStatus.XPASS
    else:
        failure = _failure(call, "call", workdir)
        if failure.exc_type in RESOURCE_EXC:
            status, note = TestStatus.ERROR, "resource: memory"
        else:
            status = TestStatus.FAILED
    if (
        teardown
        and teardown["outcome"] == "failed"
        and status in (TestStatus.PASSED, TestStatus.XPASS, TestStatus.SKIPPED, TestStatus.XFAIL)
    ):
        status, failure = TestStatus.ERROR, _failure(teardown, "teardown", workdir)
    calls = call.get("repo_calls") if call else None
    return TestOutcome(
        test_id=nodeid,
        status=status,
        duration_s=duration,
        failure=failure,
        note=note,
        repo_calls=[str(c) for c in calls] if isinstance(calls, list) else None,
    )


def parse_report(report: str, workdir: str, requested: Sequence[str]) -> dict[str, TestOutcome]:
    tests: dict[str, dict[str, dict[str, Any]]] = {}
    collect_errors: dict[str, str] = {}
    for line in report.splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if rec.get("kind") == "collect":
            collect_errors[str(rec.get("nodeid", ""))] = str(rec.get("longrepr", ""))
        elif rec.get("kind") == "test":
            tests.setdefault(str(rec["nodeid"]), {})[str(rec["when"])] = rec

    outcomes = {nid: _classify(nid, phases, workdir) for nid, phases in tests.items()}
    for cid, longrepr in collect_errors.items():
        outcomes.setdefault(
            cid or "<collection>",
            TestOutcome(
                test_id=cid or "<collection>",
                status=TestStatus.ERROR,
                failure=_collect_failure(longrepr),
            ),
        )

    for req in requested:
        if req in outcomes or any(nid.startswith(req.rstrip("/") + "::") for nid in outcomes):
            continue
        parent = next(
            (c for c in collect_errors if c and (req.startswith(c + "::") or req.startswith(c))),
            None,
        )
        if parent is not None:
            outcomes[req] = TestOutcome(
                test_id=req,
                status=TestStatus.ERROR,
                failure=_collect_failure(collect_errors[parent]),
                note="collection error",
            )
        else:
            outcomes[req] = TestOutcome(
                test_id=req, status=TestStatus.ERROR, note="requested test was not collected"
            )
    return outcomes


def _mark_timeouts(
    outcomes: dict[str, TestOutcome], requested: Sequence[str]
) -> dict[str, TestOutcome]:
    """After a timeout, anything that did not finish is TIMEOUT, never FAILED or ERROR."""
    out = dict(outcomes)
    for tid, o in outcomes.items():
        if o.note in ("test did not run", "requested test was not collected"):
            out[tid] = TestOutcome(test_id=tid, status=TestStatus.TIMEOUT, note="run timed out")
    for req in requested:
        if req not in out and not any(n.startswith(req + "::") for n in out):
            out[req] = TestOutcome(test_id=req, status=TestStatus.TIMEOUT, note="run timed out")
    return out
