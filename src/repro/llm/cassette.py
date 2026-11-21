"""Record real model responses and replay them later with no network.

Requests are keyed by a hash of their content. The same request may legitimately be
made several times (B1 draws 5 samples from one prompt), so each key holds a list
of responses replayed in order. Replay never falls through to a live call: a request
with no recording is an error.

`match="order"` replays responses in recorded order whatever the request. The agent
needs it: its prompts contain tool output (timings, temp paths) that differs between
runs, while its replayed actions are executed for real each time.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal

from repro.llm.types import LLM, LLMResponse, Message


class CassetteMiss(LookupError):
    pass


def request_key(
    messages: Sequence[Message], max_tokens: int, temperature: float, stop: Sequence[str] | None
) -> str:
    payload = {
        "messages": [{"role": m["role"], "content": m["content"]} for m in messages],
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stop": list(stop) if stop else None,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


class CassetteLLM:
    def __init__(
        self,
        path: str | Path,
        mode: Literal["record", "replay"],
        inner: LLM | None = None,
        match: Literal["request", "order"] = "request",
    ):
        if mode == "record" and inner is None:
            raise ValueError("record mode needs an inner LLM")
        self.path = Path(path)
        self.mode = mode
        self.inner = inner
        self.match = match
        self._order: list[dict[str, Any]] = []
        self._recorded: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self._cursor: dict[str, int] = defaultdict(int)
        if mode == "replay":
            for line in self.path.read_text().splitlines():
                if line.strip():
                    rec = json.loads(line)
                    self._recorded[rec["key"]].append(rec["response"])
                    self._order.append(rec["response"])

    def complete(
        self,
        messages: Sequence[Message],
        *,
        max_tokens: int,
        temperature: float = 0.0,
        stop: Sequence[str] | None = None,
    ) -> LLMResponse:
        key = request_key(messages, max_tokens, temperature, stop)
        if self.mode == "replay" and self.match == "order":
            i = self._cursor["__order__"]
            if i >= len(self._order):
                raise CassetteMiss(f"cassette has only {len(self._order)} responses; "
                                   "replay mode never calls a provider")
            self._cursor["__order__"] += 1
            return LLMResponse.model_validate(self._order[i])
        if self.mode == "replay":
            responses = self._recorded.get(key, [])
            i = self._cursor[key]
            if i >= len(responses):
                raise CassetteMiss(
                    f"no recorded response for request {key[:12]} (call {i + 1}); "
                    "replay mode never calls a provider"
                )
            self._cursor[key] += 1
            return LLMResponse.model_validate(responses[i])
        assert self.inner is not None
        resp = self.inner.complete(messages, max_tokens=max_tokens, temperature=temperature,
                                   stop=stop)
        record = {
            "key": key,
            "request": {"messages": [dict(m) for m in messages], "max_tokens": max_tokens,
                        "temperature": temperature, "stop": list(stop) if stop else None},
            "response": resp.model_dump(),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a") as f:
            f.write(json.dumps(record, sort_keys=True) + "\n")
        return resp
