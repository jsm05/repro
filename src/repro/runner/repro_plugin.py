"""pytest plugin injected into the sandbox. Writes one JSON line per test report.

Why not JUnit XML: it cannot tell XPASS from PASSED, and it gives tracebacks only as
text. This plugin records the exact phase, outcome, xfail flag and a structured
traceback for every test.

Constraints: stdlib plus pytest only, Python 3.6 syntax, works on old pytest (3.x+),
because it runs inside benchmark containers with whatever versions they ship.

When REPRO_TRACE=1, a sys.settrace tracer runs during each test's call phase and
records which repo functions (outside test paths) had their bodies executed. G5 uses
this to reject tests that never exercise the code under test. Module-level code
(imports) does not count.

Limit, stated plainly: code under test runs in the same process and could forge
these records. The plugin drops the report path from the environment and keeps the
file open, which stops casual tampering, not a determined adversary.
"""
import json
import os
import sys
import traceback

import pytest

_ENV = "REPRO_REPORT"
_out = None
_TRACE = os.environ.pop("REPRO_TRACE", "") == "1"
_ROOT = os.environ.pop("REPRO_ROOT", "")
_MAX_CALLS = 200

# Keep in sync with repro/sandbox/treehash.py (a test checks they agree).
_TEST_DIR_NAMES = {"tests", "test", "testing"}


def _is_test_path(rel_path):
    parts = rel_path.replace("\\", "/").strip("/").split("/")
    name = parts[-1]
    if any(p in _TEST_DIR_NAMES for p in parts[:-1]):
        return True
    if name == "conftest.py":
        return True
    return name.endswith(".py") and (name.startswith("test_") or name.endswith("_test.py"))


class _Tracer(object):
    def __init__(self, root):
        self.root = os.path.realpath(root) + os.sep
        self.calls = set()
        self._rel = {}

    def _relpath(self, filename):
        rel = self._rel.get(filename)
        if rel is None:
            rel = ""
            if filename.startswith("<"):  # <frozen os>, <string>, <stdin>: not repo files
                self._rel[filename] = rel
                return rel
            full = filename if os.path.isabs(filename) else os.path.join(self.root, filename)
            full = os.path.realpath(full)
            if full.startswith(self.root):
                cand = full[len(self.root):].replace(os.sep, "/")
                if not cand.startswith(".repro/") and not _is_test_path(cand):
                    rel = cand
            self._rel[filename] = rel
        return rel

    def __call__(self, frame, event, arg):
        if event == "call" and len(self.calls) < _MAX_CALLS:
            code = frame.f_code
            if code.co_name != "<module>":
                rel = self._relpath(code.co_filename)
                if rel:
                    self.calls.add(rel + ":" + code.co_name)
        return None


def _open():
    global _out
    if _out is None:
        path = os.environ.pop(_ENV, None)
        if path:
            _out = open(path, "a")
    return _out


def _write(record):
    out = _open()
    if out is not None:
        out.write(json.dumps(record) + "\n")
        out.flush()


def _exc_name(exc_type):
    mod = getattr(exc_type, "__module__", "") or ""
    name = getattr(exc_type, "__qualname__", None) or exc_type.__name__
    if mod in ("builtins", "exceptions"):
        return name
    return mod + "." + name


def _exc_record(excinfo):
    if excinfo is None:
        return None
    try:
        frames = [
            {"path": fs.filename, "lineno": fs.lineno, "func": fs.name}
            for fs in traceback.extract_tb(excinfo.tb)
        ]
    except Exception:
        frames = []
    try:
        message = str(excinfo.value)
    except Exception:
        message = "<unprintable exception>"
    return {"type": _exc_name(excinfo.type), "message": message[:4000], "frames": frames[-80:]}


def pytest_configure(config):
    _open()


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item):
    if not (_TRACE and _ROOT):
        yield
        return
    tracer = _Tracer(_ROOT)
    previous = sys.gettrace()
    sys.settrace(tracer)
    try:
        yield
    finally:
        sys.settrace(previous)
        item._repro_calls = sorted(tracer.calls)


def pytest_collectreport(report):
    if report.failed:
        _write({
            "kind": "collect",
            "nodeid": report.nodeid,
            "longrepr": str(report.longrepr)[-8000:],
        })


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    rep = outcome.get_result()
    record = {
        "kind": "test",
        "nodeid": rep.nodeid,
        "when": rep.when,
        "outcome": rep.outcome,
        "wasxfail": hasattr(rep, "wasxfail"),
        "duration": float(getattr(rep, "duration", 0.0) or 0.0),
        "exc": None,
        "longrepr": "",
        "repo_calls": getattr(item, "_repro_calls", None) if rep.when == "call" else None,
    }
    if rep.outcome == "failed":
        record["exc"] = _exc_record(call.excinfo)
        record["longrepr"] = str(rep.longrepr)[-4000:]
    _write(record)
