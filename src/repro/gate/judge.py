"""Optional LLM judge for G3 (DESIGN 5.3, EVAL section 3: the `A-main+judge` ablation).

Runs only after G3's rules pass, once per failing test, so it can veto a failure but
never rescue one. It sees AgentView content only: the issue text, the candidate's own
diff, and the failure the gate just observed. No fix, no gold tests, no hints. Its calls
go through the unit's metered LLM, so they land in the ledger and count against the
unit's budget like any other call. Off unless the config sets `judge: true`.

If the reply has no verdict line the judge passes the test (fail open), so a confused
judge can't turn into a hidden extra filter; the trace records that it happened.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from repro.llm.types import LLM
from repro.models import Candidate, FailureDetail

JUDGE_VERSION = "j1"
ISSUE_CAP = 6_000
DIFF_CAP = 8_000
FAILURE_CAP = 4_000

SYSTEM = (
    "You review one failing test that was written to reproduce a bug report. Decide "
    "whether the test fails because of the bug the issue describes (yes), or for some "
    "other reason: the test itself is broken, it checks different behaviour, or it fails "
    "on setup or an unrelated error (no). Reply with exactly one line `VERDICT: yes` or "
    "`VERDICT: no`, followed by one short sentence saying why."
)

_VERDICT = re.compile(r"verdict\s*:\s*(yes|no)\b[ \t]*(.*)", re.IGNORECASE)


def _cap(text: str, n: int) -> str:
    return text if len(text) <= n else text[:n] + "\n... (cut)"


def parse_verdict(text: str) -> tuple[bool, str]:
    m = _VERDICT.search(text)
    if not m:
        return True, "judge gave no verdict; passed (fail open)"
    why = m.group(2).strip()
    if not why:
        rest = text[m.end():].strip().splitlines()
        why = rest[0].strip() if rest else ""
    return m.group(1).lower() == "yes", why


def judge_messages(issue: str, candidate: Candidate, test_id: str,
                   failure: FailureDetail) -> list[dict[str, str]]:
    shown = f"{failure.exc_type}: {failure.message}".strip(": ")
    user = (
        f"## Issue\n{_cap(issue, ISSUE_CAP)}\n\n"
        f"## The candidate test (diff)\n{_cap(candidate.diff, DIFF_CAP)}\n\n"
        f"## Failing test\n{test_id}\n\n"
        f"## Failure on the buggy code\n{_cap(shown, 500)}\n"
        f"{_cap(failure.text, FAILURE_CAP)}"
    )
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]


@dataclass
class Judge:
    llm: LLM
    issue: str
    max_tokens: int = 400
    on_call: Callable[[dict[str, object]], None] | None = None  # e.g. a trace event

    def __call__(self, candidate: Candidate, test_id: str,
                 failure: FailureDetail) -> tuple[bool, str]:
        resp = self.llm.complete(judge_messages(self.issue, candidate, test_id, failure),
                                 max_tokens=self.max_tokens, temperature=0.0)
        ok, why = parse_verdict(resp.text)
        if self.on_call is not None:
            self.on_call({"test_id": test_id, "verdict": "yes" if ok else "no",
                          "why": why[:300], "version": JUDGE_VERSION})
        return ok, why
