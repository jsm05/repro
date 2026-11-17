"""`repro verify` on fixtures."""

from __future__ import annotations

from click.testing import CliRunner

from repro.cli import main
from tests.helpers import FIXTURES_ROOT, load_cheats


def test_verify_gold_passes() -> None:
    """Gold test on f05 (appended to an existing file): gate PASS, oracle REPRODUCED."""
    out = CliRunner().invoke(
        main, ["verify", "--fixture", "f05_append_only", "--root", str(FIXTURES_ROOT)]
    )
    assert out.exit_code == 0, out.output
    assert "gate: PASS" in out.output
    assert "oracle: REPRODUCED" in out.output
    assert "F→P" in out.output


def test_verify_cheat_fails(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """A cheat patch passed with --patch/--test is rejected and the exit code is 1."""
    fx, _, meta, patch = next(c for c in load_cheats() if c[1] == "C3")
    p = tmp_path / "c3.patch"
    p.write_text(patch)
    args = ["verify", "--fixture", fx, "--root", str(FIXTURES_ROOT), "--patch", str(p),
            "--no-oracle"]
    for tid in meta["test_ids"]:  # type: ignore[attr-defined]
        args += ["--test", tid]
    out = CliRunner().invoke(main, args)
    assert out.exit_code == 1
    assert "FAIL at G0" in out.output
