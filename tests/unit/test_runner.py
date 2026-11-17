"""Test runner: command building and outcome parsing (TEST_SPEC M1)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from repro.models import TestStatus
from repro.runner.pytest_runner import run_tests
from repro.sandbox.base import Limits
from repro.sandbox.local import LocalSandbox
from tests.helpers import write_tree

SIX = """
import pytest

from mini import add, boom


@pytest.fixture
def broken():
    raise RuntimeError("setup broke")


def test_pass():
    assert 1 == 1


def test_fail():
    assert add(1, 2) == 3


def test_raise():
    boom()


def test_error(broken):
    pass


def test_skip():
    pytest.skip("nah")


@pytest.mark.xfail
def test_xfail():
    assert 0


@pytest.mark.xfail
def test_xpass():
    assert 1


class TestGroup:
    def test_method(self):
        assert True
"""

MINI = """
def add(a, b):
    return a + b + 1


def boom():
    raise KeyError("port")
"""


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    return write_tree(
        tmp_path / "repo",
        {
            "mini/__init__.py": MINI,
            "tests/test_six.py": SIX,
            "tests/test_syntax.py": "def test_x(:\n    pass\n",
            "tests/test_big.py": "def test_big():\n    b = bytearray(2 * 1024 ** 3)\n",
        },
    )


@pytest.fixture
def sandbox(repo: Path) -> Iterator[LocalSandbox]:
    sb = LocalSandbox(repo, Limits(memory_bytes=1024 * 1024 * 1024))
    sb.start()
    yield sb
    sb.close()


def test_six_outcomes(sandbox: LocalSandbox) -> None:
    """T1.9: one passed, failed, error, skipped, xfail, xpass test map to the right outcomes."""
    res = run_tests(sandbox, ["tests/test_six.py"], timeout_s=60)
    got = {tid.split("::", 1)[1]: o.status for tid, o in res.outcomes.items()}
    assert got == {
        "test_pass": TestStatus.PASSED,
        "test_fail": TestStatus.FAILED,
        "test_raise": TestStatus.FAILED,
        "test_error": TestStatus.ERROR,
        "test_skip": TestStatus.SKIPPED,
        "test_xfail": TestStatus.XFAIL,
        "test_xpass": TestStatus.XPASS,
        "TestGroup::test_method": TestStatus.PASSED,
    }
    err = res.outcomes["tests/test_six.py::test_error"]
    assert err.failure is not None and err.failure.phase == "setup"


def test_syntax_error_is_error_not_failed(sandbox: LocalSandbox) -> None:
    """T1.10: a syntax error in the test file gives ERROR, not FAILED."""
    for requested in ["tests/test_syntax.py::test_x", "tests/test_syntax.py"]:
        res = run_tests(sandbox, [requested], timeout_s=60)
        assert res.outcomes, requested
        assert all(o.status is TestStatus.ERROR for o in res.outcomes.values())
        o = next(iter(res.outcomes.values()))
        assert o.failure is not None and o.failure.phase == "collection"
        assert "SyntaxError" in o.failure.exc_type or "SyntaxError" in o.failure.text


def test_requested_but_missing_test_is_error(sandbox: LocalSandbox) -> None:
    """T1.10: a requested node id that never runs is ERROR (not collected), never FAILED."""
    res = run_tests(sandbox, ["tests/test_six.py::test_does_not_exist"], timeout_s=60)
    o = res.outcomes["tests/test_six.py::test_does_not_exist"]
    assert o.status is TestStatus.ERROR


def test_failure_details(sandbox: LocalSandbox) -> None:
    """T1.11: failure details carry exception type, message and repo-relative frames."""
    res = run_tests(sandbox, ["tests/test_six.py::test_fail", "tests/test_six.py::test_raise"],
                    timeout_s=60)
    fail = res.outcomes["tests/test_six.py::test_fail"].failure
    assert fail is not None
    assert fail.phase == "call"
    assert fail.exc_type == "AssertionError"
    assert "assert 4 == 3" in fail.message
    assert fail.frames[-1].path == "tests/test_six.py"
    assert fail.frames[-1].func == "test_fail"

    boom = res.outcomes["tests/test_six.py::test_raise"].failure
    assert boom is not None
    assert boom.exc_type == "KeyError"
    assert "port" in boom.message
    assert [f.path for f in boom.frames] == ["tests/test_six.py", "mini/__init__.py"]
    assert boom.frames[-1].func == "boom"
    assert all(not f.path.startswith("/") for f in boom.frames)


def test_memory_error_in_test_is_resource_error(repo: Path) -> None:
    """T1.7: a test that blows the memory limit is ERROR (resource), not FAILED."""
    with LocalSandbox(repo, Limits(memory_bytes=512 * 1024 * 1024)) as sb:
        res = run_tests(sb, ["tests/test_big.py::test_big"], timeout_s=60)
    o = res.outcomes["tests/test_big.py::test_big"]
    assert o.status is TestStatus.ERROR
    assert "memory" in o.note


def test_timeout_marks_tests_timeout(tmp_path: Path) -> None:
    """T1.2: a run that times out reports TIMEOUT for the requested tests, never FAILED."""
    repo = write_tree(tmp_path / "r", {"tests/test_hang.py": "def test_hang():\n    while True:\n"
                                       "        pass\n"})
    with LocalSandbox(repo) as sb:
        res = run_tests(sb, ["tests/test_hang.py::test_hang"], timeout_s=2)
    assert res.timed_out
    assert res.outcomes["tests/test_hang.py::test_hang"].status is TestStatus.TIMEOUT
