"""Merge per-instance SWT-bench harness results into one official_report.json.

    python scripts/merge_official.py <dir with downloaded artifacts> <predictions.jsonl> <out>
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


def find_resolution(obj: Any, instance: str, key: str = "") -> str | None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            found = find_resolution(v, instance, str(k))
            if found:
                return found
    elif isinstance(obj, list) and instance in obj and "resolved" in key.lower():
        return "unresolved" if "unresolved" in key.lower() else "resolved"
    return None


def main() -> int:
    root, preds, out = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
    ids = [json.loads(x)["instance_id"] for x in preds.read_text().splitlines() if x.strip()]
    reports = [json.loads(p.read_text()) for p in sorted(root.rglob("*.json"))
               if p.stat().st_size < 50_000_000]
    result: dict[str, str] = {}
    for iid in ids:
        for rep in reports:
            found = find_resolution(rep, iid)
            if found:
                result[iid] = found
                break
        else:
            result[iid] = "not scored"
    summary = {
        "resolved": sorted(i for i, v in result.items() if v == "resolved"),
        "unresolved": sorted(i for i, v in result.items() if v == "unresolved"),
        "not_scored": sorted(i for i, v in result.items() if v == "not scored"),
        "per_instance": result,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, indent=1, sort_keys=True) + "\n")
    print(f"resolved {len(summary['resolved'])}/{len(ids)}; not scored "
          f"{len(summary['not_scored'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
