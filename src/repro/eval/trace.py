"""Trace recorder: events kept in memory and written with the row."""

from __future__ import annotations

from typing import Any

from repro.eval.rows import TraceEvent
from repro.llm.clock import Clock


class TraceRecorder:
    """Trace events kept in memory and written with the row, so nothing partial lands."""

    def __init__(self, run_id: str, instance_id: str, clock: Clock):
        self.run_id = run_id
        self.instance_id = instance_id
        self.clock = clock
        self.events: list[TraceEvent] = []

    def add(self, type: str, payload: dict[str, Any] | None = None, route: str = "",
            tokens: int = 0) -> None:
        self.events.append(TraceEvent(
            ts=self.clock.now(), run_id=self.run_id, instance_id=self.instance_id,
            seq=len(self.events), type=type, payload=payload or {}, route=route, tokens=tokens,
        ))

    def jsonl(self) -> str:
        return "".join(e.model_dump_json() + "\n" for e in self.events)
