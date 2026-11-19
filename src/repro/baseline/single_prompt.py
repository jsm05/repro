"""Baseline B0 (one prompt), plus the prompt and parser B1 shares.

Both see only the AgentView (issue text and repo metadata) plus BM25-selected files
from the pristine repo. The model answers with one fenced code block naming a test
path; the harness turns that into a test-only diff.
"""

from __future__ import annotations

import ast
import posixpath
import re
from dataclasses import dataclass, field
from typing import Literal

from repro.baseline.retrieval import RepoFiles, select_context
from repro.gate.candidate import file_diff
from repro.llm.tokens import TokenEstimator
from repro.llm.types import LLM, LLMResponse
from repro.models import AgentView, Candidate
from repro.sandbox.treehash import is_test_path

PROMPT_CAP = 32_000
DEFAULT_PATH = "tests/test_repro_issue.py"

SYSTEM = """You write one pytest test that reproduces a reported bug.

The test must FAIL on the current (buggy) code and PASS once the bug is fixed.
Do not change source files. Do not use skip or xfail.

Answer with exactly one fenced code block, in this format:

```python path=<repo-relative test file> mode=<new|append>
<test code>
```

mode=new creates a new test file (it must live under a tests directory or be named
test_*.py). mode=append adds your code to the end of an existing test file; include
any imports you need. Put nothing else in the block."""

_BLOCK = re.compile(r"```(?:python|py)?[ \t]*([^\n]*)\n(.*?)```", re.DOTALL)
_ATTR = re.compile(r"(\w+)=(\S+)")


def build_messages(
    view: AgentView, repo: RepoFiles, estimator: TokenEstimator, prompt_cap: int = PROMPT_CAP
) -> list[dict[str, str]]:
    head = f"Repository: {view.repo}\n\nIssue:\n{view.issue_text}\n\nRelevant files:\n"
    fixed = estimator.estimate([{"role": "system", "content": SYSTEM},
                                {"role": "user", "content": head}])
    budget = max(0, int((prompt_cap - fixed) / 1.15) - 200)
    files = select_context(view.issue_text, repo, budget, estimator.count)
    body = "".join(f"\n### {path}\n```\n{text}\n```\n" for path, text in files)
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": head + body}]


def _test_ids(path: str, code: str) -> list[str]:
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return [f"{path}::{n}" for n in re.findall(r"^def (test_\w+)\s*\(", code, re.M)]
    funcs = (ast.FunctionDef, ast.AsyncFunctionDef)
    ids = []
    for node in tree.body:
        if isinstance(node, funcs) and node.name.startswith("test"):
            ids.append(f"{path}::{node.name}")
        elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            for item in node.body:
                if isinstance(item, funcs) and item.name.startswith("test"):
                    ids.append(f"{path}::{node.name}::{item.name}")
    return ids


def parse_candidate(text: str, repo: RepoFiles, origin: str) -> Candidate | None:
    """Turn a model answer into a test-only diff. None if the answer is unusable."""
    m = _BLOCK.search(text)
    if not m:
        return None
    attrs = dict(_ATTR.findall(m.group(1)))
    code = m.group(2).strip("\n") + "\n"
    path = attrs.get("path", DEFAULT_PATH).strip("`'\"")
    if path.startswith("/") or posixpath.normpath(path) != path or path.startswith(".."):
        return None
    if not path.endswith(".py") or not is_test_path(path):
        return None
    try:
        original: str | None = repo.read(path) if path in set(repo.list_files()) else None
    except OSError:
        original = None
    new = code if original is None else original.rstrip("\n") + "\n\n\n" + code
    ids = _test_ids(path, code)
    if not ids:
        return None
    return Candidate(diff=file_diff(path, original, new), test_ids=ids, origin=origin)


@dataclass
class BaselineResult:
    kind: Literal["B0", "B1"]
    gated: Candidate | None
    always_emit: Candidate | None
    responses: list[LLMResponse] = field(default_factory=list)
    samples: list[Candidate | None] = field(default_factory=list)

    @property
    def tokens(self) -> int:
        return sum(r.total_tokens for r in self.responses)

    @property
    def requests(self) -> int:
        return len(self.responses)

    @property
    def outcome(self) -> str:
        return "CANDIDATE" if self.always_emit is not None else "NO_TEST"


def run_b0(
    view: AgentView,
    repo: RepoFiles,
    llm: LLM,
    *,
    estimator: TokenEstimator,
    prompt_cap: int = PROMPT_CAP,
    max_tokens: int = 8000,
) -> BaselineResult:
    """One call, no execution. B0 has no gate, so gated and always-emit are the same."""
    messages = build_messages(view, repo, estimator, prompt_cap)
    resp = llm.complete(messages, max_tokens=max_tokens, temperature=0.0)
    cand = parse_candidate(resp.text, repo, "b0")
    return BaselineResult("B0", cand, cand, [resp], [cand])

