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


# -- eval runner, status, report, export, show (M4) ----------------------------------

RESULTS_OPT = click.option("--results-dir", type=click.Path(path_type=Path),
                           default=Path("results"), show_default=True,
                           help="Local results root (holds one directory per run id).")


def _git_commit() -> str:
    import subprocess

    res = subprocess.run(["git", "rev-parse", "--short=12", "HEAD"], capture_output=True,
                         text=True, check=False)
    return res.stdout.strip() if res.returncode == 0 else ""


def _deadline_at(deadline: str | None, minutes: float | None) -> float | None:
    import datetime as dt
    import time

    if minutes is not None:
        return time.time() + minutes * 60
    if deadline is None:
        return None
    hh, _, mm = deadline.partition(":")
    now = dt.datetime.now(dt.UTC)
    at = now.replace(hour=int(hh), minute=int(mm or 0), second=0, microsecond=0)
    if at <= now:
        at += dt.timedelta(days=1)
    return at.timestamp()


def _dataset(cfg_dataset: str, fixtures_root: Path):  # type: ignore[no-untyped-def]
    from repro.eval.execute import FixtureDataset

    if cfg_dataset != "fixtures":
        raise click.UsageError(f"dataset {cfg_dataset!r} arrives in M6; only fixtures for now")
    return FixtureDataset(str(fixtures_root))


@main.command("eval")
@click.option("--config", "config_path", required=True,
              type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--run-id", required=True, help="Results go to <results>/<run-id>/.")
@RESULTS_OPT
@click.option("--results-branch", "remote", default=None,
              help="Git remote URL: commit each row to its `results` branch instead.")
@click.option("--workdir", type=click.Path(path_type=Path),
              default=Path(".repro/results-branch"), show_default=True,
              help="Local clone used with --results-branch.")
@click.option("--fixtures-root", type=click.Path(exists=True, file_okay=False, path_type=Path),
              default=DEFAULT_FIXTURES, show_default=True)
@click.option("--routes", "routes_path", type=click.Path(path_type=Path),
              default=Path("config/routes.yaml"), show_default=True)
@click.option("--max-units", type=int, default=None, help="Stop after N units.")
@click.option("--deadline", default=None, help="HH:MM UTC; no unit starts that would end later.")
@click.option("--deadline-minutes", type=float, default=None,
              help="Like --deadline, counted from now.")
@click.option("--shard", "shard_text", default="1/1", show_default=True, help="k/N")
@click.option("--stop-when-exhausted", is_flag=True,
              help="Exit cleanly when no route of the logical model has quota left.")
def eval_cmd(config_path: Path, run_id: str, results_dir: Path, remote: str | None,
             workdir: Path, fixtures_root: Path, routes_path: Path, max_units: int | None,
             deadline: str | None, deadline_minutes: float | None, shard_text: str,
             stop_when_exhausted: bool) -> None:
    """Run a benchmark config in resumable batches."""
    from repro.config import EvalConfig
    from repro.eval.execute import Executor
    from repro.eval.llm_setup import cassette_factory, chain_logicals, live_factory, recording
    from repro.eval.run import BatchOptions, ConfigMismatch, LockHeld, Runner
    from repro.eval.store import GitStore, LocalStore, ResultsStore
    from repro.llm.budget import Ledger, load_list_prices
    from repro.llm.limiter import parse_shard
    from repro.llm.registry import Registry

    cfg = EvalConfig.from_yaml(config_path)
    shard, shards = parse_shard(shard_text)
    dataset = _dataset(cfg.dataset, fixtures_root)
    ids = cfg.instances if cfg.instances is not None else dataset.ids()
    store: ResultsStore = (GitStore(remote, run_id, workdir) if remote
                           else LocalStore(results_dir, run_id))
    verified = ""
    windows: list[int] = []
    if cfg.llm == "cassette":
        if not cfg.cassette_dir:
            raise click.UsageError("llm: cassette needs cassette_dir")
        factory = cassette_factory(Path(cfg.cassette_dir),
                                   Ledger(store.run_dir / f"ledger-{shard}.jsonl"),
                                   cfg.cassette_match)

        def quota_ok() -> bool:
            return True
    else:
        reg = Registry.from_yaml(routes_path)
        factory, quota_ok = live_factory(cfg, reg, store.run_dir, run_id, shard, shards)
        if cfg.llm == "record":
            if not cfg.cassette_dir:
                raise click.UsageError("llm: record needs cassette_dir")
            factory = recording(factory, Path(cfg.cassette_dir))
        routes = [r for m in chain_logicals(cfg, reg) for r in reg.enabled_routes(m)]
        dates = [r.verified_on for r in routes if r.verified_on]
        verified = max(dates).isoformat() if dates else ""
        windows = [r.context_window for r in routes]
    executor = Executor(cfg, run_id, dataset, factory, prices=load_list_prices(),
                        git_commit=_git_commit(), routes_verified_on=verified,
                        route_windows=windows)
    options = BatchOptions(shard=shard, shards=shards, max_units=max_units,
                           deadline_at=_deadline_at(deadline, deadline_minutes),
                           stop_when_exhausted=stop_when_exhausted)
    try:
        summary = Runner(cfg, run_id, ids, store, executor, options=options,
                         quota_ok=quota_ok).run()
    except (LockHeld, ConfigMismatch) as exc:
        click.echo(f"error: {exc}", err=True)
        raise SystemExit(2) from exc
    click.echo(f"run {run_id} shard {shard}/{shards}: stopped ({summary.stop_reason}); "
               f"{len(summary.written)} written, {len(summary.deferred)} deferred, "
               f"{len(summary.infra_errors)} infra errors, {summary.skipped_done} already done, "
               f"{summary.remaining} remaining")


def _run_ids(run_dir: Path, fixtures_root: Path) -> list[str]:
    from repro.config import EvalConfig

    frozen = run_dir / "config.yaml"
    if not frozen.exists():
        raise click.UsageError(f"{run_dir} has no config.yaml; is the run id right?")
    cfg = EvalConfig.from_yaml(frozen)
    return cfg.instances if cfg.instances is not None else _dataset(cfg.dataset,
                                                                    fixtures_root).ids()


@main.command("status")
@click.argument("run_id")
@RESULTS_OPT
@click.option("--fixtures-root", type=click.Path(exists=True, file_okay=False, path_type=Path),
              default=DEFAULT_FIXTURES, show_default=True)
@click.option("--shards", default=6, show_default=True, help="Shards per night, for the ETA.")
def status_cmd(run_id: str, results_dir: Path, fixtures_root: Path, shards: int) -> None:
    """Progress of a run: done, remaining, deferred, tokens, quota, nights left."""
    from repro.eval.status import compute_status, render_status

    run_dir = results_dir / run_id
    st = compute_status(run_dir, _run_ids(run_dir, fixtures_root), shards=shards)
    click.echo(render_status(run_id, st))


@main.command("report")
@click.option("--run", "run_ids", multiple=True, required=True, help="Run id (repeatable).")
@RESULTS_OPT
@click.option("--out", type=click.Path(dir_okay=False, path_type=Path), default=None,
              help="Write the markdown here (plots go to plots/ beside it). Default: stdout.")
@click.option("--title", default="Results", show_default=True)
def report_cmd(run_ids: tuple[str, ...], results_dir: Path, out: Path | None, title: str) -> None:
    """Results tables and plots from result rows only."""
    from repro.eval.report import ReportError, build_report
    from repro.eval.rows import read_rows
    from repro.eval.store import shard_files

    rows = [r for rid in run_ids for r in read_rows(shard_files(results_dir / rid))]
    try:
        text, plots = build_report(rows, title)
    except ReportError as exc:
        click.echo(f"error: {exc}", err=True)
        raise SystemExit(2) from exc
    if out is None:
        click.echo(text, nl=False)
        return
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text)
    (out.parent / "plots").mkdir(exist_ok=True)
    for name, svg in plots.items():
        (out.parent / "plots" / name).write_text(svg)
    click.echo(f"wrote {out}")


@main.command("export")
@click.option("--run", "run_id", required=True)
@RESULTS_OPT
def export_cmd(run_id: str, results_dir: Path) -> None:
    """Write predictions.jsonl in SWT-bench format into the run directory."""
    from repro.eval.export import predictions_jsonl
    from repro.eval.rows import read_rows
    from repro.eval.store import shard_files

    run_dir = results_dir / run_id
    rows = read_rows(shard_files(run_dir))
    if not rows:
        raise click.UsageError(f"no rows in {run_dir}")
    name = f"repro-{rows[0].config_id}"
    (run_dir / "predictions.jsonl").write_text(predictions_jsonl(rows, run_dir, name))
    click.echo(f"wrote {run_dir / 'predictions.jsonl'} ({len(rows)} instances)")


@main.command("show")
@click.argument("run_id")
@click.argument("instance_id")
@RESULTS_OPT
def show_cmd(run_id: str, instance_id: str, results_dir: Path) -> None:
    """Readable timeline of one unit's trace."""
    from repro.eval.rows import read_trace
    from repro.eval.show import render_trace

    path = results_dir / run_id / "traces" / f"{instance_id}.jsonl"
    if not path.exists():
        raise click.UsageError(f"no trace at {path}")
    click.echo(render_trace(read_trace(path)))


if __name__ == "__main__":
    main()
