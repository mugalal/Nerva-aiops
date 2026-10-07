"""
Threshold tuning for the baseline (M2 runbook, Day 2).

Tries every combination in a grid on a *tuning* dataset and keeps the config
with the best run-level F1 among those that stay inside a false-alarm budget
(snapshot false-positive rate <= max_fpr). The winner must then be checked on
separate held-out data, because a config picked on one dataset always looks a
little better on that dataset than it will on new data.

Every config is scored through the same functions the service uses
(`score_values`, via `evaluate_prepared`), so there is no second copy of the
scoring rule to drift out of sync.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass

from ..baseline import BaselineConfig
from .metrics import PreparedRun, Summary, evaluate, summarize

# Candidate values per knob. None switches a rule off, so the search itself
# decides whether the relative-change rules are worth having.
# memory is not searched: none of the three scenarios moves it.
#
# alert_score 0.1 means "any single breached rule raises an alert" (the
# smallest rule is worth 1/7 = 0.14 of the score). Without it a lone
# *_change rule could never alert, and the search couldn't tell whether those
# rules are useful.
DEFAULT_GRID: dict[str, list] = {
    "latency_p95_ms": [300.0, 400.0, 500.0, 650.0, 800.0],
    "http_5xx_rate": [0.02, 0.03, 0.05, 0.08],
    "cpu": [0.75, 0.85, 0.92],
    "request_rate_change": [None, 0.25, 0.4, 0.6, 1.0],
    "latency_change": [None, 0.3, 0.6, 1.0, 2.0],
    "alert_score": [0.1, 0.2, 0.3, 0.4],
}

CHANGE_RULES = ("request_rate_change", "latency_change")


@dataclass(frozen=True)
class TuneResult:
    best_config: BaselineConfig | None   # None if nothing met the false-alarm budget
    best_summary: Summary | None
    configs_tried: int
    configs_within_budget: int
    lowest_fpr_seen: float               # useful when nothing fits the budget


def _sort_key(summary: Summary) -> tuple:
    # Higher F1 first; then faster detection; then fewer false alarms.
    delay = summary.delay_mean_s if summary.delay_mean_s is not None else float("inf")
    return (summary.f1, -delay, -summary.snapshot_fpr)


def tune(
    prepared_runs: list[PreparedRun],
    max_fpr: float,
    grid: dict[str, list] | None = None,
    allow_change_rules: bool = True,
    memory_threshold: float | None = 0.85,
) -> TuneResult:
    """Grid search. allow_change_rules=False pins both *_change rules to None,
    which answers "would the baseline do just as well with absolute thresholds
    only?" """
    grid = dict(DEFAULT_GRID if grid is None else grid)
    if not allow_change_rules:
        for name in CHANGE_RULES:
            grid[name] = [None]

    names = list(grid)
    best_config = None
    best_summary = None
    best_key = None
    tried = 0
    within_budget = 0
    lowest_fpr = float("inf")

    for combo in itertools.product(*(grid[name] for name in names)):
        config = BaselineConfig(memory=memory_threshold, **dict(zip(names, combo)))
        summary = summarize(evaluate(prepared_runs, config))
        tried += 1
        lowest_fpr = min(lowest_fpr, summary.snapshot_fpr)

        if summary.snapshot_fpr > max_fpr:
            continue
        within_budget += 1
        key = _sort_key(summary)
        if best_key is None or key > best_key:   # strict ">" keeps the first of any tie
            best_config, best_summary, best_key = config, summary, key

    return TuneResult(
        best_config=best_config,
        best_summary=best_summary,
        configs_tried=tried,
        configs_within_budget=within_budget,
        lowest_fpr_seen=lowest_fpr,
    )
