#!/usr/bin/env python3
"""Validate every task in tasks/*.json against the Docker image.

For each task:
1. Checkout the base_commit (parent of the fix).
2. Apply only the test diff from the fix commit.
3. Run the fail_to_pass tests — they must FAIL.
4. Apply the gold patch — the fail_to_pass tests must PASS.
5. Run the full suite — it must stay green.

Requires: Docker, the jouzu-bench/dokuwiki:latest image, and the DokuWiki
clone in vendor/dokuwiki with full history.
"""

import json
import subprocess
import sys
from pathlib import Path

TASKS_DIR = Path(__file__).parent / "tasks"
REPO_DIR = Path(__file__).parent / "vendor" / "dokuwiki"
IMAGE = "jouzu-bench/dokuwiki:latest"

# Common setup script preserving _test/vendor and phpunit.xml across checkout
SETUP_TEMPLATE = """
cd /dokuwiki
cp -a _test/vendor /tmp/test_vendor
cp _test/phpunit.xml /tmp/phpunit.xml
git checkout {base_commit} 2>/dev/null
rm -rf _test/vendor
mv /tmp/test_vendor _test/vendor
cp /tmp/phpunit.xml _test/phpunit.xml
"""


def run_docker(script: str, timeout: int = 300) -> tuple[int, str]:
    result = subprocess.run(
        ["docker", "run", "--rm", "--network", "none", IMAGE, "bash", "-c", script],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    output = result.stdout + result.stderr
    return result.returncode, output


def resolve_commit(short_sha: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(REPO_DIR), "rev-parse", short_sha],
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def load_task(path: Path) -> dict:
    with open(path) as f:
        return json.load(f)


def phpunit_filter(test_ids: list[str]) -> str:
    methods = []
    for tid in test_ids:
        # Extract just the method name from Class::method
        if "::" in tid:
            methods.append(tid.split("::")[-1])
        else:
            methods.append(tid)
    return "|".join(methods)


def validate_task(task: dict, verbose: bool = False) -> tuple[bool, str]:
    task_id = task["id"]
    base = resolve_commit(task["base_commit"])
    fix = resolve_commit(task["fix_commit"])
    f2p_filter = phpunit_filter(task["fail_to_pass"])
    setup = SETUP_TEMPLATE.format(base_commit=base)

    # Step 1: fail-to-pass must fail on broken code (parent + test patch only)
    script = f"""
        {setup}
        git diff {base}..{fix} -- '_test/**' > /tmp/test.patch
        git apply /tmp/test.patch 2>&1 || {{ echo 'PATCH_APPLY_FAILED'; exit 99; }}
        cd _test
        php vendor/bin/phpunit --no-coverage --filter '{f2p_filter}' 2>&1
    """
    rc, output = run_docker(script)
    if "PATCH_APPLY_FAILED" in output:
        return False, f"{task_id}: test patch failed to apply"
    if rc == 0:
        return False, f"{task_id}: STEP 1 FAIL — tests pass on broken code"
    if verbose:
        print(f"  {task_id} step 1 OK: tests fail on broken code (rc={rc})")

    # Step 2: fail-to-pass must pass after gold patch
    script = f"""
        {setup}
        git diff {base}..{fix} > /tmp/gold.patch
        git apply /tmp/gold.patch 2>&1
        cd _test
        php vendor/bin/phpunit --no-coverage --filter '{f2p_filter}' 2>&1
    """
    rc, output = run_docker(script)
    if rc != 0:
        return False, f"{task_id}: STEP 2 FAIL — tests fail after gold patch\n{output[-500:]}"
    if verbose:
        print(f"  {task_id} step 2 OK: tests pass after gold patch")

    # Step 3: full suite must be green after gold patch
    script = f"""
        {setup}
        git diff {base}..{fix} > /tmp/gold.patch
        git apply /tmp/gold.patch 2>&1
        cd _test
        php vendor/bin/phpunit --no-coverage --exclude-group internet 2>&1
    """
    rc, output = run_docker(script, timeout=300)
    if rc != 0:
        tail = output[-500:]
        return False, f"{task_id}: STEP 3 FAIL — full suite regressions\n{tail}"
    if verbose:
        for line in output.splitlines():
            if "OK" in line or "Tests:" in line or "Time:" in line:
                print(f"  {task_id} step 3: {line.strip()}")

    return True, f"{task_id}: VALIDATED"


def main():
    verbose = "--verbose" in sys.argv or "-v" in sys.argv
    task_files = sorted(TASKS_DIR.glob("dw-*.json"))

    if not task_files:
        print("No task files found in tasks/")
        sys.exit(1)

    # Optionally validate a single task
    target = None
    for arg in sys.argv[1:]:
        if arg.startswith("dw-"):
            target = arg
            break

    results = {"pass": [], "fail": []}

    for tf in task_files:
        task = load_task(tf)
        if target and task["id"] != target:
            continue

        print(f"Validating {task['id']}...")
        ok, msg = validate_task(task, verbose=verbose)
        if ok:
            results["pass"].append(task["id"])
            print(f"  PASS: {msg}")
        else:
            results["fail"].append(task["id"])
            print(f"  FAIL: {msg}")

    print(f"\n{'='*60}")
    print(f"Results: {len(results['pass'])} passed, {len(results['fail'])} failed")
    if results["fail"]:
        print(f"Failed: {', '.join(results['fail'])}")
        sys.exit(1)
    else:
        print("All tasks validated successfully.")


if __name__ == "__main__":
    main()
