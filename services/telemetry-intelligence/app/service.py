from datetime import datetime, timedelta, timezone
import json
import logging

from .baseline import build_healthy_baseline
from .config import M1Settings
from .errors import M1Error
from .models import (
    CaptureEvidenceRequest,
    DependencyHealth,
    HealthResponse,
    HealthyBaseline,
    IncidentEvidence,
    RecoveryMetrics,
    RecoveryEvidenceRecord,
    RecoveryResult,
    RecoveryValidationRequest,
    TelemetrySnapshot,
    TelemetryWindow,
    TimeWindowRequest,
)
from .providers.health import ServiceHealthClient
from .providers.kubernetes import KubectlKubernetesClient
from .providers.loki import LokiClient
from .storage import EvidenceStore
from .telemetry import TelemetryProvider


logger = logging.getLogger(__name__)


def _select_incident_logs(entries, limit: int = 20) -> list[str]:
    relevant = []
    for entry in entries:
        line = entry.line
        selected = False
        try:
            payload = json.loads(line)
            level = str(payload.get("level", "")).upper()
            status_code = int(payload.get("status_code", 0) or 0)
            selected = level in {"ERROR", "WARNING", "CRITICAL"} or status_code >= 500
        except (TypeError, ValueError, json.JSONDecodeError):
            lowered = line.lower()
            selected = "error" in lowered or "failed" in lowered or " 500 " in lowered
        if selected:
            relevant.append(entry)

    chosen = relevant[-limit:] if relevant else entries[-limit:]
    return [f"{entry.timestamp.isoformat()} {entry.line}" for entry in chosen]


class M1Service:
    def __init__(
        self,
        settings: M1Settings,
        telemetry: TelemetryProvider,
        loki: LokiClient,
        kubernetes: KubectlKubernetesClient,
        service_health: ServiceHealthClient,
        store: EvidenceStore,
    ):
        self.settings = settings
        self.telemetry = telemetry
        self.loki = loki
        self.kubernetes = kubernetes
        self.service_health = service_health
        self.store = store

    async def health(self) -> HealthResponse:
        dependencies: dict[str, DependencyHealth] = {}
        telemetry_ok, telemetry_detail = await self.telemetry.ready()
        dependencies["telemetry_provider"] = DependencyHealth(
            status="ok" if telemetry_ok else "unavailable",
            detail=telemetry_detail,
        )

        loki_ok, loki_detail = await self.loki.ready()
        dependencies["loki"] = DependencyHealth(
            status="ok" if loki_ok else "degraded",
            detail=loki_detail,
        )

        kubernetes_ok, kubernetes_detail = await self.kubernetes.ready()
        dependencies["kubernetes"] = DependencyHealth(
            status="ok" if kubernetes_ok else "degraded",
            detail=kubernetes_detail,
        )

        if self.settings.provider_mode == "mock":
            overall = "degraded"
        elif not telemetry_ok:
            overall = "unavailable"
        elif not loki_ok or not kubernetes_ok:
            overall = "degraded"
        else:
            overall = "ok"

        return HealthResponse(
            service=self.settings.runtime.service_name,
            status=overall,
            version=self.settings.runtime.service_version,
            environment=self.settings.runtime.environment,
            provider_mode=self.settings.provider_mode,
            dependencies=dependencies,
        )

    async def snapshot(self, service: str) -> TelemetrySnapshot:
        return await self.telemetry.snapshot(service)

    async def window(self, request: TimeWindowRequest) -> TelemetryWindow:
        return await self.telemetry.window(
            request.service,
            request.start,
            request.end,
            request.step_seconds,
        )

    async def measure_baseline(self, request: TimeWindowRequest) -> HealthyBaseline:
        window = await self.window(request)
        baseline = build_healthy_baseline(window, self.settings)
        self.store.save_baseline(baseline)
        logger.info(
            "healthy baseline measured",
            extra={"service_name": request.service, "provider": self.telemetry.mode},
        )
        return baseline

    async def capture_evidence(
        self, request: CaptureEvidenceRequest
    ) -> IncidentEvidence:
        now = datetime.now(timezone.utc)
        before = await self.snapshot(request.service)
        provider_errors: list[str] = []
        selected_logs: list[str] = []
        kubernetes = None
        deployment_event = None

        try:
            log_entries = await self.loki.query_logs(
                request.service,
                start=now - timedelta(minutes=5),
                end=now,
                limit=self.settings.log_limit,
            )
            selected_logs = _select_incident_logs(log_entries)
        except M1Error as exc:
            provider_errors.append(exc.message)

        try:
            kubernetes = await self.kubernetes.context(
                request.service, self.settings.namespace
            )
            deployment_event = await self.kubernetes.deployment_event(
                request.service, self.settings.namespace
            )
        except M1Error as exc:
            provider_errors.append(exc.message)

        evidence = IncidentEvidence(
            incident_id=request.incident_id,
            service=request.service,
            scenario=request.scenario,
            captured_at=now,
            before=before,
            selected_logs=selected_logs,
            kubernetes=kubernetes,
            deployment_event=deployment_event,
            provider_errors=provider_errors,
        )
        self.store.save_evidence(evidence)
        logger.info(
            "incident evidence captured",
            extra={
                "incident_id": request.incident_id,
                "service_name": request.service,
                "scenario": request.scenario,
            },
        )
        return evidence

    async def validate_recovery(
        self, request: RecoveryValidationRequest
    ) -> RecoveryResult:
        evidence = self.store.get_evidence(request.incident_id)
        if evidence.service != request.service:
            raise M1Error(
                "evidence_service_mismatch",
                f"Incident evidence is for {evidence.service}, not {request.service}",
                status_code=409,
                retryable=False,
            )
        baseline = self.store.get_baseline(request.service)
        after = await self.snapshot(request.service)
        service_ok, service_detail = await self.service_health.check(request.service)

        thresholds = baseline.thresholds
        slo_restored = (
            after.metrics.latency_p95_ms <= thresholds.latency_p95_ms_max
            and after.metrics.http_5xx_rate <= thresholds.http_5xx_rate_max
        )

        scenario = request.scenario.lower()
        scenario_checks_passed = True
        if "traffic" in scenario or "scale" in scenario:
            minimum_live_traffic = (
                evidence.before.metrics.request_rate
                * self.settings.traffic_continuity_ratio
            )
            scenario_checks_passed = (
                after.metrics.replica_count > evidence.before.metrics.replica_count
                and after.metrics.request_rate >= minimum_live_traffic
                and after.metrics.latency_p95_ms
                < evidence.before.metrics.latency_p95_ms
            )
        elif "deploy" in scenario or "rollback" in scenario:
            scenario_checks_passed = after.version == baseline.version

        recovered = service_ok and slo_restored and scenario_checks_passed
        now = datetime.now(timezone.utc)
        action_completed_at = request.action_completed_at
        if action_completed_at.tzinfo is None:
            action_completed_at = action_completed_at.replace(tzinfo=timezone.utc)
        result = RecoveryResult(
            incident_id=request.incident_id,
            recovered=recovered,
            before=RecoveryMetrics(
                latency_p95_ms=evidence.before.metrics.latency_p95_ms,
                http_5xx_rate=evidence.before.metrics.http_5xx_rate,
            ),
            after=RecoveryMetrics(
                latency_p95_ms=after.metrics.latency_p95_ms,
                http_5xx_rate=after.metrics.http_5xx_rate,
            ),
            recovery_time_seconds=max(
                0.0, (now - action_completed_at.astimezone(timezone.utc)).total_seconds()
            ),
            slo_restored=slo_restored,
        )
        self.store.save_recovery(
            RecoveryEvidenceRecord(
                incident_id=request.incident_id,
                service=request.service,
                scenario=request.scenario,
                action_completed_at=action_completed_at,
                validated_at=now,
                before=evidence.before,
                after=after,
                result=result,
            )
        )
        logger.info(
            "recovery validation completed",
            extra={
                "incident_id": request.incident_id,
                "service_name": request.service,
                "scenario": request.scenario,
                "provider": self.telemetry.mode,
                "error_category": None if recovered else service_detail,
            },
        )
        return result
