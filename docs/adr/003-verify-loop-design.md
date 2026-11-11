# ADR 003: Verify loop — automatic test feedback with revert-on-fail

**Status:** accepted  
**Date:** 2026-11-04  

## Context

Config C gives the worker automatic test feedback after each edit. The question
is what to do when tests fail: keep the edit and show the failure (informational
feedback), or revert the edit and show the failure (corrective feedback).

A secondary question is timeout: each verify cycle runs the full test suite
(~77s), so the agent needs more wall-clock time than Configs A or B.

## Decision

1. **Revert on failure.** After the worker calls `edit_file`, the harness runs
   `run_tests` automatically. If tests pass, the edit is kept. If tests fail,
   the edit is reverted to the pre-edit state and the agent receives the test
   failure output. The agent can then try a different approach with a clean
   starting point.

2. **900s timeout** (vs 600s for Configs A and B). Each verify cycle adds ~77s
   of test runtime. With 4–7 edit attempts per run on harder tasks, the agent
   needs 300–540s of test time alone. A 900s timeout gives the agent enough room
   to iterate without the wall-clock limit being the primary constraint.

3. **200K token limit** (same as Config B). The verify loop's token overhead
   comes from test output injected after each edit, not from the agent's own
   exploration. 200K is sufficient for the worker model (qwen3.7-plus).

## Rationale

**Revert over keep.** Reverting guarantees the codebase is in a known-good state
(passing all existing tests) before each new attempt. Without revert, a failed
edit stays in place and the agent must manually undo it — or worse, make a second
edit on top of a broken first edit. The revert-on-fail approach is simpler for
the agent and produces cleaner transcripts.

The tradeoff: revert discards partial progress. An edit that fixes the target bug
but introduces a test regression is thrown away entirely, even though the
regression might be a minor side effect fixable with a follow-up edit. In
practice this tradeoff proved acceptable for 11 of 12 tasks.

**The exception — dw-001 — reveals a fundamental limitation.** On dw-001, the
agent's correct fix is reverted because existing tests assert the buggy behavior.
The test suite says the wrong JSON-RPC error code is correct, so the verify loop
reverts the right answer. This happened consistently: 4–7 edit attempts per run,
2–4 reverts, all correct fixes reverted. Config B solves dw-001 3/3 without the
verify loop; Config C goes 0/3.

This is not a bug in the design — it is an inherent limitation of using the
existing test suite as an oracle. When tests encode buggy behavior, automatic
revert actively hurts.

**900s over 600s.** At 600s, the agent would hit the timeout after ~4 verify
cycles with no time for exploration. 900s allows 6–8 cycles, which the data
shows is typical for harder tasks. The extra 300s adds no cost (the clock runs
during test execution, not API calls).

## Consequences

- Config C produces more diverse failure modes than Configs A or B. Beyond
  over-exploration (shared with all configs), it adds "wrong fix" (edit passes
  existing tests but fails hidden tests) and "verify revert" (correct fix
  reverted by existing tests).

- The verify-loop paradox (correct fixes reverted by tests encoding buggy
  behavior) is a real limitation of any verify-loop approach on legacy
  codebases. This should be noted when comparing Config C to Configs A and B.

- Test output injected after each edit consumes tokens. On runs with many
  verify cycles, 30–40% of the token budget goes to test output rather than
  agent reasoning. This is a cost of the approach, not a tunable parameter.

- The 900s timeout means Config C runs take longer wall-clock time than
  Configs A or B, even though they use fewer API tokens. This affects total
  benchmark runtime but not cost.
