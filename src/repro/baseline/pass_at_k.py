"""Baseline B1: pass@k with the online gate.

Separates "the gate helps" from "the agent loop helps": same prompt as B0, k
independent samples, and the first one that passes the gate is emitted.
"""

from __future__ import annotations

from collections.abc import Callable

from repro.baseline.retrieval import RepoFiles
from repro.baseline.single_prompt import PROMPT_CAP, BaselineResult, build_messages, parse_candidate
from repro.llm.tokens import TokenEstimator
from repro.llm.types import LLM
from repro.models import AgentView, Candidate


def run_b1(
    view: AgentView,
    repo: RepoFiles,
    llm: LLM,
    gate: Callable[[Candidate], bool],
    *,
    estimator: TokenEstimator,
    k: int = 5,
    temperature: float = 0.8,
    prompt_cap: int = PROMPT_CAP,
    max_tokens: int = 8000,
) -> BaselineResult:
    """k independent samples; emit the first that passes the online gate.

    Gated emits nothing if none pass; always-emit then falls back to sample 1.
    All k samples are drawn (pass@k), so tokens are the sum over all of them.
    """
    messages = build_messages(view, repo, estimator, prompt_cap)
    responses = [llm.complete(messages, max_tokens=max_tokens, temperature=temperature)
                 for _ in range(k)]
    samples = [parse_candidate(r.text, repo, f"b1-{i + 1}") for i, r in enumerate(responses)]
    gated = next((c for c in samples if c is not None and gate(c)), None)
    always = gated if gated is not None else samples[0]
    return BaselineResult("B1", gated, always, responses, samples)
