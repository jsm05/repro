"""The agent loop with every stop condition from DESIGN 5.6.

Terminal states: SUBMITTED (gate passed and the agent submitted), ABSTAINED(reason),
BUDGET_EXHAUSTED(kind), DEFERRED (quota out under `pinned`; the unit reruns later).
On every stop except DEFERRED the best candidate so far is kept (gate passed beats
gate failed beats unchecked), so one run gives both gated and always-emit results.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

from repro.agent.context import Card, Context
from repro.agent.prompts import PROMPTS
from repro.agent.protocol import NUDGE, Action, ParseError, parse_action, repair_message
from repro.agent.tools import TOOL_SCHEMAS, TOOLS, ToolError, ToolResult, Workspace
from repro.eval.trace import TraceRecorder
from repro.llm.budget import BudgetExceeded
from repro.llm.clock import Clock
from repro.llm.router import Deferred
from repro.llm.tokens import TokenEstimator
from repro.llm.types import LLM, LLMResponse
from repro.models import AgentView, Candidate, GateVerdict

log = logging.getLogger(__name__)

State = Literal["SUBMITTED", "ABSTAINED", "BUDGET_EXHAUSTED", "DEFERRED"]


@dataclass(frozen=True)
class AgentLimits:
    steps: int = 30
    tokens: int = 600_000
    requests: int = 40
    prompt_cap: int = 32_000
    max_output: int = 8_000
    active_s: float = 25 * 60
    hard_cap_s: float = 120 * 60
    gate_checks: int = 8
    tool_errors: int = 5
    repeats: int = 3
    prompt_version: str = "v1"


@dataclass
class AgentResult:
    state: State
    reason: str
    best: Candidate | None = None
    best_passed: bool = False
    gated: Candidate | None = None
    best_tokens_at: int | None = None
    steps: int = 0
    gate_checks: int = 0
    rejections: dict[str, int] = field(default_factory=dict)
    tokens: int = 0
    requests: int = 0

    @property
    def stop_reason(self) -> str:
        return f"{self.state}({self.reason})" if self.reason else self.state


class AgentLoop:
    def __init__(
        self,
        view: AgentView,
        workspace: Workspace,
        llm: LLM,
        estimator: TokenEstimator,
        clock: Clock,
        trace: TraceRecorder,
        limits: AgentLimits | None = None,
        wait_s: Callable[[], float] = lambda: 0.0,
    ):
        self.view = view
        self.ws = workspace
        self.llm = llm
        self.est = estimator
        self.clock = clock
        self.trace = trace
        self.limits = limits or AgentLimits()
        self.wait_s = wait_s
        self.ctx = Context(PROMPTS[self.limits.prompt_version], view.issue_text, view.repo,
                           estimator, self.limits.prompt_cap)
        self.card = Card()
        # counters
        self.steps = self.requests = self.tokens = self.gate_checks = 0
        self.model_s = 0.0
        self.tool_errors = 0
        self.parse_failures = 0
        self.last_key = ""
        self.repeat = 0
        self.rejections: dict[str, int] = {}
        # candidates
        self.passed: Candidate | None = None
        self.checked: Candidate | None = None
        self.verdict_text = ""
        self.first_seen: dict[str, int] = {}  # diff -> tokens used when it first existed

    # -- helpers ----------------------------------------------------------------------

    def _budget_text(self) -> str:
        lim = self.limits
        return (f"{lim.steps - self.steps} steps, {lim.tokens - self.tokens:,} tokens, "
                f"{lim.requests - self.requests} requests, "
                f"{lim.gate_checks - self.gate_checks} checks")

    def _times(self, t0: float) -> tuple[float, float]:
        total = self.clock.now() - t0
        return total, total - self.wait_s() - self.model_s

    def _best(self) -> tuple[Candidate | None, bool]:
        if self.passed is not None:
            return self.passed, True
        if self.checked is not None:
            return self.checked, False
        return self.ws.candidate(), False

    def _finish(self, state: State, reason: str) -> AgentResult:
        best, passed = (None, False) if state == "DEFERRED" else self._best()
        tokens_at = self.first_seen.get(best.diff) if best is not None else None
        self.trace.add("stop", {"state": state, "reason": reason, "steps": self.steps,
                                "gate_checks": self.gate_checks, "best_passed": passed})
        return AgentResult(
            state=state, reason=reason, best=best, best_passed=passed,
            gated=self.passed if state != "DEFERRED" else None,
            best_tokens_at=tokens_at, steps=self.steps, gate_checks=self.gate_checks,
            rejections=dict(sorted(self.rejections.items())), tokens=self.tokens,
            requests=self.requests,
        )

    def _note_candidate(self) -> None:
        diff = self.ws.current_diff()
        if diff and diff not in self.first_seen:
            self.first_seen[diff] = self.tokens

    # -- tools ------------------------------------------------------------------------

    def _run_tool(self, action: Action) -> tuple[ToolResult | None, AgentResult | None]:
        tool, args = action.tool, action.args
        if tool not in TOOLS:
            raise ToolError(f"unknown tool {tool!r}; tools are {', '.join(TOOLS)}")
        if tool == "bash":
            r = self.ws.bash(args)
            self.card.searches.append(str(r.meta["cmd"])[:60])
            return r, None
        if tool == "view":
            r = self.ws.view(args)
            self.card.files_viewed.append(f"{r.meta['path']}:{r.meta['start']}-{r.meta['end']}")
            return r, None
        if tool == "write_test":
            r = self.ws.write_test(args)
            self.card.tests_written.append(str(r.meta["path"]))
            self._note_candidate()
            return r, None
        if tool == "check":
            if self.gate_checks >= self.limits.gate_checks:
                return None, self._finish("BUDGET_EXHAUSTED", "gate_checks")
            self.gate_checks += 1
            r, cand, v = self.ws.check(args)
            self._record_check(cand, v)
            return r, None
        if tool == "submit":
            if args:
                raise ToolError("submit takes no arguments")
            current = self.ws.current_diff()
            if self.passed is None or self.passed.diff != current:
                raise ToolError("submit refused: the gate has not passed on your current test "
                                "files. Run check first and fix what it reports.")
            return None, self._finish("SUBMITTED", "")
        # abstain
        reason = args.get("reason") if isinstance(args, dict) else None
        if not isinstance(reason, str) or not reason.strip() or set(args) - {"reason"}:
            raise ToolError('abstain needs {"reason": "<why>"}')
        return None, self._finish("ABSTAINED", reason.strip()[:300])

    def _record_check(self, cand: Candidate, v: GateVerdict) -> None:
        self.checked = cand
        if v.passed:
            self.passed = cand
        elif v.failed_check:
            self.rejections[v.failed_check] = self.rejections.get(v.failed_check, 0) + 1
        n = self.gate_checks
        self.card.checks.append(f"#{n} {len(cand.test_ids)} test(s): "
                                + ("PASS" if v.passed else f"FAIL at {v.failed_check}"))
        self.verdict_text = ("PASS: " if v.passed else f"FAIL at {v.failed_check}: ") + v.reason
        self.trace.add("gate_check", {"n": n, "candidate_sha256": cand.sha256,
                                      "test_ids": cand.test_ids, "passed": v.passed,
                                      "failed_check": v.failed_check, "reason": v.reason})

    # -- the loop ---------------------------------------------------------------------

    def run(self) -> AgentResult:
        lim = self.limits
        t0 = self.clock.now()
        last_route = ""
        while True:
            if self.steps >= lim.steps:
                return self._finish("BUDGET_EXHAUSTED", "steps")
            total, active = self._times(t0)
            if total >= lim.hard_cap_s:
                return self._finish("BUDGET_EXHAUSTED", "hard_cap")
            if active >= lim.active_s:
                return self._finish("BUDGET_EXHAUSTED", "wall_clock")
            if self.requests >= lim.requests:
                return self._finish("BUDGET_EXHAUSTED", "requests")
            self.card.budget = self._budget_text()
            built = self.ctx.build(self.card.render(self.est), self.ws.current_diff(),
                                   self.verdict_text)
            if built.trims:
                self.trace.add("trim", {"estimate": built.estimate, **built.trims})
            worst = built.estimate + lim.max_output
            if self.tokens + worst > lim.tokens:
                return self._finish("BUDGET_EXHAUSTED", "tokens")
            try:
                resp: LLMResponse = self.llm.complete(built.messages, max_tokens=lim.max_output,
                                                      temperature=0.0, tools=TOOL_SCHEMAS)
            except Deferred as exc:
                self.trace.add("deferred", {"reset_at": exc.reset_at})
                return self._finish("DEFERRED", "quota")
            except BudgetExceeded as exc:
                return self._finish("BUDGET_EXHAUSTED", exc.kind)
            if last_route and resp.route_id and resp.route_id != last_route:
                self.trace.add("failover", {"from": last_route, "to": resp.route_id},
                               route=resp.route_id)
            last_route = resp.route_id or last_route
            self.steps += 1
            self.requests += 1
            self.tokens += resp.total_tokens
            self.model_s += resp.latency_s
            done = self._step(resp.text, resp.finish_reason)
            if done is not None:
                return done

    def _step(self, text: str, finish_reason: str = "") -> AgentResult | None:
        lim = self.limits
        try:
            if not text.strip():
                raise ParseError(
                    "your reply was empty"
                    + (" because it hit the output limit, probably while reasoning; think "
                       "briefly and answer with one action" if finish_reason == "length" else ""))
            action = parse_action(text)
        except ParseError as exc:
            self.parse_failures += 1
            if self.parse_failures >= 2:
                self.tool_errors += 1
            self.trace.add("tool_call", {"tool": "(parse error)", "error": str(exc)})
            self.ctx.add(text, "harness", repair_message(str(exc)), "", harness=True)
            if self.tool_errors >= lim.tool_errors:
                return self._finish("BUDGET_EXHAUSTED", "tool_errors")
            return None
        self.parse_failures = 0
        self.repeat = self.repeat + 1 if action.key == self.last_key else 1
        self.last_key = action.key
        if self.repeat > lim.repeats:
            return self._finish("BUDGET_EXHAUSTED", "repeated_action")
        try:
            result, done = self._run_tool(action)
        except ToolError as exc:
            self.tool_errors += 1
            self.trace.add("tool_call", {"tool": action.tool, "ok": False, "error": str(exc)})
            nudge = "\n\n" + NUDGE if self.repeat == lim.repeats else ""
            self.ctx.add(text, action.tool, f"ERROR: {exc}{nudge}", f"[{action.tool}: error]")
            if self.tool_errors >= lim.tool_errors:
                return self._finish("BUDGET_EXHAUSTED", "tool_errors")
            return None
        if done is not None:
            self.trace.add("tool_call", {"tool": action.tool, "ok": True})
            return done
        assert result is not None
        self.tool_errors = 0
        self.trace.add("tool_call", {"tool": action.tool, "ok": True, **{
            k: v for k, v in result.meta.items() if k in ("cmd", "exit", "path", "mode",
                                                          "passed", "failed_check")},
            "output_chars": len(result.text)})
        body = result.text + ("\n\n" + NUDGE if self.repeat == lim.repeats else "")
        self.ctx.add(text, action.tool, body, result.stub, is_view=result.is_view)
        return None
