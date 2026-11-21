"""Installed as sitecustomize.py inside a container for non-pytest test runners.

Python 3.5 compatible, stdlib only, untyped on purpose. When REPRO_TRACE_OUT is set it
records which repo functions (non-test files under REPRO_ROOT) are called, keyed by the
running unittest test id when there is one ("*" otherwise), and appends one JSON line
per process at exit (test runners may re-exec themselves).
"""

import atexit
import json
import os
import sys

_OUT = os.environ.get("REPRO_TRACE_OUT")
_ROOT = os.environ.get("REPRO_ROOT", "/testbed").rstrip("/") + "/"
_TEST_DIRS = {"tests", "test", "testing"}


def _is_test(rel):
    parts = rel.split("/")
    name = parts[-1]
    if any(p in _TEST_DIRS for p in parts[:-1]):
        return True
    return name == "conftest.py" or name.startswith("test_") or name.endswith("_test.py")


if _OUT:
    _calls = {}
    _current = ["*"]
    _seen = {}

    def _profile(frame, event, arg):
        if event != "call":
            return
        code = frame.f_code
        key = (code.co_filename, code.co_name, _current[0])
        if key in _seen:
            return
        _seen[key] = True
        fn = code.co_filename
        if not fn.startswith(_ROOT) or "/.repro/" in fn:
            return
        rel = fn[len(_ROOT):]
        if _is_test(rel):
            return
        _calls.setdefault(_current[0], set()).add(rel + ":" + code.co_name)

    sys.setprofile(_profile)
    try:
        import threading

        threading.setprofile(_profile)
    except Exception:
        pass
    try:
        import unittest.case as _uc

        _orig_run = _uc.TestCase.run

        def _run(self, result=None):
            _current[0] = self.id()
            try:
                return _orig_run(self, result)
            finally:
                _current[0] = "*"

        _uc.TestCase.run = _run
    except Exception:
        pass

    def _dump():
        try:
            with open(_OUT, "a") as f:
                f.write(json.dumps({k: sorted(v) for k, v in _calls.items()}) + "\n")
        except Exception:
            pass

    atexit.register(_dump)
