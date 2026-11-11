"""Tool definitions and execution for the agent."""

from __future__ import annotations

from typing import Any

from .sandbox import Sandbox

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "list_dir",
            "description": "List the contents of a directory. Returns file names, one per line.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Absolute path to the directory (e.g. /dokuwiki/inc)",
                    }
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read the contents of a file. Returns the file content with line numbers.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Absolute path to the file",
                    }
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "grep",
            "description": "Search for a pattern in files. Returns matching lines with file paths and line numbers.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Grep pattern (basic regex)",
                    },
                    "path": {
                        "type": "string",
                        "description": "File or directory path to search in",
                    },
                },
                "required": ["pattern", "path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "Edit a file by replacing an exact string with a new string. The old_str must appear exactly once in the file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Absolute path to the file",
                    },
                    "old_str": {
                        "type": "string",
                        "description": "The exact string to find and replace (must occur exactly once)",
                    },
                    "new_str": {
                        "type": "string",
                        "description": "The replacement string",
                    },
                },
                "required": ["path", "old_str", "new_str"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_tests",
            "description": "Run the project's test suite. Returns test output and exit code. This runs the existing pass-to-pass tests, not the hidden evaluation tests.",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
]


MAX_OUTPUT = 10_000


def _truncate(output: str) -> str:
    if len(output) <= MAX_OUTPUT:
        return output
    half = MAX_OUTPUT // 2
    return output[:half] + "\n\n... (truncated) ...\n\n" + output[-half:]


def execute_tool(sandbox: Sandbox, name: str, arguments: dict[str, Any], pass_to_pass_cmd: str) -> str:
    try:
        if name == "list_dir":
            path = arguments["path"]
            rc, out = sandbox.exec(f"ls -1 {path!r}")
            if rc != 0:
                return f"Error: {out.strip()}"
            return _truncate(out)

        elif name == "read_file":
            path = arguments["path"]
            rc, out = sandbox.exec(f"cat -n {path!r}")
            if rc != 0:
                return f"Error: {out.strip()}"
            return _truncate(out)

        elif name == "grep":
            pattern = arguments["pattern"]
            path = arguments["path"]
            rc, out = sandbox.exec(f"grep -rn {pattern!r} {path!r}")
            if rc == 1:
                return "No matches found."
            if rc != 0:
                return f"Error: {out.strip()}"
            return _truncate(out)

        elif name == "edit_file":
            path = arguments["path"]
            if "/_test/" in path or path.startswith("_test/"):
                return "Error: editing test files is not allowed. Only modify source code."
            old_str = arguments["old_str"]
            new_str = arguments["new_str"]
            import base64
            old_b64 = base64.b64encode(old_str.encode()).decode()
            new_b64 = base64.b64encode(new_str.encode()).decode()
            script = (
                f"php -r '\n"
                f"$path = $argv[1];\n"
                f"$old = base64_decode($argv[2]);\n"
                f"$new = base64_decode($argv[3]);\n"
                f"$content = file_get_contents($path);\n"
                f"$count = substr_count($content, $old);\n"
                f"if ($count === 0) {{ fwrite(STDERR, \"Error: old_str not found in file\\n\"); exit(1); }}\n"
                f"if ($count > 1) {{ fwrite(STDERR, \"Error: old_str found $count times (expected 1)\\n\"); exit(1); }}\n"
                f"$pos = strpos($content, $old);\n"
                f"$result = substr($content, 0, $pos) . $new . substr($content, $pos + strlen($old));\n"
                f"file_put_contents($path, $result);\n"
                f"echo \"File edited successfully.\\n\";\n"
                f"' {path!r} '{old_b64}' '{new_b64}'"
            )
            rc, out = sandbox.exec(script)
            if rc != 0:
                return f"Error: {out.strip()}"
            return out.strip() or "File edited successfully."

        elif name == "run_tests":
            rc, out = sandbox.exec(f"cd /dokuwiki && {pass_to_pass_cmd}", timeout=180)
            status = "PASSED" if rc == 0 else "FAILED"
            return _truncate(f"Tests {status} (exit code {rc}):\n{out}")

        else:
            return f"Error: unknown tool '{name}'"

    except Exception as e:
        return f"Error executing {name}: {e}"
