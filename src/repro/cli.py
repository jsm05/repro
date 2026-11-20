"""Command line entry point: `repro ...`."""

from __future__ import annotations

from pathlib import Path

import click

from repro.datasets.fixtures import FixtureSource, check_fixture
from repro.gate.checks import GateConfig, OnlineGate
from repro.models import Candidate, InstanceOutcome
from repro.oracle.oracle import run_oracle
from repro.sandbox.local import local_factory

DEFAULT_FIXTURES = Path("tests/fixtures/mini_repos")


@click.group()
def main() -> None:
    """Repro: turn a GitHub issue into a verified failing test."""


@main.group()
def fixtures() -> None:
    """Fixture repos used by the hermetic test suite."""


@fixtures.command("check")
@click.option(
    "--root",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=DEFAULT_FIXTURES,
    show_default=True,
)
def fixtures_check(root: Path) -> None:
    """Run each gold test before and after its fix; expect F→P everywhere."""
    source = FixtureSource(root)
    failures = 0
    for name in source.names():
        result = check_fixture(source.load(name))
        status = "ok" if result.ok else "FAIL"
        detail = f"  {result.error}" if result.error else ""
        click.echo(f"{name:<24} {result.transition:<8} {status}{detail}")
        if not result.ok:
            failures += 1
            for tid, st in {**result.before, **result.after, **result.p2p_after}.items():
                click.echo(f"    {tid}: {st.value}")
    if failures:
        raise SystemExit(1)


@main.command()
@click.option("--fixture", "fixture_name", required=True, help="Fixture name, e.g. f01_off_by_one")
@click.option(
    "--root",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=DEFAULT_FIXTURES,
    show_default=True,
)
@click.option(
    "--patch",
    "patch_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Candidate diff. Default: the fixture's gold test.",
)
@click.option("--test", "test_ids", multiple=True, help="Test id the candidate adds (repeatable).")
@click.option("--oracle/--no-oracle", default=True, show_default=True)
@click.option("--timeout", default=60.0, show_default=True, help="Seconds per test run.")
@click.option("--json", "as_json", is_flag=True, help="Print verdicts as JSON.")
def verify(
    fixture_name: str,
    root: Path,
    patch_path: Path | None,
    test_ids: tuple[str, ...],
    oracle: bool,
    timeout: float,
    as_json: bool,
) -> None:
    """Run the online gate (and the oracle) on a candidate for a fixture."""
    fx = FixtureSource(root).load(fixture_name)
    sealed = fx.instance.sealed
    if patch_path is None:
        cand = Candidate(diff=sealed.gold_test_patch, test_ids=sealed.fail_to_pass, origin="gold")
    else:
        if not test_ids:
            raise click.UsageError("--test is required with --patch")
        cand = Candidate(diff=patch_path.read_text(), test_ids=list(test_ids), origin="file")
    factory = local_factory(fx.repo_dir)
    cfg = GateConfig(timeout_s=timeout)
    gate = OnlineGate(factory, cfg).verify(cand)
    ok = gate.passed
    if as_json:
        click.echo(gate.model_dump_json(indent=2))
    else:
        click.echo(f"gate: {'PASS' if gate.passed else 'FAIL at ' + str(gate.failed_check)}")
        click.echo(f"  {gate.reason}")
        for tid, statuses in gate.test_status.items():
            click.echo(f"  {tid}: {' '.join(s.value for s in statuses)}")
        for tid, sig in gate.signatures.items():
            click.echo(f"  signature {tid}: {sig}")
    if oracle:
        verdict = run_oracle(cand, sealed.gold_fix_patch, factory, cfg)
        ok = ok and verdict.outcome is InstanceOutcome.REPRODUCED
        if as_json:
            click.echo(verdict.model_dump_json(indent=2))
        else:
            click.echo(f"oracle: {verdict.outcome.value}")
            click.echo(f"  {verdict.reason}")
            for tid, tr in verdict.transitions.items():
                click.echo(f"  {tr.label}  {tid}")
    if not ok:
        raise SystemExit(1)


@main.group()
def llm() -> None:
    """Free-tier model routes: inspect, check live, measure."""


ROUTES_OPT = click.option(
    "--config", "config_path", type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=Path("config/routes.yaml"), show_default=True,
)


@llm.command("routes")
@ROUTES_OPT
def llm_routes(config_path: Path) -> None:
    """List routes, logical models and chains, with validation warnings."""
    from repro.llm.registry import ConfigError, Registry

    reg = Registry.from_yaml(config_path)
    for r in reg.routes.values():
        state = "on " if r.enabled else "off"
        lim = ", ".join(f"{k}={v}" for k, v in r.limits.model_dump().items() if v is not None)
        click.echo(f"{state} {r.id:<26} {r.model:<40} {lim} verified={r.verified_on}")
    for name, ids in reg.data.logical_models.items():
        click.echo(f"logical {name}: {', '.join(ids)}")
        try:
            for w in reg.validate_for_eval(name):
                click.echo(f"  warning: {w}")
        except ConfigError as exc:
            click.echo(f"  not usable in evals: {exc}")
    for name, members in reg.data.chains.items():
        click.echo(f"chain {name}: {' -> '.join(members)}")


@llm.command("quota")
@click.option("--state", type=click.Path(path_type=Path), default=Path("runs/quota_state.json"),
              show_default=True)
def llm_quota(state: Path) -> None:
    """Show persisted daily and monthly quota counters."""
    if not state.exists():
        click.echo(f"no quota state at {state}")
        return
    import json

    for route_id, entry in json.loads(state.read_text()).items():
        click.echo(f"{route_id}: {entry}")


@llm.command("ping")
@ROUTES_OPT
@click.option("--route", "route_id", default=None, help="Only this route (default: all enabled).")
def llm_ping(config_path: Path, route_id: str | None) -> None:
    """One tiny live call per enabled route (needs API keys)."""
    import os

    from repro.llm.client import OpenAICompatClient
    from repro.llm.errors import LLMError
    from repro.llm.registry import Registry
    from repro.llm.secrets import known_secrets, redact

    reg = Registry.from_yaml(config_path)
    failed = 0
    for r in reg.routes.values():
        if not r.enabled or (route_id and r.id != route_id):
            continue
        prov = reg.provider(r)
        key = os.environ.get(prov.api_key_env, "").strip()
        if not key:
            click.echo(f"{r.id}: skipped ({prov.api_key_env} not set)")
            continue
        try:
            resp = OpenAICompatClient(r, prov.base_url, key).complete(
                [{"role": "user", "content": "Reply with OK."}], max_tokens=256)
            click.echo(f"{r.id}: ok in {resp.latency_s:.1f}s, {resp.total_tokens} tokens")
        except LLMError as exc:
            failed += 1
            click.echo(f"{r.id}: FAILED {redact(str(exc), known_secrets(reg, os.environ))}")
    if failed:
        raise SystemExit(1)


@llm.command("smoke")
@ROUTES_OPT
@click.option("--out", type=click.Path(path_type=Path), default=Path("eval/live-smoke.md"),
              show_default=True)
@click.option("--ledger", "ledger_path", type=click.Path(path_type=Path),
              default=Path("runs/smoke-ledger.jsonl"), show_default=True)
def llm_smoke(config_path: Path, out: Path, ledger_path: Path) -> None:
    """Live smoke test of every enabled route; writes a markdown report (T3.L1)."""
    import datetime as dt
    import os

    from repro.llm.budget import Ledger
    from repro.llm.live import smoke, smoke_markdown, write_report
    from repro.llm.registry import Registry
    from repro.llm.secrets import known_secrets

    reg = Registry.from_yaml(config_path)
    report = smoke(reg, Ledger(ledger_path))
    when = dt.datetime.now(dt.UTC).isoformat(timespec="minutes")
    click.echo(write_report(out, smoke_markdown(report, when), known_secrets(reg, os.environ)))
    if not report.all_ok:
        raise SystemExit(1)


@llm.command("soak")
@ROUTES_OPT
@click.option("--route", "route_id", default="nvidia/mistral-large-4", show_default=True)
@click.option("--requests", "n", default=500, show_default=True)
@click.option("--hours", default=5.0, show_default=True,
              help="Time limit; no new request starts after it. Keep under 5.5 in CI.")
@click.option("--out", type=click.Path(path_type=Path), default=Path("eval/soak.md"),
              show_default=True)
def llm_soak(config_path: Path, route_id: str, n: int, hours: float, out: Path) -> None:
    """Send up to N requests to one route within H hours and report what happened (T3.L2).

    The report is rewritten after every request, so a run that gets cut off keeps
    everything it collected."""
    import datetime as dt
    import os

    import repro.llm.live as live
    from repro.llm.registry import Registry
    from repro.llm.secrets import known_secrets

    reg = Registry.from_yaml(config_path)
    secrets = known_secrets(reg, os.environ)
    when = dt.datetime.now(dt.UTC).isoformat(timespec="minutes")

    def save(samples: list[live.SoakSample]) -> None:
        live.write_report(out, live.soak_markdown(route_id, samples, when, status="running",
                                                  planned=n, hours=hours), secrets)

    samples = live.soak(reg, route_id, n, hours, on_sample=save)
    status = ("finished: all requests sent" if len(samples) >= n
              else "finished: stopped at time limit")
    click.echo(live.write_report(out, live.soak_markdown(route_id, samples, when,
                                                         status=status, planned=n,
                                                         hours=hours), secrets))


if __name__ == "__main__":
    main()
