"""
Threshold baseline (M2 runbook, Day 2). Not ML.

Fixed per-feature thresholds, no learning, no history. It exists to be the
yardstick the Day 4 model (Isolation Forest / One-Class SVM) is compared
against on precision, recall, F1, false-positive rate and detection latency.
If the model can't beat it, the baseline ships; that is a valid Day 5 outcome.

The thresholds below are PLACEHOLDERS, not evaluated numbers. Day 2 is where
they get tuned against healthy / bad-deployment / traffic-spike data.
"""

from __future__ import annotations

from .features import FeatureVector

MODEL_NAME = "threshold_baseline"

# (feature name, threshold, weight). Latency and 5xx weigh more because they
# are the strongest bad-deployment signal (runbook, Day 8).
CHECKS = (
    ("latency_p95_ms", 500.0, 1.5),
    ("http_5xx_rate", 0.05, 1.5),
    ("cpu", 0.85, 1.0),
    ("memory", 0.85, 1.0),
)


def baseline_score(features: FeatureVector) -> tuple[float, str]:
    """Return (score in 0..1, severity).

    score is the weighted share of thresholds breached. A NaN comparison is
    False, so missing data counts as "not breached" and can never produce a
    high-confidence anomaly (runbook, Day 12).
    """
    total = sum(weight for _, _, weight in CHECKS)
    breached = sum(
        weight
        for name, threshold, weight in CHECKS
        if getattr(features, name) > threshold
    )
    score = round(breached / total, 4)

    if score >= 0.6:
        severity = "high"
    elif score >= 0.3:
        severity = "medium"
    else:
        severity = "low"
    return score, severity
