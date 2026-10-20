"""Attribution: learned evidence, rule gates, calibrated confidence.

Evidence (learned): every signal is measured in usual deviations from the service's own normal, learned
by M2 from real captured healthy telemetry (app/deviation.py). Each candidate cause gets a raw evidence
score from those deviations.

Gates (rules, deliberately explicit): a release is blamed only if it changed the version within five
minutes before the anomaly and the live telemetry shows that version; traffic is never blamed right after
a version change; a release that coincides with elevated traffic is ambiguous; incomplete evidence
(provider errors) can never yield a confident cause.

Confidence (calibrated): the raw score becomes a probability through a logistic map fitted on labelled
real captures (app/calibration.json, scripts/calibrate.py), so "0.9" means the cause was present in about
90% of the recorded readings with that much evidence.
"""

import json
import math
from dataclasses import dataclass
from pathlib import Path

from .deviation import deviations, normal_for
from .models import EvidenceBundle

FAULTY_DEPLOYMENT = "faulty_deployment"
TRAFFIC_SPIKE = "traffic_spike"
UNKNOWN = "unknown"

DEGRADED = 3.0       # a health signal this many usual deviations from normal is degraded
LOAD_HIGH = 3.0      # load per replica this many usual deviations ABOVE normal is elevated
MIN_CONFIDENCE = 0.60
MIN_MARGIN = 0.10
THROTTLING_EVIDENCE_RATIO = 0.25
CALIBRATION_PATH = Path(__file__).with_name("calibration.json")


@dataclass
class ScoringResult:
    root_cause: str
    confidence: float
    affected_component: str
    evidence: list[str]


@dataclass(frozen=True)
class Evidence:
    """One candidate cause: its raw learned-evidence score and what it rests on."""
    raw: float
    lines: list[str]
    ambiguous: bool = False


def load_calibration(path: Path = CALIBRATION_PATH) -> dict:
    body = json.loads(path.read_text(encoding="utf-8"))
    return {cause: (float(body["causes"][cause]["a"]), float(body["causes"][cause]["b"]))
            for cause in (FAULTY_DEPLOYMENT, TRAFFIC_SPIKE)}


_calibration = None


def calibrated(cause: str, raw: float) -> float:
    global _calibration
    if _calibration is None:
        _calibration = load_calibration()
    if raw <= 0:
        return 0.0
    a, b = _calibration[cause]
    return 1.0 / (1.0 + math.exp(-(a * raw + b)))


def raw_score(cause: str, d: dict[str, float]) -> float:
    """The raw evidence for a cause, from usual deviations (the quantity the calibration is fitted on)."""
    if cause == FAULTY_DEPLOYMENT:
        return math.log1p(d["latency_p95_ms"]) + math.log1p(d["http_5xx_rate"])
    return math.log1p(d["request_rate"]) + 0.5 * math.log1p(max(d["latency_p95_ms"], d["cpu"]))


def _recent_changed_deployment(bundle):
    if bundle.deployment is None or bundle.anomaly is None:
        return False
    event = bundle.deployment
    delta = (bundle.anomaly.timestamp - event.timestamp).total_seconds()
    return (event.old_version != event.new_version
            and event.status.upper() == "SUCCESS" and 0 <= delta <= 300)


def _signals(bundle):
    """(deviations, normal) for the readings both M2's alert and M1's current telemetry agree on."""
    anomaly, telemetry = bundle.anomaly, bundle.telemetry
    if anomaly is None or telemetry is None or anomaly.service != telemetry.service:
        return None, None
    normal = normal_for(anomaly.service, bundle.baseline)
    if normal is None:
        return None, None
    # Conservative: a signal counts only as far as BOTH readings show it.
    values = {name: min(getattr(anomaly.features, name), getattr(telemetry.metrics, name))
              for name in ("request_rate", "latency_p95_ms", "http_5xx_rate", "cpu")}
    return deviations(normal, values, telemetry.metrics.replica_count), normal


def _degraded(bundle, d, signal):
    """Far from the learned normal AND past the measured SLO limit (the limit M1 judges recovery by).

    Deviations measure how strong the evidence is; the SLO limit says whether users are affected. A reading
    inside the limit is healthy even when it is statistically unusual."""
    if d[signal] < DEGRADED:
        return False
    if bundle.baseline is None:
        return True
    value = min(getattr(bundle.anomaly.features, signal), getattr(bundle.telemetry.metrics, signal))
    limit = (bundle.baseline.thresholds.latency_p95_ms_max if signal == "latency_p95_ms"
             else bundle.baseline.thresholds.http_5xx_rate_max)
    return value > limit


def _source(normal, service):
    if normal.source == "anchored":
        return f"{service}'s normal (M2's learned noise at M1's measured operating point)"
    if normal.source == "learned":
        return f"{service}'s learned normal (M2 reference, {normal.samples} healthy readings)"
    return f"{service}'s measured healthy baseline (M1)"


def _deployment_evidence(bundle, d, normal) -> Evidence:
    anomaly, telemetry, deployment = bundle.anomaly, bundle.telemetry, bundle.deployment
    if (d is None or deployment is None or not _recent_changed_deployment(bundle)
            or deployment.service != anomaly.service or telemetry.service != anomaly.service
            or deployment.new_version != telemetry.version
            or (bundle.baseline is not None and bundle.baseline.version != deployment.old_version)):
        return Evidence(0.0, [])
    latency_degraded, errors_degraded = _degraded(bundle, d, "latency_p95_ms"), _degraded(bundle, d, "http_5xx_rate")
    # A recent release by itself does not establish a faulty release.
    if not latency_degraded and not errors_degraded:
        return Evidence(0.0, [])
    delta = (anomaly.timestamp - deployment.timestamp).total_seconds()
    lines = [f"{deployment.service}:{deployment.new_version} deployed {int(delta)} seconds before anomaly"]
    if d["request_rate"] >= LOAD_HIGH:
        lines.append("Recent deployment and elevated traffic overlap; root cause is ambiguous")
        return Evidence(raw_score(FAULTY_DEPLOYMENT, d), lines, ambiguous=True)
    source = _source(normal, anomaly.service)
    if latency_degraded:
        lines.append(f"P95 latency increased after deployment: {d['latency_p95_ms']:.0f} usual deviations from {source}")
    if errors_degraded:
        lines.append(f"HTTP 5xx increased: {d['http_5xx_rate']:.0f} usual deviations from {source}")
    return Evidence(raw_score(FAULTY_DEPLOYMENT, d), lines)


def _traffic_evidence(bundle, d, normal) -> Evidence:
    anomaly, telemetry, baseline = bundle.anomaly, bundle.telemetry, bundle.baseline
    # Do not call a new, recently changed version a capacity issue.
    if d is None or _recent_changed_deployment(bundle):
        return Evidence(0.0, [])
    if baseline is not None and telemetry.version != baseline.version:
        return Evidence(0.0, [])
    load_high = d["request_rate"] >= LOAD_HIGH
    # Capacity is proposed only for user-visible degradation: busy CPU with healthy latency is not an incident.
    strained = _degraded(bundle, d, "latency_p95_ms")
    # Errors beyond the SLO limit are not explained by load alone: leave them unattributed. (Absolute limit:
    # a service that never errors has a near-zero spread, so deviations would overstate tiny error rates.)
    errors = min(anomaly.features.http_5xx_rate, telemetry.metrics.http_5xx_rate)
    error_limit = baseline.thresholds.http_5xx_rate_max if baseline is not None else 0.05
    if not load_high or not strained or errors > error_limit:
        return Evidence(0.0, [])
    source = _source(normal, anomaly.service)
    lines = ["Request rate increased significantly",
             f"Request rate per replica is {d['request_rate']:.0f} usual deviations above {source}"]
    if baseline is not None:
        rate = min(anomaly.features.request_rate, telemetry.metrics.request_rate)
        if rate >= baseline.request_rate.average * 1.5:
            lines.append(f"Request rate exceeds measured healthy baseline {baseline.request_rate.average:.2f} "
                         "requests/sec by at least 1.5x")
    lines.append(f"P95 latency is {d['latency_p95_ms']:.0f} usual deviations from {source}")
    if d["cpu"] >= DEGRADED:
        lines.append("CPU utilization increased significantly")
    throttled = (bundle.cpu_throttling or {}).get("throttled_period_ratio")
    if throttled is not None and throttled >= THROTTLING_EVIDENCE_RATIO:
        # Corroborates saturation; it names the CPU limit as the constraint without changing the score.
        lines.append(f"CPU limit throttled the service in {throttled:.0%} of CFS periods")
    return Evidence(raw_score(TRAFFIC_SPIKE, d), lines)


def determine_root_cause(bundle: EvidenceBundle) -> ScoringResult:
    d, normal = _signals(bundle)
    found = {FAULTY_DEPLOYMENT: _deployment_evidence(bundle, d, normal),
             TRAFFIC_SPIKE: _traffic_evidence(bundle, d, normal)}
    scores = {}
    for cause, evidence in found.items():
        probability = calibrated(cause, evidence.raw)
        # An ambiguous release cannot be a confident cause, however strong its signals.
        scores[cause] = min(probability, 0.5) if evidence.ambiguous else probability
    ranked = sorted(scores, key=scores.get, reverse=True)
    cause, score = ranked[0], scores[ranked[0]]
    evidence = found[cause].lines
    service = bundle.anomaly.service if bundle.anomaly else (
        bundle.telemetry.service if bundle.telemetry else bundle.incident.affected_services[0])
    if bundle.provider_errors:
        # Incomplete source coverage cannot authorize a confident automated cause.
        return ScoringResult(UNKNOWN, round(min(score, 0.59), 2), service, evidence)
    if score < MIN_CONFIDENCE or score - scores[ranked[1]] < MIN_MARGIN:
        return ScoringResult(UNKNOWN, round(score, 2), service, evidence)
    component = f"{bundle.deployment.service}:{bundle.deployment.new_version}" if cause == FAULTY_DEPLOYMENT else service
    return ScoringResult(cause, round(score, 2), component, evidence)
