"""The oracle: needs the real fix. Used in eval, and in the product once a fix exists.

O1 pass after: on a fresh checkout with the fix applied, every candidate test passes
   in all runs (3 of 3 by default).
O2 no collateral: no test in the files the candidate touches fails after the fix.
   This mirrors the SWT-bench success definition.

An instance is REPRODUCED when O1 and O2 hold and at least one candidate test goes
FAILED before the fix to PASSED after it (F→P).
"""

from __future__ import annotations

from repro.gate.checks import GateConfig, SandboxFactory
from repro.gate.scope import DiffParseError, parse_diff
from repro.models import (
    STATUS_LETTER,
    Candidate,
    InstanceOutcome,
    OracleVerdict,
    TestStatus,
    Transition,
)
from repro.runner.pytest_runner import TestRunResult, run_tests

BAD_AFTER = {TestStatus.FAILED, TestStatus.ERROR, TestStatus.TIMEOUT}


def _label(before: TestStatus, after: list[TestStatus]) -> str:
    a = STATUS_LETTER[after[0]] if len(set(after)) == 1 else "~"
    return f"{STATUS_LETTER[before]}→{a}"


def run_oracle(
    candidate: Candidate | None,
    fix_patch: str,
    factory: SandboxFactory,
    config: GateConfig | None = None,
) -> OracleVerdict:
    cfg = config or GateConfig()
    if candidate is None:
        return OracleVerdict(outcome=InstanceOutcome.NO_TEST, reason="no candidate submitted")

    def out(
        outcome: InstanceOutcome,
        reason: str,
        o1: bool = False,
        o2: bool = False,
        transitions: dict[str, Transition] | None = None,
        flaky: list[str] | None = None,
    ) -> OracleVerdict:
        return OracleVerdict(
            outcome=outcome,
            reason=reason,
            candidate_sha256=candidate.sha256,
            candidate_tests=list(candidate.test_ids),
            o1_pass_after=o1,
            o2_no_collateral=o2,
            transitions=transitions or {},
            flaky_tests=flaky or [],
        )

    try:
        files = sorted({f.path for f in parse_diff(candidate.diff)})
    except DiffParseError as exc:
        return out(InstanceOutcome.WRONG_TEST, f"unparseable candidate diff: {exc}")
    if not files:
        return out(InstanceOutcome.WRONG_TEST, "candidate diff touches no files")
    # Also ask for candidate tests that live outside the touched files (a Django test
    # inherited by a test case in another module, D-M6-14).
    targets = files + [t for t in candidate.test_ids if t.split("::")[0] not in files]

    sb = factory()
    try:
        if not sb.apply_patch(candidate.diff).ok:
            return out(InstanceOutcome.WRONG_TEST, "candidate does not apply to the buggy code")
        before = run_tests(sb, targets, timeout_s=cfg.timeout_s, hash_seed=cfg.seeds[0])
    finally:
        sb.close()

    afters: list[TestRunResult] = []
    for seed in cfg.seeds:
        sb = factory()
        try:
            fixed = sb.apply_patch(fix_patch)
            if not fixed.ok:
                return out(InstanceOutcome.INFRA_ERROR, f"fix does not apply: {fixed.error}")
            if not sb.apply_patch(candidate.diff).ok:
                return out(InstanceOutcome.WRONG_TEST, "candidate does not apply on top of the fix")
            afters.append(run_tests(sb, targets, timeout_s=cfg.timeout_s, hash_seed=seed))
        finally:
            sb.close()

    all_ids = set(before.outcomes) | set(candidate.test_ids)
    for a in afters:
        all_ids |= set(a.outcomes)
    transitions: dict[str, Transition] = {}
    for tid in sorted(all_ids):
        b = before.outcomes.get(tid)
        b_status = b.status if b else TestStatus.ERROR
        a_status = [a.outcomes[tid].status if tid in a.outcomes else TestStatus.ERROR
                    for a in afters]
        transitions[tid] = Transition(
            test_id=tid, before=b_status, after=a_status, label=_label(b_status, a_status)
        )

    cand = list(candidate.test_ids)
    flaky = [t for t in cand if len(set(transitions[t].after)) > 1]
    o1 = all(all(s is TestStatus.PASSED for s in transitions[t].after) for t in cand)
    # Collateral: another test in the touched files that passed on the buggy code and
    # fails once the fix is in (P→F). Tests that already fail before (F→F, often the
    # environment) are not the candidate's doing (D-M6-13).
    collateral = [
        t for t, tr in transitions.items()
        if t not in cand and tr.before is TestStatus.PASSED
        and any(s in BAD_AFTER for s in tr.after)
    ]
    o2 = not collateral
    f2p = [t for t in cand if transitions[t].label == "F→P"]
    if o1 and o2 and f2p:
        return out(InstanceOutcome.REPRODUCED, f"F→P: {', '.join(f2p)}", o1, o2, transitions, flaky)
    reasons = []
    if not o1:
        bad = [f"{t} {transitions[t].label}" for t in cand if transitions[t].label[-1] != "P"]
        reasons.append("O1 failed: " + ", ".join(bad))
    if not o2:
        reasons.append("O2 failed: " + ", ".join(f"{t} {transitions[t].label}" for t in collateral))
    if o1 and o2 and not f2p:
        reasons.append("no candidate test goes F→P")
    return out(InstanceOutcome.WRONG_TEST, "; ".join(reasons), o1, o2, transitions, flaky)
