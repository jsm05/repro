"""Live checks against real free-tier providers. Only run in live-smoke.yml / soak.yml,
or by hand with keys set: pytest -m live_llm tests/live"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from repro.llm.budget import Ledger
from repro.llm.live import smoke, soak, soak_markdown
from repro.llm.registry import Registry
from tests.helpers import ROOT

pytestmark = pytest.mark.live_llm
ROUTES = ROOT / "config" / "routes.yaml"


@pytest.mark.skipif(not os.environ.get("NVIDIA_API_KEY"), reason="NVIDIA_API_KEY not set")
def test_live_smoke(tmp_path: Path) -> None:
    """T3.L1: one small call per enabled route answers; ledger tokens equal provider usage."""
    report = smoke(Registry.from_yaml(ROUTES), Ledger(tmp_path / "ledger.jsonl"))
    primary = next(c for c in report.checks if c.route_id == "nvidia/mistral-large-4")
    assert primary.ok, primary.detail
    assert all(c.ledger_matches for c in report.checks if c.ok)
    assert report.models.get("nvidia")


@pytest.mark.skipif(not os.environ.get("NVIDIA_API_KEY"), reason="NVIDIA_API_KEY not set")
def test_live_soak_short() -> None:
    """T3.L2: a (short) soak against the primary records outcomes and latency for the report.

    The real one-night soak runs through `repro llm soak` in soak.yml."""
    n = int(os.environ.get("REPRO_SOAK_REQUESTS", "4"))
    samples = soak(Registry.from_yaml(ROUTES), "nvidia/mistral-large-4", n, hours=0.0)
    assert len(samples) == n
    assert "Requests:" in soak_markdown("nvidia/mistral-large-4", samples, "test")
