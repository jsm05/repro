"""The online gate: G0 to G5. Needs no fix, so the product can run it at issue time.

Every check runs on fresh, pristine sandboxes made by `factory`. Only the candidate's
diff is carried over; the agent's workspace is never consulted, so nothing the agent
did there can affect the verdict.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from repro.gate.scope import check_scope
from repro.gate.signature import failure_signature, is_assertion, short_type
from repro.models import Candidate, FailureDetail, GateCheck, GateVerdict, TestStatus
from repro.runner.pytest_runner import TestRunResult, run_tests
from repro.sandbox.base import Sandbox, SandboxPathError
from repro.sandbox.treehash import is_test_path

SandboxFactory = Callable[[], Sandbox]
NOT_EXECUTED = {TestStatus.ERROR, TestStatus.SKIPPED, TestStatus.XFAIL, TestStatus.XPASS}


@dataclass(frozen=True)
class GateConfig:
    seeds: tuple[int, ...] = (0, 1, 2)
    timeout_s: float = 180.0


def right_reason(f: FailureDetail) -> tuple[bool, str]:
    """G3. Passes an assertion failure, or an exception whose traceback goes through repo
    source. Rejects exceptions that come only from test code (the test itself is broken)."""
    if not f.exc_type:
        return False, "failure has no exception type"
    name = short_type(f.exc_type)
    if is_assertion(f.exc_type):
        return True, f"{name}: the test's own check failed"
    source = [fr for fr in f.frames if not is_test_path(fr.path)]
    if source:
        top = source[-1]
        return True, f"{name} raised through repo code ({top.path}:{top.func})"
    return False, f"{name} raised in test code only, so the test itself is broken"


def sandbox_reader(sb: Sandbox) -> Callable[[str], str | None]:
    def read(path: str) -> str | None:
        try:
            return sb.get_file(path).decode("utf-8", errors="replace")
        except (FileNotFoundError, SandboxPathError):
            return None

    return read


@dataclass
class _Run:
    result: TestRunResult
    hash_changed: bool


class OnlineGate:
    def __init__(self, factory: SandboxFactory, config: GateConfig | None = None):
        self.factory = factory
        self.config = config or GateConfig()

    def verify(self, candidate: Candidate) -> GateVerdict:
        cfg = self.config
        ids = list(candidate.test_ids)
        status: dict[str, list[TestStatus]] = {t: [] for t in ids}
        signatures: dict[str, str] = {}
        failing: list[str] = []
        used = 0

        def verdict(check: GateCheck | None, reason: str) -> GateVerdict:
            return GateVerdict(
                passed=check is None,
                failed_check=check,
                reason=reason,
                candidate_sha256=candidate.sha256,
                test_status=status,
                signatures=signatures,
                reproducing_tests=failing if check is None else [],
                sandboxes_used=used,
            )

        runs: list[_Run] = []
        for k, seed in enumerate(cfg.seeds):
            sb = self.factory()
            used += 1
            try:
                if k == 0:
                    scope = check_scope(candidate.diff, ids, sandbox_reader(sb))
                    if not scope.ok:
                        return verdict("G0", scope.reason)
                before = sb.source_tree_hash()
                applied = sb.apply_patch(candidate.diff)
                if not applied.ok:
                    reason = f"diff does not apply to a clean checkout: {applied.error}"
                    return verdict("G1", reason)
                result = run_tests(sb, ids, timeout_s=cfg.timeout_s, hash_seed=seed, trace=True)
                runs.append(_Run(result, sb.source_tree_hash() != before))
            finally:
                sb.close()
            for t in ids:
                o = result.outcomes.get(t)
                status[t].append(o.status if o else TestStatus.ERROR)

            if k > 0:
                continue
            # G1: every new test was collected and actually executed.
            for t in ids:
                o = result.outcomes.get(t)
                if o is None or o.status in NOT_EXECUTED:
                    detail = ""
                    if o is not None and o.failure is not None:
                        detail = f" ({o.failure.phase}: {o.failure.exc_type or 'error'})"
                    elif o is not None and o.note:
                        detail = f" ({o.note})"
                    got = o.status.value if o else "missing"
                    return verdict("G1", f"{t} did not run cleanly: {got}{detail}")
            # G2: at least one new test FAILS on the buggy code; a timeout is not a failure.
            timed_out = [t for t in ids if status[t][0] is TestStatus.TIMEOUT]
            if timed_out:
                return verdict("G2", f"{timed_out[0]} timed out; a timeout is not a failure")
            failing = [t for t in ids if status[t][0] is TestStatus.FAILED]
            if not failing:
                return verdict("G2", "no new test fails on the buggy code")
            for t in failing:
                f = result.outcomes[t].failure
                assert f is not None
                signatures[t] = failure_signature(f)
            # G3: each failure is for the right reason.
            for t in failing:
                f = result.outcomes[t].failure
                assert f is not None
                ok, why = right_reason(f)
                if not ok:
                    return verdict("G3", f"{t}: {why}")

        # G4: same statuses in every run, and failing tests fail the same way.
        for k, run in enumerate(runs[1:], start=1):
            seed = cfg.seeds[k]
            for t in ids:
                if status[t][k] != status[t][0]:
                    return verdict(
                        "G4",
                        f"{t} is unstable: {status[t][0].value} with seed {cfg.seeds[0]}, "
                        f"{status[t][k].value} with seed {seed}",
                    )
            for t in failing:
                f = run.result.outcomes[t].failure
                sig = failure_signature(f) if f else ""
                if sig != signatures[t]:
                    return verdict("G4", f"{t} fails differently with seed {seed}: {sig}")

        # G5: the test exercised repo code and left the source tree untouched.
        for k, run in enumerate(runs):
            if run.hash_changed:
                return verdict(
                    "G5", f"the source tree changed during the run with seed {cfg.seeds[k]}"
                )
        for t in failing:
            calls = runs[0].result.outcomes[t].repo_calls
            if calls is None:
                return verdict("G5", f"{t}: no tracer data, so cannot confirm repo code ran")
            if not calls:
                return verdict("G5", f"{t}: no repo function ran during the test's call phase")

        return verdict(
            None,
            f"{len(failing)} test(s) fail for the right reason, stably, and exercise repo code",
        )
