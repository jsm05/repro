"""Live checks against real providers. Run only in GitHub Actions (live-smoke.yml,
soak.yml) or by hand with keys set. Never part of the hermetic test suite.

smoke  One tiny call per enabled route, the model list each key can see, and a
       ledger cross-check: tokens recorded == tokens the provider reported (T3.L1).
       For reasoning routes, a second call with reasoning_effort="low" shows whether
       reasoning can be turned down (RISKS R32).
soak   Many requests of mixed sizes to one route, spread over hours, with raw
       outcomes recorded (no router retries), to find hidden daily caps and measure
       latency over a whole night (T3.L2).
"""

from __future__ import annotations

import json
import os
import statistics
import time
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from repro.llm.budget import Ledger, LedgerEntry
from repro.llm.client import OpenAICompatClient
from repro.llm.clock import Clock, RealClock
from repro.llm.errors import LLMError
from repro.llm.registry import Registry, Route
from repro.llm.secrets import known_secrets, read_key, redact
from repro.llm.types import LLMResponse

CODE_LINE = "def handler(request):\n    value = parse(request.body)\n    return value\n"


def list_models(base_url: str, api_key: str, timeout_s: float = 60.0) -> list[str]:
    req = urllib.request.Request(
        base_url.rstrip("/") + "/models", headers={"Authorization": f"Bearer {api_key}"}
    )
    with urllib.request.urlopen(req, timeout=timeout_s) as r:
        data = json.loads(r.read().decode())
    return sorted(str(m.get("id", "")) for m in data.get("data", []))


def entry_for(route: Route, resp: LLMResponse, run_id: str) -> LedgerEntry:
    return LedgerEntry(
        ts=time.time(), run_id=run_id, route_id=route.id, model=route.model,
        prompt_tokens=resp.prompt_tokens, completion_tokens=resp.completion_tokens,
        reasoning_tokens=resp.reasoning_tokens, latency_s=resp.latency_s,
    )


@dataclass
class RouteCheck:
    route_id: str
    model: str
    ok: bool
    detail: str
    latency_s: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0
    ledger_matches: bool = False
    low_effort_reasoning_tokens: int | None = None


@dataclass
class ProbeResult:
    variant: str
    ok: bool
    finish_reason: str = ""
    content_chars: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0
    latency_s: float = 0.0
    detail: str = ""


def first_agent_prompt(fixtures_root: str | Path = "tests/fixtures/mini_repos",
                       fixture: str = "f01_off_by_one") -> list[dict[str, str]]:
    """The exact first prompt the agent sends on a fixture (system prompt, issue, state card)."""
    from repro.agent.context import Card, Context
    from repro.agent.prompts import SYSTEM_V1
    from repro.datasets.fixtures import FixtureSource
    from repro.llm.tokens import TokenEstimator, chars_counter

    view = FixtureSource(fixtures_root).load(fixture).instance.public
    est = TokenEstimator(counter=chars_counter)
    card = Card(budget="30 steps, 600,000 tokens, 40 requests, 8 checks")
    return Context(SYSTEM_V1, view.issue_text, view.repo, est, 32_000).build(
        card.render(est), "", "").messages


ProbeTarget = tuple[str, Route, str, str]  # (label, route, base_url, key)


def agent_probe(route: Route, base_url: str, key: str, messages: list[dict[str, str]],
                max_tokens: int = 8000, timeout_s: float = 300,
                tools: list[dict[str, Any]] | None = None,
                others: Sequence[ProbeTarget] = ()) -> list[ProbeResult]:
    """Send the agent's first prompt several ways and record what comes back (M5 diagnosis
    of empty replies): as is, with the system prompt merged into the user turn, with
    reasoning_effort=low, with native tool schemas, and as is on other routes."""
    targets: list[tuple[str, Route, str, str, list[dict[str, Any]] | None]] = [
        ("system role", route.model_copy(update={"native_tools": False}), base_url, key, None),
        ("system merged into user",
         route.model_copy(update={"system_role": False, "native_tools": False}), base_url, key,
         None),
        ("reasoning_effort=low",
         route.model_copy(update={"reasoning_effort": "low", "native_tools": False}), base_url,
         key, None),
    ]
    if tools:
        targets.append(("native tools", route.model_copy(update={"native_tools": True}),
                        base_url, key, tools))
    for label, r, url, k in others:
        targets.append((label, r, url, k, tools if r.native_tools else None))
    out = []
    for name, r, url, k, t in targets:
        client = OpenAICompatClient(r, url, k, timeout_s=timeout_s)
        try:
            resp = client.complete(messages, max_tokens=min(max_tokens, r.max_output_tokens),
                                   tools=t)
            where = {"reasoning": " (taken from the reasoning field)",
                     "tool_call": " (taken from a native tool call)"}.get(resp.text_source, "")
            if resp.raw_note:
                where += f"; raw: {resp.raw_note[:200]}"
            out.append(ProbeResult(name, True, resp.finish_reason, len(resp.text),
                                   resp.completion_tokens, resp.reasoning_tokens, resp.latency_s,
                                   ("has an action block" if "```action" in resp.text
                                    else "no action block") + where))
        except LLMError as exc:
            empty = exc.response if isinstance(exc.response, LLMResponse) else None
            note = f"; raw: {empty.raw_note[:200]}" if empty and empty.raw_note else ""
            out.append(ProbeResult(name, False, empty.finish_reason if empty else "", 0,
                                   empty.completion_tokens if empty else 0,
                                   empty.reasoning_tokens if empty else 0,
                                   empty.latency_s if empty else 0.0, str(exc)[:200] + note))
    return out


@dataclass
class SmokeReport:
    models: dict[str, list[str]] = field(default_factory=dict)
    model_errors: dict[str, str] = field(default_factory=dict)
    checks: list[RouteCheck] = field(default_factory=list)
    probe_route: str = ""
    probes: list[ProbeResult] = field(default_factory=list)

    @property
    def all_ok(self) -> bool:
        return bool(self.checks) and all(c.ok and c.ledger_matches for c in self.checks)


def smoke(
    registry: Registry,
    ledger: Ledger,
    env: Mapping[str, str] | None = None,
    run_id: str = "smoke",
) -> SmokeReport:
    env = env if env is not None else os.environ
    secrets = known_secrets(registry, env)
    report = SmokeReport()
    for name, prov in registry.data.providers.items():
        key = read_key(env, prov.api_key_env)
        if not key:
            report.model_errors[name] = f"{prov.api_key_env} not set"
            continue
        try:
            report.models[name] = list_models(prov.base_url, key)
        except Exception as exc:  # report, don't crash the smoke run
            report.model_errors[name] = redact(f"{type(exc).__name__}: {str(exc)[:200]}", secrets)
    messages = [{"role": "user", "content": "Reply with the single word OK."}]
    for route in registry.routes.values():
        if not route.enabled:
            continue
        prov = registry.provider(route)
        key = read_key(env, prov.api_key_env)
        if not key:
            report.checks.append(RouteCheck(route.id, route.model, False, "no API key"))
            continue
        client = OpenAICompatClient(route, prov.base_url, key, timeout_s=300)
        try:
            resp = client.complete(messages, max_tokens=512)
        except LLMError as exc:
            report.checks.append(
                RouteCheck(route.id, route.model, False, redact(str(exc)[:300], secrets))
            )
            continue
        ledger.append(entry_for(route, resp, run_id))
        recorded = [e for e in ledger.read()[0] if e.run_id == run_id and e.route_id == route.id]
        matches = bool(recorded) and (
            recorded[-1].prompt_tokens == resp.prompt_tokens
            and recorded[-1].completion_tokens == resp.completion_tokens
        )
        check = RouteCheck(
            route.id, route.model, True, resp.finish_reason or "ok", resp.latency_s,
            resp.prompt_tokens, resp.completion_tokens, resp.reasoning_tokens, matches,
        )
        if route.reasoning:
            low = route.model_copy(update={"reasoning_effort": "low"})
            try:
                low_resp = OpenAICompatClient(low, prov.base_url, key, 300).complete(
                    messages, max_tokens=512
                )
                check.low_effort_reasoning_tokens = low_resp.reasoning_tokens
            except LLMError as exc:
                check.detail += "; reasoning_effort=low refused: " + redact(str(exc)[:120], secrets)
        report.checks.append(check)
    primary = registry.enabled_routes("primary") if "primary" in registry.data.logical_models \
        else []
    if primary:
        route = primary[0]
        prov = registry.provider(route)
        key = read_key(env, prov.api_key_env)
        if key:
            report.probe_route = route.id
            from repro.agent.tools import TOOL_SCHEMAS

            others: list[ProbeTarget] = []
            for other in registry.routes.values():
                if other.enabled and other.id != route.id and other.provider in ("nvidia",
                                                                                 "gemini"):
                    op = registry.provider(other)
                    ok = read_key(env, op.api_key_env)
                    if ok:
                        others.append((other.id, other, op.base_url, ok))
            try:
                report.probes = agent_probe(route, prov.base_url, key, first_agent_prompt(),
                                            tools=TOOL_SCHEMAS, others=others)
            except Exception as exc:  # report, don't crash the smoke run
                report.probes = [ProbeResult("all", False,
                                             detail=redact(f"{type(exc).__name__}: {exc}"[:200],
                                                           secrets))]
            for pr in report.probes:
                pr.detail = redact(pr.detail, secrets)
    return report


def smoke_markdown(report: SmokeReport, when: str) -> str:
    lines = [f"# Live smoke ({when})", "", "## Route calls", "",
             "| Route | Model | OK | Latency s | Prompt | Completion | Reasoning | "
             "Reasoning at effort=low | Ledger matches | Detail |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for c in report.checks:
        low = "" if c.low_effort_reasoning_tokens is None else str(c.low_effort_reasoning_tokens)
        lines.append(
            f"| {c.route_id} | `{c.model}` | {'yes' if c.ok else 'NO'} | {c.latency_s:.1f} | "
            f"{c.prompt_tokens} | {c.completion_tokens} | {c.reasoning_tokens} | {low} | "
            f"{'yes' if c.ledger_matches else 'no'} | {c.detail.replace('|', '/')} |"
        )
    if report.probes:
        lines += ["", f"## Agent-prompt probe ({report.probe_route}, max_tokens 8000)", "",
                  "The agent's first prompt on fixture f01, sent several ways and to the "
                  "other routes.", "",
                  "| Variant | Content | finish_reason | Chars | Completion | Reasoning | "
                  "Latency s | Detail |", "|---|---|---|---|---|---|---|---|"]
        for pr in report.probes:
            lines.append(f"| {pr.variant} | {'yes' if pr.ok else 'EMPTY'} | {pr.finish_reason} | "
                         f"{pr.content_chars} | {pr.completion_tokens} | {pr.reasoning_tokens} | "
                         f"{pr.latency_s:.1f} | {pr.detail.replace('|', '/')} |")
    lines += ["", "## Models visible to each key", ""]
    for name, ids in report.models.items():
        lines.append(f"- **{name}** ({len(ids)}): " + ", ".join(f"`{i}`" for i in ids))
    for name, err in report.model_errors.items():
        lines.append(f"- **{name}**: model list failed: {err}")
    return "\n".join(lines) + "\n"


@dataclass
class SoakSample:
    t: float
    size: str
    ok: bool
    error: str
    status: int | None
    latency_s: float
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0
    finish_reason: str = ""


def _prompt(size: str, i: int) -> str:
    repeats = {"small": 0, "medium": 220, "large": 600}[size]
    return CODE_LINE * repeats + f"\nRequest {i}: in one sentence, what does this code do?"


def soak(
    registry: Registry,
    route_id: str,
    requests: int,
    hours: float,
    env: Mapping[str, str] | None = None,
    max_tokens: int = 2048,
    *,
    client: Any = None,
    clock: Clock | None = None,
    on_sample: Callable[[list[SoakSample]], None] | None = None,
) -> list[SoakSample]:
    """Send up to `requests` calls, one every hours*3600/requests seconds, and stop at
    the time limit even if calls run slower than that gap. No new call starts after the
    limit, so the run ends at most one call timeout later. hours <= 0 means no limit and
    no gap. `on_sample` gets the full list after every call so a caller can save it."""
    env = env if env is not None else os.environ
    clock = clock if clock is not None else RealClock()
    if client is None:
        route = registry.routes[route_id]
        prov = registry.provider(route)
        client = OpenAICompatClient(route, prov.base_url, read_key(env, prov.api_key_env),
                                    timeout_s=300)
    limit_s = hours * 3600
    interval = limit_s / max(requests, 1) if limit_s > 0 else 0.0
    t0 = clock.now()
    deadline = t0 + limit_s if limit_s > 0 else None
    sizes = ["small", "medium", "medium", "large"]
    samples: list[SoakSample] = []
    for i in range(requests):
        started = clock.now()
        if deadline is not None and started >= deadline:
            break
        size = sizes[i % len(sizes)]
        try:
            resp = client.complete([{"role": "user", "content": _prompt(size, i)}],
                                   max_tokens=max_tokens)
            samples.append(SoakSample(started, size, True, "", 200, resp.latency_s,
                                      resp.prompt_tokens, resp.completion_tokens,
                                      resp.reasoning_tokens, resp.finish_reason))
        except LLMError as exc:
            samples.append(SoakSample(started, size, False, exc.cls.value, exc.status,
                                      clock.now() - started))
        if on_sample is not None:
            on_sample(samples)
        wait = interval - (clock.now() - started)
        if deadline is not None:
            wait = min(wait, deadline - clock.now())
        clock.sleep(max(0.0, wait))
    return samples


def soak_markdown(
    route_id: str,
    samples: list[SoakSample],
    when: str,
    status: str = "finished",
    planned: int | None = None,
    hours: float | None = None,
) -> str:
    ok = [s for s in samples if s.ok]
    lat = sorted(s.latency_s for s in ok)
    t0 = samples[0].t if samples else 0.0

    def pct(p: float) -> str:
        return f"{lat[min(len(lat) - 1, int(p * len(lat)))]:.1f}" if lat else "n/a"

    errors: dict[str, int] = {}
    for s in samples:
        if not s.ok:
            errors[s.error] = errors.get(s.error, 0) + 1
    first_fail = next((s for s in samples if not s.ok), None)
    plan = []
    if planned is not None:
        plan.append(f"up to {planned} requests")
    if hours:
        plan.append(f"time limit {hours:g} h")
    elapsed_h = (samples[-1].t - t0) / 3600 if samples else 0.0
    lines = [
        f"# Soak test: `{route_id}` ({when})", "",
        f"- Status: {status}",
        f"- Plan: {', '.join(plan)}" if plan else None,
        f"- Requests: {len(samples)}; succeeded: {len(ok)} "
        f"({100 * len(ok) / max(len(samples), 1):.1f}%); last request started at "
        f"{elapsed_h:.2f} h",
        f"- Latency p50 {pct(0.5)} s, p95 {pct(0.95)} s, max {lat[-1]:.1f} s" if lat
        else "- Latency: no successful requests",
        f"- Errors by class: {errors or 'none'}",
        f"- 429 responses: {sum(1 for s in samples if s.status == 429)}",
        f"- Timeouts or connection errors: "
        f"{sum(1 for s in samples if not s.ok and s.status is None)}",
        f"- Replies cut off at max_tokens (finish_reason=length): "
        f"{sum(1 for s in ok if s.finish_reason == 'length')}",
        f"- Mean reasoning tokens per reply: "
        f"{statistics.mean(s.reasoning_tokens for s in ok):.0f}" if ok else None,
    ]
    if first_fail is not None:
        lines.append(f"- First failure at request {samples.index(first_fail) + 1}, "
                     f"{(first_fail.t - t0) / 3600:.2f} h in: "
                     f"{first_fail.error} (HTTP {first_fail.status})")
    lines += ["", "| Size | Count | OK | p50 s |", "|---|---|---|---|"]
    for size in ("small", "medium", "large"):
        group = [s for s in samples if s.size == size]
        g_ok = sorted(s.latency_s for s in group if s.ok)
        p50 = f"{statistics.median(g_ok):.1f}" if g_ok else "n/a"
        lines.append(f"| {size} | {len(group)} | {len(g_ok)} | {p50} |")
    lines += ["", "| Hour | Requests | OK | 429s | Other errors | p50 s |",
              "|---|---|---|---|---|---|"]
    by_hour: dict[int, list[SoakSample]] = {}
    for s in samples:
        by_hour.setdefault(int((s.t - t0) // 3600), []).append(s)
    for hour in sorted(by_hour):
        group = by_hour[hour]
        g_ok = sorted(s.latency_s for s in group if s.ok)
        n429 = sum(1 for s in group if s.status == 429)
        other = sum(1 for s in group if not s.ok and s.status != 429)
        p50 = f"{statistics.median(g_ok):.1f}" if g_ok else "n/a"
        lines.append(f"| {hour} | {len(group)} | {len(g_ok)} | {n429} | {other} | {p50} |")
    return "\n".join(line for line in lines if line is not None) + "\n"


def write_report(path: str | Path, text: str, secrets: list[str]) -> str:
    """Write a report with every known key redacted; returns the text written."""
    clean = redact(text, secrets)
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(clean)
    return clean
