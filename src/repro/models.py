"""Core data types.

The split between AgentView and SealedView is the main safety property of the
project: anything that talks to a model (agent, baselines, prompts, the online gate)
takes an AgentView. Only the oracle, mutation scorer and audit take a SealedView.
Keeping them as two types, not one type with a flag, means a leak is a type error
that mypy catches, not a forgotten `if`.
"""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field


class TestStatus(StrEnum):
    """Outcome of one test. Only FAILED (call phase) ever counts as "fails"."""

    __test__: ClassVar[bool] = False  # not a pytest test class

    PASSED = "PASSED"
    FAILED = "FAILED"
    ERROR = "ERROR"
    SKIPPED = "SKIPPED"
    XFAIL = "XFAIL"
    XPASS = "XPASS"
    TIMEOUT = "TIMEOUT"


STATUS_LETTER = {
    TestStatus.PASSED: "P",
    TestStatus.FAILED: "F",
    TestStatus.ERROR: "E",
    TestStatus.SKIPPED: "S",
    TestStatus.XFAIL: "X",
    TestStatus.XPASS: "X",
    TestStatus.TIMEOUT: "T",
}

Phase = Literal["collection", "setup", "call", "teardown"]


class Frame(BaseModel):
    model_config = ConfigDict(frozen=True)

    path: str  # relative to the repo root
    lineno: int
    func: str


class FailureDetail(BaseModel):
    """What went wrong in a test: input for the right-reason check and signatures."""

    model_config = ConfigDict(frozen=True)

    phase: Phase
    exc_type: str = ""
    message: str = ""
    frames: list[Frame] = Field(default_factory=list)  # repo frames only, outermost first
    text: str = ""  # raw report text, capped


class TestOutcome(BaseModel):
    __test__: ClassVar[bool] = False

    model_config = ConfigDict(frozen=True)

    test_id: str
    status: TestStatus
    duration_s: float = 0.0
    failure: FailureDetail | None = None
    note: str = ""
    # Repo functions ("path:func") whose bodies ran during the call phase. None when the
    # run was not traced. Feeds G5.
    repo_calls: list[str] | None = None


class ExecResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    stdout: str
    stderr: str
    exit_code: int
    duration_s: float
    timed_out: bool = False
    truncated: bool = False
    resource_failure: Literal["memory"] | None = None


class PatchResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    ok: bool
    error: str = ""


class TestCmdSpec(BaseModel):
    __test__: ClassVar[bool] = False

    model_config = ConfigDict(frozen=True, extra="forbid")

    framework: Literal["pytest"] = "pytest"
    test_dirs: list[str] = Field(default_factory=lambda: ["tests"])


class AgentView(BaseModel):
    """Everything the agent may see. Nothing here may come from the fix or gold tests."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    instance_id: str
    repo: str
    base_commit: str
    issue_text: str
    test_cmd_spec: TestCmdSpec


class SealedView(BaseModel):
    """Ground truth. Never sent to a model, never shown to the agent."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    gold_fix_patch: str
    gold_test_patch: str
    fail_to_pass: list[str]
    pass_to_pass: list[str]
    hints_text: str = ""  # later issue comments; can discuss the fix


class Instance(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    public: AgentView
    sealed: SealedView


class Candidate(BaseModel):
    """A proposed reproduction: a test-only diff plus the test ids it adds."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    diff: str
    test_ids: list[str]
    origin: str = "agent"

    @property
    def sha256(self) -> str:
        payload = json.dumps({"diff": self.diff, "test_ids": self.test_ids}, sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()


GateCheck = Literal["G0", "G1", "G2", "G3", "G4", "G5"]


class GateVerdict(BaseModel):
    """Result of the online gate. `failed_check` names the first check that failed."""

    model_config = ConfigDict(frozen=True)

    passed: bool
    failed_check: GateCheck | None
    reason: str
    candidate_sha256: str
    test_status: dict[str, list[TestStatus]] = Field(default_factory=dict)  # one per run
    signatures: dict[str, str] = Field(default_factory=dict)  # failing tests, from run 0
    reproducing_tests: list[str] = Field(default_factory=list)
    sandboxes_used: int = 0


class InstanceOutcome(StrEnum):
    REPRODUCED = "REPRODUCED"
    WRONG_TEST = "WRONG_TEST"
    NO_TEST = "NO_TEST"
    INFRA_ERROR = "INFRA_ERROR"


class Transition(BaseModel):
    """Before (buggy) and after (fixed, one status per run) for one test."""

    model_config = ConfigDict(frozen=True)

    test_id: str
    before: TestStatus
    after: list[TestStatus]
    label: str  # e.g. "F→P"; "~" after the arrow means the runs disagreed


class OracleVerdict(BaseModel):
    model_config = ConfigDict(frozen=True)

    outcome: InstanceOutcome
    reason: str
    candidate_sha256: str = ""
    o1_pass_after: bool = False
    o2_no_collateral: bool = False
    candidate_tests: list[str] = Field(default_factory=list)
    transitions: dict[str, Transition] = Field(default_factory=dict)
    flaky_tests: list[str] = Field(default_factory=list)
