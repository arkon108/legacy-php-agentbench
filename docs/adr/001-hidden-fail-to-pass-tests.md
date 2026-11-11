# ADR 001: Hide fail-to-pass tests from the agent

**Status:** accepted  
**Date:** 2026-11-03  

## Context

Each benchmark task has a set of fail-to-pass tests — tests that exercise the
bug and fail before the fix is applied. The agent must produce a patch that
makes these tests pass while keeping the rest of the suite (pass-to-pass) green.

The question is whether the agent can see the fail-to-pass test code.

SWE-bench hides the fail-to-pass tests: the agent sees the bug report and the
codebase at the broken commit, but does not know which tests will be run to
judge its patch. It can run the existing test suite, but the specific tests
that exercise the bug are withheld.

## Decision

**Hide the fail-to-pass tests.** The agent receives:

1. The task statement (a bug report written from the commit message/issue,
   never leaking the fix).
2. The codebase checked out at the parent commit — the broken state.
3. The ability to run the existing test suite via `run_tests`.
4. The ability to read, search, and edit source files.

The fail-to-pass test code is applied only at evaluation time, after the agent
submits its patch.

## Rationale

- **Realism.** In practice, a developer gets a bug report and must both
  understand the problem and produce a correct fix. If the exact failing
  assertion is visible, the task becomes "make this assertion pass" — a
  test-driven exercise, not a bug-fixing exercise.

- **Score calibration.** Visible tests inflate resolved rates. An agent that
  reads `$this->assertSame(0, $file->get_status_code())` knows exactly what
  the output should be; one that reads "the status code is wrong" must
  understand the HTTP client's contract and find the right place to change it.
  Hidden tests produce more informative separation between configurations.

- **Comparability.** SWE-bench hides fail-to-pass tests. Using the same
  protocol lets us compare our results against published numbers (with the
  usual caveats about different codebases and sample sizes).

## Consequences

- The agent's `run_tests` tool runs the suite that existed at the base commit,
  which by definition does not include the fail-to-pass tests (they were added
  in the fix commit). This is the natural state — no special filtering needed.

- Task statements must be written carefully to describe the bug without
  revealing the fix. Each statement is reviewed to ensure it does not contain
  code snippets, line numbers, or specific variable/function names that would
  make the fix trivially locatable.

- Config C (verify loop) lets the agent iterate against the visible test
  command. Since the fail-to-pass tests are hidden, the agent iterates against
  the pass-to-pass suite (catching regressions) but cannot see whether its fix
  addresses the actual bug until evaluation time. This is intentional — the
  verify loop tests whether iterating on regressions alone improves outcomes.
