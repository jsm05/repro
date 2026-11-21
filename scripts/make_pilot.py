"""Write eval/samples/pilot_10.txt: 10 Verified instances spread across repos (seeded).

The pilot is used only with gold tests (no model runs), to check that our oracle and the
official SWT-bench harness agree (T6.D8). It may overlap the Test sample. sphinx is left
out because its harness command runs tests under tox, which this runner does not drive
yet (D-M6-5).
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

PER_REPO = {"django__django": 3, "sympy__sympy": 2, "matplotlib__matplotlib": 1,
            "scikit-learn__scikit-learn": 1, "pytest-dev__pytest": 1, "pydata__xarray": 1,
            "psf__requests": 1}
SEED = 6


def main() -> int:
    ids = Path("eval/samples/verified_ids.txt").read_text().split()
    rng = random.Random(SEED)
    picked: list[str] = []
    for repo, n in PER_REPO.items():
        pool = sorted(i for i in ids if i.rsplit("-", 1)[0] == repo)
        picked += sorted(rng.sample(pool, n))
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "eval/samples/pilot_10.txt")
    out.write_text("\n".join(picked) + "\n")
    print("\n".join(picked))
    return 0


if __name__ == "__main__":
    sys.exit(main())
