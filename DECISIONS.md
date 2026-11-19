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

## 2026-11-17, M2

**D-M2-1. Assertion failures are signed by where they failed, not by their message.**
DESIGN 5.4 defines the signature as (type, masked message, innermost repo frame). For an `AssertionError` (or pytest's `Failed`) the signature is now (type, file:line of the failing assertion). Why: pytest prints the actual values, and for sets and dicts their order legitimately changes with the hash seed. The f04 gold test failed at the same line under seeds 0, 1 and 2, but with three different messages, so G4 rejected a correct reproduction. Other exceptions keep (phase, type, masked message, innermost frame path:function).

**D-M2-2. How "additions only" is enforced.**
G0 reads the pristine file. In an existing test file, every hunk must contain only context lines followed by added lines, and must end at the old end of file. One exception: a file without a final newline, where git rewrites the last line. Every candidate test id must name a `def` that the diff itself adds.

**D-M2-3. G1 also rejects tests that were skipped, xfailed, xpassed or errored in setup.**
They "did not run cleanly". G2 rejects if any candidate test times out, even when another one fails.

**D-M2-4. G3 and G5 apply to every failing candidate test, not just one.**
A candidate with one real reproduction and one broken test would fail O1 anyway. Rejecting it at the gate gives the agent feedback earlier.

**D-M2-5. Gate run layout.**
Three fresh sandboxes with seeds 0, 1, 2. Run 0 doubles as the G1 to G3 run, and all three runs happen once G3 passes, so the gate uses three sandboxes per candidate. G5 is judged after G4, so a seed-dependent test is reported as G4. The tracer records the first call into any function in a non-test repo file during the call phase. Module-level code and pseudo-files like `<frozen os>` do not count. If the tracer produced no data, G5 fails closed.

**D-M2-6. "Imports nothing outside the stdlib" (T2.16) means stdlib plus pytest.**
The tracer is a pytest plugin, and pytest is always present where it runs.

**D-M2-7. Oracle details.**
One "before" run on the buggy code with the candidate (seed 0), then three "after" runs in fresh sandboxes with the fix (seeds 0, 1, 2), each over the whole files the candidate touches. A transition shows `~` when the after-runs disagree, and that test goes on the flaky list. E→P is not a reproduction. Collateral (O2) means any other test in a touched file that is FAILED, ERROR or TIMEOUT in any after-run.

**D-M2-8. Cheat storage and C15.**
Each cheat is stored as `cheats/<ID>.patch` plus `cheats/<ID>.json` (description, expected check, test ids) inside its fixture. C15 swaps the function under test for a fake that raises `AssertionError`. G3 accepts that (it looks like a test assertion), and the G5 tracer catches it because no repo function ran, matching the check TEST_SPEC names.

## 2026-11-19, E0 (setup results and the provider change)

**D-E0-1. The SWT-bench harness builds its own images on a free runner.**
`bootstrap.yml` (run 44538908817) resolved the gold instance `sympy__sympy-20590` in 440 s, most of it image builds. One instance's images take about 10.6 GB (base 2.2, env 4.0, eval 4.4), and the runner has 37 GB free after cleanup. So the fallback in RISKS R12 (prebuilt SWE-bench images from Docker Hub) is not needed, and the Docker Hub secrets are optional. All 433 Verified ids are in `eval/samples/verified_ids.txt`.

**D-E0-2. Runner facts differ from the plan's assumptions.**
The private-repo runner has 2 CPUs and 7.7 GB RAM, not the 4 CPUs and 16 GB the plan assumed (those are public-repo runners). Disk is better than assumed: 72 GB total, 37 GB free after removing preinstalled tools, versus about 14 GB in the plan. Making the repo public before M6 stays the default.

**D-E0-3. Primary model: `mistralai/mistral-large-4-1t-a52b` on NVIDIA's free endpoint, replacing Mistral's own free tier.**
Why: on 2026-11-19, Mistral's console asked for prepaid credits to enable API keys, so its free API plan is no longer usable at $0. Also ruled out: Cerebras (needs a card), Groq (8K tokens/min and 200K tokens/day, below one agent prompt), GitHub Models (8K input tokens per request). NVIDIA's API catalog lists free endpoints with no card (phone verification only). Two probe scripts run on my laptop on 2026-11-19 (78 requests in total):
- 32 models visible to the key.
- `moonshotai/kimi-k3.1-pro`: 174 s for a tiny request, timeouts on large prompts, and a 50-request burst got 29 timeouts and 21 HTTP 429s. Overloaded.
- `z-ai/glm-5.3-flash`: timed out.
- `mistralai/mistral-large-4-1t-a52b`: 23 of 23 requests succeeded. A 9K-token prompt took 16 s. Ten agent-sized calls took 22 s median, 100 s worst. Three parallel calls had no 429s, worst 144 s. It is a reasoning model: it used 299 of a 300-token output limit on reasoning.
- No rate-limit headers and no credit counter seen.

What changes in the plan: the provider table and chain order (DESIGN 5.5); output cap per call from 2K to 8K tokens unless reasoning effort can be lowered (decided in M3); model response time no longer counts against the 25-minute active-time budget, and the hard cap per bug rises from 60 to 120 minutes; runner-minutes from about 11,000 to about 13,500; a one-night soak test in M3 (T3.L2) before any eval run; new risks R31 (endpoint slow, overloaded or withdrawn) and R32 (reasoning tokens eat the output budget), with the positioning risks renumbered R33 to R37. Preference list if the primary must be replaced: `nvidia/nemotron-3-ultra-550b-a55b`, `poolside/laguna-xs-2.1`, Gemini Flash-Lite.

**D-E0-4. Secrets in the repo.**
`NVIDIA_API_KEY`, `GEMINI_API_KEY` and `OPENROUTER_API_KEY` added in repo settings. No `MISTRAL_API_KEY`. Docker Hub secrets optional (D-E0-1).

## 2026-11-20, M3

**D-M3-1. The rate limiter uses sliding windows, not token buckets.**
A bucket of capacity C that refills C per minute lets up to 2C through in one 60-second window: a full bucket plus a minute of refill. Six shards each holding 1/6 of a limit could then send twice the limit together. A sliding window bounds every window exactly, which the static shard split (T3.21) needs.

**D-M3-2. Token estimates fall back to one token per 3 characters when tiktoken's encoding cannot be downloaded.**
The chat workspace cannot reach tiktoken's download host. CI and Actions can. The fallback overestimates code, so ceilings stay safe. The per-route correction factor then pulls estimates toward reported usage either way. Tests inject the counter, so the hermetic suite never needs the download.

**D-M3-3. What is verified in `config/routes.yaml` and what is not.**
Verified: the primary's model id and behaviour (two probes, D-E0-3). Not verified yet: the Gemini Flash-Lite model id and limits (third-party reports, waiting for AI Studio numbers); the NVIDIA backups (listed by the key, never called); context windows (131072 assumed, prompts are capped at 32K anyway). OpenRouter is disabled until a current `:free` model is picked from the smoke report. NVIDIA routes are marked `trains_on_data: true` because the free endpoint's terms are unclear, so `private_code` refuses them. NVIDIA's cap of about 40 requests/min is per account, but the limiter counts per route; that is fine while one NVIDIA route serves at a time, which `pinned` guarantees.

**D-M3-4. Error samples are reconstructed, not captured.**
`tests/fixtures/llm_errors.json` follows each provider's documented error format; none are live captures. Added from writing them: Gemini reports a bad API key as HTTP 400, so a 400 mentioning an invalid key is classed as auth, not a bad request.

**D-M3-5. Retry and circuit-breaker details.**
A 5xx or timeout gets up to 5 retries with waits of about 1, 2, 4, 8 and 16 s (plus or minus 20% jitter), so 6 attempts, then failover. "Three in a row" means three calls in a row that used up those retries on the same route; the breaker then skips the route for 10 minutes, after which it is tried again. A per-minute 429 waits for Retry-After (or the body's "retry in Ns") up to 60 s; longer waits are treated like a short quota reset.

**D-M3-6. Reasoning effort is a route setting; the choice waits for live data.**
The client sends `reasoning_effort` when a route sets it. `repro llm smoke` calls every reasoning route twice, at the default and at `low`, and reports both reasoning-token counts. The primary keeps an 8K output cap until the smoke and soak reports show whether `low` is accepted and helps (RISKS R32).

**D-M3-7. Baseline details.**
B0 asks for exactly one fenced block with `path=` and `mode=new|append`; anything else is NO_TEST. Answers that point outside test paths or at source files are rejected before the gate. B1 lives in `baseline/pass_at_k.py` (the layout's name), draws all k samples first, then gates them in order and stops at the first pass.

**D-M3-8. Live workflows are started by me.**
This chat cannot start workflows: the dispatch API returns 403 through its proxy. `live-smoke.yml` and `soak.yml` are started from the Actions tab. They commit their reports as `github-actions[bot]`, like `bootstrap.yml`.

**D-M3-9. The router tracks waiting separately from working.**
`rate_limit_wait_s` (limiter and 429 waits) and `model_wait_s` (model response time) are kept per router, so M5's 25-minute active-time budget can leave both out (D-E0-3).

**D-M3-10. Incident: the first live smoke report leaked two API keys (2026-11-20).**
What happened: the Gemini and OpenRouter secrets had been saved with a trailing newline. urllib rejected the Authorization header, and its error message contained the whole header. `repro llm smoke` wrote error messages into `eval/live-smoke.md` unscrubbed, and the workflow committed it (commit `1f1d24d`). T3.14 had only checked logs, the ledger, quota files and cassettes, not the live reports. Response: the report was redacted in the next commit; both keys were revoked and replaced; the commit stays in history because this project does not rewrite history (the keys in it are dead). Fix: keys are read through one function that strips whitespace; every report, CLI error line and client error message is passed through `redact()` with every configured key (raw, stripped and bytes-escaped forms); regression tests in `tests/unit/test_llm_secrets.py`. Lesson: anything that writes provider or library error text to a committed file must redact first, and secret-scanning tests must cover every artefact a workflow commits.
