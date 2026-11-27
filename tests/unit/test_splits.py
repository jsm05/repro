"""Pre-registered Dev and Test samples (T7.1)."""

from __future__ import annotations

import importlib.util
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SAMPLES = ROOT / "eval" / "samples"


def _script() -> Any:
    spec = importlib.util.spec_from_file_location("make_splits",
                                                  ROOT / "scripts" / "make_splits.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["make_splits"] = mod
    spec.loader.exec_module(mod)
    return mod


def _read(name: str) -> list[str]:
    return (SAMPLES / name).read_text().split()


def test_t7_1_committed_samples_regenerate_exactly_and_are_disjoint() -> None:
    """T7.1: dev_15.txt and test_50.txt regenerate exactly from the seed and are disjoint."""
    m = _script()
    dev, test = m.make_splits(_read("verified_ids.txt"))
    assert _read("dev_15.txt") == dev
    assert _read("test_50.txt") == test
    assert len(dev) == 15 and len(test) == 50
    assert not set(dev) & set(test)
    assert set(dev) | set(test) <= set(_read("verified_ids.txt"))


def test_t7_1_test_is_stratified_by_repo() -> None:
    """Each repo gets its proportional share of the 50 (largest remainder), within one."""
    ids = _read("verified_ids.txt")
    pop = Counter(i.rsplit("-", 1)[0] for i in ids)
    got = Counter(i.rsplit("-", 1)[0] for i in _read("test_50.txt"))
    for repo, n in pop.items():
        assert abs(got.get(repo, 0) - 50 * n / len(ids)) < 1, repo


def test_t7_1_test_order_is_shuffled_so_test_20_is_a_random_subsample() -> None:
    """Test-20 is the first 20 in committed order, so the file must not be grouped by repo."""
    test = _read("test_50.txt")
    assert test != sorted(test)
    assert len({i.rsplit("-", 1)[0] for i in test[:20]}) >= 3


def test_allocation_is_largest_remainder() -> None:
    m = _script()
    q = m.allocate({"a": 216, "b": 73, "c": 4, "d": 1}, 10)
    assert sum(q.values()) == 10 and q["a"] == 7 and q["b"] == 3 and q["c"] == 0


def test_exclusions_give_the_runnable_samples() -> None:
    """D-M7-6: dev_13 and test_47 are the pre-registered lists minus the gold-check
    exclusions, each of which has a reason and belongs to the sample it names."""
    import yaml

    m = _script()
    exc = yaml.safe_load((ROOT / "eval" / "gold-exclusions.yaml").read_text())
    assert exc and all(e["reason"] and e["sample"] in ("dev_15", "test_50") for e in exc.values())
    dev, test = _read("dev_15.txt"), _read("test_50.txt")
    for iid, e in exc.items():
        assert iid in (dev if e["sample"] == "dev_15" else test), iid
    dev_run, test_run = m.apply_exclusions(dev, test, set(exc))
    assert _read("dev_13.txt") == dev_run and _read("test_47.txt") == test_run
    assert dev_run == [i for i in dev if i not in exc]  # order kept
    assert not set(dev_run + test_run) & set(exc)
