"""Tests for repos that do not run under pytest (Django's runtests.py, sympy's bin/test).

The command and the status parser come from the SWT-bench harness (DESIGN 5.3: no
parsers of our own for statuses). Failure details for the gate (exception type, repo
frames) are read from the tracebacks those runners print, and repo calls for G5 come
from a sitecustomize tracer (`repro_trace.py`). Unittest reports an exception raised in
the test body as ERROR; that is the same event as a pytest call-phase failure, so it
counts as FAILED here. A requested test missing from the log did not run (ERROR).
"""

from __future__ import annotations

import json
import logging
import os
import re
import shlex
import uuid
from importlib import resources
from typing import Any

from repro.datasets import swt_harness as swt
from repro.models import FailureDetail, Frame, TestOutcome, TestStatus
from repro.runner.pytest_runner import SCRATCH_DIR, TestRunResult
from repro.sandbox.base import Sandbox

_DJANGO_BLOCK = re.compile(
    r"^(?:FAIL|ERROR): (?P<name>[^\n]+)\n(?:[^\n]*\n)??-{20,}\n(?P<tb>.*?)"
    r"(?=^={20,}|^-{20,}\nRan |\Z)", re.M | re.S)
_SYMPY_BLOCK = re.compile(
    r"^_{3,} (?P<path>\S+?\.py):(?P<name>\w+) _{3,}\n(?P<tb>.*?)(?=^_{3,} |^={5,}|\Z)",
    re.M | re.S)
_FRAME = re.compile(r'File "(?P<path>[^"]+)", line (?P<line>\d+), in (?P<func>\S+)')
_EXC = re.compile(r"^(?P<type>[A-Za-z_][\w.]*)(?::\s?(?P<msg>.*))?$")
STATUS = {"PASSED": TestStatus.PASSED, "FAILED": TestStatus.FAILED,
          "ERROR": TestStatus.FAILED, "SKIPPED": TestStatus.SKIPPED}


def trace_source() -> str:
    return resources.files("repro.runner").joinpath("repro_trace.py").read_text("utf-8")


def parse_traceback(tb: str, workdir: str) -> FailureDetail:
    root = os.path.normpath(workdir)
    frames = []
    for m in _FRAME.finditer(tb):
        full = os.path.normpath(m.group("path"))
        if full.startswith(root + os.sep):
            frames.append(Frame(path=os.path.relpath(full, root).replace(os.sep, "/"),
                                lineno=int(m.group("line")), func=m.group("func")))
    exc_type, message = "", ""
    for line in reversed(tb.rstrip().splitlines()):
        if not line or line[0].isspace():
            continue
        em = _EXC.match(line.strip())
        if em and ("Error" in em.group("type") or "Exception" in em.group("type")
                   or em.group("msg") is not None):
            exc_type, message = em.group("type"), (em.group("msg") or "")
            break
    return FailureDetail(phase="call", exc_type=exc_type, message=message[:2000],
                         frames=frames, text=tb[-4000:])


def _label(framework: str, test_id: str) -> str:
    if framework == "django":
        from repro.datasets.swtbench import django_label

        return django_label(test_id)
    return test_id.split("::", 1)[0]


def _id_for_name(framework: str, name: str, test_ids: list[str],
                 defs: dict[str, str] | None = None) -> str | None:
    """Map a runner's test name to our id. Requested entries may be test ids or whole
    files (the oracle asks for files); a test in a requested file gets its own id."""
    files = {t for t in test_ids if "::" not in t}
    if framework == "django":
        from repro.datasets.swtbench import django_id

        tid = django_id(name)
        if tid is None:
            return None
        return tid if tid in test_ids or tid.split("::")[0] in files else None
    for t in test_ids:  # sympy: names are bare function names
        if "::" in t and t.split("::")[-1] == name:
            return t
    for path in sorted(files):
        if re.search(rf"^def {re.escape(name)}\s*\(", (defs or {}).get(path, ""), re.M):
            return f"{path}::{name}"
    if len(files) == 1 and not defs:
        return f"{next(iter(files))}::{name}"
    return None


def parse_harness_run(framework: str, statuses: dict[str, str], log: str,
                      test_ids: list[str], workdir: str,
                      calls: dict[str, list[str]] | None,
                      defs: dict[str, str] | None = None) -> dict[str, TestOutcome]:
    by_id: dict[str, str] = {}
    for name, st in statuses.items():
        tid = _id_for_name(framework, name, test_ids, defs)
        if tid is not None:
            by_id[tid] = st
    blocks: dict[str, str] = {}
    pattern = _DJANGO_BLOCK if framework == "django" else _SYMPY_BLOCK
    for m in pattern.finditer(log):
        tid = _id_for_name(framework, m.group("name").strip(), test_ids, defs)
        if tid is not None:
            blocks[tid] = m.group("tb")
    wanted = [t for t in test_ids if "::" in t]
    wanted += sorted(t for t in by_id if t not in wanted)
    out: dict[str, TestOutcome] = {}
    for t in wanted:
        raw = by_id.get(t)
        repo_calls = None
        if calls is not None:
            # Django: per test, keyed by the unittest id. sympy's runner has no hook, so
            # every repo call in the run counts for each test (a weaker G5, D-M6-4).
            key = _label(framework, t) if framework == "django" else "*"
            repo_calls = list(calls.get(key, []))
        if raw is None:
            out[t] = TestOutcome(test_id=t, status=TestStatus.ERROR, note="not in test log",
                                 repo_calls=repo_calls)
            continue
        status = STATUS.get(raw, TestStatus.ERROR)
        failure = None
        if status is TestStatus.FAILED:
            failure = parse_traceback(blocks.get(t, ""), workdir)
        out[t] = TestOutcome(test_id=t, status=status, failure=failure, repo_calls=repo_calls)
    return out


def read_calls(text: str) -> dict[str, list[str]]:
    merged: dict[str, set[str]] = {}
    for line in text.splitlines():
        if line.strip():
            for k, v in json.loads(line).items():
                merged.setdefault(k, set()).update(v)
    return {k: sorted(v) for k, v in merged.items()}


def run_harness_tests(sandbox: Sandbox, harness: Any, test_ids: list[str], *,
                      timeout_s: float, hash_seed: int, trace: bool) -> TestRunResult:
    fw = harness.framework
    workdir = sandbox.workdir
    labels = sorted({_label(fw, t) for t in test_ids})
    env = {"PYTHONHASHSEED": str(hash_seed)}
    trace_rel = ""
    if trace:
        sandbox.put_file(f"{SCRATCH_DIR}/sitecustomize.py", trace_source())
        trace_rel = f"{SCRATCH_DIR}/trace-{uuid.uuid4().hex}.jsonl"
        env.update({"PYTHONPATH": f"{workdir}/{SCRATCH_DIR}",
                    "REPRO_TRACE_OUT": f"{workdir}/{trace_rel}", "REPRO_ROOT": workdir})
    cmd = harness.test_cmd + " " + " ".join(shlex.quote(x) for x in labels)
    result = sandbox.exec(cmd, timeout_s, env)
    log = result.stdout + "\n" + result.stderr
    statuses = swt.log_parser(harness.repo)(log)
    calls: dict[str, list[str]] | None = None
    if trace:
        try:
            calls = read_calls(sandbox.get_file(trace_rel).decode("utf-8", errors="replace"))
        except FileNotFoundError:
            calls = None
        sandbox.delete_file(trace_rel)
    defs: dict[str, str] = {}
    if fw == "sympy":
        for path in {t.split("::")[0] for t in test_ids}:
            try:
                defs[path] = sandbox.get_file(path).decode("utf-8", errors="replace")
            except (FileNotFoundError, OSError):
                defs[path] = ""
    outcomes = parse_harness_run(fw, statuses, log, list(test_ids), workdir, calls, defs)
    if any(o.note == "not in test log" for o in outcomes.values()) or not outcomes:
        log_ = logging.getLogger(__name__)
        log_.warning("harness run: %d requested test(s) missing from the log; cmd=%s exit=%s\n"
                     "--- log head ---\n%s\n--- log tail ---\n%s", sum(
                         1 for o in outcomes.values() if o.note == "not in test log"), cmd,
                     result.exit_code, log[:1500], log[-2500:])
    if result.timed_out:
        outcomes = {t: (o if o.status is not TestStatus.ERROR or o.note != "not in test log"
                        else o.model_copy(update={"status": TestStatus.TIMEOUT}))
                    for t, o in outcomes.items()}
    elif result.resource_failure:
        outcomes = {t: (o if o.note != "not in test log"
                        else o.model_copy(update={"note": "resource: memory"}))
                    for t, o in outcomes.items()}
    return TestRunResult(outcomes=outcomes, exec=result, timed_out=result.timed_out)
