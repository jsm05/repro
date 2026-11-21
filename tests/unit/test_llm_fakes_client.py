"""Fakes, cassettes, the client and the error classifier (TEST_SPEC M3)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from repro.llm.cassette import CassetteLLM, CassetteMiss
from repro.llm.client import OpenAICompatClient
from repro.llm.errors import ErrorClass, LLMError, classify, retry_after_seconds
from repro.llm.fake import FakeLLM, FakeLLMExhausted, FakeProvider, error, ok
from repro.llm.registry import Route
from repro.llm.router import RoutedLLM
from tests.helpers import ROOT
from tests.llm_helpers import MSGS, TEST_KEY, registry, route, router

SAMPLES: list[dict[str, Any]] = json.loads((ROOT / "tests/fixtures/llm_errors.json").read_text())


def test_fake_llm_script() -> None:
    """T3.7: FakeLLM plays its script in order and raises when exhausted."""
    llm = FakeLLM(["first", "second", RuntimeError("boom")])
    assert llm.complete(MSGS, max_tokens=10).text == "first"
    assert llm.complete(MSGS, max_tokens=10).text == "second"
    with pytest.raises(RuntimeError, match="boom"):
        llm.complete(MSGS, max_tokens=10)
    with pytest.raises(FakeLLMExhausted):
        llm.complete(MSGS, max_tokens=10)
    assert len(llm.calls) == 4


def test_cassette_record_replay(tmp_path: Path) -> None:
    """T3.8: replay is identical (repeats in order); a changed prompt is a miss; no API key."""
    path = tmp_path / "cassette.jsonl"
    rec = CassetteLLM(path, "record", FakeLLM(["one", "two", "three"]))
    other = [{"role": "user", "content": "different"}]
    got = [rec.complete(MSGS, max_tokens=5), rec.complete(MSGS, max_tokens=5),
           rec.complete(other, max_tokens=5)]
    play = CassetteLLM(path, "replay")
    again = [play.complete(MSGS, max_tokens=5), play.complete(MSGS, max_tokens=5),
             play.complete(other, max_tokens=5)]
    assert [r.model_dump_json() for r in again] == [r.model_dump_json() for r in got]
    with pytest.raises(CassetteMiss):
        play.complete([{"role": "user", "content": "never recorded"}], max_tokens=5)
    with pytest.raises(CassetteMiss):
        play.complete(MSGS, max_tokens=5)  # third use of a request recorded twice

    with FakeProvider() as fp:
        reg = registry({"p": fp.base_url}, [route("p/m", "p", "m")], {"primary": ["p/m"]})
        live = CassetteLLM(tmp_path / "live.jsonl", "record",
                           RoutedLLM(router(reg, tmp_path), "primary"))
        live.complete(MSGS, max_tokens=5)
        assert fp.requests[0]["headers"]["authorization"] == f"Bearer {TEST_KEY}"
    assert TEST_KEY not in (tmp_path / "live.jsonl").read_text()


def _route(**kw: Any) -> Route:
    return Route.model_validate(route("p/m", "p", "m", **kw))


def test_client_request_shape() -> None:
    """T3.9: capability flags shape the request; usage parsed; SDK retries off."""
    with FakeProvider() as fp:
        fp.push(ok("hi", prompt_tokens=11, completion_tokens=7, reasoning_tokens=5))
        c = OpenAICompatClient(_route(system_role=False, stop_sequences=False), fp.base_url,
                               TEST_KEY)
        resp = c.complete([{"role": "system", "content": "SYS"},
                           {"role": "user", "content": "question"}],
                          max_tokens=99_999, stop=["END"])
        sent = fp.requests[0]["json"]
        assert [m["role"] for m in sent["messages"]] == ["user"]
        assert sent["messages"][0]["content"].startswith("SYS\n\nquestion")
        assert "stop" not in sent
        assert sent["max_tokens"] == 4096  # clamped to the route's max output
        assert (resp.prompt_tokens, resp.completion_tokens, resp.reasoning_tokens) == (11, 7, 5)
        assert c.max_retries == 0

        fp.push(ok())
        c2 = OpenAICompatClient(_route(reasoning_effort="low"), fp.base_url, TEST_KEY)
        c2.complete([{"role": "system", "content": "SYS"}, {"role": "user", "content": "q"}],
                    max_tokens=10, stop=["END"])
        sent2 = fp.requests[1]["json"]
        assert [m["role"] for m in sent2["messages"]] == ["system", "user"]
        assert sent2["stop"] == ["END"]
        assert sent2["reasoning_effort"] == "low"

        fp.push(error(500, {"error": {"message": "boom"}}))
        with pytest.raises(LLMError) as exc:
            c2.complete(MSGS, max_tokens=10)
        assert exc.value.cls is ErrorClass.TRANSIENT
        assert len(fp.requests) == 3  # one attempt only: the SDK did not retry


@pytest.mark.parametrize("sample", SAMPLES, ids=lambda s: f"{s['provider']}-{s['status']}-"
                                                           f"{s['expected']}")
def test_error_classifier(sample: dict[str, Any]) -> None:
    """T3.15: recorded error bodies from each provider map to the right class."""
    got = classify(sample["provider"], sample["status"], sample["body"])
    assert got.value == sample["expected"]
    if "retry_after" in sample:
        assert retry_after_seconds({}, sample["body"]) == pytest.approx(sample["retry_after"])


def test_empty_output_and_retry_after_header() -> None:
    """T3.15: a 200 with no content is EMPTY_OUTPUT; Retry-After headers are read."""
    with FakeProvider() as fp:
        fp.push(ok(text=""))
        with pytest.raises(LLMError) as exc:
            OpenAICompatClient(_route(), fp.base_url, TEST_KEY).complete(MSGS, max_tokens=5)
        assert exc.value.cls is ErrorClass.EMPTY_OUTPUT
        assert exc.value.response is not None and exc.value.response.text == ""
        assert exc.value.response.prompt_tokens == 10  # usage kept for accounting
    assert retry_after_seconds({"Retry-After": "7"}, "") == 7.0
