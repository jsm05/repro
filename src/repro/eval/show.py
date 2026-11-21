"""`repro show`: a readable timeline of one unit's trace (used in the demo)."""

from __future__ import annotations

from repro.eval.rows import TraceEvent


def _detail(e: TraceEvent) -> str:
    p = e.payload
    if e.type == "llm_call":
        return (f"{p.get('model', '')} in {p.get('prompt_tokens', 0)} out "
                f"{p.get('completion_tokens', 0)} (reasoning {p.get('reasoning_tokens', 0)}), "
                f"{p.get('latency_s', 0):.1f} s, retries {p.get('retries', 0)}")
    if e.type == "gate_check":
        verdict = "PASS" if p.get("passed") else f"FAIL at {p.get('failed_check')}"
        return f"{p.get('candidate', '')}: {verdict}: {p.get('reason', '')}"
    if e.type == "oracle":
        trans = ", ".join(f"{label} {t}" for t, label in sorted(p.get("transitions", {}).items()))
        return f"{p.get('outcome')}: {p.get('reason', '')}" + (f" [{trans}]" if trans else "")
    if e.type == "stop":
        return str(p.get("reason", ""))
    return ", ".join(f"{k}={v}" for k, v in sorted(p.items()))


def render_trace(events: list[TraceEvent]) -> str:
    header = f"{'#':>3}  {'event':<11} {'route':<26} {'tokens':>8} {'wait s':>7}  detail"
    lines = [header, "-" * len(header)]
    total = 0
    waited = 0.0
    for e in events:
        wait = float(e.payload.get("rate_limit_wait_s", 0.0))
        total += e.tokens
        waited += wait
        tokens = f"{e.tokens:,}" if e.tokens else ""
        wait_s = f"{wait:.1f}" if e.type == "llm_call" else ""
        lines.append(f"{e.seq:>3}  {e.type:<11} {e.route:<26} {tokens:>8} {wait_s:>7}  "
                     f"{_detail(e)}")
    lines.append("-" * len(header))
    lines.append(f"total tokens {total:,}, rate-limit wait {waited:.1f} s")
    return "\n".join(lines)
