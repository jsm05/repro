"""Guards on Test: frozen code for final runs (T7.3), no peeking without --final (T7.4)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from repro.cli import main
from repro.config import EvalConfig
from repro.eval.guards import GuardError, check_access, frozen_checkout

ROOT = Path(__file__).resolve().parents[2]
TEST50 = set((ROOT / "eval" / "samples" / "test_50.txt").read_text().split())
ONE_TEST = sorted(TEST50)[0]
DEV = EvalConfig(config_id="dev-a", kind="agent", dataset="swtbench", split="dev")


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t",
                    "-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false", *args],
                   check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "r"
    (r / "eval" / "results").mkdir(parents=True)
    (r / "a.py").write_text("x = 1\n")
    (r / "eval" / "results" / "keep.txt").write_text("")
    _git(r, "init", "-q")
    _git(r, "add", "-A")
    _git(r, "commit", "-qm", "c")
    return r


def test_t7_3_final_needs_a_clean_freeze_tag_checkout(repo: Path) -> None:
    """T7.3: refused off a tag; ok on eval-freeze-*; edits refused except under eval/results/."""
    ok, why = frozen_checkout(repo)
    assert not ok and "eval-freeze-" in why
    _git(repo, "tag", "other-tag")
    assert not frozen_checkout(repo)[0]
    _git(repo, "tag", "eval-freeze-v1")
    assert frozen_checkout(repo) == (True, "eval-freeze-v1")
    (repo / "eval" / "results" / "row.jsonl").write_text("{}\n")
    (repo / "eval" / "results" / "keep.txt").write_text("changed\n")
    assert frozen_checkout(repo)[0]
    (repo / "a.py").write_text("x = 2\n")
    ok, why = frozen_checkout(repo)
    assert not ok and "a.py" in why
    (repo / "a.py").write_text("x = 1\n")
    (repo / "stray.py").write_text("")
    assert not frozen_checkout(repo)[0]


def test_t7_3_test_split_config_needs_final_and_freeze() -> None:
    main_cfg = DEV.model_copy(update={"split": "test", "config_id": "A-main"})
    with pytest.raises(GuardError, match="--final"):
        check_access(main_cfg, [ONE_TEST], final=False, test_sample=TEST50,
                     frozen=lambda: (True, "eval-freeze-v1"))
    with pytest.raises(GuardError, match="not frozen"):
        check_access(main_cfg, [ONE_TEST], final=True, test_sample=TEST50,
                     frozen=lambda: (False, "not frozen: HEAD has no eval-freeze-* tag"))
    check_access(main_cfg, [ONE_TEST], final=True, test_sample=TEST50,
                 frozen=lambda: (True, "eval-freeze-v1"))


def test_t7_4_dev_config_touching_test_is_refused_without_final() -> None:
    """T7.4: a dev config that includes any test_50 instance is refused without --final."""
    with pytest.raises(GuardError, match=ONE_TEST):
        check_access(DEV, ["django__django-0", ONE_TEST], final=False, test_sample=TEST50,
                     frozen=lambda: (True, ""))
    # Running a whole SWT-bench dataset (no instance list) would include Test too.
    with pytest.raises(GuardError):
        check_access(DEV, None, final=False, test_sample=TEST50, frozen=lambda: (True, ""))
    check_access(DEV, ["django__django-0"], final=False, test_sample=TEST50,
                 frozen=lambda: (False, "nope"))
    fixtures = EvalConfig(config_id="f", kind="B0")
    check_access(fixtures, None, final=False, test_sample=TEST50, frozen=lambda: (False, ""))


def test_t7_4_cli_refuses_before_anything_runs(tmp_path: Path,
                                                monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(ROOT)
    cfg = tmp_path / "dev.yaml"
    cfg.write_text(DEV.model_copy(update={"instances": [ONE_TEST]}).to_yaml())
    res = CliRunner().invoke(main, ["eval", "--config", str(cfg), "--run-id", "x",
                                    "--results-dir", str(tmp_path / "res")])
    assert res.exit_code == 2
    assert "test_50" in res.output and ONE_TEST in res.output
    assert not (tmp_path / "res").exists()
