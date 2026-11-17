"""Hash of a repo's non-test source tree, used by G5 to catch tests that edit source.

Stdlib only and Python 3.6 syntax: in M6 this file is copied into benchmark
containers and run with whatever old Python they ship.
"""

import hashlib
import os
import stat

TEST_DIR_NAMES = {"tests", "test", "testing"}
IGNORED_DIRS = {
    ".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    ".tox", ".nox", ".eggs", ".repro", ".hypothesis",
}
IGNORED_SUFFIXES = (".pyc", ".pyo")


def is_test_path(rel_path):  # type: (str) -> bool
    """True for files that belong to the test suite rather than the source."""
    parts = rel_path.replace("\\", "/").strip("/").split("/")
    name = parts[-1]
    if any(p in TEST_DIR_NAMES for p in parts[:-1]):
        return True
    if name == "conftest.py":
        return True
    return name.endswith(".py") and (name.startswith("test_") or name.endswith("_test.py"))


def _ignored_dir(name):  # type: (str) -> bool
    return name in IGNORED_DIRS or name.endswith(".egg-info")


def tree_hash(root):  # type: (str) -> str
    h = hashlib.sha256()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not _ignored_dir(d))
        for name in sorted(filenames):
            if name.endswith(IGNORED_SUFFIXES):
                continue
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            if is_test_path(rel):
                continue
            st = os.lstat(full)
            h.update(rel.encode("utf-8") + b"\0")
            if stat.S_ISLNK(st.st_mode):
                h.update(b"L" + os.readlink(full).encode("utf-8"))
            else:
                h.update(b"X" if st.st_mode & stat.S_IXUSR else b"F")
                with open(full, "rb") as f:
                    h.update(f.read())
            h.update(b"\0")
    return h.hexdigest()


if __name__ == "__main__":
    import sys

    print(tree_hash(sys.argv[1] if len(sys.argv) > 1 else "."))
