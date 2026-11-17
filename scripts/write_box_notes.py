"""Write eval/box-notes.md from facts collected in bootstrap.yml (stdlib only).

Inputs come from files in a work dir (default /tmp/bootstrap) so a failed step still
leaves partial notes:
    runner.txt, disk_before.txt, disk_after_free.txt, disk_after_run.txt,
    images.txt, dataset.json, validate_seconds, validate.log, results/ (copied JSON)
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any


def read(work: Path, name: str, default: str = "(missing)") -> str:
    p = work / name
    return p.read_text().strip() if p.exists() else default


def find_resolution(obj: Any, instance: str, key: str = "") -> str | None:
    """Look for the instance id in lists under keys that mention 'resolved'."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            found = find_resolution(v, instance, str(k))
            if found:
                return found
    elif isinstance(obj, list) and instance in obj and "resolved" in key.lower():
        return "NOT resolved" if "unresolved" in key.lower() else "resolved"
    return None


def main() -> int:
    work = Path(os.environ.get("BOOTSTRAP_WORK", "/tmp/bootstrap"))
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "eval/box-notes.md")
    instance = os.environ.get("INSTANCE", "sympy__sympy-20590")

    verdict = "unknown (no results JSON found)"
    raw_results = []
    for path in sorted((work / "results").glob("**/*.json")):
        text = path.read_text()
        raw_results.append(f"### {path.name}\n\n```json\n{text[:4000]}\n```")
        try:
            found = find_resolution(json.loads(text), instance)
        except ValueError:
            found = None
        if found:
            verdict = found

    log_tail = "\n".join(read(work, "validate.log", "").splitlines()[-40:])
    lines = [
        "# Box notes (written by bootstrap.yml)",
        "",
        f"- Workflow run: {os.environ.get('RUN_URL', '(local)')}",
        f"- SWT-bench harness ref: `{os.environ.get('SWT_BENCH_REF', '?')}`",
        f"- Gold validation instance: `{instance}`: **{verdict}**",
        f"- Gold validation wall time: {read(work, 'validate_seconds')} s",
        "",
        "## Runner",
        "",
        "```",
        read(work, "runner.txt"),
        "```",
        "",
        "## Disk",
        "",
        "Before freeing space:",
        "```",
        read(work, "disk_before.txt"),
        "```",
        "After freeing space:",
        "```",
        read(work, "disk_after_free.txt"),
        "```",
        "After the gold run:",
        "```",
        read(work, "disk_after_run.txt"),
        "```",
        "",
        "## Docker images after the gold run",
        "",
        "```",
        read(work, "images.txt"),
        "```",
        "",
        "## Dataset",
        "",
        "```json",
        read(work, "dataset.json"),
        "```",
        "",
        "## Validation log (last 40 lines)",
        "",
        "```",
        log_tail or "(empty)",
        "```",
        "",
        "## Raw result files",
        "",
        *(raw_results or ["(none)"]),
        "",
    ]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines))
    print(f"wrote {out}: gold {verdict}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
