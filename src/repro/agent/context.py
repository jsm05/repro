"""Context manager: pinned items, the state card, and trimming under the prompt cap.

Pinned in every prompt: the system prompt, the issue text (first 3K and last 1K
tokens if it is over 4K), the current candidate diff, the latest gate verdict, and
the state card. History is trimmed in this order until the estimate fits the cap:

1. tool outputs are cut to 3K tokens (head and tail) when they enter history;
2. tool results older than the last 4 turns become one-line stubs;
3. file views older than the last 2 views become stubs;
4. the oldest assistant/tool turn pairs are dropped.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from repro.llm.tokens import TokenEstimator

ISSUE_PIN_OVER = 4_000
ISSUE_HEAD, ISSUE_TAIL = 3_000, 1_000
OUTPUT_CAP = 3_000
KEEP_RESULTS = 4
KEEP_VIEWS = 2
STATE_CARD_CAP = 400


def effective_prompt_cap(configured: int, route_windows: Sequence[int]) -> int:
    """Never above the smallest context window among the logical model's routes."""
    return min([configured, *route_windows]) if route_windows else configured


def head_tail(text: str, head_tokens: int, tail_tokens: int, est: TokenEstimator,
              label: str = "tokens") -> str:
    """Keep about head_tokens from the start and tail_tokens from the end."""
    total = est.count(text)
    if total <= head_tokens + tail_tokens:
        return text
    per_char = total / max(1, len(text))
    h = int(head_tokens / per_char)
    t = int(tail_tokens / per_char)
    elided = total - head_tokens - tail_tokens
    return f"{text[:h]}\n[... about {elided} {label} elided ...]\n{text[len(text) - t:]}"


def pin_issue(issue: str, est: TokenEstimator) -> str:
    if est.count(issue) <= ISSUE_PIN_OVER:
        return issue
    return head_tail(issue, ISSUE_HEAD, ISSUE_TAIL, est)


@dataclass
class Turn:
    assistant: str
    tool: str
    result: str  # already truncated on entry
    stub: str
    is_view: bool = False
    harness: bool = False  # a repair or nudge message, not a tool result


@dataclass
class Card:
    """What the agent has already tried, rebuilt by the harness every turn."""

    files_viewed: list[str] = field(default_factory=list)
    searches: list[str] = field(default_factory=list)
    tests_written: list[str] = field(default_factory=list)
    checks: list[str] = field(default_factory=list)
    budget: str = ""

    def render(self, est: TokenEstimator, cap: int = STATE_CARD_CAP) -> str:
        def dedupe(xs: list[str]) -> list[str]:
            seen: dict[str, None] = {}
            for x in xs:
                seen.pop(x, None)
                seen[x] = None
            return list(seen)

        views, searches = dedupe(self.files_viewed), dedupe(self.searches)
        checks = list(self.checks)
        while True:
            parts = ["STATE CARD (kept by the harness; old messages may have been trimmed)",
                     f"Budget left: {self.budget}",
                     "Tests written: " + (", ".join(dedupe(self.tests_written)) or "none"),
                     "Checks: " + ("; ".join(checks) or "none yet"),
                     "Files viewed: " + (", ".join(views) or "none"),
                     "Commands run: " + (" | ".join(searches) or "none")]
            text = "\n".join(parts)
            if est.count(text) <= cap:
                return text
            if searches:
                searches.pop(0)
            elif views:
                views.pop(0)
            elif len(checks) > 1:
                checks.pop(0)
            else:
                return text[: cap * 3]


@dataclass
class Built:
    messages: list[dict[str, str]]
    estimate: int
    trims: dict[str, int]


class Context:
    def __init__(self, system: str, issue: str, repo: str, est: TokenEstimator, cap: int):
        self.system = system
        self.issue = pin_issue(issue, est)
        self.repo = repo
        self.est = est
        self.cap = cap
        self.turns: list[Turn] = []

    def add(self, assistant: str, tool: str, result: str, stub: str, *, is_view: bool = False,
            harness: bool = False) -> None:
        text = head_tail(result, OUTPUT_CAP // 2, OUTPUT_CAP // 2, self.est)
        self.turns.append(Turn(assistant, tool, text, stub, is_view, harness))

    def _render(self, turns: list[tuple[Turn, bool]], card: str, diff: str,
                verdict: str) -> list[dict[str, str]]:
        msgs = [{"role": "system", "content": self.system},
                {"role": "user", "content": f"Repository: {self.repo}\n\nIssue:\n{self.issue}"}]
        for t, stubbed in turns:
            # Providers reject empty assistant messages (HTTP 400, D-M5-11).
            msgs.append({"role": "assistant", "content": t.assistant or "(empty reply)"})
            if t.harness:
                msgs.append({"role": "user", "content": t.result})
            else:
                body = t.stub if stubbed else t.result
                msgs.append({"role": "user", "content": f"Result of {t.tool}:\n{body}"})
        tail = [card, "Current candidate diff:\n" + (diff or "(no test files written yet)"),
                "Latest gate verdict:\n" + (verdict or "(no check run yet)"),
                "Reply with one action."]
        msgs.append({"role": "user", "content": "\n\n".join(tail)})
        return msgs

    def build(self, card: str, diff: str, verdict: str) -> Built:
        trims = {"stub_results": 0, "stub_views": 0, "dropped_turns": 0, "pinned_cut": 0}
        n = len(self.turns)
        stub = [False] * n
        start = 0

        def render() -> tuple[list[dict[str, str]], int]:
            m = self._render([(self.turns[i], stub[i]) for i in range(start, n)], card, diff,
                             verdict)
            return m, self.est.estimate(m)

        msgs, est = render()
        # Step 2: results older than the last KEEP_RESULTS turns become stubs, oldest first.
        for i in range(max(0, n - KEEP_RESULTS)):
            if est <= self.cap:
                break
            if not self.turns[i].harness and not stub[i]:
                stub[i] = True
                trims["stub_results"] += 1
                msgs, est = render()
        # Step 3: file views older than the last KEEP_VIEWS views become stubs.
        views = [i for i in range(n) if self.turns[i].is_view and not stub[i]]
        for i in views[: max(0, len(views) - KEEP_VIEWS)]:
            if est <= self.cap:
                break
            stub[i] = True
            trims["stub_views"] += 1
            msgs, est = render()
        # Step 4: drop the oldest turn pairs.
        while est > self.cap and start < n:
            start += 1
            trims["dropped_turns"] += 1
            msgs, est = render()
        # Last resort: the pinned diff and verdict themselves are too big; cut their middles.
        budget = 2000
        while est > self.cap and budget > 50:
            diff = head_tail(diff, budget, budget, self.est, "diff tokens")
            verdict = head_tail(verdict, budget // 2, budget // 2, self.est)
            trims["pinned_cut"] += 1
            budget //= 2
            msgs, est = render()
        return Built(msgs, est, {k: v for k, v in trims.items() if v})
