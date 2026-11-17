"""Shared helpers for the test suite (no repro imports at module level beyond models)."""

from __future__ import annotations

import hashlib
import os
import textwrap
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURES_ROOT = ROOT / "tests" / "fixtures" / "mini_repos"
FIXTURE_NAMES = sorted(p.name for p in FIXTURES_ROOT.iterdir() if p.is_dir())


def write_tree(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text).lstrip("\n"))
    return root


def tiny_repo(root: Path) -> Path:
    return write_tree(
        root,
        {
            "pkg/__init__.py": "",
            "pkg/mod.py": "def double(x):\n    return 2 * x\n",
            "tests/test_mod.py": "from pkg.mod import double\n\n\ndef test_double():\n"
            "    assert double(2) == 4\n",
        },
    )


def dir_digest(root: Path) -> str:
    """Hash every file under root (tests included), for 'unchanged' checks."""
    h = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        h.update(str(path.relative_to(root)).encode())
        h.update(b"\0")
        h.update(path.read_bytes())
        h.update(b"\0")
    return h.hexdigest()


# Runs inside a sandbox: hashes every file except scratch and bytecode dirs.
SANDBOX_DIGEST = textwrap.dedent(
    """
    import hashlib, os
    h = hashlib.sha256()
    for dirpath, dirnames, filenames in os.walk("."):
        dirnames[:] = sorted(d for d in dirnames if d not in (".repro", "__pycache__"))
        for name in sorted(filenames):
            path = os.path.join(dirpath, name)
            h.update(path.encode()); h.update(b"\\0")
            with open(path, "rb") as f:
                h.update(f.read())
    print(h.hexdigest())
    """
)


def process_alive(pid: int) -> bool:
    """True if pid exists and is not a zombie."""
    try:
        with open(f"/proc/{pid}/stat") as f:
            state = f.read().rsplit(")", 1)[1].split()[0]
    except FileNotFoundError:
        return False
    return state != "Z"


def wait_dead(pid: int, within_s: float = 1.0) -> bool:
    deadline = time.monotonic() + within_s
    while time.monotonic() < deadline:
        if not process_alive(pid):
            return True
        time.sleep(0.02)
    return not process_alive(pid)


def proc_supported() -> bool:
    return os.path.isdir("/proc/self")
