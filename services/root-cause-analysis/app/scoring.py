"""Conservative deterministic attribution from observed, linked evidence."""

from dataclasses import dataclass

from .models import EvidenceBundle


FAULTY_DEPLOYMENT = "faulty_deployment"
TRAFFIC_SPIKE = "traffic_spike"
UNKNOWN = "unknown"


@dataclass
class ScoringResult:
    root_cause: str
    confidence: float
    affected_component: str
    evidence: list[str]


def _limits(bundle):
    baseline = bundle.baseline
    return ((baseline.thresholds.latency_p95_ms_max, baseline.thresholds.http_5xx_rate_max)
            if baseline is not None else (500.0, 0.05))


def _recent_changed_deployment(bundle):
    if bundle.deployment is None or bundle.anomaly is None:
        return False
    event = bundle.deployment
    delta = (bundle.anomaly.timestamp - event.timestamp).total_seconds()
    return (event.old_version != event.new_version
            and event.status.upper() == "SUCCESS" and 0 <= delta <= 300)


def _deployment_score(bundle: EvidenceBundle) -> tuple[float, list[str]]:
    anomaly, telemetry, deployment = bundle.anomaly, bundle.telemetry, bundle.deployment
    if (anomaly is None or telemetry is None or deployment is None
            or not _recent_changed_deployment(bundle)
            or deployment.service != anomaly.service or telemetry.service != anomaly.service
            or deployment.new_version != telemetry.version
            or (bundle.baseline is not None and bundle.baseline.version != deployment.old_version)):
        return 0.0, []
    latency_limit, error_limit = _limits(bundle)
    latency = min(anomaly.features.latency_p95_ms, telemetry.metrics.latency_p95_ms)
    errors = min(anomaly.features.http_5xx_rate, telemetry.metrics.http_5xx_rate)
    latency_degraded = latency > latency_limit if bundle.baseline is not None else latency >= latency_limit
    errors_degraded = errors > error_limit if bundle.baseline is not None else errors >= error_limit
    # A recent release by itself does not establish a faulty release.
    if not latency_degraded and not errors_degraded:
        return 0.0, []
    delta = (anomaly.timestamp - deployment.timestamp).total_seconds()
    evidence = [f"{deployment.service}:{deployment.new_version} deployed {int(delta)} seconds before anomaly"]
    if (bundle.baseline is not None
            and min(anomaly.features.request_rate, telemetry.metrics.request_rate)
            >= bundle.baseline.request_rate.average * 1.5):
        evidence.append("Recent deployment and elevated traffic overlap; root cause is ambiguous")
        return 0.50, evidence
    score = 0.50
    if latency_degraded:
        score += 0.20
        evidence.append("P95 latency increased after deployment")
    if errors_degraded:
        score += 0.20
        evidence.append("HTTP 5xx increased")
    return min(score, 1.0), evidence


def _traffic_score(bundle: EvidenceBundle) -> tuple[float, list[str]]:
    anomaly, telemetry, baseline = bundle.anomaly, bundle.telemetry, bundle.baseline
    if anomaly is None or telemetry is None or anomaly.service != telemetry.service:
        return 0.0, []
    # Do not call a new, recently changed version a capacity issue.
    if _recent_changed_deployment(bundle):
        return 0.0, []
    if baseline is not None and telemetry.version != baseline.version:
        return 0.0, []
    rate_limit = max(0.1, baseline.request_rate.average * 1.5) if baseline is not None else 300.0
    latency_limit, error_limit = _limits(bundle)
    rate_increased = (anomaly.features.request_rate >= rate_limit
                      and telemetry.metrics.request_rate >= rate_limit)
    latency = min(anomaly.features.latency_p95_ms, telemetry.metrics.latency_p95_ms)
    latency_degraded = latency > latency_limit if baseline is not None else latency >= latency_limit
    if (not rate_increased or not latency_degraded
            or anomaly.features.http_5xx_rate > error_limit or telemetry.metrics.http_5xx_rate > error_limit):
        return 0.0, []
    evidence = ["Request rate increased significantly"]
    if baseline is not None:
        evidence.append(f"Request rate exceeds measured healthy baseline {baseline.request_rate.average:.2f} requests/sec by at least 1.5x")
    score = 0.35 + 0.15 + 0.15  # Increased load, degraded latency, current telemetry corroboration.
    if anomaly.features.cpu >= 0.85 and telemetry.metrics.cpu >= 0.85:
        score += 0.25
        evidence.append("CPU utilization increased significantly")
    if not bundle.provider_errors:
        score += 0.10
    return min(score, 1.0), evidence


def determine_root_cause(bundle: EvidenceBundle) -> ScoringResult:
    deployment_score, deployment_evidence = _deployment_score(bundle)
    traffic_score, traffic_evidence = _traffic_score(bundle)
    candidates = [(FAULTY_DEPLOYMENT, deployment_score, deployment_evidence),
                  (TRAFFIC_SPIKE, traffic_score, traffic_evidence)]
    candidates.sort(key=lambda item: item[1], reverse=True)
    cause, score, evidence = candidates[0]
    service = bundle.anomaly.service if bundle.anomaly else (
        bundle.telemetry.service if bundle.telemetry else bundle.incident.affected_services[0])
    if bundle.provider_errors:
        # Incomplete source coverage cannot authorize a confident automated cause.
        return ScoringResult(UNKNOWN, round(min(score, 0.59), 2), service, evidence)
    if score < 0.60 or score - candidates[1][1] < 0.10:
        return ScoringResult(UNKNOWN, round(score, 2), service, evidence)
    component = f"{bundle.deployment.service}:{bundle.deployment.new_version}" if cause == FAULTY_DEPLOYMENT else service
    return ScoringResult(cause, round(score, 2), component, evidence)
