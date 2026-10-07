"""
Scoring a detector against synthetic runs (M2 runbook, Days 2, 4, 10, 11).

Two levels of counting, because they answer different questions:

* Run level (one verdict per run). Matches the Day-11 results table:
  run, scenario, ground_truth, detected, score, detection_delay, prediction.
    - a faulty run is a hit if an alert fires at or after the fault starts
    - a healthy run is a false alarm if any alert fires in it
  Precision, recall and F1 are computed from these.

* Snapshot level, only for false alarms. The false-positive rate is the share
  of *normal* snapshots (healthy runs, plus the part of a faulty run before
  the fault starts) that raised an alert. This is the Day-10 "healthy
  interval" measure.

Detection delay is seconds from the fault's true start to the first alert at
or after it. It is only defined for faulty runs that were detected.
"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import mean, median

from ..baseline import BaselineConfig, alert_cutoff, score_values, values_of
from ..features import extract_features
from .scenarios import Run


@dataclass(frozen=True)
class PreparedRun:
    """A run with its feature values already extracted.

    Feature extraction doesn't depend on the detector's thresholds, so it is
    done once and reused for every config tried during tuning.
    """

    run: Run
    values: tuple[tuple[float, ...], ...]


def prepare(run: Run) -> PreparedRun:
    values = []
    previous = None
    for snapshot in run.snapshots:
        values.append(values_of(extract_features(snapshot, previous)))
        previous = snapshot
    return PreparedRun(run=run, values=tuple(values))


def prepare_all(runs: list[Run]) -> list[PreparedRun]:
    return [prepare(run) for run in runs]


@dataclass(frozen=True)
class RunResult:
    run_id: str
    scenario: str
    ground_truth: bool              # True = the run really contains a fault
    detected: bool                  # an alert fired at or after the fault start
    score: float                    # highest score from the fault start on (whole run if healthy)
    detection_delay_s: float | None
    prediction: bool                # the detector's verdict for the run
    false_alert_snapshots: int      # alerts on normal snapshots
    normal_snapshots: int


def evaluate_prepared(prepared: PreparedRun, config: BaselineConfig) -> RunResult:
    thresholds, total = config.compiled()
    cutoff = alert_cutoff(config)
    run = prepared.run
    fault = run.fault_start

    first_alert_after_fault = None
    any_alert = False
    false_alerts = 0
    normal = 0
    top_score = 0.0

    for i, values in enumerate(prepared.values):
        score = score_values(values, thresholds, total)
        alert = score >= cutoff
        in_fault = fault is not None and i >= fault

        if in_fault:
            top_score = max(top_score, score)
            if alert and first_alert_after_fault is None:
                first_alert_after_fault = i
        else:
            normal += 1
            if alert:
                false_alerts += 1
            if fault is None:
                top_score = max(top_score, score)
        any_alert = any_alert or alert

    if fault is None:
        detected = False
        delay = None
        prediction = any_alert
    else:
        detected = first_alert_after_fault is not None
        delay = (
            (run.snapshots[first_alert_after_fault].timestamp - run.snapshots[fault].timestamp).total_seconds()
            if detected
            else None
        )
        prediction = detected

    return RunResult(
        run_id=run.run_id,
        scenario=run.scenario,
        ground_truth=run.anomalous,
        detected=detected,
        score=top_score,
        detection_delay_s=delay,
        prediction=prediction,
        false_alert_snapshots=false_alerts,
        normal_snapshots=normal,
    )


def evaluate(prepared_runs: list[PreparedRun], config: BaselineConfig) -> list[RunResult]:
    return [evaluate_prepared(p, config) for p in prepared_runs]


@dataclass(frozen=True)
class Summary:
    runs: int
    tp: int
    fn: int
    fp: int
    tn: int
    precision: float
    recall: float
    f1: float
    run_fpr: float                      # healthy runs that raised any alert
    snapshot_fpr: float                 # normal snapshots that raised an alert
    false_alert_snapshots: int
    normal_snapshots: int
    recall_by_scenario: dict[str, float]
    delay_mean_s: float | None
    delay_median_s: float | None
    delay_max_s: float | None


def _ratio(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator else 0.0


def summarize(results: list[RunResult]) -> Summary:
    tp = sum(1 for r in results if r.ground_truth and r.prediction)
    fn = sum(1 for r in results if r.ground_truth and not r.prediction)
    fp = sum(1 for r in results if not r.ground_truth and r.prediction)
    tn = sum(1 for r in results if not r.ground_truth and not r.prediction)

    precision = _ratio(tp, tp + fp)
    recall = _ratio(tp, tp + fn)
    f1 = _ratio(2 * precision * recall, precision + recall)

    false_alerts = sum(r.false_alert_snapshots for r in results)
    normal = sum(r.normal_snapshots for r in results)

    by_scenario = {}
    for scenario in sorted({r.scenario for r in results if r.ground_truth}):
        group = [r for r in results if r.scenario == scenario]
        by_scenario[scenario] = _ratio(sum(1 for r in group if r.detected), len(group))

    delays = [r.detection_delay_s for r in results if r.detection_delay_s is not None]

    return Summary(
        runs=len(results),
        tp=tp,
        fn=fn,
        fp=fp,
        tn=tn,
        precision=precision,
        recall=recall,
        f1=f1,
        run_fpr=_ratio(fp, fp + tn),
        snapshot_fpr=_ratio(false_alerts, normal),
        false_alert_snapshots=false_alerts,
        normal_snapshots=normal,
        recall_by_scenario=by_scenario,
        delay_mean_s=mean(delays) if delays else None,
        delay_median_s=median(delays) if delays else None,
        delay_max_s=max(delays) if delays else None,
    )
