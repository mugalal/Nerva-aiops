from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.config import M1Settings
from app.models import (
    DeploymentEvent,
    KubernetesContext,
    TelemetryMetrics,
    TelemetrySnapshot,
    TelemetryWindow,
    TelemetryWindowPoint,
)
from app.providers.loki import LogEntry
from app.service import M1Service
from app.storage import EvidenceStore
from shared.config import RuntimeSettings


def make_settings(data_dir: Path) -> M1Settings:
    return M1Settings(
        runtime=RuntimeSettings(
            service_name="telemetry-intelligence",
            service_version="0.1.0",
            environment="test",
            log_level="WARNING",
            database_url=None,
            shared_nexus_api_base_url="http://shared",
            m1_telemetry_base_url="http://m1",
            m2_anomaly_base_url="http://m2",
            m3_rca_base_url="http://m3",
            m4_decision_base_url="http://m4",
            m5_memory_base_url="http://m5",
            m6_finops_base_url="http://m6",
        ),
        provider_mode="real",
        prometheus_base_url="http://prometheus",
        loki_base_url="http://loki",
        payment_service_base_url="http://payment-service",
        data_dir=data_dir,
        mock_metrics_path=data_dir / "mock_metrics.json",
        namespace="nexus-demo",
        kubectl_path="kubectl",
        kubernetes_api_url="",
        kubernetes_token_path=data_dir / "service-account-token",
        kubernetes_ca_path=data_dir / "service-account-ca.crt",
        provider_timeout_seconds=1,
        stale_after_seconds=60,
        history_step_seconds=15,
        request_rate_window="1m",
        latency_window="1m",
        baseline_latency_multiplier=1.25,
        baseline_error_multiplier=1.5,
        baseline_error_floor=0.01,
        traffic_continuity_ratio=0.5,
        log_limit=100,
    )


def make_snapshot(
    *,
    version: str = "v1",
    request_rate: float = 20,
    latency: float = 80,
    error_rate: float = 0,
    replicas: int = 1,
) -> TelemetrySnapshot:
    return TelemetrySnapshot(
        timestamp=datetime.now(timezone.utc),
        service="payment-service",
        version=version,
        metrics=TelemetryMetrics(
            cpu=0.25,
            memory=0.30,
            request_rate=request_rate,
            latency_p95_ms=latency,
            http_5xx_rate=error_rate,
            replica_count=replicas,
        ),
    )


def make_window(snapshot: TelemetrySnapshot, sample_count: int = 5) -> TelemetryWindow:
    end = datetime.now(timezone.utc)
    start = end - timedelta(seconds=(sample_count - 1) * 15)
    return TelemetryWindow(
        service=snapshot.service,
        version=snapshot.version,
        start=start,
        end=end,
        step_seconds=15,
        points=[
            TelemetryWindowPoint(
                timestamp=start + timedelta(seconds=index * 15),
                metrics=snapshot.metrics,
            )
            for index in range(sample_count)
        ],
    )


class FakeTelemetry:
    mode = "real"

    def __init__(self, current: TelemetrySnapshot):
        self._current = current
        self.history = make_window(current)

    @property
    def current(self):
        return self._current

    @current.setter
    def current(self, value):
        self._current = value
        self.history = make_window(value)

    async def ready(self) -> tuple[bool, str]:
        return True, "fake Prometheus is ready"

    async def snapshot(self, service: str) -> TelemetrySnapshot:
        return self.current.model_copy(update={"service": service})

    async def window(
        self,
        service: str,
        start: datetime,
        end: datetime,
        step_seconds: int,
    ) -> TelemetryWindow:
        count = min(len(self.history.points), int((end - start).total_seconds() // step_seconds) + 1)
        return self.history.model_copy(
            update={
                "service": service,
                "start": start,
                "end": end,
                "step_seconds": step_seconds,
                "points": [
                    point.model_copy(update={"timestamp": start + timedelta(seconds=index * step_seconds)})
                    for index, point in enumerate(self.history.points[:count])
                ],
            }
        )


class FakeLoki:
    def __init__(self):
        self.entries = [
            LogEntry(
                timestamp=datetime.now(timezone.utc),
                labels={"service_name": "payment-service"},
                line='{"level":"ERROR","message":"simulated payment failure"}',
            )
        ]

    async def ready(self) -> tuple[bool, str]:
        return True, "fake Loki is ready"

    async def query_logs(self, *args, **kwargs) -> list[LogEntry]:
        return self.entries


class FakeKubernetes:
    def __init__(self):
        self.kubernetes_context = KubernetesContext(
            service="payment-service",
            namespace="nexus-demo",
            desired_replicas=1,
            ready_replicas=1,
            pod_restarts=0,
            version="v2",
            service_health="ok",
            events=["ScalingReplicaSet: scaled payment-service"],
        )
        self.event = DeploymentEvent(
            event_id="DEP-2",
            service="payment-service",
            old_version="v1",
            new_version="v2",
            commit_sha="abc123",
            pipeline_id="pipeline-42",
            timestamp=datetime.now(timezone.utc),
            status="SUCCESS",
        )

    async def ready(self) -> tuple[bool, str]:
        return True, "fake Kubernetes is ready"

    async def context(self, *args, **kwargs) -> KubernetesContext:
        return self.kubernetes_context

    async def deployment_event(self, *args, **kwargs) -> DeploymentEvent:
        return self.event


class FakeHealth:
    def __init__(self, healthy: bool = True):
        self.healthy = healthy

    async def check(self, service: str) -> tuple[bool, str]:
        return self.healthy, "status=ok" if self.healthy else "status=degraded"


def make_service(data_dir: Path, current: TelemetrySnapshot):
    telemetry = FakeTelemetry(current)
    health = FakeHealth()
    service = M1Service(
        settings=make_settings(data_dir),
        telemetry=telemetry,
        loki=FakeLoki(),
        kubernetes=FakeKubernetes(),
        service_health=health,
        store=EvidenceStore(data_dir),
    )
    return service, telemetry, health
