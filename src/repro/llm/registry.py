"""Routes and logical models, loaded from config/routes.yaml.

A route is one (provider, exact model string) with its limits and quirks. A logical
model is a name configs use; it maps to routes that serve the SAME weights, so the
`pinned` policy can fail over between providers without changing the model.
"""

from __future__ import annotations

import datetime as dt
import logging
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

log = logging.getLogger(__name__)

DEFAULT_ROUTES_PATH = Path("config/routes.yaml")
STALE_AFTER_DAYS = 30


class ConfigError(ValueError):
    pass


class RouteLimits(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    rps: float | None = None
    rpm: int | None = None
    tpm: int | None = None
    rpd: int | None = None
    tpd: int | None = None
    tokens_per_month: int | None = None


class Provider(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    base_url: str = Field(min_length=1)
    api_key_env: str = Field(min_length=1)


class Route(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    provider: str
    model: str = Field(min_length=1)
    weights: str = Field(min_length=1)
    enabled: bool = True
    context_window: int = Field(gt=0)
    max_output_tokens: int = Field(gt=0)
    system_role: bool = True
    stop_sequences: bool = True
    reasoning: bool = False
    reasoning_effort: str | None = None
    # Send the agent's tool schemas natively. Needed where the server's tool-call parser
    # swallows replies the model writes as tool calls (NVIDIA Mistral Large 4, D-M5-13).
    native_tools: bool = False
    limits: RouteLimits
    reset: Literal["utc_day", "pacific_day"] = "utc_day"
    free: bool = False
    trains_on_data: bool = True
    verified_on: dt.date | None = None
    note: str = ""


class RoutesFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int
    providers: dict[str, Provider]
    routes: list[Route]
    logical_models: dict[str, list[str]]
    chains: dict[str, list[str]] = Field(default_factory=dict)


def is_floating_alias(model: str) -> bool:
    """`-latest` style names can change under a running eval."""
    low = model.lower()
    return low.endswith(("-latest", ":latest", "/latest")) or low == "latest"


class Registry:
    def __init__(self, data: RoutesFile):
        self.data = data
        self.routes = {r.id: r for r in data.routes}
        if len(self.routes) != len(data.routes):
            raise ConfigError("duplicate route ids")
        for r in data.routes:
            if r.provider not in data.providers:
                raise ConfigError(f"route {r.id}: unknown provider {r.provider!r}")
        for name, ids in data.logical_models.items():
            missing = [i for i in ids if i not in self.routes]
            if missing:
                raise ConfigError(f"logical model {name}: unknown routes {missing}")
            weights = {self.routes[i].weights for i in ids}
            if len(weights) > 1:
                raise ConfigError(
                    f"logical model {name} mixes different weights {sorted(weights)}; "
                    "a logical model may only group routes serving the same model"
                )
        for name, members in data.chains.items():
            bad = [m for m in members if m not in data.logical_models]
            if bad:
                raise ConfigError(f"chain {name}: unknown logical models {bad}")

    @classmethod
    def from_dict(cls, raw: object) -> Registry:
        try:
            return cls(RoutesFile.model_validate(raw))
        except ValidationError as exc:
            raise ConfigError(str(exc)) from exc

    @classmethod
    def from_yaml(cls, path: str | Path = DEFAULT_ROUTES_PATH) -> Registry:
        return cls.from_dict(yaml.safe_load(Path(path).read_text()))

    def provider(self, route: Route) -> Provider:
        return self.data.providers[route.provider]

    def logical_routes(self, logical: str) -> list[Route]:
        if logical not in self.data.logical_models:
            raise ConfigError(f"unknown logical model {logical!r}")
        return [self.routes[i] for i in self.data.logical_models[logical]]

    def enabled_routes(self, logical: str) -> list[Route]:
        return [r for r in self.logical_routes(logical) if r.enabled]

    def chain(self, name: str) -> list[str]:
        if name not in self.data.chains:
            raise ConfigError(f"unknown chain {name!r}")
        return list(self.data.chains[name])

    def validate_for_eval(
        self,
        logical: str,
        *,
        private_code: bool = False,
        today: dt.date | None = None,
    ) -> list[str]:
        """Refuse routes an eval must not use; return warnings for stale ones."""
        today = today or dt.date.today()
        routes = self.enabled_routes(logical)
        if not routes:
            raise ConfigError(f"logical model {logical!r} has no enabled routes")
        warnings = []
        for r in routes:
            if is_floating_alias(r.model):
                raise ConfigError(f"route {r.id}: floating alias {r.model!r} not allowed in evals")
            if not r.free:
                raise ConfigError(f"route {r.id} is not marked free: true")
            if private_code and r.trains_on_data:
                raise ConfigError(f"route {r.id} may train on prompts; refused for private code")
            if r.verified_on is None:
                warnings.append(f"route {r.id}: limits never verified")
            elif (today - r.verified_on).days > STALE_AFTER_DAYS:
                warnings.append(f"route {r.id}: limits last verified {r.verified_on}, re-check")
        for w in warnings:
            log.warning(w)
        return warnings
