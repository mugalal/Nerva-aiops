"""
Threshold baseline (M2 runbook, Day 2). Not ML.

Fixed rules, no learning, no history. It exists to be the yardstick the
Day 4 model (Isolation Forest / One-Class SVM) is compared against on
precision, recall, F1, false-positive rate and detection latency. The
runbook says to "select empirically" on Day 5; my reading is that a simple
detector can be the one selected if it wins. Confirm that with the team lead.

Each rule says: "if this feature is above its threshold, that's a breach".
The score is the weighted share of the active rules that are breached, and a
snapshot counts as an *alert* when the score reaches `alert_score`.

Everything tunable lives in BaselineConfig, so a "frozen config" (Day 10) is
one small object. The service and the evaluation tools both score through
the same core function (`score_values`), so what gets tuned is exactly what
runs.
"""

from __future__ import annotations

from dataclasses import dataclass

from .features import FeatureVector

MODEL_NAME = "threshold_baseline"

# (feature name, weight). The order is fixed: it is the order feature values
# are passed to `score_values`. Latency and 5xx weigh more because they are
# the strongest bad-deployment signal (runbook, Day 8).
RULES = (
    ("latency_p95_ms", 1.5),
    ("http_5xx_rate", 1.5),
    ("cpu", 1.0),
    ("memory", 1.0),
    ("request_rate_change", 1.0),
    ("latency_change", 1.0),
)
RULE_NAMES = tuple(name for name, _ in RULES)
WEIGHTS = tuple(weight for _, weight in RULES)

# Float slack for comparing a score with alert_score.
_EPS = 1e-9


@dataclass(frozen=True)
class BaselineConfig:
    """Thresholds for each rule. None switches a rule off.

    *_change thresholds are relative changes versus the previous snapshot
    (1.0 means "doubled"). A switched-off rule is left out of the score
    entirely, including from the total weight it is divided by.
    """

    latency_p95_ms: float | None = 500.0
    http_5xx_rate: float | None = 0.05
    cpu: float | None = 0.85
    memory: float | None = 0.85
    request_rate_change: float | None = None
    latency_change: float | None = None
    alert_score: float = 0.3

    def compiled(self) -> tuple[tuple[float, ...], float]:
        """(thresholds in RULES order, total active weight).

        A switched-off rule gets an infinite threshold, which nothing can
        exceed, so the scoring loop needs no special case for it.
        """
        thresholds = tuple(
            float("inf") if getattr(self, name) is None else float(getattr(self, name))
            for name in RULE_NAMES
        )
        total = sum(w for name, w in RULES if getattr(self, name) is not None)
        return thresholds, total


# The Day-1 numbers. They were guesses, kept so the tuned config has
# something honest to be compared against.
PLACEHOLDER_CONFIG = BaselineConfig()

# The config the service runs with: the best of 6000 combinations on a
# synthetic tuning set, with a false-alarm budget of 0.2% of normal snapshots.
# Reproduce with:  python -m app.evaluation.run --tune
#
# PROVISIONAL. It was tuned on synthetic data (tuning seed 1000, 10 runs per
# service and scenario), and several thresholds sit close to the limits of the
# faults the generator makes up (e.g. the 5xx rule at 0.02 is the smallest
# error jump the generator ever produces). On held-out synthetic data (seed
# 2000) F1 was 0.87, against 0.74 for the placeholder, but that only shows it
# generalises across draws from the same generator, not to real traffic.
# Re-tune on M1's real telemetry on Day 3.
DEFAULT_CONFIG = BaselineConfig(
    latency_p95_ms=800.0,
    http_5xx_rate=0.02,
    cpu=0.75,
    memory=0.85,
    request_rate_change=0.6,
    latency_change=2.0,
    alert_score=0.1,
)


def values_of(features: FeatureVector) -> tuple[float, ...]:
    """Pull the rule features out of a FeatureVector, in RULES order."""
    return tuple(getattr(features, name) for name in RULE_NAMES)


def score_values(
    values: tuple[float, ...],
    thresholds: tuple[float, ...],
    total: float,
) -> float:
    """Core scoring: weighted share of rules whose value exceeds its threshold.

    A NaN compares False against anything, so missing data counts as "not
    breached" and can never produce a high-confidence anomaly (runbook,
    Day 12).
    """
    if total <= 0:
        return 0.0
    breached = 0.0
    for value, threshold, weight in zip(values, thresholds, WEIGHTS):
        if value > threshold:
            breached += weight
    return round(breached / total, 4)


def severity_for(score: float) -> str:
    """A label for how much of the rule weight is breached. Not an evaluated
    quantity: whether a snapshot is an *alert* is decided by `alert_score`,
    and a low-severity alert is still an alert. Don't build on severity yet."""
    if score >= 0.5:
        return "high"
    if score >= 0.25:
        return "medium"
    return "low"


def alert_cutoff(config: BaselineConfig = DEFAULT_CONFIG) -> float:
    """The score at or above which a snapshot counts as an alert."""
    return config.alert_score - _EPS


def is_alert(score: float, config: BaselineConfig = DEFAULT_CONFIG) -> bool:
    return score >= alert_cutoff(config)


def baseline_score(
    features: FeatureVector,
    config: BaselineConfig = DEFAULT_CONFIG,
) -> tuple[float, str]:
    """Return (score in 0..1, severity) for one feature vector."""
    thresholds, total = config.compiled()
    score = score_values(values_of(features), thresholds, total)
    return score, severity_for(score)
