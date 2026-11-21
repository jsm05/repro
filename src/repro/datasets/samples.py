"""Seeded, repo-stratified Dev and Test samples (EVAL section 2, pre-registration).

The repo is the instance id up to its last "-" (e.g. "django__django-11099" ->
"django__django"). Test is drawn first, proportionally to each repo's share
(largest-remainder rounding); Dev is drawn the same way from what is left.
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from dataclasses import dataclass


def repo_of(instance_id: str) -> str:
    return instance_id.rsplit("-", 1)[0]


def allocate(sizes: dict[str, int], n: int) -> dict[str, int]:
    """Split n across groups proportionally to their sizes (largest remainder, ties by name)."""
    total = sum(sizes.values())
    if n > total:
        raise ValueError(f"asked for {n} from {total}")
    exact = {g: n * s / total for g, s in sizes.items()}
    out = {g: int(x) for g, x in exact.items()}
    left = n - sum(out.values())
    order = sorted(sizes, key=lambda g: (-(exact[g] - out[g]), g))
    for g in order:
        if left == 0:
            break
        if out[g] < sizes[g]:
            out[g] += 1
            left -= 1
    return out


def _stratified(ids: Sequence[str], n: int, rng: random.Random) -> list[str]:
    groups: dict[str, list[str]] = {}
    for i in sorted(ids):
        groups.setdefault(repo_of(i), []).append(i)
    alloc = allocate({g: len(v) for g, v in groups.items()}, n)
    picked: list[str] = []
    for g in sorted(groups):
        picked.extend(rng.sample(groups[g], alloc[g]))
    rng.shuffle(picked)  # committed order matters: Test-20 is the first 20
    return picked


@dataclass(frozen=True)
class Samples:
    dev: list[str]
    test: list[str]


def make_samples(ids: Sequence[str], seed: int, n_dev: int = 15, n_test: int = 50) -> Samples:
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate instance ids")
    rng = random.Random(seed)
    test = _stratified(ids, n_test, rng)
    rest = sorted(set(ids) - set(test))
    dev = _stratified(rest, n_dev, rng)
    return Samples(dev=dev, test=test)
