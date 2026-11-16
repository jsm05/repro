# Repro: Risks

What can go wrong, how likely, how bad, and what the plan does about it. Ordered by how much damage each could do to the project's claims.

L = likelihood, I = impact (H / M / L).

## A. Risks to the validity of the numbers

| # | Risk | L | I | What the plan does | How we would notice |
|---|---|---|---|---|---|
| R1 | **The fix leaks to the agent.** Paths: sealed dataset fields in a prompt, later issue comments (`hints_text`), names of the failing tests, future commits reachable in `.git`, network access, a newer copy of the library in site-packages. | M | H | `AgentView` vs `SealedView` types. `.git` replaced with a single commit. `--network none`. Loader never passes `hints_text` or fail-to-pass names. | Canary scan over every prompt and trace for fix hunks, gold test bodies, and test names (T6.6, V9.3). T6.D6 checks git history inside the container. |
| R2 | **Training-data contamination.** SWE-bench repos, fixes, and tests are public and old. The model may have memorised the gold tests. Absolute S is inflated for every system on this benchmark. | H | M | State it in the README. Lean on the paired comparison (agent vs single prompt, same model, same contamination). Report the memorisation check: similarity of each generated test to the gold test, and S with high-similarity cases removed. | Share of generated tests above 0.8 similarity to gold. |
| R3 | **Tuning on the test set.** Prompt and gate changes made after seeing Test results would make the headline number meaningless. | M | H | Dev and Test lists committed before any model run on Test. Freeze tag. `repro eval` refuses Test runs off a freeze tag (T7.3) and refuses Test instances in dev configs (T7.4). | Git history: pre-registration commit and freeze tag predate result files. |
| R4 | **My harness disagrees with the official one.** Different images, different test selection, different outcome parsing. | M | H | Official SWT-bench harness is the source of truth for S. Reuse `swebench` test specs and parsers. Prefer the harness-built images for the sandbox. Gold agreement on 10 pilots before anything else, then on all Test instances. | Harness agreement rate and the list of disagreements (target at least 97%). |
| R5 | **Sample too small to support the claim.** 150 instances gives about plus or minus 8 points. A small agent-vs-baseline gap could be noise. | M | M | Wilson CIs on everything. Paired exact McNemar for agent vs baseline. Variance reruns on Test-50. Never claim a difference the test does not support. | CI width and p-value in the report. |
| R6 | **Unfair comparison to published results.** Different model, sample vs full set, self-run vs verified, unit-test vs script mode. | H | M | Comparison table in EVAL section 7 says exactly what is and is not comparable. README never states or implies a rank. | Review checklist in M10. |
| R7 | **The gate rejects correct tests or passes wrong ones more than expected.** | H | M | This is a measured quantity, not a defect to hide: gate precision, recall cost, per-check rejection counts. Tune rules on Dev only. LLM judge as a priced ablation. | H3 numbers; hand-labelled gate misses in the failure notes. |
| R8 | **Flaky environments blamed on Repro (or the reverse).** Some benchmark instances are unstable on their own. | M | M | SWT-bench already filters unreliable instances. Gold tests get the same 10x audit as a control. | Gold-test flaky rows in `audit.jsonl`. |
| R9 | **Mutation score misread.** Equivalent mutants cannot be killed; a low raw score may mean nothing. Old Python syntax may break mutant generation. | M | M | Always report next to the gold tests' score on the same mutants. Scope to fix-changed lines. Discard mutants that do not compile. State the caveat beside the number. | `score_gold` well below 100% signals equivalent mutants. |
| R10 | **Model nondeterminism and version drift.** Reruns differ; the model may be updated or retired after the run. | H | L | Pin exact model IDs in every row. Variance reruns. Cassettes and full traces committed so results can be re-scored without the API. | Spread across the 3 reruns on Test-50. |

## B. Risks to getting it built

| # | Risk | L | I | What the plan does | How we would notice |
|---|---|---|---|---|---|
| R11 | **Build sessions cannot run Docker or reach the dataset.** Checked 2026-11-16: the chat workspace has no Docker daemon and no access to Docker Hub, GHCR, or Hugging Face. | Certain | H | The whole design: `Sandbox`, `LLMClient`, `InstanceSource` interfaces with fakes; fixture repos; cassettes; contract tests that run on both backends; a separate eval-box track with copy-paste commands. | Already known. |
| R12 | **Eval box problems.** About 120 GB of images, x86_64 required, long first build, Apple Silicon only experimental. | M | H | E0 happens right after M1, before any code depends on it. Rented x86_64 VM with 250 GB. Notes on disk and time recorded in `eval/box-notes.md`. | E0 fails or takes more than half a day. |
| R13 | **Fakes drift from real behaviour.** Tests pass in chat, things break on the box. | M | M | Contract tests are one suite run against `LocalSandbox` and `DockerSandbox`. `FakeLLM` scripts are replaced by recorded cassettes from real runs as soon as they exist (M5). First box run is 10 instances, not 150. | E1 failures. |
| R14 | **Non-pytest test runners.** Django is a large share of Verified and uses its own runner; sympy too. Test placement rules differ per repo. | H | M | Reuse `swebench` commands and log parsers (T6.5). Agent prompt tells it to copy the nearest existing test's style and location. `check` returns clear "not collected" feedback. | Per-repo S breakdown in the report; "placement or runner problem" count in failure notes. |
| R15 | **Fix needs a rebuild** (compiled extensions) so "apply patch, run test" gives wrong results. | L | M | Oracle follows the `swebench` eval steps for the instance, including reinstall where the spec says so. Gold agreement check covers it. | Gold test not F→P on that instance. |
| R16 | **Scope creep.** Ten milestones plus box work, on top of two internships and two other projects. | H | M | One milestone per session, explicit "not in this milestone" lists, a fixed cut order in MILESTONES.md, and a not-cuttable list. | A milestone needing a third session. |
| R17 | **Chat workspace does not persist.** Anything not pushed is lost. | H | M | Every session ends with a push. Session kickoff prompt starts with a clone. Decisions go in `DECISIONS.md` in the repo. | Missing work at the next clone. |
| R18 | **The small model is too weak.** Haiku 5.5 may not clear the floor. | M | M | Decision rule set in advance (EVAL section 5): below 35% on Dev after two rounds, switch primary to Sonnet 5.5 and shrink Test to 100. | Dev S. |

## C. Cost and safety

| # | Risk | L | I | What the plan does | How we would notice |
|---|---|---|---|---|---|
| R19 | **Cost overrun.** A loop bug or a long-context run burns the budget. | M | H | Pre-emptive per-bug ceiling, run-level cap of $80, 90K prompt cap to avoid the Haiku 5.5 price tier, property test on the ledger (T3.3), resume that never pays twice (T4.6). Also set a spend limit in the API console as a backstop outside my code. | Ledger total; console spend alerts. |
| R20 | **Stale prices.** The pricing file is wrong, so reported cost is wrong. | M | M | `verified_on` date in `pricing.yaml`, warning after 60 days, live check that ledger cost equals price times API-reported usage (T3.L1). Reconcile the ledger with the API console bill after the final run. | Ledger vs console mismatch. |
| R21 | **Untrusted code escapes the sandbox.** Repo code and model-written commands run on my machine. | L | H | Docker with no network, dropped capabilities, no host mounts, no Docker socket, resource limits. Throwaway VM with nothing else on it. Never `LocalSandbox` for anything but fixtures. Documented limit: not safe against kernel-level container escapes; a public GitHub App would need gVisor or Firecracker. | T6.D2 to T6.D5. |
| R22 | **API key leak.** Key pasted in a chat, committed, or written to a trace. | M | H | Key lives only in `~/.repro.env` on the box. Never inside a container (T6.D9). Never in artefacts (T3.14). `.gitignore` plus a CI check for key-shaped strings. Never paste the key into a chat session. | CI secret check; rotate the key if in doubt. |
| R23 | **Prompt injection through issue text or repo files.** "Ignore your instructions and..." | M | L (v1), H (v2) | In v1 the agent has no network, no secrets, and can only emit additions to test files, so the worst case is wasted budget or a wrong test, both bounded. Tested in T5.17. For the v2 GitHub App: issue text is treated as data, never interpolated into shell commands (T11.3), comment only when the gate passes. | Traces with unusual tool use. |
| R24 | **Leaked containers or a full disk on the box.** | M | L | Label-based reaper at start and exit (T6.3, T6.D7). `docker system df` in the box notes. | Disk usage creeping up between runs. |

## D. Positioning

| # | Risk | L | I | What the plan does |
|---|---|---|---|---|
| R25 | **"Why not just use Claude Code? It already scores 88%."** | Certain | M | Agree, then answer: the model is not the contribution. The harness is. Repro reports what the leaderboard does not: cost per bug, precision of "reproduced" with no fix available, flaky rate, mutation score against human tests. And I can explain every line and every number. `docs/DEFENSE.md` in M10 holds the prepared answers. |
| R26 | **The headline number looks low next to the leaderboard.** | H | M | Lead with the trio (S, cost per bug, gate precision), not S alone. Show the cost-vs-S curve and the Sonnet ablation to show the tradeoff is a choice. |
| R27 | **Results are disappointing** (agent barely beats the single prompt). | M | M | That is still a result. The plan requires failure notes and a per-check breakdown, so the write-up explains why. Do not tune after the fact to rescue it. |
| R28 | **Real repos are much harder than the benchmark** because no one has built the environment. | Certain | M | Say so. v1 needs `.repro.yml` with an image and a test command. M10 documents one real-repo run including the setup effort. v2 design reuses the repo's CI environment. |

## Things explicitly not defended against

- Container-escape exploits (R21).
- A model provider changing behaviour under a fixed model ID.
- Benchmark label errors in SWT-bench Verified itself (counted against Repro like any other failure).
- Low-rate flakiness below what 10 reruns can detect (EVAL H4 power table).
