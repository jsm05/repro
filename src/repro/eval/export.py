"""SWT-bench predictions export (DESIGN 5.9).

One line per instance in the run: `instance_id`, `model_name_or_path`, `model_patch`.
NO_TEST instances are exported with an empty patch so the official harness has the
same denominator as our own oracle.
"""

from __future__ import annotations

import json
from pathlib import Path

from repro.eval.rows import ResultRow


def predictions(rows: list[ResultRow], run_dir: Path, model_name: str) -> list[dict[str, str]]:
    out = []
    for r in sorted(rows, key=lambda r: r.instance_id):
        diff = run_dir / "candidates" / f"{r.instance_id}.diff"
        patch = diff.read_text() if r.candidate_sha256 and diff.exists() else ""
        out.append({"instance_id": r.instance_id, "model_name_or_path": model_name,
                    "model_patch": patch})
    return out


def predictions_jsonl(rows: list[ResultRow], run_dir: Path, model_name: str) -> str:
    return "".join(json.dumps(p, sort_keys=True) + "\n"
                   for p in predictions(rows, run_dir, model_name))
