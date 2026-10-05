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


def _deployment_score(bundle: EvidenceBundle) -> tuple[float, list[str]]:
    """
    Calculate evidence supporting a faulty deployment.
    """

    score = 0.0
    evidence: list[str] = []

    anomaly = bundle.anomaly
    telemetry = bundle.telemetry
    deployment = bundle.deployment

    if anomaly is None:
        return score, evidence

    if deployment is not None:
        if deployment.service == anomaly.service:

            time_difference = (
                anomaly.timestamp - deployment.timestamp
            ).total_seconds()

            if 0 <= time_difference <= 300:
                score += 0.40

                evidence.append(
                    f"{deployment.service}:{deployment.new_version} "
                    f"deployed {int(time_difference)} seconds before anomaly"
                )

            score += 0.10

    if anomaly.features.latency_p95_ms >= 500:
        score += 0.15

        evidence.append(
            "P95 latency increased after deployment"
        )

    if anomaly.features.http_5xx_rate >= 0.05:
        score += 0.15

        evidence.append(
            "HTTP 5xx increased"
        )

    if telemetry is not None:

        if telemetry.metrics.latency_p95_ms >= 500:
            score += 0.05

        if telemetry.metrics.http_5xx_rate >= 0.05:
            score += 0.05

    return min(score, 1.0), evidence


def _traffic_score(bundle: EvidenceBundle) -> tuple[float, list[str]]:
    """
    Calculate evidence supporting a traffic spike.
    """

    score = 0.0
    evidence: list[str] = []

    anomaly = bundle.anomaly
    telemetry = bundle.telemetry
    deployment = bundle.deployment

    if anomaly is None:
        return score, evidence

    if anomaly.features.request_rate >= 300:
        score += 0.35

        evidence.append(
            "Request rate increased significantly"
        )

    if anomaly.features.cpu >= 0.85:
        score += 0.25

        evidence.append(
            "CPU utilization increased significantly"
        )

    if anomaly.features.latency_p95_ms >= 500:
        score += 0.15

    if telemetry is not None:

        if telemetry.metrics.request_rate >= 300:
            score += 0.15

    # Important:
    # We only treat the absence of deployment evidence as useful
    # when the deployment provider actually returned successfully.
    if deployment is None and not bundle.provider_errors:
        score += 0.10

    return min(score, 1.0), evidence


def determine_root_cause(
    bundle: EvidenceBundle,
) -> ScoringResult:

    deployment_score, deployment_evidence = _deployment_score(
        bundle
    )

    traffic_score, traffic_evidence = _traffic_score(
        bundle
    )

    candidates = [
        (
            FAULTY_DEPLOYMENT,
            deployment_score,
            deployment_evidence,
        ),
        (
            TRAFFIC_SPIKE,
            traffic_score,
            traffic_evidence,
        ),
    ]

    candidates.sort(
        key=lambda item: item[1],
        reverse=True,
    )

    best_cause, best_score, best_evidence = candidates[0]

    second_score = candidates[1][1]

    # Not enough evidence.
    if best_score < 0.60:
        return ScoringResult(
            root_cause=UNKNOWN,
            confidence=round(best_score, 2),
            affected_component=(
                bundle.anomaly.service
                if bundle.anomaly
                else "unknown"
            ),
            evidence=best_evidence,
        )

    # Evidence is too close to confidently distinguish causes.
    if (best_score - second_score) < 0.10:
        return ScoringResult(
            root_cause=UNKNOWN,
            confidence=round(best_score, 2),
            affected_component=(
                bundle.anomaly.service
                if bundle.anomaly
                else "unknown"
            ),
            evidence=best_evidence,
        )

    if best_cause == FAULTY_DEPLOYMENT:

        if bundle.deployment is not None:
            affected_component = (
                f"{bundle.deployment.service}:"
                f"{bundle.deployment.new_version}"
            )
        elif bundle.anomaly is not None:
            affected_component = bundle.anomaly.service
        else:
            affected_component = "unknown"

    else:
        affected_component = (
            bundle.anomaly.service
            if bundle.anomaly
            else "unknown"
        )

    return ScoringResult(
        root_cause=best_cause,
        confidence=round(best_score, 2),
        affected_component=affected_component,
        evidence=best_evidence,
    )