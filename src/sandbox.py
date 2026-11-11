"""Docker sandbox for running agent tools inside a container."""

from __future__ import annotations

import subprocess
from pathlib import Path

IMAGE = "jouzu-bench/dokuwiki:latest"
REPO_DIR = Path(__file__).parent.parent / "vendor" / "dokuwiki"

def resolve_commit(short_sha: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(REPO_DIR), "rev-parse", short_sha],
        capture_output=True, text=True,
    )
    return result.stdout.strip()


SETUP_SCRIPT = """\
#!/bin/bash
set -e
cd /dokuwiki
cp -a _test/vendor /tmp/test_vendor
cp _test/phpunit.xml /tmp/phpunit.xml
git checkout {base_commit}
rm -rf _test/vendor
mv /tmp/test_vendor _test/vendor
cp /tmp/phpunit.xml _test/phpunit.xml
touch /tmp/.setup_done
exec sleep infinity
"""


class Sandbox:
    """Manages a Docker container for one benchmark run."""

    def __init__(self, base_commit: str):
        self.base_commit = resolve_commit(base_commit)
        self.container_id: str | None = None

    def start(self) -> None:
        script = SETUP_SCRIPT.format(base_commit=self.base_commit)
        result = subprocess.run(
            [
                "docker", "run", "-d",
                "--network", "none",
                IMAGE,
                "bash", "-c", script,
            ],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Failed to start container: {result.stderr}")
        self.container_id = result.stdout.strip()
        self._wait_for_setup()

    def _wait_for_setup(self, timeout: int = 30) -> None:
        import time
        deadline = time.time() + timeout
        while time.time() < deadline:
            rc, out = self.exec("test -f /tmp/.setup_done && echo ok")
            if rc == 0 and "ok" in out:
                return
            # Check if container is still running
            check = subprocess.run(
                ["docker", "inspect", "-f", "{{.State.Running}}", self.container_id],
                capture_output=True, text=True,
            )
            if check.stdout.strip() != "true":
                logs = subprocess.run(
                    ["docker", "logs", self.container_id],
                    capture_output=True, text=True,
                )
                raise RuntimeError(f"Container died during setup: {logs.stdout + logs.stderr}")
            time.sleep(0.5)
        raise RuntimeError("Container setup timed out")

    def exec(self, command: str, timeout: int = 120) -> tuple[int, str]:
        if not self.container_id:
            raise RuntimeError("Container not started")
        result = subprocess.run(
            ["docker", "exec", self.container_id, "bash", "-c", command],
            capture_output=True, text=True, timeout=timeout,
        )
        output = result.stdout + result.stderr
        return result.returncode, output

    def get_diff(self) -> str:
        if not self.container_id:
            return ""
        self.exec("cd /dokuwiki && git checkout -- _test/phpunit.xml 2>/dev/null")
        _, diff = self.exec("cd /dokuwiki && git diff")
        return diff

    def stop(self) -> None:
        if self.container_id:
            subprocess.run(
                ["docker", "rm", "-f", self.container_id],
                capture_output=True, text=True,
            )
            self.container_id = None

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *args):
        self.stop()
