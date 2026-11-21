"""Write the scripted fixture cassettes used by the golden end-to-end test.

No model is called. Each fixture gets a fixed list of answers (the gold test, a test
that passes everywhere, a test that fails everywhere, or a refusal), recorded under
the exact request the baseline will make, so `repro eval` replays them. Rerun this
only when the baseline prompt or the fixtures change, then regenerate the report:

    python scripts/record_fixture_cassettes.py
    repro eval --config eval/configs/fixtures-b0.yaml --run-id fx-b0 --results-dir /tmp/fx
    repro eval --config eval/configs/fixtures-b1.yaml --run-id fx-b1 --results-dir /tmp/fx
    repro report --run fx-b0 --run fx-b1 --results-dir /tmp/fx \
        --title "Fixture results (cassette replay)" --out eval/reports/fixtures.md
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from repro.baseline.retrieval import DirRepo  # noqa: E402
from repro.baseline.single_prompt import build_messages  # noqa: E402
from repro.config import EvalConfig  # noqa: E402
from repro.datasets.fixtures import FixtureSource  # noqa: E402
from repro.eval.execute import eval_estimator  # noqa: E402
from repro.gate.scope import parse_diff  # noqa: E402
from repro.llm.cassette import CassetteLLM  # noqa: E402
from repro.llm.fake import FakeLLM  # noqa: E402
from repro.llm.types import LLMResponse  # noqa: E402
from repro.sandbox.local import LocalSandbox  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures" / "mini_repos"
REFUSAL = "I can't write a test for this without more information."
PASSES = ("```python path=tests/test_repro_issue.py mode=new\n"
          "def test_issue_reproduced():\n    assert True\n```\n")
FAILS = ("```python path=tests/test_repro_issue.py mode=new\n"
         "def test_issue_reproduced():\n    assert False, 'always fails'\n```\n")

# Answer kinds per fixture. "gold" is the fixture's real test written as an answer.
B0 = {
    "f01_off_by_one": ["gold"],
    "f02_raises": ["gold"],
    "f03_wrong_reason_trap": ["passes"],
    "f04_hash_order": ["gold"],
    "f05_append_only": ["gold"],
    "f06_two_modules": ["refusal"],
    "f07_ambiguous_issue": ["gold"],
    "f08_can_hang": ["fails"],
}
B1 = {
    "f01_off_by_one": ["refusal", "passes", "gold", "gold", "gold"],
    "f02_raises": ["gold", "refusal", "refusal", "refusal", "refusal"],
    "f03_wrong_reason_trap": ["passes", "passes", "refusal", "refusal", "refusal"],
    "f06_two_modules": ["refusal", "gold", "refusal", "refusal", "refusal"],
}


def gold_answer(source: FixtureSource, name: str) -> str:
    fx = source.load(name)
    path = parse_diff(fx.instance.sealed.gold_test_patch)[0].path
    with LocalSandbox(fx.repo_dir) as sb:
        original = sb.get_file(path).decode() if (fx.repo_dir / path).exists() else ""
        assert sb.apply_patch(fx.instance.sealed.gold_test_patch).ok
        code = sb.get_file(path).decode()[len(original):]
    mode = "append" if original else "new"
    return f"Here is the test.\n\n```python path={path} mode={mode}\n{code.strip()}\n```\n"


def record(config_path: Path, plan: dict[str, list[str]]) -> None:
    cfg = EvalConfig.from_yaml(config_path)
    assert cfg.cassette_dir is not None
    out_dir = ROOT / cfg.cassette_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("*.jsonl"):
        old.unlink()
    source = FixtureSource(FIXTURES)
    temperature = 0.0 if cfg.kind == "B0" else cfg.temperature
    for name, kinds in plan.items():
        fx = source.load(name)
        view = fx.instance.public
        messages = build_messages(view, DirRepo(fx.repo_dir), eval_estimator(), cfg.prompt_cap)
        prompt_tokens = sum(len(m["content"]) for m in messages) // 4
        texts = {"gold": gold_answer(source, name), "passes": PASSES, "fails": FAILS,
                 "refusal": REFUSAL}
        replies = [LLMResponse(text=texts[k], prompt_tokens=prompt_tokens,
                               completion_tokens=max(1, len(texts[k]) // 4),
                               finish_reason="stop", route_id="fake/recorded",
                               model="fake-model") for k in kinds]
        llm = CassetteLLM(out_dir / f"{view.instance_id}.jsonl", "record", FakeLLM(replies))
        for _ in kinds:
            llm.complete(messages, max_tokens=cfg.max_output_tokens, temperature=temperature)
        print(f"{cfg.config_id}: {view.instance_id}: {', '.join(kinds)}")


if __name__ == "__main__":
    record(ROOT / "eval/configs/fixtures-b0.yaml", B0)
    record(ROOT / "eval/configs/fixtures-b1.yaml", B1)
