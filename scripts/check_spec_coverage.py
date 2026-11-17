"""Fail if a test ID listed under a finished milestone in TEST_SPEC.md has no test.

A test covers an ID when its docstring's first line starts with that ID. Several IDs
may lead the line, separated by commas or spaces ("T1.2, T1.8: ..."). Add a milestone
to FINISHED when its done criteria are ticked.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FINISHED: set[str] = {"M1"}

ID = r"(?:T\d+\.[A-Za-z]?\d+|V\d+\.\d+|C\d+|O-\d+)"
ROW_ID = re.compile(rf"^\|\s*({ID})\s*\|")
SECTION = re.compile(r"^##\s+(M\d+)\b")
LEADING_IDS = re.compile(rf"^((?:{ID}[\s,]*)+)")


def spec_ids() -> dict[str, set[str]]:
    by_milestone: dict[str, set[str]] = {}
    current: str | None = None
    for line in (ROOT / "TEST_SPEC.md").read_text().splitlines():
        if line.startswith("## "):
            m = SECTION.match(line)
            current = m.group(1) if m else None
            continue
        row = ROW_ID.match(line)
        if row and current:
            by_milestone.setdefault(current, set()).add(row.group(1))
    return by_milestone


def covered_ids() -> set[str]:
    found: set[str] = set()
    for path in (ROOT / "tests").rglob("*.py"):
        if "fixtures" in path.parts:
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                doc = ast.get_docstring(node)
                if not doc:
                    continue
                head = LEADING_IDS.match(doc.strip().splitlines()[0])
                if head:
                    found.update(re.findall(ID, head.group(1)))
    return found


def main() -> int:
    ids = spec_ids()
    have = covered_ids()
    missing = [
        f"{m}: {test_id}"
        for m in sorted(FINISHED)
        for test_id in sorted(ids.get(m, set()))
        if test_id not in have
    ]
    if missing:
        print("Spec IDs without a test:")
        print("\n".join("  " + item for item in missing))
        return 1
    total = sum(len(ids.get(m, set())) for m in FINISHED)
    names = ", ".join(sorted(FINISHED)) or "none yet"
    print(f"spec coverage ok: {total} IDs covered (finished: {names})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
