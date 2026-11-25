"""Guards that keep Test clean (EVAL section 5, T7.3 and T7.4).

- Touching any Test instance, or running a `split: test` config, needs `--final`.
  Dev tuning can't wander onto Test by accident, even through a typo in an instance
  list or a config that runs a whole dataset.
- `--final` only runs on a clean checkout of an `eval-freeze-*` tag, so every final
  number comes from frozen code. Changes under `eval/results/` don't count.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path

from repro.config import EvalConfig

FREEZE_PREFIX = "eval-freeze-"
IGNORED_PREFIX = "eval/results/"
DEFAULT_TEST_SAMPLE = Path("eval/samples/test_50.txt")


class GuardError(Exception):
    pass


def load_test_sample(path: Path = DEFAULT_TEST_SAMPLE) -> set[str]:
    return set(path.read_text().split()) if path.exists() else set()


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                          text=True).stdout


def frozen_checkout(repo: Path = Path(".")) -> tuple[bool, str]:
    """(True, tag) when HEAD carries an eval-freeze-* tag and the tree is clean apart from
    eval/results/; otherwise (False, why)."""
    try:
        tags = [t for t in _git(repo, "tag", "--points-at", "HEAD").split()
                if t.startswith(FREEZE_PREFIX)]
        status = _git(repo, "status", "--porcelain", "--untracked-files=all")
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        return False, f"not frozen: cannot read git state ({exc})"
    if not tags:
        return False, f"not frozen: HEAD has no {FREEZE_PREFIX}* tag"
    dirty = [line[3:] for line in status.splitlines()
             if line[3:].strip('"') and not line[3:].strip('"').startswith(IGNORED_PREFIX)]
    if dirty:
        return False, f"not frozen: the checkout has changes ({', '.join(dirty[:5])})"
    return True, sorted(tags)[-1]


def check_access(cfg: EvalConfig, ids: Sequence[str] | None, *, final: bool,
                 test_sample: set[str],
                 frozen: Callable[[], tuple[bool, str]] = frozen_checkout) -> None:
    """Raises GuardError when this run may not go ahead. `ids` None means every instance
    of the dataset, which for SWT-bench includes Test."""
    if ids is None:
        touches = sorted(test_sample) if cfg.dataset == "swtbench" else []
    else:
        touches = sorted(set(ids) & test_sample)
    if (cfg.split == "test" or touches) and not final:
        what = (f"includes test_50 instances ({', '.join(touches[:5])}"
                f"{', ...' if len(touches) > 5 else ''})" if touches
                else "is a split: test config")
        raise GuardError(f"config {cfg.config_id} {what}; Test runs need --final, on a "
                         f"clean checkout of an {FREEZE_PREFIX}* tag")
    if final:
        ok, why = frozen()
        if not ok:
            raise GuardError(f"--final refused: {why}")
