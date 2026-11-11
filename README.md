# Legacy PHP Agent Benchmark

How well do agent setups built on open models fix real bugs in an old, procedural PHP codebase? This benchmark compares four configurations — a single strong model, two worker+judge pairs with different model families, and a verify loop — on 12 real bug-fix tasks from [DokuWiki](https://github.com/dokuwiki/dokuwiki), a 20-year-old procedural PHP wiki engine.

## Question

How do a single model, worker+judge pairs from different model families, and a verify loop compare at resolving real bugs in legacy PHP, when built on open models through the [Shisa Platform API](https://shisa.ai)?

## Method

### Task construction

Following [SWE-bench](https://arxiv.org/abs/2310.06770):

1. Selected 12 recent bug-fix commits from DokuWiki that touch both source and test files
2. For each task: check out the parent commit (buggy state), apply only the test changes from the fix commit
3. The fail-to-pass tests must fail on the buggy code and pass after the gold patch
4. The full test suite (pass-to-pass) must remain green after the fix
5. **Fail-to-pass tests are hidden** from the agent (as in SWE-bench) — the agent sees only the bug report and existing test suite

### Isolation

Each run gets a fresh Docker container (`jouzu-bench/dokuwiki`) with:
- PHP 8.2, Composer dependencies pre-installed
- No network access (`--network none`)
- The harness talks to the API; the agent's tools execute inside the container

### Agent tools

Five tools: `list_dir`, `read_file`, `grep`, `edit_file`, `run_tests`. The agent cannot edit test files (`_test/` paths are blocked).

### Configurations

| Config | Description | Worker model | Judge model | Token limit | Timeout |
|--------|------------|--------------|-------------|-------------|---------|
| **A** | Single strong model | qwen3.8-max | — | 500K | 600s |
| **B** | Worker + judge (Qwen/GLM) | qwen3.7-plus | glm-5.3 | 200K | 600s |
| **C** | Verify loop | qwen3.7-plus | — | 200K | 900s |
| **D** | Worker + judge (GLM/Kimi) | glm-5.3 | kimi-k3 | 200K | 600s |

- **Config A:** One model runs the tool loop until it declares done or hits a limit.
- **Config B:** A cheaper worker produces a patch. A judge from a different model family reviews the diff and test output, then accepts or returns feedback (at most 2 rounds).
- **Config C:** The worker iterates against the visible test command. After each edit, tests run automatically — on pass the edit is kept, on fail it is reverted and the agent sees the failure output.
- **Config D:** Same worker+judge architecture as B, with a different model pairing: glm-5.3 as worker, kimi-k3 as judge.

Each configuration runs **3 times per task** (36 runs total per config, 144 total). Temperature: 0.0 for run 1, 0.2 for runs 2–3.

### Tasks

| ID | Area | Bug summary |
|----|------|-------------|
| dw-001 | Remote API | Wrong JSON-RPC error code for unknown methods |
| dw-002 | Auth | Numeric usernames broken after strict-comparison fix |
| dw-003 | Search index | Tuple corruption when IDs share decimal prefix |
| dw-004 | Search query | Fatal on short wildcard terms (stack underflow) |
| dw-005 | Feed parser | Transport errors masked as "unsupported status code" |
| dw-006 | Remote API | Anonymous whoAmI returns wrong error type |
| dw-007 | Parser | Table head boundary wrong with row-spanning cells |
| dw-008 | Auth (security) | Loose comparison allows numeric privilege escalation |
| dw-009 | Remote API | ACL self-check fails on case-insensitive backends |
| dw-010 | Feed parser | Repeated HTTP headers cast to "Array" |
| dw-011 | Changelog | Externally restored deleted page stays "deleted" |
| dw-012 | Changelog | Out-of-order external edits pollute recent changes |

## Results

<!-- RESULTS_TABLE_START -->
| Config | Model(s) | Resolved | Total cost | Avg cost/run | Cost/resolved | Avg turns |
|--------|----------|----------|------------|--------------|---------------|-----------|
| **A** (Single model) | qwen3.8-max | 6/36 | $28.94 | $0.80 | $4.82 | 26.0 |
| **B** (Worker + judge) | qwen3.7-plus + glm-5.3 | 9/36 | $3.15 | $0.09 | $0.35 | 12.9 |
| **C** (Verify loop) | qwen3.7-plus | 6/36 | $2.85 | $0.08 | $0.48 | 12.6 |
| **D** (Worker + judge) | glm-5.3 + kimi-k3 | 3/36 | $9.30 | $0.26 | $3.10 | 16.2 |
| **All** | | 24/144 | $44.24 | | | |

### Per-task breakdown

| Task | A | B | C | D |
|------|----|----|----|----|
| dw-001 | 1/3 | 3/3 | 0/3 | 0/3 |
| dw-002 | 0/3 | 0/3 | 0/3 | 0/3 |
| dw-003 | 2/3 | 3/3 | 3/3 | 2/3 |
| dw-004 | 0/3 | 0/3 | 0/3 | 0/3 |
| dw-005 | 0/3 | 0/3 | 0/3 | 0/3 |
| dw-006 | 0/3 | 0/3 | 0/3 | 0/3 |
| dw-007 | 0/3 | 0/3 | 0/3 | 0/3 |
| dw-008 | 0/3 | 0/3 | 0/3 | 0/3 |
| dw-009 | 0/3 | 0/3 | 0/3 | 0/3 |
| dw-010 | 3/3 | 3/3 | 3/3 | 1/3 |
| dw-011 | 0/3 | 0/3 | 0/3 | 0/3 |
| dw-012 | 0/3 | 0/3 | 0/3 | 0/3 |
| **Total** | **6/36** | **9/36** | **6/36** | **3/36** |
<!-- RESULTS_TABLE_END -->

## Failure taxonomy

<!-- FAILURE_TAXONOMY_START -->
### Config A — over-exploration without action

All 30 failed Config A runs share the same pattern: the model locates the correct source file but never calls `edit_file`.

| Exit reason | Count | Description |
|-------------|-------|-------------|
| Token limit | 17/30 | Exhausted 500K token budget on read/grep |
| Turn limit | 12/30 | Reached 30 turns without editing |
| Timeout | 1/30 | Hit 600s wall-clock limit |

**Key observations:**
- **0 edit_file calls** across all 30 failures — the model explored but never attempted a fix
- **Correct file found in 12/12 tasks** — localization is not the bottleneck
- **grep dominates:** 20–42 grep calls per failed run, vs 2–10 read_file calls
- The model re-reads the same files and re-greps the same patterns, consuming tokens on redundant exploration

**When the model does act (6 resolved runs):** 1–2 edit_file calls, 1–2 run_tests calls, correct fix every time. The bottleneck is the decision to act, not the ability to fix.

### Config B — mixed failure modes

Config B's cheaper worker (qwen3.7-plus) acts more often than Config A's qwen3.8-max, but still over-explores in most failures.

| Category | Count | Description |
|----------|-------|-------------|
| No edit, token limit | 23/27 | Worker exhausted 200K tokens without editing |
| Wrong fix, judge timeout | 3/27 | Worker produced wrong patch; judge (glm-5.3) timed out reviewing it |
| Edit reverted | 1/27 | Worker edited then reverted before completing |

**Key observations:**
- Config B resolves 3 tasks consistently (dw-001, dw-003, dw-010 all 3/3) vs Config A's 1 task at 3/3
- The worker model (qwen3.7-plus) uses fewer grep calls (avg 3.9 vs Config A's 30) and is more action-oriented
- dw-004 is the only task where Config B produced wrong fixes — the worker made edits but the judge timed out on all 3 attempts
- 26/27 failures still found the correct source file

### Config C — verify-loop paradox and over-exploration

Config C ties Config A at 6/36 but costs 10x less ($2.85 vs $28.94). The verify loop produces more diverse failures.

| Category | Count | Description |
|----------|-------|-------------|
| No edit, token limit | 23/30 | Worker exhausted 200K tokens without editing |
| Wrong fix | 4/30 | Edit passed existing tests but failed hidden fail-to-pass tests |
| Verify revert, token limit | 3/30 | Correct edit reverted by failing tests, then hit token limit |

**Key observations:**
- **dw-001 demonstrates the paradox:** the agent makes 4–7 edit attempts per run, each producing a correct fix that gets reverted because existing tests assert buggy behavior (2–4 reverts per run)
- Config C solves dw-003 and dw-010 (both 3/3) but **not** dw-001 (0/3), which Config B solves 3/3 — the verify loop actively hurts on dw-001
- The verify loop's avg 0.7 edit calls per failed run shows it's more action-oriented than Config A (0.0), but reverts waste the agent's token budget
- 29/30 failures found the correct source file

### Config D — over-exploration with high-cost judge

Config D uses glm-5.3 as worker and kimi-k3 as judge. It resolves only 3/36 (dw-003 2/3, dw-010 1/3) at $9.30 — the second-most-expensive config despite the worst resolved count.

| Category | Count | Description |
|----------|-------|-------------|
| No edit, multi-turn | 28/33 | Worker explored across many turns but never called edit_file |
| No tool calls | 5/33 | Worker responded with text plan only, no tool calls emitted |

**Key observations:**
- glm-5.3 as worker shows the same over-exploration pattern as Config A: high turn counts (avg 16.2) with no edits
- 5/33 failures produced zero tool calls — the model described a plan in text instead of using tools
- Despite using fewer tokens per run than Config A, glm-5.3's higher per-token cost makes Config D 3x more expensive than Config B for fewer resolves
- Config D does not solve dw-001 (0/3), which Config B solves 3/3 — the worker model matters more than the judge
<!-- FAILURE_TAXONOMY_END -->

## Limitations

1. **Small sample size.** 12 tasks, 3 runs each — report counts (e.g. "6/36"), not percentages. Differences between configs may not be statistically significant.

2. **Training data contamination.** DokuWiki is a popular open-source project. The fix commits, commit messages, and associated issues are public and may appear in training data for the models used. We selected recent commits (last 12 months) to reduce but not eliminate this risk.

3. **Single codebase.** Results are specific to DokuWiki's procedural PHP style. Agent performance may differ on other languages, frameworks, or codebase architectures.

4. **Hidden thinking tokens.** qwen3.8-max uses internal thinking tokens that are not exposed in API responses but count toward the token limit. This inflates cost and limits the agent's effective working context — a property of the model, not the benchmark.

5. **Config C verify-loop paradox.** The verify loop reverts edits when tests fail, but some existing tests in DokuWiki assert buggy behavior. A correct fix can be reverted because the pass-to-pass tests (which encode the old bug) fail. This is a genuine limitation of the verify-loop approach when test suites contain tests that depend on buggy behavior.

6. **API-level variability.** The Shisa Platform API routes to specific model versions; model updates during the evaluation window could affect reproducibility.

## How to reproduce

### Prerequisites

- Python 3.12+
- Docker
- A [Shisa Platform API](https://shisa.ai) key

### Setup

```bash
git clone https://github.com/arkon108/legacy-php-agentbench.git
cd legacy-php-agentbench
pip install -r requirements.txt

# Configure API access
cp .env.example .env
# Edit .env with your SHISA_API_BASE_URL and SHISA_API_KEY

# Build the Docker image
docker build -t jouzu-bench/dokuwiki docker/dokuwiki/
```

### Run benchmarks

```bash
# Single task, single run
python3 -m src.runner --config A --tasks dw-001 --runs 1

# Full run for a config
python3 -m src.runner --config A --tasks all --runs 3

# All configs
make bench CONFIG=a TASKS=all RUNS=3
make bench CONFIG=b TASKS=all RUNS=3
make bench CONFIG=c TASKS=all RUNS=3
make bench CONFIG=d TASKS=all RUNS=3
```

### Validate tasks

```bash
python3 validate_tasks.py -v
```

Results are written to `results/<config>/<task>/run_<n>/`.

## Cost

All runs used the [Shisa Platform API](https://shisa.ai) with sponsored credit. Total cost across all configurations: **$44.24**.

## Acknowledgments

Built with [Claude Code](https://docs.anthropic.com/en/docs/claude-code/overview). Models served by [Shisa.AI](https://shisa.ai).
