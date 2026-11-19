"""Token ceilings and the token ledger.

Ceilings are checked BEFORE each call against the worst case (estimated prompt plus
max_tokens), so a call that could cross a ceiling is never made. Checking after the
call would only tell you the ceiling had already been crossed.

The ledger is append-only JSONL, locked per write so parallel workers on one machine
can share it. A truncated last line (a crash mid-write) is skipped and reported.
"""

from __future__ import annotations

import fcntl
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict

BudgetKind = Literal["tokens", "requests", "gate_checks", "run_tokens"]
LIST_PRICES_PATH = Path(__file__).with_name("list_prices.yaml")


class BudgetExceeded(Exception):
    def __init__(self, kind: BudgetKind, message: str):
        super().__init__(f"{kind}: {message}")
        self.kind = kind


@dataclass
class TokenBudget:
    """Per-bug ceilings (DESIGN 5.6 stop conditions)."""

    max_tokens: int = 600_000
    max_requests: int = 40
    max_gate_checks: int = 8
    used_tokens: int = 0
    used_requests: int = 0
    used_gate_checks: int = 0

    def check_call(self, worst_case_tokens: int) -> None:
        if self.used_requests + 1 > self.max_requests:
            raise BudgetExceeded("requests", f"{self.used_requests} of {self.max_requests} used")
        if self.used_tokens + worst_case_tokens > self.max_tokens:
            raise BudgetExceeded(
                "tokens",
                f"{self.used_tokens} used + up to {worst_case_tokens} "
                f"would exceed {self.max_tokens}",
            )

    def record_call(self, tokens: int) -> None:
        self.used_requests += 1
        self.used_tokens += tokens

    def check_gate(self) -> None:
        if self.used_gate_checks + 1 > self.max_gate_checks:
            raise BudgetExceeded("gate_checks", f"{self.max_gate_checks} gate checks used")

    def record_gate(self) -> None:
        self.used_gate_checks += 1


class LedgerEntry(BaseModel):
    model_config = ConfigDict(frozen=True)

    ts: float
    run_id: str = ""
    unit_id: str = ""
    route_id: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    reasoning_tokens: int = 0
    estimated_prompt_tokens: int = 0
    latency_s: float = 0.0
    retries: int = 0
    outcome: str = "ok"

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class Ledger:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def append(self, entry: LedgerEntry) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = entry.model_dump_json() + "\n"
        with self.path.open("a") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                f.write(line)
                f.flush()
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)

    def read(self) -> tuple[list[LedgerEntry], int]:
        """All valid entries, plus how many lines were unreadable (e.g. truncated)."""
        if not self.path.exists():
            return [], 0
        entries, bad = [], 0
        for line in self.path.read_text().splitlines():
            if not line.strip():
                continue
            try:
                entries.append(LedgerEntry.model_validate_json(line))
            except ValueError:
                bad += 1
        return entries, bad

    def total_tokens(self, run_id: str | None = None) -> int:
        entries, _ = self.read()
        return sum(e.total_tokens for e in entries if run_id is None or e.run_id == run_id)


class RunBudget:
    """Run-level ceiling across all bugs: a safety net against loop bugs."""

    def __init__(self, ledger: Ledger | None, run_id: str = "", max_tokens: int = 150_000_000):
        self.max_tokens = max_tokens
        self.used = ledger.total_tokens(run_id) if ledger else 0

    def check(self, worst_case_tokens: int) -> None:
        if self.used + worst_case_tokens > self.max_tokens:
            raise BudgetExceeded(
                "run_tokens",
                f"run has used {self.used}; up to {worst_case_tokens} more would exceed "
                f"the run ceiling of {self.max_tokens}",
            )

    def record(self, tokens: int) -> None:
        self.used += tokens


def load_list_prices(path: Path = LIST_PRICES_PATH) -> dict[str, tuple[float, float] | None]:
    raw = yaml.safe_load(path.read_text()) or {}
    out: dict[str, tuple[float, float] | None] = {}
    for model, p in (raw.get("models") or {}).items():
        if p and p.get("input_per_m") is not None and p.get("output_per_m") is not None:
            out[model] = (float(p["input_per_m"]), float(p["output_per_m"]))
        else:
            out[model] = None
    return out


def list_price_usd(
    entries: list[LedgerEntry], prices: dict[str, tuple[float, float] | None]
) -> float | None:
    """What these calls would have cost at paid list prices. None if a price is unknown."""
    total = 0.0
    for e in entries:
        price = prices.get(e.model)
        if price is None:
            return None
        total += e.prompt_tokens / 1e6 * price[0] + e.completion_tokens / 1e6 * price[1]
    return total

