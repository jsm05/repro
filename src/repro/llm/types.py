"""What every model call returns, and the interface the agent and baselines call."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Protocol

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
    # "reasoning" when the provider left `content` empty and the reply was taken from its
    # separate reasoning field (D-M5-11).
    text_source: str = "content"
    # When content was empty: the provider's raw message, for diagnosis (truncated).
    raw_note: str = ""

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
        tools: Sequence[Mapping[str, Any]] | None = None,
    ) -> LLMResponse:
        """`tools`: optional native tool schemas; routes without native tool support
        ignore them (the text action protocol still applies)."""
        ...
