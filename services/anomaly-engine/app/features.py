"""
Feature extraction (M2 runbook, Day 1).

Turns a TelemetrySnapshot (plus the previous snapshot for the same service)
into M2's fixed P0 feature vector. That vector has 8 features and is internal
to M2: it is what the baseline and, later, the ML model score.

Only 4 of them travel on an AnomalyEvent, because that is what the frozen
contract carries. `to_contract()` does that narrowing in one place. If M3 or
M5 turn out to need the other four (e.g. request_rate_change for telling a
traffic spike from a bad deployment), that is a contract version bump to
propose to the team lead, not something to add to the event here.
"""

from __future__ import annotations

from pydantic import BaseModel

from shared.contracts import AnomalyFeatures, TelemetrySnapshot


class FeatureVector(BaseModel):
    request_rate: float
    request_rate_change: float
    latency_p95_ms: float
    latency_change: float
    http_5xx_rate: float
    cpu: float
    memory: float
    replica_count: int

    def as_vector(self) -> tuple[float, ...]:
        """All 8 features as plain numbers, always in FEATURE_ORDER."""
        return tuple(float(getattr(self, name)) for name in FEATURE_ORDER)

    def to_contract(self) -> AnomalyFeatures:
        """The 4-field subset the frozen AnomalyEvent contract carries."""
        return AnomalyFeatures(
            request_rate=self.request_rate,
            latency_p95_ms=self.latency_p95_ms,
            http_5xx_rate=self.http_5xx_rate,
            cpu=self.cpu,
        )


# The model reads the 8 features in this fixed order. It is the field order of
# FeatureVector above, so adding or reordering a field changes it everywhere.
FEATURE_ORDER = tuple(FeatureVector.model_fields)


def _relative_change(previous: float, current: float) -> float:
    """(current - previous) / previous, safe when previous is 0."""
    if previous == 0:
        return 0.0 if current == 0 else 1.0
    return (current - previous) / previous


def extract_features(
    current: TelemetrySnapshot,
    previous: TelemetrySnapshot | None = None,
) -> FeatureVector:
    """Build the feature vector for `current`.

    `previous` is the prior snapshot for the same service. On the first
    snapshot there is nothing to compare with, so both *_change features are
    0.0 rather than an invented spike.
    """
    cur = current.metrics
    if previous is None:
        request_rate_change = 0.0
        latency_change = 0.0
    else:
        prev = previous.metrics
        request_rate_change = _relative_change(prev.request_rate, cur.request_rate)
        latency_change = _relative_change(prev.latency_p95_ms, cur.latency_p95_ms)

    return FeatureVector(
        request_rate=cur.request_rate,
        request_rate_change=request_rate_change,
        latency_p95_ms=cur.latency_p95_ms,
        latency_change=latency_change,
        http_5xx_rate=cur.http_5xx_rate,
        cpu=cur.cpu,
        memory=cur.memory,
        replica_count=cur.replica_count,
    )
