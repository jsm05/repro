"""Export SWE-bench Verified dataset rows for SWT-bench Verified instances to JSONL
(needs `datasets` and network; runs in Actions). The rows hold sealed fields (the fix,
the real tests), so the file stays on the runner and is never committed.

    python scripts/export_instances.py --ids-file eval/samples/pilot_10.txt \
        --out eval/data/swt_records.jsonl
    python scripts/export_instances.py --config eval/configs/X.yaml --out ...
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# The original SWE-bench rows: the SWT bm25 variant's patches failed to parse (D-M6-12).
DATASET = "princeton-nlp/SWE-bench_Verified"
KEEP = ["repo", "instance_id", "base_commit", "patch", "test_patch", "problem_statement",
        "hints_text", "created_at", "version", "FAIL_TO_PASS", "PASS_TO_PASS",
        "environment_setup_commit"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids-file")
    ap.add_argument("--config")
    ap.add_argument("--out", default="eval/data/swt_records.jsonl")
    args = ap.parse_args()
    if args.config:
        sys.path.insert(0, "src")
        from repro.config import EvalConfig

        ids = EvalConfig.from_yaml(args.config).instance_ids()
    else:
        ids = [x.strip() for x in Path(args.ids_file).read_text().split() if x.strip()]
    from datasets import load_dataset  # type: ignore[import-not-found]

    dd = load_dataset(DATASET)
    split = max(dd, key=lambda name: len(dd[name]))
    rows = {r["instance_id"]: {k: r[k] for k in KEEP} for r in dd[split]
            if ids is None or r["instance_id"] in set(ids)}
    missing = sorted(set(ids or []) - set(rows))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(json.dumps(rows[i], sort_keys=True) + "\n" for i in sorted(rows)))
    print(f"wrote {len(rows)} records to {out}" + (f"; missing: {missing}" if missing else ""))
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
