"""Leak canary (EVAL section 6): scan prompts and traces for sealed content.

Markers per instance: lines the real fix adds (long enough to be distinctive and not
already in the repo at the base commit), the real tests' added function names, the
fail-to-pass test names, and a slice of the later issue comments. A hit means sealed
content reached something the model or the agent could see.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from repro.gate.scope import DiffParseError, FileDiff, parse_diff
from repro.models import SealedView

MIN_LINE = 20
_DEF = re.compile(r"^\s*def (test\w+)\s*\(")


@dataclass(frozen=True)
class Hit:
    where: str
    kind: str
    marker: str


def _files(diff: str) -> list[FileDiff]:
    try:
        return parse_diff(diff)
    except DiffParseError:
        return []


def markers(sealed: SealedView, repo_text: str = "") -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for f in _files(sealed.gold_fix_patch):
        for h in f.hunks:
            for line in h.lines:
                if line.startswith("+"):
                    code = line[1:].strip()
                    if len(code) >= MIN_LINE and code not in repo_text:
                        out.append(("fix_line", code))
    for f in _files(sealed.gold_test_patch):
        for h in f.hunks:
            for line in h.lines:
                m = _DEF.match(line[1:]) if line.startswith("+") else None
                if m and len(m.group(1)) >= 10 and m.group(1) not in repo_text:
                    out.append(("gold_test_name", m.group(1)))
    for tid in sealed.fail_to_pass:
        name = tid.split("::")[-1]
        if len(name) >= 10 and name not in repo_text:
            out.append(("fail_to_pass", name))
    hint = " ".join(sealed.hints_text.split())
    if len(hint) >= 60:
        out.append(("hints_text", hint[:60]))
    return sorted(set(out))


def scan(texts: Iterable[tuple[str, str]], marks: list[tuple[str, str]]) -> list[Hit]:
    hits = []
    for where, text in texts:
        for kind, marker in marks:
            if marker in text:
                hits.append(Hit(where, kind, marker))
    return hits


def _strings(obj: object) -> list[str]:
    if isinstance(obj, str):
        return [obj]
    if isinstance(obj, dict):
        return [s for v in obj.values() for s in _strings(v)]
    if isinstance(obj, list):
        return [s for v in obj for s in _strings(v)]
    return []


def decoded(text: str) -> str:
    """JSONL text with every string value unescaped, so quotes and backslashes in code
    match the markers."""
    out = []
    for line in text.splitlines():
        try:
            out += _strings(json.loads(line))
        except ValueError:
            out.append(line)
    return "\n".join(out)


def scan_run(run_dir: Path, sealed_by_id: dict[str, SealedView],
             cassette_dir: Path | None = None) -> list[Hit]:
    """Scan every trace in a run (and its cassettes, which hold the prompts)."""
    hits: list[Hit] = []
    for iid, sealed in sealed_by_id.items():
        marks = markers(sealed)
        files = [run_dir / "traces" / f"{iid}.jsonl"]
        if cassette_dir is not None:
            files.append(cassette_dir / f"{iid}.jsonl")
        texts = [(str(p), decoded(p.read_text(errors="replace"))) for p in files
                 if p.exists()]
        hits += scan(texts, marks)
    return hits
