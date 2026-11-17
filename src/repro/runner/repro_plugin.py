"""pytest plugin injected into the sandbox. Writes one JSON line per test report.

Why not JUnit XML: it cannot tell XPASS from PASSED, and it gives tracebacks only as
text. This plugin records the exact phase, outcome, xfail flag and a structured
traceback for every test.

Constraints: stdlib plus pytest only, Python 3.6 syntax, works on old pytest (3.x+),
because it runs inside benchmark containers with whatever versions they ship.

Limit, stated plainly: code under test runs in the same process and could forge
these records. The plugin drops the report path from the environment and keeps the
file open, which stops casual tampering, not a determined adversary.
"""
import json
import os
import traceback

import pytest

_ENV = "REPRO_REPORT"
_out = None


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
    }
    if rep.outcome == "failed":
        record["exc"] = _exc_record(call.excinfo)
        record["longrepr"] = str(rep.longrepr)[-4000:]
    _write(record)
