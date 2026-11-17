# Decisions log

Deviations from DESIGN.md and choices the plan left open. Newest last. Format: date, milestone, decision, why.

## 2026-11-17, M1

**D-M1-1. The runner reads a JSON report from an injected pytest plugin, not JUnit XML.**
DESIGN 5.3 said JUnit XML. Checked against pytest 9: JUnit XML cannot tell XPASS from PASSED, and with `--tb=native` the traceback is text that includes pytest's own frames. The plugin (`src/repro/runner/repro_plugin.py`) writes one JSON line per test report with phase, outcome, the xfail flag and a structured traceback (`traceback.extract_tb`). It is stdlib plus pytest, Python 3.6 syntax, so it can run inside old benchmark containers. Known limit: code under test runs in the same process and could forge records. The plugin drops the report path from the environment and holds the file open, which stops casual tampering only. The oracle stays the final word. Non-pytest repos still use the swebench log parsers in M6, as planned.

**D-M1-2. `TestStatus` has a seventh value, `TIMEOUT`, from M1.**
A run that hits its time limit marks every unfinished requested test `TIMEOUT`, so a hang can never show up as `FAILED` or `ERROR`. M2's T2.14 relies on this.

**D-M1-3. pytest runs with `--continue-on-collection-errors`, and a requested test that never ran is `ERROR`.**
Without the flag, one broken file stops the whole session and hides the other results. A requested node id with no report is `ERROR` ("not collected"), never `FAILED`.

**D-M1-4. Failure frames keep only repo frames, with repo-relative paths.**
Frames from site-packages and pytest internals are dropped. This keeps signatures stable across sandboxes (temp paths differ) and gives G3 exactly what it needs: whether the traceback passes through repo source, and which repo frame is innermost.

**D-M1-5. Sandbox protocol additions.**
`workdir` and `python` properties (the runner needs both) and `delete_file` (to clean up report files). Scratch files live in `.repro/` inside the workdir; the tree hash ignores that directory.

**D-M1-6. LocalSandbox memory limit is `RLIMIT_AS`.**
A resource failure is flagged when stderr contains `MemoryError` or the process died from SIGKILL without a timeout. A `MemoryError` inside a test becomes `ERROR` with note `resource: memory`, never `FAILED`.

**D-M1-7. The test-path rule lives in `sandbox/treehash.py`.**
A path is a test path if any directory is named `tests`, `test` or `testing`, or the file is `test_*.py`, `*_test.py` or `conftest.py`. The tree hash and G0 (M2) share this one function. The file is stdlib-only, Python 3.6 syntax, for use inside containers in M6.

**D-M1-8. bootstrap.yml details.**
SWT-bench harness pinned to commit `be12d4c8e7a5315a6bd60b0cad72736e96a4af3e`. The gold check uses the README's own command (no `--dataset_name`). Verified ids come from `nmuendler/SWT-Bench_Verified_bm25_27k_zsp` (largest split; split names and columns recorded in box notes). The workflow commits its outputs as `github-actions[bot]`, since it is an automated commit, not one I wrote.
