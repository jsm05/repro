"""Sandbox contract tests (TEST_SPEC M1, and T6.D1 for Docker). Every Sandbox backend must
pass these."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from repro.sandbox.base import SandboxError, SandboxPathError
from tests.contract.conftest import LocalBackend
from tests.helpers import SANDBOX_DIGEST, dir_digest, tiny_repo


def digest(sb) -> str:  # type: ignore[no-untyped-def]
    r = sb.exec([sb.python, "-c", SANDBOX_DIGEST], timeout_s=30)
    assert r.exit_code == 0, r.stderr
    return str(r.stdout.strip())


def test_exec_basic(backend: LocalBackend, tmp_path: Path) -> None:
    """T1.1: exec returns stdout, stderr, exit code and duration, and runs in the workdir."""
    sb = backend.make(tiny_repo(tmp_path))
    r = sb.exec(["sh", "-c", "echo out; echo err >&2; exit 3"], timeout_s=10)
    assert r.stdout == "out\n"
    assert r.stderr == "err\n"
    assert r.exit_code == 3
    assert 0 <= r.duration_s < 5
    assert not r.timed_out and not r.truncated
    assert sb.exec("pwd", timeout_s=10).stdout.strip() == sb.workdir


def test_timeout_kills_process_tree(backend: LocalBackend, tmp_path: Path) -> None:
    """T1.2: sleep 60 with a 1 s timeout returns within 2 s of the limit; grandchild dies too."""
    sb = backend.make(tiny_repo(tmp_path))
    start = time.monotonic()
    r = sb.exec("sleep 60 & echo $! > grandchild.pid; wait", timeout_s=1)
    elapsed = time.monotonic() - start
    assert r.timed_out
    assert elapsed < 1 + 2
    pid = int(sb.get_file("grandchild.pid").decode().strip())
    assert backend.process_dead(sb, pid), "grandchild survived the timeout"


def test_output_is_capped_head_and_tail(backend: LocalBackend, tmp_path: Path) -> None:
    """T1.3: 1 MB of output is truncated to the cap, head and tail kept, flag set."""
    cap = 16 * 1024
    sb = backend.make(tiny_repo(tmp_path), output_cap_bytes=cap)
    code = "import sys; sys.stdout.write('a' * 500000 + 'MIDDLE' + 'b' * 500000)"
    r = sb.exec([sb.python, "-c", code], timeout_s=30)
    assert r.truncated
    assert len(r.stdout.encode()) <= cap + 200
    assert r.stdout.startswith("a" * 100)
    assert r.stdout.endswith("b" * 100)
    assert "MIDDLE" not in r.stdout


def test_sandboxes_are_isolated(backend: LocalBackend, tmp_path: Path) -> None:
    """T1.4: two sandboxes from one source do not see each other's writes; source unchanged."""
    src = tiny_repo(tmp_path)
    before = dir_digest(src)
    original = (src / "pkg" / "mod.py").read_bytes()
    a = backend.make(src)
    b = backend.make(src)
    a.put_file("pkg/new.txt", "hi")
    a.exec("echo '# edited' >> pkg/mod.py", timeout_s=10)
    with pytest.raises(FileNotFoundError):
        b.get_file("pkg/new.txt")
    assert b.get_file("pkg/mod.py") == original
    assert a.get_file("pkg/mod.py") != original
    assert dir_digest(src) == before


def test_file_api_round_trip_and_path_safety(backend: LocalBackend, tmp_path: Path) -> None:
    """T1.5: put_file/get_file round trip with unicode and nested dirs; escapes rejected."""
    sb = backend.make(tiny_repo(tmp_path))
    text = "héllo wörld, ünïcode ✓\n"
    sb.put_file("deep/nested/dir/file.txt", text)
    assert sb.get_file("deep/nested/dir/file.txt").decode("utf-8") == text
    sb.put_file("raw.bin", b"\x00\xff")
    assert sb.get_file("raw.bin") == b"\x00\xff"
    for bad in ["../escape.txt", "a/../../escape.txt", "/etc/passwd", ""]:
        with pytest.raises(SandboxPathError):
            sb.put_file(bad, "x")
        with pytest.raises(SandboxPathError):
            sb.get_file(bad)
    sb.exec("ln -s / rootlink", timeout_s=10)
    with pytest.raises(SandboxPathError):
        sb.get_file("rootlink/etc/hostname")
    with pytest.raises(SandboxPathError):
        sb.put_file("rootlink/tmp/planted.txt", "x")


GOOD_PATCH = """\
diff --git a/pkg/mod.py b/pkg/mod.py
--- a/pkg/mod.py
+++ b/pkg/mod.py
@@ -1,2 +1,2 @@
 def double(x):
-    return 2 * x
+    return x + x
diff --git a/pkg/extra.py b/pkg/extra.py
new file mode 100644
--- /dev/null
+++ b/pkg/extra.py
@@ -0,0 +1 @@
+VALUE = 1
"""

HALF_BAD_PATCH = """\
diff --git a/pkg/mod.py b/pkg/mod.py
--- a/pkg/mod.py
+++ b/pkg/mod.py
@@ -1,2 +1,2 @@
 def double(x):
-    return 2 * x
+    return x + x
diff --git a/tests/test_mod.py b/tests/test_mod.py
--- a/tests/test_mod.py
+++ b/tests/test_mod.py
@@ -1,2 +1,2 @@
-this line is not in the file
+neither is this one
 from pkg.mod import double
"""


def test_apply_patch_is_atomic(backend: LocalBackend, tmp_path: Path) -> None:
    """T1.6: a good patch applies; malformed or half-bad patches error and change nothing."""
    sb = backend.make(tiny_repo(tmp_path))
    before = digest(sb)
    for bad in ["this is not a patch\n", HALF_BAD_PATCH]:
        res = sb.apply_patch(bad)
        assert not res.ok
        assert res.error
        assert digest(sb) == before
    res = sb.apply_patch(GOOD_PATCH)
    assert res.ok, res.error
    assert b"return x + x" in sb.get_file("pkg/mod.py")
    assert sb.get_file("pkg/extra.py") == b"VALUE = 1\n"


def test_memory_limit_is_a_resource_failure(backend: LocalBackend, tmp_path: Path) -> None:
    """T1.7: allocating 2 GB under a 256 MB limit is killed and reported as a resource failure."""
    sb = backend.make(tiny_repo(tmp_path), memory_bytes=256 * 1024 * 1024)
    r = sb.exec([sb.python, "-c", "b = bytearray(2 * 1024 ** 3)"], timeout_s=30)
    assert r.exit_code != 0
    assert not r.timed_out
    assert r.resource_failure == "memory"


def test_close_removes_everything(backend: LocalBackend, tmp_path: Path) -> None:
    """T1.8: close() removes all state, including after a timeout and leftover processes."""
    sb = backend.make(tiny_repo(tmp_path))
    r = sb.exec("sleep 30", timeout_s=0.5)
    assert r.timed_out
    bg = sb.exec("sleep 30 > /dev/null 2>&1 & echo $!", timeout_s=10)
    pid = int(bg.stdout.strip())
    sb.close()
    assert backend.is_gone(sb)
    assert backend.process_dead(sb, pid), "background process outlived close()"
    sb.close()  # idempotent
    with pytest.raises(SandboxError):
        sb.exec("true", timeout_s=5)
