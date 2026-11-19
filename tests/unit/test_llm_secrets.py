"""Keys never reach reports, errors or logs, even when a provider or library echoes them.

Regression tests for the 2026-11-20 leak: a key saved with a trailing newline made
urllib reject the Authorization header, its error message contained the header, and
the smoke report wrote that message to a committed file."""

from __future__ import annotations

from pathlib import Path

import pytest

from repro.llm import live
from repro.llm.budget import Ledger
from repro.llm.client import OpenAICompatClient, make_client_factory
from repro.llm.errors import LLMError
from repro.llm.fake import FakeProvider, error
from repro.llm.registry import Route
from repro.llm.secrets import known_secrets, redact
from tests.llm_helpers import MSGS, TEST_KEY, registry, route


def test_trailing_newline_is_stripped() -> None:
    """T3.14: a key pasted with a trailing newline is sent without it."""
    with FakeProvider() as fp:
        reg = registry({"p": fp.base_url}, [route("p/m", "p", "m")], {"primary": ["p/m"]})
        client = make_client_factory(reg, {"P_KEY": TEST_KEY + "\n"})(reg.routes["p/m"])
        client.complete(MSGS, max_tokens=5)
        assert fp.requests[0]["headers"]["authorization"] == f"Bearer {TEST_KEY}"


def test_client_errors_redact_echoed_key() -> None:
    """T3.14: a provider error body that echoes the key is redacted in the LLMError."""
    with FakeProvider(default=error(401, {"error": f"Invalid API key: {TEST_KEY}"})) as fp:
        client = OpenAICompatClient(Route.model_validate(route("p/m", "p", "m")), fp.base_url,
                                    TEST_KEY)
        with pytest.raises(LLMError) as exc:
            client.complete(MSGS, max_tokens=5)
    assert TEST_KEY not in str(exc.value)
    assert "[REDACTED]" in str(exc.value)


def test_smoke_report_never_contains_keys(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """T3.14: model-list and route errors that echo a key never reach the smoke report."""
    other = "sk-or-v1-0123456789abcdef0123456789abcdef"

    def leaky_list_models(base_url: str, api_key: str, timeout_s: float = 60.0) -> list[str]:
        raise ValueError(f"Invalid header value b'Bearer {api_key}\\n'")

    monkeypatch.setattr(live, "list_models", leaky_list_models)
    with FakeProvider(default=error(401, {"error": f"bad key {TEST_KEY}"})) as fp, \
            FakeProvider() as fp2:
        reg = registry({"p": fp.base_url, "q": fp2.base_url},
                       [route("p/m", "p", "m"), route("q/m", "q", "n")],
                       {"primary": ["p/m"], "other": ["q/m"]})
        env = {"P_KEY": TEST_KEY + "\n", "Q_KEY": other}
        report = live.smoke(reg, Ledger(tmp_path / "ledger.jsonl"), env)
        text = live.write_report(tmp_path / "smoke.md", live.smoke_markdown(report, "now"),
                                 known_secrets(reg, env))
    written = (tmp_path / "smoke.md").read_text()
    for blob in (text, written, str(report)):
        assert TEST_KEY not in blob
        assert other not in blob
    assert "[REDACTED]" in written


def test_redact_handles_raw_stripped_and_escaped_forms() -> None:
    """T3.14: redaction covers the raw, stripped and bytes-escaped forms of a key."""
    key = TEST_KEY + "\n"
    text = f"a {TEST_KEY} b {repr(key.encode())} c"
    out = redact(text, [key])
    assert TEST_KEY not in out
