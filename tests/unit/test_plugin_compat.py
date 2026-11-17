"""The code injected into benchmark containers must run on old Pythons (TEST_SPEC M2)."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

from repro.runner import repro_plugin
from repro.sandbox import treehash

FILES = [Path(repro_plugin.__file__), Path(treehash.__file__)]


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.name)
def test_old_syntax_and_stdlib_only(path: Path) -> None:
    """T2.16: tracer plugin parses with feature_version (3, 6); imports only stdlib and pytest."""
    tree = ast.parse(path.read_text(), feature_version=(3, 6))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "relative imports would break when copied into a container"
            imported.add((node.module or "").split(".")[0])
    allowed = set(sys.stdlib_module_names) | {"pytest"}
    assert imported <= allowed, imported - allowed


@pytest.mark.parametrize(
    "path",
    ["tests/test_x.py", "pkg/tests/h.py", "conftest.py", "pkg/mod.py", "pkg/x_test.py",
     "pkg/testing_utils.py", "test/data.txt", "pkg/contest.py"],
)
def test_plugin_test_path_rule_matches_treehash(path: str) -> None:
    """T2.16: the plugin's copy of the test-path rule agrees with the shared one."""
    assert repro_plugin._is_test_path(path) is treehash.is_test_path(path)
