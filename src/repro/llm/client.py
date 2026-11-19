"""One client for every provider: the official openai SDK pointed at each route's base URL.

The SDK's own retries are off (max_retries=0) so the router owns every retry
decision. Per-route quirks are handled by capability flags on the route, not by
provider-specific classes.
"""

from __future__ import annotations

import os
import time
from collections.abc import Mapping, Sequence
from typing import Any

import openai

from repro.llm.errors import ErrorClass, LLMError, classify, retry_after_seconds
from repro.llm.registry import Registry, Route
from repro.llm.secrets import read_key, redact
from repro.llm.types import LLMResponse, Message


class OpenAICompatClient:
    def __init__(
        self,
        route: Route,
        base_url: str,
        api_key: str,
        timeout_s: float = 300.0,
        http_client: Any = None,
    ):
        self.route = route
        api_key = api_key.strip()
        self._secrets = (api_key,)
        kwargs: dict[str, Any] = {
            "base_url": base_url,
            "api_key": api_key,
            "max_retries": 0,
            "timeout": timeout_s,
        }
        if http_client is not None:
            kwargs["http_client"] = http_client
        self._sdk = openai.OpenAI(**kwargs)

    @property
    def max_retries(self) -> int:
        return int(self._sdk.max_retries)

    def build_request(
        self,
        messages: Sequence[Message],
        *,
        max_tokens: int,
        temperature: float = 0.0,
        stop: Sequence[str] | None = None,
    ) -> dict[str, Any]:
        r = self.route
        msgs = [{"role": m["role"], "content": m["content"]} for m in messages]
        if not r.system_role:
            system = "\n\n".join(m["content"] for m in msgs if m["role"] == "system")
            msgs = [m for m in msgs if m["role"] != "system"]
            if system:
                first_user = next((m for m in msgs if m["role"] == "user"), None)
                if first_user is None:
                    msgs.insert(0, {"role": "user", "content": system})
                else:
                    first_user["content"] = system + "\n\n" + first_user["content"]
        req: dict[str, Any] = {
            "model": r.model,
            "messages": msgs,
            "max_tokens": min(max_tokens, r.max_output_tokens),
            "temperature": temperature,
        }
        if stop and r.stop_sequences:
            req["stop"] = list(stop)
        if r.reasoning_effort:
            req["extra_body"] = {"reasoning_effort": r.reasoning_effort}
        return req

    def complete(
        self,
        messages: Sequence[Message],
        *,
        max_tokens: int,
        temperature: float = 0.0,
        stop: Sequence[str] | None = None,
    ) -> LLMResponse:
        req = self.build_request(
            messages, max_tokens=max_tokens, temperature=temperature, stop=stop
        )
        start = time.monotonic()
        provider = self.route.provider
        try:
            raw = self._sdk.chat.completions.with_raw_response.create(**req)
            resp = raw.parse()
        except openai.APIStatusError as exc:
            body = exc.response.text if exc.response is not None else str(exc.body)
            body = redact(body, self._secrets)
            headers = dict(exc.response.headers) if exc.response is not None else {}
            raise LLMError(
                classify(provider, exc.status_code, body),
                body,
                status=exc.status_code,
                provider=provider,
                retry_after=retry_after_seconds(headers, body),
                headers=headers,
            ) from None
        except (openai.APITimeoutError, openai.APIConnectionError) as exc:
            detail = redact(f"{type(exc).__name__}: {exc}", self._secrets)
            raise LLMError(ErrorClass.TRANSIENT, detail, provider=provider) from None
        latency = time.monotonic() - start
        choice = resp.choices[0] if resp.choices else None
        text = (choice.message.content or "") if choice is not None else ""
        usage = resp.usage
        if choice is None or not text.strip():
            raise LLMError(ErrorClass.EMPTY_OUTPUT, "response had no content", provider=provider)
        details = getattr(usage, "completion_tokens_details", None) if usage else None
        return LLMResponse(
            text=text,
            prompt_tokens=int(usage.prompt_tokens) if usage else 0,
            completion_tokens=int(usage.completion_tokens) if usage else 0,
            reasoning_tokens=int(getattr(details, "reasoning_tokens", 0) or 0),
            finish_reason=str(choice.finish_reason or ""),
            route_id=self.route.id,
            model=self.route.model,
            latency_s=latency,
        )


def make_client_factory(
    registry: Registry, env: Mapping[str, str] | None = None, timeout_s: float = 300.0
) -> Any:
    """Route -> client, reading each provider's key from the environment."""
    env = env if env is not None else os.environ
    cache: dict[str, OpenAICompatClient] = {}

    def factory(route: Route) -> OpenAICompatClient:
        if route.id not in cache:
            prov = registry.provider(route)
            key = read_key(env, prov.api_key_env)
            if not key:
                raise LLMError(
                    ErrorClass.AUTH, f"{prov.api_key_env} is not set", provider=route.provider
                )
            cache[route.id] = OpenAICompatClient(route, prov.base_url, key, timeout_s)
        return cache[route.id]

    return factory
