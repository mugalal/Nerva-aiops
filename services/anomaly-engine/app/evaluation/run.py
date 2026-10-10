"""
Command-line runner for the Day-2 evaluation.

Run from services/anomaly-engine:

    python -m app.evaluation.run                    # placeholder vs current config
    python -m app.evaluation.run --tune             # tune first, then compare
    python -m app.evaluation.run --csv results.csv  # also write the Day-11 table

Tuning uses one synthetic dataset (--tune-seed). Every comparison is scored on
a different one (--eval-seed) that tuning never saw.
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import fields

from ..baseline import DEFAULT_CONFIG, PLACEHOLDER_CONFIG, BaselineConfig
from .metrics import RunResult, Summary, evaluate, prepare_all, summarize
from .scenarios import generate_dataset
from .tune import tune


def _pct(x: float) -> str:
    return f"{100 * x:.1f}%"


def _delay(x: float | None) -> str:
    return "n/a" if x is None else f"{x:.0f}s"


def print_comparison(title: str, columns: list[tuple[str, Summary]]) -> None:
    labels = [label for label, _ in columns]
    width = max(14, *(len(label) + 2 for label in labels))
    scenarios = sorted({k for _, s in columns for k in s.recall_by_scenario})

    rows: list[tuple[str, list[str]]] = [
        ("precision", [f"{s.precision:.3f}" for _, s in columns]),
        ("recall", [f"{s.recall:.3f}" for _, s in columns]),
        ("F1", [f"{s.f1:.3f}" for _, s in columns]),
    ]
    for scenario in scenarios:
        rows.append((f"  recall: {scenario}", [_pct(s.recall_by_scenario.get(scenario, 0.0)) for _, s in columns]))
    rows += [
        ("false alarms: healthy runs", [_pct(s.run_fpr) for _, s in columns]),
        ("false alarms: normal snapshots", [_pct(s.snapshot_fpr) for _, s in columns]),
        ("  (count)", [f"{s.false_alert_snapshots}/{s.normal_snapshots}" for _, s in columns]),
        ("detection delay: mean", [_delay(s.delay_mean_s) for _, s in columns]),
        ("detection delay: median", [_delay(s.delay_median_s) for _, s in columns]),
        ("detection delay: worst", [_delay(s.delay_max_s) for _, s in columns]),
    ]

    name_width = max(len(name) for name, _ in rows) + 2
    print(f"\n{title}")
    print(" " * name_width + "".join(f"{label:>{width}}" for label in labels))
    for name, cells in rows:
        print(f"{name:<{name_width}}" + "".join(f"{cell:>{width}}" for cell in cells))


def config_as_code(config: BaselineConfig) -> str:
    parts = ",\n".join(f"    {f.name}={getattr(config, f.name)!r}" for f in fields(config))
    return f"BaselineConfig(\n{parts},\n)"


def write_day11_table(path: str, results: list[RunResult]) -> None:
    """The Day-11 results table: run, scenario, ground_truth, detected, score,
    detection_delay, prediction."""
    with open(path, "w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["run", "scenario", "ground_truth", "detected", "score", "detection_delay", "prediction"])
        for r in results:
            writer.writerow(
                [
                    r.run_id,
                    r.scenario,
                    "anomalous" if r.ground_truth else "normal",
                    str(r.detected).lower(),
                    f"{r.score:.4f}",
                    "" if r.detection_delay_s is None else f"{r.detection_delay_s:.0f}",
                    "anomalous" if r.prediction else "normal",
                ]
            )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Evaluate the M2 threshold baseline on synthetic scenarios.")
    parser.add_argument("--tune", action="store_true", help="tune thresholds first, then compare")
    parser.add_argument("--max-fpr", type=float, default=0.002,
                        help="false-alarm budget while tuning, as a share of normal snapshots (default 0.002)")
    parser.add_argument("--per-cell", type=int, default=10, help="runs per (service, scenario) (default 10)")
    parser.add_argument("--tune-seed", type=int, default=1000)
    parser.add_argument("--eval-seed", type=int, default=2000)
    parser.add_argument("--csv", metavar="PATH", help="write the Day-11 per-run table for the held-out set")
    args = parser.parse_args(argv)

    held_out = prepare_all(generate_dataset(args.eval_seed, args.per_cell))
    snapshots = sum(len(p.values) for p in held_out)
    print(f"Held-out set: {len(held_out)} runs, {snapshots} snapshots (seed {args.eval_seed}). Tuning never sees this data.")

    columns = [("placeholder", summarize(evaluate(held_out, PLACEHOLDER_CONFIG)))]
    csv_config = DEFAULT_CONFIG

    if args.tune:
        tuning = prepare_all(generate_dataset(args.tune_seed, args.per_cell))
        print(f"Tuning set:   {len(tuning)} runs (seed {args.tune_seed}); false-alarm budget {_pct(args.max_fpr)} of normal snapshots.")

        winners = {}
        for label, allow in (("absolute only", False), ("+ change rules", True)):
            result = tune(tuning, max_fpr=args.max_fpr, allow_change_rules=allow)
            print(f"  {label}: tried {result.configs_tried} configs, {result.configs_within_budget} within budget")
            if result.best_config is None:
                print(f"    none fit the budget (lowest false-alarm rate seen: {_pct(result.lowest_fpr_seen)})")
                continue
            winners[label] = result
            columns.append((f"tuned {label}", summarize(evaluate(held_out, result.best_config))))

        print_comparison(
            "On the TUNING set (what the search optimised, so this looks best):",
            [(f"tuned {k}", v.best_summary) for k, v in winners.items()],
        )
        for label, result in winners.items():
            print(f"\nBest config, {label}:\n{config_as_code(result.best_config)}")
        if "+ change rules" in winners:
            csv_config = winners["+ change rules"].best_config
    else:
        columns.append(("current", summarize(evaluate(held_out, DEFAULT_CONFIG))))

    print_comparison("On the HELD-OUT set (data the tuning never saw):", columns)

    if args.csv:
        write_day11_table(args.csv, evaluate(held_out, csv_config))
        print(f"\nWrote the per-run table to {args.csv}")


if __name__ == "__main__":
    main()
