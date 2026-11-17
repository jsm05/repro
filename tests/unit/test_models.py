"""Data separation between what the agent sees and what is sealed (TEST_SPEC M1)."""

from __future__ import annotations

import pytest
from repro.datasets.fixtures import FixtureSource
from repro.models import AgentView, Candidate, SealedView

from tests.helpers import FIXTURE_NAMES


def added_lines(patch: str) -> list[str]:
    """Significant added lines of a diff: long enough to be distinctive, not imports."""
    out = []
    for line in patch.splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            text = line[1:].strip()
            if len(text) >= 12 and not text.startswith(("import ", "from ")):
                out.append(text)
    return out


def test_field_names_disjoint() -> None:
    """T1.14: AgentView and SealedView share no field names."""
    assert set(AgentView.model_fields).isdisjoint(SealedView.model_fields)


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_agent_view_has_no_sealed_text(fixture_source: FixtureSource, name: str) -> None:
    """T1.14: a serialised AgentView contains no text from the fix or the gold test."""
    inst = fixture_source.load(name).instance
    blob = inst.public.model_dump_json() + "\n".join(
        str(v) for v in inst.public.model_dump().values()
    )
    lines = added_lines(inst.sealed.gold_fix_patch) + added_lines(inst.sealed.gold_test_patch)
    assert lines, "fixture patches should have distinctive lines"
    for line in lines:
        assert line not in blob, f"sealed text leaked into AgentView: {line!r}"
    for test_id in inst.sealed.fail_to_pass:
        assert test_id.rsplit("::", 1)[-1] not in blob


def test_candidate_hash_is_stable() -> None:
    """T1.14: a candidate is frozen and its hash depends only on its content."""
    a = Candidate(diff="+x\n", test_ids=["tests/t.py::test_a"])
    b = Candidate(diff="+x\n", test_ids=["tests/t.py::test_a"])
    c = Candidate(diff="+y\n", test_ids=["tests/t.py::test_a"])
    assert a.sha256 == b.sha256 != c.sha256
    with pytest.raises(Exception):  # noqa: B017 - pydantic raises ValidationError
        a.diff = "changed"  # type: ignore[misc]
