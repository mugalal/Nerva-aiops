"""
Day 4: Isolation Forest against the threshold baseline (M2 runbook).

Run from services/anomaly-engine:

    python -m app.evaluation.compare

The contest is kept fair in three ways:

* Three datasets with fixed roles.
    training  (--train-seed)  healthy readings only; the model learns from
                              nothing else
    tuning    (--tune-seed)   the labelled data the baseline was tuned on; the
                              model's alert line is chosen here
    held-out  (--eval-seed)   final scores only; no choice ever uses it
* The same false-alarm budget (--max-fpr) for choosing the model's alert line
  as was used for the baseline's thresholds.
* The same code decides every verdict: both detectors produce one score per
  reading, and `result_from_scores` judges them identically.

Four detectors are compared. The point of the three extra ones is to find out
*what* helps, not just whether something beats the baseline:

    baseline            global threshold lines (the Day-2 baseline)
    z-score per-service a plain ruler: how many "usual wobbles" is this reading
                        from this service's own healthy average? No ML.
    forest per-service  Isolation Forest, one model per service
    forest pooled       Isolation Forest, one model for all services

If the z-score ruler matches the forest, the gain comes from knowing each
service's own normal, not from the forest.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass

import numpy as np

from ..baseline import DEFAULT_CONFIG, BaselineConfig
from ..model import IsolationForestDetector, ZScoreDetector
from .metrics import PreparedRun, RunResult, Summary, evaluate, prepare_all, result_from_scores, summarize
from .run import print_comparison
from .scenarios import generate_dataset
from .tune import tune


def normal_training_matrices(prepared_runs: list[PreparedRun]) -> dict[str, list[tuple[float, ...]]]:
    """Healthy readings per service: whole healthy runs, plus the stretch of a
    faulty run before its fault begins. Nothing from the fault itself."""
    healthy: dict[str, list[tuple[float, ...]]] = {}
    for prepared in prepared_runs:
        run = prepared.run
        stop = len(prepared.full) if run.fault_start is None else run.fault_start
        healthy.setdefault(run.service, []).extend(prepared.full[:stop])
    return healthy


def score_runs(detector, prepared_runs: list[PreparedRun]) -> list[np.ndarray]:
    return [detector.score_many(p.run.service, p.full) for p in prepared_runs]


def judge(prepared_runs: list[PreparedRun], scores: list[np.ndarray], cutoff: float) -> list[RunResult]:
    return [result_from_scores(p.run, s.tolist(), cutoff) for p, s in zip(prepared_runs, scores)]


@dataclass(frozen=True)
class CutoffChoice:
    cutoff: float | None            # None if no cutoff met the false-alarm budget
    summary: Summary | None
    lowest_fpr_seen: float


def choose_cutoff(
    prepared_runs: list[PreparedRun],
    scores: list[np.ndarray],
    max_fpr: float,
    candidates: int = 300,
) -> CutoffChoice:
    """Pick the alert line with the best run-level F1 among those that keep
    false alarms within budget. Same objective as the baseline tuner: higher
    F1, then faster detection, then fewer false alarms. On an exact tie the
    higher (stricter) cutoff wins, because it leaves more margin against
    false alarms on new data."""
    flat = np.concatenate(scores)
    best_cutoff = best_summary = best_key = None
    lowest_fpr = float("inf")

    for cutoff in np.linspace(flat.min(), flat.max(), candidates)[::-1]:
        summary = summarize(judge(prepared_runs, scores, float(cutoff)))
        lowest_fpr = min(lowest_fpr, summary.snapshot_fpr)
        if summary.snapshot_fpr > max_fpr:
            continue
        delay = summary.delay_mean_s if summary.delay_mean_s is not None else float("inf")
        key = (summary.f1, -delay, -summary.snapshot_fpr)
        if best_key is None or key > best_key:
            best_cutoff, best_summary, best_key = float(cutoff), summary, key

    return CutoffChoice(best_cutoff, best_summary, lowest_fpr)


def misses_by_group(prepared_runs: list[PreparedRun], results: list[RunResult]) -> Counter:
    """Faulty runs the detector missed, counted by (service, scenario)."""
    return Counter(
        (p.run.service, r.scenario)
        for p, r in zip(prepared_runs, results)
        if r.ground_truth and not r.detected
    )


def _format_misses(counter: Counter) -> str:
    if not counter:
        return "none"
    return ", ".join(f"{service} {scenario} x{n}" for (service, scenario), n in sorted(counter.items()))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Compare Isolation Forest with the threshold baseline.")
    parser.add_argument("--max-fpr", type=float, default=0.002,
                        help="false-alarm budget, as a share of normal snapshots (default 0.002)")
    parser.add_argument("--per-cell", type=int, default=10, help="runs per (service, scenario) (default 10)")
    parser.add_argument("--train-seed", type=int, default=1500)
    parser.add_argument("--tune-seed", type=int, default=1000)
    parser.add_argument("--eval-seed", type=int, default=2000)
    parser.add_argument("--severity", type=float, default=1.0,
                        help="fault size: 1.0 standard, 0.3 = only 30%% of each fault's effect (default 1.0)")
    args = parser.parse_args(argv)

    training = prepare_all(generate_dataset(args.train_seed, args.per_cell, args.severity))
    tuning = prepare_all(generate_dataset(args.tune_seed, args.per_cell, args.severity))
    held_out = prepare_all(generate_dataset(args.eval_seed, args.per_cell, args.severity))

    # The shipped baseline config was tuned on standard-size faults. On other
    # sizes it would be judged with thresholds meant for something else, so
    # re-tune it on this tuning set, with the same budget the others get.
    baseline_config: BaselineConfig = DEFAULT_CONFIG
    if args.severity != 1.0:
        print(f"Fault size {args.severity}: re-tuning the baseline on the tuning set so it gets the same chance.")
        retuned = tune(tuning, max_fpr=args.max_fpr)
        if retuned.best_config is not None:
            baseline_config = retuned.best_config

    healthy = normal_training_matrices(training)
    counts = ", ".join(f"{s} {len(rows)}" for s, rows in sorted(healthy.items()))
    print(f"Training (seed {args.train_seed}): healthy readings only -> {counts}")
    print(f"Tuning   (seed {args.tune_seed}): chooses the model's alert line; budget {100 * args.max_fpr:.1f}% false alarms")
    print(f"Held-out (seed {args.eval_seed}): final scores only, never used for a choice")

    variants = {
        "z-score per-service": ZScoreDetector(),
        "forest per-service": IsolationForestDetector(per_service=True),
        "forest pooled": IsolationForestDetector(per_service=False),
    }

    tuning_columns = [("baseline", summarize(evaluate(tuning, baseline_config)))]
    held_out_columns = [("baseline", summarize(evaluate(held_out, baseline_config)))]
    held_out_results = {"baseline": evaluate(held_out, baseline_config)}
    cutoffs = {}

    for name, detector in variants.items():
        detector.fit(healthy)
        tuning_scores = score_runs(detector, tuning)
        choice = choose_cutoff(tuning, tuning_scores, args.max_fpr)
        if choice.cutoff is None:
            print(f"\n{name}: no alert line met the budget (lowest false-alarm rate seen: {100 * choice.lowest_fpr_seen:.2f}%)")
            continue
        cutoffs[name] = choice.cutoff
        tuning_columns.append((name, choice.summary))

        results = judge(held_out, score_runs(detector, held_out), choice.cutoff)
        held_out_results[name] = results
        held_out_columns.append((name, summarize(results)))

    print_comparison("On the TUNING set (where the alert lines were chosen, so this looks best):", tuning_columns)
    print_comparison("On the HELD-OUT set (the real comparison):", held_out_columns)

    if cutoffs:
        print("\nAlert lines chosen on the tuning set (score 0..1; higher = more unusual):")
        for name, cutoff in cutoffs.items():
            note = ""
            if name == "z-score per-service":
                note = f"  (= {ZScoreDetector.wobbles_of(cutoff):.1f} usual wobbles from normal)"
            print(f"  {name}: {cutoff:.3f}{note}")

    print("\nFaulty runs each detector missed on the held-out set:")
    for name, results in held_out_results.items():
        print(f"  {name}: {_format_misses(misses_by_group(held_out, results))}")


if __name__ == "__main__":
    main()
