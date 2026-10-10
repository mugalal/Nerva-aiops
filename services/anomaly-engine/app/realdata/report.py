"""
Day-3 report: how the detectors do on REAL readings.

A single drill gives a few minutes of each fault, far too little to choose
alert lines honestly. So instead of verdicts at a chosen cutoff, this reports
three things that don't depend on one:

  1. What the real readings look like, normal versus during the fault.
  2. The frozen baseline (tuned on synthetic data, never shown real readings)
     with its own alert line, as a straight transfer test.
  3. For each detector, how well it separates fault from normal, without
     choosing an alert line:
       - AUC: the chance a fault reading scores higher than a normal one
         (1.0 = always, 0.5 = coin toss)
       - "caught at zero false alarms": put the alert line just above the
         highest score among the run's own normal readings, and see what share
         of the fault readings, and how soon, would alert

The ruler and the forest are trained on the normal readings of every OTHER
run, then scored on the run being judged, so no detector is graded on
readings it trained on.
"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import mean, pstdev

from ..baseline import DEFAULT_CONFIG, BaselineConfig, alert_cutoff, score_values
from ..evaluation.metrics import PreparedRun, prepare_all, result_from_scores
from ..evaluation.scenarios import Run

METRICS = ("request_rate", "latency_p95_ms", "http_5xx_rate", "cpu", "memory", "replica_count")

# Real captures are small, so the detectors are allowed to train on fewer
# readings than the safeguard used for synthetic data.
REPORT_MIN_TRAINING_SAMPLES = 20


def auc(fault_scores: list[float], normal_scores: list[float]) -> float:
    """Chance that a random fault reading scores above a random normal one."""
    if not fault_scores or not normal_scores:
        return float("nan")
    wins = sum(
        1.0 if f > n else 0.5 if f == n else 0.0
        for f in fault_scores
        for n in normal_scores
    )
    return wins / (len(fault_scores) * len(normal_scores))


@dataclass(frozen=True)
class Separation:
    detector: str
    auc: float
    caught_share: float            # fault readings above the highest normal score
    first_alert_s: float | None    # seconds from fault start to the first such reading
    normal_max: float
    fault_max: float


def separation(run: Run, scores: list[float], detector: str) -> Separation:
    f = run.fault_start
    normal, fault = scores[:f], scores[f:]
    ceiling = max(normal)
    above = [i for i, s in enumerate(fault) if s > ceiling]
    first = None
    if above:
        first = (run.snapshots[f + above[0]].timestamp - run.snapshots[f].timestamp).total_seconds()
    return Separation(
        detector=detector,
        auc=auc(fault, normal),
        caught_share=len(above) / len(fault),
        first_alert_s=first,
        normal_max=ceiling,
        fault_max=max(fault),
    )


def _fmt(x: float) -> str:
    return f"{x:.4g}"


def describe(runs: list[Run]) -> list[str]:
    lines = ["WHAT THE REAL READINGS LOOK LIKE (normal part versus during the fault)"]
    for run in (r for r in runs if r.anomalous):
        f = run.fault_start
        lines.append(f"\n  {run.run_id}: {f} normal readings, then {len(run.snapshots) - f} during the fault")
        lines.append(f"    {'':16}{'normal (mean, wobble)':>26}{'during fault (mean)':>22}")
        for name in METRICS:
            normal = [getattr(s.metrics, name) for s in run.snapshots[:f]]
            fault = [getattr(s.metrics, name) for s in run.snapshots[f:]]
            lines.append(
                f"    {name:16}{_fmt(mean(normal)) + '  (' + _fmt(pstdev(normal)) + ')':>26}{_fmt(mean(fault)):>22}"
            )
    return lines


def baseline_transfer(prepared: list[PreparedRun], config: BaselineConfig) -> list[str]:
    thresholds, total = config.compiled()
    cutoff = alert_cutoff(config)
    lines = [
        "FROZEN BASELINE ON REAL READINGS (thresholds tuned on synthetic data, never shown a real reading)",
    ]
    for p in prepared:
        scores = [score_values(v, thresholds, total) for v in p.values]
        result = result_from_scores(p.run, scores, cutoff)
        if not p.run.anomalous:
            lines.append(f"  {p.run.run_id:22} healthy: {result.false_alert_snapshots} false alarms in {result.normal_snapshots} readings")
        else:
            verdict = f"caught after {result.detection_delay_s:.0f}s" if result.detected else "MISSED"
            lines.append(
                f"  {p.run.run_id:22} {verdict}; false alarms before the fault: "
                f"{result.false_alert_snapshots} of {result.normal_snapshots}"
            )
    return lines


def _normal_rows(prepared: PreparedRun) -> list[tuple[float, ...]]:
    stop = len(prepared.full) if prepared.run.fault_start is None else prepared.run.fault_start
    return list(prepared.full[:stop])


def detector_separation(prepared: list[PreparedRun], config: BaselineConfig) -> list[str]:
    try:
        from ..model import IsolationForestDetector, ZScoreDetector
    except ImportError:
        return ["DETECTOR SEPARATION: skipped (install scikit-learn and numpy to include the ruler and forest)"]

    thresholds, total = config.compiled()
    lines = [
        "CAN EACH DETECTOR SEPARATE THE FAULT FROM NORMAL? (no alert line chosen; trained on the other runs only)",
        "  'caught' = share of fault readings scoring above the highest normal reading of the same run.",
    ]
    for index, target in enumerate(prepared):
        run = target.run
        if not run.anomalous:
            continue
        others = [_normal_rows(p) for j, p in enumerate(prepared) if j != index and p.run.service == run.service]
        rows = [row for part in others for row in part]

        results = [separation(run, [score_values(v, thresholds, total) for v in target.values], "baseline (frozen)")]
        skipped = None
        if len(rows) < REPORT_MIN_TRAINING_SAMPLES:
            skipped = f"only {len(rows)} normal readings from the other runs; need {REPORT_MIN_TRAINING_SAMPLES}"
        else:
            training = {run.service: rows}
            for name, detector in (
                ("ruler", ZScoreDetector(min_samples=REPORT_MIN_TRAINING_SAMPLES)),
                ("forest", IsolationForestDetector(min_samples=REPORT_MIN_TRAINING_SAMPLES)),
            ):
                detector.fit(training)
                scores = detector.score_many(run.service, target.full).tolist()
                results.append(separation(run, scores, name))

        lines.append(f"\n  {run.run_id}  (trained on {len(rows)} normal readings from other runs)")
        lines.append(f"    {'detector':20}{'AUC':>7}{'caught':>9}{'first alert':>13}{'normal max':>12}{'fault max':>11}")
        for r in results:
            first = "never" if r.first_alert_s is None else f"{r.first_alert_s:.0f}s"
            lines.append(
                f"    {r.detector:20}{r.auc:>7.3f}{100 * r.caught_share:>8.0f}%{first:>13}"
                f"{r.normal_max:>12.3f}{r.fault_max:>11.3f}"
            )
        if skipped:
            lines.append(f"    ruler and forest skipped: {skipped}")
    return lines


def build_report(runs: list[Run], config: BaselineConfig = DEFAULT_CONFIG) -> str:
    prepared = prepare_all(runs)
    sections = [
        describe(runs),
        baseline_transfer(prepared, config),
        detector_separation(prepared, config),
    ]
    return "\n\n".join("\n".join(section) for section in sections)
