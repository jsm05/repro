"""Command line entry point: `repro ...`."""

from __future__ import annotations

from pathlib import Path

import click

from repro.datasets.fixtures import FixtureSource, check_fixture

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


if __name__ == "__main__":
    main()
