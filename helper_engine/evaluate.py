"""Summarize metadata journals from controlled task runs; never infer success from action counts."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from statistics import mean


def summarize(paths) -> dict:
    """Aggregate finished run journals into completion rates and mean metrics.

    Conversational answers are excluded from the task completion rate.
    Never infers success from action counts.
    """
    runs = {}
    invalid = 0
    for path in paths:
        with Path(path).open(encoding="utf-8") as stream:
            for line in stream:
                try:
                    row = json.loads(line)
                    if row.get("event") == "finished" and row.get("run_id"):
                        runs[row["run_id"]] = row
                except (json.JSONDecodeError, AttributeError):
                    invalid += 1
    rows = list(runs.values())
    counts = Counter(row.get("status", "unknown") for row in rows)
    # Conversational answers are excluded from task completion rate.
    task_rows = [row for row in rows if row.get("status") != "answered"]
    result = {"finished_runs": len(rows), "statuses": dict(counts), "task_runs": len(task_rows),
              "task_completion_rate": counts["completed"] / len(task_rows) if task_rows else None,
              "invalid_lines": invalid}
    for metric in ("elapsed_seconds", "model_seconds", "model_calls", "actions",
                   "observations", "stale_decisions", "rejected_actions", "screenshot_bytes"):
        # bool is a subclass of int; exclude it so a stray True/False can't skew a mean.
        values = [row[metric] for row in task_rows
                  if isinstance(row.get(metric), (int, float)) and not isinstance(row.get(metric), bool)]
        result["mean_" + metric] = round(mean(values), 3) if values else None
    result["note"] = "Runtime completion is model-verified. Use an external task judge for a benchmark claim."
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    paths = []
    for path in args.paths:
        paths.extend(sorted(path.glob("*.jsonl")) if path.is_dir() else [path])
    result = json.dumps(summarize(paths), indent=2)
    print(result)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(result + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
