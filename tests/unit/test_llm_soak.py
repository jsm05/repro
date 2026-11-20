"""Soak loop: stops on a time limit even when calls are slow, and saves results as it goes."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from repro.llm.clock import FakeClock
from repro.llm.errors import ErrorClass, LLMError
from repro.llm.live import SoakSample, soak, soak_markdown
from repro.llm.registry import Registry
from repro.llm.types import LLMResponse, Message
from tests.helpers import ROOT

ROUTES = ROOT / "config" / "routes.yaml"
ROUTE = "nvidia/mistral-large-4"
ENV = {"NVIDIA_API_KEY": "nvapi-test", "GEMINI_API_KEY": "g", "OPENROUTER_API_KEY": "o"}


class SlowClient:
    """Each call moves the fake clock forward by `seconds`; every `fail_every`-th call 429s."""

    def __init__(self, clock: FakeClock, seconds: float, fail_every: int = 0):
        self.clock = clock
        self.seconds = seconds
        self.fail_every = fail_every
        self.calls = 0

    def complete(self, messages: Sequence[Message], *, max_tokens: int,
                 temperature: float = 0.0, stop: Sequence[str] | None = None) -> LLMResponse:
        self.calls += 1
        self.clock.advance(self.seconds)
        if self.fail_every and self.calls % self.fail_every == 0:
            raise LLMError(ErrorClass.RATE_LIMIT, "slow down", status=429)
        return LLMResponse(text="ok", prompt_tokens=10, completion_tokens=5,
                           latency_s=self.seconds, finish_reason="stop")


def run(clock: FakeClock, client: SlowClient, requests: int, hours: float,
        seen: list[int] | None = None) -> list[SoakSample]:
    def on_sample(samples: list[SoakSample]) -> None:
        if seen is not None:
            seen.append(len(samples))

    return soak(Registry.from_yaml(ROUTES), ROUTE, requests, hours, env=ENV,
                client=client, clock=clock, on_sample=on_sample)


def test_soak_stops_at_time_limit_when_calls_are_slower_than_the_gap() -> None:
    """T3.24: calls slower than the planned gap still stop at the time limit, not the count."""
    clock = FakeClock()
    start = clock.now()
    client = SlowClient(clock, seconds=60)  # planned gap is 3600 / 500 = 7.2 s
    samples = run(clock, client, requests=500, hours=1.0)
    assert len(samples) < 500
    # The last call may start just before the limit and run to completion, no later.
    assert clock.now() - start <= 3600 + 60
    assert all(s.t - start < 3600 for s in samples)


def test_soak_keeps_the_planned_gap_when_calls_are_fast() -> None:
    """T3.24: fast calls are paced by the gap and all requests are sent inside the limit."""
    clock = FakeClock()
    start = clock.now()
    samples = run(clock, SlowClient(clock, seconds=1), requests=10, hours=0.1)
    assert len(samples) == 10
    gaps = [b.t - a.t for a, b in zip(samples, samples[1:], strict=False)]
    assert all(abs(g - 36.0) < 1e-6 for g in gaps)
    assert clock.now() - start <= 0.1 * 3600


def test_soak_reports_every_sample_as_it_goes() -> None:
    """T3.24: the callback sees each new sample, so a run cut off midway keeps its results."""
    clock = FakeClock()
    seen: list[int] = []
    run(clock, SlowClient(clock, seconds=1, fail_every=3), requests=6, hours=0.0, seen=seen)
    assert seen == [1, 2, 3, 4, 5, 6]


def test_soak_markdown_shows_status_and_hourly_breakdown() -> None:
    """T3.24: the report says whether the run is still going and how each hour went."""
    clock = FakeClock()
    samples = run(clock, SlowClient(clock, seconds=600, fail_every=4), requests=20, hours=2.0)
    text = soak_markdown(ROUTE, samples, "test", status="running", planned=20, hours=2.0)
    assert "Status: running" in text
    assert "| Hour |" in text
    assert "| 0 |" in text and "| 1 |" in text
    assert "429 responses:" in text
    done = soak_markdown(ROUTE, samples, "test", status="stopped at time limit",
                         planned=20, hours=2.0)
    assert "Status: stopped at time limit" in done


def test_cli_soak_writes_report_after_each_sample(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """T3.24: the CLI rewrites the report after every sample, with keys redacted."""
    from click.testing import CliRunner

    import repro.llm.live as live
    from repro.cli import main

    out = tmp_path / "soak.md"
    snapshots: list[str] = []
    real_soak = live.soak

    def fake_soak(registry, route_id, requests, hours, env=None, max_tokens=2048, *,  # type: ignore[no-untyped-def]
                  client=None, clock=None, on_sample=None):
        clock = FakeClock()
        slow = SlowClient(clock, seconds=1)

        def spy(samples):  # type: ignore[no-untyped-def]
            assert on_sample is not None
            on_sample(samples)
            snapshots.append(out.read_text())

        return real_soak(registry, route_id, requests, hours, env=env, client=slow,
                         clock=clock, on_sample=spy)

    monkeypatch.setattr(live, "soak", fake_soak)
    for k, v in ENV.items():
        monkeypatch.setenv(k, v)
    res = CliRunner().invoke(main, ["llm", "soak", "--route", ROUTE, "--requests", "3",
                                    "--hours", "0.01", "--out", str(out)])
    assert res.exit_code == 0, res.output
    assert len(snapshots) == 3
    assert "Requests: 1;" in snapshots[0] and "Status: running" in snapshots[0]
    final = out.read_text()
    assert "Requests: 3;" in final and "Status: finished" in final
    assert "nvapi-test" not in final
