"""Source tree hash used by G5 (TEST_SPEC M1)."""

from __future__ import annotations

from pathlib import Path

import pytest
from repro.sandbox.local import LocalSandbox
from repro.sandbox.treehash import is_test_path, tree_hash

from tests.helpers import FIXTURES_ROOT, tiny_repo


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("tests/test_x.py", True),
        ("pkg/tests/helpers.py", True),
        ("pkg/test/data.json", True),
        ("test_top.py", True),
        ("pkg/thing_test.py", True),
        ("conftest.py", True),
        ("pkg/sub/conftest.py", True),
        ("pkg/mod.py", False),
        ("pkg/testing_utils.py", False),
        ("pkg/contest.py", False),
    ],
)
def test_is_test_path(path: str, expected: bool) -> None:
    """T1.15: the test-path rule (shared with G0) classifies paths as documented."""
    assert is_test_path(path) is expected


def test_hash_stable_and_sensitive(tmp_path: Path) -> None:
    """T1.15: stable across runs, changes with source, ignores test paths and caches."""
    root = tiny_repo(tmp_path)
    h = tree_hash(str(root))
    assert tree_hash(str(root)) == h

    (root / "tests" / "test_new.py").write_text("def test_n():\n    pass\n")
    (root / "pkg" / "__pycache__").mkdir()
    (root / "pkg" / "__pycache__" / "mod.cpython-311.pyc").write_bytes(b"\x00junk")
    (root / ".pytest_cache").mkdir()
    (root / ".pytest_cache" / "v").write_text("x")
    (root / ".repro").mkdir()
    (root / ".repro" / "report.jsonl").write_text("{}")
    (root / "conftest.py").write_text("# hooks\n")
    assert tree_hash(str(root)) == h

    (root / "pkg" / "mod.py").write_text("def double(x):\n    return x * 2\n")
    assert tree_hash(str(root)) != h


def test_hash_same_across_sandboxes() -> None:
    """T1.15: two sandboxes from one fixture report the same source tree hash."""
    src = FIXTURES_ROOT / "f01_off_by_one" / "repo"
    with LocalSandbox(src) as a, LocalSandbox(src) as b:
        assert a.source_tree_hash() == b.source_tree_hash()
        a.put_file("pager/pages.py", a.get_file("pager/pages.py") + b"\n# changed\n")
        assert a.source_tree_hash() != b.source_tree_hash()
        b.put_file("tests/test_extra.py", "def test_e():\n    pass\n")
        assert b.source_tree_hash() == tree_hash(str(src))
