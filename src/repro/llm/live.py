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
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from repro.llm.budget import Ledger, LedgerEntry
from repro.llm.client import OpenAICompatClient
from repro.llm.errors import LLMError
from repro.llm.registry import Registry, Route
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
class SmokeReport:
    models: dict[str, list[str]] = field(default_factory=dict)
    model_errors: dict[str, str] = field(default_factory=dict)
    checks: list[RouteCheck] = field(default_factory=list)

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
    report = SmokeReport()
    for name, prov in registry.data.providers.items():
        key = env.get(prov.api_key_env, "")
        if not key:
            report.model_errors[name] = f"{prov.api_key_env} not set"
            continue
        try:
            report.models[name] = list_models(prov.base_url, key)
        except Exception as exc:  # report, don't crash the smoke run
            report.model_errors[name] = f"{type(exc).__name__}: {str(exc)[:200]}"
    messages = [{"role": "user", "content": "Reply with the single word OK."}]
    for route in registry.routes.values():
        if not route.enabled:
            continue
        prov = registry.provider(route)
        key = env.get(prov.api_key_env, "")
        if not key:
            report.checks.append(RouteCheck(route.id, route.model, False, "no API key"))
            continue
        client = OpenAICompatClient(route, prov.base_url, key, timeout_s=300)
        try:
            resp = client.complete(messages, max_tokens=512)
        except LLMError as exc:
            report.checks.append(RouteCheck(route.id, route.model, False, str(exc)[:300]))
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
                check.detail += f"; reasoning_effort=low refused: {str(exc)[:120]}"
        report.checks.append(check)
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
) -> list[SoakSample]:
    env = env if env is not None else os.environ
    route = registry.routes[route_id]
    prov = registry.provider(route)
    client = OpenAICompatClient(route, prov.base_url, env[prov.api_key_env], timeout_s=300)
    interval = hours * 3600 / max(requests, 1)
    sizes = ["small", "medium", "medium", "large"]
    samples: list[SoakSample] = []
    for i in range(requests):
        started = time.time()
        size = sizes[i % len(sizes)]
        try:
            resp = client.complete([{"role": "user", "content": _prompt(size, i)}],
                                   max_tokens=max_tokens)
            samples.append(SoakSample(started, size, True, "", 200, resp.latency_s,
                                      resp.prompt_tokens, resp.completion_tokens,
                                      resp.reasoning_tokens, resp.finish_reason))
        except LLMError as exc:
            samples.append(SoakSample(started, size, False, exc.cls.value, exc.status,
                                      time.time() - started))
        time.sleep(max(0.0, interval - (time.time() - started)))
    return samples


def soak_markdown(route_id: str, samples: list[SoakSample], when: str) -> str:
    ok = [s for s in samples if s.ok]
    lat = sorted(s.latency_s for s in ok)

    def pct(p: float) -> str:
        return f"{lat[min(len(lat) - 1, int(p * len(lat)))]:.1f}" if lat else "n/a"

    errors: dict[str, int] = {}
    for s in samples:
        if not s.ok:
            errors[s.error] = errors.get(s.error, 0) + 1
    first_fail = next((s for s in samples if not s.ok), None)
    lines = [
        f"# Soak test: `{route_id}` ({when})", "",
        f"- Requests: {len(samples)}; succeeded: {len(ok)} "
        f"({100 * len(ok) / max(len(samples), 1):.1f}%)",
        f"- Latency p50 {pct(0.5)} s, p95 {pct(0.95)} s, max {lat[-1]:.1f} s" if lat
        else "- Latency: no successful requests",
        f"- Errors by class: {errors or 'none'}",
        f"- 429 responses: {sum(1 for s in samples if s.status == 429)}",
        f"- Timeouts or connection errors: "
        f"{sum(1 for s in samples if not s.ok and s.status is None)}",
        f"- Replies cut off at max_tokens (finish_reason=length): "
        f"{sum(1 for s in ok if s.finish_reason == 'length')}",
        f"- Mean reasoning tokens per reply: "
        f"{statistics.mean(s.reasoning_tokens for s in ok):.0f}" if ok else "",
    ]
    if first_fail is not None:
        lines.append(f"- First failure at request {samples.index(first_fail) + 1}: "
                     f"{first_fail.error} (HTTP {first_fail.status})")
    lines += ["", "| Size | Count | OK | p50 s |", "|---|---|---|---|"]
    for size in ("small", "medium", "large"):
        group = [s for s in samples if s.size == size]
        g_ok = sorted(s.latency_s for s in group if s.ok)
        p50 = f"{statistics.median(g_ok):.1f}" if g_ok else "n/a"
        lines.append(f"| {size} | {len(group)} | {len(g_ok)} | {p50} |")
    return "\n".join(line for line in lines if line is not None) + "\n"


def write_report(path: str | Path, text: str) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
