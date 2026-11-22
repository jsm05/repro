"""The gold-agreement report and its explained exceptions (T6.D8, D-M6-19)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]


def _script() -> Any:
    spec = importlib.util.spec_from_file_location("gold_agreement",
                                                  ROOT / "scripts" / "gold_agreement.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["gold_agreement"] = mod
    spec.loader.exec_module(mod)
    return mod


def _row(iid: str, ours: str, official: str) -> dict[str, Any]:
    return {"instance_id": iid, "ours": ours, "official": official, "seconds": 1,
            "agree": (ours == "REPRODUCED") == (official == "resolved")}


EXC = {"a": {"official": "unresolved", "reason": "parser read a pip line as a test",
             "evidence": "report.json tests_base PASS_TO_FAIL"}}


def test_listed_exception_passes_and_is_written_up(tmp_path: Path) -> None:
    g = _script()
    rows = [_row("a", "REPRODUCED", "unresolved"), _row("b", "REPRODUCED", "resolved")]
    out = tmp_path / "gold.md"
    assert g.write_report(rows, out, 2, EXC) == 2
    text = out.read_text()
    assert "2 of 10" not in text and "2 of 2" in text
    assert "explained exception" in text and "parser read a pip line as a test" in text
    assert "report.json tests_base PASS_TO_FAIL" in text


def test_exception_does_not_cover_our_own_failure_or_a_changed_official_verdict(
        tmp_path: Path) -> None:
    g = _script()
    out = tmp_path / "gold.md"
    # Our oracle didn't reproduce it: the exception can't help.
    assert g.write_report([_row("a", "WRONG_TEST", "unresolved")], out, 1, EXC) == 0
    # The official verdict moved away from what the exception explains: stale, not applied.
    assert g.write_report([_row("a", "REPRODUCED", "error: build")], out, 1, EXC) == 0


def test_the_checked_in_exceptions_file_loads() -> None:
    g = _script()
    exc = g.load_exceptions(ROOT / "eval" / "gold-exceptions.yaml")
    assert set(exc) == {"django__django-15127", "django__django-16662"}
    for e in exc.values():
        assert e["official"] and e["reason"] and e["evidence"]
