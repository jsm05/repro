"""G0 scope checker (TEST_SPEC M2)."""

from __future__ import annotations

import pytest

from repro.gate.scope import check_scope
from tests.helpers import new_file_diff

ORIGINALS = {
    "tests/test_pages.py": "import pytest\n\n\ndef test_old():\n    assert 1 == 1\n",
    "pager/pages.py": "def paginate():\n    return 1\n",
    "tests/conftest.py": "",
    "setup.cfg": "[tool:pytest]\n",
}


def read(path: str) -> str | None:
    return ORIGINALS.get(path)


APPEND = """\
diff --git a/tests/test_pages.py b/tests/test_pages.py
--- a/tests/test_pages.py
+++ b/tests/test_pages.py
@@ -3,3 +3,7 @@ import pytest
 
 def test_old():
     assert 1 == 1
+
+
+def test_new():
+    assert 2 == 3
"""

NEW_TEST = new_file_diff("tests/test_new.py", "def test_new():\n    assert 2 == 3\n")


def test_accepts_new_file_and_append() -> None:
    """T2.1: G0 accepts a new test file, and lines appended to an existing test file."""
    assert check_scope(NEW_TEST, ["tests/test_new.py::test_new"], read).ok
    res = check_scope(APPEND, ["tests/test_pages.py::test_new"], read)
    assert res.ok, res.reason
    both = NEW_TEST + APPEND
    assert check_scope(both, ["tests/test_new.py::test_new", "tests/test_pages.py::test_new"],
                       read).ok


SOURCE_EDIT = """\
diff --git a/pager/pages.py b/pager/pages.py
--- a/pager/pages.py
+++ b/pager/pages.py
@@ -1,2 +1,3 @@
 def paginate():
     return 1
+# tweak
"""

REMOVED_LINE = """\
diff --git a/tests/test_pages.py b/tests/test_pages.py
--- a/tests/test_pages.py
+++ b/tests/test_pages.py
@@ -3,3 +3,6 @@ import pytest
 
 def test_old():
-    assert 1 == 1
+    pass
+
+def test_new():
+    assert 2 == 3
"""

INSERT_MIDDLE = """\
diff --git a/tests/test_pages.py b/tests/test_pages.py
--- a/tests/test_pages.py
+++ b/tests/test_pages.py
@@ -3,3 +3,4 @@ import pytest
 
 def test_old():
+    return
     assert 1 == 1
"""

CONFTEST_EDIT = """\
diff --git a/tests/conftest.py b/tests/conftest.py
--- a/tests/conftest.py
+++ b/tests/conftest.py
@@ -0,0 +1 @@
+collect_ignore = ["test_pages.py"]
"""

SETUP_CFG_EDIT = """\
diff --git a/setup.cfg b/setup.cfg
--- a/setup.cfg
+++ b/setup.cfg
@@ -1 +1,2 @@
 [tool:pytest]
+addopts = -p no:warnings
"""

RENAME = """\
diff --git a/tests/test_pages.py b/tests/test_renamed.py
similarity index 100%
rename from tests/test_pages.py
rename to tests/test_renamed.py
"""

SYMLINK = """\
diff --git a/tests/test_link.py b/tests/test_link.py
new file mode 120000
--- /dev/null
+++ b/tests/test_link.py
@@ -0,0 +1 @@
+../pager/pages.py
\\ No newline at end of file
"""

BINARY = """\
diff --git a/tests/data.bin b/tests/data.bin
new file mode 100644
GIT binary patch
literal 4
LcmZQzWMT#Y01f~L
"""

DELETE = """\
diff --git a/tests/test_pages.py b/tests/test_pages.py
deleted file mode 100644
--- a/tests/test_pages.py
+++ /dev/null
@@ -1,5 +0,0 @@
-import pytest
-
-
-def test_old():
-    assert 1 == 1
"""


def skip_variant(body: str) -> str:
    return new_file_diff("tests/test_new.py", body)


REJECTS = {
    "source edit": (SOURCE_EDIT + NEW_TEST, ["tests/test_new.py::test_new"]),
    "removed line": (REMOVED_LINE, ["tests/test_pages.py::test_new"]),
    "insert in middle": (INSERT_MIDDLE + NEW_TEST, ["tests/test_new.py::test_new"]),
    "conftest edit": (CONFTEST_EDIT + NEW_TEST, ["tests/test_new.py::test_new"]),
    "new conftest": (new_file_diff("tests/sub/conftest.py", "x = 1\n") + NEW_TEST,
                     ["tests/test_new.py::test_new"]),
    "setup.cfg edit": (SETUP_CFG_EDIT + NEW_TEST, ["tests/test_new.py::test_new"]),
    "pyproject in tests": (new_file_diff("tests/pyproject.toml", "[tool.pytest]\n") + NEW_TEST,
                           ["tests/test_new.py::test_new"]),
    "tox.ini": (new_file_diff("tox.ini", "[tox]\n") + NEW_TEST, ["tests/test_new.py::test_new"]),
    "skip decorator": (skip_variant("import pytest\n\n\n@pytest.mark.skip\ndef test_new():\n"
                                    "    assert 0\n"), ["tests/test_new.py::test_new"]),
    "skipif decorator": (skip_variant("import pytest\n\n\n@pytest.mark.skipif(True, reason='x')\n"
                                      "def test_new():\n    assert 0\n"),
                         ["tests/test_new.py::test_new"]),
    "xfail decorator": (skip_variant("import pytest\n\n\n@pytest.mark.xfail\ndef test_new():\n"
                                     "    assert 0\n"), ["tests/test_new.py::test_new"]),
    "pytest.skip call": (skip_variant("import pytest\n\n\ndef test_new():\n    pytest.skip('x')\n"),
                         ["tests/test_new.py::test_new"]),
    "pytest.xfail call": (skip_variant("import pytest\n\n\ndef test_new():\n"
                                       "    pytest.xfail('x')\n"), ["tests/test_new.py::test_new"]),
    "unittest.skip": (skip_variant("import unittest\n\n\n@unittest.skip('x')\ndef test_new():\n"
                                   "    assert 0\n"), ["tests/test_new.py::test_new"]),
    "pytestmark": (
        skip_variant("import pytest\n\npytestmark = pytest.mark.skip\n\n\ndef test_new():\n"
                     "    assert 0\n"),
        ["tests/test_new.py::test_new"],
    ),
    "outside test paths": (new_file_diff("pager/test_helper_data.txt", "x\n") + NEW_TEST,
                           ["tests/test_new.py::test_new"]),
    "binary": (BINARY + NEW_TEST, ["tests/test_new.py::test_new"]),
    "rename": (RENAME, ["tests/test_renamed.py::test_old"]),
    "symlink": (SYMLINK, ["tests/test_link.py::test_x"]),
    "delete": (DELETE + NEW_TEST, ["tests/test_new.py::test_new"]),
    "path traversal": (new_file_diff("tests/../../evil_test.py", "def test_new():\n    pass\n"),
                       ["tests/../../evil_test.py::test_new"]),
    "test id not added": (NEW_TEST, ["tests/test_pages.py::test_old"]),
    "no test ids": (NEW_TEST, []),
    "empty diff": ("", ["tests/test_new.py::test_new"]),
}


@pytest.mark.parametrize("case", sorted(REJECTS))
def test_rejects(case: str) -> None:
    """T2.2: G0 rejects source edits, removals, conftest/config edits, skip/xfail, and more."""
    diff, ids = REJECTS[case]
    res = check_scope(diff, ids, read)
    assert not res.ok, f"{case} should be rejected"
    assert res.reason
