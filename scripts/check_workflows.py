"""Static safety check over .github/workflows/ (T6.10). Exit 1 on any problem.

Rules, because the repo goes public and holds free-tier API keys:
- no `pull_request_target`, and triggers only `push`, `schedule`, `workflow_dispatch`;
- secrets are referenced only in the `env` of a single step, never at workflow or job
  level, and only in steps that call models (`repro eval`, `repro llm`, live tests) or
  log in to Docker Hub;
- a scheduled workflow does nothing unless `eval/active_run.yaml` exists on main;
- every workflow has a `concurrency` group.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

import yaml

ALLOWED_TRIGGERS = {"push", "schedule", "workflow_dispatch"}
SECRET = re.compile(r"\$\{\{\s*secrets\.")
MODEL_STEP = re.compile(r"repro (eval|llm)\b|pytest -m live_llm|docker login")


def _triggers(doc: dict[Any, Any]) -> set[str]:
    on = doc.get("on", doc.get(True))  # PyYAML reads a bare `on:` key as True
    if isinstance(on, str):
        return {on}
    if isinstance(on, list):
        return set(on)
    return set(on or {})


def check(path: Path) -> list[str]:
    text = path.read_text()
    doc = yaml.safe_load(text) or {}
    name = path.name
    problems = []
    if "pull_request_target" in text:
        problems.append(f"{name}: uses pull_request_target")
    bad = _triggers(doc) - ALLOWED_TRIGGERS
    if bad:
        problems.append(f"{name}: triggers not allowed: {', '.join(sorted(bad))}")
    if "concurrency" not in doc:
        problems.append(f"{name}: no concurrency group")
    if SECRET.search(yaml.safe_dump(doc.get("env") or {})):
        problems.append(f"{name}: secrets in workflow-level env")
    for job_id, job in (doc.get("jobs") or {}).items():
        job = job or {}
        if SECRET.search(yaml.safe_dump(job.get("env") or {})):
            problems.append(f"{name}: job {job_id}: secrets in job-level env")
        for key in ("with", "container", "services", "strategy"):
            if SECRET.search(yaml.safe_dump(job.get(key) or {})):
                problems.append(f"{name}: job {job_id}: secrets in {key}")
        for i, step in enumerate(job.get("steps") or []):
            label = step.get("name") or step.get("uses") or f"step {i + 1}"
            body = yaml.safe_dump({k: v for k, v in step.items() if k != "env"})
            if SECRET.search(body):
                problems.append(f"{name}: {job_id}/{label}: secrets outside the step env")
            if SECRET.search(yaml.safe_dump(step.get("env") or {})) and not MODEL_STEP.search(
                    str(step.get("run", ""))):
                problems.append(f"{name}: {job_id}/{label}: secrets in a step that does not "
                                "call models or log in to Docker Hub")
    if "schedule" in _triggers(doc) and "eval/active_run.yaml" not in text:
        problems.append(f"{name}: scheduled but never checks eval/active_run.yaml")
    return problems


def main(argv: list[str]) -> int:
    root = Path(argv[1]) if len(argv) > 1 else Path(".github/workflows")
    problems = [p for f in sorted(root.glob("*.y*ml")) for p in check(f)]
    for p in problems:
        print(p)
    print(f"workflow check: {len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
