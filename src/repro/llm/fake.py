"""Test doubles: an in-process HTTP server speaking OpenAI chat completions, and a
scripted LLM. Neither touches the network beyond 127.0.0.1."""

from __future__ import annotations

import json
import threading
import time
from collections import deque
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from repro.llm.types import LLMResponse, Message


@dataclass
class Reply:
    status: int = 200
    body: Any = None
    headers: dict[str, str] = field(default_factory=dict)
    delay_s: float = 0.0


def ok(text: str = "ok", prompt_tokens: int = 10, completion_tokens: int = 5,
       reasoning_tokens: int = 0, headers: dict[str, str] | None = None) -> Reply:
    body = {
        "id": "chatcmpl-fake",
        "object": "chat.completion",
        "created": 0,
        "model": "fake",
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": text},
            "finish_reason": "stop",
        }],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
            "completion_tokens_details": {"reasoning_tokens": reasoning_tokens},
        },
    }
    return Reply(200, body, dict(headers or {}))


def error(status: int, body: Any, headers: dict[str, str] | None = None) -> Reply:
    return Reply(status, body, dict(headers or {}))


class FakeProvider:
    """Serves scripted replies in order; when the script runs out, serves `default`."""

    def __init__(self, default: Reply | None = None, model_ids: Sequence[str] = ("fake-model",)):
        self.default = default or ok()
        self.model_ids = list(model_ids)
        self.requests: list[dict[str, Any]] = []
        self._script: deque[Reply] = deque()
        self._lock = threading.Lock()
        provider = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:
                pass

            def _send(self, reply: Reply) -> None:
                if reply.delay_s:
                    time.sleep(reply.delay_s)
                body = reply.body
                data = body.encode() if isinstance(body, str) else json.dumps(body).encode()
                try:
                    self.send_response(reply.status)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(data)))
                    for k, v in reply.headers.items():
                        self.send_header(k, v)
                    self.end_headers()
                    self.wfile.write(data)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def do_GET(self) -> None:
                self._send(Reply(200, {"data": [{"id": m} for m in provider.model_ids]}))

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length) or b"{}")
                with provider._lock:
                    provider.requests.append({
                        "path": self.path,
                        "headers": {k.lower(): v for k, v in self.headers.items()},
                        "json": payload,
                    })
                    reply = provider._script.popleft() if provider._script else provider.default
                self._send(reply)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(
            target=self._server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True
        )

    def push(self, *replies: Reply) -> FakeProvider:
        with self._lock:
            self._script.extend(replies)
        return self

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_address[1]}/v1"

    def start(self) -> FakeProvider:
        self._thread.start()
        return self

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()

    def __enter__(self) -> FakeProvider:
        return self.start()

    def __exit__(self, *exc: object) -> None:
        self.stop()


class FakeLLMExhausted(AssertionError):
    """The test asked for more model calls than it scripted."""


class FakeLLM:
    """Plays a script of replies in order. Strings become responses; exceptions are raised."""

    def __init__(self, script: Iterable[LLMResponse | str | BaseException]):
        self._script: deque[LLMResponse | str | BaseException] = deque(script)
        self.calls: list[list[dict[str, str]]] = []

    def complete(
        self,
        messages: Sequence[Message],
        *,
        max_tokens: int,
        temperature: float = 0.0,
        stop: Sequence[str] | None = None,
        tools: Sequence[Mapping[str, Any]] | None = None,
    ) -> LLMResponse:
        self.calls.append([dict(m) for m in messages])
        if not self._script:
            raise FakeLLMExhausted(f"FakeLLM script exhausted after {len(self.calls) - 1} calls")
        item = self._script.popleft()
        if isinstance(item, BaseException):
            raise item
        if isinstance(item, str):
            prompt = sum(len(m.get("content", "")) for m in messages) // 4
            return LLMResponse(text=item, prompt_tokens=prompt, completion_tokens=len(item) // 4,
                               route_id="fake", model="fake")
        return item

    @property
    def remaining(self) -> int:
        return len(self._script)
