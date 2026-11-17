"""Export SWT-bench Verified instance ids (run in bootstrap.yml; needs `datasets` + network).

Writes eval/samples/verified_ids.txt (sorted, one per line) and prints the dataset's
split names and column names as JSON so box notes can record them.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from datasets import load_dataset  # type: ignore[import-not-found]

DATASET = "nmuendler/SWT-Bench_Verified_bm25_27k_zsp"


def main() -> int:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "eval/samples/verified_ids.txt")
    dd = load_dataset(DATASET)
    split = max(dd, key=lambda name: len(dd[name]))
    ids = sorted(set(dd[split]["instance_id"]))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(ids) + "\n")
    info = {
        "dataset": DATASET,
        "splits": {name: len(dd[name]) for name in dd},
        "split_used": split,
        "columns": dd[split].column_names,
        "n_ids": len(ids),
    }
    print(json.dumps(info))
    return 0


if __name__ == "__main__":
    sys.exit(main())
