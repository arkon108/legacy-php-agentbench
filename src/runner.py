"""Main runner: load tasks, run agent, score, write results."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from .agent import AgentLoop
from .agent_b import WorkerJudgeLoop
from .agent_c import VerifyLoop
from .models import RunLimits, RunResult, compute_cost
from .sandbox import Sandbox
from .scorer import score_run

TASKS_DIR = Path(__file__).parent.parent / "tasks"
RESULTS_DIR = Path(__file__).parent.parent / "results"

# Config defaults
CONFIG_DEFAULTS = {
    "A": {"model": "qwen3.8-max"},
    "B": {"worker_model": "qwen3.7-plus", "judge_model": "glm-5.3"},
    "C": {"model": "qwen3.7-plus"},
    "D": {"worker_model": "glm-5.3", "judge_model": "kimi-k3"},
}


def load_task(task_id: str) -> dict:
    path = TASKS_DIR / f"{task_id}.json"
    with open(path) as f:
        return json.load(f)


def run_single(
    task_id: str,
    config_name: str = "A",
    run_number: int = 1,
    limits: RunLimits | None = None,
    temperature: float = 0.0,
    model_override: str | None = None,
    judge_model_override: str | None = None,
) -> RunResult:
    if limits is None:
        limits = RunLimits()

    task = load_task(task_id)
    run_dir = RESULTS_DIR / config_name / task_id / f"run_{run_number}"
    run_dir.mkdir(parents=True, exist_ok=True)

    transcript_path = str(run_dir / "transcript.jsonl")

    defaults = CONFIG_DEFAULTS.get(config_name, {})

    with Sandbox(task["base_commit"]) as sandbox:
        if config_name in ("B", "D"):
            worker_model = model_override or defaults["worker_model"]
            judge_model = judge_model_override or defaults["judge_model"]
            print(f"[{task_id}] config={config_name} run={run_number} worker={worker_model} judge={judge_model}")

            agent = WorkerJudgeLoop(
                worker_model=worker_model,
                judge_model=judge_model,
                sandbox=sandbox,
                pass_to_pass_cmd=task["pass_to_pass"],
                limits=limits,
                transcript_path=transcript_path,
                temperature=temperature,
            )
            result = agent.run(task["statement"])

        elif config_name == "C":
            model_id = model_override or defaults["model"]
            print(f"[{task_id}] config=C run={run_number} model={model_id}")

            agent = VerifyLoop(
                model_id=model_id,
                sandbox=sandbox,
                pass_to_pass_cmd=task["pass_to_pass"],
                limits=limits,
                transcript_path=transcript_path,
                temperature=temperature,
            )
            result = agent.run(task["statement"])

        else:
            model_id = model_override or defaults.get("model", "qwen3.8-max")
            print(f"[{task_id}] config={config_name} run={run_number} model={model_id}")

            agent = AgentLoop(
                model_id=model_id,
                sandbox=sandbox,
                pass_to_pass_cmd=task["pass_to_pass"],
                limits=limits,
                transcript_path=transcript_path,
                temperature=temperature,
            )
            result = agent.run(task["statement"])

    model_id_for_result = (
        model_override or defaults.get("worker_model") or defaults.get("model", "unknown")
    )

    print(f"  agent: done={result['done']} turns={result['turns']} "
          f"tokens={result['tokens_in']}+{result['tokens_out']} "
          f"cost=${result['cost_usd']:.4f} time={result['wall_time_seconds']}s")

    # Save agent diff
    diff_path = run_dir / "agent.diff"
    diff_path.write_text(result["diff"])

    # Score
    print(f"  scoring...")
    score = score_run(
        base_commit=task["base_commit"],
        fix_commit=task["fix_commit"],
        agent_diff=result["diff"],
        fail_to_pass=task["fail_to_pass"],
        pass_to_pass_cmd=task["pass_to_pass"],
        gold_patch_path=task["gold_patch"],
    )

    print(f"  f2p={'PASS' if score['fail_to_pass_passed'] else 'FAIL'} "
          f"p2p={'PASS' if score['pass_to_pass_passed'] else 'FAIL'} "
          f"resolved={score['resolved']}")

    run_result = RunResult(
        task_id=task_id,
        config=config_name,
        run_number=run_number,
        model_id=model_id_for_result,
        resolved=score["resolved"],
        fail_to_pass_passed=score["fail_to_pass_passed"],
        pass_to_pass_passed=score["pass_to_pass_passed"],
        tokens_in=result["tokens_in"],
        tokens_out=result["tokens_out"],
        cost_usd=result["cost_usd"],
        wall_time_seconds=result["wall_time_seconds"],
        turns=result["turns"],
        diff=result["diff"],
        error=result.get("error") or score.get("error"),
    )

    # Save result JSON
    result_path = run_dir / "result.json"
    result_path.write_text(run_result.model_dump_json(indent=2))

    return run_result


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Run benchmark")
    parser.add_argument("--config", default="A", help="Configuration name (A, B, C, or D)")
    parser.add_argument("--tasks", default="dw-001", help="Comma-separated task IDs or 'all'")
    parser.add_argument("--runs", type=int, default=1, help="Runs per task")
    parser.add_argument("--model", default=None, help="Worker model ID (overrides config default)")
    parser.add_argument("--judge-model", default=None, help="Judge model ID (config B only)")
    parser.add_argument("--max-turns", type=int, default=30)
    parser.add_argument("--max-tokens", type=int, default=200_000)
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--temperature", type=float, default=None,
                        help="Temperature (default: 0.0 for run 1, 0.2 for runs 2+)")
    args = parser.parse_args()

    # Resolve tasks
    if args.tasks == "all":
        task_ids = sorted(
            p.stem for p in TASKS_DIR.glob("dw-*.json")
        )
    else:
        task_ids = [t.strip() for t in args.tasks.split(",")]

    base_timeout = args.timeout
    max_tokens = args.max_tokens
    if args.config == "A" and max_tokens == 200_000:
        max_tokens = 500_000
    limits = RunLimits(
        max_turns=args.max_turns,
        max_tokens=max_tokens,
        timeout_seconds=base_timeout if args.config != "C" else max(base_timeout, 900),
    )

    results = []
    for task_id in task_ids:
        for run_num in range(1, args.runs + 1):
            # Temperature: 0.0 for run 1, 0.2 for runs 2+, unless overridden
            if args.temperature is not None:
                temp = args.temperature
            else:
                temp = 0.0 if run_num == 1 else 0.2

            r = run_single(
                task_id=task_id,
                config_name=args.config,
                run_number=run_num,
                limits=limits,
                temperature=temp,
                model_override=args.model,
                judge_model_override=args.judge_model,
            )
            results.append(r)

    # Summary
    print(f"\n{'='*60}")
    resolved = sum(1 for r in results if r.resolved)
    total_cost = sum(r.cost_usd for r in results)
    print(f"Results: {resolved}/{len(results)} resolved, total cost ${total_cost:.4f}")
    for r in results:
        print(f"  {r.task_id} run {r.run_number}: "
              f"resolved={r.resolved} cost=${r.cost_usd:.4f} "
              f"turns={r.turns} time={r.wall_time_seconds}s")

    # Save aggregate
    agg_path = RESULTS_DIR / args.config / "summary.json"
    agg_path.parent.mkdir(parents=True, exist_ok=True)
    agg_path.write_text(json.dumps(
        [r.model_dump() for r in results],
        indent=2, ensure_ascii=False,
    ))


if __name__ == "__main__":
    main()
