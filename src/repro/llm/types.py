"""What every model call returns, and the interface the agent and baselines call."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol

from pydantic import BaseModel, ConfigDict

Message = Mapping[str, str]


class LLMResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    text: str
    prompt_tokens: int
    completion_tokens: int
    reasoning_tokens: int = 0
    finish_reason: str = ""
    route_id: str = ""
    model: str = ""
    latency_s: float = 0.0
    retries: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class LLM(Protocol):
    def complete(
        self,
        messages: Sequence[Message],
        *,
        max_tokens: int,
        temperature: float = 0.0,
        stop: Sequence[str] | None = None,
    ) -> LLMResponse: ...
