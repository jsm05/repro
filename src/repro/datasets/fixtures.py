"""Fixture repos: tiny buggy projects with a known fix and gold test.

Layout of each fixture directory:
    repo/            the buggy project
    fix.patch        the real fix (sealed)
    gold_test.patch  the reference reproduction test (sealed)
    issue.md         the bug report the agent sees
    instance.json    metadata (validated by FixtureMeta)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from repro.models import AgentView, Instance, SealedView, TestCmdSpec, TestStatus
from repro.runner.pytest_runner import run_tests
from repro.sandbox.base import Limits
from repro.sandbox.local import LocalSandbox


class FixtureMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instance_id: str
    repo: str
    base_commit: str
    designed_to_exercise: str
    test_cmd_spec: TestCmdSpec
    fail_to_pass: list[str] = Field(min_length=1)
    pass_to_pass: list[str]
    hints_text: str = ""


@dataclass(frozen=True)
class Fixture:
    name: str
    root: Path
    repo_dir: Path
    instance: Instance
    designed_to_exercise: str


class FixtureSource:
    def __init__(self, root: str | Path):
        self.root = Path(root)

    def names(self) -> list[str]:
        return sorted(p.name for p in self.root.iterdir() if (p / "instance.json").is_file())

    def load(self, name: str) -> Fixture:
        d = self.root / name
        meta = FixtureMeta.model_validate_json((d / "instance.json").read_text())
        public = AgentView(
            instance_id=meta.instance_id,
            repo=meta.repo,
            base_commit=meta.base_commit,
            issue_text=(d / "issue.md").read_text(),
            test_cmd_spec=meta.test_cmd_spec,
        )
        sealed = SealedView(
            gold_fix_patch=(d / "fix.patch").read_text(),
            gold_test_patch=(d / "gold_test.patch").read_text(),
            fail_to_pass=meta.fail_to_pass,
            pass_to_pass=meta.pass_to_pass,
            hints_text=meta.hints_text,
        )
        return Fixture(
            name=name,
            root=d,
            repo_dir=d / "repo",
            instance=Instance(public=public, sealed=sealed),
            designed_to_exercise=meta.designed_to_exercise,
        )


LETTER = {
    TestStatus.PASSED: "P",
    TestStatus.FAILED: "F",
    TestStatus.ERROR: "E",
    TestStatus.SKIPPED: "S",
    TestStatus.XFAIL: "X",
    TestStatus.XPASS: "X",
    TestStatus.TIMEOUT: "T",
}


@dataclass
class FixtureCheck:
    name: str
    before: dict[str, TestStatus] = field(default_factory=dict)
    after: dict[str, TestStatus] = field(default_factory=dict)
    p2p_after: dict[str, TestStatus] = field(default_factory=dict)
    error: str = ""

    @property
    def ok(self) -> bool:
        return (
            not self.error
            and bool(self.before)
            and all(s is TestStatus.FAILED for s in self.before.values())
            and all(s is TestStatus.PASSED for s in self.after.values())
            and all(s is TestStatus.PASSED for s in self.p2p_after.values())
        )

    @property
    def transition(self) -> str:
        pairs = {
            f"{LETTER[self.before[t]]}→{LETTER.get(self.after.get(t, TestStatus.ERROR), 'E')}"
            for t in self.before
        }
        return ",".join(sorted(pairs)) or "?"


def check_fixture(fx: Fixture, limits: Limits | None = None) -> FixtureCheck:
    """Gold test before the fix, then after: should be F→P with pass-to-pass still green."""
    sealed = fx.instance.sealed
    check = FixtureCheck(name=fx.name)
    with LocalSandbox(fx.repo_dir, limits) as sb:
        res = sb.apply_patch(sealed.gold_test_patch)
        if not res.ok:
            check.error = f"gold test patch: {res.error}"
            return check
        before = run_tests(sb, sealed.fail_to_pass, timeout_s=60)
        check.before = {t: before.outcomes[t].status for t in sealed.fail_to_pass}
        res = sb.apply_patch(sealed.gold_fix_patch)
        if not res.ok:
            check.error = f"fix patch: {res.error}"
            return check
        after = run_tests(sb, sealed.fail_to_pass + sealed.pass_to_pass, timeout_s=60)
        check.after = {t: after.outcomes[t].status for t in sealed.fail_to_pass}
        check.p2p_after = {t: after.outcomes[t].status for t in sealed.pass_to_pass}
    return check
