"""One result row per (instance, config) unit, and the trace event format (DESIGN 6)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from repro.models import InstanceOutcome

GATE_CHECKS = ("G0", "G1", "G2", "G3", "G4", "G5")


class ResultRow(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: str
    instance_id: str
    config_id: str
    config_hash: str
    kind: str
    logical_model: str
    model: str = ""  # exact model version string(s) that answered, joined with "+"
    routes_used: list[str] = Field(default_factory=list)
    route_calls: dict[str, int] = Field(default_factory=dict)
    mixed: bool = False
    # Two scoring modes from one run (EVAL 3): always-emit and gated.
    outcome: InstanceOutcome
    outcome_gated: InstanceOutcome
    gate_passed: bool | None = None  # did the emitted candidate pass the online gate
    stop_reason: str = ""
    deferrals: int = 0
    candidate_sha256: str = ""
    # Cumulative model tokens at the moment the emitted candidate first existed.
    candidate_tokens_at: int | None = None
    gate_rejections: dict[str, int] = Field(default_factory=dict)  # per check, all candidates
    oracle_transitions: dict[str, str] = Field(default_factory=dict)  # test id -> "F→P"
    steps: int = 0
    gate_checks: int = 0
    requests: int = 0
    retries: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    tokens_reasoning: int = 0
    rate_limit_wait_s: float = 0.0
    list_price_usd: float | None = None
    wall_clock_s: float = 0.0
    infra_error: str = ""
    git_commit: str = ""
    routes_verified_on: str = ""

    @property
    def tokens(self) -> int:
        return self.tokens_in + self.tokens_out

    @property
    def key(self) -> tuple[str, str]:
        return (self.instance_id, self.config_id)


class TraceEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    ts: float
    run_id: str
    instance_id: str
    seq: int
    type: str  # llm_call | llm_retry | failover | trim | tool_call | gate_check | stop | oracle
    payload: dict[str, Any] = Field(default_factory=dict)
    route: str = ""
    tokens: int = 0


def read_rows(paths: list[Path]) -> list[ResultRow]:
    """Rows from shard files, in file then line order. A truncated last line is skipped."""
    rows: list[ResultRow] = []
    for p in paths:
        if not p.exists():
            continue
        for line in p.read_text().splitlines():
            if not line.strip():
                continue
            try:
                rows.append(ResultRow.model_validate_json(line))
            except ValueError:
                continue
    return rows


def read_trace(path: Path) -> list[TraceEvent]:
    return [TraceEvent.model_validate(json.loads(x)) for x in path.read_text().splitlines()
            if x.strip()]
