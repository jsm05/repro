"""Running one unit: load the instance, run the method, gate, oracle, build the row.

M4 runs the baselines (B0, B1); the agent plugs in here in M5. Datasets are behind
a small interface: fixtures now, SWT-bench in M6.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from repro.baseline.pass_at_k import run_b1
from repro.baseline.retrieval import DirRepo, RepoFiles
from repro.baseline.single_prompt import BaselineResult, run_b0
from repro.config import EvalConfig
from repro.datasets.fixtures import FixtureSource
from repro.eval.rows import ResultRow
from repro.eval.run import UnitOutput
from repro.eval.trace import TraceRecorder
from repro.gate.checks import GateConfig, JudgeFn, OnlineGate, SandboxFactory
from repro.llm.budget import BudgetExceeded, Ledger, LedgerEntry, list_price_usd
from repro.llm.clock import Clock, RealClock
from repro.llm.router import Deferred
from repro.llm.tokens import TokenEstimator, chars_counter
from repro.llm.types import LLM, LLMResponse, Message
from repro.models import Candidate, Instance, InstanceOutcome
from repro.oracle.oracle import run_oracle
from repro.sandbox.local import local_factory

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class LoadedInstance:
    instance: Instance
    repo: RepoFiles
    factory: SandboxFactory


class Dataset(Protocol):
    def ids(self) -> list[str]: ...

    def load(self, instance_id: str) -> LoadedInstance: ...


class FixtureDataset:
    def __init__(self, root: str):
        self.source = FixtureSource(root)
        self._names = {self.source.load(n).instance.public.instance_id: n
                       for n in self.source.names()}

    def ids(self) -> list[str]:
        return sorted(self._names)

    def load(self, instance_id: str) -> LoadedInstance:
        fx = self.source.load(self._names[instance_id])
        return LoadedInstance(fx.instance, DirRepo(fx.repo_dir), local_factory(fx.repo_dir))


class MeteredLLM:
    """Passes calls through, keeping every response and tracing it."""

    def __init__(self, inner: LLM, trace: TraceRecorder, ledger: Ledger | None,
                 run_id: str, unit_id: str, clock: Clock,
                 wait_s: Callable[[], float] = lambda: 0.0):
        self.inner = inner
        self.wait_s = wait_s
        self.trace = trace
        self.ledger = ledger
        self.run_id = run_id
        self.unit_id = unit_id
        self.clock = clock
        self.responses: list[LLMResponse] = []

    def complete(self, messages: Sequence[Message], *, max_tokens: int,
                 temperature: float = 0.0, stop: Sequence[str] | None = None,
                 tools: Sequence[Mapping[str, Any]] | None = None) -> LLMResponse:
        waited = self.wait_s()
        if tools is None:
            resp = self.inner.complete(messages, max_tokens=max_tokens,
                                       temperature=temperature, stop=stop)
        else:
            resp = self.inner.complete(messages, max_tokens=max_tokens,
                                       temperature=temperature, stop=stop, tools=tools)
        waited = self.wait_s() - waited
        self.responses.append(resp)
        self.trace.add("llm_call", {
            "rate_limit_wait_s": round(waited, 3),
            "model": resp.model, "prompt_tokens": resp.prompt_tokens,
            "completion_tokens": resp.completion_tokens,
            "reasoning_tokens": resp.reasoning_tokens, "latency_s": resp.latency_s,
            "retries": resp.retries, "finish_reason": resp.finish_reason,
            "text_source": resp.text_source,
            **({"raw_note": resp.raw_note} if resp.raw_note else {}),
        }, route=resp.route_id, tokens=resp.total_tokens)
        if self.ledger is not None:
            self.ledger.append(entry_of(resp, self.run_id, self.unit_id, self.clock.now()))
        return resp


def entry_of(resp: LLMResponse, run_id: str, unit_id: str, ts: float) -> LedgerEntry:
    return LedgerEntry(
        ts=ts, run_id=run_id, unit_id=unit_id, route_id=resp.route_id, model=resp.model,
        prompt_tokens=resp.prompt_tokens, completion_tokens=resp.completion_tokens,
        reasoning_tokens=resp.reasoning_tokens, latency_s=resp.latency_s, retries=resp.retries,
    )


@dataclass
class LLMHandle:
    """One unit's model access. `ledger` is set when the handle itself must write the
    ledger (cassette or fake); the live router writes its own."""

    llm: LLM
    rate_limit_wait_s: Callable[[], float] = lambda: 0.0
    mixed: Callable[[], bool] | None = None
    ledger: Ledger | None = None
    extra: dict[str, Any] = field(default_factory=dict)


LLMFactory = Callable[[str], LLMHandle]


def eval_estimator() -> TokenEstimator:
    """Fixed chars/3 counting, so prompts (and cassette keys) match on every machine."""
    return TokenEstimator(counter=chars_counter)


@dataclass
class _Unit:
    row: ResultRow
    files: dict[str, str]
    trace: TraceRecorder


@dataclass
class MethodOutcome:
    always: Candidate | None
    gated: Candidate | None
    gate_passed: bool | None
    stop_reason: str
    tokens_at: int | None
    gate_checks: int = 0
    rejections: dict[str, int] = field(default_factory=dict)
    steps: int = 0


class Executor:
    """Runs one unit of any kind (B0, B1, agent) and builds its row."""

    def __init__(
        self,
        config: EvalConfig,
        run_id: str,
        dataset: Dataset,
        llm_factory: LLMFactory,
        *,
        prices: dict[str, tuple[float, float] | None] | None = None,
        git_commit: str = "",
        routes_verified_on: str = "",
        route_windows: Sequence[int] = (),
        clock: Clock | None = None,
    ):
        self.config = config
        self.run_id = run_id
        self.dataset = dataset
        self.llm_factory = llm_factory
        self.prices = prices or {}
        self.git_commit = git_commit
        self.routes_verified_on = routes_verified_on
        self.route_windows = list(route_windows)
        self.clock = clock or RealClock()
        self.estimator = eval_estimator()

    def _base(self, instance_id: str) -> dict[str, Any]:
        c = self.config
        return dict(run_id=self.run_id, instance_id=instance_id, config_id=c.config_id,
                    config_hash=c.hash, kind=c.kind, logical_model=c.logical_model,
                    git_commit=self.git_commit, routes_verified_on=self.routes_verified_on)

    def infra_row(self, instance_id: str, reason: str) -> ResultRow:
        return ResultRow(**self._base(instance_id), outcome=InstanceOutcome.INFRA_ERROR,
                         outcome_gated=InstanceOutcome.INFRA_ERROR, infra_error=reason,
                         stop_reason="infra_error")

    # -- methods ----------------------------------------------------------------------

    def _judge(self, loaded: LoadedInstance, llm: MeteredLLM,
               trace: TraceRecorder) -> JudgeFn | None:
        """The G3 judge when the config asks for it. It gets the public issue text only."""
        if not self.config.judge:
            return None
        from repro.gate.judge import Judge

        return Judge(llm, loaded.instance.public.issue_text,
                     on_call=lambda d: trace.add("judge", d))

    def _baseline(self, loaded: LoadedInstance, llm: MeteredLLM,
                  trace: TraceRecorder) -> MethodOutcome:
        cfg = self.config
        gate_cfg = GateConfig(timeout_s=cfg.gate_timeout_s)
        judge = self._judge(loaded, llm, trace)
        rejections: dict[str, int] = {}
        checks = 0

        def gate(c: Candidate) -> bool:
            nonlocal checks
            checks += 1
            v = OnlineGate(loaded.factory, gate_cfg, judge).verify(c)
            if v.failed_check:
                rejections[v.failed_check] = rejections.get(v.failed_check, 0) + 1
            trace.add("gate_check", {"candidate": c.origin, "passed": v.passed,
                                     "failed_check": v.failed_check, "reason": v.reason})
            return v.passed

        view, repo = loaded.instance.public, loaded.repo
        stop_reason = ""
        result: BaselineResult | None = None
        try:
            if cfg.kind == "B0":
                result = run_b0(view, repo, llm, estimator=self.estimator,
                                prompt_cap=cfg.prompt_cap, max_tokens=cfg.max_output_tokens)
            else:
                result = run_b1(view, repo, llm, gate, estimator=self.estimator, k=cfg.k,
                                temperature=cfg.temperature, prompt_cap=cfg.prompt_cap,
                                max_tokens=cfg.max_output_tokens)
        except BudgetExceeded as exc:
            stop_reason = f"budget: {exc.kind}"
        cand = result.always_emit if result else None
        gated = result.gated if result else None
        if not stop_reason:
            stop_reason = "candidate" if cand is not None else "no candidate"
        tokens_at = None
        if cand is not None and result is not None:
            idx = next(i for i, s in enumerate(result.samples) if s is cand)
            tokens_at = sum(r.total_tokens for r in llm.responses[: idx + 1])
        if cfg.kind == "B0":
            gated, gate_passed = cand, None
        else:
            gate_passed = (gated is not None) if cand is not None else None
        return MethodOutcome(cand, gated, gate_passed, stop_reason, tokens_at, checks,
                             rejections, len(llm.responses))

    def _agent(self, loaded: LoadedInstance, llm: MeteredLLM, trace: TraceRecorder,
               handle: LLMHandle) -> MethodOutcome:
        from repro.agent.context import effective_prompt_cap
        from repro.agent.loop import AgentLimits, AgentLoop
        from repro.agent.tools import Workspace

        cfg = self.config
        limits = AgentLimits(
            steps=cfg.max_steps, tokens=cfg.per_bug_tokens, requests=cfg.max_requests,
            prompt_cap=effective_prompt_cap(cfg.prompt_cap, self.route_windows),
            max_output=cfg.max_output_tokens, active_s=cfg.active_minutes * 60,
            hard_cap_s=cfg.hard_cap_minutes * 60, gate_checks=cfg.max_gate_checks,
            tool_errors=cfg.max_tool_errors, repeats=cfg.max_repeats,
            prompt_version=cfg.prompt_version,
        )
        sandbox = loaded.factory()
        try:
            gate_cfg = GateConfig(timeout_s=cfg.gate_timeout_s)
            judge = self._judge(loaded, llm, trace)
            ws = Workspace(sandbox, loaded.repo, loaded.factory, gate_cfg,
                           gate=OnlineGate(loaded.factory, gate_cfg, judge).verify)
            res = AgentLoop(loaded.instance.public, ws, llm, self.estimator, self.clock, trace,
                            limits, wait_s=handle.rate_limit_wait_s).run()
        finally:
            sandbox.close()
        if res.state == "DEFERRED":
            raise Deferred(0.0, "quota ran out mid-run; the unit will rerun from scratch")
        return MethodOutcome(res.best, res.gated,
                             res.best_passed if res.best is not None else None,
                             res.stop_reason, res.best_tokens_at, res.gate_checks,
                             res.rejections, res.steps)

    # -- one unit ---------------------------------------------------------------------

    def execute(self, instance_id: str) -> UnitOutput:
        release = getattr(self.dataset, "release", None)
        released = False
        try:
            out = self._execute(instance_id)
            if release is not None:
                # Remove this instance's images and prune (T6.11); log the free disk.
                free = release(instance_id)
                released = True
                out.trace.add("disk", {"free_gb": round(float(free), 1)})
            return UnitOutput(out.row, {**out.files, f"traces/{instance_id}.jsonl":
                                        out.trace.jsonl()})
        finally:
            if release is not None and not released:
                try:
                    release(instance_id)
                except Exception:
                    log.exception("cleanup after %s failed", instance_id)

    def _execute(self, instance_id: str) -> _Unit:
        cfg = self.config
        t_start = time.monotonic()
        loaded = self.dataset.load(instance_id)
        trace = TraceRecorder(self.run_id, instance_id, self.clock)
        handle = self.llm_factory(instance_id)
        llm = MeteredLLM(handle.llm, trace, handle.ledger, self.run_id, instance_id, self.clock,
                         handle.rate_limit_wait_s)
        if cfg.kind == "agent":
            m = self._agent(loaded, llm, trace, handle)
        else:
            m = self._baseline(loaded, llm, trace)
        cand = m.always

        if cand is not None:
            trace.add("freeze", {"candidate_sha256": cand.sha256, "test_ids": cand.test_ids})
        verdict = run_oracle(cand, loaded.instance.sealed.gold_fix_patch, loaded.factory,
                             GateConfig(timeout_s=cfg.gate_timeout_s))
        trace.add("oracle", {"outcome": verdict.outcome.value, "reason": verdict.reason,
                             "transitions": {t: tr.label for t, tr in
                                             verdict.transitions.items()}})
        outcome = verdict.outcome
        same = m.gated is not None and cand is not None and m.gated.sha256 == cand.sha256
        if same or outcome is InstanceOutcome.INFRA_ERROR:
            outcome_gated = outcome
        else:
            outcome_gated = InstanceOutcome.NO_TEST

        responses = llm.responses
        route_calls: dict[str, int] = {}
        for r in responses:
            route_calls[r.route_id] = route_calls.get(r.route_id, 0) + 1
        models = sorted({r.model for r in responses if r.model})
        mixed = handle.mixed() if handle.mixed is not None else len(models) > 1
        entries = [entry_of(r, self.run_id, instance_id, 0.0) for r in responses]
        row = ResultRow(
            **self._base(instance_id),
            model="+".join(models),
            routes_used=sorted(route_calls),
            route_calls=dict(sorted(route_calls.items())),
            mixed=mixed,
            outcome=outcome,
            outcome_gated=outcome_gated,
            gate_passed=m.gate_passed,
            stop_reason=m.stop_reason,
            candidate_sha256=cand.sha256 if cand else "",
            candidate_tokens_at=m.tokens_at,
            gate_rejections=dict(sorted(m.rejections.items())),
            oracle_transitions={t: tr.label for t, tr in sorted(verdict.transitions.items())},
            steps=m.steps,
            gate_checks=m.gate_checks,
            requests=len(responses),
            retries=sum(r.retries for r in responses),
            tokens_in=sum(r.prompt_tokens for r in responses),
            tokens_out=sum(r.completion_tokens for r in responses),
            tokens_reasoning=sum(r.reasoning_tokens for r in responses),
            rate_limit_wait_s=round(handle.rate_limit_wait_s(), 3),
            list_price_usd=list_price_usd(entries, self.prices) if entries else 0.0,
            wall_clock_s=round(time.monotonic() - t_start, 3),
            infra_error=verdict.reason if outcome is InstanceOutcome.INFRA_ERROR else "",
        )
        files: dict[str, str] = {}
        if cand is not None:
            files[f"candidates/{instance_id}.diff"] = cand.diff
            files[f"candidates/{instance_id}.json"] = json.dumps(
                {"test_ids": cand.test_ids, "origin": cand.origin}, sort_keys=True) + "\n"
        return _Unit(row, files, trace)


BaselineExecutor = Executor  # name used before the agent existed
