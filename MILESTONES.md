# Repro: Milestones

Revision 2 (2026-11-17): $0 budget. Model calls go to free tiers through one router, and eval runs happen in GitHub Actions in resumable overnight batches. There is no rented VM any more.

One milestone = one build session. Each ends with green tests and a push to `main`.

There are two tracks:

- **Track A (M1 to M11): build sessions in Claude chat.** No Docker, no dataset access, no model API access there. Everything is built against fakes, fixture repos, and cassettes.
- **Track B (E0 to E3): GitHub Actions runs.** Docker, real free-tier model calls, SWT-bench. A chat session can start them through the GitHub REST API and read the results back from the repo. I only do a one-time setup (E0). Everything after that is a workflow run.

## Order and dependencies

| ID | What | Needs | Where |
|---|---|---|---|
| M1 | Skeleton, models, fixture repos, LocalSandbox, test runner, CI and bootstrap workflows | none | chat |
| E0 | My one-time setup: free accounts, API keys as repo secrets, run `bootstrap.yml` | M1 | me, then Actions |
| M2 | Verification gate + oracle + cheat suite | M1 | chat |
| M3 | LLM router over free tiers, rate limiting, token budget, cassettes, baselines B0 and B1 | M2 | chat, then a live smoke run in Actions |
| M4 | Eval runner with resumable batches and shards, metrics, report (on fixtures) | M3 | chat |
| M5 | Agent loop with context trimming | M4 | chat, then a live fixture run in Actions |
| M6 | DockerSandbox, SWT-bench loader, leak hardening, eval workflows | M2, E0 | chat, then E1 |
| E1 | Docker tests + gold agreement on 10 pilot instances | M6 | Actions |
| M7 | Dev pilot, iteration, pre-registration, freeze | M5, E1 | chat + E2 |
| E2 | Dev runs | M7 | Actions |
| M8 | Mutation scorer + flaky audit | M4 | chat |
| M9 | Final eval, report, failure notes | M7, M8 | chat + E3 |
| E3 | Final Test runs in nightly batches, official scoring, audit, mutation | M9 | Actions |
| M10 | Product layer: README with results, demo, real-repo run | M9 | chat |
| M11 | Stretch: GitHub Action | M10 | chat |

The eval pipeline is complete at the end of M4, one milestone before the agent exists. That is deliberate: the agent is measured from its first run.

M6 can be pulled forward to right after M2 if I want the Docker risk retired early.

**Repo visibility decision, needed before M6.** The plan needs about 13,500 Actions minutes (EVAL section 5). Public repos get unlimited free minutes on standard runners; private repos get 2,000 per month. Default: make the repo public before M6. Until then everything fits in the private allowance.

## How every session runs

Kickoff prompt (copy, change the number):

```
Project: Repro, milestone M<k>.
Clone github.com/jsm05/repro. Read DESIGN.md, then the M<k> sections of MILESTONES.md and TEST_SPEC.md. Read DECISIONS.md (if it exists) for anything that changed since the plan.
Build at most 2 milestones this session, starting at M<k>. Do not start a third. Write each milestone's tests from TEST_SPEC.md first, then the code.
If something in the plan looks wrong, stop and ask me before changing it.
Before finishing: run the full test suite, ruff and mypy. Append any deviation from DESIGN.md to DECISIONS.md. Tick the done criteria in MILESTONES.md.
Commit with: git -c commit.gpgsign=false commit --author="Jashan Singh Mehta <71641689+jsm05@users.noreply.github.com>" (set user.name and user.email to the same so the committer matches). One commit per logical step (tests, implementation, docs). No Co-Authored-By or Claude-Session lines. Real timestamps only.
If main was rewritten since your last fetch, rebase unpushed work onto origin/main; never force-push old history. Push to main.
If this milestone has an Actions step, start it with the GitHub REST API and report the result, or tell me the exact gh command if you can't.
End the session with a study list: the "Explain it" points for each milestone built, plus the key files to read. Also tell me what deviates from the plan and what I need to do, if anything.
```

Definition of done for every milestone:

- [ ] All tests listed for this milestone in TEST_SPEC.md exist and pass
- [ ] Whole suite green: `pytest -m "not docker and not live_llm"`
- [ ] `ruff check` and `mypy --strict src` clean
- [ ] CI green on GitHub
- [ ] `DECISIONS.md` updated (date, decision, why) for anything that differs from DESIGN.md
- [ ] Done criteria below ticked, pushed to `main`

After each session, my own part: read the code, and be able to answer the "explain it" questions listed under that milestone without looking.

---

## M1. Skeleton, models, fixtures, LocalSandbox, test runner

**Goal:** a gold test can be shown to fail before and pass after on toy bugs, end to end, with no Docker.

**Build**
- `pyproject.toml` (src layout, Python 3.11+), ruff, mypy, pytest with markers `docker`, `live_llm`, `slow`.
- `.github/workflows/ci.yml`: lint, types, hermetic tests on every push.
- `.github/workflows/bootstrap.yml` (manual trigger): frees runner disk, installs the SWT-bench harness, validates one gold instance (`sympy__sympy-20590`, the README's own example), exports the Verified instance ids to `eval/samples/verified_ids.txt`, writes `eval/box-notes.md` (runner specs, disk before and after, time taken, image names and sizes, dataset column names), and commits both files. No API keys needed.
- `models.py`: `Instance`, `AgentView`, `SealedView`, `Candidate`, `TestOutcome`, `ExecResult`.
- `Sandbox` protocol and `LocalSandbox` (temp dir copy, rlimits, process-group kill on timeout, output cap).
- Test runner: build the pytest command, parse JUnit XML into per-test outcomes.
- `FixtureSource` and 8 fixture repos under `tests/fixtures/mini_repos/`, each with `repo/`, `fix.patch`, `gold_test.patch`, `issue.md`, `instance.json`:

| Fixture | Designed to exercise |
|---|---|
| `f01_off_by_one` | Plain assertion bug |
| `f02_raises` | Bug shows up as an unexpected exception |
| `f03_wrong_reason_trap` | An obvious test fails with a `TypeError` in the test itself |
| `f04_hash_order` | Output depends on set ordering; a careless test is hash-seed dependent |
| `f05_append_only` | New test must be appended to an existing test file |
| `f06_two_modules` | Bug spans two modules |
| `f07_ambiguous_issue` | Issue text allows a plausible but wrong expectation (ends F→F) |
| `f08_can_hang` | A wrong test can loop forever (timeout path) |

- `repro fixtures check`: runs the gold test before and after the fix for every fixture.

**Not in M1:** gate rules, LLM, Docker.

**Done when**
- [x] `repro fixtures check` prints F→P for all 8 fixtures
- [x] Sandbox contract tests (T1.x) pass on `LocalSandbox`
- [x] A timeout kills the whole process tree within 2 s of the limit
- [x] CI is green on the first push
- [x] `bootstrap.yml` passes `actionlint` (it first runs for real in E0)

**Explain it:** Why is `AgentView` a separate type instead of a flag? What does the runner count as "failed" and why are errors excluded? What can `LocalSandbox` not protect against?

---

## E0. My one-time setup (about 30 minutes)

**Goal:** free accounts and keys in place, and proof that the benchmark harness runs on a free GitHub runner.

1. Create free accounts and API keys. No card needed for any of these:
   - **NVIDIA** (build.nvidia.com): sign up (phone verification, no card), then Settings → API Keys → create a key. This is the primary model provider.
   - **Google AI Studio** (aistudio.google.com): "Get API key". Note the free limits it shows for the Flash and Flash-Lite models.
   - **OpenRouter** (openrouter.ai): create a key. Do not buy credits.
   - **Docker Hub** (hub.docker.com), optional: free account, then a read-only personal access token. Only needed if the harness has to pull prebuilt images.
   - **Cerebras: skip.** Its trial needs a payment method and expires after 30 days.
   - **Mistral: skip.** Its free API plan now asks for prepaid credits (checked 2026-11-19).
2. Put the keys into the repo as Actions secrets. On my laptop, with the GitHub CLI installed (`gh`); each command prompts for the value, so keys never go into a chat:

```bash
gh auth login
gh secret set NVIDIA_API_KEY     --repo jsm05/repro
gh secret set GEMINI_API_KEY     --repo jsm05/repro
gh secret set OPENROUTER_API_KEY --repo jsm05/repro
gh secret set DOCKERHUB_USERNAME --repo jsm05/repro
gh secret set DOCKERHUB_TOKEN    --repo jsm05/repro
gh secret list --repo jsm05/repro
```

   Without the CLI: repo → Settings → Secrets and variables → Actions → New repository secret, once per key.

3. Run the bootstrap workflow and watch it:

```bash
gh workflow run bootstrap.yml --repo jsm05/repro
gh run watch --repo jsm05/repro
```

4. Send the next chat session the free limits shown in AI Studio. The chat cannot see that page; M3 puts them into `config/routes.yaml`. NVIDIA shows no limits page; its behaviour was measured with two probe scripts (DECISIONS D-E0-3).

5. When ready (before M6): make the repo public.

```bash
gh repo edit jsm05/repro --visibility public --accept-visibility-change-consequences
```

**Done when**
- [x] Repo secrets set: `NVIDIA_API_KEY`, `GEMINI_API_KEY`, `OPENROUTER_API_KEY` (Docker Hub optional)
- [x] `bootstrap.yml` reports the gold instance as resolved
- [x] `eval/samples/verified_ids.txt` committed with 433 ids, and `eval/box-notes.md` committed
- [x] Decision recorded in `DECISIONS.md`: did the SWT-bench harness's image build fit on the runner? If not, the sandbox and oracle use prebuilt SWE-bench images from Docker Hub (RISKS R12)

---

## M2. Verification gate + oracle + cheat suite

**Goal:** the harness can tell a real reproduction from every fake I can think of.

**Build**
- G0 scope checker (diff parser: test paths only, additions only, no conftest or config edits, no skip or xfail markers).
- G1 to G5 on pristine sandboxes: collection, fails-before, right-reason classifier, 3x stability with hash seeds 0 to 2, stdlib tracer plugin, source-tree hash check.
- Failure signature (type, masked message, innermost repo frame).
- Oracle: O1, O2, per-test transitions, instance outcome.
- `GateVerdict` and `OracleVerdict` models; `repro verify` on fixtures.
- Cheat candidates for the adversarial suite, stored per fixture under `cheats/`.

**Not in M2:** the LLM judge (M7 ablation), Docker.

**Done when**
- [x] All 8 gold tests pass G0 to G5 and the oracle
- [x] Every cheat in TEST_SPEC T2.A is rejected by the check named there
- [x] The two oracle-only cases (T2.B) pass the gate and are caught by the oracle, and the test says so explicitly
- [x] Verification provably never touches the workspace it was given (T2.9)

**Explain it:** Walk through G0 to G5 from memory. Which cheats can only the oracle catch, and what metric does that create? Why additions-only?

---

## M3. LLM router over free tiers, token budget, baselines

**Goal:** model calls go through one OpenAI-compatible client to free routes, with rate limiting, quota tracking, failover, and hard token ceilings. All of it is tested with no network. Two baselines produce candidates.

**Build**
- `config/routes.yaml`: NVIDIA (primary Mistral Large 4, plus the backup models in DESIGN 5.5), Gemini, and OpenRouter routes with base URLs, exact model version strings, capability flags, published or observed limits, reset rules, `trains_on_data` flag, and a `verified_on` date. Filled from the AI Studio limits I send at the start of this session (E0 step 4) and the NVIDIA probe results (DECISIONS D-E0-3).
- `registry.py`: logical models mapped to same-weights routes. `-latest` aliases refused in eval configs.
- `OpenAICompatClient`: the `openai` SDK with `base_url` per route, `max_retries=0`, raw headers exposed.
- `errors.py`: table-driven classifier (provider, status, body pattern) → rate limit, quota exhausted, transient, context length, bad request, auth, empty output. Tested on recorded error samples.
- `limiter.py`: token buckets (req/s, req/min, tokens/min) and worst-case reservation before each call.
- `quota.py`: persisted daily and monthly counters with per-provider reset rules; overridden by provider-reported remaining quota where available; 1/N split for shards.
- `router.py`: `pinned` and `chain` policies, backoff with jitter, circuit breaker, per-call route recording, `mixed` tagging.
- `tokens.py`: `tiktoken` estimate times 1.15, per-route correction factor learned from reported usage.
- `budget.py`: `TokenBudget` with pre-emptive per-bug and run-level ceilings; append-only token ledger with a file lock. `list_prices.yaml` for the list-price equivalent.
- `FakeProvider` (an in-process HTTP stub that can be told to return 429s, quota errors, 5xx, timeouts, context-length errors), `FakeLLM`, `CassetteLLM`.
- BM25 retrieval fitted to the prompt cap. B0 (single prompt) and B1 (pass@5 with the online gate).
- `repro llm routes | ping | quota`.
- `.github/workflows/live-smoke.yml` (manual): one tiny call per enabled route; lists the models each key can see (the providers' model list endpoints), prints observed rate-limit headers; commits a short `eval/live-smoke.md`.
- `.github/workflows/soak.yml` (manual): up to about 500 requests of mixed sizes to the primary route within a 5 h limit, saving the report after every request; reports success rate, latency p50 and p95, 429s, timeouts, and any quota errors; commits `eval/soak.md`.
- The primary is fixed: `mistralai/mistral-large-4-1t-a52b` on NVIDIA (DECISIONS D-E0-3). Decide how to handle its reasoning tokens: 8K output cap, or lower reasoning effort if the API supports it. Record the result in `DECISIONS.md`.

If this session runs long, B1 moves to M4. Nothing else moves.

**Not in M3:** the agent loop, context trimming (M5).

**Done when**
- [x] Property test: no sequence of calls can push tokens past a ceiling (T3.3)
- [x] Failover tests against `FakeProvider` pass for every error class (T3.15 to T3.22)
- [x] `pinned` never switches models; `chain` does and tags the run `mixed` (T3.18, T3.19)
- [x] Quota state survives a restart and resets at the right boundary per provider (T3.20)
- [x] B0 and B1 produce candidates on fixtures with `FakeLLM`, and those go through gate and oracle
- [x] Record-then-replay through `CassetteLLM` gives byte-identical results
- [ ] `live-smoke.yml` run green in Actions: every enabled route answered, and the ledger's token counts match what each provider reported (T3.L1)
- [ ] `soak.yml` run for one night: no hidden daily cap on the primary, or the plan adjusted and recorded (T3.L2)

**Explain it:** Why check the budget before the call instead of after? Why may the eval never fail over to a different model? How does the router tell a per-minute limit from a daily quota, and why does it matter? Why a text action protocol instead of native tool calls?

---

## M4. Eval runner with resumable batches, metrics, report

**Goal:** the full measuring pipeline runs on fixtures, can be killed and resumed at any point, and produces the README table. The agent does not exist yet.

**Build**
- `repro eval`: work units keyed (instance, config), resume by skipping units that have a row, `--max-units`, `--deadline`, `--deadline-minutes`, `--shard k/N`, `--stop-when-exhausted`.
- Deferral: a unit that hits exhausted quota under `pinned` writes nothing and is retried later; after 3 deferrals it becomes INFRA_ERROR. Deferral count kept in a small sidecar file.
- Graceful SIGTERM and SIGINT, lock file per (run, shard).
- Results writer behind an interface: local file (default) or `results` branch (commit per unit, push with rebase-and-retry). Tested against a local bare git repo.
- `repro status`: done, remaining, deferred, infra errors, tokens per route, quota left, estimated nights to finish.
- `metrics.py`: S with Wilson CI, exact McNemar, gate precision and recall, token stats and list-price equivalent, token-ceiling curve, route mix, flaky and mutation aggregations (inputs stubbed until M8).
- SWT-bench predictions export.
- `repro report`: markdown table and plots from results only. `repro show`: readable trace timeline.
- Golden end-to-end test: fixtures + cassette in, byte-stable report out.

**Done when**
- [ ] Metrics match hand-computed answers on the synthetic results file (T4.1 to T4.5)
- [ ] Kill the run mid-way, restart: no duplicate rows, no unit redone, ledger totals unchanged for finished units (T4.6)
- [ ] Batch controls, deferral, results-branch writer, and status behave as specified (T4.14 to T4.22)
- [ ] `repro report` output is byte-identical across two runs on the same input
- [ ] B0 and B1 numbers on fixtures appear in a generated report

**Explain it:** What is in the denominator of S, and why does a deferred unit not count yet? Why McNemar and not a plain difference of two percentages? How does resume avoid redoing work? What happens to a unit when the Actions job is killed at 6 hours?

---

## M5. Agent loop with context trimming

**Goal:** the agent reproduces fixture bugs under hard token and step limits, keeps every prompt under the cap, and every way it can stop is tested.

**Build**
- Text action protocol parser with one repair message on a parse failure.
- Six tools (`bash`, `view`, `write_test`, `check`, `submit`, `abstain`) with argument validation.
- Loop with all stop conditions from DESIGN 5.6, including tokens, requests, active wall clock (rate-limit waits excluded) and the hard cap (waits included); best-candidate tracking; terminal states including `DEFERRED`.
- Context manager: pinned items, state card, the four trimming steps, the 32K prompt cap (never above the smallest context window among the logical model's routes).
- System prompt v1.
- Trace events for every step, including `trim` and `failover`.
- Live fixture run: `live-smoke.yml` gains a `fixtures` mode that runs `A-chain` on the 8 fixtures with `LocalSandbox` (no Docker needed) and commits the cassettes to `eval/cassettes/`.

**Done when**
- [ ] Scripted `FakeLLM` scenarios cover every stop condition and terminal state (T5.1 to T5.13)
- [ ] Trimming and protocol tests pass: cap never exceeded, pinned items always present, stubs replace old outputs in order, parse failures handled (T5.12, T5.18 to T5.23)
- [ ] One agent run yields both gated and always-emit results
- [ ] Agent config plugs into `repro eval` on fixtures and shows up in the report next to B0 and B1
- [ ] Live fixture run in Actions completed, cassettes committed, and the hermetic suite replays them

**Explain it:** List the stop conditions and what each protects against. What does the state card contain and why does trimming need it? What happens when quota runs out halfway through an agent run? Why a shell tool?

---

## M6. DockerSandbox, SWT-bench loader, leak hardening, eval workflows

**Goal:** the same pipeline runs on real SWT-bench instances in locked-down containers on free GitHub runners.

**Build (chat)**
- `DockerSandbox` over the `docker` CLI with the settings in DESIGN 5.2, watchdog, label-based reaper, image cleanup after each instance.
- `SWTBenchSource`: loads instances, builds `AgentView` and `SealedView`, resolves the image (per the E0 decision), takes test commands and log parsers from the `swebench` package.
- Leak hardening: `.git` replacement, no-network check, sealed-field canary scanner.
- Unit tests with a fake `docker` binary on PATH. Real-Docker tests marked `docker`.
- Workflows:
  - `docker-tests.yml`: `pytest -m docker`, then gold agreement on `eval/samples/pilot_10.txt`
  - `eval.yml`: inputs `config`, `run_id`, `shards` (default 6), `ref`; a matrix of shards, each freeing disk, logging in to Docker Hub, running `repro eval --shard k/N --deadline-minutes 330 --stop-when-exhausted`, committing rows to the `results` branch per unit, uploading artifacts even on failure; nightly schedule at 20:30 UTC that runs only if `eval/active_run.yaml` exists on `main`
  - `official-score.yml`: SWT-bench harness on exported predictions, one matrix job per instance
  - all workflows: triggers limited to `push`, `schedule`, `workflow_dispatch`; `concurrency` groups per run
- `eval/samples/pilot_10.txt`: 10 instances spread across repos, picked from `verified_ids.txt`. Used only with gold tests.

**E1 (Actions)**, started by the chat session through the REST API, or by me:
```bash
gh workflow run docker-tests.yml --repo jsm05/repro
gh run watch --repo jsm05/repro
```

**Done when**
- [ ] Sandbox contract tests pass on `DockerSandbox` (same tests as `LocalSandbox`, plus network, memory, PID, cleanup tests)
- [ ] For 10 pilot instances the gold tests are F→P in my oracle, and the official harness agrees on all 10 (or the ones it could build, with the rest listed)
- [ ] `.git` inside the sandbox has exactly one commit; a network call from inside fails
- [ ] No containers left after a killed run; disk is reclaimed between instances
- [ ] A static check confirms no workflow uses `pull_request_target` and secrets appear only in steps that need them (T6.10)

**Explain it:** What does each Docker flag defend against? What is the threat model and where does it stop? How could the fix leak into the agent's view, and what blocks each path? Why can't a fork's pull request read the API keys?

---

## M7. Dev pilot, iteration, pre-registration, freeze

**Goal:** the agent works on real bugs, tuned only on Dev, then frozen.

**Build and run**
- First: generate `dev_15.txt` and `test_50.txt` from `verified_ids.txt` with the seeded script and commit them (pre-registration commit).
- E2: run `A-main`, `B0-main`, `B1-main` on Dev. Optionally `A-chain` to see whether the fallback models help.
- Read Dev traces, fix prompt, gate, trimming, and budget problems. At most 3 rounds.
- Add the optional G3 LLM judge behind a flag.
- Apply the model decision rule (EVAL section 5).
- Tag `eval-freeze-v1`.

**E2 (Actions)**, once per round:
```bash
gh workflow run eval.yml --repo jsm05/repro -f config=eval/configs/dev.yaml -f run_id=dev-r1 -f shards=6
gh run watch --repo jsm05/repro
```
If a round does not finish in one go (quota or the 6-hour limit), run the same command again with the same `run_id`; it resumes.

**Done when**
- [ ] Pre-registration commit exists and predates any run touching Test instances
- [ ] Dev results for each round are on the `results` branch with config hashes
- [ ] Each change between rounds is one line in `DECISIONS.md` with the Dev number before and after
- [ ] Model decision recorded
- [ ] Tag `eval-freeze-v1` pushed

**Explain it:** Why is tuning on Test a problem even if I do not mean to cheat? What changed between rounds and what did each change buy? Why 50 and not 433, and what does 50 cost in precision?

---

## M8. Mutation scorer + flaky audit

**Goal:** two quality measurements that go beyond pass/fail. Built and tested on fixtures; can run in parallel with M6 and M7. No model calls.

**Build**
- Six mutation operators, scoping to fix-changed lines with fallback to the enclosing function, seeded cap of 30, host-side generation, sandbox-side execution.
- A small mutation fixture under `tests/fixtures/mutation/`: a fix region that yields exactly 4 valid mutants, one strong test, one weak test.
- `repro mutate`: per-mutant outcomes, score for Repro's tests and for gold tests on the same mutants. Resumable per instance, like `repro eval`.
- `repro audit`: 10x on buggy and on fixed, hash seeds 0 to 9, plus the gold-test control. Resumable per instance.
- Wire both into `metrics.py`, the report, and `eval.yml` (a `phase` input: `run`, `audit`, `mutate`).

**Done when**
- [ ] On the mutation fixture, a strong test kills 4 of 4 and a weak test 1 of 4 (T8.3)
- [ ] Mutants land only on fix-changed lines (T8.2)
- [ ] The audit flags the hash-order fixture's careless test and passes its careful test (T8.6)
- [ ] Report shows mutation and flaky sections populated from fixture data

**Explain it:** What does mutation score tell me that fail-before/pass-after does not? Why compare against gold tests instead of quoting the raw score? What flip rate can 10 runs realistically detect?

---

## M9. Final eval, report, failure notes

**Goal:** the numbers.

**E3 (Actions)**, spread over several nights:
```bash
# turn on the nightly schedule for the main run (the chat session can do this commit)
printf 'run_id: main\nconfig: eval/configs/main.yaml\nref: eval-freeze-v1\nshards: 6\n' > eval/active_run.yaml
git add eval/active_run.yaml && git commit -m "Start main eval run" && git push

# or start a batch right now instead of waiting for the night
gh workflow run eval.yml --repo jsm05/repro -f config=eval/configs/main.yaml -f run_id=main -f ref=eval-freeze-v1 -f shards=6

# then the ablations, variance reruns, and the no-model phases
gh workflow run eval.yml --repo jsm05/repro -f config=eval/configs/ablations.yaml -f run_id=abl -f ref=eval-freeze-v1
gh workflow run eval.yml --repo jsm05/repro -f run_id=main -f phase=audit  -f ref=eval-freeze-v1
gh workflow run eval.yml --repo jsm05/repro -f run_id=main -f phase=mutate -f ref=eval-freeze-v1
gh workflow run official-score.yml --repo jsm05/repro -f run_id=main
```
Progress at any time: the chat session clones the `results` branch and runs `repro status main`. When every unit has a row, delete `eval/active_run.yaml` to stop the nightly schedule.

Fallback if Actions is not usable: the same `repro eval` command on my own x86_64 machine overnight, for example `repro eval --config eval/configs/main.yaml --deadline 07:00 --stop-when-exhausted`, rerun each night until done.

**Chat session**
- Merge results into `main`, run `repro report --check`, check every item in EVAL section 6.
- Hand-read 15 failures (seeded pick), label them, write the failure notes.
- List every gate-passing WRONG_TEST.

**Done when**
- [ ] All configs have a row for every unit; infra errors counted, not dropped
- [ ] Model purity check passes: no `mixed` rows in headline configs
- [ ] Harness agreement computed and every disagreement listed
- [ ] Leak canary and memorisation check run over all traces
- [ ] `report.md` generated; targets table marked hit or miss
- [ ] Failure notes written with counts and examples
- [ ] Nightly schedule turned off

**Explain it:** Every headline number, its denominator, and its CI. Where the agent fails and why. What N=50 lets me claim and what it does not. What I would do next with a paid model.

---

## M10. Product layer

**Goal:** someone can understand the result in 90 seconds.

**Build**
- README filled in by `repro report` output (results first).
- `repro run` on one real open-source repo outside SWE-bench, with a `.repro.yml`, documented start to finish. Public repo only, since the free routes may train on prompts.
- 90-second demo: issue text, `repro run`, `repro show` timeline, test fails, apply fix, test passes, token and list-price line.
- `docs/V2_GITHUB_APP.md` expanded from DESIGN section 9, including the no-training-routes rule for private code.
- `docs/DEFENSE.md`: the 15 questions an interviewer would ask, with my answers.

**Done when**
- [ ] README opens with the generated results table and headline sentence
- [ ] Demo recording linked
- [ ] Real-repo run documented, including what setup it needed
- [ ] No number in the README is typed by hand

---

## M11. Stretch: GitHub Action

Workflow on `issues: opened` with a `bug` label. Runs `repro run` inside the repo's own CI environment using the free routes, comments the test only if the gate passes, respects a per-repo monthly token budget. Demo on a public toy repo of mine.

**Done when**
- [ ] Opening a labelled issue on the toy repo produces a comment with a failing test within the budget
- [ ] An issue the gate cannot confirm produces no comment

---

## If time runs short, cut in this order

1. M11 (GitHub Action)
2. `A-gemini` cross-model ablation
3. `A-main+judge` ablation
4. Variance reruns
5. Real-repo run in M10
6. Official SWT-bench scoring beyond the 10 pilots (my oracle's S becomes the headline, with the pilot agreement reported)

Not cuttable: gate + cheat suite, B0 baseline, pre-registration, token accounting, model purity, flaky audit, mutation score, failure notes.
