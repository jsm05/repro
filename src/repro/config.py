"""Typed eval config and its hash (DESIGN 4.1, EVAL section 3).

Every result row carries the hash of the config that produced it, so results from
different setups can never be blended in one report.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field


class EvalConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    config_id: str
    kind: Literal["B0", "B1", "agent"]
    dataset: Literal["fixtures", "swtbench"] = "fixtures"
    # Instance ids to run, in order. None means every instance the dataset has.
    instances: list[str] | None = None
    logical_model: str = "primary"
    policy: Literal["pinned", "chain"] = "pinned"
    # live: real routes through the router. cassette: replay recorded responses only.
    llm: Literal["live", "cassette"] = "live"
    cassette_dir: str | None = None
    k: int = 5
    temperature: float = 0.8
    max_output_tokens: int = 8000
    prompt_cap: int = 32_000
    per_bug_tokens: int = 600_000
    max_requests: int = 40
    run_tokens: int = 150_000_000
    gate_timeout_s: float = 60.0
    max_deferrals: int = 3
    notes: str = Field(default="", description="Free text; part of the hash like every field")

    @property
    def hash(self) -> str:
        return config_hash(self)

    @classmethod
    def from_yaml(cls, path: str | Path) -> EvalConfig:
        return cls.model_validate(yaml.safe_load(Path(path).read_text()))

    def to_yaml(self) -> str:
        return yaml.safe_dump(self.model_dump(mode="json"), sort_keys=True)


def config_hash(cfg: EvalConfig) -> str:
    payload = json.dumps(cfg.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()[:16]
