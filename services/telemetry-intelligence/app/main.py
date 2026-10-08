from datetime import datetime
import logging
import os
from pathlib import Path
import socket
import sys
import time

from fastapi import FastAPI, Query, Request, Response
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared.logging import configure_json_logging

from .config import M1Settings, load_m1_settings
from .errors import M1Error
from .models import (
    CaptureEvidenceRequest,
    DeploymentEvent,
    HealthResponse,
    HealthyBaseline,
    IncidentEvidence,
    LogEntriesResponse,
    RecoveryResult,
    RecoveryEvidenceRecord,
    RecoveryValidationRequest,
    TelemetrySnapshot,
    TelemetryWindow,
    TimeWindowRequest,
)
from .providers.health import ServiceHealthClient
from .providers.kubernetes_api import InClusterKubernetesClient
from .providers.kubernetes import KubectlKubernetesClient
from .providers.loki import LokiClient
from .providers.prometheus import PrometheusClient
from .service import M1Service
from .storage import EvidenceStore
from .telemetry import MockTelemetryProvider, PrometheusTelemetryProvider


METRIC_LABELS = ("service_name", "namespace", "pod", "version", "environment")
REQUESTS = Counter(
    "nexus_http_requests_total",
    "HTTP requests handled by a NEXUS service.",
    METRIC_LABELS + ("method", "route", "status_code"),
)
REQUEST_DURATION = Histogram(
    "nexus_http_request_duration_seconds",
    "HTTP request duration for a NEXUS service.",
    METRIC_LABELS + ("method", "route"),
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 0.75, 1.0, 2.5, 5.0),
)
SERVICE_INFO = Gauge(
    "nexus_service_info",
    "Static service identity used for version and replica discovery.",
    METRIC_LABELS,
)
CPU_LIMIT = Gauge(
    "nexus_resource_limit_cpu_cores",
    "Configured CPU limit in cores.",
    METRIC_LABELS,
)
MEMORY_LIMIT = Gauge(
    "nexus_resource_limit_memory_bytes",
    "Configured memory limit in bytes.",
    METRIC_LABELS,
)


def build_default_service(settings: M1Settings) -> M1Service:
    if settings.provider_mode == "real":
        prometheus = PrometheusClient(
            settings.prometheus_base_url,
            timeout=settings.provider_timeout_seconds,
        )
        telemetry = PrometheusTelemetryProvider(prometheus, settings)
    elif settings.provider_mode == "mock":
        telemetry = MockTelemetryProvider(settings.mock_metrics_path)
    else:
        raise ValueError("M1_PROVIDER_MODE must be either 'real' or 'mock'")

    if settings.kubernetes_api_url and settings.kubernetes_token_path.exists():
        kubernetes = InClusterKubernetesClient(
            settings.kubernetes_api_url,
            settings.kubernetes_token_path,
            settings.kubernetes_ca_path,
            timeout=settings.provider_timeout_seconds,
        )
    else:
        kubernetes = KubectlKubernetesClient(
            settings.kubectl_path,
            timeout=settings.provider_timeout_seconds,
        )

    return M1Service(
        settings=settings,
        telemetry=telemetry,
        loki=LokiClient(
            settings.loki_base_url,
            timeout=settings.provider_timeout_seconds,
        ),
        kubernetes=kubernetes,
        service_health=ServiceHealthClient(
            {"payment-service": settings.payment_service_base_url},
            timeout=settings.provider_timeout_seconds,
        ),
        store=EvidenceStore(settings.data_dir),
    )


def create_app(
    settings: M1Settings | None = None,
    service: M1Service | None = None,
) -> FastAPI:
    settings = settings or load_m1_settings()
    configure_json_logging(
        settings.runtime.service_name,
        settings.runtime.service_version,
        settings.runtime.environment,
        settings.runtime.log_level,
    )
    logger = logging.getLogger(__name__)
    service = service or build_default_service(settings)

    namespace = os.getenv("NAMESPACE", settings.namespace)
    pod = os.getenv("POD_NAME", socket.gethostname())
    common_labels = (
        settings.runtime.service_name,
        namespace,
        pod,
        settings.runtime.service_version,
        settings.runtime.environment,
    )
    SERVICE_INFO.labels(*common_labels).set(1)
    CPU_LIMIT.labels(*common_labels).set(float(os.getenv("CPU_LIMIT_CORES", "1")))
    MEMORY_LIMIT.labels(*common_labels).set(
        float(os.getenv("MEMORY_LIMIT_BYTES", "536870912"))
    )

    api = FastAPI(
        title="NEXUS Telemetry Intelligence",
        version=settings.runtime.service_version,
    )
    api.state.m1_service = service

    @api.exception_handler(M1Error)
    async def m1_error_handler(_: Request, exc: M1Error) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "detail": {
                    "category": exc.category,
                    "message": exc.message,
                    "retryable": exc.retryable,
                }
            },
        )

    @api.middleware("http")
    async def observe_http_request(request: Request, call_next):
        if request.url.path == "/metrics":
            return await call_next(request)

        started = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        finally:
            duration_seconds = time.perf_counter() - started
            route_object = request.scope.get("route")
            route = getattr(route_object, "path", request.url.path)
            REQUESTS.labels(
                *common_labels,
                request.method,
                route,
                str(status_code),
            ).inc()
            REQUEST_DURATION.labels(
                *common_labels,
                request.method,
                route,
            ).observe(duration_seconds)
            logger.info(
                "request completed",
                extra={
                    "service_name": settings.runtime.service_name,
                    "duration_ms": round(duration_seconds * 1000, 2),
                    "error_category": None if status_code < 500 else "server_error",
                },
            )

    @api.get("/live")
    async def live() -> dict[str, str]:
        return {"status": "ok", "service": settings.runtime.service_name}

    @api.get("/health", response_model=HealthResponse)
    async def health():
        result = await service.health()
        if result.status == "unavailable":
            return JSONResponse(status_code=503, content=result.model_dump(mode="json"))
        return result

    @api.get("/metrics", include_in_schema=False)
    async def metrics() -> Response:
        return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)

    @api.get(
        "/internal/telemetry/snapshot",
        response_model=TelemetrySnapshot,
    )
    async def telemetry_snapshot(
        requested_service: str = Query(alias="service", min_length=1),
    ) -> TelemetrySnapshot:
        return await service.snapshot(requested_service)

    @api.get("/internal/telemetry/window", response_model=TelemetryWindow)
    async def telemetry_window(
        requested_service: str = Query(alias="service", min_length=1),
        start: datetime = Query(),
        end: datetime = Query(),
        step_seconds: int = Query(default=15, ge=5, le=300),
    ) -> TelemetryWindow:
        try:
            request = TimeWindowRequest(
                service=requested_service, start=start, end=end, step_seconds=step_seconds,
            )
        except ValidationError as exc:
            raise RequestValidationError(exc.errors(include_context=False)) from exc
        return await service.window(request)

    @api.post("/internal/baselines/measure", response_model=HealthyBaseline)
    async def measure_baseline(request: TimeWindowRequest) -> HealthyBaseline:
        return await service.measure_baseline(request)

    @api.get("/internal/baselines/{service_name}", response_model=HealthyBaseline)
    async def get_baseline(service_name: str) -> HealthyBaseline:
        return service.store.get_baseline(service_name)

    @api.post("/internal/evidence/capture", response_model=IncidentEvidence)
    async def capture_evidence(request: CaptureEvidenceRequest) -> IncidentEvidence:
        return await service.capture_evidence(request)

    @api.get("/internal/evidence/{incident_id}", response_model=IncidentEvidence)
    async def get_evidence(incident_id: str) -> IncidentEvidence:
        return service.store.get_evidence(incident_id)

    @api.get("/internal/telemetry", response_model=TelemetrySnapshot)
    async def incident_telemetry(
        incident_id: str = Query(min_length=1),
    ) -> TelemetrySnapshot:
        return service.store.get_evidence(incident_id).before

    @api.get("/internal/deployments", response_model=DeploymentEvent)
    async def incident_deployment(
        incident_id: str = Query(min_length=1),
    ) -> DeploymentEvent:
        evidence = service.store.get_evidence(incident_id)
        if evidence.deployment_event is None:
            raise M1Error(
                "deployment_evidence_missing",
                f"No deployment evidence was captured for incident {incident_id}",
                status_code=404,
                retryable=False,
            )
        return evidence.deployment_event

    @api.get("/internal/logs", response_model=LogEntriesResponse)
    async def incident_logs(
        incident_id: str = Query(min_length=1),
    ) -> LogEntriesResponse:
        evidence = service.store.get_evidence(incident_id)
        return LogEntriesResponse(
            incident_id=evidence.incident_id,
            service=evidence.service,
            entries=evidence.selected_logs,
        )

    @api.post("/internal/recovery/validate", response_model=RecoveryResult)
    async def recovery_validate(
        request: RecoveryValidationRequest,
    ) -> RecoveryResult:
        return await service.validate_recovery(request)

    @api.get(
        "/internal/recovery/{incident_id}",
        response_model=RecoveryEvidenceRecord,
    )
    async def get_recovery_evidence(incident_id: str) -> RecoveryEvidenceRecord:
        return service.store.get_recovery(incident_id)

    return api


app = create_app()
