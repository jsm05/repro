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


if __name__ == "__main__":
    main()
