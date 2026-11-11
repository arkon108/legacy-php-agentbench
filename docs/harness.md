# Benchmark Harness

How the harness drives an LLM agent through a bug-fixing task inside a Docker container, scores the result, and accounts for cost.

## Architecture overview

```
                        Shisa Platform API
                              ^
                              | OpenAI-compatible
                              | chat completions
                              |
  ┌───────────────────────────┼───────────────────────────┐
  │  Host (Python harness)    │                           │
  │                           │                           │
  │  runner.py                │                           │
  │    ├─ AgentLoop ──────────┘                           │
  │    │    sends messages + tool schemas                 │
  │    │    parses tool_calls from response               │
  │    │    calls execute_tool() ───────┐                 │
  │    │    appends tool results        │                 │
  │    │    repeats until done/limit    │                 │
  │    │                               ▼                  │
  │    │                         ┌───────────┐            │
  │    │                         │  Docker   │            │
  │    │   Sandbox.exec() ─────► │ container │            │
  │    │                         │ --network │            │
  │    │   Sandbox.get_diff() ◄─ │   none    │            │
  │    │                         └───────────┘            │
  │    │                                                  │
  │    └─ scorer.py                                       │
  │         spins up fresh containers                     │
  │         applies agent diff + test patch               │
  │         runs fail-to-pass and pass-to-pass            │
  └───────────────────────────────────────────────────────┘
```

The harness talks to the LLM API from the host. Tools execute inside a network-isolated Docker container. The agent never touches the host filesystem, and the container never touches the network. This is the same separation SWE-bench uses.

## The agent loop (`src/agent.py`)

### Initialization

The loop creates an OpenAI client pointed at the Shisa Platform API (`api.shisa.ai/openai/v1`). Credentials come from env vars `SHISA_API_BASE_URL` and `SHISA_API_KEY`. Per-request timeout is 120 seconds.

### Message flow

A run starts with two messages:

1. **System prompt** — tells the agent it is debugging DokuWiki inside `/dokuwiki`, lists the available tools, gives guidelines (minimal fix, run tests, say DONE or GIVE_UP, don't modify test files).
2. **User message** — the task statement, written as a bug report.

Then the loop:

```
while turns < max_turns:
    check wall-clock timeout
    check cumulative token limit
    call the API with full message history + tool schemas
    accumulate token usage
    append assistant message to history
    if "DONE" in response text → break (success)
    if "GIVE_UP" in response text → break (failure)
    if no tool_calls → break (agent stopped naturally)
    for each tool_call:
        parse arguments from JSON
        execute inside the sandbox
        append tool result to history
```

Every API call sends the **full conversation history** — system prompt, user message, all prior assistant messages and tool results. This means prompt tokens grow with each turn. A 14-turn conversation on dw-001 accumulated 209K prompt tokens even though the unique content was far less. This is how billing works and how SWE-bench counts tokens, but it means the token limit is really a context-budget limit, not a content limit.

### Limits

Three configurable limits, checked before each API call:

| Limit | Default | What it means |
|-------|---------|---------------|
| `max_turns` | 30 | Maximum number of API calls (one call = one turn, even if it makes multiple tool calls) |
| `max_tokens` | 200,000 | Cumulative `prompt_tokens + completion_tokens` across all calls |
| `timeout_seconds` | 600 | Wall-clock time from start of `run()` |

If the agent has already applied a fix when it hits a limit, the diff is still extracted and scored. A run can resolve even if it exits via `token_limit`.

### Token accounting

After each API response, the harness reads `response.usage.prompt_tokens` and `response.usage.completion_tokens` and adds them to running totals. At the end, it converts to USD using the pricing table in `src/models.py`. The pricing was taken from `platform.shisa.ai` on 2026-11-04 and converted from JPY at ~150 JPY/USD.

Some models (notably `qwen3.7-plus`) report inflated completion tokens that include internal reasoning/thinking tokens. This inflates the cost calculation. We record whatever the API reports and note this caveat in results.

### Transcript

Every event is logged to a JSONL file — one JSON object per line:

- `{"type": "system", "content": "..."}` — system prompt
- `{"type": "user", "content": "..."}` — bug report
- `{"type": "assistant", "message": {...}, "usage": {...}, "finish_reason": "..."}` — API response
- `{"type": "tool_call", "name": "...", "arguments": {...}}` — what the agent asked to run
- `{"type": "tool_result", "name": "...", "content": "..."}` — what came back (truncated to 2K chars in the log; full output goes to the API)

The transcript is the primary artifact for failure analysis. Each line is self-contained and can be parsed independently.

## Tools (`src/tools.py`)

### Why these five

The toolset is intentionally small. The question the benchmark answers is: can the agent find and fix a bug using basic code-exploration and editing primitives? Giving it a richer toolset (an LSP, a debugger, `git log`) would change what we're measuring.

The five tools map to what a human developer does when handed an unfamiliar bug in an unfamiliar codebase:

1. **Look around** — `list_dir`, `read_file`
2. **Search** — `grep`
3. **Edit** — `edit_file`
4. **Verify** — `run_tests`

This matches SWE-bench's approach and makes results comparable. Adding tools would help the agent but confound the comparison.

### Tool definitions

Each tool is defined as an OpenAI function-calling schema (the `tools` parameter on `chat.completions.create`). The model sees the name, description, and parameter schema, and returns structured `tool_calls` when it wants to use one.

#### `list_dir(path)`

Lists a directory. Implemented as `ls -1` inside the container. Returns one filename per line.

This is the agent's way to orient itself. A typical first move is `list_dir("/dokuwiki")` to see the project structure, then drilling into subdirectories.

#### `read_file(path)`

Reads a file with line numbers. Implemented as `cat -n`. Returns the entire file content, truncated to 10K characters if large (first 5K + last 5K with a `... (truncated) ...` marker).

Line numbers matter — they let the agent reference specific locations in its reasoning and give context to `edit_file`.

#### `grep(pattern, path)`

Searches for a regex pattern across files. Implemented as `grep -rn` (recursive, with line numbers). If `path` is a directory, it searches all files under it.

This is the agent's most powerful navigation tool. In the pilot, both models' first productive move was `grep("32603", "/dokuwiki")` — searching for the error code mentioned in the bug report. That immediately located the buggy line.

#### `edit_file(path, old_str, new_str)`

String replacement: finds `old_str` in the file (must occur exactly once) and replaces it with `new_str`. Returns an error if the string is not found or appears more than once.

The implementation is a PHP one-liner inside the container. The host base64-encodes both strings and passes them as CLI arguments to avoid any shell-escaping issues. PHP was chosen because it's the only scripting language in the container image (no Python, no Ruby — just PHP, Perl, and standard Unix tools).

The "exactly once" constraint is intentional. It forces the agent to provide enough context in `old_str` to uniquely identify the location. This prevents accidental mass-replacements and makes the edit reviewable in the transcript.

Why search-and-replace instead of unified diffs? Two reasons:
- LLMs are better at generating exact string matches than correctly formatted unified diffs (off-by-one in line numbers, wrong context lines).
- The uniqueness constraint gives a clear error signal when the agent targets the wrong location.

#### `run_tests`

Runs the project's test suite. The command is the task's `pass_to_pass` field — for DokuWiki, that's `cd _test && php vendor/bin/phpunit --no-coverage --exclude-group internet`. This runs the full suite minus tests that need network access.

The agent does NOT see the fail-to-pass tests (ADR #1). It sees only the pre-existing test suite at the base commit. This is the same approach SWE-bench uses: the agent must find and fix the bug from the description alone, not by reading a failing test assertion and working backward.

The test suite takes about 77 seconds. The tool has a 180-second timeout.

### Output truncation

All tool outputs are capped at 10,000 characters. For large outputs, the truncation keeps the first 5K and last 5K characters with a middle marker. This prevents a single `read_file` on a large file from blowing up the context window. The transcript log further truncates to 2K chars per tool result for human reviewability — the full output still goes to the API.

## The sandbox (`src/sandbox.py`)

### Container lifecycle

Each benchmark run gets its own Docker container:

1. **Start**: `docker run -d --network none jouzu-bench/dokuwiki:latest bash -c <setup-script>`
2. **Setup script** runs inside the container:
   - Saves `_test/vendor` (PHPUnit + deps, installed by Dockerfile, untracked by git) and `_test/phpunit.xml` (patched by Dockerfile for PHP 8.2 compat) to `/tmp`
   - `git checkout <base_commit>` — detached HEAD at the parent of the fix commit
   - Restores vendor dir and phpunit.xml from `/tmp`
   - Touches `/tmp/.setup_done` sentinel
   - `exec sleep infinity` — keeps the container alive for `docker exec` calls
3. **Wait**: The harness polls for `/tmp/.setup_done` before returning. If the container dies during setup, it reads `docker logs` and raises an error.
4. **Execute**: `Sandbox.exec(command)` runs `docker exec <id> bash -c <command>` and returns `(exit_code, stdout+stderr)`.
5. **Extract diff**: `Sandbox.get_diff()` resets `_test/phpunit.xml` (to exclude the Dockerfile's patch) then runs `git diff`.
6. **Stop**: `docker rm -f <id>`.

The `Sandbox` class is a context manager, so `with Sandbox(commit) as s:` handles cleanup automatically.

### Why `--network none`

The container has no network access. This is a hard requirement:
- The agent must solve the bug from the code alone, not by searching the web or calling APIs.
- It prevents the container from exfiltrating data.
- It matches how SWE-bench isolates runs.

### The phpunit.xml problem

DokuWiki's `phpunit.xml` is tracked by git. The Docker image patches it to suppress PHP 8.2 deprecation warnings (`convertWarningsToExceptions="false"`, etc.). When we `git checkout` to a historical commit, the tracked file reverts to the original without these flags, and the test suite fails with thousands of deprecation-related errors.

The setup script preserves the patched version across checkout. `get_diff()` resets it before extracting the diff so it doesn't pollute the agent's output. The scorer's setup does the same thing.

## Scoring (`src/scorer.py`)

Scoring runs in **fresh containers**, not the agent's container. This ensures the agent's intermediate state (temp files, partial edits that were later reverted) doesn't affect the score.

### Two-step scoring

For each agent run, the scorer creates two ephemeral containers (`docker run --rm`):

**Step 1 — Fail-to-pass tests:**
1. Checkout the base commit (same setup as the agent's container)
2. Apply the agent's diff via bind-mounted tmpfile
3. Apply the test-only changes from the fix commit (`git diff base..fix -- '_test/**'`)
4. Run the fail-to-pass tests (filtered by `--filter`)
5. Must PASS — these are the tests that verify the bug is fixed

**Step 2 — Pass-to-pass tests:**
1. Same setup as step 1
2. Run the full test suite
3. Must PASS — verifies the agent didn't break anything else

A task is **resolved** only if both steps pass.

### Why the test patch is applied during scoring

The agent never sees the fail-to-pass tests (ADR #1). These tests are part of the fix commit — they were written by the developer alongside the fix. During scoring, we apply just the test-file changes from the fix commit on top of the agent's code changes. This way:
- The agent's code fix is tested against the developer's test expectations
- The agent couldn't have cheated by reading the test assertions
- The methodology matches SWE-bench exactly

### How the agent's diff gets into the scoring container

The agent's diff is written to a temporary file on the host and bind-mounted into the container as `/tmp/agent.patch:ro`. The scoring script checks if the patch is non-empty (`-s`), applies it with `git apply`, and reports `AGENT_DIFF_FAILED` if it doesn't apply cleanly.

An earlier approach embedded the diff in a bash heredoc, but that broke on diffs containing the heredoc delimiter or special shell characters. The bind-mount approach is immune to content issues.
