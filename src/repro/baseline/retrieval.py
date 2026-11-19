"""BM25 over repo files: the context the single-prompt baselines get.

Deterministic: ties break by path. Identifiers are split on snake_case and
camelCase so `get_int` in an issue matches `def get_int` and `getInt`.
"""

from __future__ import annotations

import math
import os
import re
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

TEXT_SUFFIXES = {".py", ".pyi", ".md", ".rst", ".txt", ".cfg", ".toml", ".ini", ".yaml", ".yml"}
SKIP_DIRS = {".git", "__pycache__", ".tox", ".venv", "venv", "build", "dist", ".repro",
             "node_modules", ".mypy_cache", ".pytest_cache"}
MAX_FILE_BYTES = 400_000
_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|\d+")
_CAMEL = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+")


class RepoFiles(Protocol):
    def list_files(self) -> list[str]: ...

    def read(self, path: str) -> str: ...


class DirRepo:
    """Read-only view of a repo directory (the pristine checkout)."""

    def __init__(self, root: str | Path):
        self.root = Path(root)

    def list_files(self) -> list[str]:
        out = []
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS
                                 and not d.endswith(".egg-info"))
            for name in sorted(filenames):
                full = Path(dirpath) / name
                if full.suffix in TEXT_SUFFIXES and full.stat().st_size <= MAX_FILE_BYTES:
                    out.append(full.relative_to(self.root).as_posix())
        return out

    def read(self, path: str) -> str:
        return (self.root / path).read_text(encoding="utf-8", errors="replace")

    def exists(self, path: str) -> bool:
        return (self.root / path).is_file()


def tokenize(text: str) -> list[str]:
    out: list[str] = []
    for word in _WORD.findall(text):
        low = word.lower()
        out.append(low)
        parts = [p.lower() for chunk in word.split("_") for p in _CAMEL.findall(chunk)]
        if len(parts) > 1:
            out.extend(p for p in parts if len(p) > 1)
    return out


def bm25_rank(
    query: str, repo: RepoFiles, k1: float = 1.5, b: float = 0.75
) -> list[tuple[str, float]]:
    paths = repo.list_files()
    docs = {p: tokenize(p.replace("/", " ") + " " + repo.read(p)) for p in paths}
    if not docs:
        return []
    avgdl = sum(len(d) for d in docs.values()) / len(docs)
    df: Counter[str] = Counter()
    for d in docs.values():
        df.update(set(d))
    n = len(docs)
    q_terms = set(tokenize(query))
    scores = []
    for path, words in docs.items():
        tf = Counter(words)
        dl = len(words) or 1
        score = 0.0
        for term in q_terms:
            if term not in tf:
                continue
            idf = math.log((n - df[term] + 0.5) / (df[term] + 0.5) + 1)
            f = tf[term]
            score += idf * f * (k1 + 1) / (f + k1 * (1 - b + b * dl / avgdl))
        scores.append((path, score))
    scores.sort(key=lambda item: (-item[1], item[0]))
    return scores


def select_context(
    query: str,
    repo: RepoFiles,
    token_budget: int,
    count_tokens: Callable[[str], int],
    min_chunk_tokens: int = 200,
) -> list[tuple[str, str]]:
    """Top-ranked files, whole while they fit, then one truncated file if room remains."""
    chosen: list[tuple[str, str]] = []
    used = 0
    for path, score in bm25_rank(query, repo):
        if score <= 0:
            break
        text = repo.read(path)
        cost = count_tokens(text) + 20
        if used + cost <= token_budget:
            chosen.append((path, text))
            used += cost
            continue
        room = token_budget - used - 20
        if room >= min_chunk_tokens:
            ratio = room / max(cost, 1)
            cut = text[: int(len(text) * ratio * 0.95)]
            while cut and count_tokens(cut) > room:
                cut = cut[: int(len(cut) * 0.9)]
            if cut:
                chosen.append((path, cut + "\n# ... [truncated]\n"))
        break
    return chosen
