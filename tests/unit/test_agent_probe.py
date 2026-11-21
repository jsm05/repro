"""The live smoke's agent-prompt probe, against a FakeProvider (M5 empty-reply diagnosis)."""

from __future__ import annotations

from repro.llm.fake import FakeProvider, ok
from repro.llm.live import agent_probe, first_agent_prompt
from repro.llm.registry import Route
from tests.helpers import FIXTURES_ROOT
from tests.llm_helpers import TEST_KEY, route


def test_agent_probe_reports_each_variant() -> None:
    """M5: the probe sends the agent's first prompt three ways and records what came back."""
    empty = ok(text="", prompt_tokens=900, completion_tokens=8000, reasoning_tokens=8000)
    empty.body["choices"][0]["finish_reason"] = "length"
    action = 'Look first.\n```action\n{"tool": "bash", "args": {"cmd": "ls"}}\n```'
    msgs = first_agent_prompt(FIXTURES_ROOT)
    assert msgs[0]["role"] == "system" and "paginate" in msgs[1]["content"]
    r = Route.model_validate(route("p/m", "p", "m", max_output_tokens=8192, reasoning=True))
    with FakeProvider() as fp:
        fp.push(empty, ok(action), ok("no action here"))
        res = agent_probe(r, fp.base_url, TEST_KEY, msgs)
        sent = fp.requests
    assert [x.variant for x in res] == ["system role", "system merged into user",
                                        "reasoning_effort=low"]
    assert not res[0].ok and res[0].finish_reason == "length" and res[0].reasoning_tokens == 8000
    assert res[1].ok and res[1].detail == "has an action block"
    assert res[2].ok and res[2].detail == "no action block"
    assert sent[0]["json"]["messages"][0]["role"] == "system"
    assert sent[1]["json"]["messages"][0]["role"] == "user"
    assert sent[2]["json"].get("reasoning_effort") == "low"
