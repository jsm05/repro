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

**D-M3-11. Incident: the first soak run was cut off and lost all its results (2026-11-20).**
What happened: run 44733703044 was set to 500 requests over 5.5 h. The loop waited a fixed gap (5.5 h / 500 = 40 s) after each call, but calls longer than the gap simply pushed the schedule later, so the run was still going when the job hit its 355-minute limit. The report was only written at the end, so nothing was committed. T3.L2 stays open. Fix: the loop now stops starting calls at the time limit (the last one may finish up to one call timeout later), the report is rewritten after every request with a status line and an hourly table, the Soak step has its own 330-minute timeout so the commit step always runs, and the default limit is 5 h. Tests: T3.24 with a fake slow client on the fake clock. Lesson: any long job must checkpoint as it goes and be bounded by wall-clock time, not by a count that assumes how fast each step is.

**D-M3-12. Live results close M3: no hidden daily cap on the primary; latency tail is heavier than planned (2026-11-21).**
Live smoke (run 44711595086, after the key fix): all four enabled routes answered (Mistral Large 4, Nemotron 3 Ultra, Laguna XS 2.1, Gemini `gemini-3.5-flash-lite`) and every ledger count matched the provider's usage (T3.L1). The Gemini model id is now verified; its rate limits are still unverified. Soak (run 44795770595, one request at a time, 5 h limit): 275 of 275 requests succeeded, 0 429s, 0 timeouts, 0 replies cut off, mean 412 reasoning tokens per reply (T3.L2). Latency p50 35 s, p95 155 s, max 300 s (one call ran right up to the client timeout); hourly p50 ranged from 26 s to 69 s. The run averaged one request per 65 s including the planned gaps, which puts mean latency at roughly 55 s, above the 45 s the capacity plan assumes. Decisions: keep NVIDIA Mistral Large 4 as the pinned primary; keep the 300 s client timeout; leave `reasoning_effort` unset (the smoke showed no saving); keep the capacity plan for now and re-measure under real agent traffic and parallel shards in M5, since the soak never sent concurrent requests and so did not test the reported 40 RPM limit.

## 2026-11-21, M4

**D-M4-1. The result row stores both scoring modes and a few fields DESIGN 6 did not list.**
`outcome` (always-emit) and `outcome_gated` replace the single `terminal_state`, so one row gives both modes with no recomputation. Added: `gate_passed`, `route_calls` (calls per route, for the route mix), `candidate_tokens_at` (cumulative tokens when the emitted candidate first existed, for the ceiling curve), `gate_rejections` per check, `retries`, `tokens_reasoning`, `kind`. `wall_clock_s` and `git_commit` are stored but never printed in the report, so the report stays byte-stable.

**D-M4-2. Eval prompts count tokens with a fixed chars/3 estimator.**
The BM25 context budget depends on the token estimator, and tiktoken is available on some machines and not others (it could not download in the chat workspace). A different estimator gives a different prompt and therefore a different cassette key. `eval_estimator()` always uses chars/3, which is conservative, so prompts are identical everywhere and cassettes replay on any machine.

**D-M4-3. Shards are numbered from 1 everywhere, assigned by SHA-256 of the instance id.**
`--shard 2/6` writes `shard-2.jsonl`, `deferrals-2.json`, `ledger-2.jsonl`. The hash is pinned by a test (`django__django-11099` is in shard 4 of 6, index 3), because changing it mid-run would reshuffle units between shards.

**D-M4-4. What a fresh Actions job needs to resume is on the results branch; locks are not.**
`config.yaml`, rows, deferral counts, the ledger and quota files are committed with each unit (or each deferral), so a new job on a new runner resumes exactly. Lock files live outside the committed tree (`.git/repro-locks/` or `<results>/.locks/`); a lock whose process is gone is taken over. Across machines the workflow `concurrency` group does the locking (DESIGN 5.9). A run refuses to continue with a config whose hash differs from its frozen `config.yaml`.

**D-M4-5. Units run in a worker thread; only the main thread writes rows.**
On SIGTERM or SIGINT the main thread stops starting units and waits up to 60 s. If the unit is still running it is dropped: the main thread never writes its row, so a late finish cannot sneak in a partial result. Traces are buffered in memory and written with the row. Ledger entries are still written as calls happen, because they record spend even for dropped units.

**D-M4-6. The token ceiling curve credits only the emitted candidate.**
At ceiling c, a reproduced instance counts if its emitted candidate existed before cumulative tokens reached c. An earlier candidate that might also have reproduced is not credited, because the oracle only ran on the emitted one. The curve is therefore a lower bound at low ceilings.

**D-M4-7. Plots are hand-written SVG, not matplotlib.**
matplotlib output embeds version and font details that change across machines, which would break the byte-identical report check (T4.9). The ceiling chart is a small deterministic SVG.

**D-M4-8. B0 has no gate, so its gated mode equals always-emit; B1 needs one oracle run per unit.**
In B1 the gated candidate is either the always-emit candidate or nothing, so one oracle run covers both modes. The report says B0's gate numbers are not a gate measurement.

**D-M4-9. Fixture cassettes are scripted, not recorded from a model.**
`scripts/record_fixture_cassettes.py` writes, for each fixture, a fixed list of answers (gold test, a test that passes everywhere, one that fails everywhere, a refusal) under the exact request the baseline makes. One cassette file per instance, so resume order never matters. The logical model is named `fake-recorded` so no one mistakes these numbers for model results. `eval/reports/fixtures.md` is generated from them and the golden test (T4.11) checks it byte for byte.

**D-M4-10. Not in M4: the SWT-bench dataset (M6) and the agent executor (M5).**
`repro eval` refuses `dataset: swtbench` and `kind: agent` with a clear message. The live model path (router, quota, ledger, `--stop-when-exhausted`) is wired and type-checked but only exercised with fakes until M6. The hermetic suite now takes about 2 min 15 s; the golden end-to-end test is about 28 s of that.

## 2026-11-21, M5

**D-M5-1. Agent cassettes replay in recorded order, not by request hash.**
The agent's prompts contain tool output (pytest timings, temporary sandbox paths) that differs from run to run, so a request-hash key would never match on replay. With `cassette_match: order` the replies come back in sequence and the actions in them are executed for real each time. Baselines keep request-hash matching. A recorded unit's cassette is restarted at the start of each attempt, so a retried unit holds only its last attempt.

**D-M5-2. Parse failures: the first gets a repair message, the second in a row counts as a tool error.**
DESIGN 5.5 says a parse failure counts as a tool error; TEST_SPEC T5.22 says only the second consecutive one does. I followed T5.22: one malformed reply should cost a repair message, not a strike. Two action blocks in one reply are a parse error.

**D-M5-3. LocalSandbox blocks HTTP through dead proxy settings; real isolation comes with Docker.**
LocalSandbox has no network namespace. The bash tool sets every proxy variable to a closed local port, so curl, pip and urllib fail (T5.17). Code that opens raw sockets could still get out, which is acceptable only because LocalSandbox runs our own fixtures. Benchmark instances run in DockerSandbox with the network off (M6).

**D-M5-4. The candidate is the files written with `write_test`, read back from the workspace.**
Only paths the agent wrote with `write_test` go into the diff; their current contents are read from Sandbox A, so a later bash edit to the same file is included and anything else the agent changed is ignored. `conftest.py` and non-test paths are refused, and `mode: new` on a file that already exists in the repo is refused (use append).

**D-M5-5. Best candidate and the two modes.**
Last gate-passing candidate beats the last checked one, which beats the current unchecked one. Gated emits the last passing candidate even if the agent never submitted (T5.9). `submit` is accepted only when the current files are exactly the ones that last passed the gate.

**D-M5-6. Repeats and tool errors count consecutive events.**
Identical action (tool plus arguments) three times in a row: the nudge is appended to the third result, and a fourth identical action ends the run as `BUDGET_EXHAUSTED(repeated_action)`. Tool errors reset on any successful tool call. Rows record the stop as `STATE(kind)`, e.g. `BUDGET_EXHAUSTED(steps)`.

**D-M5-7. Trimming is recomputed from the full history on every call and goes only as far as needed.**
Each prompt is rebuilt from all turns (outputs already cut to 3K tokens on entry), then steps 2 to 4 are applied in order until the estimate fits. Nothing is deleted from memory, so a later bigger cap would show more. If even the pinned items do not fit, the middle of the candidate diff and verdict is cut as a last resort. Every call that needed trimming gets a `trim` trace event with counts per step.

**D-M5-8. Active time excludes rate-limit waits and model response time; tool time counts.**
Active time = elapsed - router waits - sum of `latency_s` of the replies. Sandbox and gate runs count as active, because they are the agent's own work. The hard cap uses elapsed time.

**D-M5-9. Live fixture run: A-chain, 20 steps, keys redacted before commit.**
`live-smoke.yml` with `mode: fixtures` runs `eval/configs/fixtures-a-chain.yaml` (chain policy, dev only) on all 8 fixtures with `llm: record`, limited to 20 steps per fixture to keep the job under 3 hours. It commits the cassettes, the report, the rows and the traces under `eval/reports/live/`, after `repro llm redact` (new command) runs over every file it commits. A failover shows up in the trace whenever the serving route changes between two replies. The replay test skips until those files exist.

**D-M5-10. Incident: the first live fixture run crashed every unit on empty replies (2026-11-21).**
Run 44881561203 (`live-smoke.yml`, mode fixtures): Mistral Large 4 returned HTTP 200 with no content to the agent's first prompt on all 8 fixtures, twice each. The client raised `EMPTY_OUTPUT`, the router turned that into a `RouterError`, and the runner recorded 8 infra errors after one retry each. Nothing was committed: the workflow then tried to redact and `git add` a cassette directory that did not exist, and both commands failed. The smoke and soak never saw this because they send short user-only prompts with small `max_tokens`. The first attempt took 72 s, which fits about 8K tokens of reasoning hitting the output cap; later attempts came back in 1 to 5 s, so the cause is not settled. Fixes: an empty reply is now a model problem, as DESIGN 5.5 says. The router returns it with its usage, so the tokens are counted in the ledger, quota and budget. The agent treats it as a parse failure and sends a repair message, saying the output limit was hit when `finish_reason` is `length`. B0 and B1 get no candidate. For diagnosis, the smoke now sends the agent's real first prompt to the primary route three ways (system role, system merged into the user turn, `reasoning_effort=low`) and records `finish_reason`, content size and reasoning tokens. Fixtures mode runs that smoke first. The workflow redacts and adds only paths that exist. Lesson: the live smoke must use prompts shaped like the real workload, not just "reply OK".

**D-M5-11. Second live fixture run: the reply sits in the reasoning field, and empty assistant turns get a 400.**
Run 44885965429. The probe showed every variant (system role, system merged, `reasoning_effort=low`) coming back with `finish_reason=stop`, empty `content`, and completion tokens equal to reasoning tokens plus one (54 and 53 on f01). So the model did answer, about 50 tokens, the size of one action, but NVIDIA's reasoning parser filed all of it as reasoning. The empty reply was handled as planned, but the next request carried an assistant message with empty content, and NVIDIA rejected it with HTTP 400, so all 8 units still ended as infra errors. Fixes: when `content` is empty and the reply finished (`stop`), the client uses the provider's `reasoning_content` or `reasoning` field as the reply and marks it `text_source=reasoning` in the trace. A reply cut off at the limit stays empty. Empty assistant turns are sent back as `(empty reply)`. The probe now says when the reply came from the reasoning field. If that field turns out to be empty too, the agent ends each fixture on repeated empty replies, and the next step is to try a different primary model on the fixtures.

**D-M5-12. Third live fixture run: no crashes, but the actions were split or swallowed by the provider.**
Run 44887356094 completed all 8 fixtures without infra errors, each ending `BUDGET_EXHAUSTED(tool_errors)` after 6 replies with no usable action. The cassettes show three shapes from Mistral Large 4 on NVIDIA: a short thought ("Let me look at the repo.") in the reasoning field with empty content; empty content with zero reasoning tokens but 29 completion tokens, about the size of one action, so the provider's parser most likely turned it into a native tool call that the client ignored; and an action block in content with its closing fence missing. Fixes: a native `tool_calls` entry is turned back into our text action (`text_source=tool_call`); the parser accepts a missing closing fence at the end of a reply and a bare JSON object with a `"tool"` key (still exactly one per reply); when content is empty the raw provider message is kept in the trace (`raw_note`) so the next run shows exactly what came back. The stale cassettes and report of this run were removed, since replaying them under the new parser diverges by design; the replay test skips until the next live run records new ones.

**D-M5-13. Fourth live fixture run: the server drops replies written as tool calls; send native tool schemas to that route.**
Run 44891484001. With the raw messages now in the trace the pattern is plain. Mistral Large 4 on NVIDIA returns either a short thought in `reasoning_content` ("Let me explore the repo.") or a message with nothing at all: no content, no reasoning, no `tool_calls`, yet 22 to 29 completion tokens, about the length of one action. The model is writing its action as a native tool call (it was trained for function calling, and the prompt lists "Tools"), and because the request declared no tools, the server's tool-call parser drops it. The short "reply OK" and soak prompts never trigger this. Fix: routes can set `native_tools: true` (now set on `nvidia/mistral-large-4`); the agent passes JSON schemas for its six tools with every call, the client sends them only to such routes, and the returned `tool_calls` are turned back into the same text actions (D-M5-12), so the loop, traces and cassettes do not change. Baselines send no tools. This departs from DESIGN 5.5's text-only protocol for one route, for a provider reason; other routes stay text-only. The probe now also tries the native-tools variant and sends the same prompt to the other NVIDIA and Gemini routes, so if this guess is wrong the next run shows which route answers. This run's cassettes and report were removed as stale.

**D-M5-14. Fifth live fixture run works: 7 of 8 fixtures reproduced; M5 closed (2026-11-21).**
Run 44893477875 (23 min). With native tool schemas, Mistral Large 4 answered with tool calls (61 of 80 replies) and the agent reproduced 7 of 8 fixtures, all `SUBMITTED` after one gate check (f08 took 3 checks and 20 steps, 67K tokens; the others 7 to 10 steps, 11K to 18K tokens). f05 is an honest WRONG_TEST: the agent wrote two tests, both failed for the right reason on the buggy code, but one expected the wrong behaviour and still fails after the fix (F→F). The gate cannot catch that without the fix, which is exactly the gap H3 measures. No failover happened (every call served by Mistral). The probe also showed Nemotron Ultra and Gemini Flash-Lite answering the agent prompt with a proper text action, so they are working fallbacks; Laguna XS answered without one. The hermetic suite now replays the committed cassettes to the same outcomes. Smaller issues seen, left for M7 prompt work: 13 replies were prose with no action, two bash calls used `command` instead of `cmd`, and three calls nested our `{"args": ...}` format inside the native call (now unwrapped by the client; the recorded run is unaffected because cassettes store the converted text).

## 2026-11-21, M6 (chat part; the Docker run is E1)

**D-M6-1. Test commands, log parsers and image builds come from the pinned SWT-bench harness, not the `swebench` package.**
SWT-bench vendors its own copy of the SWE-bench specs (`constants.py`, `log_parsers.py`) and builds its own images, and it is the harness that scores our predictions, so using it keeps both sides identical. Its top-level package is called `src`, which clashes with this repo's layout, so Repro loads `constants.py` and `log_parsers.py` by file path (both are stdlib-only, so CI only needs a checkout, no install), and runs image builds in a subprocess with the checkout as working directory (`repro/datasets/_swt_driver.py`). The commit is pinned in one constant (`SWT_BENCH_REF`), and a test checks every workflow uses the same one.

**D-M6-2. One hardened image per instance instead of hardening every container.**
DESIGN 5.2 says to replace `.git` at container start. The agent's workspace and every gate and oracle run start a container, and `git add -A` on Django takes a while, so Repro does it once: start the SWT-bench instance image with the network off, replace `.git` with one fresh commit, garbage-collect, apply nothing else, and `docker commit` it as `repro-hardened:<instance>`. All sandboxes start from that image. Same guarantee (no later commit, remote or reflog reachable), paid once.

**D-M6-3. Django and sympy run through the harness's own command and parser; pytest repos keep the Repro plugin.**
Statuses for `runtests.py` and `bin/test` come from SWT-bench's parsers, as DESIGN 5.3 asks. The gate also needs failure details and repo calls, so: exception type, message and repo frames are read from the tracebacks those runners print, and a `sitecustomize.py` tracer (Python 3.5 compatible) records which repo functions run. Unittest reports an exception raised inside a test as ERROR; that is the same event as a pytest call-phase failure, so it counts as FAILED. A requested test missing from the log counts as ERROR (did not run). An end-to-end test drives a unittest repo through the whole gate this way.

**D-M6-4. G5 is weaker on sympy.**
Django's tests run through `unittest.TestCase.run`, so the tracer attributes calls to each test. sympy's `bin/test` has no such hook, so every repo function called during the run counts for each test in it. A test that touches no repo code could pass G5 if another test in the same file does. Accepted for now and flagged for the M7 pilot.

**D-M6-5. sphinx is not supported yet.**
The harness runs sphinx's tests through `tox`, which this runner does not drive. sphinx is 28 of the 433 Verified instances (6.5%). It is left out of the pilot; M7 decides between running pytest in the image's environment or driving tox, before the Test sample is drawn.

**D-M6-6. Disk policy per instance.**
After every unit the instance's SWT-bench image and its hardened image are removed and `docker system prune` runs; if free disk is then under 12 GB, the shared environment image goes too. Free disk is written to the trace as a `disk` event. The executor releases even when a unit crashes.

**D-M6-7. Workflow hardening landed with M6.**
`live-smoke.yml` and `soak.yml` had the API keys in job-level `env`; they are now only in the steps that call models. `ci.yml` no longer runs on `pull_request` (only `push` and manual), and every workflow has a `concurrency` group. `scripts/check_workflows.py` enforces this in CI (T6.10). The nightly `eval.yml` schedule does nothing unless `eval/active_run.yaml` exists on `main`.

**D-M6-8. Instance records are exported on the runner and never committed.**
They hold the real fix and tests. `scripts/export_instances.py` writes them to `eval/data/` (git-ignored) in each workflow. Configs can name their instances with `instances_file` (new field, part of the hash, which is why the fixture report's hashes changed).

**D-M6-9. The leak canary scans traces and recorded prompts after the fact.**
Markers: lines the real fix adds (20+ characters, not already in the repo), the real tests' function names, the fail-to-pass names, and a slice of the hints. It scans traces and, where a run recorded them, cassettes (which hold the prompts). A live guard that refuses to send a prompt with a marker was considered and left out for now: an agent can legitimately invent the same test name as the real one.

**D-M6-10. Docker details.**
The reaper runs at the start and end of every SWT-bench batch (new `on_stop` hook on the runner, called on SIGTERM too). The host watchdog gives the in-container `timeout` 15 s of grace before killing the container. A process killed with exit 137 that did not time out is reported as a memory failure. In-container timeouts are whole seconds, at least 1.

**D-M6-11. Incident: the first gold-agreement run timed out with nothing saved (2026-11-21).**
docker-tests run 44903209101: every real-Docker test passed in 24 s (T6.D1 to T6.D7, T6.D9), but the gold-agreement step ran all 10 pilot instances one after another and hit its 280-minute limit. The script wrote its report only at the end and its output was captured, so nothing was committed and nothing was visible: the same mistake as the first soak run (D-M3-11), repeated in a new script. The likely time sink is the official harness: by default it computes coverage and runs the whole test module for each gold patch (a Django module can have hundreds of tests) with a 30-minute timeout. Fixes: one matrix job per instance (each on its own runner, 105-minute limit) and a final job that merges the per-instance results and commits the report; the script writes its result after every instance, resumes from a partial report, and streams progress to the log; the official harness runs with coverage off and a 15-minute timeout; our oracle's per-run timeout drops from 15 to 5 minutes. Lesson, now a rule for every long job: save as you go, and split independent work across jobs instead of looping inside one.

**D-M6-12. Second gold-agreement run: "10 of 10 agree" was two failures agreeing; records now come from SWE-bench Verified.**
docker-tests run 44932530547 finished in 51 minutes with the matrix (all 10 jobs and the 7 Docker tests green), but the report was hollow: our parser rejected every gold test patch ("diff ends in the middle of a hunk"), so our oracle said WRONG_TEST (or failed to load, on sympy), and the official harness said unresolved for the same rows read from `nmuendler/SWT-Bench_Verified_bm25_27k_zsp`. The script counted that as agreement and the test passed. What a hunk cut short at the end looks like is a patch string that was stripped, losing trailing blank context lines. Fixes: records (and official scoring) use the original `princeton-nlp/SWE-bench_Verified` rows for the SWT-bench Verified ids, the way the bootstrap run validated the harness; gold patches go through `repair_patch`, which restores trimmed trailing context; the diff parser and the candidate-diff writer split on newlines only (`str.splitlines` also splits on form feeds, which appear in real source files); a gold instance now passes only if our oracle says REPRODUCED and the official harness resolved it (or could not score it, listed separately), and the test fails otherwise; a gold patch our oracle rejects is printed to the log. Lesson: an agreement check needs a positive control, or two broken sides look like agreement.

**D-M6-13. Third gold-agreement run: 3 of 10 pass; two bugs in our oracle path found.**
docker-tests run 44939998948 (46 min): with readable patches, the pytest-based instances scikit-learn, pytest and xarray reproduce in both our oracle and the official harness. The other seven exposed two bugs. (1) The oracle asks the runner for whole test files, but the Django and sympy runner only reported outcomes for exact test ids, so every test came back missing (E→E) on all five Django and sympy instances. Now a requested file expands to every test the log shows in it (sympy names are matched to files by their `def`). (2) O2 counted a test that fails both before and after the fix (F→F) as collateral damage. On matplotlib (image-comparison tests) and requests (proxy tests that need network), those tests fail in any sandbox, and the official harness ignores them. O2 now counts only tests that passed on the buggy code and fail once the fix is in (P→F), which is what "the candidate broke something" means. The candidate's own tests must still all pass after the fix (O1). The Django and sympy runner also logs the command and the head and tail of the output whenever a requested test is missing from the log.

**D-M6-14. Fourth gold-agreement run: 6 of 10 pass; three more fixes.**
docker-tests run 44995328875: sympy (both), scikit-learn, pytest, xarray and requests now reproduce on both sides. The rest: (1) `django__django-16662`'s fail-to-pass name is a docstring ("#24155 - Tests ordering of imports."), which Django prints instead of the test name and SWE-bench keeps; it could not be mapped to a test id. Such names are now kept as `<test file>::<docstring>` (the file whose added lines contain it), matched against the log by text, and run through their module. (2) On `django__django-16938` the fail-to-pass tests are inherited by test cases in modules the gold patch does not touch, and the oracle only ran the touched files. It now also runs candidate test ids outside those files. (3) matplotlib hit the 105-minute step limit without a word in the log; the official-harness call could wait up to an hour. That call now times out at 25 minutes, the gold job gets 160 minutes, and the script prints a timestamped line per stage plus the official harness's output tail. Open: on `django__django-15127` our oracle reproduces the gold test (F→P) but the official harness says unresolved; its output in the next run should say why.

**D-M6-15. Fifth gold-agreement run: still 6 of 10; making the remaining four visible.**
docker-tests run 45007934279 (2 h). matplotlib's image build ran past its 2-hour limit (an earlier run built it in about 30 minutes, so the build time varies a lot), and the harness writes build logs to files, so nothing showed. `django__django-16662` and `django__django-16938` still had docstring names that were unmapped because the docstring sits in unchanged lines of the test file; they now map to the patch's test file and are matched by text. On `django__django-15127` the official harness reports "unresolved" with no errors, and its per-instance logs were not printed. For the next run: the Django and sympy runner logs every command, its outcomes and the output tail (`REPRO_HARNESS_DEBUG`); a failed or timed-out image build prints the tails of the harness's build logs; and the gold script prints the official harness's `report.json` and test outputs. The build timeout drops to 90 minutes.

