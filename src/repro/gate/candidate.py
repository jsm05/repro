"""Build a candidate diff from files in a workspace.

The agent names the test files it wants to submit; this turns them into a diff
against the pristine repo. It only reads from the workspace. Everything else the
agent left there (helper files, source edits) is ignored by construction.
"""

from __future__ import annotations

import difflib
import os
from collections.abc import Callable, Sequence
from pathlib import Path

from repro.gate.scope import NO_NEWLINE
from repro.sandbox.base import Sandbox


def repo_reader(repo_dir: str | os.PathLike[str]) -> Callable[[str], str | None]:
    root = Path(repo_dir)

    def read(path: str) -> str | None:
        full = root / path
        return full.read_text(encoding="utf-8", errors="replace") if full.is_file() else None

    return read


def _terminate(lines: list[str]) -> list[str]:
    """Make every diff line end in a newline, marking a missing final newline as git does."""
    out = []
    for line in lines:
        if line.endswith("\n"):
            out.append(line)
        else:
            out.append(line + "\n")
            out.append(NO_NEWLINE + "\n")
    return out


def file_diff(path: str, old: str | None, new: str) -> str:
    if old == new:
        return ""
    new_lines = new.splitlines(keepends=True)
    if old is None:
        header = (
            f"diff --git a/{path} b/{path}\nnew file mode 100644\n--- /dev/null\n+++ b/{path}\n"
            f"@@ -0,0 +1,{len(new_lines)} @@\n"
        )
        return header + "".join(_terminate(["+" + ln for ln in new_lines]))
    body = list(
        difflib.unified_diff(
            old.splitlines(keepends=True), new_lines, fromfile=f"a/{path}", tofile=f"b/{path}"
        )
    )
    return f"diff --git a/{path} b/{path}\n" + "".join(_terminate(body))


def diff_from_workspace(
    workspace: Sandbox, read_original: Callable[[str], str | None], paths: Sequence[str]
) -> str:
    parts = []
    for path in sorted(set(paths)):
        new = workspace.get_file(path).decode("utf-8", errors="replace")
        parts.append(file_diff(path, read_original(path), new))
    return "".join(parts)
