"""The six tools, run against Sandbox A (the agent's long-lived, dirty workspace).

Only files written with `write_test` become the candidate; the diff is taken against
the pristine repo and checked by the gate in fresh sandboxes the agent never touched.
"""

from __future__ import annotations

import posixpath
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from repro.baseline.retrieval import RepoFiles
from repro.baseline.single_prompt import _test_ids
from repro.gate.candidate import diff_from_workspace
from repro.gate.checks import GateConfig, OnlineGate, SandboxFactory
from repro.models import Candidate, GateVerdict
from repro.sandbox.base import Sandbox, SandboxPathError
from repro.sandbox.treehash import is_test_path

BASH_TIMEOUT_S = 60.0
VIEW_MAX_LINES = 400
# LocalSandbox has no network namespace; pointing every proxy variable at a closed
# port makes ordinary HTTP clients fail. Real isolation is DockerSandbox (M6).
NO_NETWORK_ENV = {
    "http_proxy": "http://127.0.0.1:9", "https_proxy": "http://127.0.0.1:9",
    "HTTP_PROXY": "http://127.0.0.1:9", "HTTPS_PROXY": "http://127.0.0.1:9",
    "ALL_PROXY": "http://127.0.0.1:9", "all_proxy": "http://127.0.0.1:9",
    "no_proxy": "", "NO_PROXY": "", "PIP_NO_INDEX": "1",
}
TOOLS = ("bash", "view", "write_test", "check", "submit", "abstain")


def _fn(name: str, desc: str, props: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {"type": "function", "function": {
        "name": name, "description": desc,
        "parameters": {"type": "object", "properties": props, "required": required}}}


# Native schemas for routes that need them (Route.native_tools). The replies come back as
# tool calls and are turned into the same text actions, so the loop is unchanged.
TOOL_SCHEMAS: list[dict[str, Any]] = [
    _fn("bash", "Run a shell command in the repo root.", {"cmd": {"type": "string"}}, ["cmd"]),
    _fn("view", "Show lines start..end of a file (1-based).",
        {"path": {"type": "string"}, "start": {"type": "integer"}, "end": {"type": "integer"}},
        ["path"]),
    _fn("write_test", "Create a test file, or append to an existing one.",
        {"path": {"type": "string"}, "content": {"type": "string"},
         "mode": {"type": "string", "enum": ["new", "append"]}}, ["path", "content"]),
    _fn("check", "Run the verification gate on your test files.",
        {"test_ids": {"type": "array", "items": {"type": "string"}}}, []),
    _fn("submit", "Finish; allowed only after check passed on the current files.", {}, []),
    _fn("abstain", "Give up with a reason.", {"reason": {"type": "string"}}, ["reason"]),
]


class ToolError(Exception):
    """A bad call: shown to the model as an error, counted toward the error limit."""


@dataclass
class ToolResult:
    tool: str
    text: str  # full output (truncated later on entry to history)
    stub: str  # one-line summary used once the result is old
    is_view: bool = False
    meta: dict[str, Any] = field(default_factory=dict)


# Argument names the model keeps using instead of ours. Mapping them costs nothing and
# keeps real mistakes (unknown arguments, wrong types) as errors (D-M7-7).
ARG_ALIASES: dict[str, dict[str, str]] = {
    "bash": {"command": "cmd", "script": "cmd", "shell_command": "cmd"},
    "view": {"file": "path", "file_path": "path", "filename": "path", "start_line": "start",
             "end_line": "end", "line_start": "start", "line_end": "end"},
    "write_test": {"file": "path", "file_path": "path", "filename": "path", "text": "content"},
}


def normalize_args(tool: str, args: dict[str, Any], workdir: str) -> dict[str, Any]:
    """Map known aliases to our names, make absolute paths inside the repo relative, and
    unwrap a nested {"action": tool, "args": {...}}. Unknown keys are left for `_require`."""
    out = dict(args)
    if set(out) <= {"action", "args", "tool", "name"} and isinstance(out.get("args"), dict):
        out = dict(out["args"])
        if set(out) <= {"action", "args", "tool", "name"} and isinstance(out.get("args"), dict):
            out = dict(out["args"])
    aliases = ARG_ALIASES.get(tool, {})
    for old, new in aliases.items():
        if old in out and new not in out:
            out[new] = out.pop(old)
    root = workdir.rstrip("/") + "/"
    p = out.get("path")
    if tool in ("view", "write_test") and isinstance(p, str) and p.startswith(root):
        out["path"] = p[len(root):]
    return out


def _require(args: dict[str, Any], spec: dict[str, tuple[type | tuple[type, ...], bool]]) -> None:
    unknown = set(args) - set(spec)
    if unknown:
        raise ToolError(f"unexpected argument(s): {', '.join(sorted(unknown))}; "
                        f"expected {', '.join(spec) or 'none'}")
    for name, (types, required) in spec.items():
        if name not in args:
            if required:
                raise ToolError(f"missing argument {name!r}")
            continue
        value = args[name]
        if isinstance(value, bool) and bool not in (types if isinstance(types, tuple)
                                                     else (types,)):
            raise ToolError(f"argument {name!r} has the wrong type")
        if not isinstance(value, types):
            raise ToolError(f"argument {name!r} has the wrong type")


class Workspace:
    def __init__(
        self,
        sandbox: Sandbox,
        repo: RepoFiles,
        gate_factory: SandboxFactory,
        gate_config: GateConfig,
        *,
        bash_timeout_s: float = BASH_TIMEOUT_S,
        gate: Callable[[Candidate], GateVerdict] | None = None,
    ):
        self.sandbox = sandbox
        self.repo = repo
        self.bash_timeout_s = bash_timeout_s
        self._gate = gate or OnlineGate(gate_factory, gate_config).verify
        self._repo_files = set(repo.list_files())
        self.written: list[str] = []

    def _original(self, path: str) -> str | None:
        return self.repo.read(path) if path in self._repo_files else None

    # -- tools ------------------------------------------------------------------------

    def bash(self, args: dict[str, Any]) -> ToolResult:
        _require(args, {"cmd": (str, True)})
        cmd = args["cmd"].strip()
        if not cmd:
            raise ToolError("cmd is empty")
        res = self.sandbox.exec(cmd, timeout_s=self.bash_timeout_s, env=NO_NETWORK_ENV)
        out = res.stdout
        if res.stderr:
            out += ("\n" if out and not out.endswith("\n") else "") + res.stderr
        status = "timed out" if res.timed_out else f"exit {res.exit_code}"
        lines = out.count("\n")
        text = f"[{status}]\n{out}" if out else f"[{status}, no output]"
        short = cmd if len(cmd) <= 60 else cmd[:57] + "..."
        return ToolResult("bash", text, f"[bash: {short}, {status}, {lines} lines, elided]",
                          meta={"cmd": cmd, "exit": res.exit_code, "timed_out": res.timed_out})

    def view(self, args: dict[str, Any]) -> ToolResult:
        _require(args, {"path": (str, True), "start": (int, False), "end": (int, False)})
        path = args["path"]
        try:
            data = self.sandbox.get_file(path).decode("utf-8", errors="replace")
        except FileNotFoundError:
            raise ToolError(f"no such file: {path}") from None
        except SandboxPathError as exc:
            raise ToolError(str(exc)) from None
        lines = data.splitlines()
        start = max(1, int(args.get("start", 1)))
        end = int(args.get("end", start + VIEW_MAX_LINES - 1))
        end = min(len(lines), end, start + VIEW_MAX_LINES - 1)
        if start > max(1, len(lines)):
            raise ToolError(f"{path} has only {len(lines)} lines")
        body = "\n".join(f"{i:>5}  {lines[i - 1]}" for i in range(start, end + 1))
        text = f"{path} lines {start}-{end} of {len(lines)}\n{body}"
        return ToolResult("view", text, f"[view: {path} lines {start}-{end}, elided]",
                          is_view=True, meta={"path": path, "start": start, "end": end})

    def write_test(self, args: dict[str, Any]) -> ToolResult:
        _require(args, {"path": (str, True), "content": (str, True), "mode": (str, False)})
        path, content = args["path"], args["content"]
        mode = args.get("mode", "new")
        if mode not in ("new", "append"):
            raise ToolError('mode must be "new" or "append"')
        if (path.startswith("/") or posixpath.normpath(path) != path or path.startswith("..")
                or not path.endswith(".py")):
            raise ToolError(f"{path!r} is not a relative .py path inside the repo")
        if not is_test_path(path) or posixpath.basename(path) == "conftest.py":
            raise ToolError(f"{path!r} is not a test file; write under a tests directory or "
                            "name it test_*.py (conftest.py is not allowed)")
        if not content.strip():
            raise ToolError("content is empty")
        try:
            existing: str | None = self.sandbox.get_file(path).decode("utf-8", errors="replace")
        except FileNotFoundError:
            existing = None
        except SandboxPathError as exc:
            raise ToolError(str(exc)) from None
        if mode == "new" and existing is not None and path not in self.written:
            raise ToolError(f"{path} already exists; use mode \"append\" to add to it")
        if mode == "append" and existing is None:
            raise ToolError(f"{path} does not exist; use mode \"new\"")
        if mode == "append":
            assert existing is not None
            new = existing.rstrip("\n") + "\n\n\n" + content.strip("\n") + "\n"
        else:
            new = content.rstrip("\n") + "\n"
        self.sandbox.put_file(path, new)
        if path not in self.written:
            self.written.append(path)
        n = len(self.default_ids())
        return ToolResult("write_test", f"wrote {path} ({mode}); {n} test(s) in your files",
                          f"[write_test: {path} ({mode})]", meta={"path": path, "mode": mode})

    # -- candidate and gate -----------------------------------------------------------

    def current_diff(self) -> str:
        if not self.written:
            return ""
        return diff_from_workspace(self.sandbox, self._original, self.written)

    def default_ids(self) -> list[str]:
        ids: list[str] = []
        for path in self.written:
            try:
                new = self.sandbox.get_file(path).decode("utf-8", errors="replace")
            except FileNotFoundError:
                continue
            before = set(_test_ids(path, self._original(path) or ""))
            ids += [t for t in _test_ids(path, new) if t not in before]
        return ids

    def candidate(self, test_ids: list[str] | None = None) -> Candidate | None:
        diff = self.current_diff()
        if not diff:
            return None
        ids = test_ids if test_ids else self.default_ids()
        if not ids:
            return None
        return Candidate(diff=diff, test_ids=ids, origin="agent")

    def check(self, args: dict[str, Any]) -> tuple[ToolResult, Candidate, GateVerdict]:
        _require(args, {"test_ids": (list, False)})
        ids = args.get("test_ids") or None
        if ids is not None and not all(isinstance(t, str) and t for t in ids):
            raise ToolError("test_ids must be a list of strings")
        if not self.written:
            raise ToolError("no test files written yet; use write_test first")
        if ids:
            files = tuple(f"{p}::" for p in self.written)
            bad = [t for t in ids if not t.startswith(files)]
            if bad:
                raise ToolError(f"test ids not in your test files: {', '.join(bad[:5])}")
        cand = self.candidate(ids)
        if cand is None:
            raise ToolError("your test files contain no test functions")
        v = self._gate(cand)
        if v.passed:
            head = (f"GATE PASSED: {len(v.reproducing_tests)} test(s) fail on the buggy code "
                    "for the right reason, stably, and run repo code. You may submit.")
        else:
            head = f"GATE FAILED at {v.failed_check}: {v.reason}"
        lines = [head]
        for tid, statuses in v.test_status.items():
            lines.append(f"  {tid}: {' '.join(s.value for s in statuses)}")
        for tid, sig in v.signatures.items():
            lines.append(f"  failure {tid}: {sig}")
        verdict = "PASS" if v.passed else f"FAIL at {v.failed_check}"
        result = ToolResult("check", "\n".join(lines), f"[check: {verdict}]",
                            meta={"passed": v.passed, "failed_check": v.failed_check})
        return result, cand, v
