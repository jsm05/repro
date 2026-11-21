"""Running one unit: load the instance, run the method, gate, oracle, build the row.

M4 runs the baselines (B0, B1); the agent plugs in here in M5. Datasets are behind
a small interface: fixtures now, SWT-bench in M6.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from repro.baseline.pass_at_k import run_b1
from repro.baseline.retrieval import DirRepo, RepoFiles
from repro.baseline.single_prompt import BaselineResult, run_b0
from repro.config import EvalConfig
from repro.datasets.fixtures import FixtureSource
from repro.eval.rows import ResultRow, TraceEvent
from repro.eval.run import UnitOutput
from repro.gate.checks import GateConfig, OnlineGate, SandboxFactory
from repro.llm.budget import BudgetExceeded, Ledger, LedgerEntry, list_price_usd
from repro.llm.clock import Clock, RealClock
from repro.llm.tokens import TokenEstimator, chars_counter
from repro.llm.types import LLM, LLMResponse, Message
from repro.models import Candidate, Instance, InstanceOutcome
from repro.oracle.oracle import run_oracle
from repro.sandbox.local import local_factory


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


class TraceRecorder:
    """Trace events kept in memory and written with the row, so nothing partial lands."""

    def __init__(self, run_id: str, instance_id: str, clock: Clock):
        self.run_id = run_id
        self.instance_id = instance_id
        self.clock = clock
        self.events: list[TraceEvent] = []

    def add(self, type: str, payload: dict[str, Any] | None = None, route: str = "",
            tokens: int = 0) -> None:
        self.events.append(TraceEvent(
            ts=self.clock.now(), run_id=self.run_id, instance_id=self.instance_id,
            seq=len(self.events), type=type, payload=payload or {}, route=route, tokens=tokens,
        ))

    def jsonl(self) -> str:
        return "".join(e.model_dump_json() + "\n" for e in self.events)


class MeteredLLM:
    """Passes calls through, keeping every response and tracing it."""

    def __init__(self, inner: LLM, trace: TraceRecorder, ledger: Ledger | None,
                 run_id: str, unit_id: str, clock: Clock):
        self.inner = inner
        self.trace = trace
        self.ledger = ledger
        self.run_id = run_id
        self.unit_id = unit_id
        self.clock = clock
        self.responses: list[LLMResponse] = []

    def complete(self, messages: Sequence[Message], *, max_tokens: int,
                 temperature: float = 0.0, stop: Sequence[str] | None = None) -> LLMResponse:
        resp = self.inner.complete(messages, max_tokens=max_tokens, temperature=temperature,
                                   stop=stop)
        self.responses.append(resp)
        self.trace.add("llm_call", {
            "model": resp.model, "prompt_tokens": resp.prompt_tokens,
            "completion_tokens": resp.completion_tokens,
            "reasoning_tokens": resp.reasoning_tokens, "latency_s": resp.latency_s,
            "retries": resp.retries, "finish_reason": resp.finish_reason,
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


class BaselineExecutor:
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
        clock: Clock | None = None,
    ):
        if config.kind not in ("B0", "B1"):
            raise ValueError(f"BaselineExecutor cannot run kind {config.kind}")
        self.config = config
        self.run_id = run_id
        self.dataset = dataset
        self.llm_factory = llm_factory
        self.prices = prices or {}
        self.git_commit = git_commit
        self.routes_verified_on = routes_verified_on
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

    def execute(self, instance_id: str) -> UnitOutput:
        cfg = self.config
        t_start = time.monotonic()
        loaded = self.dataset.load(instance_id)
        trace = TraceRecorder(self.run_id, instance_id, self.clock)
        handle = self.llm_factory(instance_id)
        llm = MeteredLLM(handle.llm, trace, handle.ledger, self.run_id, instance_id, self.clock)
        gate_cfg = GateConfig(timeout_s=cfg.gate_timeout_s)
        rejections: dict[str, int] = {}
        gate_checks = 0

        def gate(c: Candidate) -> bool:
            nonlocal gate_checks
            gate_checks += 1
            v = OnlineGate(loaded.factory, gate_cfg).verify(c)
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

        verdict = run_oracle(cand, loaded.instance.sealed.gold_fix_patch, loaded.factory,
                             gate_cfg)
        trace.add("oracle", {"outcome": verdict.outcome.value, "reason": verdict.reason,
                             "transitions": {t: tr.label for t, tr in
                                             verdict.transitions.items()}})
        outcome = verdict.outcome
        if cfg.kind == "B0":
            outcome_gated, gate_passed = outcome, None
        else:
            outcome_gated = outcome if gated is not None else (
                InstanceOutcome.INFRA_ERROR if outcome is InstanceOutcome.INFRA_ERROR
                else InstanceOutcome.NO_TEST)
            gate_passed = (gated is not None) if cand is not None else None
        trace.add("stop", {"reason": stop_reason})

        responses = llm.responses
        tokens_at = None
        if cand is not None and result is not None:
            idx = next(i for i, s in enumerate(result.samples) if s is cand)
            tokens_at = sum(r.total_tokens for r in responses[: idx + 1])
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
            gate_passed=gate_passed,
            stop_reason=stop_reason,
            candidate_sha256=cand.sha256 if cand else "",
            candidate_tokens_at=tokens_at,
            gate_rejections=dict(sorted(rejections.items())),
            oracle_transitions={t: tr.label for t, tr in sorted(verdict.transitions.items())},
            steps=len(responses),
            gate_checks=gate_checks,
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
        files = {f"traces/{instance_id}.jsonl": trace.jsonl()}
        if cand is not None:
            files[f"candidates/{instance_id}.diff"] = cand.diff
            files[f"candidates/{instance_id}.json"] = json.dumps(
                {"test_ids": cand.test_ids, "origin": cand.origin}, sort_keys=True) + "\n"
        return UnitOutput(row, files)
