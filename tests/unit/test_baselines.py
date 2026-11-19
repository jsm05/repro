"""BM25 retrieval and the B0/B1 baselines (TEST_SPEC M3)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from repro.baseline.pass_at_k import run_b1
from repro.baseline.retrieval import DirRepo, bm25_rank, select_context
from repro.baseline.single_prompt import build_messages, run_b0
from repro.datasets.fixtures import FixtureSource
from repro.gate.candidate import repo_reader
from repro.gate.checks import GateConfig, OnlineGate
from repro.gate.scope import check_scope, parse_diff
from repro.llm.cassette import CassetteLLM
from repro.llm.fake import FakeLLM
from repro.llm.tokens import TokenEstimator
from repro.llm.types import LLMResponse
from repro.models import Candidate
from repro.oracle.oracle import run_oracle
from repro.sandbox.local import LocalSandbox, local_factory
from tests.helpers import FIXTURE_NAMES

EST = TokenEstimator(counter=lambda s: max(1, len(s) // 4))


def gold_answer(source: FixtureSource, name: str) -> str:
    """The gold test written the way the model is asked to answer."""
    fx = source.load(name)
    path = parse_diff(fx.instance.sealed.gold_test_patch)[0].path
    with LocalSandbox(fx.repo_dir) as sb:
        original = sb.get_file(path).decode() if (fx.repo_dir / path).exists() else ""
        assert sb.apply_patch(fx.instance.sealed.gold_test_patch).ok
        code = sb.get_file(path).decode()[len(original):]
    mode = "append" if original else "new"
    return f"Here is the test.\n\n```python path={path} mode={mode}\n{code.strip()}\n```\n"


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_bm25_finds_buggy_file(fixture_source: FixtureSource, name: str) -> None:
    """T3.11: BM25 ranks the buggy file in the top 3, respects the budget, is deterministic."""
    fx = fixture_source.load(name)
    repo = DirRepo(fx.repo_dir)
    buggy = {f.path for f in parse_diff(fx.instance.sealed.gold_fix_patch)}
    ranked = bm25_rank(fx.instance.public.issue_text, repo)
    assert buggy & {p for p, _ in ranked[:3]}, ranked[:4]
    assert ranked == bm25_rank(fx.instance.public.issue_text, repo)
    picked = select_context(fx.instance.public.issue_text, repo, 120, EST.count)
    assert sum(EST.count(text) + 20 for _, text in picked) <= 120


def test_b0_prompt_is_leak_free_and_robust(fixture_source: FixtureSource) -> None:
    """T3.12: the B0 prompt holds only AgentView content; malformed output is NO_TEST."""
    for name in FIXTURE_NAMES:
        fx = fixture_source.load(name)
        repo = DirRepo(fx.repo_dir)
        prompt = json.dumps(build_messages(fx.instance.public, repo, EST))
        pristine = "\n".join(repo.read(p) for p in repo.list_files())
        sealed = fx.instance.sealed
        for patch in (sealed.gold_fix_patch, sealed.gold_test_patch):
            for line in patch.splitlines():
                text = line[1:].strip()
                if not line.startswith("+") or line.startswith("+++") or len(text) < 12:
                    continue
                if text in pristine:
                    continue  # already in the buggy repo, so not sealed information
                assert text not in prompt, f"{name}: sealed line leaked: {text!r}"
        for tid in sealed.fail_to_pass:
            assert tid.rsplit("::", 1)[-1] not in prompt
    fx = fixture_source.load("f01_off_by_one")
    for bad in ["I can't help with that.",
                "```python path=../evil_test.py mode=new\ndef test_x():\n    assert 0\n```",
                "```python path=pager/pages.py mode=append\ndef test_x():\n    assert 0\n```",
                "```python path=tests/test_x.py mode=new\nprint('no tests here')\n```"]:
        res = run_b0(fx.instance.public, DirRepo(fx.repo_dir), FakeLLM([bad]), estimator=EST)
        assert res.outcome == "NO_TEST" and res.gated is None, bad


def test_b0_append_answer_is_append_only(fixture_source: FixtureSource) -> None:
    """T3.12: a mode=append answer becomes a diff that only appends (passes G0)."""
    fx = fixture_source.load("f05_append_only")
    res = run_b0(fx.instance.public, DirRepo(fx.repo_dir),
                 FakeLLM([gold_answer(fixture_source, "f05_append_only")]), estimator=EST)
    assert res.always_emit is not None
    assert res.always_emit.test_ids == ["tests/test_text.py::test_truncate_result_fits_width"]
    scope = check_scope(res.always_emit.diff, res.always_emit.test_ids, repo_reader(fx.repo_dir))
    assert scope.ok, scope.reason


def _resp(text: str, tokens: int) -> LLMResponse:
    return LLMResponse(text=text, prompt_tokens=tokens, completion_tokens=tokens // 10)


def test_b1_pass_at_k_logic(fixture_source: FixtureSource) -> None:
    """T3.13: B1 emits the first gate-passing sample; else gated is empty and always-emit is
    sample 1; tokens are the sum over all samples."""
    fx = fixture_source.load("f01_off_by_one")
    answers = [f"```python path=tests/test_s{i}.py mode=new\ndef test_s{i}():\n    assert {i}\n```"
               for i in range(1, 6)]
    responses = [_resp(a, 100 * (i + 1)) for i, a in enumerate(answers)]
    passing = {"tests/test_s3.py::test_s3", "tests/test_s5.py::test_s5"}
    checked: list[str] = []

    def gate(c: Candidate) -> bool:
        checked.append(c.test_ids[0])
        return c.test_ids[0] in passing

    res = run_b1(fx.instance.public, DirRepo(fx.repo_dir), FakeLLM(responses), gate,
                 estimator=EST)
    assert res.gated is not None and res.gated.test_ids == ["tests/test_s3.py::test_s3"]
    assert res.always_emit == res.gated
    assert checked == ["tests/test_s1.py::test_s1", "tests/test_s2.py::test_s2",
                       "tests/test_s3.py::test_s3"]
    assert res.requests == 5
    assert res.tokens == sum(r.total_tokens for r in responses)

    res = run_b1(fx.instance.public, DirRepo(fx.repo_dir), FakeLLM(responses), lambda c: False,
                 estimator=EST)
    assert res.gated is None
    assert res.always_emit is not None and res.always_emit.test_ids == ["tests/test_s1.py::test_s1"]


@pytest.mark.parametrize("name", ["f01_off_by_one"])
def test_baselines_through_gate_and_oracle(fixture_source: FixtureSource, name: str) -> None:
    """B0 and B1 candidates from FakeLLM go through the gate and the oracle on fixtures."""
    fx = fixture_source.load(name)
    repo = DirRepo(fx.repo_dir)
    factory = local_factory(fx.repo_dir)
    cfg = GateConfig(timeout_s=30)
    gold = gold_answer(fixture_source, name)

    b0 = run_b0(fx.instance.public, repo, FakeLLM([gold]), estimator=EST)
    assert b0.always_emit is not None
    assert OnlineGate(factory, cfg).verify(b0.always_emit).passed
    oracle = run_oracle(b0.always_emit, fx.instance.sealed.gold_fix_patch, factory, cfg)
    assert oracle.outcome == "REPRODUCED", oracle.reason

    wrong = "```python path=tests/test_wrong.py mode=new\ndef test_wrong():\n    assert True\n```"
    b1 = run_b1(fx.instance.public, repo, FakeLLM(["no code", wrong, gold, gold, gold]),
                lambda c: OnlineGate(factory, cfg).verify(c).passed, estimator=EST)
    assert b1.gated is not None and b1.gated.origin == "b1-3"


def test_cassette_replay_is_byte_identical(fixture_source: FixtureSource, tmp_path: Path) -> None:
    """Record-then-replay of a baseline run through CassetteLLM gives identical results."""
    fx = fixture_source.load("f01_off_by_one")
    repo = DirRepo(fx.repo_dir)
    path = tmp_path / "b0.jsonl"
    gold = gold_answer(fixture_source, "f01_off_by_one")
    recorded = run_b0(fx.instance.public, repo, CassetteLLM(path, "record", FakeLLM([gold])),
                      estimator=EST)
    replayed = run_b0(fx.instance.public, repo, CassetteLLM(path, "replay"), estimator=EST)

    def dump(r: object) -> str:
        assert hasattr(r, "always_emit") and hasattr(r, "responses")
        return json.dumps({
            "candidate": r.always_emit.model_dump() if r.always_emit else None,
            "responses": [x.model_dump() for x in r.responses],
        }, sort_keys=True)

    assert dump(replayed) == dump(recorded)
