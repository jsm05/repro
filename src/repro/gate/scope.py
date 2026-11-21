"""G0: the candidate diff may only add tests.

Rules (DESIGN 5.4): test paths only; additions only (new files, or lines appended at the
end of existing test files); no conftest.py or config edits; no skip or xfail markers;
no renames, deletions, binary files, symlinks or mode changes; every candidate test id
names a test function the diff itself adds.

Why additions-only: if a candidate could change existing lines, it could weaken or
delete the repo's own tests, or insert an early `return` into one, and nothing
downstream would notice. Appending is the only edit that cannot change what existing
tests do.
"""

from __future__ import annotations

import posixpath
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from repro.sandbox.treehash import is_test_path

CONFIG_FILES = {
    "setup.cfg", "pyproject.toml", "tox.ini", "pytest.ini", "setup.py", ".coveragerc",
    "noxfile.py", "conftest.py", "sitecustomize.py", "usercustomize.py",
}
SKIP_PATTERNS = [
    re.compile(p)
    for p in (
        r"\bpytest\.mark\.(skip|skipif|xfail)\b",
        r"\bmark\.(skip|skipif|xfail)\b",
        r"\bpytest\.(skip|xfail|importorskip)\s*\(",
        r"\bunittest\.(skip|skipIf|skipUnless|expectedFailure)\b",
        r"@\s*(skip|skipIf|skipUnless|expectedFailure)\b",
        r"\bSkipTest\b",
        r"\.skipTest\s*\(",
        r"^\s*pytestmark\s*=",
        r"^\s*__test__\s*=",
    )
]
_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_DEF = re.compile(r"^\s*(?:async\s+)?def\s+(\w+)\s*\(")
NO_NEWLINE = "\\ No newline at end of file"


class DiffParseError(ValueError):
    pass


@dataclass
class Hunk:
    old_start: int
    old_len: int
    new_start: int
    new_len: int
    lines: list[str] = field(default_factory=list)


@dataclass
class FileDiff:
    old_path: str | None = None
    new_path: str | None = None
    is_new: bool = False
    is_deleted: bool = False
    is_rename: bool = False
    is_binary: bool = False
    mode_change: bool = False
    new_mode: str | None = None
    hunks: list[Hunk] = field(default_factory=list)

    @property
    def path(self) -> str:
        return self.new_path or self.old_path or ""

    def added_lines(self) -> list[str]:
        return [ln[1:] for h in self.hunks for ln in h.lines if ln.startswith("+")]


def _strip_prefix(raw: str) -> str | None:
    path = raw.split("\t", 1)[0].strip()
    if path == "/dev/null":
        return None
    if path.startswith(("a/", "b/")):
        path = path[2:]
    return path


def _lines(diff: str) -> list[str]:
    """Split on newlines only: str.splitlines() also splits on form feeds and other
    separators that appear inside source lines, which breaks hunk counts."""
    lines = diff.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def repair_patch(diff: str) -> str:
    """Restore trailing blank context lines that were trimmed off the end of a patch
    (a stripped dataset string), and the final newline. Anything else is left alone."""
    lines = _lines(diff)
    old_left = new_left = 0
    in_hunk = False
    for line in lines:
        if in_hunk and (old_left > 0 or new_left > 0):
            tag = line[:1] or " "
            if tag == " ":
                old_left, new_left = old_left - 1, new_left - 1
            elif tag == "-":
                old_left -= 1
            elif tag == "+":
                new_left -= 1
            continue
        m = _HUNK.match(line) if line.startswith("@@") else None
        if m:
            in_hunk = True
            old_left = int(m.group(2)) if m.group(2) is not None else 1
            new_left = int(m.group(4)) if m.group(4) is not None else 1
        else:
            in_hunk = False
    if in_hunk and old_left == new_left and old_left > 0:
        lines += [" "] * old_left
    return "\n".join(lines) + "\n"


def parse_diff(diff: str) -> list[FileDiff]:
    """Parse a git-style or plain unified diff."""
    files: list[FileDiff] = []
    cur: FileDiff | None = None
    hunk: Hunk | None = None
    old_left = new_left = 0
    for line in _lines(diff):
        if hunk is not None and (old_left > 0 or new_left > 0):
            tag = line[:1]
            if line == "":
                tag, line = " ", " "
            if tag == " ":
                old_left, new_left = old_left - 1, new_left - 1
            elif tag == "-":
                old_left -= 1
            elif tag == "+":
                new_left -= 1
            elif tag == "\\":
                pass
            else:
                raise DiffParseError(f"hunk ended early at: {line[:60]!r}")
            hunk.lines.append(line)
            continue
        if hunk is not None and line.startswith("\\"):
            hunk.lines.append(line)
            continue
        hunk = None
        if line.startswith("diff --git "):
            cur = FileDiff()
            files.append(cur)
            m = re.match(r"^diff --git a/(.+) b/(.+)$", line)
            if m:
                cur.old_path, cur.new_path = m.group(1), m.group(2)
        elif line.startswith("--- "):
            if cur is None or cur.hunks:
                cur = FileDiff()
                files.append(cur)
            cur.old_path = _strip_prefix(line[4:])
            if cur.old_path is None:
                cur.is_new = True
        elif line.startswith("+++ "):
            if cur is None:
                raise DiffParseError("'+++' without '---'")
            cur.new_path = _strip_prefix(line[4:])
            if cur.new_path is None:
                cur.is_deleted = True
        elif line.startswith("@@"):
            m = _HUNK.match(line)
            if not m or cur is None:
                raise DiffParseError(f"bad hunk header: {line[:60]!r}")
            old_len = int(m.group(2)) if m.group(2) is not None else 1
            new_len = int(m.group(4)) if m.group(4) is not None else 1
            hunk = Hunk(int(m.group(1)), old_len, int(m.group(3)), new_len)
            cur.hunks.append(hunk)
            old_left, new_left = old_len, new_len
        elif cur is None:
            if line.strip():
                raise DiffParseError(f"text before the first file header: {line[:60]!r}")
        elif line.startswith("new file mode "):
            cur.is_new, cur.new_mode = True, line.split()[-1]
        elif line.startswith("deleted file mode"):
            cur.is_deleted = True
        elif line.startswith(("old mode", "new mode")):
            cur.mode_change = True
        elif line.startswith(("rename from", "rename to", "copy from", "copy to",
                              "similarity index", "dissimilarity index")):
            cur.is_rename = True
        elif line.startswith("GIT binary patch") or line.startswith("Binary files"):
            cur.is_binary = True
        elif cur.is_binary or not line.strip() or line.startswith(("index ", "literal ")):
            continue
        else:
            raise DiffParseError(f"unrecognised line: {line[:60]!r}")
    if hunk is not None and (old_left > 0 or new_left > 0):
        raise DiffParseError("diff ends in the middle of a hunk")
    return files


@dataclass(frozen=True)
class ScopeResult:
    ok: bool
    reason: str
    files: tuple[str, ...] = ()


def _bad_path(path: str) -> bool:
    if not path or path.startswith("/") or "\\" in path:
        return True
    norm = posixpath.normpath(path)
    return norm.startswith("..") or norm != path


def _append_only(f: FileDiff, original: str) -> str | None:
    """None if every hunk only appends at the end of `original`, else the reason."""
    n_old = len(original.splitlines())
    for h in f.hunks:
        seen_plus = False
        lines = h.lines
        for i, ln in enumerate(lines):
            tag = ln[:1]
            if tag == "-":
                # Appending to a file with no final newline rewrites its last line.
                nxt = lines[i + 1] if i + 1 < len(lines) else ""
                after = lines[i + 2] if i + 2 < len(lines) else ""
                if not (nxt == NO_NEWLINE and after[:1] == "+" and after[1:] == ln[1:]):
                    return "removes or changes existing lines (only additions are allowed)"
            elif tag == " " and seen_plus:
                return "inserts lines in the middle of an existing test file (append only)"
            elif tag == "+":
                seen_plus = True
        if seen_plus:
            old_end = h.old_start + h.old_len - 1 if h.old_len else h.old_start
            if old_end != n_old:
                return "adds lines before the end of an existing test file (append only)"
    return None


def check_scope(
    diff: str, test_ids: Sequence[str], read_original: Callable[[str], str | None]
) -> ScopeResult:
    """G0. `read_original(path)` returns the pristine file text, or None if absent."""

    def no(reason: str) -> ScopeResult:
        return ScopeResult(False, reason)

    if not diff.strip():
        return no("empty diff")
    if not test_ids:
        return no("candidate names no test ids")
    try:
        files = parse_diff(diff)
    except DiffParseError as exc:
        return no(f"unparseable diff: {exc}")
    if not files:
        return no("diff touches no files")

    defs: dict[str, set[str]] = {}
    for f in files:
        for p in {f.old_path, f.new_path} - {None}:
            assert p is not None
            if _bad_path(p):
                return no(f"unsafe path: {p!r}")
        path = f.path
        name = posixpath.basename(path)
        if f.is_binary:
            return no(f"{path}: binary files are not allowed")
        if f.is_rename:
            return no(f"{path}: renames and copies are not allowed")
        if f.is_deleted:
            return no(f"{path}: deleting files is not allowed")
        if f.mode_change or (f.new_mode is not None and f.new_mode != "100644"):
            return no(f"{path}: mode changes and symlinks are not allowed")
        if name == "conftest.py":
            return no(f"{path}: conftest.py edits are not allowed")
        if name in CONFIG_FILES:
            return no(f"{path}: config file edits are not allowed")
        if not is_test_path(path):
            return no(f"{path}: outside test paths")
        added = f.added_lines()
        for ln in added:
            if any(p.search(ln) for p in SKIP_PATTERNS):
                return no(f"{path}: skip/xfail markers are not allowed: {ln.strip()[:80]!r}")
        original = read_original(path)
        if f.is_new:
            if original is not None:
                return no(f"{path}: diff creates a file that already exists")
        else:
            if original is None:
                return no(f"{path}: diff modifies a file that does not exist")
            why = _append_only(f, original)
            if why:
                return no(f"{path}: {why}")
        defs.setdefault(path, set()).update(
            m.group(1) for m in (_DEF.match(ln) for ln in added) if m
        )

    for tid in test_ids:
        file_part, sep, rest = tid.partition("::")
        if not sep or not rest:
            return no(f"test id must be a node id (file::test): {tid!r}")
        if file_part not in defs:
            return no(f"test id {tid!r} is not in a file the diff adds to")
        func = rest.split("::")[-1].split("[", 1)[0]
        if func not in defs[file_part]:
            return no(f"test id {tid!r} is not a test the diff adds")
    return ScopeResult(True, "only adds tests", tuple(sorted(defs)))
