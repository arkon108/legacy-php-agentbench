"""Aggregate benchmark results and update README tables."""

import json
import re
from pathlib import Path

RESULTS_DIR = Path(__file__).parent / "results"
README_PATH = Path(__file__).parent / "README.md"
FINDINGS_PATH = Path(__file__).parent / "docs" / "findings.md"

CONFIGS = ["A", "B", "C", "D"]
TASK_IDS = [f"dw-{i:03d}" for i in range(1, 13)]


def load_config(name: str) -> list[dict]:
    path = RESULTS_DIR / name / "summary.json"
    if not path.exists():
        return []
    return json.load(open(path))


def per_task_table(all_results: dict[str, list[dict]]) -> str:
    lines = []
    # Header
    header = "| Task |"
    sep = "|------|"
    for c in CONFIGS:
        if c in all_results:
            header += f" {c} |"
            sep += "----|"
    lines.append(header)
    lines.append(sep)

    for task in TASK_IDS:
        row = f"| {task} |"
        for c in CONFIGS:
            if c not in all_results:
                continue
            runs = [r for r in all_results[c] if r["task_id"] == task]
            resolved = sum(1 for r in runs if r["resolved"])
            total = len(runs)
            row += f" {resolved}/{total} |"
        lines.append(row)

    # Total row
    row = "| **Total** |"
    for c in CONFIGS:
        if c not in all_results:
            continue
        runs = all_results[c]
        resolved = sum(1 for r in runs if r["resolved"])
        total = len(runs)
        row += f" **{resolved}/{total}** |"
    lines.append(row)

    return "\n".join(lines)


def summary_table(all_results: dict[str, list[dict]]) -> str:
    config_info = {
        "A": ("qwen3.8-max", "Single model"),
        "B": ("qwen3.7-plus + glm-5.3", "Worker + judge"),
        "C": ("qwen3.7-plus", "Verify loop"),
        "D": ("glm-5.3 + kimi-k3", "Worker + judge"),
    }

    lines = [
        "| Config | Model(s) | Resolved | Total cost | Avg cost/run | Cost/resolved | Avg turns |",
        "|--------|----------|----------|------------|--------------|---------------|-----------|",
    ]

    for c in CONFIGS:
        if c not in all_results:
            continue
        runs = all_results[c]
        model, desc = config_info[c]
        resolved = sum(1 for r in runs if r["resolved"])
        total = len(runs)
        cost = sum(r["cost_usd"] for r in runs)
        avg_cost = cost / total
        cost_per_res = f"${cost / resolved:.2f}" if resolved else "—"
        avg_turns = sum(r["turns"] for r in runs) / total
        lines.append(
            f"| **{c}** ({desc}) | {model} | {resolved}/{total} | "
            f"${cost:.2f} | ${avg_cost:.2f} | {cost_per_res} | {avg_turns:.1f} |"
        )

    total_cost = sum(r["cost_usd"] for runs in all_results.values() for r in runs)
    total_resolved = sum(1 for runs in all_results.values() for r in runs if r["resolved"])
    total_runs = sum(len(runs) for runs in all_results.values())
    lines.append(f"| **All** | | {total_resolved}/{total_runs} | ${total_cost:.2f} | | | |")

    return "\n".join(lines)


def update_readme(all_results: dict[str, list[dict]]):
    readme = README_PATH.read_text()

    results_block = (
        summary_table(all_results) + "\n\n"
        "### Per-task breakdown\n\n" +
        per_task_table(all_results)
    )

    readme = re.sub(
        r"<!-- RESULTS_TABLE_START -->.*?<!-- RESULTS_TABLE_END -->",
        f"<!-- RESULTS_TABLE_START -->\n{results_block}\n<!-- RESULTS_TABLE_END -->",
        readme,
        flags=re.DOTALL,
    )

    # Update total cost
    total_cost = sum(r["cost_usd"] for runs in all_results.values() for r in runs)
    readme = re.sub(
        r"\*\*\$XX\.XX\*\*",
        f"**${total_cost:.2f}**",
        readme,
    )

    README_PATH.write_text(readme)
    print(f"Updated {README_PATH}")


def write_summary_all(all_results: dict[str, list[dict]]):
    output = {"configs": {}}
    for c, runs in all_results.items():
        resolved = sum(1 for r in runs if r["resolved"])
        total = len(runs)
        cost = sum(r["cost_usd"] for r in runs)
        output["configs"][c] = {
            "total_runs": total,
            "resolved": resolved,
            "total_cost_usd": round(cost, 4),
            "avg_cost_per_run": round(cost / total, 4),
            "cost_per_resolved": round(cost / resolved, 4) if resolved else None,
        }
    output["tasks"] = TASK_IDS
    output["total_cost_usd"] = round(
        sum(r["cost_usd"] for runs in all_results.values() for r in runs), 4
    )

    path = RESULTS_DIR / "summary_all.json"
    path.write_text(json.dumps(output, indent=2, ensure_ascii=False))
    print(f"Wrote {path}")


def main():
    all_results = {}
    for c in CONFIGS:
        runs = load_config(c)
        if runs:
            all_results[c] = runs
            resolved = sum(1 for r in runs if r["resolved"])
            print(f"Config {c}: {resolved}/{len(runs)} resolved")

    if not all_results:
        print("No results found")
        return

    write_summary_all(all_results)
    update_readme(all_results)

    # Print tables to stdout
    print("\n" + summary_table(all_results))
    print("\n" + per_task_table(all_results))


if __name__ == "__main__":
    main()
