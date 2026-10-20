import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(
    0,
    str(Path(__file__).resolve().parents[1]),
)

from app.models import (
    AnomalyEvent,
    AnomalyFeatures,
    DeploymentEvent,
    EvidenceBundle,
    Incident,
    TelemetryMetrics,
    TelemetrySnapshot,
)

from app.scoring import (
    FAULTY_DEPLOYMENT,
    TRAFFIC_SPIKE,
    UNKNOWN,
    determine_root_cause,
)


def make_incident() -> Incident:
    return Incident(
        incident_id="INC-001",
        started_at=datetime(
            2026,
            9,
            27,
            10,
            0,
            42,
            tzinfo=timezone.utc,
        ),
        status="DETECTED",
        severity="high",
        affected_services=["payment-service"],
        anomaly_ids=["ANO-001"],
    )


def make_deployment() -> DeploymentEvent:
    return DeploymentEvent(
        event_id="DEP-001",
        service="payment-service",
        old_version="v1",
        new_version="v2",
        commit_sha="abc123",
        pipeline_id="jenkins-42",
        timestamp=datetime(
            2026,
            9,
            27,
            10,
            0,
            0,
            tzinfo=timezone.utc,
        ),
        status="SUCCESS",
    )


def make_anomaly(
    request_rate: float = 180,
    latency_p95_ms: float = 820,
    http_5xx_rate: float = 0.14,
    cpu: float = 0.72,
) -> AnomalyEvent:
    return AnomalyEvent(
        anomaly_id="ANO-001",
        timestamp=datetime(
            2026,
            9,
            27,
            10,
            0,
            42,
            tzinfo=timezone.utc,
        ),
        service="payment-service",
        score=0.94,
        severity="high",
        model="isolation_forest",
        features=AnomalyFeatures(
            request_rate=request_rate,
            latency_p95_ms=latency_p95_ms,
            http_5xx_rate=http_5xx_rate,
            cpu=cpu,
        ),
    )


def make_telemetry(
    request_rate: float = 180,
    latency_p95_ms: float = 820,
    http_5xx_rate: float = 0.14,
    cpu: float = 0.72,
) -> TelemetrySnapshot:
    return TelemetrySnapshot(
        timestamp=datetime(
            2026,
            9,
            27,
            10,
            0,
            0,
            tzinfo=timezone.utc,
        ),
        service="payment-service",
        version="v2",
        metrics=TelemetryMetrics(
            cpu=cpu,
            memory=0.61,
            request_rate=request_rate,
            latency_p95_ms=latency_p95_ms,
            http_5xx_rate=http_5xx_rate,
            replica_count=3,
        ),
    )


def make_bundle(
    anomaly=None,
    telemetry=None,
    deployment=None,
    provider_errors=None,
) -> EvidenceBundle:
    return EvidenceBundle(
        incident=make_incident(),
        telemetry=telemetry,
        anomaly=anomaly,
        deployment=deployment,
        provider_errors=provider_errors or [],
    )


def test_faulty_deployment_is_selected():
    # Normal load (10 req/s per replica, payment-service's learned normal); latency and errors far off normal.
    bundle = make_bundle(
        anomaly=make_anomaly(request_rate=30),
        telemetry=make_telemetry(request_rate=30),
        deployment=make_deployment(),
    )

    result = determine_root_cause(bundle)

    assert result.root_cause == FAULTY_DEPLOYMENT
    assert result.affected_component == "payment-service:v2"
    assert result.confidence >= 0.9
    assert len(result.evidence) == 3
    assert "usual deviations from payment-service's learned normal" in result.evidence[1]


def cluster_baseline(rate=10.0, latency=9.5, replicas=1):
    """M1's measured operating point in the kind cluster demo (10 req/s on one replica, p95 ~9.5 ms)."""
    from app.models import HealthyBaseline
    stats = lambda value: {"minimum": value, "maximum": value, "average": value, "p95": value}
    return HealthyBaseline.model_validate({
        "service": "payment-service", "version": "v1", "measured_at": "2026-09-27T09:58:00Z",
        "window_start": "2026-09-27T09:56:30Z", "window_end": "2026-09-27T09:57:45Z", "sample_count": 6,
        "request_rate": stats(rate), "latency_p95_ms": stats(latency), "http_5xx_rate": stats(0.0),
        "cpu": stats(0.05), "memory": stats(0.2), "replica_count": stats(replicas),
        "thresholds": {"latency_p95_ms_max": 25.0, "http_5xx_rate_max": 0.01}})


def test_a_release_during_a_traffic_surge_stays_ambiguous():
    # 60 req/s per replica is six times the measured operating point: release or load cannot be told apart.
    bundle = make_bundle(anomaly=make_anomaly(), telemetry=make_telemetry(), deployment=make_deployment())
    bundle.baseline = cluster_baseline(rate=30, replicas=3)

    result = determine_root_cause(bundle)

    assert result.root_cause == UNKNOWN and result.confidence <= 0.5
    assert any("ambiguous" in line for line in result.evidence)


def test_traffic_spike_is_selected():
    bundle = make_bundle(
        anomaly=make_anomaly(
            request_rate=500,
            latency_p95_ms=600,
            http_5xx_rate=0.01,
            cpu=0.95,
        ),
        telemetry=make_telemetry(
            request_rate=500,
            latency_p95_ms=600,
            http_5xx_rate=0.01,
            cpu=0.95,
        ),
        deployment=None,
    )

    result = determine_root_cause(bundle)

    assert result.root_cause == TRAFFIC_SPIKE
    assert result.affected_component == "payment-service"


def test_unknown_when_evidence_is_insufficient():
    bundle = make_bundle(
        anomaly=make_anomaly(
            request_rate=180,
            latency_p95_ms=100,
            http_5xx_rate=0.01,
            cpu=0.50,
        ),
        telemetry=make_telemetry(
            request_rate=180,
            latency_p95_ms=100,
            http_5xx_rate=0.01,
            cpu=0.50,
        ),
        deployment=None,
    )

    result = determine_root_cause(bundle)

    assert result.root_cause == UNKNOWN
    assert result.affected_component == "payment-service"


def test_missing_deployment_does_not_create_fake_deployment_evidence():
    bundle = make_bundle(
        anomaly=make_anomaly(),
        telemetry=make_telemetry(),
        deployment=None,
        provider_errors=["deployment: provider unavailable"],
    )

    result = determine_root_cause(bundle)

    assert result.root_cause != FAULTY_DEPLOYMENT
    assert result.root_cause in {
        TRAFFIC_SPIKE,
        UNKNOWN,
    }

def test_live_cluster_spike_is_a_confident_traffic_spike():
    """Regression: payment-service's training captures ran at ~85 req/s per replica, the cluster demo at 10.
    Load must be judged against M1's measured operating point (with M2's learned noise shape), otherwise the
    live spike (78 req/s on one replica, p95 9.7 s, measured 2026-10-19) scores zero load and goes unexplained."""
    spike = dict(request_rate=78.7, latency_p95_ms=9697, http_5xx_rate=0.0, cpu=0.99)
    bundle = make_bundle(anomaly=make_anomaly(**spike), telemetry=make_telemetry(**spike), deployment=None)
    bundle.telemetry.metrics.replica_count = 1
    bundle.telemetry.version = "v1"
    bundle.baseline = cluster_baseline()

    result = determine_root_cause(bundle)

    assert result.root_cause == TRAFFIC_SPIKE and result.confidence >= 0.9
    assert any("M2's learned noise at M1's measured operating point" in line for line in result.evidence)
