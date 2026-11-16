# Repro: Milestones

One milestone = one build session. Each ends with green tests and a push to `main`.

There are two tracks:

- **Track A (M1 to M11): build sessions in Claude chat.** No Docker, no dataset access, no real LLM calls there. Everything is built against fakes and fixture repos.
- **Track B (E0 to E3): eval-box tasks I run myself.** Docker, real model calls, SWT-bench. Each is a short list of commands. Results are pushed back to the repo so the next chat session can read them.

## Order and dependencies

| ID | What | Needs | Where |
|---|---|---|---|
| M1 | Skeleton, models, fixture repos, LocalSandbox, test runner | none | chat |
| E0 | Eval box up, SWT-bench harness validated on one instance | none (do it any time after M1) | box |
| M2 | Verification gate + oracle + cheat suite | M1 | chat |
| M3 | LLM layer, budget ledger, baselines B0 and B1 | M2 | chat |
| M4 | Eval runner, metrics, report (on fixtures) | M3 | chat |
| M5 | Agent loop | M4 | chat |
| M6 | DockerSandbox, SWT-bench loader, leak hardening | M2, E0 | chat, then E1 |
| E1 | Docker tests + gold agreement on 10 pilot instances | M6 | box |
| M7 | Dev pilot, iteration, pre-registration, freeze | M5, E1 | chat + E2 |
| E2 | Dev runs | M7 | box |
| M8 | Mutation scorer + flaky audit | M4 | chat |
| M9 | Final eval, report, failure notes | M7, M8 | chat + E3 |
| E3 | Final Test run, official scoring, audit, mutation | M9 | box |
| M10 | Product layer: README with results, demo, real-repo run | M9 | chat |
| M11 | Stretch: GitHub Action | M10 | chat |

The eval pipeline is complete at the end of M4, one milestone before the agent exists. That is deliberate: the agent is measured from its first run.

M6 can be pulled forward to right after M2 if I want the Docker risk retired early.

## How every session runs

Kickoff prompt (copy, change the number):

```
Project: Repro, milestone M<k>.
Clone github.com/jsm05/repro. Read DESIGN.md, then the M<k> sections of MILESTONES.md and TEST_SPEC.md. Read DECISIONS.md (if it exists) for anything that changed since the plan.
Build M<k> only. Write the M<k> tests from TEST_SPEC.md first, then the code. Do not start the next milestone.
If something in the plan looks wrong, stop and ask me before changing it.
Before finishing: run the full test suite, ruff and mypy. Append any deviation from DESIGN.md to DECISIONS.md. Tick the done criteria in MILESTONES.md. Push to main.
End by telling me: what you built, what deviates from the plan, and the exact commands I need to run on the eval box.
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
- `pyproject.toml` (src layout, Python 3.11+), ruff, mypy, pytest with markers `docker`, `live_llm`, `slow`. GitHub Actions workflow running lint, types, tests.
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
- [ ] `repro fixtures check` prints F→P for all 8 fixtures
- [ ] Sandbox contract tests (T1.x) pass on `LocalSandbox`
- [ ] A timeout kills the whole process tree within 2 s of the limit
- [ ] CI is green on the first push

**Explain it:** Why is `AgentView` a separate type instead of a flag? What does the runner count as "failed" and why are errors excluded? What can `LocalSandbox` not protect against?

---

## E0. Eval box (my task, about 1 to 2 hours)

**Goal:** prove the benchmark runs on a machine I control, before any code depends on it.

Machine: x86_64 Linux, 8 vCPU, 32 GB RAM, 250 GB SSD, Ubuntu 24.04. Rent it for the eval window; stop it between sessions.

```bash
# 1. Docker
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER && newgrp docker
docker run --rm hello-world

# 2. SWT-bench harness
sudo apt-get update && sudo apt-get install -y git python3-venv
git clone https://github.com/logic-star-ai/swt-bench.git && cd swt-bench
python3 -m venv .venv && source .venv/bin/activate
pip install -e .

# 3. Validate one gold instance (command from the SWT-bench README)
python -m src.main --predictions_path gold --max_workers 1 \
  --instance_ids sympy__sympy-20590 --run_id validate-gold

# 4. Record what it cost in disk and time
df -h / ; docker images | head -20 ; docker system df

# 5. Export the Verified instance ids (chat sessions cannot reach Hugging Face)
pip install datasets
cat > export_ids.py <<'PY'
from datasets import load_dataset
ds = load_dataset("nmuendler/SWT-Bench_Verified_bm25_27k_zsp")
print(ds)                                    # splits, columns, row counts
split = list(ds.keys())[0]
ids = sorted(ds[split]["instance_id"])
open("verified_ids.txt", "w").write("\n".join(ids) + "\n")
print(len(ids), "ids written")               # expect 433
PY
python export_ids.py

# 6. Put the ids and my notes in the repo (it is private, so log in first)
sudo apt-get install -y gh && gh auth login
gh repo clone jsm05/repro ~/repro
mkdir -p ~/repro/eval/samples && cp verified_ids.txt ~/repro/eval/samples/
cd ~/repro && nano eval/box-notes.md         # write the notes listed below
git add eval && git commit -m "E0: box notes and Verified ids" && git push
```

These commands are untested until E0 is done. If any command differs from the SWT-bench README on the day, the README wins. The dataset name in step 5 is taken from that README; `print(ds)` shows the real splits and columns, so adjust `instance_id` if the column is named differently. Before the first commit on the box, set `git config --global user.name` and `user.email` (use the GitHub noreply address).

**Done when**
- [ ] The gold validation reports the instance as resolved
- [ ] `eval/samples/verified_ids.txt` committed with 433 ids
- [ ] `eval/box-notes.md` committed: VM type and hourly price, time taken, disk used per image, image names produced by the harness, the exact flag for the Verified split, the dataset's column names
- [ ] Decision recorded in `DECISIONS.md`: reuse harness-built images for the sandbox, or pull prebuilt SWE-bench images

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
- [ ] All 8 gold tests pass G0 to G5 and the oracle
- [ ] Every cheat in TEST_SPEC T2.A is rejected by the check named there
- [ ] The two oracle-only cases (T2.B) pass the gate and are caught by the oracle, and the test says so explicitly
- [ ] Verification provably never touches the workspace it was given (T2.9)

**Explain it:** Walk through G0 to G5 from memory. Which cheats can only the oracle catch, and what metric does that create? Why additions-only?

---

## M3. LLM layer, budget ledger, baselines

**Goal:** model calls are metered, capped, replayable; two baselines produce candidates.

**Build**
- `LLMClient` protocol, `FakeLLM` (scripted), `CassetteLLM` (record and replay by request hash), `AnthropicClient`.
- `pricing.yaml` with `verified_on`; cost function including the Haiku 5.5 100K-token price tier. Re-check prices against Anthropic's pricing page in this session.
- Ledger: append-only JSONL, file lock, pre-emptive per-bug and run-level ceilings.
- BM25 retrieval with a token budget.
- B0 (single prompt) and B1 (pass@5 with the online gate).

**Not in M3:** the agent loop, any paid run beyond one smoke call.

**Done when**
- [ ] Property test: no sequence of calls can push spend past a ceiling (T3.3)
- [ ] Two processes writing the ledger at once lose no entries (T3.4)
- [ ] B0 and B1 produce candidates on fixtures with `FakeLLM`, and those go through gate and oracle
- [ ] Record-then-replay through `CassetteLLM` gives byte-identical results
- [ ] Optional, on my own machine: one live smoke call (`pytest -m live_llm`) costs what the ledger says it cost, checked against the API usage numbers

**Explain it:** Why check the budget before the call instead of after? What exactly is in the cassette key and what breaks replay? Why is B1 in the eval at all?

---

## M4. Eval runner, metrics, report

**Goal:** the full measuring pipeline runs on fixtures and produces the README table. The agent does not exist yet.

**Build**
- `repro eval`: work units keyed (instance, config), resumable, 4 workers, global cap.
- `metrics.py`: S with Wilson CI, exact McNemar, gate precision and recall, cost stats, cost-vs-S curve, flaky and mutation aggregations (inputs stubbed until M8).
- SWT-bench predictions export.
- `repro report`: markdown table and plots from `results.jsonl` only. `repro show`: readable trace timeline.
- Golden end-to-end test: fixtures + cassette in, byte-stable report out.

**Done when**
- [ ] Metrics match hand-computed answers on the synthetic results file (T4.1 to T4.5)
- [ ] Kill the run mid-way, restart: no duplicate rows, no double spend (T4.6)
- [ ] `repro report` output is byte-identical across two runs on the same input
- [ ] B0 and B1 numbers on fixtures appear in a generated report

**Explain it:** What is in the denominator of S, and what is not allowed to leave it? Why McNemar and not a plain difference of two percentages? How does resume avoid paying twice?

---

## M5. Agent loop

**Goal:** the agent reproduces fixture bugs under hard limits, and every way it can stop is tested.

**Build**
- Six tools (`bash`, `view`, `write_test`, `check`, `submit`, `abstain`) with argument validation.
- Loop with all stop conditions from DESIGN 5.6, best-candidate tracking, terminal states.
- Context manager: output truncation, 90K prompt cap, drop-oldest policy that keeps the issue and the latest verdict.
- System prompt v1.
- Trace events for every step.

**Done when**
- [ ] Scripted `FakeLLM` scenarios cover every stop condition and terminal state (T5.1 to T5.13)
- [ ] One agent run yields both gated and always-emit results
- [ ] Agent config plugs into `repro eval` on fixtures and shows up in the report next to B0 and B1
- [ ] Optional, my machine: live run on the 8 fixtures with Haiku 5.5, traces recorded as cassettes and committed

**Explain it:** List the stop conditions and what each protects against. What happens when the budget runs out with a gate-passing candidate vs without one? Why a shell tool?

---

## M6. DockerSandbox, SWT-bench loader, leak hardening

**Goal:** the same pipeline runs on real SWT-bench instances in locked-down containers.

**Build (chat)**
- `DockerSandbox` over the `docker` CLI with the settings in DESIGN 5.2, watchdog, label-based reaper.
- `SWTBenchSource`: loads instances, builds `AgentView` and `SealedView`, resolves the image, takes test commands and log parsers from the `swebench` package.
- Leak hardening: `.git` replacement, no-network check, sealed-field canary scanner.
- Unit tests with a fake `docker` binary on PATH. Real-Docker tests marked `docker`.
- `scripts/gold_agreement.sh` and `scripts/official_score.sh`.
- `eval/samples/pilot_10.txt`: 10 instances spread across repos, picked from `verified_ids.txt` (committed in E0). Used only with gold tests. No model runs on them here, so overlap with Test later is harmless.

**E1 (box)**
```bash
cd ~/repro && git pull && python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[eval]"
mkdir -p eval/box-logs
pytest -m docker -x 2>&1 | tee eval/box-logs/e1-docker-tests.log
bash scripts/gold_agreement.sh eval/samples/pilot_10.txt 2>&1 | tee eval/box-logs/e1-gold-agreement.log
git add eval/box-logs && git commit -m "E1 logs" && git push
```

**Done when**
- [ ] Sandbox contract tests pass on `DockerSandbox` (same tests as `LocalSandbox`, plus network, memory, PID, cleanup tests)
- [ ] For 10 pilot instances the gold tests are F→P in my oracle, and the official harness agrees on all 10
- [ ] `.git` inside the sandbox has exactly one commit; a network call from inside fails
- [ ] No containers left after a killed run

**Explain it:** What does each Docker flag defend against? What is the threat model and where does it stop? How could the fix leak into the agent's view, and what blocks each path?

---

## M7. Dev pilot, iteration, pre-registration, freeze

**Goal:** the agent works on real bugs, tuned only on Dev, then frozen.

**Build and run**
- First: generate `dev_30.txt` and `test_150.txt` from `verified_ids.txt` with the seeded script and commit them (pre-registration commit).
- E2: run `A-haiku`, `B0-haiku`, `B1-haiku` on Dev. Push results.
- Read Dev traces, fix prompt, gate, and budget problems. At most 3 rounds.
- Add the optional G3 LLM judge behind a flag.
- Apply the model decision rule (EVAL section 5).
- Tag `eval-freeze-v1`.

**E2 (box)**
```bash
cd ~/repro && git pull && source .venv/bin/activate
set -a; source ~/.repro.env; set +a        # ANTHROPIC_API_KEY lives here, never in the repo
repro eval --config eval/configs/dev.yaml
git add eval/results/dev && git commit -m "E2 dev round N" && git push
```

**Done when**
- [ ] Pre-registration commit exists and predates any run touching Test instances
- [ ] Dev results for each round are in `eval/results/dev/` with config hashes
- [ ] Each change between rounds is one line in `DECISIONS.md` with the Dev number before and after
- [ ] Model decision recorded
- [ ] Tag `eval-freeze-v1` pushed

**Explain it:** Why is tuning on Test a problem even if I do not mean to cheat? What changed between rounds and what did each change buy? Why 150 and not 433?

---

## M8. Mutation scorer + flaky audit

**Goal:** two quality measurements that go beyond pass/fail. Built and tested on fixtures; can run in parallel with M6 and M7.

**Build**
- Six mutation operators, scoping to fix-changed lines with fallback to the enclosing function, seeded cap of 30, host-side generation, sandbox-side execution.
- A small mutation fixture under `tests/fixtures/mutation/`: a fix region that yields exactly 4 valid mutants, one strong test, one weak test.
- `repro mutate`: per-mutant outcomes, score for Repro's tests and for gold tests on the same mutants.
- `repro audit`: 10x on buggy and on fixed, hash seeds 0 to 9, plus the gold-test control.
- Wire both into `metrics.py` and the report.

**Done when**
- [ ] On the mutation fixture, a strong test kills 4 of 4 and a weak test 1 of 4 (T8.3)
- [ ] Mutants land only on fix-changed lines (T8.2)
- [ ] The audit flags the hash-order fixture's careless test and passes its careful test (T8.6)
- [ ] Report shows mutation and flaky sections populated from fixture data

**Explain it:** What does mutation score tell me that fail-before/pass-after does not? Why compare against gold tests instead of quoting the raw score? What flip rate can 10 runs realistically detect?

---

## M9. Final eval, report, failure notes

**Goal:** the numbers.

**E3 (box)**
```bash
cd ~/repro && git fetch --tags && git checkout eval-freeze-v1 && source .venv/bin/activate
set -a; source ~/.repro.env; set +a
repro eval   --config eval/configs/main.yaml          # A-haiku, B0-haiku, B1-haiku on Test
repro eval   --config eval/configs/ablations.yaml     # A-sonnet, B0-sonnet, A-haiku+judge, variance reruns on Test-50
repro audit  --run main
repro mutate --run main
bash scripts/official_score.sh main                   # runs the SWT-bench harness on predictions.jsonl
git checkout -b results-v1 && git add eval/results && git commit -m "E3 final results" && git push -u origin results-v1
```

**Chat session**
- Merge results, run `repro report`, check every item in EVAL section 6.
- Hand-read 20 failures (seeded pick), label them, write the failure notes.
- List every gate-passing WRONG_TEST.

**Done when**
- [ ] All six configs have complete results; infra errors counted, not dropped
- [ ] Harness agreement computed and every disagreement listed
- [ ] Leak canary and memorisation check run over all traces
- [ ] `report.md` generated; targets table marked hit or miss
- [ ] Failure notes written with counts and examples

**Explain it:** Every headline number, its denominator, and its CI. Where the agent fails and why. What I would do next with another $100.

---

## M10. Product layer

**Goal:** someone can understand the result in 90 seconds.

**Build**
- README filled in by `repro report` output (results first).
- `repro run` on one real open-source repo outside SWE-bench, with a `.repro.yml`, documented start to finish.
- 90-second demo: issue text, `repro run`, `repro show` timeline, test fails, apply fix, test passes, cost line.
- `docs/V2_GITHUB_APP.md` expanded from DESIGN section 9.
- `docs/DEFENSE.md`: the 15 questions an interviewer would ask, with my answers.

**Done when**
- [ ] README opens with the generated results table and headline sentence
- [ ] Demo recording linked
- [ ] Real-repo run documented, including what setup it needed
- [ ] No number in the README is typed by hand

---

## M11. Stretch: GitHub Action

Workflow on `issues: opened` with a `bug` label. Runs `repro run` inside the repo's own CI environment, comments the test only if the gate passes, respects a per-repo monthly budget. Demo on a toy repo of mine.

**Done when**
- [ ] Opening a labelled issue on the toy repo produces a comment with a failing test within the budget
- [ ] An issue the gate cannot confirm produces no comment

---

## If time runs short, cut in this order

1. M11 (GitHub Action)
2. `A-haiku+judge` ablation
3. Sonnet ablation (`A-sonnet`, `B0-sonnet`)
4. Variance reruns
5. Real-repo run in M10
6. Shrink Test from 150 to 100 (first 100 of the committed order)

Not cuttable: gate + cheat suite, B0 baseline, pre-registration, cost accounting, flaky audit, mutation score, failure notes.
