"""Config C: Verify loop. Each edit is kept on test pass, reverted on test fail."""

from __future__ import annotations

import json
import os
import time
from typing import Any

from openai import OpenAI

from .models import RunLimits, compute_cost
from .sandbox import Sandbox
from .tools import TOOL_SCHEMAS, execute_tool

SYSTEM_PROMPT = """You are a senior software engineer debugging a PHP application (DokuWiki).
You have access to tools to explore the codebase and make edits inside a Docker container.

The codebase is at /dokuwiki. You can explore it freely with list_dir, read_file, and grep.
When you find the bug, use edit_file to fix it. Use run_tests to verify your fix passes.

IMPORTANT: After each edit you make, the harness will automatically run the test suite.
- If tests PASS, your edit is kept.
- If tests FAIL, your edit is automatically REVERTED (the file goes back to its previous state).
  You will see the test failure output so you can try a different approach.

Guidelines:
- Read the bug report carefully. Understand what's expected vs actual behavior.
- Explore the relevant source files to understand the code.
- Make the minimal fix. Don't refactor or add unrelated changes.
- After editing, tests run automatically — you don't need to call run_tests yourself (but you can if you want to check the baseline first).
- When you're done (fix applied and tests pass), say "DONE" in your final message.
- If you can't find or fix the bug, say "GIVE_UP" and explain why.

Do NOT modify test files. Only fix source code."""


class VerifyLoop:
    def __init__(
        self,
        model_id: str,
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
        )
        self.model_id = model_id
        self.sandbox = sandbox
        self.pass_to_pass_cmd = pass_to_pass_cmd
        self.limits = limits
        self.temperature = temperature
        self.transcript_path = transcript_path

        self.messages: list[dict[str, Any]] = []
        self.tokens_in = 0
        self.tokens_out = 0
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

    def _call_api(self) -> dict[str, Any]:
        response = self.client.chat.completions.create(
            model=self.model_id,
            messages=self.messages,
            tools=TOOL_SCHEMAS,
            temperature=self.temperature,
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
            "type": "assistant",
            "message": result,
            "usage": {
                "prompt_tokens": usage.prompt_tokens if usage else 0,
                "completion_tokens": usage.completion_tokens if usage else 0,
            },
            "finish_reason": choice.finish_reason,
        })
        return result

    def _run_tests(self) -> tuple[int, str]:
        rc, out = self.sandbox.exec(
            f"cd /dokuwiki && {self.pass_to_pass_cmd}", timeout=180
        )
        return rc, out

    def _revert_file(self, path: str) -> None:
        self.sandbox.exec(f"cd /dokuwiki && git checkout -- {path!r}")

    def _get_modified_files(self) -> list[str]:
        _, out = self.sandbox.exec("cd /dokuwiki && git diff --name-only")
        return [f.strip() for f in out.strip().split("\n") if f.strip()]

    def run(self, task_statement: str) -> dict[str, Any]:
        self.messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Bug report:\n\n{task_statement}"},
        ]
        self._log({"type": "system", "content": SYSTEM_PROMPT})
        self._log({"type": "user", "content": task_statement})

        start_time = time.time()
        done = False
        error = None

        try:
            while self.turns < self.limits.max_turns:
                elapsed = time.time() - start_time
                if elapsed > self.limits.timeout_seconds:
                    error = "timeout"
                    break

                total_tokens = self.tokens_in + self.tokens_out
                if total_tokens > self.limits.max_tokens:
                    error = "token_limit"
                    break

                assistant_msg = self._call_api()
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

                has_edit = any(
                    tc["function"]["name"] == "edit_file"
                    for tc in tool_calls
                )

                edited_files: list[str] = []
                for tc in tool_calls:
                    fn = tc["function"]
                    name = fn["name"]
                    try:
                        arguments = json.loads(fn["arguments"])
                    except json.JSONDecodeError:
                        arguments = {}

                    self._log({"type": "tool_call", "name": name, "arguments": arguments})

                    if name == "edit_file":
                        path = arguments.get("path", "")
                        edited_files.append(path)

                    result_str = execute_tool(
                        self.sandbox, name, arguments, self.pass_to_pass_cmd
                    )
                    self._log({"type": "tool_result", "name": name, "content": result_str[:2000]})

                    self.messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": result_str,
                    })

                # After edits, auto-run tests and keep or revert
                if has_edit and edited_files:
                    self._log({"type": "verify", "action": "running_tests"})
                    rc, test_output = self._run_tests()

                    if rc == 0:
                        self._log({"type": "verify", "action": "keep", "files": edited_files})
                        self.messages.append({
                            "role": "user",
                            "content": (
                                "[VERIFY] Tests PASSED after your edit. Changes kept.\n"
                                f"```\n{test_output[-2000:]}\n```"
                            ),
                        })
                    else:
                        # Revert only the files the agent edited (not phpunit.xml etc)
                        for f in edited_files:
                            rel = f.removeprefix("/dokuwiki/")
                            self._revert_file(rel)

                        self._log({
                            "type": "verify",
                            "action": "revert",
                            "files": edited_files,
                        })
                        self.messages.append({
                            "role": "user",
                            "content": (
                                "[VERIFY] Tests FAILED after your edit. "
                                "All source changes have been REVERTED. "
                                "Try a different approach.\n"
                                f"Test output:\n```\n{test_output[-2000:]}\n```"
                            ),
                        })

        except Exception as e:
            error = f"exception: {e}"

        wall_time = time.time() - start_time
        diff = self.sandbox.get_diff()

        return {
            "done": done,
            "error": error,
            "diff": diff,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "cost_usd": compute_cost(self.model_id, self.tokens_in, self.tokens_out),
            "wall_time_seconds": round(wall_time, 1),
            "turns": self.turns,
        }
