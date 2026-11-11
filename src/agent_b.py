"""Config B: Worker + Judge. Worker produces a patch, judge reviews it."""

from __future__ import annotations

import json
import os
import time
from typing import Any

from openai import OpenAI

from .models import RunLimits, compute_cost
from .sandbox import Sandbox
from .tools import TOOL_SCHEMAS, execute_tool

WORKER_SYSTEM_PROMPT = """You are a senior software engineer debugging a PHP application (DokuWiki).
You have access to tools to explore the codebase and make edits inside a Docker container.

The codebase is at /dokuwiki. You can explore it freely with list_dir, read_file, and grep.
When you find the bug, use edit_file to fix it. Use run_tests to verify your fix passes.

Guidelines:
- Read the bug report carefully. Understand what's expected vs actual behavior.
- Explore the relevant source files to understand the code.
- Make the minimal fix. Don't refactor or add unrelated changes.
- Run tests after editing to verify your fix works.
- When you're done (fix applied and tests pass), say "DONE" in your final message.
- If you can't find or fix the bug, say "GIVE_UP" and explain why.

Do NOT modify test files. Only fix source code."""

JUDGE_SYSTEM_PROMPT = """You are a code review expert. You are reviewing a patch produced by another engineer to fix a bug in DokuWiki (a PHP wiki application).

You will receive:
1. The original bug report
2. The diff (patch) the engineer produced
3. The test output after applying the patch

Your job is to decide: does this patch correctly fix the reported bug?

Respond with one of:
- ACCEPT — the patch looks correct and the tests pass.
- REJECT — the patch is wrong or incomplete. Explain what's wrong and what needs to change. Be specific: name the file, the line, and what the correct fix should be."""


MAX_JUDGE_ROUNDS = 2


class WorkerJudgeLoop:
    def __init__(
        self,
        worker_model: str,
        judge_model: str,
        sandbox: Sandbox,
        pass_to_pass_cmd: str,
        limits: RunLimits,
        transcript_path: str | None = None,
        temperature: float = 0.0,
    ):
        self.client = OpenAI(
            base_url=os.environ["SHISA_API_BASE_URL"],
            api_key=os.environ["SHISA_API_KEY"],
            timeout=120.0,
            max_retries=5,
        )
        self.worker_model = worker_model
        self.judge_model = judge_model
        self.sandbox = sandbox
        self.pass_to_pass_cmd = pass_to_pass_cmd
        self.limits = limits
        self.temperature = temperature
        self.transcript_path = transcript_path

        self.messages: list[dict[str, Any]] = []
        self.tokens_in = 0
        self.tokens_out = 0
        self.judge_tokens_in = 0
        self.judge_tokens_out = 0
        self.turns = 0
        self._log_opened = False

    def _log(self, entry: dict[str, Any]) -> None:
        if not self.transcript_path:
            return
        if not self._log_opened:
            mode = "w"
            self._log_opened = True
        else:
            mode = "a"
        with open(self.transcript_path, mode) as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def _call_worker(self) -> dict[str, Any]:
        response = self.client.chat.completions.create(
            model=self.worker_model,
            messages=self.messages,
            tools=TOOL_SCHEMAS,
            temperature=self.temperature,
            parallel_tool_calls=False,
        )
        choice = response.choices[0]
        usage = response.usage
        if usage:
            self.tokens_in += usage.prompt_tokens
            self.tokens_out += usage.completion_tokens

        msg = choice.message
        result: dict[str, Any] = {"role": "assistant"}
        if msg.content:
            result["content"] = msg.content
        if msg.tool_calls:
            result["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in msg.tool_calls
            ]

        self._log({
            "type": "worker",
            "message": result,
            "usage": {
                "prompt_tokens": usage.prompt_tokens if usage else 0,
                "completion_tokens": usage.completion_tokens if usage else 0,
            },
            "finish_reason": choice.finish_reason,
        })
        return result

    def _call_judge(self, bug_report: str, diff: str, test_output: str) -> tuple[str, str]:
        judge_prompt = (
            f"## Bug Report\n{bug_report}\n\n"
            f"## Patch\n```diff\n{diff}\n```\n\n"
            f"## Test Output\n```\n{test_output[-3000:]}\n```"
        )
        judge_client = OpenAI(
            base_url=os.environ["SHISA_API_BASE_URL"],
            api_key=os.environ["SHISA_API_KEY"],
            timeout=300.0,
            max_retries=5,
        )
        response = judge_client.chat.completions.create(
            model=self.judge_model,
            messages=[
                {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
                {"role": "user", "content": judge_prompt},
            ],
            temperature=0.0,
        )
        choice = response.choices[0]
        usage = response.usage
        if usage:
            self.judge_tokens_in += usage.prompt_tokens
            self.judge_tokens_out += usage.completion_tokens

        verdict = choice.message.content or ""
        self._log({
            "type": "judge",
            "content": verdict,
            "usage": {
                "prompt_tokens": usage.prompt_tokens if usage else 0,
                "completion_tokens": usage.completion_tokens if usage else 0,
            },
        })

        if "ACCEPT" in verdict.upper().split("\n")[0]:
            return "ACCEPT", verdict
        return "REJECT", verdict

    def _run_worker_loop(self, task_statement: str, start_time: float) -> tuple[bool, str | None]:
        """Run the worker tool loop until DONE, GIVE_UP, or limit."""
        done = False
        error = None

        while self.turns < self.limits.max_turns:
            elapsed = time.time() - start_time
            if elapsed > self.limits.timeout_seconds:
                error = "timeout"
                break

            total_tokens = self.tokens_in + self.tokens_out
            if total_tokens > self.limits.max_tokens:
                error = "token_limit"
                break

            assistant_msg = self._call_worker()
            self.messages.append(assistant_msg)
            self.turns += 1

            content = assistant_msg.get("content", "") or ""
            if "DONE" in content:
                done = True
                break
            if "GIVE_UP" in content:
                error = "gave_up"
                break

            tool_calls = assistant_msg.get("tool_calls")
            if not tool_calls:
                break

            for tc in tool_calls:
                fn = tc["function"]
                name = fn["name"]
                try:
                    arguments = json.loads(fn["arguments"])
                except json.JSONDecodeError:
                    arguments = {}

                self._log({"type": "tool_call", "name": name, "arguments": arguments})

                result_str = execute_tool(
                    self.sandbox, name, arguments, self.pass_to_pass_cmd
                )
                self._log({"type": "tool_result", "name": name, "content": result_str[:2000]})

                self.messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": result_str,
                })

        return done, error

    def run(self, task_statement: str) -> dict[str, Any]:
        self.messages = [
            {"role": "system", "content": WORKER_SYSTEM_PROMPT},
            {"role": "user", "content": f"Bug report:\n\n{task_statement}"},
        ]
        self._log({"type": "system", "content": WORKER_SYSTEM_PROMPT})
        self._log({"type": "user", "content": task_statement})

        start_time = time.time()
        done = False
        error = None

        try:
            for judge_round in range(MAX_JUDGE_ROUNDS + 1):
                done, error = self._run_worker_loop(task_statement, start_time)

                diff = self.sandbox.get_diff()
                if not diff.strip():
                    break

                # Run tests to get output for the judge
                _, test_output = self.sandbox.exec(
                    f"cd /dokuwiki && {self.pass_to_pass_cmd}", timeout=180
                )

                if judge_round >= MAX_JUDGE_ROUNDS:
                    self._log({"type": "judge_skip", "reason": "max_rounds_reached"})
                    break

                # Judge reviews the patch
                try:
                    verdict, feedback = self._call_judge(task_statement, diff, test_output)
                except Exception as e:
                    self._log({"type": "judge_error", "error": str(e)})
                    break

                if verdict == "ACCEPT":
                    done = True
                    break

                # REJECT: send feedback to worker and continue
                self._log({"type": "judge_feedback", "round": judge_round + 1, "verdict": verdict})
                self.messages.append({
                    "role": "user",
                    "content": (
                        f"A code reviewer has rejected your patch. Here is their feedback:\n\n"
                        f"{feedback}\n\n"
                        f"Please revise your fix based on this feedback. "
                        f"Remember to run tests after making changes."
                    ),
                })

        except Exception as e:
            error = f"exception: {e}"

        wall_time = time.time() - start_time
        diff = self.sandbox.get_diff()

        worker_cost = compute_cost(self.worker_model, self.tokens_in, self.tokens_out)
        judge_cost = compute_cost(self.judge_model, self.judge_tokens_in, self.judge_tokens_out)

        return {
            "done": done,
            "error": error,
            "diff": diff,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "judge_tokens_in": self.judge_tokens_in,
            "judge_tokens_out": self.judge_tokens_out,
            "cost_usd": worker_cost + judge_cost,
            "worker_cost_usd": worker_cost,
            "judge_cost_usd": judge_cost,
            "wall_time_seconds": round(wall_time, 1),
            "turns": self.turns,
        }
