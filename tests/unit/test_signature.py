"""Failure signatures and the right-reason classifier (TEST_SPEC M2)."""

from __future__ import annotations

import pytest

from repro.gate.checks import right_reason
from repro.gate.signature import failure_signature
from repro.models import FailureDetail, Frame

T = Frame(path="tests/test_x.py", lineno=7, func="test_x")
SRC = Frame(path="pkg/mod.py", lineno=3, func="parse")


def fd(exc: str, msg: str = "", frames: list[Frame] | None = None) -> FailureDetail:
    return FailureDetail(phase="call", exc_type=exc, message=msg, frames=frames or [T])


@pytest.mark.parametrize(
    ("failure", "ok"),
    [
        (fd("AssertionError", "assert 1 == 2"), True),
        (fd("KeyError", "'port'", [T, SRC]), True),
        (fd("ValueError", "bad", [T, SRC, Frame(path="pkg/util.py", lineno=1, func="f")]), True),
        (fd("NameError", "name 'paginatee' is not defined"), False),
        (fd("TypeError", "slugify() takes 1 positional argument but 2 were given"), False),
        (fd("ModuleNotFoundError", "No module named 'helper'"), False),
        (fd("Failed", "DID NOT RAISE <class 'ValueError'>"), True),
        (fd("_pytest.outcomes.Failed", "DID NOT RAISE"), True),
    ],
)
def test_right_reason_table(failure: FailureDetail, ok: bool) -> None:
    """T2.5: assertion and repo-frame exceptions pass; test-only NameError/TypeError rejected."""
    passed, reason = right_reason(failure)
    assert passed is ok
    assert reason


def test_signature_masks_noise() -> None:
    """T2.6: addresses, temp paths and numbers are masked; exception type discriminates."""
    a = fd("ValueError", "bad value 42 at 0x7f3a2b1c in /tmp/repro-sbx-abc/repo/x.py", [T, SRC])
    b = fd("ValueError", "bad value 17 at 0x55aa01 in /tmp/repro-sbx-xyz/repo/x.py", [T, SRC])
    c = fd("TypeError", "bad value 42 at 0x7f3a2b1c in /tmp/repro-sbx-abc/repo/x.py", [T, SRC])
    assert failure_signature(a) == failure_signature(b)
    assert failure_signature(a) != failure_signature(c)
    other_frame = fd("ValueError", a.message, [T, Frame(path="pkg/other.py", lineno=9, func="g")])
    assert failure_signature(a) != failure_signature(other_frame)


def test_assertion_signature_uses_the_failing_line() -> None:
    """T2.6: assertion failures at the same line match even if the shown values differ."""
    x = fd("AssertionError", "assert ['c', 'a'] == ['a', 'c']")
    y = fd("AssertionError", "assert ['a', 'c', 'b'] == ['a', 'c']")
    z = fd("AssertionError", "assert ['c', 'a'] == ['a', 'c']",
           [Frame(path="tests/test_x.py", lineno=8, func="test_x")])
    assert failure_signature(x) == failure_signature(y)
    assert failure_signature(x) != failure_signature(z)
