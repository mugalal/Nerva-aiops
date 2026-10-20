"""How far each signal is from a service's learned normal, in that service's usual deviations.

The statistics come from M2's trained reference (services/anomaly-engine/reference/reference.json): per
service, the mean and the usual spread of every feature, learned from real captured healthy telemetry. They
are applied exactly as M2 applies them: load (request rate) is measured per replica and only counts when it
is ABOVE normal; health signals count in either direction; spreads have M2's floors.

A service the reference has no statistics for falls back to the healthy baseline M1 measured for it, with
a robust spread estimate (p95 minus average). The source is always reported with the evidence.
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from pathlib import Path

# M2's floors (services/anomaly-engine/app/model.py ZScoreDetector), so the same reading gets the same distance.
MIN_RELATIVE_SPREAD = 0.01
MIN_ABSOLUTE_SPREAD = 0.001
SIGNALS = ("request_rate", "latency_p95_ms", "http_5xx_rate", "cpu")
LOAD_SIGNALS = {"request_rate"}

_DEFAULT_PATHS = (Path("/app/reference/reference.json"),
                  Path(__file__).resolve().parents[2] / "anomaly-engine" / "reference" / "reference.json")


@dataclass(frozen=True)
class Normal:
    mean: dict[str, float]
    spread: dict[str, float]
    source: str                     # "anchored" (learned noise at M1's operating point), "learned" or "baseline"
    samples: int


def _spread(value: float, mean: float) -> float:
    return max(value, MIN_RELATIVE_SPREAD * abs(mean), MIN_ABSOLUTE_SPREAD)


class Reference:
    """M2's learned per-service normals, read-only."""

    def __init__(self, path: str | Path | None = None):
        self.path = None
        self._services: dict[str, Normal] = {}
        candidates = [Path(path)] if path else ([Path(os.environ["M3_REFERENCE_PATH"])]
                                               if os.getenv("M3_REFERENCE_PATH") else list(_DEFAULT_PATHS))
        for candidate in candidates:
            if candidate.is_file():
                self._load(candidate)
                break

    def _load(self, path: Path) -> None:
        body = json.loads(path.read_text(encoding="utf-8"))
        order = body["feature_order"]
        for service, entry in body["services"].items():
            mean = {name: float(entry["mean"][order.index(name)]) for name in SIGNALS}
            spread = {name: float(entry["spread"][order.index(name)]) for name in SIGNALS}
            self._services[service] = Normal(mean, spread, "learned", int(entry.get("n", 0)))
        self.path = path

    def normal(self, service: str) -> Normal | None:
        return self._services.get(service)

    @property
    def services(self) -> list[str]:
        return sorted(self._services)


def anchored(learned: Normal, mean: dict[str, float], samples: int) -> Normal:
    """M2's learned noise shape at a measured operating point.

    The operating point (how much traffic, what latency) belongs to the environment and is measured by M1
    just before the incident. The noise shape (how much each signal wobbles relative to its level) is a
    property of the service, learned by M2. A signal whose learned mean is ~0 (errors) keeps its absolute
    learned spread."""
    spread = {}
    for name in SIGNALS:
        level = abs(learned.mean[name])
        scaled = learned.spread[name] * abs(mean[name]) / level if level > 1e-9 else learned.spread[name]
        spread[name] = _spread(scaled, mean[name])
    return Normal(mean, spread, "anchored", samples)


def operating_point(baseline) -> dict[str, float]:
    replicas = max(1.0, float(baseline.replica_count.average))
    return {name: float(getattr(baseline, name).average) / (replicas if name in LOAD_SIGNALS else 1.0)
            for name in SIGNALS}


def normal_for(service: str, baseline, reference: "Reference | None" = None) -> Normal | None:
    """Learned noise at M1's measured operating point; either source alone when the other is missing."""
    learned = (reference or shared_reference()).normal(service)
    if learned is not None and baseline is not None:
        return anchored(learned, operating_point(baseline), int(baseline.sample_count))
    if baseline is not None:
        return baseline_normal(baseline)
    return learned


def baseline_normal(baseline) -> Normal:
    """A per-service normal from M1's measured healthy baseline (fallback when M2 has no reference)."""
    replicas = max(1.0, float(baseline.replica_count.average))
    mean, spread = {}, {}
    for name in SIGNALS:
        stats = getattr(baseline, name)
        average, p95 = float(stats.average), float(stats.p95)
        if name in LOAD_SIGNALS:
            average, p95 = average / replicas, p95 / replicas
        mean[name] = average
        spread[name] = _spread(p95 - average, average)
    return Normal(mean, spread, "baseline", int(baseline.sample_count))


def deviations(normal: Normal, values: dict[str, float], replicas: float) -> dict[str, float]:
    """Usual deviations from normal per signal (load per replica, above normal only)."""
    out = {}
    for name in SIGNALS:
        value = float(values[name])
        if not math.isfinite(value):
            out[name] = 0.0
            continue
        if name in LOAD_SIGNALS:
            value = value / max(1.0, float(replicas))
            out[name] = max(0.0, (value - normal.mean[name]) / normal.spread[name])
        else:
            out[name] = abs(value - normal.mean[name]) / normal.spread[name]
    return out


_shared: Reference | None = None


def shared_reference() -> Reference:
    global _shared
    if _shared is None:
        _shared = Reference()
    return _shared


def set_reference(reference: Reference | None) -> None:
    """Tests: inject a reference (None reloads from the default locations)."""
    global _shared
    _shared = reference
