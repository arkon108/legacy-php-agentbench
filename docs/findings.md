# Findings: Agentic Bug-Fixing on Legacy PHP

*A benchmark of four agent configurations on 12 real bugs in DokuWiki, a 20-year-old procedural PHP wiki engine.*

## Setup

We tested four configurations, each running 3 times on 12 tasks (36 runs per config, 144 total):

- **Config A (single model):** qwen3.8-max with a 500K token limit. The model runs a tool loop (read, grep, list, edit, test) until it declares done or hits a limit.
- **Config B (worker + judge, Qwen/GLM):** qwen3.7-plus as the worker, glm-5.3 as the judge. The judge reviews each patch attempt and accepts or returns feedback (max 2 rounds).
- **Config C (verify loop):** qwen3.7-plus with automatic test verification after each edit. Passing edits are kept; failing edits are reverted.
- **Config D (worker + judge, GLM/Kimi):** glm-5.3 as the worker, kimi-k3 as the judge. Same architecture as Config B with a different model pairing.

All models accessed through the Shisa Platform API. Each run gets a fresh Docker container with no network access.

## Key findings

### 1. The over-exploration problem

Config A's qwen3.8-max resolved 6/36 runs — but in every failed run, the model found the correct source file and never called `edit_file`. It spent 20–42 grep calls per run re-reading code it had already seen, consuming its 500K token budget on understanding rather than acting.

When the model did act (6 runs across 3 tasks), it produced the correct fix every time. The bottleneck is not capability but decision-making: the model can fix the bug, but won't commit to trying.

### 2. The cheaper model wins

Config B (qwen3.7-plus worker + glm-5.3 judge) resolved 9/36 at $3.19 total — compared to Config A's 6/36 at $28.94. The cheaper worker model is more action-oriented: avg 3.9 grep calls per failed run vs Config A's 30, and it actually attempts edits.

| Config | Resolved | Total cost | Cost per resolved |
|--------|----------|------------|-------------------|
| A (qwen3.8-max) | 6/36 | $28.94 | $4.82 |
| B (qwen3.7-plus + glm-5.3) | 9/36 | $3.19 | $0.35 |

Config B costs **14x less per resolved task** while resolving 50% more. All four configs solve dw-010, but Config B is the only one to solve dw-001 consistently (3/3 vs A's 1/3, C's 0/3, D's 0/3).

| Config | Resolved | Total cost | Cost/resolved | Avg turns |
|--------|----------|------------|---------------|-----------|
| A (qwen3.8-max) | 6/36 | $28.94 | $4.82 | 26.0 |
| B (qwen3.7-plus + glm-5.3) | 9/36 | $3.15 | $0.35 | 12.8 |
| C (qwen3.7-plus, verify) | 6/36 | $2.85 | $0.48 | 12.6 |
| D (glm-5.3 + kimi-k3) | 3/36 | $9.30 | $3.10 | 16.2 |

### 3. Config C's verify-loop paradox

Config C resolved 6/36 — tying Config A at a fraction of the cost ($2.85 vs $28.94). But it fails on dw-001 (0/3), which Config B solves 3/3.

The paradox: on dw-001, the agent makes 4–7 edit attempts per run, each producing a correct fix. But existing tests assert the wrong behavior, so the verify loop reverts every correct fix (2–4 reverts per run). The agent burns through its token budget on a cycle of fix → test → revert → try differently.

This is a genuine limitation of the verify-loop approach: it works only when the test suite is authoritative. Legacy codebases often have tests that implicitly depend on buggy behavior.

## Failure taxonomy

### Config A (30 failures)

| Category | Count | Description |
|----------|-------|-------------|
| Over-explored, token limit | 17/30 | Exhausted 500K tokens on grep/read without editing |
| Over-explored, turn limit | 12/30 | Reached 30 turns without editing |
| Over-explored, timeout | 1/30 | Hit 600s wall-clock limit |

All 30: correct file found, 0 edit_file calls, avg 30 grep calls per run.

### Config B (27 failures)

| Category | Count | Description |
|----------|-------|-------------|
| No edit, token limit | 23/27 | Worker exhausted 200K tokens without editing |
| Wrong fix, judge timeout | 3/27 | Worker's patch was wrong; judge timed out |
| Edit reverted | 1/27 | Worker reverted its own edit |

### Config C (30 failures)

| Category | Count | Description |
|----------|-------|-------------|
| No edit, token limit | 23/30 | Worker exhausted 200K tokens without editing |
| Wrong fix | 4/30 | Edit passed existing tests but failed hidden tests |
| Verify revert, token limit | 3/30 | Correct fix reverted by tests, then hit limit |

dw-001 is the verify-loop paradox in action: 4–7 edits per run, 2–4 reverts, all correct fixes reverted by tests encoding the bug.

### Config D (33 failures)

| Category | Count | Description |
|----------|-------|-------------|
| No edit, multi-turn | 28/33 | Worker explored across many turns but never called edit_file |
| No tool calls | 5/33 | Worker responded with text plan only, no tool calls emitted |

glm-5.3 as worker shows the same over-exploration pattern as Config A but at higher per-token cost. 5/33 failures produced zero tool calls — the model described a plan in text instead of using tools.

## Surprises

1. **100% fix accuracy when qwen3.8-max acts.** Every Config A run where the model produced an edit resolved the task. The model's problem is not understanding the bug — it's deciding to attempt a fix.

2. **The cheaper model outperforms the expensive one.** qwen3.7-plus (Config B) resolves 9/36 at $3.19; qwen3.8-max (Config A) resolves 6/36 at $28.94. The "stronger" model spends its budget on understanding; the "weaker" one spends it on acting.

3. **The verify loop can hurt.** Config C resolves 6/36 (tying A) but fails dw-001, which Config B solves 3/3. On dw-001, Config C's agent makes correct fixes that get reverted by tests encoding the bug — then burns tokens retrying. Without the verify loop, Config B's agent produces the same fix and it's accepted.

4. **Localization is solved.** Across all 144 runs, the agent read the correct source file in the vast majority of failures. The bottleneck is everywhere except finding the bug.

5. **grep addiction scales with model size.** Config A averages 30 grep calls per failed run; Configs B and C average 3.9 and 4.9. The larger model's hidden thinking tokens appear to create a compulsion to "understand everything" before acting.

6. **9 of 12 tasks unsolved by all configs.** Only dw-001, dw-003, and dw-010 were solved by any configuration. The remaining 9 tasks (75%) defeated all four agent setups across 108 runs.

7. **The worker model matters more than the judge.** Config B (qwen3.7-plus worker) resolves 9/36; Config D (glm-5.3 worker) resolves 3/36 — same judge architecture, different worker, 3x the difference. glm-5.3 works well as a judge but over-explores as a worker.

## Limitations

- **12 tasks, 3 runs each** — too small for percentage comparisons or statistical significance. Report counts (e.g. "9/36"), not percentages.
- **Training data contamination** — DokuWiki's commits and issues are public; models may have seen them. We used recent commits (last 12 months) to reduce this.
- **Single codebase** — procedural PHP; results may not transfer to other languages, frameworks, or architectures.
- **Hidden thinking tokens** inflate qwen3.8-max costs without being visible in transcripts.
- **Total cost: $44.24** across 144 runs using the Shisa Platform API with sponsored credit.
