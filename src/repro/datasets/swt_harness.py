"""Bridge to the pinned SWT-bench harness (DESIGN 5.3, D-M6-1).

Test commands and log parsers come from SWT-bench's own `constants.py` and
`log_parsers.py`, loaded by file path. Both are stdlib-only, so no install is needed,
and loading them under private names avoids a clash with the harness's top-level
package, which is called `src`. Building images needs the full harness (Docker SDK and
friends), so that runs in a subprocess with the checkout as its working directory.

The checkout lives at $REPRO_SWT_BENCH (default /tmp/swt-bench), at SWT_BENCH_REF.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from collections.abc import Callable, Sequence
from functools import cache
from importlib import resources
from pathlib import Path
from types import ModuleType
from typing import Any, Literal

SWT_BENCH_REF = "be12d4c8e7a5315a6bd60b0cad72736e96a4af3e"
Framework = Literal["pytest", "django", "sympy"]


class HarnessMissing(RuntimeError):
    pass


def swt_root() -> Path:
    root = Path(os.environ.get("REPRO_SWT_BENCH", "/tmp/swt-bench"))
    if not (root / "src" / "constants.py").is_file():
        raise HarnessMissing(
            f"SWT-bench checkout not found at {root}; clone it at {SWT_BENCH_REF} and set "
            "REPRO_SWT_BENCH")
    return root


@cache
def _module(name: str) -> ModuleType:
    path = swt_root() / "src" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"_repro_swt_{name}", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def framework(repo: str) -> Framework:
    if repo == "django/django":
        return "django"
    if repo == "sympy/sympy":
        return "sympy"
    return "pytest"


def test_command(repo: str, version: str) -> str:
    return str(_module("constants").MAP_REPO_TO_TEST_FRAMEWORK[repo][version])


def install_spec(repo: str, version: str) -> dict[str, Any]:
    return dict(_module("constants").MAP_VERSION_TO_INSTALL[repo][version])


def eval_commands(repo: str, version: str) -> list[str]:
    return [str(c) for c in install_spec(repo, version).get("eval_commands", [])]


def parser_name(repo: str) -> str:
    return str(_module("log_parsers").MAP_REPO_TO_PARSER[repo].__name__)


def log_parser(repo: str) -> Callable[[str], dict[str, str]]:
    fn: Callable[[str], dict[str, str]] = _module("log_parsers").MAP_REPO_TO_PARSER[repo]
    return fn


def directives(repo: str, test_files: Sequence[str]) -> list[str]:
    """Same transform as SWT-bench's get_test_directives: Django wants module labels."""
    if repo != "django/django":
        return list(test_files)
    out = []
    for d in test_files:
        d = d[: -len(".py")] if d.endswith(".py") else d
        d = d[len("tests/"):] if d.startswith("tests/") else d
        out.append(d.replace("/", "."))
    return out


def image_keys(records: Sequence[dict[str, Any]], build: bool = False,
               timeout_s: float = 5400) -> dict[str, dict[str, str]]:
    """Ask the harness for each instance's image names, building them if asked."""
    driver = resources.files("repro.datasets").joinpath("_swt_driver.py")
    payload = json.dumps({"records": list(records), "build": build})
    try:
        proc = subprocess.run([sys.executable, str(driver)], input=payload, text=True,
                              capture_output=True, cwd=swt_root(), timeout=timeout_s,
                              check=False)
    except subprocess.TimeoutExpired:
        print_build_logs()
        raise
    for line in proc.stdout.splitlines():
        if line.startswith("REPRO_JSON "):
            result: dict[str, dict[str, str]] = json.loads(line[len("REPRO_JSON "):])
            return result
    print_build_logs()
    raise RuntimeError(f"SWT-bench image step failed (exit {proc.returncode}): "
                       f"{(proc.stderr or proc.stdout)[-2000:]}")


def print_build_logs(lines: int = 60) -> None:
    """Show the tails of the harness's image build logs (they go to files, not stdout)."""
    root = swt_root() / "image_build_logs"
    if not root.is_dir():
        return
    for log in sorted(root.rglob("*.log"), key=lambda p: p.stat().st_mtime)[-3:]:
        tail = log.read_text(errors="replace").splitlines()[-lines:]
        print(f"--- {log} (last {lines} lines) ---", *tail, sep="\n", file=sys.stderr,
              flush=True)
