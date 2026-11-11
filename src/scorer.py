"""Score agent runs: apply diff, run fail-to-pass and pass-to-pass tests."""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from .sandbox import IMAGE, REPO_DIR, resolve_commit

SETUP_TEMPLATE = """
cd /dokuwiki
cp -a _test/vendor /tmp/test_vendor
cp _test/phpunit.xml /tmp/phpunit.xml
git checkout {base_commit} 2>/dev/null
rm -rf _test/vendor
mv /tmp/test_vendor _test/vendor
cp /tmp/phpunit.xml _test/phpunit.xml
"""


def phpunit_filter(test_ids: list[str]) -> str:
    methods = []
    for tid in test_ids:
        if "::" in tid:
            methods.append(tid.split("::")[-1])
        else:
            methods.append(tid)
    return "|".join(methods)


def _run_scoring_container(
    setup: str,
    agent_diff: str,
    base: str,
    fix: str,
    test_cmd: str,
    timeout: int = 300,
) -> tuple[int, str]:
    """Run a scoring step in a fresh container, piping the agent diff via a volume mount."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".patch", delete=False) as f:
        f.write(agent_diff)
        diff_path = f.name

    script = f"""
        {setup}
        # Apply agent's diff
        if [ -s /tmp/agent.patch ]; then
            git apply /tmp/agent.patch 2>&1
            if [ $? -ne 0 ]; then echo 'AGENT_DIFF_FAILED'; exit 99; fi
        fi
        # Apply the test-only changes from the fix commit
        git diff {base}..{fix} -- '_test/**' > /tmp/test.patch
        git apply /tmp/test.patch 2>&1 || true
        {test_cmd}
    """

    result = subprocess.run(
        [
            "docker", "run", "--rm", "--network", "none",
            "-v", f"{diff_path}:/tmp/agent.patch:ro",
            IMAGE, "bash", "-c", script,
        ],
        capture_output=True, text=True, timeout=timeout,
    )
    Path(diff_path).unlink(missing_ok=True)
    return result.returncode, result.stdout + result.stderr


def score_run(
    base_commit: str,
    fix_commit: str,
    agent_diff: str,
    fail_to_pass: list[str],
    pass_to_pass_cmd: str,
    gold_patch_path: str,
) -> dict[str, bool | str]:
    base = resolve_commit(base_commit)
    fix = resolve_commit(fix_commit)
    setup = SETUP_TEMPLATE.format(base_commit=base)
    f2p_filter = phpunit_filter(fail_to_pass)

    # Step 1: fail-to-pass tests
    f2p_cmd = f"cd _test && php vendor/bin/phpunit --no-coverage --filter '{f2p_filter}' 2>&1"
    rc, f2p_output = _run_scoring_container(setup, agent_diff, base, fix, f2p_cmd)
    f2p_passed = rc == 0

    if "AGENT_DIFF_FAILED" in f2p_output:
        return {
            "fail_to_pass_passed": False,
            "pass_to_pass_passed": False,
            "resolved": False,
            "error": "agent diff failed to apply",
            "f2p_output": f2p_output[-1000:],
            "p2p_output": "",
        }

    # Step 2: pass-to-pass (full suite)
    p2p_cmd = f"cd /dokuwiki && {pass_to_pass_cmd} 2>&1"
    rc, p2p_output = _run_scoring_container(setup, agent_diff, base, fix, p2p_cmd, timeout=300)
    p2p_passed = rc == 0

    return {
        "fail_to_pass_passed": f2p_passed,
        "pass_to_pass_passed": p2p_passed,
        "resolved": f2p_passed and p2p_passed,
        "error": None,
        "f2p_output": f2p_output[-1000:],
        "p2p_output": p2p_output[-1000:],
    }
