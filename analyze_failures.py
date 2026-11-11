"""Analyze failure patterns across all configs."""

import json
from collections import Counter, defaultdict
from pathlib import Path

RESULTS_DIR = Path(__file__).parent / "results"
TASKS_DIR = Path(__file__).parent / "tasks"

CONFIGS = ["A", "B", "C", "D"]
TASK_IDS = [f"dw-{i:03d}" for i in range(1, 13)]


def load_results(config: str) -> list[dict]:
    path = RESULTS_DIR / config / "summary.json"
    if not path.exists():
        return []
    return json.load(open(path))


def analyze_transcript(config: str, task_id: str, run: int) -> dict:
    path = RESULTS_DIR / config / task_id / f"run_{run}" / "transcript.jsonl"
    if not path.exists():
        return {}
    msgs = [json.loads(l) for l in open(path)]

    tool_counts = Counter()
    for m in msgs:
        if m.get("type") == "tool_call":
            tool_counts[m["name"]] += 1

    # Check if agent found the gold-patch file
    task = json.load(open(TASKS_DIR / f"{task_id}.json"))
    gold_files = set(task.get("source_files", []))
    read_files = set()
    for m in msgs:
        if m.get("type") == "tool_call" and m.get("name") == "read_file":
            args = m.get("arguments", {})
            if isinstance(args, str):
                args = json.loads(args)
            p = args.get("path", "")
            if "/dokuwiki/" in p:
                p = p.split("/dokuwiki/")[-1]
            read_files.add(p)

    found_correct_file = bool(gold_files & read_files)

    # Check for judge entries (Config B)
    judge_entries = [m for m in msgs if m.get("type") in ("judge", "judge_feedback")]

    # Check for verify entries (Config C)
    verify_entries = [m for m in msgs if m.get("type") in ("verify_pass", "verify_fail", "revert")]

    return {
        "tool_counts": dict(tool_counts),
        "found_correct_file": found_correct_file,
        "total_tool_calls": sum(tool_counts.values()),
        "edit_calls": tool_counts.get("edit_file", 0),
        "test_calls": tool_counts.get("run_tests", 0),
        "grep_calls": tool_counts.get("grep", 0),
        "judge_rounds": len(judge_entries),
        "verify_events": len(verify_entries),
    }


def classify_failure(result: dict, transcript: dict, config: str) -> str:
    """Classify a single failure into a category."""
    err = result.get("error")
    has_edit = transcript.get("edit_calls", 0) > 0
    has_diff = bool(result.get("diff", "").strip())

    if config == "C":
        if transcript.get("verify_events", 0) > 0:
            return "verify_revert"

    if not has_edit and not has_diff:
        if err == "token_limit":
            return "over_explored_token"
        elif err == "timeout":
            return "over_explored_timeout"
        elif result.get("turns", 0) >= 30:
            return "over_explored_turn"
        elif err == "gave_up":
            return "gave_up"
        else:
            return "over_explored_other"

    if has_diff:
        f2p = result.get("fail_to_pass_passed", False)
        p2p = result.get("pass_to_pass_passed", False)
        if not f2p and p2p:
            return "incomplete_fix"
        elif f2p and not p2p:
            return "broke_other_tests"
        elif not f2p and not p2p:
            return "wrong_fix"
        else:
            return "wrong_fix"

    if has_edit and not has_diff:
        return "edit_reverted"

    return "unknown"


def main():
    for config in CONFIGS:
        results = load_results(config)
        if not results:
            continue

        resolved = sum(1 for r in results if r["resolved"])
        total = len(results)
        print(f"\n{'='*60}")
        print(f"Config {config}: {resolved}/{total} resolved")
        print(f"{'='*60}")

        failures = [r for r in results if not r["resolved"]]
        if not failures:
            print("  No failures to analyze!")
            continue

        taxonomy = Counter()
        found_correct_file_count = 0
        total_grep = 0
        total_edit = 0

        for r in failures:
            t = analyze_transcript(config, r["task_id"], r["run_number"])
            category = classify_failure(r, t, config)
            taxonomy[category] += 1
            if t.get("found_correct_file"):
                found_correct_file_count += 1
            total_grep += t.get("grep_calls", 0)
            total_edit += t.get("edit_calls", 0)

        print(f"\nFailure taxonomy ({len(failures)} failed runs):")
        for cat, count in taxonomy.most_common():
            print(f"  {cat}: {count}/{len(failures)}")

        print(f"\nBehavioral stats:")
        print(f"  Found correct file: {found_correct_file_count}/{len(failures)}")
        print(f"  Avg grep calls: {total_grep/len(failures):.1f}")
        print(f"  Avg edit calls: {total_edit/len(failures):.1f}")


if __name__ == "__main__":
    main()
