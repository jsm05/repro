"""Write the pre-registered samples: eval/samples/dev_15.txt and eval/samples/test_50.txt.

Both come from the 433 SWT-bench Verified ids, stratified by repo (the instance id minus
its number). Test gets each repo's proportional share of 50 by largest remainder, then
Dev gets the same treatment over what's left, so the two never overlap. Each list is
written in a seeded shuffled order: Test-20, the optional ablation set, is the first 20
lines of test_50.txt, so the order has to be random too.

The seed was fixed before the first run of this script and is never changed. Rerunning
regenerates both files exactly (T7.1). The pilot may overlap either list, since no model
runs on pilot instances.

    python scripts/make_splits.py

Instances that fail the gold check are listed in eval/gold-exclusions.yaml and dropped
into dev_13.txt and test_47.txt (D-M7-6); dev_15/test_50 stay as pre-registered.
"""

from __future__ import annotations

import random
import sys
from collections import Counter
from pathlib import Path

SEED = 20261125  # the date the samples were drawn, picked before any output was seen
TEST_N = 50
DEV_N = 15
SAMPLES = Path(__file__).resolve().parent.parent / "eval" / "samples"


def repo_of(instance_id: str) -> str:
    return instance_id.rsplit("-", 1)[0]


def allocate(counts: dict[str, int], n: int) -> dict[str, int]:
    """Largest-remainder (Hamilton) allocation of n slots in proportion to counts.
    Ties on the remainder go to the larger repo, then alphabetically."""
    total = sum(counts.values())
    exact = {r: n * c / total for r, c in counts.items()}
    quota = {r: int(e) for r, e in exact.items()}
    left = n - sum(quota.values())
    order = sorted(counts, key=lambda r: (-(exact[r] - quota[r]), -counts[r], r))
    for r in order[:left]:
        quota[r] += 1
    return quota


def _draw(pool: list[str], n: int, rng: random.Random) -> list[str]:
    by_repo: dict[str, list[str]] = {}
    for i in sorted(pool):
        by_repo.setdefault(repo_of(i), []).append(i)
    quota = allocate(dict(Counter({r: len(v) for r, v in by_repo.items()})), n)
    picked: list[str] = []
    for repo in sorted(by_repo):
        picked += rng.sample(by_repo[repo], quota[repo])
    rng.shuffle(picked)
    return picked


def make_splits(verified: list[str]) -> tuple[list[str], list[str]]:
    """Returns (dev, test), each in its committed order."""
    rng = random.Random(SEED)
    test = _draw(verified, TEST_N, rng)
    rest = [i for i in verified if i not in set(test)]
    dev = _draw(rest, DEV_N, rng)
    return dev, test


def apply_exclusions(dev: list[str], test: list[str],
                     excluded: set[str]) -> tuple[list[str], list[str]]:
    """The runnable samples: the pre-registered lists minus instances that failed the gold
    check (eval/gold-exclusions.yaml), order kept. dev_15/test_50 themselves never change."""
    return [i for i in dev if i not in excluded], [i for i in test if i not in excluded]


def main() -> int:
    verified = (SAMPLES / "verified_ids.txt").read_text().split()
    dev, test = make_splits(verified)
    (SAMPLES / "dev_15.txt").write_text("\n".join(dev) + "\n")
    (SAMPLES / "test_50.txt").write_text("\n".join(test) + "\n")
    import yaml

    excl = SAMPLES.parent / "gold-exclusions.yaml"
    excluded = set(yaml.safe_load(excl.read_text()) or {}) if excl.exists() else set()
    dev_run, test_run = apply_exclusions(dev, test, excluded)
    (SAMPLES / f"dev_{len(dev_run)}.txt").write_text("\n".join(dev_run) + "\n")
    (SAMPLES / f"test_{len(test_run)}.txt").write_text("\n".join(test_run) + "\n")
    for name, ids in (("dev_15", dev), ("test_50", test), ("dev run", dev_run),
                      ("test run", test_run)):
        mix = Counter(repo_of(i) for i in ids)
        print(name, dict(sorted(mix.items(), key=lambda kv: -kv[1])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
